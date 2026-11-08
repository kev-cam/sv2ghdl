"""The fixes of phase 2e's fifth round (FIX2E5: the round-4 skeptic S-2E4's regressions and silent values, and the
guide trial TRIAL2EH's findings).  Standard library only, Python 3.9; the engine runs on WSL.

TestNotEqual            a word name!=value is an assignment to name! only where a statement assigns node values (ic,
                        nodeset); every other != is a comparison (S-2E4: round 4 read `sel = m0!=0 ? 3k : 1k' as
                        `sel=' with no value and `m0!=0', refused on both engines where 5d2291f gave out = 0.75)
TestFlagsGivenBack      a SIGUSR1 or SIGUSR2 that the VACASK runner took over from the flow and did not answer goes
                        back to the flow, which answers it as before (S-2E4-N1: a Verilog-A compile error dropped it);
                        TestFlagsAfterAFailedCompile, the same through run_vacask.run
TestIcBesideAProbe      on Xyce a vamos current probe is a zero-gain vcvs, so an ic, a readic and a nodeset on a node it
                        touches are kept (S-2E4-N3: Xyce applies no .IC to a node a V element touches, so `ic out=0.5'
                        with C1:1 saved gave out(0) = 0)
TestIcBesideASource     on Xyce an ic or readic entry on a node that the netlist's own voltage source (a 0 V ammeter,
                        an iprobe, a floating source) touches is refused unless voltage sources tie the node to the
                        ground (found in this round beside S-2E4-N3: `Vm (out mid) vsource dc=0' with `ic out=0.5'
                        gave out(0) = 0 with exit 0 on Xyce, where VACASK gives 0.5)
TestMacroConditionals   a Verilog-A macro on Xyce: a `define in an `ifdef branch that is not taken, a `define then
                        an `undef, a use before an `undef or a new `define (S-2E4-N2: V1:p = -1e-12 with exit 0 on
                        an older PyMS, refused by round 5's scan); PyMS 1.1.7 follows the conditions and expands a
                        macro at its point of use, giving VACASK's value or VACASK's refusal (FIX2E7P)
TestPymsDefaults        a Verilog-A parameter default an older PyMS evaluated as 0 (TRIAL2EH-F1: `1k * 1', refused
                        by round 5's scan) runs on PyMS 1.1.7 with VACASK's value (FIX2E7P)
TestVamosTokenInDefaults a --vamos-* token in the defaults variable has no effect, and its warning says so, not that it
                        is `not supported yet' (TRIAL2EH-F7)
TestSecondSignal        the second signal's stop names the analysis it came in (TRIAL2EH-F11a: `during initial
                        setup'), and a VACASK that ran to its end after a stop signal is not said to have been stopped
                        (TRIAL2EH-F11b); TestSecondSignalInTheFlow, the flow's warning; TestXyceRanToItsEnd, the same
                        on Xyce, which said nothing
TestEsccharsTexts       +escchars escapes the PSF signal names, not the title and the logFile's texts (S-2E4: psfcheck
                        rejected `\\/\\/\\ escaped' in "design")
TestVaDefineMessages    a -va,define given twice is refused once on Xyce; an empty value's fix has no double blank
TestOutdirStateFiles    a state file that goes into -outdir is written when the results go elsewhere: vamos makes the
                        directory (found in this round: `cannot write the state file o2/op1.ns', exit 2)
TestAlterAfterTheLastAnalysis
                        a source alter after the last analysis that breaks a §3.8.1 rule is a warning naming the alter,
                        the field and the rule, not an error, since no analysis runs in that state (the user,
                        2026-10-23: `fix any regressions'; X-2c8's R0057 and R0024 ran at 5d2291f and were refused
                        since round 1); the netlist's own constant stays refused, and so does the alter where an
                        analysis runs
TestResultsEncoding     the PSF ASCII, nutmeg ASCII and state files and the logFile are UTF-8 (§14 q.62, decided
                        2026-10-23 by the driver): every name and title is written as it is, with no notice and no `?'
                        (round 4 wrote Latin-1, a name it could not hold escaped and a title with `?'); a name is still
                        escaped where the format cannot hold it (a nutmeg blank or comma, a file name's /); a lone
                        surrogate os.fsdecode made of a file name's byte goes back to that byte; a state file written
                        by hand in Latin-1 still reads; psfcheck, the vendored psf-parser and ngspice read the files"""

import os
import shutil
import signal
import tempfile
import unittest

from vamos_testlib import LAUNCHER, LINUX, TempDir, needs_vacask, needs_xyce, run  # noqa: E402
from spectre_testlib import have_psf_parser, import_psf_parser, needs_ngspice, needs_psf_parser, ngspice_bin  # noqa: E402

import psfcheck  # noqa: E402  (tests/vamos: the strict reader)
import test_spectre_flow as flowlib  # noqa: E402  (its fake-runner flow cases; its test classes stay in its module)
import test_spectre_run_vacask as rvlib  # noqa: E402  (its scripted VACASK and openvaf-r)
import test_spectre_run_xyce as rxlib  # noqa: E402  (its scripted Xyce)


def parse_deck(text, files=None):
    from vamos.netlist import spectre
    d = tempfile.mkdtemp(prefix="vamos_2eh_")
    try:
        for fn, body in (files or {}).items():
            with open(os.path.join(d, fn), "w", encoding="utf-8") as fh:
                fh.write(body)
        path = os.path.join(d, "deck.scs")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return spectre.parse(path, d, spectre.SpectreParseOpts())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def resolved(text, engine):
    from vamos.netlist import plan as PL, signals as SG
    from vamos.spectre.job import Settings
    nl = parse_deck(text)
    plan = PL.build(nl, engine, Settings())
    nl2, sm, notes = SG.resolve(nl, plan)
    return nl, plan, nl2, sm, notes


def xyce_decks(text):
    from vamos.spectre import run_xyce
    _nl, plan, nl2, sm, _notes = resolved(text, "xyce")
    decks, names = run_xyce.render_decks(nl2, plan, sm)
    return [d.text for d in decks], names


def spectre(test, sub, text, engine, args=(), env=None, files=None, name="n.scs"):
    """bin/vamos -spectre on one engine in tmp/sub: (the directory, the CompletedProcess)."""
    d = os.path.join(test.tmp, sub)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        fh.write(text)
    for fn, body in (files or {}).items():
        p = os.path.join(d, fn)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
    e = {k: v for k, v in os.environ.items()
         if not k.endswith("_DEFAULTS") and k not in ("VAMOS_ANALOG", "VAMOS_BANNER")}
    e.update(env or {})
    r = run([LAUNCHER, "-spectre", "--vamos-analog=" + engine] + list(args) + [name], cwd=d, env=e, timeout=1200)
    return d, r


def value(path, signal_name, x=None):
    p = psfcheck.read(path)
    if x is not None:
        return p.at(signal_name, x)
    return p.column(signal_name) if p.points else p.value(signal_name)


# -- 1: != -------------------------------------------------------------------------------------------------------------

