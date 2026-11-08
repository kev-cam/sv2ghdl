"""The fixes of phase 2e's round 7' (FIX2E7P: PyMS 1.1.7, the guide trial TRIAL2EJ's findings and the round-6 skeptic's
leftovers).  Standard library only, Python 3.9; the engine runs on WSL.

TestPymsVersion           on Xyce, Verilog-A runs with PyMS 1.1.7 or later only: an older PyMS, or one that states no
                          version (before 1.1.0), is refused before Xyce runs, naming the directory and the version;
                          the PyMS found as Xyce finds it ($PYMS_DIR, $XYCE_PREFIX/share/xyce/PyMS, the source trees)
TestPymsRunsWhatTheScanRefused
                          the forms vamos's own scan (PymsScan, rounds 4-6) refused on Xyce on PyMS's behalf now run on
                          PyMS 1.1.7 and give VACASK's value, or PyMS refuses them itself with a file:line error (an
                          undefined macro), measured with the scan off (FIX2E7P)
TestSineFreqAlteredToZero on VACASK an alter that sets a sine's parameter-bound freq to 0 carries Spectre's constant
                          through the switchable hook as a sweep point does (TRIAL2EJ-2: refused, exit 2, with a message
                          naming alter a1 as both the lost alter and the change that loses it; Xyce ran it)
TestIncludedFileNotUtf8   the non-UTF-8 warning for a byte of an included file names that file and its own line, as
                          the parser's other messages about the file do (the round-6 skeptic: `l.scs:5:3' for sub.scs
                          line 3)"""

import os
import unittest

from vamos_testlib import TempDir, needs_vacask, needs_xyce  # noqa: E402

import test_spectre_trial2eh as eh  # noqa: E402  (its deck runner and value reader)

HEAD = '`include "disciplines.vams"\n`include "constants.vams"\n'


def module(decl="parameter real r = 1k;", header="module a1(p, n);", body=None, extra="", pre="",
           ports="  inout p, n;\n  electrical p, n;\n"):
    body = body or "analog I(p, n) <+ V(p, n) / r;"
    return HEAD + pre + header + "\n" + ports + "  " + decl + "\n" + extra + "  " + body + "\nendmodule\n"


A1_DECK = """// a1: a Verilog-A resistor over R2 = 1k: out = 1000 / (r + 1000)
simulator lang=spectre
ahdl_include "a1.va"
V1 (in 0) vsource dc=1
X1 (in out) a1
R2 (out 0) resistor r=1k
op1 dc
"""


# -- 1: the PyMS version -------------------------------------------------------------------------------------------------

class TestPymsVersion(TempDir):

    def pyms(self, name, init=None, version=None):
        """A fake PyMS directory: vae/xyce_device_gen.py, with vae/__init__.py holding init and VERSION version."""
        d = os.path.join(self.tmp, name)
        self.write("%s/vae/xyce_device_gen.py" % name, "# a fake\n")
        if init is not None:
            self.write("%s/vae/__init__.py" % name, init)
        if version is not None:
            self.write("%s/VERSION" % name, version)
        return d

    def test_the_version_and_the_minimum(self):
        from vamos.spectre import run_xyce as RX
        self.assertEqual(RX.PYMS_MIN_VERSION, (1, 1, 7))
        self.assertEqual(RX.pyms_version(self.pyms("a", '"""PyMS"""\n\n__version__ = \'1.1.7\'\n')), (1, 1, 7))
        self.assertEqual(RX.pyms_version(self.pyms("b", "__version__ = \"1.2\"\n")), (1, 2))
        self.assertEqual(RX.pyms_version(self.pyms("c", "# no marker\n", "1.1.6\n")), (1, 1, 6))
        self.assertIsNone(RX.pyms_version(self.pyms("d", "# no marker\n")))
        self.assertIsNone(RX.pyms_version(self.pyms("e")))

    def test_the_directory_is_found_as_xyce_finds_it(self):
        from vamos.spectre import run_xyce as RX
        a = self.pyms("a", "__version__ = '1.1.7'\n")
        b = self.pyms("b", "__version__ = '1.1.8'\n")
        os.makedirs(os.path.join(self.tmp, "prefix", "share", "xyce"))
        os.symlink(b, os.path.join(self.tmp, "prefix", "share", "xyce", "PyMS"))
        prefix = os.path.join(self.tmp, "prefix")
        self.assertEqual(RX.pyms_dir({"PYMS_DIR": a, "XYCE_PREFIX": prefix}), a)
        self.assertEqual(RX.pyms_dir({"PYMS_DIR": os.path.join(self.tmp, "none"), "XYCE_PREFIX": prefix}),
                         os.path.join(prefix, "share", "xyce", "PyMS"))

    def test_an_older_pyms_is_refused_and_a_newer_one_is_not(self):
        from vamos.spectre import run_xyce as RX
        self.assertIsNone(RX.pyms_version_problem({"PYMS_DIR": self.pyms("new", "__version__ = '1.1.7'\n")}))
        self.assertIsNone(RX.pyms_version_problem({"PYMS_DIR": self.pyms("newer", "__version__ = '1.10.0'\n")}))
        old = self.pyms("old", "__version__ = '1.1.6'\n")
        self.assertEqual(RX.pyms_version_problem({"PYMS_DIR": old}),
                         "Xyce's Verilog-A compiler, PyMS in %s, is version 1.1.6: vamos runs Verilog-A on Xyce with "
                         "PyMS 1.1.7 or later, which compiles right or refuses the forms an older PyMS gets silently "
                         "wrong (§4.5 item 13); update Xyce's PyMS (or point PYMS_DIR at a newer one), or run on VACASK"
                         % old)
        none = self.pyms("none")
        self.assertIn("PyMS in %s, is older than 1.1.0 (it states no version): vamos runs" % none,
                      RX.pyms_version_problem({"PYMS_DIR": none}))

    @needs_xyce
    def test_xyce_refuses_an_old_pyms_before_it_runs(self):
        old = self.pyms("old", "__version__ = '1.1.6'\n")
        d, r = eh.spectre(self, "x", A1_DECK, "xyce", env={"PYMS_DIR": old}, files={"a1.va": module()})
        self.assertEqual(r.returncode, 2, r.stdout[-3000:])
        self.assertIn("Error from spectre during AHDL read-in.", r.stdout)
        self.assertIn("PyMS in %s, is version 1.1.6: vamos runs Verilog-A on Xyce with PyMS 1.1.7 or later" % old,
                      r.stdout)
        self.assertFalse(os.path.exists(os.path.join(d, "n.raw", "op1.dc")))

    @needs_xyce
    def test_a_netlist_without_verilog_a_does_not_need_pyms(self):
        old = self.pyms("old", "__version__ = '1.0'\n")
        d, r = eh.spectre(self, "x", A1_DECK.replace('ahdl_include "a1.va"\n', "").replace("a1\n", "resistor r=1k\n"),
                          "xyce", env={"PYMS_DIR": old})
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertAlmostEqual(eh.value(os.path.join(d, "n.raw", "op1.dc"), "out"), 0.5, places=9)


