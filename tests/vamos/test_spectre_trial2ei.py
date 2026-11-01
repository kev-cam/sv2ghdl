"""The fixes of phase 2e's sixth round (FIX2E6: the round-5 skeptic S-2E5's regression, silent values and loud failure,
and the guide trial TRIAL2EI's findings).  Standard library only, Python 3.9; the engine runs on WSL.

TestAnnotationPositions   on Xyce an undefined `name(...), PyMS's annotation form (BSIM-CMG's `attr), is accepted where
                          PyMS's parser drops it (the end of a module header or of a parameter statement, a statement of
                          its own, a port direction or variable declaration) and refused with what PyMS does elsewhere
                          (S-2E5's regression: round 4's undefined-macro rule refused `parameter real r = 1k
                          `attr(info="resistance")', which 5d2291f ran right; inside an analog block before a statement
                          PyMS swallows the statement: out = 0 with exit 0, measured)
TestPymsDefaultForms      on Xyce every parameter default PyMS's evaluation cannot read is refused: a function call, the
                          ternary, a digit separator, the power operator, !, &&, a two-character comparison, a based
                          literal, a system function, a name that is not a parameter (S-2E5's three silent values:
                          pow(10, 3), (1 > 0) ? 1000.0 : 2000.0 and 1_000 gave out = 0 with exit 0); what PyMS reads
                          right keeps running (2 * rr, 1000 * 1, `M_PI * 1000.0 / `M_PI, (1000.0), - 1000.0, ...)
TestCurrentReadInAnExpression
                          on Xyce a module that reads a current I(...) in an expression is refused before PyMS's compile:
                          PyMS assigns currents from voltages only (TRIAL2EI-1: `V(p, n) <+ r * I(p, n)' failed its C++
                          compile; a named branch's `V(br) <+ r * I(br)' gave out = 1 where VACASK gives 0.5, exit 0)
TestIcOnAnAllProbedNode   on Xyce the probe of a two-terminal element on an ic or nodeset node sits in series with the
                          element's other terminal, reversed, so the node keeps the element and the .IC applies
                          (S-2E5 3a: two zero-gain vcvs probes on the ic node made Xyce's matrix singular, exit 1); an
                          ic on a node that only the probes of multi-terminal devices touch is refused
TestNetlistBytesNotUtf8   a netlist byte that is not UTF-8 is read as U+FFFD with one warning naming the file, the line
                          and the byte (S-2E5: round 5 wrote the replacement character with no warning)
TestPercentAInFileNames   %A in an analysis's quoted strings takes the file-name escape of a / (S-2E5: `op\\/1 dc
                          write="%A.ns"' made the path op/1.ns, `cannot write the state file', where the results file is
                          _op_0571.dc)"""

import os
import unittest

from vamos_testlib import TempDir, needs_vacask, needs_xyce  # noqa: E402

import test_spectre_trial2eh as eh  # noqa: E402  (its deck runner, value reader and resolve helpers)

A1_DECK = eh.A1_DECK
HEAD = '`include "disciplines.vams"\n`include "constants.vams"\n'


def module(decl="parameter real r = 1k;", header="module a1(p, n);", body=None, extra="", pre="",
           ports="  inout p, n;\n  electrical p, n;\n"):
    body = body or "analog I(p, n) <+ V(p, n) / r;"
    return HEAD + pre + header + "\n" + ports + "  " + decl + "\n" + extra + "  " + body + "\nendmodule\n"


# -- 1: the annotation positions -----------------------------------------------------------------------------------------

DROPPED = {
    "parameter_end": module('parameter real r = 1k `attr(info="resistance");'),
    "module_header": module(header='module a1(p, n) `attr(info="x");'),
    "module_header_two_lines": module(header='module a1(p, n) `attr(info="x",\n   type="m");'),
    "body_line": module(extra='  `attr(info="x")\n'),
    "body_line_semicolon": module(extra='  `attr(info="x");\n'),
    "body_two_lines": module(extra='  `attr(info="x",\n        type="instance")\n'),
    "analog_block_statement": module(body='analog begin\n    `attr(info="x");\n    I(p, n) <+ V(p, n) / r;\n  end'),
    "after_the_range": module('parameter real r = 1k from (0:inf) `attr(info="x");'),
    "two_annotations": module('parameter real r = 1k `attr(info="x") `attr(type="instance");'),
    "blank_before_the_paren": module('parameter real r = 1k `attr (info="x");'),
    "another_name": module('parameter real r = 1k `info(desc="x");'),
    "two_lines": module('parameter real r = 1k `attr(info="x",\n     type="model");'),
    "port_direction": module(ports='  inout p, n `attr(info="x");\n  electrical p, n;\n'),
    "variable": module(extra='  real v `attr(info="x");\n',
                       body='analog begin\n    v = V(p, n);\n    I(p, n) <+ v / r;\n  end'),
}
REFUSED = {
    "analog_block_before_a_statement": (module(body='analog begin\n    `attr(info="x")\n    I(p, n) <+ V(p, n) / r;\n  end'),
                                        "reads it and the statement after it (I ( p , n ) ...) as one statement and drops both"),
    "in_a_contribution": (module(body='analog I(p, n) <+ V(p, n) / r `attr(info="x");'),
                          "leaves it in the statement as an operand, and the C++ compile of the module's evaluator fails"),
    "before_the_name": (module('parameter real `attr(info="x") r = 1k;'), "reads it where it expects the parameter's name"),
    "before_the_range": (module('parameter real r = 1k `attr(info="x") from (0:inf);'),
                         "after the annotation it expects the ';' and finds `from'"),
    "electrical": (module(ports='  inout p, n;\n  electrical p, n `attr(info="x");\n'),
                   "reads `attr as an internal node of the module"),
}


