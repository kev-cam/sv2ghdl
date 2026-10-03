"""HSPICE expressions: parser, evaluator, printers (docs/VAMOS_AMS_DESIGN.md §4.1, §9).

    python3 -m unittest discover -s tests/vamos -p 'test_netlist_expr*.py' -v

The engine classes run where VACASK / Xyce are installed (WSL).  They print
every case of ENGINE_CASES for one engine and context into one deck (each
case its own source), run it, and require the engine's value to equal
evaluate()'s HSPICE value; the cross-engine class requires both engines'
parameter values to be identical.
"""

import math
import os
import unittest

from vamos_testlib import TempDir, needs_vacask, needs_xyce, openvaf_bin, run, vacask_bin, \
    xyce_bin, xyce_env

from vamos.netlist import expr as E  # noqa: E402
from vamos.netlist import rawfile  # noqa: E402
from vamos.netlist.expr_ast import Binary, Call, Name, Num, Str, Ternary, Unary  # noqa: E402


def P(text, **kw):
    return E.parse(text, **kw)


def V(*nodes):
    return Call("v", tuple(Name(n) for n in nodes))


class TestParse(unittest.TestCase):
    def test_numbers_and_suffixes(self):
        cases = {"1k": 1e3, "1.5n": 1.5e-9, "2meg": 2e6, "10ns": 1e-8, "1e-3": 1e-3, ".5": 0.5,
                 "5.": 5.0, "1e3k": 1e6, "1mil": 25.4e-6, "3x": 3e6, "-2": -2.0, "+4": 4.0,
                 "100a": 1e-16, "1t": 1e12}
        for text, v in cases.items():
            e = P(text)
            self.assertIsInstance(e, Num, text)
            self.assertAlmostEqual(e.value, v, delta=abs(v) * 1e-15, msg=text)

    def test_numbers_are_correctly_rounded(self):
        """A suffix scales the decimal exponent (ngspice reads 0.22u as 22e-8), so '0.22u' and
        '2.2e-7' are the same double: a bin edge written either way is one edge."""
        from vamos.netlist.numbers import parse_number
        for text, v in (("0.22u", 2.2e-07), ("60n", 6e-08), ("1.5n", 1.5e-09), ("0.15u", 1.5e-07),
                        ("2meg", 2e6), ("-3.3", -3.3), (".5p", 5e-13), ("1e3k", 1e6), ("+2.5e-3u", 2.5e-09),
                        ("1.2v", 1.2), ("10ns", 1e-08), ("7", 7.0), ("1mil", 25.4e-6)):
            self.assertEqual(E.number(text), v, text)
            self.assertEqual(P(text), Num(v), text)
            self.assertAlmostEqual(E.number(text), parse_number(text), delta=abs(v) * 4e-16, msg=text)
        self.assertEqual(parse_number("0.22u"), 2.2e-07)       # parse_number scales the exponent too
        self.assertEqual(E.evaluate(P("0.22*1e-6"), {}), 0.22 * 1e-6)   # an expression keeps its rounding
        for bad in ("u", "1..2", "e3", ""):
            with self.assertRaises(ValueError):
                E.number(bad)

    def test_d_exponent(self):
        """HSPICE: "Exponents are designated by D or E" (Star-HSPICE 2001.2, 3-4).  A signed D
        exponent was read as a unit letter and a subtraction: '2.5D-12' gave 2.5-12 = -9.5."""
        for text, v in (("1D3", 1e3), ("1.0D+3", 1e3), ("2.5d-12", 2.5e-12), ("-1.5D-2", -0.015),
                        ("3.3D+00", 3.3), ("50D-15", 5e-14), ("1.0d-14", 1e-14), ("2.5D-6u", 2.5e-12)):
            self.assertEqual(E.number(text), v, text)
            self.assertEqual(P(text), Num(v), text)
        # a D with no digits after it stays a unit letter
        for text, v in (("2.5D", 2.5), ("1d", 1.0), ("10dB", 10.0), ("5day", 5.0)):
            self.assertEqual(E.number(text), v, text)
            self.assertEqual(P(text), Num(v), text)
        self.assertEqual(E.evaluate(P("'2*1.0D+3'"), {}), 2000.0)
        self.assertEqual(E.evaluate(P("1.0D+3+1"), {}), 1001.0)
        self.assertEqual(E.evaluate(P("x*2.5d-12"), {"x": 2.0}), 5e-12)

    def test_precedence_and_associativity(self):
        self.assertEqual(P("1+2*3"), Binary("+", Num(1.0), Binary("*", Num(2.0), Num(3.0))))
        self.assertEqual(P("a-b-c"), Binary("-", Binary("-", Name("a"), Name("b")), Name("c")))
        self.assertEqual(P("a/b/c"), Binary("/", Binary("/", Name("a"), Name("b")), Name("c")))
        # power: right associative, tighter than unary minus on its left
        self.assertEqual(P("-a**2"), Unary("-", Binary("**", Name("a"), Num(2.0))))
        self.assertEqual(P("a**-b"), Binary("**", Name("a"), Unary("-", Name("b"))))
        self.assertEqual(P("a**b**c"), Binary("**", Name("a"), Binary("**", Name("b"), Name("c"))))
        self.assertEqual(P("a^b"), P("a**b"))
        self.assertEqual(P("-a*b"), Binary("*", Unary("-", Name("a")), Name("b")))
        self.assertEqual(P("!a==b"), Binary("==", Unary("!", Name("a")), Name("b")))
        # C levels: || < && < == != < relational < additive
        self.assertEqual(P("a||b&&c"), Binary("||", Name("a"), Binary("&&", Name("b"), Name("c"))))
        self.assertEqual(P("a==b<c"), Binary("==", Name("a"), Binary("<", Name("b"), Name("c"))))
        self.assertEqual(P("a<b+c"), Binary("<", Name("a"), Binary("+", Name("b"), Name("c"))))
        self.assertEqual(P("a?b:c?d:e"), Ternary(Name("a"), Name("b"),
                                                 Ternary(Name("c"), Name("d"), Name("e"))))
        self.assertEqual(P("a||b?c:d"), Ternary(Binary("||", Name("a"), Name("b")), Name("c"),
                                                Name("d")))
        self.assertEqual(P("a?b?c:d:e"), Ternary(Name("a"), Ternary(Name("b"), Name("c"), Name("d")),
                                                 Name("e")))
        self.assertEqual(E.evaluate(P("-2**2"), {}), -4.0)
        self.assertEqual(E.evaluate(P("2**-1"), {}), 0.5)
        self.assertEqual(E.evaluate(P("2**3**2"), {}), 512.0)

    def test_quotes_and_braces_group(self):
        self.assertEqual(P("'a*b'"), Binary("*", Name("a"), Name("b")))
        self.assertEqual(P("{a+1}"), Binary("+", Name("a"), Num(1.0)))
        self.assertEqual(P("'a'*{b+c}"), Binary("*", Name("a"), Binary("+", Name("b"), Name("c"))))
        self.assertEqual(P("  ' 2 * x '  "), Binary("*", Num(2.0), Name("x")))

    def test_node_access_takes_raw_names(self):
        self.assertEqual(P("v(x1.d<0>, vdd!)"), V("x1.d<0>", "vdd!"))
        self.assertEqual(P("V(Net#1)"), V("net#1"))
        self.assertEqual(P("i(x1.vs)"), Call("i", (Name("x1.vs"),)))
        self.assertEqual(P("v(a[3]) - v(b:c)"), Binary("-", V("a[3]"), V("b:c")))
        self.assertEqual(P("i1(m1)+lx4(m2)"), Binary("+", Call("i1", (Name("m1"),)),
                                                    Call("lx4", (Name("m2"),))))
        self.assertEqual(P("vdb(out)"), Call("vdb", (Name("out"),)))
        self.assertEqual(P("v (a)"), V("a"))

    def test_case(self):
        self.assertEqual(P("Vdd*W"), Binary("*", Name("vdd"), Name("w")))
        self.assertEqual(P("Vdd*TEMPER", case="sensitive"), Binary("*", Name("Vdd"), Name("temper")))
        self.assertEqual(P("Vdd+v(Out)", case="upper"), Binary("+", Name("VDD"), V("OUT")))
        self.assertEqual(P("POW(a,2)"), Call("pow", (Name("a"), Num(2.0))))

    def test_calls_strings_and_folding(self):
        self.assertEqual(P("pow(a, 2)"), Call("pow", (Name("a"), Num(2.0))))
        self.assertEqual(P("f(a, b+1, -c)"), Call("f", (Name("a"), Binary("+", Name("b"), Num(1.0)),
                                                        Unary("-", Name("c")))))
        self.assertEqual(P('"file.dat"'), Str("file.dat"))
        self.assertEqual(P("-(-2)"), Num(2.0))
        self.assertEqual(P("+a"), Name("a"))

    def test_errors_name_the_position(self):
        bad = {"": 0, "a*(b+c": 2, "a+": 2, "a b": 2, "a & b": 2, "a | b": 2, "~a": 0, "a % b": 2,
               "a = b": 2, "a <> b": 2, "f()": 2, "()": 1, "'a": 0, "a)": 1, "v(a b)": 3,
               "v(a,)": 4, "v(a": 1, "1.5.2": 3, "a ? b": 5, "{a)": 2, "a.b": 1, '"x': 0, "$a": 0}
        for text, pos in bad.items():
            with self.assertRaises(E.ExprError, msg=text) as cm:
                P(text)
            self.assertEqual(cm.exception.pos, pos, "%r: %s" % (text, cm.exception))
            self.assertEqual(cm.exception.text, text)

    def test_to_text_round_trips(self):
        for text in ("-a**2", "(-a)**2", "a**-b", "a**b**c", "(a**b)**c", "a-(b-c)", "a/(b*c)",
                     "a?b:c?d:e", "(a?b:c)?d:e", "a||b&&c", "(a||b)&&c", "!a==b", "!(a==b)",
                     "-(a+b)*c", "a*-b", "pow(-2,1.5)+v(x1.n,0)", "(-2)**y", 'f("s")',
                     "a<b==c>d", "(a?b:c)+1"):
            ast = P(text)
            self.assertEqual(P(E.to_text(ast)), ast, "%s -> %s" % (text, E.to_text(ast)))