NE = """// %s
simulator lang=spectre
parameters m0=1
%s
V1 (in 0) vsource dc=1
R1 (in out) resistor r=1k
%s
op1 dc
"""
# S-2E4's seven decks (probes_r4.py G_ne_expressions p1, p4, p5; probes_r4b.py NE_*): out = 0.75 on 5d2291f and round 3
NE_CASES = {
    "p1": ("a comparison after a spaced =", "parameters sel = m0!=0 ? 3k : 1k", "R2 (out 0) resistor r=sel"),
    "p4": ("a comparison after an operator word", "parameters sel = 1k + m0!=0 ? 3k : 1k", "R2 (out 0) resistor r=sel"),
    "p5": ("an instance parameter with a spaced =", "", "R2 (out 0) resistor r = m0!=0 ? 3k : 1k"),
    "sub_default": ("a subckt parameter default",
                    "subckt half (p q)\n  parameters rr = m0!=0 ? 3k : 1k\n  RB (p q) resistor r=rr\nends half",
                    "X2 (out 0) half"),
    "inst_param": ("an X instance parameter",
                   "subckt half (p q)\n  parameters rr=1k\n  RB (p q) resistor r=rr\nends half",
                   "X2 (out 0) half rr = m0!=0 ? 3k : 1k"),
    "model": ("a model card parameter", "model rm resistor r = m0!=0 ? 3k : 1k", "R2 (out 0) rm"),
    "two_params": ("two parameters on one line", "parameters a1=2 sel = m0!=0 ? 3k : 1k", "R2 (out 0) resistor r=sel"),
}


class TestNotEqual(TempDir):

    def test_a_comparison_after_a_spaced_equals_is_a_comparison(self):
        from vamos.netlist import expr as X
        for k, (title, top, line) in NE_CASES.items():
            with self.subTest(case=k):
                nl = parse_deck(NE % (title, top, line))              # no NoteError (`sel= has no value' before)
                if "sel" in nl.values:
                    self.assertEqual(nl.values["sel"], 3000.0)
        nl = parse_deck(NE % ("", "", "R2 (out 0) resistor r = m0!=0 ? 3k : 1k"))
        r2 = next(it for it in nl.instances() if it.name == "R2")
        self.assertEqual(X.evaluate(r2.value, nl.values, "spectre"), 3000.0)

    def test_a_node_whose_name_ends_in_a_bang_in_ic_and_nodeset(self):
        """FIX2E4's gap stays closed: `nodeset vdd!=0.4' sets the node vdd!, in ic too, beside other nodes and for a
        hierarchical name; a value after a spaced = stays an expression whose != is a comparison."""
        from vamos.netlist import expr as X
        nl = parse_deck("// t\nsimulator lang=spectre\nparameters m0=1\nglobal 0 vdd!\nV1 (vdd! 0) vsource dc=1\n"
                        "R1 (vdd! a) resistor r=1k\nR2 (a 0) resistor r=1k\nX1 (a 0) cell\n"
                        "subckt cell (p q)\n  R1 (p n!) resistor r=1k\n  R2 (n! q) resistor r=1k\nends cell\n"
                        "nodeset vdd!=0.4 a=0.2 X1.n!=0.1\nic a=0.3 vdd!=0.5\nnodeset b = m0!=0 ? 0.4 : 0.1\nop1 dc\n")
        self.assertEqual(sorted(nl.nodesets), ["X1.n!", "a", "b", "vdd!"])
        self.assertEqual(sorted(nl.ics), ["a", "vdd!"])
        self.assertEqual(X.evaluate(nl.nodesets["b"], nl.values, "spectre"), 0.4)

    def check(self, engine):
        for k, (title, top, line) in NE_CASES.items():
            with self.subTest(engine=engine, case=k):
                d, r = spectre(self, "%s_%s" % (engine, k), NE % (title, top, line), engine)
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                self.assertAlmostEqual(value(os.path.join(d, "n.raw", "op1.dc"), "out"), 0.75, 6)

    @needs_vacask
    def test_vacask(self):
        self.check("vacask")

    @needs_xyce
    def test_xyce(self):
        self.check("xyce")


# -- 2: the flags the VACASK runner did not answer ---------------------------------------------------------------------

@unittest.skipUnless(hasattr(signal, "SIGUSR1"), "needs SIGUSR1")
class TestFlagsGivenBack(unittest.TestCase):

    def test_flags_the_runner_did_not_answer_go_back_to_the_flow(self):
        from vamos.spectre import flow, run_vacask, run_xyce
        for make in (run_vacask._Interrupts, run_xyce.Signals):
            with self.subTest(runner=make.__module__):
                sig = flow._Signals()
                sig.install()
                try:
                    os.kill(os.getpid(), signal.SIGUSR1)
                    os.kill(os.getpid(), signal.SIGUSR2)
                    self.assertTrue(sig.pending())
                    with make():
                        pass                                # a runner that fails before its engine answers none
                    self.assertEqual(sig.take_flags(), [signal.SIGUSR1, signal.SIGUSR2])
                    os.kill(os.getpid(), signal.SIGUSR1)
                    with make() as intr:
                        self.assertEqual(intr.take_flags(), [signal.SIGUSR1])      # answered: not given back
                    self.assertFalse(sig.pending())
                finally:
                    sig.restore()


class TestFlagsAfterAFailedCompile(rvlib.FakeCase):
    """S-2E4-N1 through run_vacask.run: openvaf-r fails before VACASK runs; the SIGUSR1 the flow had recorded (and the
    runner took over, TRIAL2EG-F07) was dropped, where 5d2291f's flow answered it after the error."""

    @rvlib.needs_signals
    def test_a_compile_error_gives_the_flag_back(self):
        from vamos.spectre import flow
        bad = rvlib.VR_VA.replace("endmodule", "// fake_syntax_error\nendmodule")
        deck, _steps = self.setup_deck(rvlib.VA_DECK, {"vr.va": bad})
        self.fake({"steps": [{"id": "vamos_a1", "raw": rvlib.op_raw()}]})
        sig = flow._Signals()
        sig.install()
        try:
            os.kill(os.getpid(), signal.SIGUSR1)
            er, *_ = self.run_deck(deck)
            self.assertEqual(er.rc, 2, self.log_text())
            self.assertIn("openvaf-r failed (exit status 65)", self.log_text())
            self.assertEqual(sig.take_flags(), [signal.SIGUSR1])
        finally:
            sig.restore()


# -- 3a: an ic beside a current probe on Xyce ------------------------------------------------------------------------

IC_C = """// an RC whose capacitor current is saved, with an ic on its node
simulator lang=spectre
V1 (in 0) vsource dc=0
R1 (in out) resistor r=1k
C1 (out 0) capacitor c=1n
%s
tr1 tran stop=1u%s
save out C1:1
"""
IC_R = """// an RC with an ic; the resistor's terminal 1 is the ic's node; an ac analysis
simulator lang=spectre
V1 (in 0) vsource dc=0 mag=1
R1 (out in) resistor r=1k
C1 (out 0) capacitor c=1n
ic out=0.5
tr1 tran stop=1u
ac1 ac start=1k stop=10k dec=1
save out R1:1
"""
TAU = 1e-6                                              # R1 C1