def scan(test, name, va):
    from vamos.spectre import run_xyce as RX
    src = test.write("%s/a1.va" % name, va)
    return RX.pyms_scans([src])


class TestAnnotationPositions(TempDir):

    def test_the_scan_accepts_what_pyms_drops(self):
        from vamos.spectre import run_xyce as RX
        for k, va in DROPPED.items():
            with self.subTest(case=k):
                scans = scan(self, k, va)
                self.assertEqual(RX.macro_problems([scans[0].src], scans=scans), [])
                self.assertEqual(RX.pyms_default_problems([scans[0].src], scans=scans), [])

    def test_the_scan_refuses_the_other_positions_with_what_pyms_does(self):
        from vamos.spectre import run_xyce as RX
        for k, (va, what) in REFUSED.items():
            with self.subTest(case=k):
                scans = scan(self, k, va)
                got = RX.macro_problems([scans[0].src], scans=scans)
                self.assertEqual([(m, len(got)) for _s, _f, _l, m, _w in got][:1], [("attr", 1)], got)
                why = got[0][4]
                self.assertIn("is not defined, and Xyce's PyMS leaves an undefined macro as it stands: it drops `attr(...) as "
                              "an annotation at the end of a module header or of a parameter statement (BSIM-CMG's `attr) "
                              "and as a statement of its own, but here it ", why)
                self.assertIn(what, why)
                self.assertIn("not supported on Xyce in v1: `define attr, or move or drop the annotation (VACASK's "
                              "openvaf-r refuses an undefined macro: macro '`attr' has not been declared)", why)
        # a plain undefined macro, no '(' after it: round 4's message (PyMS leaves it in the default, which it reads as 0)
        scans = scan(self, "plain", module("parameter real r = 1k `attr;"))
        got = RX.macro_problems([scans[0].src], scans=scans)
        self.assertEqual(len(got), 1)
        self.assertIn("is not defined: no `define attr in the Verilog-A source", got[0][4])

    def test_the_defined_macro_is_as_before(self):
        from vamos.spectre import run_xyce as RX
        scans = scan(self, "defined", module('parameter real r = 1k `attr(info="x");', pre="`define attr(txt) (*txt*)\n"))
        self.assertEqual(RX.macro_problems([scans[0].src], scans=scans), [])

    @needs_xyce
    def test_xyce_runs_the_annotated_module_and_refuses_the_swallowing_position(self):
        for k in ("parameter_end", "module_header"):
            with self.subTest(case=k):
                d, r = eh.spectre(self, k, A1_DECK, "xyce", files={"a1.va": DROPPED[k]})
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                self.assertAlmostEqual(eh.value(os.path.join(d, "n.raw", "op1.dc"), "out"), 0.5, 6)
        d, r = eh.spectre(self, "swallow", A1_DECK, "xyce", files={"a1.va": REFUSED["analog_block_before_a_statement"][0]})
        self.assertEqual(r.returncode, 2, r.stdout[-3000:])
        self.assertIn("Error from spectre during AHDL read-in.", r.stdout)
        self.assertIn("a1.va:8: `attr is not defined, and Xyce's PyMS leaves an undefined macro as it stands", r.stdout)
        self.assertIn("reads it and the statement after it (I ( p , n ) ...) as one statement and drops both (measured: a "
                      "contribution after `attr(...) in an analog block gave a device that conducted nothing, with exit "
                      "status 0)", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(d, "n.raw", "op1.dc")))

    @needs_vacask
    def test_vacask_refuses_the_undefined_macro_as_before(self):
        d, r = eh.spectre(self, "va", A1_DECK, "vacask", files={"a1.va": DROPPED["parameter_end"]})
        self.assertEqual(r.returncode, 2, r.stdout[-3000:])
        self.assertIn("openvaf-r failed", r.stdout)