class TestInspect(unittest.TestCase):
    def test_names(self):
        self.assertEqual(E.names(P("pow(a, b) + v(c) + i(vd) + time*temper + hertz + f(e)")),
                         {"a", "b", "e"})
        self.assertEqual(E.names(P("2*3")), set())

    def test_node_calls_and_dependence(self):
        e = P("v(a) + v(a) * i(v1) + v(b, 0)")
        self.assertEqual(E.node_calls(e), [V("a"), Call("i", (Name("v1"),)), V("b", "0")])
        self.assertTrue(E.is_node_dependent(e))
        self.assertTrue(E.is_node_dependent(P("time*2")))
        self.assertFalse(E.is_node_dependent(P("temper*a")))
        self.assertTrue(E.is_constant(P("pow(2, 3) + 1")))
        self.assertFalse(E.is_constant(P("a + 1")))

    def test_substitute_map_nodes_inline(self):
        e = P("a*b + v(a)")
        self.assertEqual(E.substitute(e, {"a": Num(2.0)}),
                         Binary("+", Binary("*", Num(2.0), Name("b")), V("a")))
        self.assertEqual(E.map_nodes(P("v(gnd, x) + i(v1)"), lambda n: "0" if n == "gnd" else n),
                         Binary("+", V("0", "x"), Call("i", (Name("v1"),))))
        funcs = {"f": (["x", "y"], P("x*y + k")), "g": (["x"], P("f(x, 2)"))}
        self.assertEqual(E.inline(P("g(a+1)"), funcs), P("(a+1)*2 + k"))
        with self.assertRaises(E.ExprError):
            E.inline(P("f(1)"), funcs)
        with self.assertRaises(E.ExprError):
            E.inline(P("r(1)"), {"r": (["x"], P("r(x)"))})

    def test_fold(self):
        self.assertEqual(E.fold(P("2*3 + a")), Binary("+", Num(6.0), Name("a")))
        self.assertEqual(E.fold(P("a*b"), {"a": 2.0}), Binary("*", Num(2.0), Name("b")))
        self.assertEqual(E.fold(P("0 ? v(x) : 3")), Num(3.0))
        self.assertEqual(E.fold(P("0 && v(x)")), Num(0.0))
        self.assertEqual(E.fold(P("1 || v(x)")), Num(1.0))
        self.assertEqual(E.fold(P("if(1, a, 1/0)")), Name("a"))
        self.assertEqual(E.fold(P("a + 1/0")), P("a + 1/0"))
        with self.assertRaises(E.EvalError):
            E.fold(P("a + 1/0"), strict=True)
        self.assertEqual(E.fold(P("if(1, a, 1/0) + (0 ? log(0) : 2)"), strict=True),
                         Binary("+", Name("a"), Num(2.0)))          # never-evaluated branches
        self.assertEqual(E.to_xyce(P("if(1, a, 1/0)")), "a")