class TestIcBesideAProbe(TempDir):

    def test_the_xyce_probe_is_a_zero_gain_vcvs(self):
        decks, names = xyce_decks(IC_C % ("", ""))
        self.assertIn("\nevamos_tp1 out vamos_tn1 0 0 0.0\n", decks[0])
        self.assertIn("\nC1 vamos_tn1 0 1e-09\n", decks[0])
        self.assertEqual(names["vamos_a1"][("i", "vamos_tp1")], "I(EVAMOS_TP1)")
        # FIX2E6 (S-2E5 3a): on an ic node the probe of a two-terminal element sits in series with its other terminal,
        # reversed, the same current (test_spectre_trial2ei.TestIcOnAnAllProbedNode); re-pinned on purpose
        decks, names = xyce_decks(IC_C % ("ic out=0.5", ""))
        self.assertIn("\nevamos_tp1 vamos_tn1 0 0 0 0.0\n", decks[0])
        self.assertIn("\nC1 out vamos_tn1 1e-09\n", decks[0])
        self.assertIn(".IC V(OUT)=0.5", decks[0])
        self.assertEqual(names["vamos_a1"][("i", "vamos_tp1")], "I(EVAMOS_TP1)")
        _nl, _plan, nl2, _sm, _notes = resolved(IC_C % ("ic out=0.5", ""), "vacask")
        probe = next(it for it in nl2.body if getattr(it, "name", "") == "vamos_tp1")
        self.assertEqual((probe.kind, probe.prim), ("v", "vsource"))     # VACASK keeps its 0 V source

    def the_ic_is_kept(self, engine):
        decks = {"c1": IC_C % ("ic out=0.5", ""), "r1": IC_C % ("", ' readic="s.ns"'), "a0": IC_R}
        for k, text in decks.items():
            with self.subTest(engine=engine, deck=k):
                d, r = spectre(self, "%s_%s" % (engine, k), text, engine, files={"s.ns": "out 0.5\n"})
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                tran = os.path.join(d, "n.raw", "tr1.tran.tran")
                self.assertAlmostEqual(value(tran, "out", 0.0), 0.5, 4)
                self.assertAlmostEqual(value(tran, "out", 1e-6) / (0.5 * 2.718281828459045 ** -1), 1.0, 2)
                cur = "C1:1" if k != "a0" else "R1:1"
                sign = -1.0 if k != "a0" else 1.0      # the capacitor discharges: its terminal current flows out
                self.assertAlmostEqual(value(tran, cur, 1e-7) / (sign * 0.5e-3 * 2.718281828459045 ** -0.1), 1.0, 2)

    def names_deck(self, engine):
        """The names test's own deck (test_spectre_names.DECK): `ic b.c=0.3' beside the probe of X.1.R1:1; Xyce gave
        b.c(0) = 0.432 for both spellings (S-2E4), and the test compared the spellings only."""
        import test_spectre_names as names
        for odd in (False, True):
            with self.subTest(engine=engine, odd=odd):
                text = names.spelled(names.DECK, odd)
                d, r = spectre(self, "%s_names_%s" % (engine, odd), text, engine)
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                tran = os.path.join(d, "n.raw", "tr1.tran.tran")
                dot = "b.c" if odd else names.NAMES["DOT"][2]
                self.assertAlmostEqual(value(tran, dot, 0.0), 0.3, 6)

    @needs_xyce
    def test_xyce_keeps_the_ic(self):
        self.the_ic_is_kept("xyce")

    @needs_vacask
    def test_vacask_keeps_the_ic(self):
        self.the_ic_is_kept("vacask")

    @needs_xyce
    def test_xyce_keeps_the_names_deck_s_ic(self):
        self.names_deck("xyce")

    @needs_vacask
    def test_vacask_keeps_the_names_deck_s_ic(self):
        self.names_deck("vacask")

    @needs_vacask
    @needs_xyce
    def test_a_state_file_holds_the_same_currents_on_both_engines(self):
        """The currents a probe measures (C1:1, V1:n) are written to the state files as before, on both engines."""
        deck = ("// currents in a state file\nsimulator lang=spectre\nV1 (in 0) vsource dc=1\nR1 (in out) resistor "
                "r=1k\nC1 (out 0) capacitor c=1n\nR2 (out 0) resistor r=1k\nI1 (out 0) isource dc=0.1m\n"
                "op1 dc write=\"op1.ns\"\nsave out C1:1 V1:n V1:p R1:2 I1:sink\n")
        got = {}
        for engine in ("vacask", "xyce"):
            d, r = spectre(self, "st_" + engine, deck, engine)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            with open(os.path.join(d, "op1.ns"), encoding="utf-8") as fh:
                got[engine] = [ln.split()[0] for ln in fh if not ln.startswith("#")]
        self.assertEqual(got["vacask"], ["in", "out", "V1:p", "C1:1", "V1:n", "R1:2", "I1:sink"])
        self.assertEqual(got["xyce"], got["vacask"])


# -- 3a, found in this round: an ic beside the netlist's own voltage source on Xyce ---------------------------------

IC_SRC = """// an ic on a node that a voltage source of the netlist touches
simulator lang=spectre
V1 (in 0) vsource dc=0
R1 (in out) resistor r=1k
%s
C1 (mid 0) capacitor c=1n
%s
tr1 tran stop=1u%s
save out mid
"""
# (the source lines, the ic statement, the tran's extra arguments) -> (refused on Xyce, out(0) where it runs: the ic
# forces out where no source pins it, and has no effect on a node voltage sources tie to the ground)
IC_SRC_CASES = {
    "ammeter": ("Vm (out mid) vsource dc=0", "ic out=0.5", "", True, 0.5),
    "iprobe": ("Vm (out mid) iprobe", "ic out=0.5", "", True, 0.5),
    "a_floating_source": ("Vm (out mid) vsource dc=0.3", "ic out=0.5", "", True, 0.5),
    "the_other_node": ("Vm (out mid) vsource dc=0", "ic mid=0.5", "", True, 0.5),
    "readic": ("Vm (out mid) vsource dc=0", "", ' readic="s.ns"', True, 0.5),
    "to_the_ground": ("Vm (out 0) vsource dc=0\nRm (out mid) resistor r=1", "ic out=0.5", "", False, 0.0),
    "a_chain_to_the_ground": ("Vm (out mid) vsource dc=0\nV2 (mid 0) vsource dc=0.2", "ic out=0.5", "", False, 0.2),
    "nodeset_only": ("Vm (out mid) vsource dc=0", "nodeset out=0.5", "", False, 0.0),
    "ic_dev": ("Vm (out mid) vsource dc=0", "ic out=0.5", " ic=dev", False, 0.0),
}
IC_SRC_REFUSAL = "Xyce applies no .IC to a node a voltage source touches"


class TestIcBesideASource(TempDir):
    """Found in this round beside S-2E4-N3: Xyce applies no .IC to a node a V element touches, so an ic on a node that
    the netlist's own voltage source (a 0 V ammeter, an iprobe, a floating source) touches was lost silently (measured:
    `Vm (out mid) vsource dc=0' beside `ic out=0.5' gave out(0) = 0 with exit 0, where VACASK gives 0.5), unless
    voltage sources tie the node to the ground, where the ic has no effect on either engine; refused on Xyce."""

    def deck(self, k, state_file):
        src, ic, extra, _refused, _v = IC_SRC_CASES[k]
        return IC_SRC % (src, ic, extra.replace("s.ns", state_file))

    def test_resolve_refuses_it_on_xyce_only(self):
        from vamos.notes import ERROR
        for k, (_src, _ic, _extra, refused, _v) in IC_SRC_CASES.items():
            with self.subTest(case=k):
                text = self.deck(k, self.write("%s/s.ns" % k, "out 0.5\n"))
                _nl, _plan, _nl2, _sm, notes = resolved(text, "xyce")
                errors = [n.message for n in notes if n.severity == ERROR and IC_SRC_REFUSAL in n.message]
                if refused:
                    node = "mid" if k == "the_other_node" else "out"
                    self.assertEqual(len(errors), 1, notes)
                    self.assertTrue(errors[0].startswith("tran tr1: "), errors[0])
                    self.assertIn("%s=0.5 is on a node the voltage source Vm touches" % node, errors[0])
                    self.assertIn("readic=" if k == "readic" else " ic %s=0.5" % node, errors[0])
                else:
                    self.assertEqual(errors, [], notes)
                _nl, _plan, _nl2, _sm, notes = resolved(text, "vacask")
                self.assertEqual([n for n in notes if IC_SRC_REFUSAL in n.message], [])

    def run_cases(self, engine):
        for k, (_src, _ic, _extra, refused, v) in IC_SRC_CASES.items():
            with self.subTest(engine=engine, case=k):
                d, r = spectre(self, "%s_%s" % (engine, k), self.deck(k, "s.ns"), engine, files={"s.ns": "out 0.5\n"})
                tran = os.path.join(d, "n.raw", "tr1.tran.tran")
                if engine == "xyce" and refused:
                    self.assertEqual(r.returncode, 2, r.stdout[-3000:])
                    self.assertIn("Error from spectre during initial setup.", r.stdout)
                    self.assertIn("is on a node the voltage source Vm touches", r.stdout)
                    self.assertIn(IC_SRC_REFUSAL, r.stdout)
                    self.assertFalse(os.path.exists(tran))
                else:
                    self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                    self.assertAlmostEqual(value(tran, "out", 0.0), v, 3)

    @needs_xyce
    def test_xyce(self):
        self.run_cases("xyce")

    @needs_vacask
    def test_vacask(self):
        self.run_cases("vacask")