# -- 2: what the scan refused runs on PyMS 1.1.7 -------------------------------------------------------------------------

# out = 1000 / (r + 1000) on both engines, measured with the scan off (FIX2E7P logs/m_cases_*_v117.txt)
RUNS = {
    "undef_after_the_use": (module("parameter real r = `RV;", pre="`define RV 2k\n", extra="`undef RV\n"), 1 / 3.0),
    "defined_again_after_the_use": (module("parameter real r = `RV;", pre="`define RV 2k\n",
                                           extra="`undef RV\n`define RV 4k\n"), 1 / 3.0),
    "blank_before_the_paren": (module("parameter real r = `RV;", pre="`define RV (2k)\n"), 1 / 3.0),
    "scaled_number_in_an_expression": (module("parameter real r = 2k * 1;"), 1 / 3.0),
    "function_call": (module("parameter real r = max(1k, 2k);"), 1 / 3.0),
    "ternary": (module("parameter real r = 1 ? 2k : 1k;"), 1 / 3.0),
    "power": (module("parameter real r = 2 * 10**3;"), 1 / 3.0),
    "integer_division": (module("parameter integer a = 5;\n  parameter integer b = 2;\n  parameter real r = a / b * 1000;"),
                         1 / 3.0),
    "chained_comparison": (module("parameter real r = (3 > 2 > 1) * 1000.0 + 2000.0;"), 1 / 3.0),
    "comparison_beside_and": (module("parameter real r = ((1 & 2 > 1) + 1) * 1000.0;"), 1 / 3.0),
    "two_parameters_in_one_statement": (module("parameter real q = 4k, r = 2k;"), 1 / 3.0),
    "named_branch_current": (module(extra="  branch (p, n) b1;\n", body="analog V(b1) <+ r * I(b1);"), 0.5),
    "annotation_at_the_parameter_end": (module('parameter real r = 1k `attr(info="resistance");',
                                               pre="`define attr(txt) (*txt*)\n"), 0.5),
    "attribute_before_a_statement": (module(body='analog begin\n    (* info="x" *)\n    I(p, n) <+ V(p, n) / r;\n  end'),
                                     0.5),
}
# openvaf-r 20260616 refuses an attribute at the end of a parameter statement (measured; the CMC trees' own form)
VACASK_REFUSES = {"annotation_at_the_parameter_end": "error: unexpected token '(*'; expected ',' or ';'"}