class TestEvaluate(unittest.TestCase):
    def ev(self, text, **scope):
        return E.evaluate(P(text), scope)

    def test_hspice_builtins(self):
        pi = math.pi
        cases = [
            ("pow(2,1.5)", 2.0), ("pow(-2,1.5)", -2.0), ("pow(-2,3)", -8.0), ("pow(2,-1.5)", 0.5),
            ("pow(0,2)", 0.0), ("pow(0,0)", 1.0), ("2**1.5", 2 ** 1.5), ("(-2)**1.5", -2.0),
            ("(-2)**2", 4.0), ("0**5", 0.0), ("0**0", 0.0), ("0**-1", 0.0), ("2^3", 8.0),
            ("(-8)**(1/3)", 1.0), ("pwr(-2,2)", -4.0), ("pwr(-8,1/3)", -2.0), ("pwr(0,-1)", 0.0),
            ("sqrt(-4)", -2.0), ("sqrt(0)", 0.0), ("sqrt(2.25)", 1.5),
            ("log(-2)", -math.log(2)), ("ln(0.5)", math.log(0.5)), ("log10(-100)", -2.0),
            ("db(-10)", -20.0), ("db(0.1)", -20.0), ("sgn(0)", 0.0), ("sgn(-3)", -1.0),
            ("sign(3,-2)", -3.0), ("sign(-3,0)", 0.0), ("sign(-3)", -1.0),
            ("int(-2.7)", -2.0), ("trunc(2.7)", 2.0), ("nint(-2.5)", -3.0), ("nint(2.5)", 3.0),
            ("nint(0.49)", 0.0), ("nint(-0.5)", -1.0), ("floor(-2.5)", -3.0), ("ceil(-2.5)", -2.0),
            ("abs(-3)", 3.0), ("exp(0)", 1.0), ("atan2(1,-1)", 3 * pi / 4),
            ("atan2(-1,-1)", -3 * pi / 4), ("atan2(1,0)", pi / 2), ("min(3,-1,2)", -1.0),
            ("max(3,-1,2)", 3.0), ("dmax(1,2)", 2.0), ("limit(5,0,2)", 2.0),
            ("limit(-5,0,2)", 0.0), ("if(0,1,2)", 2.0), ("if(-0.5,1,2)", 1.0),
            ("agauss(1.5,0.1,3)", 1.5), ("gauss(2,0.1,3,1)", 2.0), ("aunif(4,1)", 4.0),
            ("unif(5,0.1)", 5.0), ("limit(6,1)", 6.0), ("asinh(0)", 0.0), ("tanh(0)", 0.0),
            ("1<2", 1.0), ("2<=1", 0.0), ("1==1", 1.0), ("1!=1", 0.0), ("!0", 1.0), ("!2", 0.0),
            ("2 && 0.5", 1.0), ("0 || 0", 0.0), ("1 || 1 && 0", 1.0), ("0.5 ? 3 : 4", 3.0),
            ("-2**2", -4.0), ("10/4", 2.5), ("7/2", 3.5),
        ]
        for text, want in cases:
            self.assertAlmostEqual(self.ev(text), want, delta=1e-15 * max(1.0, abs(want)), msg=text)

    def test_scope_and_laziness(self):
        self.assertEqual(self.ev("a*b+c", a=2, b=3, c=1), 7.0)
        self.assertEqual(self.ev("x == 0 ? 0 : 1/x", x=0.0), 0.0)
        self.assertEqual(self.ev("0 && 1/0"), 0.0)
        self.assertEqual(self.ev("1 || 1/0"), 1.0)
        self.assertEqual(self.ev("if(1, 2, 1/0)"), 2.0)

    def test_not_constant_names_the_construct(self):
        cases = {"v(a)+1": "v(a)", "i(v1)": "i(v1)", "time*2": "time", "temper": "temper",
                 "hertz": "hertz", "foo+1": "foo", "bar(1)": "bar", '"s"+1': '"s"'}
        for text, what in cases.items():
            with self.assertRaises(E.EvalError, msg=text) as cm:
                self.ev(text)
            self.assertIn(what, str(cm.exception), text)

    def test_numeric_failures(self):
        for text in ("1/0", "log(0)", "db(0)", "acos(2)", "exp(1000)", "pow(0,-1)", "1e308*10",
                     "pow(2,2,2)", "if(1,2)", "acosh(0.5)", "sqrt(1e308*1e308)"):
            with self.assertRaises(E.EvalError, msg=text):
                self.ev(text)