# -- 2: the parameter defaults PyMS's evaluation cannot read ----------------------------------------------------------------

WRONG_DEFAULTS = {
    "function_call": ("parameter real r = pow(10, 3);", "the function call pow(...)"),
    "ternary": ("parameter real r = (1 > 0) ? 1000.0 : 2000.0;", "the ternary ?:"),
    "ternary_of_a_parameter": ("parameter real rr = 1.0;\n  parameter real r = rr > 0 ? 1000.0 : 1.0;", "the ternary ?:"),
    "digit_separator": ("parameter real r = 1_000;", "the digit separator of 1_000 (PyMS reads the tokens `1' and `_000')"),
    "sqrt": ("parameter real r = sqrt(1e6);", "the function call sqrt(...)"),
    "function_in_a_sum": ("parameter real r = ln(1.0) + 1000.0;", "the function call ln(...)"),
    "power_operator": ("parameter real r = 2 ** 10;", "the power operator **"),
    "not": ("parameter real r = !0 * 1000.0;", "the operator !"),
    "two_character_comparison": ("parameter real r = (1 >= 0) * 1000.0;", "the operator >="),
    "logical_and": ("parameter real r = (1 && 1) * 1000.0;", "the operator &&"),
    "based_literal": ("parameter real r = 'h3e8;", "a based literal"),
    "system_function": ('parameter real r = $simparam("gmin", 1000.0);', "the system function $simparam"),
    "unknown_name": ("parameter real r = rx * 1000.0;", "the name rx, which is not a parameter of the module"),
    "bitwise_or_of_a_parameter": ("parameter real rr = 1000.0;\n  parameter real r = rr | 0;",
                                  "the operator | on a value that is not an integer literal"),
    "macro_of_a_function_call": ("parameter real r = `RV;", "the function call pow(...)"),
}
# a parameter named in capitals: the device shell's 0 for a default that is not a number overlays PyMS's evaluation (G27-G37
# of the round's measurements: R = 2 * RR, R = (1000.0), G = GG with GG = 2 * 0.5e-3 and G = - 1e-3 gave out = 0)
CAPITALS = {
    "arithmetic": "parameter real RR = 500.0;\n  parameter real R = 2 * RR;",
    "parentheses": "parameter real R = (1000.0);",
    "unary_minus_with_a_blank": "parameter real R = - 1000.0;",
    "name_of_an_arithmetic_default": "parameter real RR = 2 * 500.0;\n  parameter real R = RR;",
    "name_of_a_name": "parameter real RRR = 1000.0;\n  parameter real RR = RRR;\n  parameter real R = RR;",
}
CAPITALS_RIGHT = ["parameter real R = 1k;", "parameter real RR = 1000.0;\n  parameter real R = RR;",
                  "parameter real Rr = 2 * 500.0;", "parameter real rr = 2 * 500.0;\n  parameter real r = rr;",
                  "parameter real R1 = 1000.0;"]
# Python's arithmetic against Verilog-A's: the division of two integers, and % with a negative operand
VERILOG = {
    "integer_division": ("parameter real g = 1 / 1000;", "the integer division 1 / 1000"),
    "integer_parameter_division": ("parameter integer n = 3;\n  parameter real r = n / 2;", "the integer division n / 2"),
    "negative_modulus": ("parameter real r = -7 % 3;", "the operator % with an operand that is not a non-negative literal"),
    "modulus_of_a_parameter": ("parameter real rr = 7.0;\n  parameter real r = rr % 3;",
                               "the operator % with an operand that is not a non-negative literal"),
}
VERILOG_RIGHT = ["parameter real r = 2000.0 / 2;", "parameter real r = 1000 / 1;", "parameter real r = 1.0 / 1000;",
                 "parameter real r = 1000 % 1001;"]
RIGHT_DEFAULTS = ["parameter real r = 1e3;", "parameter real rr = 500.0;\n  parameter real r = 2 * rr;",
                  "parameter real rr = 1000.0;\n  parameter real r = rr;", "parameter real r = 1000 * 1;",
                  "parameter real r = `M_PI * 1000.0 / `M_PI;", "parameter real r = (1000.0);",
                  "parameter real r = - 1000.0;", "parameter real r = -(-1000.0);", "parameter real r = 2000.0 / 2;",
                  "parameter real r = 1000 % 1001;", "parameter real r = (1 > 0) * 1000.0;", "parameter real r = 1000 | 0;",
                  "parameter real r = 1e-3 * 1e6;", "parameter real r = 1000.;", "parameter real r = 1k;",
                  "parameter real r = 1K;", "parameter real r = +1000;", "parameter real r = .5k;",
                  "parameter real a = 1.0;\n  parameter real b = a * 2;\n  parameter real r = b * 500.0;",
                  'parameter string s = "x";\n  parameter real r = 1k;', "parameter real r = `M_PI;"]