# -- 3b: the macro check follows the preprocessor --------------------------------------------------------------------

VR = """`include "disciplines.vams"
%s
module vres3(p, n);
  inout p, n;
  electrical p, n;
  parameter real r = %s;
  analog I(p, n) <+ V(p, n) / r;
endmodule
%s"""
VA_DECK = """// a Verilog-A resistor
simulator lang=spectre
ahdl_include "vres3.va"
V1 (in 0) vsource dc=1
X1 (in 0) vres3
op1 dc
"""
# (before the module, the parameter's default, after the module) -> the macros flagged, as (line, name)
MACRO_CASES = {
    "false_ifdef": ("`ifdef FOO\n`define RV 2000.0\n`endif", "`RV", "", [(8, "RV")]),
    "undef": ("`define RV 2000.0\n`undef RV", "`RV", "", [(7, "RV")]),
    "elsif_not_taken": ("`ifdef FOO\n`define RV 4000.0\n`elsif BAR\n`define RV 3000.0\n`endif", "`RV", "",
                        [(10, "RV")]),
    "use_then_undef": ("`define RV 2000.0", "`RV", "`undef RV\n", [(6, "RV")]),
    "redefined_after_use": ("`define RV 4000.0", "`RV", "`define RV 2000.0\n", [(6, "RV")]),
    "use_before_define": ("", "`RV", "`define RV 2000.0\n", [(6, "RV")]),
    "nested_body_undef": ("`define RB 2000.0\n`define RV `RB", "`RV", "`undef RB\n", [(7, "RB")]),
    "ifdef_else": ("`ifdef FOO\n`define RV 4000.0\n`else\n`define RV 2000.0\n`endif", "`RV", "", []),
    "ifndef": ("`ifndef FOO\n`define RV 2000.0\n`endif", "`RV", "", []),
    "defined_again_after_undef": ("`define RV 4000.0\n`undef RV\n`define RV 2000.0", "`RV", "", []),
    "elsif_else": ("`ifdef FOO\n`define RV 4000.0\n`elsif BAR\n`define RV 3000.0\n`else\n`define RV 2000.0\n`endif",
                   "`RV", "", []),
    "nested_conditions": ("`define A\n`ifdef A\n`ifdef FOO\n`define RV 4000.0\n`else\n`define RV 2000.0\n`endif\n"
                          "`endif", "`RV", "", []),
    "use_in_a_branch_not_taken": ("`ifdef FOO\n`define RX `NOSUCH\n`endif", "2000.0", "", []),
    "condition_on_an_undef": ("`define FOO\n`undef FOO\n`ifdef FOO\n`define RV 4000.0\n`else\n`define RV 2000.0\n"
                              "`endif", "`RV", "", []),
    "pyms_predefines_insideADMS": ("`ifdef insideADMS\n`define RV 2000.0\n`endif", "`RV", "", []),
    "same_definition_again": ("`define RV 2000.0", "`RV", "`define RV 2000.0\n", []),
    "nested_body": ("`define RB 2000.0\n`define RV `RB", "`RV", "", []),
    # PyMS takes a '(' after blanks for an argument list: `define RV (2000.0) is RV(2000.0) with no body, which leaves a
    # use `RV as written (measured on Xyce: V1:p = -1e-12, exit 0; VACASK gives -5e-4)
    "parenthesized_body": ("`define RV (2000.0)", "`RV", "", [(6, "RV")]),
    "with_an_argument": ("`define RV(x) (x)", "`RV(2000.0)", "", []),
}


# the outcome on PyMS 1.1.7 and on VACASK, measured (FIX2E7P): V1:p = -1 / r, or the line of the use both refuse as
# undefined there (PyMS: "`RV is not defined: no `define RV precedes this use ..."; openvaf-r: "macro '`RV' has not been
# declared"); PyMS expands a macro at its point of use with the definition in force there, as Verilog-A does
MACRO_OUTCOME = {
    "false_ifdef": ("undefined", 8), "undef": ("undefined", 7), "elsif_not_taken": ("undefined", 10),
    "use_before_define": ("undefined", 6),
    "use_then_undef": ("value", -5e-4), "redefined_after_use": ("value", -2.5e-4), "nested_body_undef": ("value", -5e-4),
    "parenthesized_body": ("value", -5e-4), "ifdef_else": ("value", -5e-4), "defined_again_after_undef": ("value", -5e-4),
    "pyms_predefines_insideADMS": ("value", -5e-4), "with_an_argument": ("value", -5e-4),
}
# PyMS defines insideADMS itself, openvaf-r does not: the `define in the `ifdef insideADMS branch is not taken there
VACASK_OUTCOME = {"pyms_predefines_insideADMS": ("undefined", 8)}


class TestMacroConditionals(TempDir):
    """FIX2E7P: round 5's scan of the preprocessor (PymsScan) is gone: PyMS 1.1.7 evaluates the conditions and expands a
    macro at its point of use, and refuses one undefined there itself, as VACASK does."""

    def outcomes(self, engine):
        for k, (what, want) in MACRO_OUTCOME.items():
            if engine == "vacask":
                what, want = VACASK_OUTCOME.get(k, (what, want))
            with self.subTest(case=k):
                pre, use, post, _flagged = MACRO_CASES[k]
                d, r = spectre(self, k, VA_DECK, engine, files={"vres3.va": VR % (pre, use, post)},
                               env={"PYMS_CACHE": os.path.join(self.tmp, k, "hdl"),
                                    "PYMS_VAE_CACHE": os.path.join(self.tmp, k, "vae")})
                if what == "value":
                    self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                    self.assertAlmostEqual(value(os.path.join(d, "n.raw", "op1.dc"), "V1:p"), want, 9)
                    continue
                self.assertEqual(r.returncode, 2, r.stdout[-3000:])
                self.assertNotIn("not supported on Xyce", r.stdout)
                if engine == "xyce":
                    self.assertIn("vres3.va:%d: `RV is not defined: no `define RV precedes this use" % want, r.stdout)
                else:
                    self.assertIn("macro '`RV' has not been declared", r.stdout)
                    self.assertIn("vres3.va:%d:" % want, r.stdout)
                self.assertFalse(os.path.exists(os.path.join(d, "n.raw", "op1.dc")))

    @needs_xyce
    def test_xyce(self):
        self.outcomes("xyce")

    @needs_vacask
    def test_vacask(self):
        self.outcomes("vacask")


# -- F1: parameter defaults PyMS evaluates as 0 ----------------------------------------------------------------------