class TestPrinters(unittest.TestCase):
    def both(self, text, ctx="param"):
        ast = P(text)
        return E.to_vacask(ast, ctx), E.to_xyce(ast, ctx)

    def test_golden(self):
        cases = [
            # (hspice, ctx, vacask, xyce)
            ("a*(b+c)-d/e", "param", "a*(b+c)-d/e", "a*(b+c)-d/e"),
            ("a-(b-c)", "param", "a-(b-c)", "a-(b-c)"),
            ("-a**2", "param", "(-pow(a, 2.0))", "(-pow(a, 2.0))"),
            ("pow(a, 1.5)", "param", "pow(a, 1.0)", "pow(a, 1.0)"),
            ("pow(a, n)", "param", "(integer(n)==0.0 ? 1.0 : pow(a, integer(n)))",
             "((n>=0.0 ? floor(n) : ceil(n))==0.0 ? 1.0 : pow(a, (n>=0.0 ? floor(n) : ceil(n))))"),
            ("pow(v(a), v(n))", "behavioral",
             "((v(n)>=0.0 ? floor(v(n)) : (-floor((-v(n)))))==0.0 ? 1.0 : "
             "pow(v(a), (v(n)>=0.0 ? floor(v(n)) : (-floor((-v(n)))))))",
             "((v(n)>=0.0 ? floor(v(n)) : ceil(v(n)))==0.0 ? 1.0 : "
             "pow(v(a), (v(n)>=0.0 ? floor(v(n)) : ceil(v(n)))))"),
            ("pow(a, 0.7)", "param", "1.0", "1.0"),
            ("(-2)**n", "param", "pow((-2.0), integer(n))",
             "pow((-2.0), (n>=0.0 ? floor(n) : ceil(n)))"),
            ("a**1.5", "param", "(a>0.0 ? pow(a, 1.5) : (a<0.0 ? pow(a, 1.0) : 0.0))",
             "(a>0.0 ? pow(a, 1.5) : (a<0.0 ? pow(a, 1.0) : 0.0))"),
            ("a**3", "param", "pow(a, 3.0)", "pow(a, 3.0)"),
            ("a**-1", "param", "(a==0.0 ? 0.0 : pow(a, (-1.0)))", "(a==0.0 ? 0.0 : pow(a, (-1.0)))"),
            ("2**a", "param", "pow(2.0, a)", "pow(2.0, a)"),
            ("sgn(a)", "param", "(a>0.0 ? 1.0 : (a<0.0 ? (-1.0) : 0.0))", "sgn(a)"),
            ("sqrt(a)", "param", "(a>0.0 ? 1.0 : (a<0.0 ? (-1.0) : 0.0))*sqrt(abs(a))",
             "sgn(a)*sqrt(abs(a))"),
            ("log(v(a))", "behavioral",
             "(v(a)>0.0 ? 1.0 : (v(a)<0.0 ? (-1.0) : 0.0))*ln(max(abs(v(a)), 1e-300))",
             "sgn(v(a))*ln(max(abs(v(a)), 1e-300))"),
            ("db(a)", "param", "(a>0.0 ? 1.0 : (a<0.0 ? (-1.0) : 0.0))*(20.0*log10(abs(a)))",
             "sgn(a)*(20.0*log10(abs(a)))"),
            ("int(a)", "param", "integer(a)", "(a>=0.0 ? floor(a) : ceil(a))"),
            ("nint(a)", "param", "round(a)", "nint(a)"),
            ("nint(v(a))", "behavioral", "(v(a)>=0.0 ? floor(v(a)+0.5) : (-floor((-v(a))+0.5)))",
             "nint(v(a))"),
            ("ceil(v(a))", "behavioral", "(-floor((-v(a))))", "ceil(v(a))"),
            ("(a>b)/2", "param", "real(a>b)/2.0", "(a>b)/2.0"),
            ("(v(a)>b)/2", "behavioral", "floor(v(a)>b)/2.0", "(v(a)>b)/2.0"),
            ("!a", "param", "real((!a))", "(a==0.0)"),
            ("a||b&&c", "param", "real(a||(b&&c))", "a||(b&&c)"),
            ("a?b:c?d:e", "param", "(a ? b : (c ? d : e))", "(a ? b : (c ? d : e))"),
            ("if(a<b, 1, 2)", "param", "(a<b ? 1.0 : 2.0)", "(a<b ? 1.0 : 2.0)"),
            ("limit(a, lo, hi)", "param", "min(max(a, lo), hi)", "min(max(a, lo), hi)"),
            ("max(a, b, c)", "param", "max(max(a, b), c)", "max(max(a, b), c)"),
            ("sign(a, b)", "param", "abs(a)*(b>0.0 ? 1.0 : (b<0.0 ? (-1.0) : 0.0))", "abs(a)*sgn(b)"),
            ("pwr(a, b)", "param", "(a>0.0 ? 1.0 : (a<0.0 ? (-1.0) : 0.0))*pow(abs(a), b)",
             "sgn(a)*pow(abs(a), b)"),
            ("atan2(y, x)", "behavioral", "atan2(y, x)", "atan2(y, x)"),
            ("agauss(a, 0.1, 3) + limit(b, 1)", "param", "a+b", "a+b"),
            ("temper + 1", "param", "$temp+1.0", "temp+1.0"),
            ("time*1e9", "behavioral", "$abstime*1000000000.0", "time*1000000000.0"),
            ("v(x1.n) - v(a, b) + i(x2.vs)", "behavioral", "v('x1:n')-v(a,b)+i('x2:vs')",
             "v(x1:n)-v(a,b)+i(x2:vs)"),
            ("v(0) + v(12)", "behavioral", "v(0)+v(12)", "v(0)+v(12)"),
            ("a*b*(c*d)", "param", "a*b*(c*d)", "a*b*(c*d)"),
            ("-(a+b)", "param", "(-(a+b))", "(-(a+b))"),
            ("a*-b", "param", "a*(-b)", "a*(-b)"),
            ("a - -2", "param", "a-(-2.0)", "a-(-2.0)"),
            ("pow(2,1.5) + sgn(0) + db(-10)", "param", "(-18.0)", "(-18.0)"),
            ('"model.dat"', "param", '"model.dat"', '"model.dat"'),
        ]
        for text, ctx, want_v, want_x in cases:
            v, x = self.both(text, ctx)
            self.assertEqual(v, want_v, "%s [%s] vacask" % (text, ctx))
            self.assertEqual(x, want_x, "%s [%s] xyce" % (text, ctx))

    def test_vacask_atan2_parameter_formula(self):
        t = E.to_vacask(P("atan2(y, x)"))
        self.assertNotIn("atan2", t)                # VACASK's parameter atan2 is wrong for x < 0
        self.assertIn("atan(y/x)+3.141592653589793", t)

    def test_vacask_behavioral_integer_literals(self):
        # Integer-valued literals become Verilog-A integers in VACASK's translation.
        self.assertEqual(E.to_vacask(P("sgn(v(a))/2"), "behavioral"),
                         "floor((v(a)>0.0 ? 1.0 : (v(a)<0.0 ? (-1.0) : 0.0)))/2.0")
        self.assertEqual(E.to_vacask(P("(v(a) ? 3 : 4)/2"), "behavioral"),
                         "floor((v(a) ? 3.0 : 4.0))/2.0")
        self.assertEqual(E.to_vacask(P("v(a)*3e9"), "behavioral"), "v(a)*(3000000000.5-0.5)")
        self.assertEqual(E.to_vacask(P("v(a)*1e16"), "behavioral"),
                         "v(a)*(9313225.746154785*1073741824.0)")
        self.assertEqual(E.to_vacask(P("v(a)*2147483648*2"), "behavioral"),
                         "v(a)*(2147483648.5-0.5)*2.0")
        self.assertEqual(E.to_vacask(P("v(a)*1e17"), "behavioral"), "v(a)*1e+17")
        self.assertEqual(E.to_vacask(P("a*3e9")), "a*3000000000.0")
        # a wholly constant behavioral expression is folded by VACASK itself
        self.assertEqual(E.to_vacask(P("3e9 + 1"), "behavioral"), "(3000000001.5-0.5)+0.0*$abstime")
        self.assertEqual(E.to_vacask(P("-3e9"), "behavioral"), "(-2999999999.5-0.5)+0.0*$abstime")
        self.assertEqual(E.to_vacask(P("2e9"), "behavioral"), "2000000000.0")

    def test_print_errors(self):
        cases = [("v(a)+1", "param", "v(a)"), ("i(v1)", "param", "i(v1)"),
                 ("time", "param", "time"), ("hertz*2", "behavioral", "hertz"),
                 ("vm(a)", "behavioral", "vm(a)"), ("i1(m1)", "behavioral", "i1(m1)"),
                 ("lv9(m1)", "behavioral", "lv9(m1)"), ("foo(a)", "param", "foo"),
                 ('"s"', "behavioral", "string"), ("pow(a)", "param", "pow"),
                 ("a + 1/0", "param", "division by zero"), ("v(a,b,c)", "behavioral", "v(a, b, c)")]
        for text, ctx, what in cases:
            for printer in (E.to_vacask, E.to_xyce):
                with self.assertRaises(E.PrintError, msg="%s %s" % (text, printer.__name__)) as cm:
                    printer(P(text), ctx)
                self.assertIn(what, str(cm.exception), text)
        with self.assertRaises(E.PrintError):
            E.to_vacask(Num(5e-324))
        with self.assertRaises(ValueError):
            E.to_xyce(P("a"), "behavioural")

    def test_node_callback(self):
        f = lambda kind, n: "%s_%s" % (kind, n.upper())  # noqa: E731
        self.assertEqual(E.to_vacask(P("v(a)+i(b)"), "behavioral", node=f), "v(v_A)+i(i_B)")
        self.assertEqual(E.to_xyce(P("v(a)+i(b)"), "behavioral", node=f), "v(v_A)+i(i_B)")

    def test_param_ident_and_quote(self):
        for n in ("pi", "PI", "dt", "vt", "temp", "freq", "gmin", "exp", "ctok", "constctok", "poly"):
            self.assertEqual(E.param_ident(n, "xyce"), "vamos_" + n)
            self.assertEqual(E.param_ident(n, "vacask"), n)
        self.assertEqual(E.param_ident("vdd", "xyce"), "vdd")
        self.assertEqual(E.param_ident("M_PI", "vacask"), "vamos_M_PI")
        self.assertEqual(E.param_ident("m_pi", "vacask"), "m_pi")
        self.assertEqual(E.to_xyce(P("pi*vt+exp")), "vamos_pi*vamos_vt+vamos_exp")
        for name, want in (("a", "a"), ("x1:n", "'x1:n'"), ("12", "12"), ("d<0>", "'d<0>'"),
                           ("model", "'model'"), ("vdd!", "'vdd!'"), ("_a$1", "_a$1")):
            self.assertEqual(E.vacask_quote(name), want)


