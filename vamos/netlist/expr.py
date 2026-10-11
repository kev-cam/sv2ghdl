"""HSPICE expressions: parse, inspect, evaluate, print (docs/VAMOS_AMS_DESIGN.md §4.1).

parse() turns HSPICE expression text into the frozen AST of expr_ast.py;
evaluate() gives HSPICE's value of a constant expression; to_vacask() and
to_xyce() print an expression so that the engine computes what HSPICE
computes, or raise PrintError.  Nothing here knows about statements: spice.py
hands expression text in, the emitters print with the two printers.

API
---
Dialects (docs/VAMOS_SPECTRE_DESIGN.md §3.5, §4.2): number(), parse(), evaluate()
and to_text() take dialect="hspice" (this module's HSPICE reader, byte for byte),
"spectre" or "spectre-spice" (Spectre's semantics; phase 1, S1: until then they
raise NotImplementedError); a name outside DIALECTS is a ValueError.  parse()
also takes funcs (user functions inlined as the text is parsed, Spectre only)
and number()/parse() a warn(severity, message) callback (Warn).  Data for the
Spectre dialects: SPECTRE_FUNCS (name -> arity), SPECTRE_CONSTANTS (the 22
M_*/P_* constants), EXTRA_FUNCS (cpow hypot fmod: evaluated in every dialect,
never parsed from HSPICE text).  RESERVED gains "$tnom" (Spectre's tnom);
VACASK_CONSTANTS gains M_DEGPERRAD.

parse(text, case="lower", dialect="hspice", funcs=None, warn=None) -> Expr
    One expression.  An enclosing '...' or {...} is optional; quotes and
    braces may also group sub-expressions, exactly like parentheses.
    Numbers go through number() (numbers.parse_number's suffixes and unit
    letters, "1.5n", "2meg", "10ns", correctly rounded: "0.22u" == 2.2e-07).  Identifiers are [A-Za-z_][A-Za-z0-9_]*, folded
    per case ("lower", "upper" or "sensitive", as ParseOpts.case); function
    names and the reserved variables time, temper and hertz are always
    lowercase.  A double-quoted "text" is a Str.  The node-access calls
    v vr vi vm vp vdb vt, i ir ii im ip idb it, i1..i9, lv<k> and lx<k> take
    raw node or element names (any characters except whitespace , ( ) quotes
    braces and =): "v(x1.d<0>, vdd!)" -> Call('v', (Name('x1.d<0>'),
    Name('vdd!'))).  A unary minus on a literal is folded ("-1.5" ->
    Num(-1.5)), a unary plus dropped.  Errors raise ExprError(message, text,
    pos).
    Precedence, lowest first.  The HSPICE manuals document none, so vamos
    uses C's levels and gives ** and ^ (both power in HSPICE) the binding
    Xyce's grammar gives **: tighter than unary minus on their left, right
    associative, and a unary minus allowed on their right:
        ?:   ||   &&   == !=   < <= > >=   + -   * /   unary + - !   ** ^
    so -2**2 = -4, 2**-1 = 0.5, 2**3**2 = 512 and a || b && c = a || (b && c).
    ^ is parsed as Binary('**'); & | ~ % and <> are errors (not HSPICE).

names(ast) -> Set[str]
    The parameter names referenced (dependency sorting).  Function names,
    v()/i() arguments and time/temper/hertz are excluded.

evaluate(ast, scope: Mapping[str, float], dialect="hspice") -> float
    HSPICE's value of a constant expression (the semantics below).  Raises
    EvalError naming the construct for anything not constant (node voltages
    and currents, time, temper, hertz, unknown names or functions) and for
    numeric failures (division by zero, log(0), domain errors, overflow, a
    non-finite result).  ?:, if(), && and || are lazy.

to_vacask(ast, ctx="param", node=None) -> str
to_xyce(ast, ctx="param", node=None) -> str
    Bare expression text: no {} (wrapping values is the emitter's job).
    ctx is "param" (parameter values, instance and model parameters, source
    values: no v()/i()/time) or "behavioral" (E/G vol=/cur= sources: VACASK
    b-sources, which VACASK compiles through OpenVAF, and Xyce B sources).
    Constant subexpressions are folded first and every number is printed
    with numbers.fmt.  Parameter names go through param_ident().
    node(kind, name) -> text overrides how a v()/i() argument prints (kind
    'v' or 'i'); by default HSPICE's '.' hierarchy separator becomes ':' and
    VACASK names are quoted per vacask_quote().  temper prints as $temp /
    temp, time (behavioral only) as $abstime / time.  Constructs an engine
    cannot be made to compute as HSPICE does raise PrintError: v()/i()/time
    in a parameter value, hertz, AC and terminal-current accesses (vm(),
    i1(), lv()...), strings in a behavioral expression, unknown functions
    (inline user functions first), subnormal literals on VACASK.

param_ident(name, engine) -> str
    How a parameter name is spelled in an engine's deck; the emitters must
    use it for definitions and overrides too.  Xyce reads pi, dt, exp, ctok,
    constctok as built-ins inside expressions, silently (.param pi=2 then
    {pi} gives 3.14159), and rejects vt, temp, freq, gmin, poly as names, so
    those (any case) get the prefix "vamos_"; VACASK needs it only for a
    name equal to one of its built-in constants (M_PI, P_K, ...).
vacask_quote(name) -> str
    The VACASK identifier rule, shared with the emitter: a name that is not
    [A-Za-z_$][A-Za-z0-9_$]* or all digits, or is a reserved word, is
    single-quoted.
number(s, dialect="hspice", warn=None) -> float
    A SPICE number literal (numbers.parse_number's grammar, plus HSPICE's D
    exponent: "Exponents are designated by D or E", Star-HSPICE 2001.2 3-4, so
    "1.0D+3" == 1000.0 and "2.5d-12" == 2.5e-12; a D with no digits after it is
    a unit letter, "2.5D" == 2.5, "10dB" == 10.0), correctly rounded: a
    power-of-ten suffix scales the decimal exponent, as ngspice reads it, so
    "0.22u" == "2.2e-7" == 2.2e-07 (parse_number multiplies two rounded
    values, 0.22 * 1e-6 = 2.1999999999999998e-07, which moves bin edges).  The
    parser and spice.parse read every number through it; parse_number (the
    control file and the vamos options) has no D exponent.

Helpers: walk(ast), node_calls(ast), is_constant(ast), is_node_dependent(ast),
fold(ast, scope=None, strict=False), transform(ast, fn), substitute(ast,
mapping), map_nodes(ast, fn), inline(ast, funcs), hspice_power(x, y),
to_text(ast, dialect="hspice") (HSPICE spelling, for messages), FUNCS (built-ins and arity).

HSPICE semantics (evaluate; both printers reproduce them)
---------------------------------------------------------
T(y) truncates toward zero; S(x) is the three-way sign (S(0) = 0).
    pow(x,y)      x ** T(y)                  pwr(x,y)    S(x)*|x|**y
    x**y, x^y     x>0: x**y   x<0: x**T(y)   x=0: 0
    sqrt log ln log10 db
                  S(x)*f(|x|): log and ln are natural logs, db = 20*log10
    sgn(x)        S(x)         sign(x,y)  |x|*S(y)     sign(x)  S(x)
    int trunc     T(x)         nint       round half away from zero
    floor ceil abs exp min max (2+ args; dmin dmax) atan2(y,x) sin cos tan
    asin acos atan sinh cosh tanh asinh acosh atanh: as in C
    if(c,a,b)     c ? a : b    limit(x,lo,hi)  min(max(x,lo),hi)
    agauss gauss aunif unif and limit(nom,var): the nominal value (the first
                  argument): vamos runs no Monte Carlo analysis, and HSPICE
                  uses nominal values outside one
    comparisons, && || !: 1.0 or 0.0; any nonzero value is true

Engine facts behind the printers (each verified on the installed engines)
-------------------------------------------------------------------------
VACASK, parameters: int() converts to int32 (int(3e9) = -2147483648), so T(y)
prints as integer(y), a real truncation; comparisons, && || and ! give
integers and are wrapped in real() where a value is used (7/2-style integer
division otherwise); atan2 is wrong for x<0 (it returns atan(y/x)), so it
prints as an explicit quadrant formula; sgn(0) is 1, so S(x) prints as a
conditional; nint prints as round() (half away from zero).
VACASK, behavioral sources (translated to Verilog-A, compiled by openvaf-r):
integer-valued literals lose their ".0" in the translation (1.0*c/2.0 -> 1*c/2,
an integer division), so an arithmetic operation on two integer-typed
operands gets floor() around its left operand; integer-valued literals of
magnitude 2**31 or more crash openvaf-r and print as an exact real-typed
expression (plus "+0.0*$abstime" when that is the whole expression, which
VACASK would fold back into the literal); ceil() crashes openvaf-r and int(),
real() and round() do not translate, so T(y), nint and ceil print through
floor() (a conditional inside pow() is fine, a node-dependent exponent
included); sqrt/log/pwr arguments are guarded with max(|x|,1e-300), because
the first Newton iterate is 0.
Xyce: ^ is XOR and ! does not exist (neither is printed: power is pow(), !x
is (x==0.0)); && and || share one precedence level (operands are
parenthesised); int() is a C int cast (32-bit), so T(y) prints through
floor/ceil; sgn, nint (std::round) and atan2 are native and correct; its
logs return the real part of the complex result for x<0 and db() drops the
sign, hence S(x)*f(|x|) there too.
Both: Xyce's pow(0,0) is 1e50 and VACASK's parameter pow(0,0) is an error, so
pow(x,T(y)) with a non-constant y decides T(y) == 0 (giving 1) before pow().
"""

