"""The Spectre expression dialects of expr.py (docs/VAMOS_SPECTRE_DESIGN.md §3.3, §3.5, §4.2; owner S1).

    python3 -m unittest discover -s tests/vamos -p 'test_spectre_expr.py' -v

§11.1 "Lexer and numbers" and "Expression dialect": the number tables of both modes (the 38 literals
of E52 against UG p.84), the binding-power table, cpow/hypot/fmod and the bitwise folding, Spectre's
domains, the constants, temp/tnom, user functions, vectors, to_text, the printers' new forms, and
the HSPICE dialect unchanged.  Runs on both legs, no engine.
"""

import math
import unittest

import vamos_testlib  # noqa: F401  (puts the repo root on sys.path)

from vamos.netlist import expr as E  # noqa: E402
from vamos.netlist.expr_ast import Binary, Call, Name, Num, Str, Ternary, Unary  # noqa: E402
from vamos.notes import NOTE, WARNING  # noqa: E402


def P(text, dialect="spectre", case="sensitive", **kw):
    return E.parse(text, case, dialect, **kw)


def SP(text, **kw):
    return E.parse(text, "lower", "spectre-spice", **kw)


def ev(text, scope=None, dialect="spectre"):
    return E.evaluate(P(text, dialect, "sensitive" if dialect == "spectre" else "lower"), scope or {}, dialect)


class Recorder:
    def __init__(self):
        self.notes = []

    def __call__(self, severity, message):
        self.notes.append((severity, message))


# -- §3.3 numbers --------------------------------------------------------------------------------------

# the 38 literals of E52 against UG p.84: (literal, Spectre mode value or "error", SPICE mode value or "error")
LITERALS = [
    ("5u", 5e-6, 5e-6), ("3.26k", 3260.0, 3260.0), ("4.2m", 4.2e-3, 4.2e-3), ("2.65e3", 2650.0, 2650.0),
    ("5.32e-4", 5.32e-4, 5.32e-4), ("1E-14", 1e-14, 1e-14), ("3.04E6", 3.04e6, 3.04e6), ("23pf", 23e-12, 23e-12),
    ("1uH", 1e-6, 1e-6), ("6.3ns", 6.3e-9, 6.3e-9), ("27_C", 27.0, 27.0), ("50_Ohms", 50.0, 50.0),
    (".148p", 0.148e-12, 0.148e-12), ("0.22u", 2.2e-07, 2.2e-07), (".55", 0.55, 0.55), ("42", 42.0, 42.0),
    ("1.5k", 1500.0, 1500.0), ("1T", 1e12, 1e12), ("1G", 1e9, 1e9), ("1M", 1e6, 1e-3), ("1K", 1e3, 1e3),
    ("1k", 1e3, 1e3), ("1c", 1e-2, 1.0), ("5%", 0.05, "error"), ("1n", 1e-9, 1e-9), ("1f", 1e-15, 1e-15),
    ("1a", 1e-18, 1.0), ("0.3MHz", 3e5, 3e-4), ("1.234E-3p", 1.234e-3, 1.234e-3), ("1meg", 1e-3, 1e6),
    ("1mil", 1e-3, 25.4e-6), ("50Ohms", "error", 50.0), ("1P", "error", 1e-12), ("1x", "error", 1.0),
    ("1F", "error", 1e-15), ("1D3", "error", "error"), ("-.5u", -5e-7, -5e-7), ("2E+5", 2e5, 2e5),
]