# -- engine runs -----------------------------------------------------------------

# Argument values, named so they read in the decks: m25 = -2.5, p05 = 0.5, z = 0.
VALS = {"m27": -2.7, "m25": -2.5, "m10": -10.0, "m8": -8.0, "m5": -5.0, "m4": -4.0, "m3": -3.0,
        "m2": -2.0, "m15": -1.5, "m1": -1.0, "m05": -0.5, "z": 0.0, "p13": 1.0 / 3.0, "p04": 0.4,
        "p05": 0.5, "p1": 1.0, "p15": 1.5, "p2": 2.0, "p225": 2.25, "p25": 2.5, "p27": 2.7,
        "p3": 3.0, "p5": 5.0, "p10": 10.0, "big": 3000000000.5}

# (HSPICE template over a, b, c; argument tuples).  Every row is printed in
# both contexts on both engines and must reproduce evaluate().
ENGINE_CASES = [
    ("sqrt(a)", [("m4",), ("z",), ("p225",), ("m05",)]),
    ("log(a)", [("m2",), ("p05",), ("p10",)]),
    ("ln(a)", [("m10",), ("p05",)]),
    ("log10(a)", [("m10",), ("p05",)]),
    ("db(a)", [("m10",), ("p05",)]),
    ("sgn(a)", [("m3",), ("z",), ("p05",)]),
    ("int(a)", [("m27",), ("m25",), ("z",), ("p25",), ("m05",)]),
    ("int(a) + 10*nint(a) - 33000000010", [("big",)]),     # past 32-bit int() conversions
    ("nint(a)", [("m25",), ("p25",), ("p04",), ("m05",), ("z",), ("m27",)]),
    ("floor(a)", [("m25",), ("z",), ("p25",)]),
    ("ceil(a)", [("m25",), ("z",), ("p25",), ("m2",)]),
    ("abs(a)", [("m25",), ("z",)]),
    ("exp(a) + sin(a) + cos(a) + tan(a) + atan(a)", [("m1",), ("p05",)]),
    ("sinh(a) + cosh(a) + tanh(a) + asinh(a)", [("m1",), ("p05",)]),
    ("asin(a) + acos(a) + atanh(a)", [("m05",), ("p05",)]),
    ("acosh(a + 2)", [("p2",), ("z",)]),         # in the domain at the all-zero first iterate too
    ("pow(a, b)", [("p2", "p15"), ("m2", "p15"), ("m2", "p3"), ("p2", "m15"), ("m2", "m1"),
                   ("p05", "p27"), ("z", "p2"), ("z", "z"), ("z", "p05"), ("m2", "z")]),
    ("a**b", [("p2", "p15"), ("m2", "p15"), ("m2", "p2"), ("z", "p2"), ("z", "p05"), ("m8", "p13"),
              ("p2", "m1"), ("z", "m1"), ("m2", "m15")]),
    ("a**1.5", [("m2",), ("p2",), ("z",)]),
    ("a**-2", [("m2",), ("z",), ("p05",)]),
    ("a**3", [("m2",), ("z",)]),
    ("2**a", [("p15",), ("m15",), ("z",)]),
    ("(-2)**a", [("p15",), ("m15",), ("z",), ("p2",)]),
    ("pwr(a, b)", [("m2", "p2"), ("p2", "p05"), ("m8", "p13"), ("z", "p2"), ("m2", "m1")]),
    ("sign(a, b)", [("p3", "m2"), ("m3", "z"), ("m3", "p2")]),
    ("sign(a)", [("m3",), ("z",)]),
    ("atan2(a, b)", [("p1", "m1"), ("m1", "m1"), ("p1", "p1"), ("z", "m1"), ("p1", "z"),
                     ("m1", "z"), ("z", "z"), ("z", "p1")]),
    ("limit(a, b, c)", [("p5", "z", "p2"), ("m5", "z", "p2"), ("p1", "z", "p2")]),
    ("min(a, b) + 10*max(a, b)", [("m1", "m2"), ("p1", "z")]),
    ("min(a, b, c) + 10*max(a, b, c)", [("p1", "p2", "p3"), ("m3", "p05", "m1")]),
    ("if(a, b, c)", [("z", "p1", "p2"), ("p05", "p1", "p2"), ("m1", "p1", "p2")]),
    ("a ? b : c", [("z", "p1", "p2"), ("m05", "p1", "p2")]),
    ("a > b && b > 0", [("p2", "p1"), ("p1", "p2"), ("p2", "m1")]),
    ("a || b && c", [("p1", "p1", "z"), ("z", "p1", "z"), ("z", "p1", "p1")]),
    ("!a + 2*(a == b) + 4*(a != b)", [("z", "z"), ("p05", "p1")]),
    ("(a > b)/2", [("p2", "p1"), ("p1", "p2")]),
    # integer/integer in Verilog-A; the divisor is never 0, not even at the
    # all-zero first Newton iterate
    ("(a < b)/((a < b) + 1)", [("p1", "p2"), ("p2", "p1")]),
    ("sgn(a)/2 + nint(b)/2 + int(c)/2", [("m3", "p25", "p15"), ("p05", "m25", "m27")]),
    ("(a ? 3 : 4)/2", [("p1",), ("z",)]),
    ("agauss(a, 0.1, 3) + limit(b, 0.5)", [("p15", "m2")]),
    ("a*3e9 + b*1e16 + 4294967296", [("p05", "m05")]),
    ("3e9 + 1", [()]),                                    # wholly constant
    ("-1e16", [()]),
    ("-a**2 + 10*(-a)**2", [("p2",), ("m2",)]),
    ("db(sqrt(a)) + log(b)**2", [("p10", "m2")]),
    ("temper + a", [("z",)]),
    # Parameter context only: integer/integer divisions (VACASK parameters
    # need real() for them); in a behavioral source they divide 0 by 0 at the
    # all-zero first Newton iterate.
    ("(a < b)/((a < b) + (a < b)) + (!c)/((!c) + (!c)) + (a < b && c == 0)/((b > a) + (b > a))",
     [("p1", "p2", "z")], ("param",)),
]