class TestPymsDefaultForms(TempDir):

    def test_the_scan(self):
        from vamos.spectre import run_xyce as RX
        for k, (decl, form) in WRONG_DEFAULTS.items():
            with self.subTest(case=k):
                pre = "`define RV pow(10, 3)\n" if k == "macro_of_a_function_call" else ""
                va = module(decl, pre=pre)
                scans = scan(self, k, va)
                got = RX.pyms_default_problems([scans[0].src], scans=scans)
                line = next(i for i, ln in enumerate(va.split("\n"), 1) if "parameter real r " in ln)
                self.assertEqual([(ln, name) for _s, _f, ln, name, _w in got], [(line, "r")], got)
                why = got[0][4]
                self.assertIn("parameter r of module a1: its default ", why)
                self.assertIn(" holds %s, which Xyce's PyMS evaluates as 0 (it reads a default as a number, as the name of "
                              "another parameter, or as arithmetic of numbers and parameter names (+, -, *, /, %%, parentheses "
                              "and a comparison, read as Python reads them)), so the module would run wrong (measured: a "
                              "default pow(10, 3) gave a device that conducted nothing, with exit status 0); not supported on "
                              "Xyce in v1: write the default as a number, or as such arithmetic, or run it on VACASK" % form,
                              why)
        for k, decl in enumerate(RIGHT_DEFAULTS):
            with self.subTest(case=decl):
                scans = scan(self, "right%d" % k, module(decl))
                self.assertEqual(RX.pyms_default_problems([scans[0].src], scans=scans), [])

    def test_a_name_in_capitals(self):
        from vamos.spectre import run_xyce as RX
        for k, decl in CAPITALS.items():
            with self.subTest(case=k):
                scans = scan(self, k, module(decl, body="analog I(p, n) <+ V(p, n) / R;"))
                got = RX.pyms_default_problems([scans[0].src], scans=scans)
                self.assertEqual([name for _s, _f, _l, name, _w in got], ["R"] if k != "name_of_an_arithmetic_default" else ["RR", "R"], got)
                why = got[-1][4]
                self.assertIn("parameter R of module a1: its default %s is not a number, and the parameter's name R is written "
                              "in capitals: Xyce's PyMS registers such a default as 0 on Xyce's side (it reads a default there "
                              "as a number, or as the name of a parameter whose default is a number), and its evaluator takes "
                              "the device's value, that 0, for a parameter named in capitals (measured: `parameter real R = 2 * "
                              "RR' and `R = (1000.0)' gave a device that conducted nothing, with exit status 0, where `r = 2 * "
                              "rr' runs right); not supported on Xyce in v1: write the default as a number, or name the "
                              "parameter with a lower-case letter, or run it on VACASK" % decl.split(" = ")[-1].rstrip(";"), why)
        for k, decl in enumerate(CAPITALS_RIGHT):
            with self.subTest(case=decl):
                name = decl.split("\n")[-1].split()[2]
                scans = scan(self, "capright%d" % k, module(decl, body="analog I(p, n) <+ V(p, n) / %s;" % name))
                self.assertEqual(RX.pyms_default_problems([scans[0].src], scans=scans), [])

    def test_python_s_arithmetic_against_verilog_a_s(self):
        from vamos.spectre import run_xyce as RX
        for k, (decl, form) in VERILOG.items():
            with self.subTest(case=k):
                scans = scan(self, k, module(decl))
                got = RX.pyms_default_problems([scans[0].src], scans=scans)
                self.assertEqual(len(got), 1, got)
                self.assertIn(" holds " + form, got[0][4])
                if "division" in k:
                    self.assertIn("which Verilog-A truncates to an integer and Xyce's PyMS evaluates as Python does, to a real "
                                  "(measured: `parameter real g = 1 / 1000' gave out = 0.5 on Xyce where VACASK gives 0, with "
                                  "exit status 0); not supported on Xyce in v1: divide reals (1.0 / 1000), or write the value as "
                                  "a number, or run it on VACASK", got[0][4])
        for k, decl in enumerate(VERILOG_RIGHT):
            with self.subTest(case=decl):
                scans = scan(self, "vright%d" % k, module(decl))
                self.assertEqual(RX.pyms_default_problems([scans[0].src], scans=scans), [])

    @needs_xyce
    def test_xyce_refuses_a_name_in_capitals_and_an_integer_division(self):
        d, r = eh.spectre(self, "caps", A1_DECK, "xyce",
                          files={"a1.va": module(CAPITALS["arithmetic"], body="analog I(p, n) <+ V(p, n) / R;")})
        self.assertEqual(r.returncode, 2, r.stdout[-3000:])
        self.assertIn("a1.va:7: parameter R of module a1: its default 2 * RR is not a number, and the parameter's name R is "
                      "written in capitals", r.stdout)
        d, r = eh.spectre(self, "intdiv", A1_DECK, "xyce",
                          files={"a1.va": module("parameter real g = 1 / 1000;", body="analog I(p, n) <+ V(p, n) * g;")})
        self.assertEqual(r.returncode, 2, r.stdout[-3000:])
        self.assertIn("a1.va:6: parameter g of module a1: its default 1 / 1000 holds the integer division 1 / 1000", r.stdout)
        d, r = eh.spectre(self, "capsright", A1_DECK, "xyce",
                          files={"a1.va": module(CAPITALS_RIGHT[1], body="analog I(p, n) <+ V(p, n) / R;")})
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertAlmostEqual(eh.value(os.path.join(d, "n.raw", "op1.dc"), "out"), 0.5, 6)

    @needs_vacask
    def test_vacask_runs_a_name_in_capitals_and_truncates_the_integer_division(self):
        d, r = eh.spectre(self, "caps", A1_DECK, "vacask",
                          files={"a1.va": module(CAPITALS["arithmetic"], body="analog I(p, n) <+ V(p, n) / R;")})
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertAlmostEqual(eh.value(os.path.join(d, "n.raw", "op1.dc"), "out"), 0.5, 9)
        d, r = eh.spectre(self, "intdiv", A1_DECK, "vacask",
                          files={"a1.va": module("parameter real g = 1 / 1000;", body="analog I(p, n) <+ V(p, n) * g;")})
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertAlmostEqual(eh.value(os.path.join(d, "n.raw", "op1.dc"), "out"), 0.0, 9)     # 1 / 1000 is 0: g = 0

    @needs_xyce
    def test_xyce_refuses_them_and_runs_the_right_forms(self):
        for k in ("function_call", "ternary", "digit_separator"):
            with self.subTest(case=k):
                d, r = eh.spectre(self, k, A1_DECK, "xyce", files={"a1.va": module(WRONG_DEFAULTS[k][0])})
                self.assertEqual(r.returncode, 2, r.stdout[-3000:])
                self.assertIn("Error from spectre during AHDL read-in.", r.stdout)
                self.assertIn("a1.va:6: parameter r of module a1: its default %s holds %s, which Xyce's PyMS evaluates as 0"
                              % (WRONG_DEFAULTS[k][0][len("parameter real r = "):-1], WRONG_DEFAULTS[k][1]), r.stdout)
                self.assertFalse(os.path.exists(os.path.join(d, "n.raw", "op1.dc")))
        for k, decl in enumerate(["parameter real rr = 500.0;\n  parameter real r = 2 * rr;", "parameter real r = (1000.0);",
                                  "parameter real r = - 1000.0;", "parameter real r = 1000 % 1001;"]):
            with self.subTest(case=decl):
                d, r = eh.spectre(self, "right%d" % k, A1_DECK, "xyce", files={"a1.va": module(decl)})
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                out = eh.value(os.path.join(d, "n.raw", "op1.dc"), "out")
                if "- 1000.0" in decl:
                    self.assertAlmostEqual(out / -1e9, 1.0, 3)   # r = -1k against R2 = 1k: Xyce's gmin bounds the divider
                else:
                    self.assertAlmostEqual(out, 0.5, 6)

    @needs_vacask
    def test_vacask_runs_them(self):
        for k in ("function_call", "ternary"):
            with self.subTest(case=k):
                d, r = eh.spectre(self, k, A1_DECK, "vacask", files={"a1.va": module(WRONG_DEFAULTS[k][0])})
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                self.assertAlmostEqual(eh.value(os.path.join(d, "n.raw", "op1.dc"), "out"), 0.5, 9)