A1 = """`include "disciplines.vams"
`include "constants.vams"
%s
module a1(p, n);
  inout p, n;
  electrical p, n;
  %s
  analog I(p, n) <+ V(p, n) / r;
endmodule
"""
A1_DECK = """// a1
simulator lang=spectre
ahdl_include "a1.va"
V1 (in 0) vsource dc=1
X1 (in out) a1
R2 (out 0) resistor r=1k
op1 dc
"""
# (a define before the module, the declarations): each gave out = 0 with exit 0 on Xyce's PyMS before 1.1.0, and
# round 5 refused them on Xyce; PyMS 1.1.7 runs them right (FIX2E7P)
BAD_DEFAULTS = {
    "scaled_in_an_expression": ("", "parameter real r = 1k * 1;"),
    "scaled_times_two": ("", "parameter real r = 2 * 0.5k;"),
    "in_parentheses": ("", "parameter real r = (1k);"),
    "negated_twice": ("", "parameter real r = -(-1k);"),
    "a_capital_k": ("", "parameter real r = 1K * 1;"),
    "with_constants": ("", "parameter real r = 1k * `M_PI / `M_PI;"),
    "an_atto": ("", "parameter real r = 1000a * 1e18;"),
    "through_a_macro": ("`define RV 1k * 1", "parameter real r = `RV;"),
    "after_an_attribute": ("", '(* desc="r" *) parameter real r = 1k * 1;'),
    "two_in_one_statement": ("", "parameter real r = 1000.0, g = 1.0;"),
}
# measured right on Xyce before 1.1.0 too (out = 0.5)
GOOD_DEFAULTS = {
    "scaled_alone": ("", "parameter real r = 1k;"),
    "capital_k_alone": ("", "parameter real r = 1K;"),
    "signed": ("", "parameter real r = +1k;"),
    "unscaled_expression": ("", "parameter real r = 1000 * 1;"),
    "exponent": ("", "parameter real r = 1.0e3 * 1;"),
    "constants": ("", "parameter real r = `M_PI * 1000.0 / `M_PI;"),
    "a_range": ("", "parameter real r = 1k from (0:inf);"),
    "a_macro_of_one_number": ("`define RV 1k", "parameter real r = `RV;"),
    "another_parameter": ("", "parameter real rr = 500.0; parameter real r = 2 * rr;"),
}


class TestPymsDefaults(TempDir):
    """FIX2E7P: PyMS 1.1.7 evaluates a parameter default as Verilog-A does (vae/va_const.py), so round 5's refusal of the
    defaults an older PyMS took as 0 is gone: each runs on both engines with out = 0.5 (measured)."""

    def runs(self, engine):
        for k, (pre, decl) in list(BAD_DEFAULTS.items()) + list(GOOD_DEFAULTS.items()):
            with self.subTest(case=k):
                d, r = spectre(self, k, A1_DECK, engine, files={"a1.va": A1 % (pre, decl)},
                               env={"PYMS_CACHE": os.path.join(self.tmp, k, "hdl"),
                                    "PYMS_VAE_CACHE": os.path.join(self.tmp, k, "vae")})
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                self.assertAlmostEqual(value(os.path.join(d, "n.raw", "op1.dc"), "out"), 0.5, 8)

    @needs_xyce
    def test_xyce(self):
        self.runs("xyce")

    @needs_vacask
    def test_vacask(self):
        self.runs("vacask")


# -- F7: a --vamos-* token in the defaults variable ------------------------------------------------------------------

class TestVamosTokenInDefaults(unittest.TestCase):

    def test_the_warning_says_it_has_no_effect(self):
        from vamos import optable
        from vamos.personalities import spectre as personality
        job = personality.build_job(["x.scs"], {"SPECTRE_DEFAULTS": "--vamos-analog=xyce --vamos-keep"}, os.getcwd(),
                                    "spectre")
        texts = [n.message for n in optable.unmapped_notes(job)]
        why = ("vamos options are read from the command line only, not from SPECTRE_DEFAULTS; use VAMOS_ANALOG, "
               "VAMOS_BANNER or VAMOS_VERBOSE")
        self.assertEqual(texts, ["--vamos-analog=xyce has no effect: " + why, "--vamos-keep has no effect: " + why])
        self.assertEqual(optable.strict_failures(job), ["--vamos-analog=xyce", "--vamos-keep"])   # strict: an error


# -- F11: the second signal, and an engine that runs to its end ------------------------------------------------------

class TestSecondSignal(rvlib.FakeCase):

    def all_ok(self, steps):
        return rvlib.TestFakeVacask.all_ok(self, steps)              # its spec of rvlib.FAKE_DECK's five steps

    @rvlib.needs_signals
    def test_the_stop_names_the_analysis_it_came_in(self):
        """F11a: a second SIGINT while VACASK (one that ignores the forwarded SIGTERM) runs tr1: stop.Stop carries the
        phase of tr1, which the flow's warning names (it said `during initial setup')."""
        from vamos.spectre import stop
        deck, steps = self.setup_deck()
        spec = self.all_ok(steps)
        spec["ignore_term"] = True
        spec["steps"][1]["raw"] = rvlib.tran_raw(complete=False, events=[{"at": 3, "sig": "SIGINT", "sleep": 0.3},
                                                                         {"at": 3, "sig": "SIGINT", "sleep": 20}])
        self.fake(spec)
        with rvlib.QuietSignals():
            with self.assertRaises(stop.Stop) as cm:
                self.run_deck(deck)
        self.assertEqual((cm.exception.signum, cm.exception.phase), (signal.SIGINT, "transient analysis `tr1'"))

    @rvlib.needs_signals
    def test_an_engine_that_ran_to_its_end_is_not_said_to_be_stopped(self):
        """F11b: VACASK ignores the SIGTERM vamos passed on and finishes every analysis by itself: the run ends with
        status 3 and every analysis written, and the warning says that VACASK ran to its end (it said `the run was
        stopped (vamos got SIGINT and stopped VACASK)')."""
        deck, steps = self.setup_deck()
        spec = self.all_ok(steps)
        spec["ignore_term"] = True
        spec["steps"][1]["raw"] = rvlib.tran_raw(events=[{"at": 3, "sig": "SIGINT", "sleep": 0.3}])
        self.fake(spec)
        with rvlib.QuietSignals():
            er, *_ = self.run_deck(deck)
        text = self.log_text()
        self.assertEqual(er.rc, 3, text)
        self.assertEqual(set(er.status.values()), {"ok"}, text)
        self.assertNotIn("stopped VACASK", text)
        self.assertIn("vamos got SIGINT and passed it on, but VACASK ran every analysis to its end: all of them are "
                      "written", text)


class TestXyceRanToItsEnd(rxlib.FakeCase):
    """F11b on Xyce: a Xyce that ignores the SIGTERM vamos passed on ran its deck to its end; the run ended with status
    3 and no word of the signal (measured with a wrapper that traps the stop signals)."""

    @unittest.skipUnless(hasattr(signal, "SIGKILL") and hasattr(os, "setpgid"), "needs POSIX signals")
    def test_the_warning_says_xyce_ran_the_analysis_to_its_end(self):
        import threading
        import time
        saved = {s: signal.signal(s, lambda *a: None) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            ch = rxlib.Chain(os.path.join(rxlib.E2E, "01_rc", "deck.scs"))
            run_dir = os.path.join(self.tmp, "run")
            self.behave(run_dir, ch, {"vamos_a2.cir": {"ignore_term": True, "sleep": 1.5}})

            def send():
                end = time.time() + 60
                calls = os.path.join(run_dir, "fake.calls")
                while time.time() < end:
                    if os.path.isfile(calls):
                        with open(calls) as fh:
                            if "vamos_a2.cir" in [ln.strip() for ln in fh]:
                                break
                    time.sleep(0.02)
                time.sleep(0.4)
                os.kill(os.getpid(), signal.SIGINT)
            th = threading.Thread(target=send, daemon=True)
            th.start()
            er, log, _run_dir = rxlib.run_chain(self, ch)
            th.join(10)
        finally:
            for s, h in saved.items():
                signal.signal(s, h)
        self.assertEqual(er.rc, 3, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "ok"}, log)
        self.assertIn("vamos got SIGINT and passed it on, but Xyce ran analysis `myac' to its end: its results are "
                      "written, and the analyses after it are not run", log)