ENGINE_TEMP = 27.0          # both engines' default temperature (the emitters set HSPICE's 25)


def _build_cases(ctx):
    out = []
    for row in ENGINE_CASES:
        template, arglists = row[0], row[1]
        if ctx not in (row[2] if len(row) > 2 else ("param", "behavioral")):
            continue
        tmpl = P(template)
        for args in arglists:
            names = dict(zip("abc", args))
            pa = E.substitute(tmpl, {k: Name(v) for k, v in names.items()})
            ba = E.substitute(tmpl, {k: V("n_" + v) for k, v in names.items()})
            want = E.evaluate(E.substitute(pa, {"temper": Num(ENGINE_TEMP), "time": Num(0.0)}), VALS)
            out.append(("%s @ %s" % (template, ",".join(args)), pa if ctx == "param" else ba, want))
    return out


CASES = {ctx: _build_cases(ctx) for ctx in ("param", "behavioral")}


def _close(got, want, rel, abs_):
    return abs(got - want) <= max(abs_, rel * abs(want))


class _EngineCase(TempDir):
    """Shared deck plumbing; subclasses define deck() and run_deck()."""

    def check(self, ctx, rel, abs_):
        cases = CASES[ctx]
        values = self.run_deck(ctx, [ast for _, ast, _ in cases])
        bad = []
        for (label, ast, want), got in zip(cases, values):
            if not _close(got, want, rel, abs_):
                text = E.to_vacask(ast, ctx) if self.engine == "vacask" else E.to_xyce(ast, ctx)
                bad.append("%s: got %r, HSPICE %r  [%s]" % (label, got, want, text))
        self.assertFalse(bad, "\n".join(bad))
        return values