from __future__ import annotations

import math
import re
from typing import Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from vamos.netlist.expr_ast import Binary, Call, Expr, Name, Num, Str, Ternary, Unary
from vamos.netlist.numbers import fmt, parse_number

CONTEXTS = ("param", "behavioral")
# HSPICE's special variables, and "$tnom", the name the Spectre dialects give Spectre's tnom
# (docs/VAMOS_SPECTRE_DESIGN.md §3.5, §4.2); HSPICE text can never produce a "$" name.
RESERVED = ("time", "temper", "hertz", "$tnom")
CASES = ("lower", "upper", "sensitive")
# Expression dialects (VAMOS_SPECTRE_DESIGN.md §4.2): "hspice" is today's reader, byte for byte;
# "spectre" and "spectre-spice" (§3.5) are S1's.  Any other name is a ValueError (spice.py maps
# its own "spice" to "hspice" first).  Phase 0: the Spectre dialects raise NotImplementedError.
DIALECTS = ("hspice", "spectre", "spectre-spice")
Warn = Callable[[str, str], None]               # (severity, message); spectre.py adds the origin


def _dialect(dialect: str) -> None:
    """ValueError for a name outside DIALECTS; NotImplementedError for a Spectre dialect (phase 0)."""
    if dialect not in DIALECTS:
        raise ValueError("dialect must be one of %s, not %r" % (DIALECTS, dialect))
    if dialect != "hspice":
        raise NotImplementedError("expression dialect %r is not implemented yet (VAMOS_SPECTRE_DESIGN.md "
                                  "§3.5, §4.2: phase 1, S1)" % (dialect,))

# -- numbers --------------------------------------------------------------------

# D or E marks the exponent (HSPICE: "Exponents are designated by D or E"): 1.0D+3 is 1000, not
# the number 1.0 with the unit letter D, minus 3.  A D without digits after it ("2.5D", "10dB")
# stays a unit letter.
_NUMBER = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+))(?:[eEdD]([+-]?\d+))?([A-Za-z_]*)\s*$")
_SCALE_EXP = (("meg", 6), ("mil", None), ("t", 12), ("g", 9), ("x", 6), ("k", 3), ("m", -3),
              ("u", -6), ("n", -9), ("p", -12), ("f", -15), ("a", -18))


def number(s: str, dialect: str = "hspice", warn: Optional[Warn] = None) -> float:
    """A SPICE number literal, correctly rounded: numbers.parse_number's grammar (suffixes,
    trailing unit letters) plus HSPICE's D exponent ("1.0D+3" is 1000.0), but a power-of-ten
    suffix scales the decimal exponent, as ngspice (and HSPICE) read it: "0.22u" is 2.2e-07
    like "2.2e-7", where parse_number multiplies two rounded values (0.22 * 1e-6 =
    2.1999999999999998e-07), which moves bin edges.  ValueError if s is not a number.

    dialect (VAMOS_SPECTRE_DESIGN.md §3.3, §4.2): "hspice" is this reader; the Spectre dialects
    (S1) read Spectre's and SPICE mode's number tables and report through warn(severity, message).
    """
    _dialect(dialect)
    m = _NUMBER.match(s)
    if not m:
        return parse_number(s)                          # raises ValueError naming s
    mant, exp, tail = m.group(1), int(m.group(2) or 0), m.group(3).lower()
    for name, k in _SCALE_EXP:
        if tail.startswith(name):
            if k is None:                               # mil: 25.4e-6, not a power of ten
                return float("%se%d" % (mant, exp)) * 25.4e-6
            return float("%se%d" % (mant, exp + k))
    return float("%se%d" % (mant, exp))

# Node/element access calls: their arguments are raw names, never expressions.
_NODE_FUNC = re.compile(r"(?:v|vr|vi|vm|vp|vdb|vt|i|ir|ii|im|ip|idb|it|i[1-9]|lv\d+|lx\d+)$")

# HSPICE built-ins and their arity (min, max); max None = no limit.
FUNCS: Dict[str, Tuple[int, Optional[int]]] = {
    "sin": (1, 1), "cos": (1, 1), "tan": (1, 1), "asin": (1, 1), "acos": (1, 1), "atan": (1, 1),
    "sinh": (1, 1), "cosh": (1, 1), "tanh": (1, 1), "asinh": (1, 1), "acosh": (1, 1),
    "atanh": (1, 1), "exp": (1, 1), "abs": (1, 1), "sqrt": (1, 1), "log": (1, 1), "ln": (1, 1),
    "log10": (1, 1), "db": (1, 1), "int": (1, 1), "trunc": (1, 1), "nint": (1, 1),
    "floor": (1, 1), "ceil": (1, 1), "sgn": (1, 1), "sign": (1, 2), "pow": (2, 2),
    "pwr": (2, 2), "atan2": (2, 2), "min": (2, None), "max": (2, None), "dmin": (2, None),
    "dmax": (2, None), "if": (3, 3), "limit": (2, 3), "agauss": (3, 4), "gauss": (3, 4),
    "aunif": (2, 3), "unif": (2, 3),
}
_NOMINAL = ("agauss", "gauss", "aunif", "unif")
_SAME_NAME = ("sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh", "asinh",
              "acosh", "atanh", "exp", "floor")