class TestNumbers(unittest.TestCase):
    def test_the_38_literals_in_both_modes(self):
        self.assertEqual(len(LITERALS), 38)
        for lit, want_sp, want_spice in LITERALS:
            for dialect, want in (("spectre", want_sp), ("spectre-spice", want_spice)):
                with self.subTest(literal=lit, dialect=dialect):
                    if want == "error":
                        with self.assertRaises(ValueError):
                            E.number(lit, dialect)
                    else:
                        self.assertEqual(E.number(lit, dialect), want)

    def test_spectre_mode_notes_and_warnings(self):
        r = Recorder()
        self.assertEqual(E.number("1.234E-3p", "spectre", r), 1.234e-3)
        self.assertEqual(r.notes[0][0], NOTE)
        self.assertIn("ignored", r.notes[0][1])
        r = Recorder()
        self.assertEqual(E.number("1meg", "spectre", r), 1e-3)
        self.assertEqual(r.notes[0][0], WARNING)
        self.assertIn("m is milli", r.notes[0][1])
        r = Recorder()
        self.assertEqual(E.number("1mil", "spectre", r), 1e-3)
        self.assertEqual(r.notes[0][0], WARNING)
        r = Recorder()
        self.assertEqual(E.number("1MEG", "spectre", r), 1e6)                  # §14 q.6: M is mega, EG units
        self.assertEqual(r.notes, [])
        with self.assertRaises(ValueError) as cm:
            E.number("50Ohms", "spectre")
        self.assertIn("units without a scale factor", str(cm.exception))

    def test_spice_mode_notes_and_warnings(self):
        for lit, letter in (("1x", "x"), ("1a", "a")):
            r = Recorder()
            self.assertEqual(E.number(lit, "spectre-spice", r), 1.0)
            self.assertEqual(r.notes[0][0], WARNING)
            self.assertIn("HSPICE reads %s" % letter, r.notes[0][1])
        r = Recorder()
        self.assertEqual(E.number("5V", "spectre-spice", r), 5.0)
        self.assertEqual(E.number("10Ohm", "spectre-spice", r), 10.0)
        self.assertEqual(r.notes, [])
        r = Recorder()
        self.assertEqual(E.number("1e-3p", "spectre-spice", r), 1e-3)
        self.assertEqual(r.notes[0][0], NOTE)
        with self.assertRaises(ValueError):
            E.number("1.0D+3", "spectre-spice")
        self.assertEqual(E.number("2.5D", "spectre-spice"), 2.5)             # D as a unit letter
        self.assertEqual(E.number("1MeG", "spectre-spice"), 1e6)              # case insensitive

    def test_hspice_reader_unchanged(self):
        self.assertEqual(E.number("1.0D+3"), 1000.0)
        self.assertEqual(E.number("1M"), 1e-3)
        self.assertEqual(E.number("1x"), 1e6)
        self.assertEqual(E.number("0.22u"), 2.2e-07)
        with self.assertRaises(ValueError):
            E.number("1k", "spice")

    def test_spectre_number_partial(self):
        from vamos.netlist import spectre
        self.assertEqual(spectre.number("1M"), 1e6)
        self.assertEqual(spectre.number("5%"), 0.05)

    def test_numbers_in_expressions_follow_the_table(self):
        self.assertEqual(ev("2*1M"), 2e6)
        self.assertEqual(ev("5%"), 0.05)
        self.assertEqual(ev("27_C + 1"), 28.0)
        self.assertEqual(E.evaluate(SP("2*1M"), {}, "spectre-spice"), 2e-3)
        with self.assertRaises(E.ExprError):
            P("50Ohms")
        with self.assertRaises(E.ExprError):
            P("1D3")
        with self.assertRaises(E.ExprError):
            SP("1.0D+3")


# -- §3.5 the dialect ------------------------------------------------------------------------------------

class TestPrecedence(unittest.TestCase):
    def test_binding_powers(self):
        self.assertEqual(ev("-2**2"), 4.0)
        self.assertEqual(ev("2**3**2"), 512.0)
        self.assertEqual(ev("2**-1"), 0.5)
        self.assertEqual(P("a || b && c"), Binary("||", Name("a"), Binary("&&", Name("b"), Name("c"))))
        self.assertEqual(P("a | b & c"), Binary("|", Name("a"), Binary("&", Name("b"), Name("c"))))
        self.assertEqual(P("a & b == c"), Binary("&", Name("a"), Binary("==", Name("b"), Name("c"))))
        self.assertEqual(P("a == b < c"), Binary("==", Name("a"), Binary("<", Name("b"), Name("c"))))
        self.assertEqual(P("a < b << c"), Binary("<", Name("a"), Binary("<<", Name("b"), Name("c"))))
        self.assertEqual(P("a << b + c"), Binary("<<", Name("a"), Binary("+", Name("b"), Name("c"))))
        self.assertEqual(P("a ~^ b & c"), Binary("~^", Name("a"), Binary("&", Name("b"), Name("c"))))
        self.assertEqual(P("a ^~ b"), Binary("~^", Name("a"), Name("b")))
        self.assertEqual(P("a ? b : c ? d : e"), Ternary(Name("a"), Name("b"), Ternary(Name("c"), Name("d"), Name("e"))))
        self.assertEqual(P("!a"), Unary("!", Name("a")))
        self.assertEqual(ev("!0"), 1.0)
        self.assertEqual(ev("7/2"), 3.5)

    def test_power_is_cpow(self):
        self.assertEqual(P("a**b"), Call("cpow", (Name("a"), Name("b"))))
        self.assertEqual(P("pow(a,b)"), Call("cpow", (Name("a"), Name("b"))))
        self.assertEqual(ev("pow(2,1.5)"), 2.8284271247461903)
        self.assertEqual(ev("2**1.5"), 2.8284271247461903)
        self.assertEqual(ev("0**0"), 1.0)
        for text in ("(-8)**(1/3)", "0**-1"):
            with self.assertRaises(E.EvalError):
                ev(text)

    def test_hspice_power_unchanged(self):
        self.assertEqual(E.evaluate(E.parse("-2**2"), {}), -4.0)
        self.assertEqual(E.evaluate(E.parse("0**0"), {}), 0.0)
        self.assertEqual(E.evaluate(E.parse("pow(2, 1.5)"), {}), 2.0)
        self.assertEqual(E.parse("a^b"), E.parse("a**b"))

    def test_caret_and_other_non_operators(self):
        for text in ("a^b", "a%b", "a~&b", "a~|b", "a <> b", "~a", "8'hFF", "[1 2]"):
            with self.assertRaises(E.ExprError, msg=text):
                P(text)
            with self.assertRaises(E.ExprError, msg=text):
                SP(text)