# -- TRIAL2EI-1: a current read in an expression ---------------------------------------------------------------------------

READS = {
    "node_pair": (module(body="analog V(p, n) <+ r * I(p, n);"), "I(p, n)",
                  "the C++ compile of its evaluator fails on a node pair's current (`'p' was not declared in this scope'), so "
                  "Xyce would not run the module (PyMS could not build it)"),
    "named_branch": (module(extra="  branch (p, n) br;\n", body="analog V(br) <+ r * I(br);"), "I(br)",
                     "it takes a named branch's current as 0, so the module would run wrong (measured: `V(br) <+ r * I(br)' "
                     "gave out = 1 where VACASK gives 0.5, with exit status 0)"),
    "in_a_current_contribution": (module(body="analog I(p, n) <+ V(p, n) / r + 0 * I(p, n);"), "I(p, n)", "node pair"),
    "assigned_to_a_variable": (module(extra="  real cur;\n",
                                      body="analog begin\n    cur = I(p, n);\n    V(p, n) <+ r * cur;\n  end"), "I(p, n)",
                               "node pair"),
}
BUILDS = [module(), module(extra="  branch (p, n) br;\n", body="analog I(br) <+ V(br) / r;"),
          module(body="analog V(p, n) <+ 0.5;"), module(body="analog I(p, n) <+ V(p, n) / r + ddt(1e-12 * V(p, n));")]