class TestSecondSignalInTheFlow(flowlib.FlowCase):

    def test_the_flow_s_warning_names_the_runner_s_phase(self):
        from vamos.spectre import stop

        def runner(*a, **k):
            raise stop.Stop(signal.SIGINT, phase="transient analysis `tr1'")
        self.deck(flowlib.TWO)
        self.use(runner)
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertEqual(rc, 3, out)
        self.assertIn("Warning from spectre during transient analysis `tr1'.\n    stopped by a second SIGINT: the "
                      "engine was killed and nothing is converted (§2.7)\n", out)

        def runner0(*a, **k):
            raise stop.Stop(signal.SIGINT)
        self.use(runner0)
        rc, out, _ = self.spectre(["rc.scs"])
        self.assertIn("Warning from spectre during initial setup.\n    stopped by a second SIGINT", out)


# -- +escchars: the names, not the texts -----------------------------------------------------------------------------

class TestEsccharsTexts(TempDir):

    def test_the_title_and_the_logfile_s_texts_are_not_escaped(self):
        from vamos.output import psf
        from vamos.spectre.results import AnalysisResult, Signal
        head = psf.PsfHead(version="0.1.0", date="1:02:03 AM, Mon Oct 19, 2026", design="// escaped node names")
        res = AnalysisResult("op1-dc", "dc", "op1.dc", tree="", description="DC Analysis `op1'",
                             header={"tolerance.relative": 1e-3, "reltol": 1e-3, "abstol(V)": 1e-6, "abstol(I)": 1e-12,
                                     "temp": 27.0, "tnom": 27.0, "tempeffects": "all", "gmindc": 1e-12},
                             data_type="scalar",
                             signals=[Signal("a<0>", "V", "V", [0.5]), Signal("out", "V", "V", [0.25])])
        psf.write_analysis(self.tmp, res, head, escchars=True)
        psf.write_logfile(self.tmp, [res], head, escchars=True, stamp="Mon Oct 19 01:02:03 2026")
        with open(os.path.join(self.tmp, "op1.dc"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('"design" "// escaped node names"\n', text)
        self.assertIn('"a\\<0\\>" "V" 5', text)                           # a signal name is escaped
        with open(os.path.join(self.tmp, "logFile"), encoding="utf-8") as fh:
            log = fh.read()
        self.assertIn('"design" "// escaped node names"\n', log)
        self.assertIn('"DC Analysis `op1\'"', log)
        self.assertEqual(psfcheck.check_logfile(os.path.join(self.tmp, "logFile"), escchars=True).keys(), ["op1-dc"])
        # plain signal names: the files under +escchars are the strict reader's without its +escchars rules too (the
        # escaped title broke them before)
        plain = os.path.join(self.tmp, "plain")
        res.signals = [Signal("out", "V", "V", [0.25])]
        psf.write_analysis(plain, res, head, escchars=True)
        psf.write_logfile(plain, [res], head, escchars=True, stamp="Mon Oct 19 01:02:03 2026")
        self.assertEqual(psfcheck.check_logfile(os.path.join(plain, "logFile")).keys(), ["op1-dc"])


# -- -va,define messages ----------------------------------------------------------------------------------------------

VRES = """`include "disciplines.vams"
module vres(p, n);
  inout p, n;
  electrical p, n;
  parameter real r = 1000.0;
  analog I(p, n) <+ V(p, n) / r;
endmodule
"""
VRES_DECK = """// a Verilog-A resistor
simulator lang=spectre
ahdl_include "vres.va"
V1 (a 0) vsource dc=1
X1 (a 0) vres
op1 dc
"""


class TestVaDefineMessages(flowlib.FlowCase):

    def test_a_repeated_define_is_refused_once_and_an_empty_value_has_one_blank(self):
        self.write("vres.va", VRES)
        self.deck(VRES_DECK, "va.scs")
        self.use(flowlib.never_called, engine="xyce")
        rc, out, _ = self.spectre(["-va,define", "BIGR", "-va,define", "BIGR", "va.scs"], {"analog": "xyce"})
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.count("-va,define BIGR: not supported on Xyce in v1"), 1, out)
        rc, out, _ = self.spectre(["-va,define", "M=", "va.scs"], {"analog": "xyce"})
        self.assertEqual(rc, 2, out)
        self.assertIn("; `define M in the Verilog-A source (§2.3)", out)
        self.assertNotIn("`define M  in", out)
        self.use(flowlib.never_called)
        rc, out, _ = self.spectre(["-va,define", "M=1", "-va,define", "M=1", "va.scs"])
        self.assertEqual(rc, 2, out)
        self.assertEqual(out.count("-va,define M=1: openvaf-r leaves a macro"), 1, out)


# -- -outdir and the state files --------------------------------------------------------------------------------------

class TestOutdirStateFiles(flowlib.FlowCase):

    def test_a_state_file_in_outdir_when_the_results_go_elsewhere(self):
        self.deck(flowlib.TWO.replace("op1 dc\n", "op1 dc write=\"op1.ic\"\n"))
        self.use(flowlib.synth_runner())
        rc, out, _ = self.spectre(["-outdir", "o", "-raw", "r", "rc.scs"])
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "o", "op1.ic")))
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "r")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "o", "r")))


# -- a source alter after the last analysis ---------------------------------------------------------------------------

LATE = """// a pulse source altered after the last analysis (%s)
simulator lang=spectre
parameters other=1.0 p0=0.0
V1 (in 0) vsource type=pulse val0=p0 val1=1.0 delay=5e-07 rise=5e-07 fall=5e-07 width=1e-06 period=2e-05
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
%s
%s
"""
SWEPT_TRAN = "sw1 sweep %s values=[%s] {\n  tr1 tran stop=3.5u maxstep=2n\n}"
# (the analyses, the alters after them, the field and rule the warning names): S-2E4's X-2c8 R0057 (right on Xyce at
# 5d2291f, refused since round 1) and R0024 (both engines), S-2E's p6, and the period form 5d2291f refused at the alter
LATE_CASES = {
    "r0057": (SWEPT_TRAN % ("dev=V1 param=val1", "1.0 -0.5"), "a1 alter dev=V1 param=fall value=-1e-07",
              "fall=-1e-07 s: a negative fall is an error (§3.8.1)"),
    "r0024": (SWEPT_TRAN % ("param=p0", "0.0 0.2"), "a1 alter dev=V1 param=width value=-1e-06\na2 alter param=p0 value=0.2",
              "width=-1e-06 s: a negative width is an error in v1 (Spectre's reading is not known)"),
    "p6": ("tr1 tran stop=3.5u maxstep=2n", "a1 alter dev=V1 param=fall value=-0.1u",
           "fall=-1e-07 s: a negative fall is an error (§3.8.1)"),
    "period": ("tr1 tran stop=3.5u maxstep=2n", "a1 alter dev=V1 param=period value=1u",
               "period=1e-06 s, not longer than rise+fall+width = 2e-06 s: an explicit period no longer than "
               "rise+fall+width is an error (§3.8.1)"),
}
LATE_TAIL = " where an analysis runs, and no analysis runs in this state: the run goes on"