class TestEvaluate(unittest.TestCase):
    def test_extra_functions_and_bitwise_in_every_dialect(self):
        for d in E.DIALECTS:
            self.assertEqual(E.evaluate(Call("cpow", (Num(2.0), Num(3.0))), {}, d), 8.0)
            self.assertEqual(E.evaluate(Call("hypot", (Num(3.0), Num(4.0))), {}, d), 5.0)
            self.assertEqual(E.evaluate(Call("fmod", (Num(-7.0), Num(3.0))), {}, d), -1.0)
            self.assertEqual(E.evaluate(Binary("&", Num(3.0), Num(1.0)), {}, d), 1.0)
            self.assertEqual(E.evaluate(Binary("|", Num(4.0), Num(1.0)), {}, d), 5.0)
            self.assertEqual(E.evaluate(Binary("<<", Num(1.0), Num(10.0)), {}, d), 1024.0)
            self.assertEqual(E.evaluate(Binary(">>", Num(1024.0), Num(3.0)), {}, d), 128.0)
        self.assertEqual(E.fold(Call("cpow", (Num(2.0), Num(3.0))), strict=True), Num(8.0))
        self.assertEqual(E.fold(P("hypot(3,4) + fmod(-7,3) + (3 & 1)"), strict=True), Num(5.0))

    def test_bitwise_rules(self):
        for text in ("a ~^ b", "1.5 & 2", "-1 & 2", "1 << 60", "2**53 | 1"):
            with self.assertRaises(E.EvalError, msg=text):
                ev(text, {"a": 3, "b": 1})
        with self.assertRaises(E.EvalError):
            E.evaluate(Call("fmod", (Num(1.0), Num(0.0))), {})

    def test_domains(self):
        for text in ("log(-1)", "ln(0)", "log10(-2)", "sqrt(-1)", "exp(80)", "exp(81)"):
            with self.assertRaises(E.EvalError, msg=text):
                ev(text)
            with self.assertRaises(E.EvalError, msg=text):
                E.evaluate(SP(text), {}, "spectre-spice")
        self.assertEqual(ev("sqrt(4)"), 2.0)
        self.assertEqual(ev("exp(79)"), math.exp(79))
        self.assertEqual(ev("int(-2.7)"), -2.0)
        self.assertEqual(ev("sign(-3, 2)"), 3.0)
        self.assertEqual(ev("atan2(1, 1)"), math.atan(1.0))
        # the default dialect keeps HSPICE's sign-keeping forms, identical in the domain
        self.assertEqual(E.evaluate(P("sqrt(-4)"), {}), -2.0)
        self.assertEqual(E.evaluate(P("sqrt(4)"), {}), 2.0)

    def test_no_short_circuit(self):
        self.assertEqual(ev("0 && 1"), 0.0)
        self.assertEqual(ev("1 || 0"), 1.0)
        with self.assertRaises(E.EvalError):
            ev("0 && (1/0)")
        with self.assertRaises(E.EvalError):
            ev("1 || log(-1)")
        self.assertEqual(E.evaluate(E.parse("0 && (1/0)"), {}), 0.0)       # HSPICE: lazy

    def test_dialect_names(self):
        with self.assertRaises(ValueError):
            E.evaluate(Num(1.0), {}, "xyce")
        with self.assertRaises(ValueError):
            E.parse("1", dialect="hspice2")
        with self.assertRaises(ValueError):
            E.to_text(Num(1.0), "")
        with self.assertRaises(ValueError):
            E.number("1k", "spice")