# Spectre's closed function table (VAMOS_SPECTRE_DESIGN.md §3.5; Spectre Circuit Simulator
# Reference 19.1 pp.476-477), name -> (min, max) arity: one argument for the first group, two for
# the second (sign(x,y) = sgn(y)*|x|, atan2(x,y) = atan(x/y)).  The Spectre dialects (S1) accept a
# call to these names and to user functions only; FUNCS stays HSPICE's table.
SPECTRE_FUNCS: Dict[str, Tuple[int, int]] = dict(
    [(f, (1, 1)) for f in ("log", "ln", "log10", "exp", "sqrt", "abs", "int", "floor", "ceil", "sgn",
                           "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh",
                           "asinh", "acosh", "atanh")]
    + [(f, (2, 2)) for f in ("min", "max", "pow", "fmod", "hypot", "atan2", "sign")])

# Spectre's built-in mathematical and physical constants (Reference 19.1 pp.466-467, 22 names),
# equal to VACASK's lib/context.cpp:13-35 (the old CODATA values included).  The Spectre dialects
# (S1) fold them to Num at parse time; constants may not name parameters (ref19 p.495).
SPECTRE_CONSTANTS: Dict[str, float] = {
    "M_E": math.e, "M_LOG2E": 1.0 / math.log(2.0), "M_LOG10E": 1.0 / math.log(10.0),
    "M_LN2": math.log(2.0), "M_LN10": math.log(10.0), "M_PI": math.pi, "M_TWO_PI": 2.0 * math.pi,
    "M_PI_2": math.pi / 2.0, "M_PI_4": math.pi / 4.0, "M_1_PI": 1.0 / math.pi, "M_2_PI": 2.0 / math.pi,
    "M_2_SQRTPI": 2.0 / math.sqrt(math.pi), "M_SQRT2": math.sqrt(2.0), "M_SQRT1_2": 1.0 / math.sqrt(2.0),
    "M_DEGPERRAD": 180.0 / math.pi,
    "P_Q": 1.6021918e-19, "P_C": 2.997924562e8, "P_K": 1.3806226e-23, "P_H": 6.6260755e-34,
    "P_EPS0": 8.85418792394420013968e-12, "P_U0": 4.0e-7 * math.pi, "P_CELSIUS0": 273.15,
}

# Functions the Spectre dialects produce and the evaluator (in every dialect, S1) and the
# printers know: cpow (C pow, what Spectre's ** and pow() mean), hypot and fmod.  Never parsed
# from HSPICE text (the HSPICE parser rejects the calls as unknown functions, as today).
EXTRA_FUNCS: Dict[str, Tuple[int, Optional[int]]] = {"cpow": (2, 2), "hypot": (2, 2), "fmod": (2, 2)}


# -- errors -------------------------------------------------------------------

class ExprError(ValueError):
    """A syntax error: the message, the expression text and a 0-based position."""

    def __init__(self, message: str, text: str = "", pos: int = -1):
        self.message, self.text, self.pos = message, text, pos
        where = " at col %d" % (pos + 1) if pos >= 0 else ""
        super().__init__("%s%s in %r" % (message, where, text) if text else message)


class EvalError(ValueError):
    """An expression with no constant HSPICE value; the message names the construct."""


class PrintError(ValueError):
    """An expression an engine cannot be made to compute as HSPICE does."""


# -- lexer --------------------------------------------------------------------

_NUM_RE = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:[eEdD][+-]?\d+)?[A-Za-z_]*")   # D exponent: _NUMBER
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RAW_BAD = frozenset(" \t\r\n,()'\"{}=")
# Longest first; '^' is HSPICE power.
_OPS = ("**", "==", "!=", "<=", ">=", "&&", "||", "+", "-", "*", "/", "^", "<", ">", "!", "?",
        ":", ",")


class _Tok:
    __slots__ = ("kind", "val", "pos")

    def __init__(self, kind: str, val, pos: int):
        self.kind, self.val, self.pos = kind, val, pos     # NUM NAME STR NODE OP ( ) EOF


def _lex(text: str) -> List[_Tok]:
    toks: List[_Tok] = []
    groups: List[Tuple[str, int]] = []                      # open ( { ' and where
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
            m = _NUM_RE.match(text, i)
            toks.append(_Tok("NUM", number(m.group(0)), i))
            i = m.end()
        elif c.isalpha() or c == "_":
            m = _NAME_RE.match(text, i)
            word = m.group(0)
            j = m.end()
            while j < n and text[j] in " \t":
                j += 1
            if j < n and text[j] == "(" and _NODE_FUNC.match(word.lower()):
                toks.append(_Tok("NODE", (word.lower(), _raw_names(text, word.lower(), j)), i))
                i = text.index(")", j) + 1
            else:
                toks.append(_Tok("NAME", word, i))
                i = m.end()
        elif c == '"':
            end = text.find('"', i + 1)
            if end < 0:
                raise ExprError("unterminated string", text, i)
            toks.append(_Tok("STR", text[i + 1:end], i))
            i = end + 1
        elif c in "({" or (c == "'" and not (groups and groups[-1][0] == "'")):
            groups.append((c, i))
            toks.append(_Tok("(", c, i))
            i += 1
        elif c in ")}'":
            want = {")": "(", "}": "{", "'": "'"}[c]
            if not groups or groups[-1][0] != want:
                raise ExprError("unbalanced %r" % c, text, i)
            groups.pop()
            toks.append(_Tok(")", c, i))
            i += 1
        elif text.startswith("<>", i):
            raise ExprError("operator '<>' is not an HSPICE operator (use !=)", text, i)
        else:
            for op in _OPS:
                if text.startswith(op, i):
                    toks.append(_Tok("OP", "**" if op == "^" else op, i))
                    i += len(op)
                    break
            else:
                if c in "&|~%=":
                    raise ExprError("operator %r is not an HSPICE expression operator" % c, text, i)
                raise ExprError("unexpected character %r" % c, text, i)
    if groups:
        c, pos = groups[-1]
        raise ExprError("unclosed %r" % c, text, pos)
    toks.append(_Tok("EOF", None, n))
    return toks


def _raw_names(text: str, func: str, lparen: int) -> List[str]:
    """The comma-separated raw names of func( ... ) starting at text[lparen] == '('."""
    end = text.find(")", lparen)
    if end < 0:
        raise ExprError("unterminated %s(" % func, text, lparen)
    out = []
    pos = lparen + 1
    for part in text[lparen + 1:end].split(","):
        name = part.strip()
        if not name:
            raise ExprError("empty name in %s()" % func, text, pos)
        for k, ch in enumerate(name):
            if ch in _RAW_BAD:
                raise ExprError("bad character %r in a %s() name" % (ch, func), text,
                                pos + part.index(name) + k)
        out.append(name)
        pos += len(part) + 1
    return out


# -- parser (Pratt) -----------------------------------------------------------

_LBP = {"?": 1, "||": 2, "&&": 3, "==": 4, "!=": 4, "<": 5, "<=": 5, ">": 5, ">=": 5,
        "+": 6, "-": 6, "*": 7, "/": 7, "**": 10}
_UNARY_RBP = 8          # -a*b = (-a)*b, -a**b = -(a**b)