class TestAlterAfterTheLastAnalysis(TempDir):

    def test_the_plan_warns_and_does_not_refuse(self):
        from vamos.netlist import plan as PL
        from vamos.notes import ERROR, WARNING, NoteError
        from vamos.spectre.job import Settings
        for k, (analyses, alters, rule) in LATE_CASES.items():
            for engine in ("vacask", "xyce"):
                with self.subTest(case=k, engine=engine):
                    plan = PL.build(parse_deck(LATE % (k, analyses, alters)), engine, Settings())   # no NoteError
                    warned = [n.message for n in plan.notes if n.severity == WARNING and "after alter a1" in n.message]
                    self.assertEqual(warned, ["the netlist (after alter a1): the pulse of V1 has %s%s" % (rule, LATE_TAIL)])
                    self.assertEqual([n.message for n in plan.notes if n.severity == ERROR], [])
        # the netlist's own constant stays refused (as at 5d2291f), and so does the alter where an analysis runs
        analyses, alters, _rule = LATE_CASES["p6"]
        for k, text in (("own", (LATE % ("own", analyses, "")).replace("fall=5e-07", "fall=-1e-07")),
                        ("before_an_analysis", LATE % ("before", analyses, alters + "\ntr2 tran stop=3.5u"))):
            for engine in ("vacask", "xyce"):
                with self.subTest(case=k, engine=engine):
                    with self.assertRaises(NoteError) as cm:
                        PL.build(parse_deck(text), engine, Settings())
                    self.assertIn("fall=-1e-07 s: a negative fall is an error (§3.8.1", " ".join(n.message for n in cm.exception.notes))

    def check(self, engine):
        for k in ("r0057", "r0024", "p6", "period"):
            analyses, alters, rule = LATE_CASES[k]
            with self.subTest(engine=engine, case=k):
                d, r = spectre(self, "%s_%s" % (engine, k), LATE % (k, analyses, alters), engine)
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                self.assertIn("Warning from spectre during", r.stdout)
                self.assertIn("n.scs:4: the netlist (after alter a1): the pulse of V1 has %s%s\n" % (rule, LATE_TAIL),
                              r.stdout)
                self.assertIn("spectre completes with 0 errors, 1 warnings, and 0 notices", r.stdout)
                raw = os.path.join(d, "n.raw")
                if k in ("r0057", "r0024"):
                    hi = [1.0, -0.5] if k == "r0057" else [1.0, 1.0]
                    for i in (0, 1):
                        tran = os.path.join(raw, "sw1-%03d_tr1.tran.tran" % i)
                        self.assertAlmostEqual(value(tran, "in", 1.5e-6), hi[i], 6)         # the pulse's high level
                else:
                    self.assertAlmostEqual(value(os.path.join(raw, "tr1.tran.tran"), "in", 1.5e-6), 1.0, 6)

    @needs_vacask
    def test_vacask(self):
        self.check("vacask")

    @needs_xyce
    def test_xyce(self):
        self.check("xyce")


# -- q.62: the results files are UTF-8 -------------------------------------------------------------------------------

DC_HEADER = {"tolerance.relative": 1e-3, "reltol": 1e-3, "abstol(V)": 1e-6, "abstol(I)": 1e-12, "temp": 27.0,
             "tnom": 27.0, "tempeffects": "all", "gmindc": 1e-12}
UNI_TITLE = "// 10 kΩ divider — §3, 日本"
UNI_NAMES = ["nΩ", "Ж", "_n_316_251", "日本", "né", "X1.nΩ"]
UNI_DECK = """%s
simulator lang=spectre
V1 (a 0) vsource dc=1
R1 (a n\\Ω) resistor r=1k
R2 (n\\Ω n\\é) resistor r=1k
R3 (n\\é 0) resistor r=1k
op1 dc write="u.ns"
op2 dc readns="hand.ns"
"""
# a state file written by hand in Latin-1 (its bytes are not UTF-8)
HAND_LATIN1 = "# by hand, in a Latin-1 editor\nné 0.3\na 1\n".encode("latin-1")


def op_result(names, key="op1-dc", file="op1.dc"):
    from vamos.spectre.results import AnalysisResult, Signal
    return AnalysisResult(key, "dc", file, description="DC Analysis `op1'", header=dict(DC_HEADER), data_type="scalar",
                          signals=[Signal(n, "V", "V", [float(k + 1)]) for k, n in enumerate(names)])