class TestFunctionsAndNames(unittest.TestCase):
    def test_closed_table(self):
        for text in ("db(10)", "agauss(1,0.1,3)", "gauss(1,0.1,3)", "sign(x)", "nint(2.5)", "pwr(2,3)", "if(1,2,3)",
                     "limit(1,2,3)", "dmin(1,2)", "trunc(1.5)", "min(1,2,3)", "sqrt(1,2)", "foo(1)"):
            with self.assertRaises(E.ExprError, msg=text):
                P(text)
        self.assertEqual(ev("min(1,2) + max(1,2)"), 3.0)
        self.assertEqual(ev("hypot(3,4)"), 5.0)
        self.assertEqual(ev("fmod(-7,3)"), -1.0)
        self.assertEqual(P("floor(x)"), Call("floor", (Name("x"),)))
        with self.assertRaises(E.ExprError):
            P("Sqrt(4)")                                   # function names keep their case in Spectre mode
        self.assertEqual(SP("SQRT(4)"), Call("sqrt", (Num(4.0),)))   # SPICE mode folds case

    def test_no_behavioral_expressions(self):
        for text in ("V(out)", "v(a,b)", "I(v1)", "vm(x)", "time", "hertz", "time*2"):
            with self.assertRaises(E.ExprError, msg=text) as cm:
                P(text)
            self.assertIn("behavioral", str(cm.exception))

    def test_temp_tnom_temper(self):
        self.assertEqual(P("temp"), Name("temper"))
        self.assertEqual(P("tnom"), Name("$tnom"))
        self.assertEqual(P("temp + tnom"), Binary("+", Name("temper"), Name("$tnom")))
        self.assertEqual(SP("TEMP+tnom"), Binary("+", Name("temper"), Name("$tnom")))
        with self.assertRaises(E.ExprError) as cm:
            P("temper")
        self.assertIn("the temperature is temp", str(cm.exception))
        self.assertEqual(SP("temper"), Name("temper"))     # HSPICE's spelling in SPICE mode
        self.assertEqual(P("Temp"), Name("Temp"))          # Spectre mode is case sensitive
        self.assertEqual(E.names(P("temp*r+tnom")), {"r"})
        self.assertEqual(E.fold(P("temp*2"), {"temper": 27.0}), P("temp*2"))        # non-constant
        with self.assertRaises(E.EvalError):
            ev("temp+1", {"temper": 27.0})
        self.assertEqual(E.evaluate(E.substitute(P("temp+tnom"), {"temper": Num(27.0), "$tnom": Num(25.0)}), {}), 52.0)

    def test_constants(self):
        self.assertEqual(P("M_PI"), Num(math.pi))
        self.assertEqual(P("M_DEGPERRAD"), Num(180.0 / math.pi))
        self.assertEqual(P("P_Q*2"), Binary("*", Num(1.6021918e-19), Num(2.0)))
        self.assertEqual(ev("2*M_PI"), 2 * math.pi)
        self.assertEqual(SP("m_pi"), Num(math.pi))         # SPICE mode: case insensitive
        self.assertEqual(SP("M_E"), Num(math.e))
        self.assertEqual(P("m_pi"), Name("m_pi"))          # Spectre mode: exact case
        self.assertEqual(E.names(P("M_PI*r")), {"r"})

    def test_user_functions(self):
        funcs = {"MyF": (["a", "b"], P("a*2+b")), "vt": (["x"], P("x+1")), "sqrt": (["x"], P("x*x"))}
        self.assertEqual(P("MyF(1, q)", funcs=funcs), Binary("+", Binary("*", Num(1.0), Num(2.0)), Name("q")))
        self.assertEqual(P("vt(3)", funcs=funcs), Binary("+", Num(3.0), Num(1.0)))     # a user vt() wins
        self.assertEqual(P("sqrt(3)", funcs=funcs), Binary("*", Num(3.0), Num(3.0)))   # named like a built-in
        with self.assertRaises(E.ExprError):
            P("MyF(1)", funcs=funcs)
        with self.assertRaises(E.ExprError):
            P("myf(1,2)", funcs=funcs)                    # case as written
        self.assertEqual(SP("myf(1,2)", funcs={"myf": (["a", "b"], SP("a+b"))}), Binary("+", Num(1.0), Num(2.0)))
        with self.assertRaises(NotImplementedError):
            E.parse("f(1)", funcs={"f": (["a"], Name("a"))})   # HSPICE: inline() stays its route (§4.3)

    def test_user_function_without_arguments(self):
        # R-lang-21: real myfunc( [real arg1, ...real argn] ) (ref19 p.481): a function declared without
        # arguments is called with (); any other empty call stays an error
        funcs = {"rdef": ([], Num(2000.0)), "dt": ([], P("temp-tnom")), "f1": (["x"], P("x"))}
        self.assertEqual(P("rdef()", funcs=funcs), Num(2000.0))
        self.assertEqual(P("1k*(1+0.001*dt())", funcs=funcs),
                         Binary("*", Num(1000.0), Binary("+", Num(1.0), Binary("*", Num(0.001),
                                                                                 Binary("-", Name("temper"), Name("$tnom"))))))
        for text in ("f1()", "sin()", "nosuch()"):
            with self.assertRaises(E.ExprError):
                P(text, funcs=funcs)

    def test_spice_mode_groupings(self):
        self.assertEqual(SP("par'a*2'"), Binary("*", Name("a"), Num(2.0)))
        self.assertEqual(SP("par 'a*2'"), Binary("*", Name("a"), Num(2.0)))
        self.assertEqual(SP("{a*2}"), Binary("*", Name("a"), Num(2.0)))
        self.assertEqual(SP("'a*2'"), Binary("*", Name("a"), Num(2.0)))
        self.assertEqual(SP("1 + {a*2}"), Binary("+", Num(1.0), Binary("*", Name("a"), Num(2.0))))
        with self.assertRaises(E.ExprError):
            P("{a*2}")                                     # not a Spectre-mode grouping

    def test_strings(self):
        self.assertEqual(P('"abc"'), Str("abc"))