class TestCurrentReadInAnExpression(TempDir):

    def test_the_scan(self):
        from vamos.spectre import run_xyce as RX
        for k, (va, probe, what) in READS.items():
            with self.subTest(case=k):
                scans = scan(self, k, va)
                got = RX.pyms_probe_problems([scans[0].src], scans=scans)
                self.assertEqual([p for _s, _f, _l, p, _w in got], [probe], got)
                self.assertIn("module a1 reads the current %s in an expression: Xyce's PyMS builds a contribution that "
                              "assigns a current from voltages (I(a, b) <+ f(V(...))) and reads no current: " % probe, got[0][4])
                self.assertIn(what, got[0][4])
                self.assertIn("not supported on Xyce in v1: write the contribution the other way round (I(p, n) <+ V(p, n) / "
                              "r), or run it on VACASK", got[0][4])
        for k, va in enumerate(BUILDS):
            with self.subTest(case=k):
                scans = scan(self, "builds%d" % k, va)
                self.assertEqual(RX.pyms_probe_problems([scans[0].src], scans=scans), [])

    @needs_xyce
    def test_xyce_refuses_it_before_pyms(self):
        for k in ("node_pair", "named_branch"):
            with self.subTest(case=k):
                d, r = eh.spectre(self, k, A1_DECK, "xyce", files={"a1.va": READS[k][0]})
                self.assertEqual(r.returncode, 2, r.stdout[-3000:])
                self.assertIn("Error from spectre during AHDL read-in.", r.stdout)
                self.assertIn("module a1 reads the current %s in an expression" % READS[k][1], r.stdout)
                # the runner's own report of a PyMS build that failed (after the compile) is not reached
                self.assertNotIn("which Xyce would run as a device that contributes nothing", r.stdout)
                self.assertNotIn("Xyce, the deck of analysis", r.stdout)
                self.assertFalse(os.path.exists(os.path.join(d, "n.raw", "op1.dc")))
        d, r = eh.spectre(self, "builds", A1_DECK, "xyce", files={"a1.va": BUILDS[1]})
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertAlmostEqual(eh.value(os.path.join(d, "n.raw", "op1.dc"), "out"), 0.5, 6)

    @needs_vacask
    def test_vacask_runs_it(self):
        for k in ("node_pair", "named_branch"):
            with self.subTest(case=k):
                d, r = eh.spectre(self, k, A1_DECK, "vacask", files={"a1.va": READS[k][0]})
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                self.assertAlmostEqual(eh.value(os.path.join(d, "n.raw", "op1.dc"), "out"), 0.5, 9)


# -- 3: an ic on a node whose every element is probed --------------------------------------------------------------------

IC_TWO = """// an ic beside the capacitor's and the resistor's currents
simulator lang=spectre
V1 (in 0) vsource dc=0 mag=1
R1 (in out) resistor r=1k
C1 (out 0) capacitor c=1n
%s
tr1 tran stop=1u%s
save out C1:1 R1:2
"""
IC_THREE = IC_TWO.replace("C1 (out 0) capacitor c=1n\n", "C1 (out 0) capacitor c=1n\nC2 (out 0) capacitor c=2n\n") \
                 .replace("save out C1:1 R1:2", "save out C1:1 C2:1 R1:2")
IC_PORTS = """// an ic on a node that only two subckt ports touch, both probed
simulator lang=spectre
subckt half (a b)
  R1 (a m) resistor r=1k
  R2 (m b) resistor r=1k
ends half
V1 (in 0) vsource dc=0
X1 (in out) half
X2 (out 0) half
%s
tr1 tran stop=1u
save out X1:2 X2:1
"""
E = 2.718281828459045