class TestResultsEncoding(TempDir):

    def test_the_names_mapping_escapes_only_what_a_format_cannot_hold(self):
        from vamos.names import FILE_ENCODING, holds_file_text, written_name, written_note, written_unname
        self.assertEqual(FILE_ENCODING, "utf-8")
        for name in UNI_NAMES + ["VΩ:p", "b.c", "_b_056c", "a b", "..k", "a/b"]:
            with self.subTest(name=name):
                self.assertEqual(written_name(name), name)                        # a PSF trace, a state file: as it is
                self.assertEqual(written_unname(name), name)
        for use, name, want in (("nutmeg", "a b", "_a_040b"), ("nutmeg", "a,b", "_a_054b"), ("nutmeg", "_a_040b", "__137a_137040b"),
                                ("nutmeg", "nΩ b", "_n_316_251_040b"), ("nutmeg", "nΩ", "nΩ"), ("file", "a/b", "_a_057b"),
                                ("file", "aΩ/b", "_a_316_251_057b"), ("file", "nΩ", "nΩ"), ("file", "s-w", "s-w")):
            with self.subTest(use=use, name=name):
                self.assertEqual(written_name(name, use), want)
                self.assertEqual(written_unname(want, use), name)
        self.assertIn("ngspice ends a variable name at a blank or a comma", written_note("signal", "a b", "_a_040b", "nutmeg"))
        self.assertIn("a / cannot be part of the name of its results file", written_note("analysis", "a/b", "_a_057b", "file"))
        self.assertTrue(holds_file_text(UNI_TITLE))
        self.assertTrue(holds_file_text("x\udcffy.scs"))                        # a file name's byte that is not UTF-8
        self.assertFalse(holds_file_text("x\ud800y"))                           # any other lone surrogate

    def test_psf_files_hold_every_name_and_title_as_written(self):
        from vamos.output import psf
        head = psf.PsfHead(version="0.1.0", date="1:02:03 AM, Mon Oct 19, 2026", design=UNI_TITLE)
        notes = []
        psf.write_analysis(self.tmp, op_result(UNI_NAMES), head, notes=notes)
        psf.write_logfile(self.tmp, [op_result(UNI_NAMES)], head, stamp="Mon Oct 19 01:02:03 2026")
        with open(os.path.join(self.tmp, "op1.dc"), "rb") as fh:
            data = fh.read()
        self.assertIn(('"design" "%s"\n' % UNI_TITLE).encode("utf-8"), data)
        self.assertIn('"nΩ" "V" 1.0'.encode("utf-8"), data)
        self.assertEqual(notes, [])                                             # no name is written otherwise
        p = psfcheck.check(os.path.join(self.tmp, "op1.dc"))
        self.assertEqual(p.names(), UNI_NAMES)
        self.assertEqual([p.value(n) for n in UNI_NAMES], [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        self.assertEqual(p.header["design"], UNI_TITLE)
        log = psfcheck.check_logfile(os.path.join(self.tmp, "logFile"))
        self.assertEqual(log.keys(), ["op1-dc"])
        self.assertEqual(log.header["design"], UNI_TITLE)
        # a title with a lone surrogate os.fsdecode cannot have made: `?' (the flow warns of it); one it made: its byte
        psf.write_analysis(os.path.join(self.tmp, "s"), op_result(["out"]),
                           psf.PsfHead(version="0.1.0", date=head.date, design="// a\ud800b \udcffc"))
        with open(os.path.join(self.tmp, "s", "op1.dc"), "rb") as fh:
            self.assertIn(b'"design" "// a?b \xffc"\n', fh.read())

    @needs_psf_parser
    def test_the_vendored_psf_parser_reads_the_files(self):
        from vamos.output import psf
        head = psf.PsfHead(version="0.1.0", date="1:02:03 AM, Mon Oct 19, 2026", design=UNI_TITLE)
        psf.write_analysis(self.tmp, op_result(UNI_NAMES), head)
        parser = import_psf_parser()
        p = parser.PsfFile(os.path.join(self.tmp, "op1.dc"))
        self.assertEqual(p.header.get("design"), UNI_TITLE)
        self.assertEqual([x.name for x in p.traces + p.values], UNI_NAMES)      # an operating point: values

    def test_a_state_file_round_trips_and_a_hand_written_latin1_one_reads(self):
        from vamos.output import statefile
        p = os.path.join(self.tmp, "w.ns")
        nodes = {"nΩ": 1.0, "nЖ": 0.5, "_n_316_251": 0.25, "X1.Ω": 0.125, "日本": 2.0, "n é": 0.75}
        notes = []
        statefile.write(p, nodes, {"VΩ:p": 1e-3}, "Ωdiv.scs", "opΩ", "1:02:03 AM, oct 19, 2026", notes=notes)
        with open(p, "rb") as fh:
            text = fh.read().decode("utf-8")                                  # valid UTF-8
        self.assertIn("# State file generated by Spectre from circuit file 'Ωdiv.scs'\n# during 'opΩ' at", text)
        for line in ("nΩ 1", "nЖ 0.5", "_n_316_251 0.25", "X1.Ω 0.125", "日本 2", "n\\ é 0.75", "VΩ:p 0.001"):
            self.assertIn("\n" + line + "\n", text, line)
        self.assertEqual(statefile.read(p), (nodes, {"VΩ:p": 1e-3}))
        self.assertEqual(notes, [])
        # a file written by hand in Latin-1 reads as the names it spells; one written by hand in UTF-8 too (round 4 read
        # it as Latin-1: nÃ©)
        hand = os.path.join(self.tmp, "hand.ns")
        with open(hand, "wb") as fh:
            fh.write(HAND_LATIN1)
        self.assertEqual(statefile.read(hand), ({"né": 0.3, "a": 1.0}, {}))
        with open(hand, "wb") as fh:
            fh.write("né 0.5\nnΩ 0.25\nVΩ:p 1e-3\n".encode("utf-8"))
        self.assertEqual(statefile.read(hand), ({"né": 0.5, "nΩ": 0.25}, {"VΩ:p": 1e-3}))
        # a netlist's file name that is not UTF-8 (os.fsdecode's lone surrogate) is written as its byte
        statefile.write(p, {"a": 1.0}, {}, "x\udcffy.scs", "op1", "1:02:03 AM, oct 19, 2026")
        with open(p, "rb") as fh:
            self.assertIn(b"circuit file 'x\xffy.scs'\n", fh.read())
        self.assertEqual(statefile.read(p), ({"a": 1.0}, {}))

    def test_nutmeg_names(self):
        from vamos.netlist import rawfile
        from vamos.output import nutmeg
        notes = []
        p = os.path.join(self.tmp, "w.nut")
        nutmeg.write(p, [op_result(["a b", "nΩ", "c,d", "in", "日本"])], False, notes=notes, title=UNI_TITLE)
        with open(p, "rb") as fh:
            text = fh.read().decode("utf-8")
        self.assertIn("Title: %s\n" % UNI_TITLE, text)
        self.assertIn("\t0\t_a_040b\tvoltage\n\t1\tnΩ\tvoltage\n\t2\t_c_054d\tvoltage\n\t3\tin\tvoltage\n\t4\t日本\tvoltage\n",
                      text)
        self.assertEqual(sorted(n.message[:28] for n in notes), ["signal a b is written as _a_", "signal c,d is written as _c_"])
        raw = rawfile.read_all(p)[0]
        self.assertEqual([v[0] for v in raw.variables], ["_a_040b", "nΩ", "_c_054d", "in", "日本"])
        self.assertEqual(raw.title, UNI_TITLE)

    def hand_written(self, sub):
        """The run directory tmp/sub with a Latin-1 state file written by hand in it (bytes, not spectre()'s UTF-8)."""
        d = os.path.join(self.tmp, sub)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "hand.ns"), "wb") as fh:
            fh.write(HAND_LATIN1)
        return d

    def check(self, engine):
        self.hand_written(engine)
        d, r = spectre(self, engine, UNI_DECK % UNI_TITLE, engine)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertIn("spectre completes with 0 errors, 0 warnings, and 0 notices", r.stdout)   # no name or title notice
        self.assertNotIn("is written as", r.stdout)
        raw = os.path.join(d, "n.raw")
        p = psfcheck.check(os.path.join(raw, "op1.dc"))
        self.assertEqual(sorted(p.names()), ["V1:p", "a", "né", "nΩ"])
        self.assertAlmostEqual(p.value("nΩ"), 2.0 / 3, 9)
        self.assertEqual(p.header["design"], UNI_TITLE)
        self.assertEqual(psfcheck.check_logfile(os.path.join(raw, "logFile")).header["design"], UNI_TITLE)
        with open(os.path.join(d, "u.ns"), "rb") as fh:
            text = fh.read().decode("utf-8")
        self.assertIn("\nnΩ 0.666666666666667\n", text)
        self.assertIn("\nné 0.333333333333333\n", text)
        self.assertEqual(sorted(psfcheck.check(os.path.join(raw, "op2.dc")).names()), ["V1:p", "a", "né", "nΩ"])
        if have_psf_parser():
            parser = import_psf_parser()
            pf = parser.PsfFile(os.path.join(raw, "op1.dc"))
            self.assertEqual(pf.header.get("design"), UNI_TITLE)
            self.assertEqual(sorted(x.name for x in pf.traces + pf.values), ["V1:p", "a", "né", "nΩ"])
        # the nutmeg file: the same names, and ngspice loads it (its `display' lists the vectors)
        d2 = self.hand_written(engine + "_nut")
        d2, r = spectre(self, engine + "_nut", UNI_DECK % UNI_TITLE, engine, args=["-format", "nutascii", "-raw", "n.nut"])
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        from vamos.netlist import rawfile
        plots = rawfile.read_all(os.path.join(d2, "n.nut"))
        self.assertEqual(sorted(v[0] for v in plots[0].variables), ["V1:p", "a", "né", "nΩ"])
        self.assertEqual(plots[0].title, UNI_TITLE)
        if ngspice_bin():
            with open(os.path.join(d2, "rd.sp"), "w", encoding="utf-8") as fh:
                fh.write("* read\n.control\nload n.nut\ndisplay\nquit\n.endc\n.end\n")
            ng = run([ngspice_bin(), "-b", "rd.sp"], cwd=d2, timeout=120)
            out = (ng.stdout or "") + (ng.stderr or "")
            for name in ("nΩ", "né", "V1:p"):
                self.assertIn(name, out, out[-3000:])

    @needs_vacask
    def test_vacask(self):
        self.check("vacask")

    @needs_xyce
    def test_xyce(self):
        self.check("xyce")


if __name__ == "__main__":
    unittest.main()