@needs_vacask
class TestVacaskPrinter(_EngineCase):
    engine = "vacask"

    def env(self):
        env = dict(os.environ)
        if openvaf_bin():
            env["SIM_OPENVAF"] = openvaf_bin()
        from vamos.ams import engines
        mp = engines.vacask_module_path()
        if mp:
            env["SIM_MODULE_PATH"] = mp
        return env

    def run_text(self, text, name="t"):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        with open(os.path.join(d, "t.sim"), "w") as fh:
            fh.write(text)
        r = run([vacask_bin(), "t.sim"], cwd=d, env=self.env(), timeout=900)
        self.assertEqual(r.returncode, 0, "VACASK failed:\n%s\n--- deck:\n%s" % (r.stdout[-3000:], text))
        return rawfile.read(os.path.join(d, "op1.raw"))

    def run_deck(self, ctx, asts, extra=""):
        lines = ["vamos expr test (%s)" % ctx, "model vsrc vsource", "model isrc isource"]
        if ctx == "param":
            lines.append("parameters " + " ".join("%s=%s" % (k, E.to_vacask(Num(v)))
                                                  for k, v in VALS.items()))
            for k, a in enumerate(asts):
                lines.append("parameters e%d=%s" % (k, E.to_vacask(a, "param")))
                lines.append("v%d (o%d 0) vsrc dc=e%d" % (k, k, k))
        else:
            for name, v in VALS.items():
                lines.append("vn_%s (n_%s 0) vsrc dc=%s" % (name, name, E.to_vacask(Num(v))))
            for k, a in enumerate(asts):
                lines.append("b%d (o%d 0) v=%s" % (k, k, E.to_vacask(a, "behavioral")))
        lines += [extra, "control", "  abort always",
                  "  save " + " ".join("v(o%d)" % k for k in range(len(asts))),
                  "  analysis op1 op", "endc", ""]
        raw = self.run_text("\n".join(lines), ctx)
        return [raw.column("o%d" % k)[0] for k in range(len(asts))]

    def test_parameter_context(self):
        self.check("param", 1e-12, 1e-14)

    def test_behavioral_context(self):
        self.check("behavioral", 1e-9, 1e-12)

    def test_behavioral_access_forms(self):
        text = "\n".join([
            "vamos access test", "model vsrc vsource", "model isrc isource",
            "parameters k=2.0",
            "subckt s (p)", "  vs (n 0) vsrc dc=0.75", "  rr (p n) vsrc dc=0.0", "ends",
            "x1 (q) s",
            "ii (0 ni) isrc dc=0.001", "vi (ni 0) vsrc dc=0.0",
            "vx (x 0) vsrc dc=1.25",
            "b0 (o0 0) v=%s" % E.to_vacask(P("v(x1.n)"), "behavioral"),
            "b1 (o1 0) v=%s" % E.to_vacask(P("i(vi)*1000"), "behavioral"),
            "b2 (o2 0) v=%s" % E.to_vacask(P("v(x, 0) + time + k*v(0)"), "behavioral"),
            "b3 (o3 0) v=%s" % E.to_vacask(P("k*v(x) - v(q)"), "behavioral"),
            "control", "  abort always", "  save v(o0) v(o1) v(o2) v(o3)", "  analysis op1 op",
            "endc", ""])
        raw = self.run_text(text)
        got = [raw.column("o%d" % k)[0] for k in range(4)]
        for g, w in zip(got, [0.75, 1.0, 1.25, 2.5 - 0.75]):
            self.assertTrue(_close(g, w, 1e-9, 1e-12), (got, text))