class TestIcOnAnAllProbedNode(TempDir):

    def test_the_xyce_probes_move_to_the_far_terminals(self):
        decks, names = eh.xyce_decks(IC_TWO % ("ic out=0.5", ""))
        for line in ("\nC1 out vamos_tn1 1e-09\n", "\nevamos_tp1 vamos_tn1 0 0 0 0.0\n", "\nR1 vamos_tn2 out 1000.0\n",
                     "\nevamos_tp2 vamos_tn2 in 0 0 0.0\n", ".IC V(OUT)=0.5"):
            self.assertIn(line, decks[0])
        self.assertEqual(names["vamos_a1"][("i", "vamos_tp1")], "I(EVAMOS_TP1)")
        self.assertEqual(names["vamos_a1"][("i", "vamos_tp2")], "I(EVAMOS_TP2)")
        # a nodeset node, and an ic from a state file, move the probes too; a node without one keeps them where they were
        decks, _n = eh.xyce_decks(IC_TWO % ("nodeset out=0.5", ""))
        self.assertIn("\nevamos_tp1 vamos_tn1 0 0 0 0.0\n", decks[0])
        self.assertIn(".NODESET V(OUT)=0.5", decks[0])
        decks, _n = eh.xyce_decks(IC_TWO % ("", ""))
        self.assertIn("\nC1 vamos_tn1 0 1e-09\n", decks[0])
        self.assertIn("\nevamos_tp1 out vamos_tn1 0 0 0.0\n", decks[0])
        # VACASK keeps its 0 V source at the probed terminal
        _nl, _plan, nl2, _sm, notes = eh.resolved(IC_TWO % ("ic out=0.5", ""), "vacask")
        probe = next(it for it in nl2.body if getattr(it, "name", "") == "vamos_tp1")
        self.assertEqual((probe.kind, probe.prim, probe.nodes), ("v", "vsource", ["out", "vamos_tn1"]))

    def test_resolve_refuses_an_ic_on_a_node_only_probes_touch_on_xyce_only(self):
        from vamos.notes import ERROR
        _nl, _plan, _nl2, _sm, notes = eh.resolved(IC_PORTS % "ic out=0.5", "xyce")
        errors = [n.message for n in notes if n.severity == ERROR]
        self.assertEqual(len(errors), 1, notes)
        self.assertEqual(errors[0], "tran tr1: ic out=0.5 is on node out, which only vamos's current probes touch (the saved "
                                    "currents X1:2, X2:1): on Xyce a probe is a zero-gain vcvs, and an .IC on a node that "
                                    "connects only through such elements makes Xyce's matrix singular (measured: `Numerically "
                                    "singular matrix found by Amesos', exit status 1, no results); not supported on Xyce in v1: "
                                    "leave one of these currents unsaved, or run it on VACASK")
        for text, engine in ((IC_PORTS % "ic out=0.5", "vacask"), (IC_PORTS % "", "xyce"), (IC_PORTS % "nodeset out=0.5", "xyce"),
                             (IC_TWO % ("ic out=0.5", ""), "xyce"), (IC_THREE % ("ic out=0.5", ""), "xyce")):
            with self.subTest(deck=text.splitlines()[0], engine=engine):
                _nl, _plan, _nl2, _sm, notes = eh.resolved(text, engine)
                self.assertEqual([n.message for n in notes if n.severity == ERROR], [])

    def the_ic_applies(self, engine):
        for k, text in (("two", IC_TWO % ("ic out=0.5", "")), ("three", IC_THREE % ("ic out=0.5", "")),
                        ("readic", IC_TWO % ("", ' readic="s.ns"'))):
            with self.subTest(engine=engine, deck=k):
                d, r = eh.spectre(self, "%s_%s" % (engine, k), text, engine, files={"s.ns": "out 0.5\n"})
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                tran = os.path.join(d, "n.raw", "tr1.tran.tran")
                tau = 1e-6 if k != "three" else 3e-6
                self.assertAlmostEqual(eh.value(tran, "out", 0.0), 0.5, 4)
                self.assertAlmostEqual(eh.value(tran, "out", 1e-6) / (0.5 * E ** (-1e-6 / tau)), 1.0, 2)
                i = 0.5e-3 * E ** (-1e-7 / tau)                     # the current through R1 at 1e-7 s
                self.assertAlmostEqual(eh.value(tran, "R1:2", 1e-7) / i, 1.0, 2)         # into the resistor's terminal 2
                self.assertAlmostEqual(eh.value(tran, "C1:1", 1e-7) / (-i * (1e-6 / tau)), 1.0, 2)   # the capacitor discharges
                self.assertAlmostEqual(eh.value(tran, "C1:1", 0.0), 0.0, 9)              # 0 at t = 0, as the operating point

    @needs_xyce
    def test_xyce_applies_the_ic(self):
        self.the_ic_applies("xyce")

    @needs_vacask
    def test_vacask_applies_the_ic(self):
        self.the_ic_applies("vacask")

    @needs_xyce
    def test_xyce_refuses_the_ports_deck(self):
        d, r = eh.spectre(self, "ports", IC_PORTS % "ic out=0.5", "xyce")
        self.assertEqual(r.returncode, 2, r.stdout[-3000:])
        self.assertIn("Error from spectre during initial setup.", r.stdout)
        self.assertIn("tran tr1: ic out=0.5 is on node out, which only vamos's current probes touch (the saved currents X1:2, "
                      "X2:1)", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(d, "n.raw", "tr1.tran.tran")))
        d, r = eh.spectre(self, "ports_noic", IC_PORTS % "", "xyce")
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])

    @needs_vacask
    def test_vacask_runs_the_ports_deck(self):
        d, r = eh.spectre(self, "ports", IC_PORTS % "ic out=0.5", "vacask")
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertAlmostEqual(eh.value(os.path.join(d, "n.raw", "tr1.tran.tran"), "out", 0.0), 0.5, 4)