class TestToText(unittest.TestCase):
    def test_spectre_spelling_round_trips(self):
        for text in ("-(a**2)", "-a**2", "a**b**c", "a ? b : c ? d : e", "(a ? b : c) ? d : e", "a << b & c",
                     "a | b ~^ c", "a - (b - c)", "hypot(a, b) * fmod(a, 3)", "!a && b", "a - -b", "2**-1"):
            ast = P(text)
            back = E.to_text(ast, "spectre")
            self.assertEqual(P(back), ast, "%s -> %s" % (text, back))
        self.assertEqual(E.to_text(P("-(a**2)"), "spectre"), "-(a**2.0)")   # Spectre reads -a**2 as (-a)**2 [E50]
        self.assertEqual(E.to_text(P("-a**2"), "spectre"), "-a**2.0")
        self.assertEqual(E.to_text(P("pow(a,b)"), "spectre-spice"), "a**b")

    def test_hspice_spelling_unchanged_and_knows_the_bitwise_operators(self):
        self.assertEqual(E.to_text(E.parse("-2**2")), "-2.0**2.0")
        self.assertEqual(E.to_text(E.parse("a ? b : c ? d : e")), "a ? b : c ? d : e")
        self.assertEqual(E.to_text(E.parse("-(a+b)*c**-d")), "-(a + b) * c**-d")
        self.assertEqual(E.to_text(E.parse("a + b*2")), "a + b * 2.0")
        for op in ("&", "|", "~^", "<<", ">>"):
            text = E.to_text(Binary(op, Name("a"), Binary("+", Name("b"), Num(1.0))))
            self.assertEqual(text, "a %s b + 1.0" % op)
        self.assertEqual(E.to_text(Call("cpow", (Name("a"), Num(2.0)))), "cpow(a, 2.0)")
        self.assertIn("~^", E.to_text(P("a ~^ b"), "spectre"))

    def test_messages_raise_no_keyerror(self):
        e = Binary("~^", Name("a"), Name("b"))
        with self.assertRaises(E.EvalError):
            E.evaluate(e, {"a": 1, "b": 2})
        for pr in (E.to_vacask, E.to_xyce):
            with self.assertRaises(E.PrintError):
                pr(e)
        with self.assertRaises(E.PrintError):
            E.to_xyce(Binary("<<", Name("a"), Num(2.0)))