class TestPymsRunsWhatTheScanRefused(TempDir):

    def runs(self, engine):
        for name, (va, want) in RUNS.items():
            with self.subTest(case=name):
                d, r = eh.spectre(self, name, A1_DECK, engine, files={"a1.va": va},
                                  env={"PYMS_CACHE": os.path.join(self.tmp, name, "hdl"),
                                       "PYMS_VAE_CACHE": os.path.join(self.tmp, name, "vae")})
                if engine == "vacask" and name in VACASK_REFUSES:
                    self.assertEqual(r.returncode, 2, r.stdout[-3000:])
                    self.assertIn(VACASK_REFUSES[name], r.stdout)
                    continue
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                self.assertNotIn("not supported on Xyce", r.stdout)
                self.assertAlmostEqual(eh.value(os.path.join(d, "n.raw", "op1.dc"), "out"), want, delta=1e-8)

    @needs_xyce
    def test_xyce(self):
        self.runs("xyce")

    @needs_vacask
    def test_vacask(self):
        self.runs("vacask")

    @needs_xyce
    def test_pyms_refuses_an_undefined_macro_itself(self):
        d, r = eh.spectre(self, "u", A1_DECK, "xyce", files={"a1.va": module("parameter real r = `RV;")},
                          env={"PYMS_CACHE": os.path.join(self.tmp, "u", "hdl"),
                               "PYMS_VAE_CACHE": os.path.join(self.tmp, "u", "vae")})
        self.assertEqual(r.returncode, 2, r.stdout[-3000:])
        self.assertIn("a1.va:6: `RV is not defined: no `define RV precedes this use in the file or the files it "
                      "includes (an undefined macro is an error in Verilog-A)", r.stdout.replace("\n ", " "))


# -- 3: a sine's parameter-bound freq altered to 0 -----------------------------------------------------------------------

SINE = """// sine alter
simulator lang=spectre
parameters fr=1k
V1 (in 0) vsource type=sine sinedc=0.2 ampl=1 %s sinephase=30
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
op0 dc
%s
op1 dc
tr1 tran stop=1m maxstep=10u
"""


class TestSineFreqAlteredToZero(TempDir):

    def runs(self, engine):
        # the constant sine: sinedc + ampl * sin(sinephase) = 0.7 V, out 0.35 in op0, op1 and the transient
        for name, deck in (("altered", SINE % ("freq=fr", "a1 alter dev=V1 param=freq value=0")),
                           ("constant", SINE % ("freq=0", ""))):
            with self.subTest(case=name):
                d, r = eh.spectre(self, name, deck, engine)
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                self.assertIn("V1: type=sine with freq=0 is a constant: folded into dc (§3.8.1)", r.stdout)
                raw = os.path.join(d, "n.raw")
                self.assertAlmostEqual(eh.value(os.path.join(raw, "op0.dc"), "out"), 0.35, places=9)
                self.assertAlmostEqual(eh.value(os.path.join(raw, "op1.dc"), "out"), 0.35, places=9)
                for t in (0.0, 0.25e-3, 1e-3):
                    self.assertAlmostEqual(eh.value(os.path.join(raw, "tr1.tran.tran"), "out", t), 0.35, places=9)

    @needs_vacask
    def test_vacask(self):
        self.runs("vacask")

    @needs_xyce
    def test_xyce(self):
        self.runs("xyce")

    def test_the_hook_carries_the_engines_value(self):
        from vamos.netlist.tables import CONSTANT_SINE_FREQ
        _nl, plan, _nl2, _sm, _notes = eh.resolved(SINE % ("freq=fr", "a1 alter dev=V1 param=freq value=0"), "vacask")
        alters = [a for a in plan.actions if a.op == "alter"]
        self.assertEqual(len(alters), 1, plan.actions)
        self.assertEqual(alters[0].args.get("hook_value"), CONSTANT_SINE_FREQ)
        # an alter to a frequency the engines take as written carries no hook value of its own
        _nl, plan, _nl2, _sm, _notes = eh.resolved(SINE % ("freq=fr", "a1 alter dev=V1 param=freq value=2k"), "vacask")
        self.assertNotIn("hook_value", [a for a in plan.actions if a.op == "alter"][0].args)


# -- 4: the non-UTF-8 byte of an included file ----------------------------------------------------------------------------

class TestIncludedFileNotUtf8(TempDir):

    def test_the_warning_names_the_included_file_and_its_line(self):
        from vamos.netlist import spectre
        from vamos.notes import WARNING
        top = self.write("l.scs", '// top\nsimulator lang=spectre\nV1 (in 0) vsource dc=1\nR2 (out 0) resistor r=1k\n'
                                  'include "sub.scs"\nop1 dc\n')
        with open(os.path.join(self.tmp, "sub.scs"), "wb") as fh:
            fh.write(b"// sub\nsimulator lang=spectre\nR1 (in out) resistor r=1k // caf\xe9\n")
        nl = spectre.parse(top, self.tmp, spectre.SpectreParseOpts())
        warnings = [n.origin for n in nl.notes if n.severity == WARNING and "is not UTF-8" in n.message]
        self.assertEqual(len(warnings), 1, warnings)
        self.assertTrue(warnings[0].endswith("sub.scs:3"), warnings)


if __name__ == "__main__":
    unittest.main()