# -- 5a: a netlist byte that is not UTF-8 ---------------------------------------------------------------------------------

LATIN = b"\nsimulator lang=spectre\nV1 (a 0) vsource dc=1\nR1 (a 0) resistor r=1k\nop1 dc\n"


class TestNetlistBytesNotUtf8(TempDir):

    def parse(self, name, data):
        from vamos.netlist import spectre
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return spectre.parse(path, self.tmp, spectre.SpectreParseOpts())

    def test_one_warning_names_the_file_the_line_and_the_byte(self):
        from vamos.notes import WARNING
        nl = self.parse("lat.scs", b"// caf\xe9" + LATIN)
        self.assertEqual(nl.title, "// caf�")
        warnings = [(n.origin, n.message) for n in nl.notes if n.severity == WARNING]
        self.assertEqual(warnings, [(os.path.join(self.tmp, "lat.scs") + ":1",
                                     "the byte 0xe9 is not UTF-8: the file is read as UTF-8, so the byte is read as U+FFFD (the "
                                     "replacement character), which a title or a name that holds it carries into the results "
                                     "files (§14 q.62); write the file in UTF-8")])
        # the first such byte of a file names the line; a second one adds no warning
        nl = self.parse("two.scs", b"// ok" + LATIN.replace(b"r=1k", b"r=1k // caf\xe9").replace(b"op1 dc", b"op1 dc // \xe9"))
        warnings = [(n.origin, n.message) for n in nl.notes if n.severity == WARNING]
        self.assertEqual(len(warnings), 1, warnings)
        self.assertTrue(warnings[0][0].endswith("two.scs:4"), warnings)
        # UTF-8 bytes give no warning
        nl = self.parse("utf.scs", "// 10 kΩ divider".encode("utf-8") + LATIN)
        self.assertEqual(nl.title, "// 10 kΩ divider")
        self.assertEqual([n for n in nl.notes if n.severity == WARNING], [])


# -- 5b: %A in an analysis's file names ------------------------------------------------------------------------------------

PERCENT_A = """// an analysis whose name holds a / writes a state file named by %%A
simulator lang=spectre
V1 (in 0) vsource dc=1
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
%s dc write="%%A.ns"
"""


class TestPercentAInFileNames(TempDir):

    def test_the_slash_is_escaped_as_in_the_results_file_name(self):
        from vamos.netlist import spectre
        from vamos.notes import NOTE
        from vamos.netlist.expr_ast import Str
        nl = eh.parse_deck(PERCENT_A % "op\\/1")
        a = nl.analyses[0]
        self.assertEqual((a.name, a.args["write"]), ("op/1", Str("_op_0571.ns")))
        notes = [n.message for n in nl.notes if n.severity == NOTE]
        self.assertIn("%A in the quoted strings of analysis op/1 is written as _op_0571: a / cannot be part of a file name, so "
                      "the name is written escaped by the rule of the engines' decks, as the name of its results file is",
                      notes)
        nl = eh.parse_deck(PERCENT_A % "op1")
        self.assertEqual(nl.analyses[0].args["write"], Str("op1.ns"))
        self.assertEqual([n.message for n in nl.notes if "%A" in n.message], [])
        self.assertIsNotNone(spectre)

    def the_state_file_is_written(self, engine):
        d, r = eh.spectre(self, engine, PERCENT_A % "op\\/1", engine)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertIn("Notice from spectre during circuit read-in.", r.stdout)
        self.assertIn("%A in the quoted strings of analysis op/1 is written as _op_0571", r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(d, "_op_0571.ns")), os.listdir(d))
        self.assertTrue(os.path.isfile(os.path.join(d, "n.raw", "_op_0571.dc")), os.listdir(os.path.join(d, "n.raw")))

    @needs_vacask
    def test_vacask(self):
        self.the_state_file_is_written("vacask")

    @needs_xyce
    def test_xyce(self):
        self.the_state_file_is_written("xyce")


if __name__ == "__main__":
    unittest.main()