# -- §4.2 the printers -----------------------------------------------------------------------------------

class TestPrinters(unittest.TestCase):
    def test_cpow(self):
        self.assertEqual(E.to_vacask(P("a**b")), "(b==0.0 ? 1.0 : pow(a, b))")
        self.assertEqual(E.to_xyce(P("a**b")), "(b==0.0 ? 1.0 : pow(a, b))")
        self.assertEqual(E.to_vacask(P("a**2")), "pow(a, 2.0)")
        self.assertEqual(E.to_xyce(P("2**b")), "pow(2.0, b)")
        self.assertEqual(E.to_vacask(P("a**0")), "1.0")
        self.assertEqual(E.to_vacask(P("2**3")), "8.0")                        # a constant folds
        self.assertEqual(E.to_xyce(P("r0*pow(x, 1.5)")), "r0*pow(x, 1.5)")     # [E54]: C pow, never T(y)

    def test_hypot_fmod(self):
        self.assertEqual(E.to_vacask(P("hypot(a,b)")), "hypot(a, b)")
        self.assertEqual(E.to_xyce(P("hypot(a,b)")), "sqrt(a*a+b*b)")
        self.assertEqual(E.to_vacask(P("fmod(a,3)")), "fmod(a, 3.0)")
        self.assertEqual(E.to_xyce(P("fmod(a,3)")), "fmod(a, 3.0)")
        self.assertEqual(E.to_xyce(P("hypot(3,4)")), "5.0")

    def test_temp_and_tnom(self):
        self.assertEqual(E.to_vacask(P("temp+tnom")), "$temp+$tnom")
        self.assertEqual(E.to_xyce(E.substitute(P("temp+tnom"), {"$tnom": Num(27.0)})), "temp+27.0")
        with self.assertRaises(E.PrintError):
            E.to_xyce(P("tnom"))

    def test_bitwise_operators_are_print_errors(self):
        for op in ("&", "|", "~^", "<<", ">>"):
            for pr in (E.to_vacask, E.to_xyce):
                with self.assertRaises(E.PrintError):
                    pr(Binary(op, Name("a"), Name("b")))
        self.assertEqual(E.to_vacask(P("3 & 1")), "1.0")                        # a constant folds first

    def test_hspice_rejects_the_extra_functions_at_parse_time(self):
        """§4.2: the HSPICE dialect rejects cpow hypot fmod as unknown functions, the same error as
        today only earlier (an ExprError that is also the EvalError and PrintError the later stages
        raised, so test_spectre_shared's phase-0 pin holds)."""
        for text in ("cpow(2,3)", "hypot(3,4)", "fmod(-7,3)"):
            with self.assertRaises(E.ExprError):
                E.parse(text)
            with self.assertRaises(E.EvalError):
                E.evaluate(E.parse(text), {})
            with self.assertRaises(E.PrintError):
                E.to_vacask(E.parse(text))
        self.assertEqual(E.evaluate(E.parse("sqrt(4)"), {}), 2.0)

    def test_vacask_quote_leading_zero(self):
        self.assertEqual(E.vacask_quote("007"), "'007'")
        self.assertEqual(E.vacask_quote("00"), "'00'")
        self.assertEqual(E.vacask_quote("0"), "0")
        self.assertEqual(E.vacask_quote("7"), "7")
        self.assertEqual(E.vacask_quote("10"), "10")


class TestVectors(unittest.TestCase):
    def test_elements(self):
        from vamos.netlist import spectre
        self.assertEqual(spectre._vector_elements("0.5 1 +p2 (sqrt(p2*p2))"), ["0.5", "1", "+p2", "(sqrt(p2*p2))"])
        self.assertEqual(spectre._vector_elements("1+p2"), ["1", "+p2"])
        self.assertEqual(spectre._vector_elements(" 0 var +3 -2 (inparen) var (inparen+1u) (var-(1-x)) "),
                         ["0", "var", "+3", "-2", "(inparen)", "var", "(inparen+1u)", "(var-(1-x))"])
        self.assertEqual(spectre._vector_elements("25 75 100"), ["25", "75", "100"])


if __name__ == "__main__":
    unittest.main()