class _Parser:
    def __init__(self, text: str, case: str):
        self.text, self.case = text, case
        self.toks = _lex(text)
        self.k = 0

    def peek(self) -> _Tok:
        return self.toks[self.k]

    def next(self) -> _Tok:
        t = self.toks[self.k]
        self.k += 1
        return t

    def fail(self, msg: str, tok: _Tok):
        raise ExprError(msg, self.text, tok.pos)

    def ident(self, name: str) -> str:
        low = name.lower()
        if low in RESERVED or self.case == "lower":
            return low
        return name.upper() if self.case == "upper" else name

    def parse(self) -> Expr:
        if self.peek().kind == "EOF":
            self.fail("empty expression", self.peek())
        e = self.expr(0)
        t = self.peek()
        if t.kind != "EOF":
            self.fail("unexpected %s" % _describe(t), t)
        return e

    def expr(self, rbp: int) -> Expr:
        left = self.nud()
        while True:
            t = self.peek()
            if t.kind != "OP" or t.val not in _LBP or _LBP[t.val] <= rbp:
                return left
            left = self.led(left)

    def nud(self) -> Expr:
        t = self.next()
        if t.kind == "NUM":
            return Num(t.val)
        if t.kind == "STR":
            return Str(t.val)
        if t.kind == "NODE":
            func, args = t.val
            return Call(func, tuple(Name(self.ident(a)) for a in args))
        if t.kind == "NAME":
            if self.peek().kind == "(" and self.peek().val == "(":
                return self.call(t)
            return Name(self.ident(t.val))
        if t.kind == "(":
            if self.peek().kind == ")":
                self.fail("empty parentheses", self.peek())
            e = self.expr(0)
            self.close()
            return e
        if t.kind == "OP" and t.val in ("-", "+", "!"):
            arg = self.expr(_UNARY_RBP)
            if t.val == "+":
                return arg
            if t.val == "-" and isinstance(arg, Num):
                return Num(-arg.value)
            return Unary(t.val, arg)
        self.fail("expected an operand, got %s" % _describe(t), t)
        raise AssertionError("unreachable")

    def call(self, name_tok: _Tok) -> Expr:
        self.next()                                         # (
        func = name_tok.val.lower()
        if self.peek().kind == ")":
            self.fail("%s() needs arguments" % func, self.peek())
        args = [self.expr(0)]
        while self.peek().kind == "OP" and self.peek().val == ",":
            self.next()
            args.append(self.expr(0))
        self.close()
        return Call(func, tuple(args))

    def close(self) -> None:
        t = self.next()
        if t.kind != ")":
            self.fail("expected ')', got %s" % _describe(t), t)

    def led(self, left: Expr) -> Expr:
        op = self.next().val
        if op == "?":
            mid = self.expr(0)
            c = self.next()
            if c.kind != "OP" or c.val != ":":
                self.fail("expected ':' of ?:, got %s" % _describe(c), c)
            return Ternary(left, mid, self.expr(0))
        if op == "**":                                       # right associative
            return Binary(op, left, self.expr(_LBP[op] - 1))
        return Binary(op, left, self.expr(_LBP[op]))


def _describe(t: _Tok) -> str:
    if t.kind == "EOF":
        return "end of expression"
    if t.kind in ("NUM", "STR"):
        return {"NUM": "number", "STR": "string"}[t.kind]
    if t.kind == "NAME":
        return "name %r" % t.val
    if t.kind == "NODE":
        return "%s()" % t.val[0]
    return repr(t.val)


def parse(text: str, case: str = "lower", dialect: str = "hspice",
          funcs: Optional[Mapping[str, Tuple[Sequence[str], Expr]]] = None,
          warn: Optional[Warn] = None) -> Expr:
    """Parse one HSPICE expression (see the module docstring).

    dialect (VAMOS_SPECTRE_DESIGN.md §3.5, §4.2): "hspice" is today's parser, byte for byte;
    "spectre" and "spectre-spice" (S1) parse with Spectre's binding powers, functions and
    constants, inline the user functions `funcs` (name -> (parameter names, body)) as the text is
    parsed, and report through warn(severity, message).
    """
    if case not in CASES:
        raise ValueError("case must be one of %s" % (CASES,))
    _dialect(dialect)
    if funcs is not None:
        raise NotImplementedError("parse(funcs=...) is the Spectre dialects' user-function inlining "
                                  "(VAMOS_SPECTRE_DESIGN.md §3.5: phase 1, S1); the HSPICE route uses inline()")
    return _Parser(text, case).parse()


# -- inspection and rewriting -------------------------------------------------

def is_node_call(e: Expr) -> bool:
    """A node/element access call: v(), i(), vm(), i1(), lv9(), ..."""
    return isinstance(e, Call) and bool(_NODE_FUNC.match(e.func))


def walk(ast: Expr) -> Iterator[Expr]:
    """Every node, pre-order; the raw names inside v()/i() are not visited."""
    stack = [ast]
    while stack:
        e = stack.pop()
        yield e
        if isinstance(e, Call):
            if not is_node_call(e):
                stack.extend(reversed(e.args))
        elif isinstance(e, Unary):
            stack.append(e.arg)
        elif isinstance(e, Binary):
            stack.extend((e.right, e.left))
        elif isinstance(e, Ternary):
            stack.extend((e.b, e.a, e.cond))


def names(ast: Expr) -> Set[str]:
    """Referenced parameter names (functions, v()/i() arguments and time/temper/hertz excluded)."""
    return {e.name for e in walk(ast) if isinstance(e, Name) and e.name not in RESERVED}


def node_calls(ast: Expr) -> List[Call]:
    """The node/element access calls (v(), i(), ...), in order, without repeats."""
    out: List[Call] = []
    for e in walk(ast):
        if is_node_call(e) and e not in out:
            out.append(e)
    return out


def is_constant(ast: Expr) -> bool:
    """No names, no node access, no strings: only numbers, operators and built-in calls."""
    for e in walk(ast):
        if isinstance(e, (Name, Str)) or is_node_call(e):
            return False
    return True


def is_node_dependent(ast: Expr) -> bool:
    """True if ast reads node voltages, currents, time or hertz (a behavioral expression)."""
    for e in walk(ast):
        if is_node_call(e) or (isinstance(e, Name) and e.name in ("time", "hertz")):
            return True
    return False


def transform(ast: Expr, fn: Callable[[Expr], Expr]) -> Expr:
    """Rebuild bottom-up: fn(node) sees each node with its children already transformed.

    A node-access call reaches fn whole; its raw names are not visited.
    """
    if isinstance(ast, Call) and not is_node_call(ast):
        args = tuple(transform(a, fn) for a in ast.args)
        if args != ast.args:
            ast = Call(ast.func, args)
    elif isinstance(ast, Unary):
        a = transform(ast.arg, fn)
        if a is not ast.arg:
            ast = Unary(ast.op, a)
    elif isinstance(ast, Binary):
        l, r = transform(ast.left, fn), transform(ast.right, fn)
        if l is not ast.left or r is not ast.right:
            ast = Binary(ast.op, l, r)
    elif isinstance(ast, Ternary):
        c, a, b = transform(ast.cond, fn), transform(ast.a, fn), transform(ast.b, fn)
        if c is not ast.cond or a is not ast.a or b is not ast.b:
            ast = Ternary(c, a, b)
    return fn(ast)


def substitute(ast: Expr, mapping: Mapping[str, Expr]) -> Expr:
    """Replace parameter names by expressions (node-access arguments are untouched)."""
    return transform(ast, lambda e: mapping.get(e.name, e) if isinstance(e, Name) else e)


def map_nodes(ast: Expr, fn: Callable[[str], str]) -> Expr:
    """Apply fn to every v()/i() argument name (e.g. spice.fold_ground)."""
    def f(e: Expr) -> Expr:
        if is_node_call(e):
            return Call(e.func, tuple(Name(fn(a.name)) for a in e.args))
        return e
    return transform(ast, f)