@needs_xyce
class TestXycePrinter(_EngineCase):
    engine = "xyce"

    def run_text(self, text, name="t"):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        with open(os.path.join(d, "t.cir"), "w") as fh:
            fh.write(text)
        r = run([xyce_bin(), "t.cir"], cwd=d, env=xyce_env(), timeout=900)
        self.assertEqual(r.returncode, 0, "Xyce failed:\n%s\n--- deck:\n%s" % (r.stdout[-3000:], text))
        return rawfile.read(os.path.join(d, "out.raw"))

    def run_deck(self, ctx, asts, extra=""):
        lines = ["* vamos expr test (%s)" % ctx]
        if ctx == "param":
            for name, v in VALS.items():
                lines.append(".param %s={%s}" % (name, E.to_xyce(Num(v))))
            for k, a in enumerate(asts):
                lines.append(".param ex%d={%s}" % (k, E.to_xyce(a, "param")))
                lines.append("V%d o%d 0 {ex%d}" % (k, k, k))
        else:
            for name, v in VALS.items():
                lines.append("Vn_%s n_%s 0 {%s}" % (name, name, E.to_xyce(Num(v))))
            for k, a in enumerate(asts):
                lines.append("B%d o%d 0 V={%s}" % (k, k, E.to_xyce(a, "behavioral")))
                lines.append("R%d o%d 0 1k" % (k, k))
        lines += [extra, ".tran 1n 2n", ".print tran format=raw file=out.raw"]
        lines += ["+ v(o%d)" % k for k in range(len(asts))]
        lines += [".end", ""]
        raw = self.run_text("\n".join(lines), ctx)
        return [raw.column("v(o%d)" % k)[0] for k in range(len(asts))]

    def test_parameter_context(self):
        self.check("param", 1e-12, 1e-14)

    def test_behavioral_context(self):
        self.check("behavioral", 1e-9, 1e-12)

    def test_behavioral_access_forms(self):
        text = "\n".join([
            "* vamos access test",
            ".param k=2.0",
            ".subckt s p", "VS n 0 0.75", "RR p n 1", ".ends",
            "X1 q s",
            "II 0 ni 1e-3", "VI ni 0 0",
            "VX x 0 1.25",
            "B0 o0 0 V={%s}" % E.to_xyce(P("v(x1.n)"), "behavioral"),
            "B1 o1 0 V={%s}" % E.to_xyce(P("i(vi)*1000"), "behavioral"),
            "B2 o2 0 V={%s}" % E.to_xyce(P("v(x, 0) + time + k*v(0)"), "behavioral"),
            "B3 o3 0 V={%s}" % E.to_xyce(P("k*v(x) - v(q)"), "behavioral"),
            "R0 o0 0 1k", "R1 o1 0 1k", "R2 o2 0 1k", "R3 o3 0 1k", "RQ q 0 1k",
            ".tran 1n 2n", ".print tran format=raw file=out.raw v(o0) v(o1) v(o2) v(o3)", ".end", ""])
        raw = self.run_text(text)
        got = [raw.column("v(o%d)" % k)[0] for k in range(4)]
        vq = 0.75 * 1000.0 / 1001.0
        for g, w in zip(got, [0.75, 1.0, 1.25, 2.5 - vq]):
            self.assertTrue(_close(g, w, 1e-9, 1e-12), (got, text))

    def test_reserved_parameter_names(self):
        # Xyce reads pi, dt, exp, ctok inside {} as built-ins even after .param pi=...
        names = {"pi": 2.0, "dt": 3.0, "exp": 4.0, "vt": 5.0, "temp": 6.0, "ctok": 7.0}
        lines = ["* reserved names"]
        lines += [".param %s={%s}" % (E.param_ident(n, "xyce"), E.to_xyce(Num(v)))
                  for n, v in names.items()]
        ast = P("pi + 10*dt + 100*exp + 1000*vt + 10000*temp + 100000*ctok")
        lines += ["V0 o0 0 {%s}" % E.to_xyce(ast), ".tran 1n 2n",
                  ".print tran format=raw file=out.raw v(o0)", ".end", ""]
        raw = self.run_text("\n".join(lines))
        self.assertEqual(raw.column("v(o0)")[0], E.evaluate(ast, names))


@needs_vacask
@needs_xyce
class TestCrossEngineParameters(TempDir):
    """§9: one parameter deck, identical values on both engines (and HSPICE's)."""

    def test_identical(self):
        v = TestVacaskPrinter("test_parameter_context")
        x = TestXycePrinter("test_parameter_context")
        for t in (v, x):
            t.setUp()
        try:
            asts = [ast for _, ast, _ in CASES["param"]]
            vv = v.run_deck("param", asts)
            xv = x.run_deck("param", asts)
        finally:
            for t in (v, x):
                t.tearDown()
        bad = ["%s: VACASK %r, Xyce %r, HSPICE %r" % (label, a, b, want)
               for (label, _, want), a, b in zip(CASES["param"], vv, xv)
               if not (_close(a, b, 1e-12, 1e-14) and _close(a, want, 1e-12, 1e-14))]
        self.assertFalse(bad, "\n".join(bad))
        self.assertGreater(len(bad) + len(vv), 100)


if __name__ == "__main__":
    unittest.main()