def inline(ast: Expr, funcs: Mapping[str, Tuple[Sequence[str], Expr]]) -> Expr:
    """Expand user-defined functions (.param f(a,b)='a*b'): funcs maps a name to (params, body).

    The call's arguments replace the parameters of the body; other names in
    the body stay free and are looked up where the call is.  A recursive
    definition or a wrong argument count raises ExprError.
    """
    def expand(e: Expr, active: Tuple[str, ...]) -> Expr:
        def f(x: Expr) -> Expr:
            if isinstance(x, Call) and x.func in funcs and not is_node_call(x):
                params, body = funcs[x.func]
                if x.func in active:
                    raise ExprError("recursive function %s()" % x.func)
                if len(params) != len(x.args):
                    raise ExprError("%s() takes %d argument(s), got %d"
                                    % (x.func, len(params), len(x.args)))
                return substitute(expand(body, active + (x.func,)), dict(zip(params, x.args)))
            return x
        return transform(e, f)
    return expand(ast, ())


# -- HSPICE spelling (messages) ------------------------------------------------

_TPREC = {"?": 1, "||": 2, "&&": 3, "==": 4, "!=": 4, "<": 5, "<=": 5, ">": 5, ">=": 5,
          "+": 6, "-": 6, "*": 7, "/": 7, "**": 10}


def to_text(ast: Expr, dialect: str = "hspice") -> str:
    """HSPICE spelling with the parentheses vamos's precedence needs (parse(to_text(e)) == e).

    dialect (VAMOS_SPECTRE_DESIGN.md §4.2, S1): "spectre" spells cpow as ** and knows << >> & | ~^
    at the dialect's levels, so messages quote Spectre syntax.
    """
    _dialect(dialect)
    return _text(ast, 0)


def _text(e: Expr, need: int) -> str:
    if isinstance(e, Num):
        s = repr(float(e.value))
        return "(%s)" % s if s.startswith("-") and need > 0 else s
    if isinstance(e, Name):
        return e.name
    if isinstance(e, Str):
        return '"%s"' % e.text
    if isinstance(e, Call):
        return "%s(%s)" % (e.func, ", ".join(_text(a, 0) for a in e.args))
    if isinstance(e, Unary):
        p = _UNARY_RBP
        s = e.op + _text(e.arg, p + 1)
    elif isinstance(e, Binary):
        p = _TPREC[e.op]
        if e.op == "**":
            s = "%s**%s" % (_text(e.left, p + 1), _text(e.right, _UNARY_RBP))
        else:
            s = "%s %s %s" % (_text(e.left, p), e.op, _text(e.right, p + 1))
    elif isinstance(e, Ternary):
        p = 1
        s = "%s ? %s : %s" % (_text(e.cond, 2), _text(e.a, 0), _text(e.b, 1))
    else:
        raise TypeError("not an expression: %r" % (e,))
    return "(%s)" % s if p < need else s


# -- evaluation ---------------------------------------------------------------

def _sgn(x: float) -> float:
    return 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)


def _trunc(x: float) -> float:
    return float(math.trunc(x))


def _nint(x: float) -> float:
    return float(math.floor(x + 0.5)) if x >= 0 else -float(math.floor(-x + 0.5))


def hspice_power(x: float, y: float) -> float:
    """HSPICE x**y: x>0 -> x^y; x<0 -> x^T(y); x=0 -> 0."""
    if x > 0:
        return math.pow(x, y)
    if x < 0:
        return math.pow(x, _trunc(y))
    return 0.0


_C_FUNCS: Dict[str, Callable[[float], float]] = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos,
    "atan": math.atan, "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "asinh": math.asinh, "acosh": math.acosh, "atanh": math.atanh, "exp": math.exp,
    "abs": abs, "floor": lambda x: float(math.floor(x)), "ceil": lambda x: float(math.ceil(x)),
    "int": _trunc, "trunc": _trunc, "nint": _nint, "sgn": _sgn,
}


def check_arity(e: Call, err=EvalError) -> None:
    """Raise err if a built-in is called with the wrong number of arguments."""
    lo, hi = FUNCS[e.func]
    n = len(e.args)
    if n < lo or (hi is not None and n > hi):
        want = str(lo) if lo == hi else ("%d or more" % lo if hi is None else "%d or %d" % (lo, hi))
        raise err("%s() takes %s argument(s), got %d: %s" % (e.func, want, n, to_text(e)))


class _Eval:
    def __init__(self, scope: Mapping[str, float]):
        self.scope = scope

    def ev(self, e: Expr) -> float:
        r = self.raw(e)
        if not math.isfinite(r):
            raise EvalError("%s is not finite" % to_text(e))
        return r

    def raw(self, e: Expr) -> float:
        if isinstance(e, Num):
            return float(e.value)
        if isinstance(e, Name):
            if e.name in RESERVED:
                raise EvalError("%s is not a constant" % e.name)
            if e.name not in self.scope:
                raise EvalError("unknown parameter %r" % e.name)
            return float(self.scope[e.name])
        if isinstance(e, Str):
            raise EvalError("string %s is not a number" % to_text(e))
        if isinstance(e, Unary):
            x = self.ev(e.arg)
            if e.op == "-":
                return -x
            if e.op == "+":
                return x
            if e.op == "!":
                return 1.0 if x == 0 else 0.0
            raise EvalError("operator %s is not HSPICE: %s" % (e.op, to_text(e)))
        if isinstance(e, Ternary):
            return self.ev(e.a) if self.ev(e.cond) != 0 else self.ev(e.b)
        if isinstance(e, Binary):
            return self.binary(e)
        if isinstance(e, Call):
            return self.call(e)
        raise TypeError("not an expression: %r" % (e,))

    def binary(self, e: Binary) -> float:
        op = e.op
        if op == "&&":
            return 1.0 if self.ev(e.left) != 0 and self.ev(e.right) != 0 else 0.0
        if op == "||":
            return 1.0 if self.ev(e.left) != 0 or self.ev(e.right) != 0 else 0.0
        x, y = self.ev(e.left), self.ev(e.right)
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        if op == "/":
            if y == 0:
                raise EvalError("division by zero in %s" % to_text(e))
            return x / y
        if op in ("**", "^"):
            try:
                return hspice_power(x, y)
            except (OverflowError, ValueError, ZeroDivisionError) as exc:
                raise EvalError("%s: %s" % (to_text(e), exc))
        cmp = {"==": x == y, "!=": x != y, "<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}
        if op in cmp:
            return 1.0 if cmp[op] else 0.0
        raise EvalError("operator %s is not HSPICE: %s" % (op, to_text(e)))

    def call(self, e: Call) -> float:
        f = e.func
        if is_node_call(e):
            raise EvalError("%s is not a constant (it needs the circuit's solution)" % to_text(e))
        if f not in FUNCS:
            raise EvalError("unknown function %s()" % f)
        check_arity(e)
        if f == "if":
            return self.ev(e.args[1]) if self.ev(e.args[0]) != 0 else self.ev(e.args[2])
        a = [self.ev(x) for x in e.args]
        try:
            return _apply(f, a, e)
        except EvalError:
            raise
        except (OverflowError, ValueError, ZeroDivisionError) as exc:
            raise EvalError("%s: %s" % (to_text(e), exc))


def _signed_log(f: Callable[[float], float], x: float, e: Call) -> float:
    if x == 0:
        raise EvalError("%s: logarithm of 0" % to_text(e))
    return _sgn(x) * f(abs(x))


def _apply(f: str, a: List[float], e: Call) -> float:
    if f in _C_FUNCS:
        return _C_FUNCS[f](a[0])
    if f == "sqrt":
        return _sgn(a[0]) * math.sqrt(abs(a[0]))
    if f in ("log", "ln"):
        return _signed_log(math.log, a[0], e)
    if f == "log10":
        return _signed_log(math.log10, a[0], e)
    if f == "db":
        return _signed_log(lambda v: 20.0 * math.log10(v), a[0], e)
    if f == "pow":
        if a[0] == 0 and _trunc(a[1]) < 0:
            raise EvalError("%s: zero to a negative power" % to_text(e))
        return math.pow(a[0], _trunc(a[1]))
    if f == "pwr":
        return 0.0 if a[0] == 0 else _sgn(a[0]) * math.pow(abs(a[0]), a[1])
    if f == "atan2":
        return math.atan2(a[0], a[1])
    if f in ("min", "dmin"):
        return min(a)
    if f in ("max", "dmax"):
        return max(a)
    if f == "sign":
        return _sgn(a[0]) if len(a) == 1 else abs(a[0]) * _sgn(a[1])
    if f == "limit":
        return a[0] if len(a) == 2 else min(max(a[0], a[1]), a[2])
    if f in _NOMINAL:
        return a[0]
    raise EvalError("unknown function %s()" % f)


def evaluate(ast: Expr, scope: Mapping[str, float], dialect: str = "hspice") -> float:
    """HSPICE's value of a constant expression; EvalError otherwise (see the module docstring).

    dialect (VAMOS_SPECTRE_DESIGN.md §3.5, §4.2, S1): "spectre" adds Spectre's domain rules (log,
    sqrt, exp, both operands of && and ||); EXTRA_FUNCS and the bitwise operators are evaluated
    in every dialect.
    """
    _dialect(dialect)
    try:
        return _Eval(scope).ev(ast)
    except RecursionError:
        raise EvalError("expression too deeply nested: %s..." % to_text(ast)[:60])


def fold(ast: Expr, scope: Optional[Mapping[str, float]] = None, strict: bool = False) -> Expr:
    """Replace every constant subexpression by its HSPICE value (a Num).

    With scope, the names it defines count as constants.  ?:, if(), && and ||
    with a constant deciding operand fold to the side HSPICE evaluates.  A
    constant subexpression that fails to evaluate (1/0) is kept as written; with
    strict, one that is still in the result (not in a branch HSPICE never
    evaluates) raises its EvalError.
    """
    scope = scope or {}

    def known(e: Expr) -> bool:
        return isinstance(e, Num)

    def foldable(e: Expr) -> bool:
        return isinstance(e, (Unary, Binary, Ternary, Call)) and not is_node_call(e) and \
            all(known(c) for c in _children(e))

    def f(e: Expr) -> Expr:
        if isinstance(e, Num):
            return e
        if isinstance(e, Name):
            if e.name in scope and e.name not in RESERVED:
                return Num(float(scope[e.name]))
            return e
        if isinstance(e, Ternary) and known(e.cond):
            return e.a if e.cond.value != 0 else e.b
        if isinstance(e, Call) and e.func == "if" and len(e.args) == 3 and known(e.args[0]):
            return e.args[1] if e.args[0].value != 0 else e.args[2]
        if isinstance(e, Binary) and e.op in ("&&", "||") and known(e.left):
            if (e.left.value != 0) == (e.op == "||"):
                return Num(1.0 if e.op == "||" else 0.0)
            if known(e.right):
                return Num(1.0 if e.right.value != 0 else 0.0)
            return e
        if foldable(e):
            try:
                return Num(evaluate(e, {}))
            except EvalError:
                pass
        return e
    out = transform(ast, f)
    if strict:
        for e in walk(out):
            if foldable(e):
                evaluate(e, {})                             # raises, naming the construct
    return out


def _children(e: Expr) -> Tuple[Expr, ...]:
    if isinstance(e, Call):
        return e.args
    if isinstance(e, Unary):
        return (e.arg,)
    if isinstance(e, Binary):
        return (e.left, e.right)
    if isinstance(e, Ternary):
        return (e.cond, e.a, e.b)
    return ()


# -- identifiers ----------------------------------------------------------------

XYCE_RESERVED = frozenset(("pi", "dt", "vt", "temp", "freq", "gmin", "exp", "ctok", "constctok",
                           "poly", "time", "temper", "hertz"))
# VACASK's built-in constants (lib/context.cpp:13-35): a parameter of such a name would shadow
# the constant silently, so param_ident prefixes it.  The 22 names of SPECTRE_CONSTANTS.
VACASK_CONSTANTS = frozenset(("M_E", "M_LOG2E", "M_LOG10E", "M_LN2", "M_LN10", "M_PI", "M_TWO_PI",
                              "M_PI_2", "M_PI_4", "M_1_PI", "M_2_PI", "M_2_SQRTPI", "M_SQRT2",
                              "M_SQRT1_2", "M_DEGPERRAD", "P_Q", "P_C", "P_K", "P_H", "P_EPS0", "P_U0",
                              "P_CELSIUS0"))
VACASK_RESERVED = frozenset(("include", "section", "endsection", "load", "model", "global",
                             "ground", "subckt", "ends", "parameters", "control", "endc",
                             "analysis", "sweep", "embed", "save"))
_VACASK_ID = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*$")
PARAM_PREFIX = "vamos_"


def param_ident(name: str, engine: str) -> str:
    """The spelling of a parameter name in an engine's deck (see the module docstring)."""
    if engine == "xyce":
        return PARAM_PREFIX + name if name.lower() in XYCE_RESERVED else name
    if engine == "vacask":
        return PARAM_PREFIX + name if name in VACASK_CONSTANTS else name
    raise ValueError("unknown engine %r" % (engine,))


def vacask_quote(name: str) -> str:
    """A name as a VACASK identifier: bare when it is a plain identifier or all digits, else quoted."""
    if name.isdigit() or (_VACASK_ID.match(name) and name not in VACASK_RESERVED):
        return name
    if "'" in name or not name:
        raise PrintError("name %r cannot be written as a VACASK identifier" % (name,))
    return "'%s'" % name


# -- printers -----------------------------------------------------------------

# A printed fragment: text, the precedence of its outermost operator, and its
# type in the target ('r' real, 'i' integer; only VACASK has integers).
_ATOM, _MUL, _ADD, _CMP, _AND, _OR = 100, 7, 6, 5, 3, 2
_BIG = 2147483648.0                     # 2**31: openvaf-r crashes on integer literals from here
_EXACT_HALF = 4503599627370496.0        # 2**52: below it, v+0.5 is exact
_TWO30 = 1073741824.0
_TINY = 1e-300                          # behavioral guard for sqrt/log/pwr arguments
_MIN_NORMAL = 2.2250738585072014e-308


class _F:
    __slots__ = ("text", "prec", "typ")

    def __init__(self, text: str, prec: int = _ATOM, typ: str = "r"):
        self.text, self.prec, self.typ = text, prec, typ


class _Printer:
    """HSPICE AST -> engine text.  Every form is built by the combinators
    (arith, cmp, logic, tern, neg, call), which apply the engine's typing and
    parenthesisation rules; the engines override the hooks below them."""

    engine = ""

    def __init__(self, ctx: str, node: Optional[Callable[[str, str], str]]):
        if ctx not in CONTEXTS:
            raise ValueError("ctx must be one of %s" % (CONTEXTS,))
        self.beh = ctx == "behavioral"
        self.node_fn = node

    def emit(self, ast: Expr) -> str:
        try:
            ast = fold(ast, strict=True)
        except EvalError as exc:
            raise PrintError(str(exc))
        try:
            return self.whole(ast, self.val(self.p(ast)).text)
        except RecursionError:
            raise PrintError("expression too deeply nested: %s..." % to_text(ast)[:60])

    def whole(self, ast: Expr, text: str) -> str:
        """Last word on the complete text (ast is the folded expression)."""
        return text

    # -- combinators
    @staticmethod
    def paren(f: _F, minprec: int) -> str:
        return f.text if f.prec >= minprec else "(%s)" % f.text

    def arith(self, op: str, l: _F, r: _F) -> _F:
        p = _MUL if op in "*/" else _ADD
        l, r = self.val(l), self.val(r)
        return _F("%s%s%s" % (self.paren(l, p), op, self.paren(r, p + 1)), p,
                  "i" if (l.typ == "i" and r.typ == "i") else "r")

    def cmp(self, op: str, l: _F, r: _F) -> _F:
        return _F("%s%s%s" % (self.paren(l, _ADD), op, self.paren(r, _ADD)), _CMP, "i")

    def logic(self, op: str, l: _F, r: _F) -> _F:
        return _F("%s%s%s" % (self.paren(l, _CMP), op, self.paren(r, _CMP)),
                  _AND if op == "&&" else _OR, "i")

    def tern(self, c: _F, a: _F, b: _F) -> _F:
        a, b = self.val(a), self.val(b)
        return _F("(%s ? %s : %s)" % (self.paren(c, _CMP), self.paren(a, _CMP), self.paren(b, _CMP)),
                  _ATOM, "i" if (a.typ == "i" and b.typ == "i") else "r")

    def neg(self, f: _F) -> _F:
        f = self.val(f)
        return _F("(-%s)" % self.paren(f, _ATOM), _ATOM, f.typ)

    def call(self, func: str, args: Sequence[_F], typ: str = "r") -> _F:
        return _F("%s(%s)" % (func, ", ".join(self.val(a).text for a in args)), _ATOM, typ)

    # -- engine hooks
    def val(self, f: _F) -> _F:
        """f where a value is needed (VACASK parameters make integers real)."""
        return f

    def num(self, v: float) -> _F:
        s = _fmt(v)
        return _F("(%s)" % s) if s.startswith("-") else _F(s)

    def name(self, n: str) -> _F:
        return _F(param_ident(n, self.engine))

    def S(self, x: _F) -> _F:
        """HSPICE's three-way sign."""
        zero = self.num(0.0)
        return self.tern(self.cmp(">", x, zero), self.num(1.0),
                         self.tern(self.cmp("<", x, zero), self.num(-1.0), zero))

    def T(self, y: _F) -> _F:
        """Truncation toward zero, without integer conversion."""
        return self.tern(self.cmp(">=", y, self.num(0.0)), self.call("floor", [y]), self.ceil(y))

    def ceil(self, x: _F) -> _F:
        return self.call("ceil", [x])

    def nint(self, x: _F) -> _F:
        return self.call("nint", [x])

    def atan2(self, y: _F, x: _F) -> _F:
        return self.call("atan2", [y, x])

    def absf(self, x: _F) -> _F:
        return self.call("abs", [x])

    def minmax(self, func: str, args: Sequence[_F]) -> _F:
        f = args[0]
        for a in args[1:]:
            f = self.call(func, [f, a])
        return f

    def guard(self, a: _F) -> _F:
        """|x| kept away from 0 in behavioral sources (the first Newton iterate is 0)."""
        return self.call("max", [a, self.num(_TINY)]) if self.beh else a

    def not_(self, x: _F) -> _F:
        raise NotImplementedError

    def temper(self) -> _F:
        raise NotImplementedError

    def time(self) -> _F:
        raise NotImplementedError

    def access(self, e: Call) -> _F:
        if e.func == "v" and len(e.args) in (1, 2):
            return _F("v(%s)" % ",".join(self.node_text("v", a.name) for a in e.args))
        if e.func == "i" and len(e.args) == 1:
            return _F("i(%s)" % self.node_text("i", e.args[0].name))
        raise PrintError("%s has no %s equivalent in a behavioral source (AC and terminal "
                         "accesses are not supported)" % (to_text(e), self.engine))

    def node_text(self, kind: str, name: str) -> str:
        if self.node_fn is not None:
            return self.node_fn(kind, name)
        return name.replace(".", ":")

    # -- the walk
    def p(self, e: Expr) -> _F:
        if isinstance(e, Num):
            return self.num(e.value)
        if isinstance(e, Name):
            if e.name == "temper":
                return self.temper()
            if e.name == "time":
                if not self.beh:
                    raise PrintError("time is only allowed in behavioral sources, not in a "
                                     "parameter value")
                return self.time()
            if e.name == "hertz":
                raise PrintError("hertz (the AC analysis frequency) has no value in a transient "
                                 "co-simulation")
            return self.name(e.name)
        if isinstance(e, Str):
            if self.beh:
                raise PrintError("string %s in a behavioral expression" % to_text(e))
            return _F('"%s"' % e.text)
        if isinstance(e, Unary):
            if e.op == "-":
                return self.neg(self.p(e.arg))
            if e.op == "+":
                return self.p(e.arg)
            if e.op == "!":
                return self.not_(self.p(e.arg))
            raise PrintError("operator %s is not HSPICE: %s" % (e.op, to_text(e)))
        if isinstance(e, Ternary):
            return self.tern(self.p(e.cond), self.p(e.a), self.p(e.b))
        if isinstance(e, Binary):
            if e.op in ("**", "^"):
                return self.power(e)
            l, r = self.p(e.left), self.p(e.right)
            if e.op in ("+", "-", "*", "/"):
                return self.arith(e.op, l, r)
            if e.op in ("==", "!=", "<", "<=", ">", ">="):
                return self.cmp(e.op, l, r)
            if e.op in ("&&", "||"):
                return self.logic(e.op, l, r)
            raise PrintError("operator %s is not HSPICE: %s" % (e.op, to_text(e)))
        if isinstance(e, Call):
            if is_node_call(e):
                if not self.beh:
                    raise PrintError("%s needs the circuit's solution: not allowed in a parameter "
                                     "value" % to_text(e))
                return self.access(e)
            return self.func(e)
        raise TypeError("not an expression: %r" % (e,))

    def pow_t(self, x: _F, yast: Expr, x_nonzero: bool = False) -> _F:
        """x ** T(y), C pow with a truncated exponent.

        pow(x, 0) is 1 for every x (C, HSPICE); Xyce returns 1e50 for
        pow(0, 0) and VACASK's parameter pow() refuses it, so a zero exponent
        is decided before pow() unless x is known to be nonzero.
        """
        if isinstance(yast, Num):
            t = _trunc(yast.value)
            return self.num(1.0) if t == 0 else self.call("pow", [x, self.num(t)])
        ty = self.T(self.p(yast))
        xt = self.call("pow", [x, ty])
        if x_nonzero:
            return xt
        return self.tern(self.cmp("==", ty, self.num(0.0)), self.num(1.0), xt)

    def power(self, e: Binary) -> _F:
        """HSPICE x**y: x>0 -> x^y; x<0 -> x^T(y); x=0 -> 0."""
        x = self.p(e.left)
        zero = self.num(0.0)
        y = e.right
        if isinstance(y, Num):
            if y.value == _trunc(y.value):
                xy = self.call("pow", [x, self.num(y.value)])
                if y.value > 0:
                    return xy                               # exact for every x, 0 included
                return self.tern(self.cmp("==", x, zero), zero, xy)
        elif isinstance(e.left, Num):                       # constant x, varying y
            if e.left.value > 0:
                return self.call("pow", [x, self.p(y)])
            return self.pow_t(x, y, True) if e.left.value < 0 else zero
        return self.tern(self.cmp(">", x, zero), self.call("pow", [x, self.p(y)]),
                         self.tern(self.cmp("<", x, zero), self.pow_t(x, y, True), zero))

    def func(self, e: Call) -> _F:
        f = e.func
        if f not in FUNCS:
            raise PrintError("unknown function %s() (user functions must be inlined first)" % f)
        check_arity(e, PrintError)
        if f in _NOMINAL or (f == "limit" and len(e.args) == 2):
            for a in e.args[1:]:
                self.p(a)                                   # still checked for the context
            return self.p(e.args[0])
        if f == "if":
            return self.tern(self.p(e.args[0]), self.p(e.args[1]), self.p(e.args[2]))
        if f == "pow":
            return self.pow_t(self.p(e.args[0]), e.args[1])
        a = [self.p(x) for x in e.args]
        if f in _SAME_NAME:
            return self.call(f, a)
        if f == "abs":
            return self.absf(a[0])
        if f == "ceil":
            return self.ceil(a[0])
        if f in ("min", "dmin", "max", "dmax"):
            return self.minmax(f[-3:], a)
        if f == "limit":
            return self.minmax("min", [self.minmax("max", a[:2]), a[2]])
        if f in ("int", "trunc"):
            return self.T(a[0])
        if f == "nint":
            return self.nint(a[0])
        if f == "sgn" or (f == "sign" and len(a) == 1):
            return self.S(a[0])
        if f == "sign":
            return self.arith("*", self.absf(a[0]), self.S(a[1]))
        if f == "atan2":
            return self.atan2(a[0], a[1])
        x = a[0]
        ax = self.guard(self.absf(x))
        if f == "pwr":
            return self.arith("*", self.S(x), self.call("pow", [ax, a[1]]))
        if f == "sqrt":
            return self.arith("*", self.S(x), self.call("sqrt", [ax]))
        if f in ("log", "ln"):
            return self.arith("*", self.S(x), self.call("ln", [ax]))
        if f == "log10":
            return self.arith("*", self.S(x), self.call("log10", [ax]))
        if f == "db":
            return self.arith("*", self.S(x),
                              self.arith("*", self.num(20.0), self.call("log10", [ax])))
        raise PrintError("function %s() has no %s form" % (f, self.engine))


def _fmt(v: float) -> str:
    try:
        return fmt(v)
    except ValueError as exc:
        raise PrintError(str(exc))


class _VacaskPrinter(_Printer):
    engine = "vacask"

    def num(self, v: float) -> _F:
        if v != 0 and abs(v) < _MIN_NORMAL:
            raise PrintError("literal %r is subnormal: VACASK rejects it" % v)
        if not self.beh:
            return super().num(v)
        # Behavioral: the Verilog-A translation prints integer-valued reals as
        # integer literals (%.17g), so they are integers there.
        integral = v == _trunc(v) and abs(v) < 1e17
        if not integral or abs(v) < _BIG:
            f = super().num(v)
            f.typ = "i" if integral else "r"
            return f
        if abs(v) < _EXACT_HALF:                            # v = (v+0.5) - 0.5 exactly
            return _F("(%s-0.5)" % _fmt(v + 0.5))
        r = v / _TWO30                                      # exact; |r| < 2**27
        rt = "(%s-0.5)" % _fmt(r + 0.5) if r == _trunc(r) else _fmt(r)
        return _F("(%s*%s)" % (rt, _fmt(_TWO30)))

    def whole(self, ast: Expr, text: str) -> str:
        # VACASK folds a wholly constant behavioral expression itself and would
        # print the integer literal that crashes openvaf-r: keep it varying.
        if self.beh and isinstance(ast, Num) and ast.value == _trunc(ast.value) and \
                _BIG <= abs(ast.value) < 1e17:
            return text + "+0.0*$abstime"
        return text

    def val(self, f: _F) -> _F:
        if f.typ == "i" and not self.beh:
            return _F("real(%s)" % f.text, _ATOM, "r")
        return f

    def arith(self, op: str, l: _F, r: _F) -> _F:
        if self.beh and l.typ == "i" and r.typ == "i":
            l = _F("floor(%s)" % l.text, _ATOM, "r")       # else Verilog-A integer arithmetic
        return super().arith(op, l, r)

    def absf(self, x: _F) -> _F:
        return self.call("abs", [x], "i" if (self.beh and x.typ == "i") else "r")

    def minmax(self, func: str, args: Sequence[_F]) -> _F:
        f = args[0]
        for a in args[1:]:
            f = self.call(func, [f, a], "i" if (self.beh and f.typ == "i" and a.typ == "i") else "r")
        return f

    def not_(self, x: _F) -> _F:
        return _F("(!%s)" % self.paren(x, _ATOM), _ATOM, "i")

    def T(self, y: _F) -> _F:
        if not self.beh:
            return self.call("integer", [y])                 # a real truncation; int() is int32
        return super().T(y)

    def ceil(self, x: _F) -> _F:
        if self.beh:                                        # ceil() crashes openvaf-r
            return self.neg(self.call("floor", [self.neg(x)]))
        return self.call("ceil", [x])

    def nint(self, x: _F) -> _F:
        if not self.beh:
            return self.call("round", [x])                   # std::round: half away from zero
        half = self.num(0.5)
        return self.tern(self.cmp(">=", x, self.num(0.0)),
                         self.call("floor", [self.arith("+", x, half)]),
                         self.neg(self.call("floor", [self.arith("+", self.neg(x), half)])))

    def atan2(self, y: _F, x: _F) -> _F:
        if self.beh:
            return self.call("atan2", [y, x])
        # VACASK's parameter atan2 returns atan(y/x) for x<0: spell out the quadrants.
        zero = self.num(0.0)
        at = self.call("atan", [self.arith("/", y, x)])
        pi = self.num(math.pi)
        return self.tern(
            self.cmp(">", x, zero), at,
            self.tern(self.cmp("<", x, zero),
                      self.tern(self.cmp(">=", y, zero), self.arith("+", at, pi),
                                self.arith("-", at, pi)),
                      self.tern(self.cmp(">", y, zero), self.num(math.pi / 2),
                                self.tern(self.cmp("<", y, zero), self.num(-math.pi / 2), zero))))

    def temper(self) -> _F:
        return _F("$temp")

    def time(self) -> _F:
        return _F("$abstime")

    def node_text(self, kind: str, name: str) -> str:
        if self.node_fn is not None:
            return self.node_fn(kind, name)
        return vacask_quote(name.replace(".", ":"))


class _XycePrinter(_Printer):
    engine = "xyce"

    def not_(self, x: _F) -> _F:                            # Xyce has no '!'
        return _F("(%s==0.0)" % self.paren(x, _ADD), _ATOM)

    def S(self, x: _F) -> _F:
        return self.call("sgn", [x])                        # Xyce's SGN(0) is 0

    def temper(self) -> _F:
        return _F("temp")

    def time(self) -> _F:
        return _F("time")


def to_vacask(ast: Expr, ctx: str = "param",
              node: Optional[Callable[[str, str], str]] = None) -> str:
    """VACASK text for ast in ctx ('param' or 'behavioral'); see the module docstring."""
    return _VacaskPrinter(ctx, node).emit(ast)


def to_xyce(ast: Expr, ctx: str = "param", node: Optional[Callable[[str, str], str]] = None) -> str:
    """Xyce text for ast in ctx ('param' or 'behavioral'); see the module docstring."""
    return _XycePrinter(ctx, node).emit(ast)
