"""The fixes of phase 2e's last guide trial and its skeptic (FIX2E4: TRIAL2EG's findings, S-2E's regressions and its
silent values).  Standard library only, Python 3.9; the engine runs on WSL.

TestXyceNames        the letter Xyce's elements start with never makes two elements one name (S-2E H3, TRIAL2EG-F02:
                     R<1> beside a plain r_R_0741_076, sub1 beside xsub1, a beside ra; Xyce stopped with `Duplicate
                     device'), and the names Xyce gives back map to one netlist name each
TestWrittenNames     two names the results files could not hold (S-2E D4, TRIAL2EG-F01: Latin-1 wrote them as two
                     traces `?') are two traces, each under its own name: the files are UTF-8 since FIX2E5 (§14 q.62),
                     where round 4 wrote them escaped; a nutmeg variable that holds a blank or a comma, which end a name
                     for ngspice, is escaped, uniquely and reversibly (TRIAL2EG-F08); a title is written as it is
                     (round 4: `?', with a warning)
TestPathSplits       a path is split by the netlist, backtracking over the instance names it can hold (S-2E K1-K6:
                     instances X and X\\.1, and one named ..k)
TestUndefinedMacro   a Verilog-A macro nothing defines is refused on Xyce: by PyMS 1.1.7 itself, with a file:line
                     error (S-2E H5: an older PyMS compiled it silently; FIX2E7P)
TestAnalysisNames    an analysis's and a sweep's name is the name its escapes spell (TRIAL2EG-F05); a / in one is
                     escaped in its file's name
TestBannerErrors     a bad VAMOS_BANNER is the error a bad --vamos-banner is (TRIAL2EG-F03), and a profile that is not
                     JSON says so (TRIAL2EG-F14)
TestEngineEnv        the engine runs get the scrubbed PATH and the lock-out, as the probes do (TRIAL2EG-F04)
TestStatusFlags      a SIGUSR1 the flow took before the engine's start is the runner's to answer (TRIAL2EG-F07)
TestNameGaps         ends s\\ t, nodeset vdd!=0.4, an escaped * (a character, not a wildcard), an escaped : in an
                     instance name in save (S-2E's loud gaps)
TestPsfcheckNames    psfcheck reads a sweep child whose name holds a _ (an escaped name always does)"""

import os
import random
import shutil
import signal
import tempfile
import types
import unittest

from vamos_testlib import LAUNCHER, LINUX, TempDir, needs_vacask, needs_xyce, run  # noqa: E402

import psfcheck  # noqa: E402  (tests/vamos: the strict reader)

# the header table a dc data file carries (psfcheck's rule; results.collect fills it from the step)
DC_HEADER = {"tolerance.relative": 1e-3, "reltol": 1e-3, "abstol(V)": 1e-6, "abstol(I)": 1e-12, "temp": 27.0,
             "tnom": 27.0, "tempeffects": "all", "gmindc": 1e-12}


def parse_deck(text, files=None):
    from vamos.netlist import spectre
    d = tempfile.mkdtemp(prefix="vamos_2eg_")
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


def element_names(deck):
    """The element names of each body of a Xyce deck: {body: [first token of each element line]}."""
    out = {"": []}
    body = ""
    for line in deck.splitlines()[2:]:
        if line.startswith(".subckt "):
            body = line.split()[1]
            out[body] = []
        elif line.startswith(".ends"):
            body = ""
        elif line and not line.startswith((".", "*", "+")):
            out[body].append(line.split()[0])
    return out


def spectre(test, sub, text, engine, args=(), env=None, files=None, name="n.scs"):
    """bin/vamos -spectre on one engine in tmp/sub: (the directory, the CompletedProcess)."""
    d = os.path.join(test.tmp, sub)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        fh.write(text)
    for fn, body in (files or {}).items():
        with open(os.path.join(d, fn), "w", encoding="utf-8") as fh:
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


# -- 1: Xyce's letter ------------------------------------------------------------------------------------------------

COLLIDE = {
    "c": r"""// R<1> prints r_R_0741_076 on Xyce, as a resistor of that plain name does
simulator lang=spectre
V1 (a 0) vsource dc=1
R\<1\> (a b) resistor r=1k
r_R_0741_076 (b 0) resistor r=3k
op1 dc
save b R\<1\>:1 r_R_0741_076:1
""",
    "v": r"""// V<1> prints v_V_0741_076, as a source of that plain name does
simulator lang=spectre
V\<1\> (a 0) vsource dc=1
v_V_0741_076 (b 0) vsource dc=2
R1 (a b) resistor r=1k
op1 dc
""",
    "x": """// two X instances sub1 and xsub1
simulator lang=spectre
subckt one (p q)
R1 (p q) resistor r=1k
ends one
V1 (in 0) vsource dc=1
sub1 (in a) one
xsub1 (a 0) one
op1 dc
save a sub1.R1:1 xsub1.R1:1
""",
    "a": """// a resistor a beside a resistor ra, altered and swept
simulator lang=spectre
V1 (in 0) vsource dc=1
a (in m) resistor r=1k
ra (m 0) resistor r=3k
op1 dc
a1 alter dev=a param=r value=3k
op2 dc
d1 dc dev=a param=r values=[1k 3k]
save m a:1 ra:1
""",
    "d": r"""// R.9 beside a plain r_R_0569
simulator lang=spectre
V1 (in 0) vsource dc=1
R\.9 (in a) resistor r=1k
r_R_0569 (a b) resistor r=2k
R3 (b 0) resistor r=1k
save R\.9:1 r_R_0569:1 a b
op1 dc
""",
    "n": """// inside a subckt: load beside Rload
simulator lang=spectre
V1 (in 0) vsource dc=1
X1 (in 0) cell
subckt cell (p q)
  load (p m) resistor r=1k
  Rload (m q) resistor r=1k
ends cell
op1 dc
save X1.m X1.load:1 X1.Rload:1
""",
}


class TestXyceNames(unittest.TestCase):
    """vamos adds the letter Xyce wants to an element name that lacks it; where that is the name an element whose own
    name starts with the letter prints, the one vamos added the letter to prints its letter, a _ and its escaped name
    (xyce.element_renames)."""

    def test_an_added_letter_never_makes_another_element_s_name(self):
        want = {"c": "r__R_0741_076", "v": "v__V_0741_076", "x": "x_sub1", "a": "r_a", "d": "r__R_0569",
                "n": "r_load"}
        for key, deck in COLLIDE.items():
            with self.subTest(deck=key):
                decks, _names = xyce_decks(deck)
                for text in decks:
                    for body, names in element_names(text).items():
                        low = [n.lower() for n in names]
                        self.assertEqual(len(low), len(set(low)), (key, body, names))
                    self.assertIn("\n%s " % want[key], text)

    def test_the_columns_and_targets_name_the_renamed_elements(self):
        decks, names = xyce_decks(COLLIDE["a"])
        text = "\n".join(decks)
        self.assertIn(".DC R_A:R LIST 1000.0 3000.0", text)
        self.assertEqual(names["vamos_a1"][("i", "a")], "I(R_A)")
        self.assertEqual(names["vamos_a1"][("i", "ra")], "I(RA)")
        decks, names = xyce_decks(COLLIDE["x"])
        self.assertEqual(names["vamos_a1"][("i", "sub1.R1")], "I(X_SUB1:R1)")
        self.assertEqual(names["vamos_a1"][("i", "xsub1.R1")], "I(XSUB1:R1)")

    def test_names_that_differ_only_in_case_are_still_refused(self):
        from vamos.notes import NoteError
        deck = ("// t\nsimulator lang=spectre\nV1 (a 0) vsource dc=1\nload (a b) resistor r=1k\nLoad (b 0) resistor "
                "r=1k\nop1 dc\n")
        with self.assertRaises(NoteError) as cm:
            xyce_decks(deck)
        self.assertIn("elements load and Load differ only in case; Xyce would merge them", str(cm.exception))

    def test_xyce_s_names_map_back_to_one_netlist_name_each(self):
        from vamos.spectre import run_xyce
        nl = parse_deck(COLLIDE["c"])
        r = types.SimpleNamespace(nl=nl, _printed=None)
        self.assertEqual(run_xyce._Runner.unprint(r, "device R__R_0741_076 and device R_R_0741_076"),
                         "device R<1> and device R_R_0741_076")
        nl = parse_deck(COLLIDE["x"])
        r = types.SimpleNamespace(nl=nl, _printed=None)
        self.assertEqual(run_xyce._Runner.unprint(r, "node X_SUB1:R1 and XSUB1:R1"), "node sub1:R1 and XSUB1:R1")

    def check_runs(self, engine):
        tmp = self
        d, r = spectre(tmp, "c_" + engine, COLLIDE["c"], engine)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        raw = os.path.join(d, "n.raw", "op1.dc")
        self.assertAlmostEqual(value(raw, "b"), 0.75, 9)
        self.assertAlmostEqual(value(raw, "R<1>:1"), 2.5e-4, 12)
        self.assertAlmostEqual(value(raw, "r_R_0741_076:1"), 2.5e-4, 12)
        d, r = spectre(tmp, "v_" + engine, COLLIDE["v"], engine)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        raw = os.path.join(d, "n.raw", "op1.dc")
        self.assertAlmostEqual(value(raw, "V<1>:p"), 1e-3, 12)
        self.assertAlmostEqual(value(raw, "v_V_0741_076:p"), -1e-3, 12)
        d, r = spectre(tmp, "x_" + engine, COLLIDE["x"], engine)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        raw = os.path.join(d, "n.raw", "op1.dc")
        self.assertAlmostEqual(value(raw, "a"), 0.5, 9)
        self.assertAlmostEqual(value(raw, "sub1.R1:1"), 5e-4, 12)
        self.assertAlmostEqual(value(raw, "xsub1.R1:1"), 5e-4, 12)
        d, r = spectre(tmp, "a_" + engine, COLLIDE["a"], engine)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        self.assertAlmostEqual(value(os.path.join(d, "n.raw", "op1.dc"), "m"), 0.75, 9)
        self.assertAlmostEqual(value(os.path.join(d, "n.raw", "op2.dc"), "m"), 0.5, 9)
        got = value(os.path.join(d, "n.raw", "d1.dc"), "m")
        self.assertTrue(abs(got[0] - 0.75) < 1e-9 and abs(got[1] - 0.5) < 1e-9, got)
        d, r = spectre(tmp, "n_" + engine, COLLIDE["n"], engine)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        self.assertAlmostEqual(value(os.path.join(d, "n.raw", "op1.dc"), "X1.load:1"), 5e-4, 12)

    @needs_xyce
    def test_xyce_runs_them(self):
        self.tmp = tempfile.mkdtemp(prefix="vamos-2eg-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.check_runs("xyce")

    @needs_vacask
    def test_vacask_runs_them(self):
        self.tmp = tempfile.mkdtemp(prefix="vamos-2eg-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.check_runs("vacask")


# -- 2: the names the results files could not hold (Latin-1 in round 4; UTF-8 since FIX2E5) -------------------------

D4 = "// two names Latin-1 cannot hold\nsimulator lang=spectre\nV1 (\\Ω 0) vsource dc=1\nR1 (\\Ω \\Ж) " \
     "resistor r=1k\nR2 (\\Ж 0) resistor r=1k\nop1 dc write=\"w.ns\"\n"
UNI = ("// non-ASCII node names, escaped\nsimulator lang=spectre\nV1 (a 0) vsource dc=1\nR1 (a n\\Ω) resistor r=1k\n"
       "R2 (n\\Ω n\\Ж) resistor r=1k\nR3 (n\\Ж 0) resistor r=1k\nop1 dc write=\"%s\"\n")
UNI_RD = ("// read the state back\nsimulator lang=spectre\nV1 (a 0) vsource dc=0\nR1 (a n\\Ω) resistor r=1k\n"
          "C1 (n\\Ω 0) capacitor c=1n\nR2 (n\\Ω n\\Ж) resistor r=1k\nC2 (n\\Ж 0) capacitor c=1n\n"
          "R3 (n\\Ж 0) resistor r=1k\ntr1 tran stop=1n readic=\"%s\"\n")


class TestWrittenNames(TempDir):
    """FIX2E5 re-decided round 4's pins on purpose (§14 q.62, 2026-10-23: the files are UTF-8): a name Latin-1 could not
    hold is written as it is, not escaped, and a title keeps its characters, with no warning; the nutmeg and file-name
    escapes stay, since those formats cannot hold the characters."""

    def test_the_mapping(self):
        from vamos.names import escape_name, holds_file_text, written_name, written_unname
        for name in ("nΩ", "X1.nΩ", "VΩ:p", "_n_316_251", "b.c", "_b_056c", "né", "a b", "..k", "a/b"):
            with self.subTest(name=name):
                self.assertEqual(written_name(name), name)                  # a PSF trace or a state-file entry: as it is
                self.assertEqual(written_unname(name), name)
        self.assertEqual(written_name("a b", "nutmeg"), "_a_040b")
        self.assertEqual(written_name("a,b", "nutmeg"), "_a_054b")
        self.assertEqual(written_name("_a_040b", "nutmeg"), "__137a_137040b")
        self.assertEqual(written_name("nΩ", "nutmeg"), "nΩ")
        self.assertEqual(written_name("a/b", "file"), "_a_057b")
        self.assertEqual(written_name("nΩ", "file"), "nΩ")
        rnd = random.Random(2026)
        alphabet = ["a", "Z", "0", "_", ".", ":", "Ω", "é", "Ж", " ", ",", "/", "5", "3", "1", "2"]
        for use in ("psf", "nutmeg", "file"):
            seen = {}
            for _ in range(20000):
                n = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 6)))
                if rnd.random() < 0.3:
                    n = escape_name(n)
                w = written_name(n, use)
                self.assertTrue(holds_file_text(w), (use, n, w))
                if use == "nutmeg":
                    self.assertFalse(" " in w or "," in w, (n, w))
                elif use == "file":
                    self.assertNotIn("/", w, (n, w))
                self.assertEqual(written_unname(w, use), n, (use, w))
                self.assertEqual(seen.setdefault(w, n), n, "%r and %r are both written %r (%s)" % (seen[w], n, w, use))

    def op_result(self, names):
        from vamos.spectre.results import AnalysisResult, Signal
        return AnalysisResult("op1-dc", "dc", "op1.dc", description="DC Analysis `op1'", header=dict(DC_HEADER),
                              signals=[Signal(n, "V", "V", [float(k + 1)]) for k, n in enumerate(names)])

    def test_two_names_latin1_cannot_hold_are_two_traces(self):
        from vamos.output import psf
        notes = []
        names = ["Ω", "Ж", "_n_316_251", "nΩ"]
        psf.write_analysis(self.tmp, self.op_result(names), psf.PsfHead(version="0.1.0", date="1:02:03 AM, Mon Oct 19, "
                                                                          "2026", design="// t"), notes=notes)
        p = psfcheck.check(os.path.join(self.tmp, "op1.dc"))
        self.assertEqual(p.names(), names)                                  # each under its own name (UTF-8)
        self.assertEqual([p.value(n) for n in p.names()], [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(notes, [])

    def test_a_state_file_round_trips(self):
        from vamos.output import statefile
        p = os.path.join(self.tmp, "w.ns")
        nodes = {"nΩ": 1.0, "nЖ": 0.5, "_n_316_251": 0.25, "X1.Ω": 0.125}
        notes = []
        statefile.write(p, nodes, {"VΩ:p": 1e-3}, "w.scs", "opΩ", "1:02:03 AM, oct 19, 2026", notes=notes)
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        for line in ("nΩ 1", "nЖ 0.5", "_n_316_251 0.25", "X1.Ω 0.125", "VΩ:p 0.001", "# during 'opΩ' at"):
            self.assertIn(line, text)
        self.assertEqual(statefile.read(p), (nodes, {"VΩ:p": 1e-3}))
        self.assertEqual(notes, [])

    def test_nutmeg_names_ngspice_reads(self):
        from vamos.output import nutmeg
        notes = []
        p = os.path.join(self.tmp, "w.nut")
        nutmeg.write(p, [self.op_result(["a b", "nΩ", "c,d", "in"])], False, notes=notes, title="// t")
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("\t0\t_a_040b\tvoltage\n\t1\tnΩ\tvoltage\n\t2\t_c_054d\tvoltage\n\t3\tin\tvoltage\n", text)
        self.assertTrue(any("ngspice ends a variable name at a blank or a comma" in n.message for n in notes), notes)
        self.assertFalse(any("nΩ" in n.message for n in notes), notes)

    def check_d4(self, engine):
        d, r = spectre(self, "d4_" + engine, D4, engine)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        self.assertNotIn("is written as", r.stdout)
        p = psfcheck.check(os.path.join(d, "n.raw", "op1.dc"))
        self.assertAlmostEqual(p.value("Ω"), 1.0, 9)
        self.assertAlmostEqual(p.value("Ж"), 0.5, 9)
        psfcheck.check_logfile(os.path.join(d, "n.raw", "logFile"))
        from vamos.output import statefile
        nodes, _cur = statefile.read(os.path.join(d, "w.ns"))
        self.assertEqual(sorted(nodes), ["Ω", "Ж"])
        # a state file of such names read back: the tran starts from the values it holds
        ns = os.path.join(self.tmp, "u_%s.ns" % engine)
        _d, r = spectre(self, "u_" + engine, UNI % ns, engine)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        d2, r = spectre(self, "rd_" + engine, UNI_RD % ns, engine)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        tr = os.path.join(d2, "n.raw", "tr1.tran.tran")
        self.assertAlmostEqual(value(tr, "nΩ", 0.0), 2.0 / 3, 4)
        self.assertAlmostEqual(value(tr, "nЖ", 0.0), 1.0 / 3, 4)
        # a title Latin-1 could not hold is written as it is, with no warning (round 4: `?', with one)
        d4, r = spectre(self, "t_" + engine, D4.replace("// two names Latin-1 cannot hold", "// 10 kΩ divider"),
                        engine)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        self.assertNotIn("Warning from", r.stdout)
        self.assertEqual(psfcheck.check(os.path.join(d4, "n.raw", "op1.dc")).header["design"], "// 10 kΩ divider")

    @needs_vacask
    def test_vacask(self):
        self.check_d4("vacask")

    @needs_xyce
    def test_xyce(self):
        self.check_d4("xyce")


# -- 3: path splits --------------------------------------------------------------------------------------------------

TWO = r"""// two subckt instances X and X.1 (X's subckt has no instance 1)
simulator lang=spectre
V1 (in 0) vsource dc=%s
X (in 0) half
X\.1 (in 0) half3
subckt half (p q)
  RA (p m) resistor r=1k
  RB (m q) resistor r=1k
ends half
subckt half3 (p q)
  RA (p m) resistor r=1k
  RB (m q) resistor r=3k%s
ends half3
"""
DOTS = r"""// an instance named ..k
simulator lang=spectre
V1 (in 0) vsource dc=1
\.\.k (in 0) half
subckt half (p q)
  RA (p m) resistor r=1k
  RB (m q) resistor r=1k
ends half
op1 dc
a1 alter dev=\.\.k.RB param=r value=3k
op2 dc
d1 dc dev=\.\.k.RB param=r values=[1k 3k]
save \.\.k.m \.\.k.RA:1
"""


class TestPathSplits(TempDir):

    def test_the_split_backtracks(self):
        from vamos.netlist import tables as T
        nl = parse_deck(TWO % ("1", "") + "op1 dc\n")
        for path, want in (("X.1.m", ["X.1", "m"]), ("X.m", ["X", "m"]), ("X.1.RB", ["X.1", "RB"]),
                           ("X.RA", ["X", "RA"]), ("X.1", ["X.1"]), ("X.9.q", ["X", "9.q"])):
            with self.subTest(path=path):
                self.assertEqual(T.path_parts(nl, path), want)
        nl = parse_deck(DOTS)
        self.assertEqual(T.path_parts(nl, "..k.m"), ["..k", "m"])
        self.assertEqual(T.path_parts(nl, "..k.RB"), ["..k", "RB"])

    def check(self, engine):
        runs = {
            "k1": TWO % ("1", "") + "op1 dc\nsave X.m X\\.1.m X\\.1.RA:1 X.RA:1\n",
            "k2": TWO % ("1", "") + "op1 dc\na1 alter dev=X\\.1.RB param=r value=1k\nop2 dc\nsave X.m X\\.1.m\n",
            "k3": TWO % ("1", "") + "dc1 dc dev=X\\.1.RB param=r values=[1k 3k]\nsave X.m X\\.1.m\n",
            "k4": TWO % ("0", "\n  C1 (m q) capacitor c=1n")
            + "tr1 tran stop=1u maxstep=10n\nsave X.m X\\.1.m\nic X\\.1.m=0.4\n",
            "k5": TWO % ("0 mag=1", "") + "nz1 (X\\.1.m 0) noise start=1k stop=10k dec=1 iprobe=V1\n",
            "k6": TWO % ("1", "") + 'op1 dc write="c.ns"\n',
            "dk": DOTS,
        }
        out = {}
        for key, text in runs.items():
            d, r = spectre(self, "%s_%s" % (key, engine), text, engine)
            self.assertEqual(r.returncode, 0, (key, r.stdout[-2000:]))
            out[key] = d
        raw = lambda k, f: os.path.join(out[k], "n.raw", f)  # noqa: E731
        self.assertAlmostEqual(value(raw("k1", "op1.dc"), "X.1.m"), 0.75, 9)
        self.assertAlmostEqual(value(raw("k1", "op1.dc"), "X.m"), 0.5, 9)
        self.assertAlmostEqual(value(raw("k1", "op1.dc"), "X.1.RA:1"), 2.5e-4, 12)
        self.assertAlmostEqual(value(raw("k2", "op2.dc"), "X.1.m"), 0.5, 9)
        self.assertEqual([round(x, 9) for x in value(raw("k3", "dc1.dc"), "X.1.m")], [0.5, 0.75])
        self.assertAlmostEqual(value(raw("k4", "tr1.tran.tran"), "X.1.m", 0.0), 0.4, 4)
        self.assertTrue(os.path.isfile(raw("k5", "nz1.noise")))
        with open(os.path.join(out["k6"], "c.ns"), encoding="latin-1") as fh:
            self.assertIn("\nX.1.m 0.75\n", fh.read())
        self.assertAlmostEqual(value(raw("dk", "op2.dc"), "..k.m"), 0.75, 9)
        self.assertEqual([round(x, 9) for x in value(raw("dk", "d1.dc"), "..k.m")], [0.5, 0.75])

    @needs_vacask
    def test_vacask(self):
        self.check("vacask")

    @needs_xyce
    def test_xyce(self):
        self.check("xyce")


# -- 6: an undefined Verilog-A macro on Xyce -------------------------------------------------------------------------

VRES3 = """`include "disciplines.vams"
module vres3(p, n);
  inout p, n;
  electrical p, n;
  parameter real r = `RV;
  analog I(p, n) <+ V(p, n) / r;
endmodule
"""
VA_DECK = """// a Verilog-A resistor
simulator lang=spectre
ahdl_include "%s"
V1 (a 0) vsource dc=1
X1 (a 0) %s
op1 dc
"""


class TestUndefinedMacro(TempDir):
    """FIX2E7P: PyMS 1.1.7 refuses an undefined macro itself, with a file:line error (round 4's scan, which refused it
    before PyMS ran, is gone); a defined one runs."""

    @needs_xyce
    def test_pyms_refuses_it(self):
        d, r = spectre(self, "x", VA_DECK % ("vres3.va", "vres3"), "xyce", files={"vres3.va": VRES3})
        self.assertEqual(r.returncode, 2, r.stdout[-2000:])
        self.assertIn("vres3.va:5: `RV is not defined: no `define RV precedes this use in the file or the files it "
                      "includes (an undefined macro is an error in Verilog-A)", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(d, "n.raw", "op1.dc")))
        d, r = spectre(self, "y", VA_DECK % ("vres3.va", "vres3"), "xyce",
                       files={"vres3.va": VRES3.replace("module", "`define RV 2000.0\nmodule", 1)})
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        self.assertAlmostEqual(value(os.path.join(d, "n.raw", "op1.dc"), "V1:p"), -5e-4, 9)


# -- F05: analysis names ---------------------------------------------------------------------------------------------

AN = r"""// analysis and sweep names with escapes
simulator lang=spectre
parameters rv=1k
V1 (in 0) vsource dc=1
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
my\.op dc
s\-w sweep param=rv values=[1k 2k] {
  o\ p dc
}
a\/b dc
"""


class TestAnalysisNames(TempDir):

    def test_the_names_are_unescaped(self):
        nl = parse_deck(AN)
        self.assertEqual([a.name for a in nl.analyses], ["my.op", "s-w", "a/b"])
        self.assertEqual([c.name for c in nl.analyses[1].children], ["o p"])

    @needs_vacask
    def test_vacask_writes_them(self):
        d, r = spectre(self, "v", AN, "vacask")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        self.assertIn("DC Analysis `my.op'", r.stdout)
        self.assertEqual(sorted(os.listdir(os.path.join(d, "n.raw"))),
                         ["_a_057b.dc", "logFile", "my.op.dc", "s-w-000_o p.dc", "s-w-001_o p.dc", "s-w_o p.sweep"])
        log = psfcheck.check_logfile(os.path.join(d, "n.raw", "logFile"))
        self.assertEqual(log.keys(), ["my.op-dc", "s-w_o p-sweep", "s-w-000_o p-dc", "s-w-001_o p-dc", "_a_057b-dc"])
        self.assertIn("analysis a/b is written as _a_057b: a / cannot be part of the name of its results file",
                      r.stdout)


# -- F03, F14: banner profiles ---------------------------------------------------------------------------------------

RC = ("// RC\nsimulator lang=spectre\nV1 (in 0) vsource dc=1\nR1 (in out) resistor r=1k\nR2 (out 0) resistor r=1k\n"
      "op1 dc\n")


class TestBannerErrors(TempDir):

    def spectre_run(self, args, env=None):
        with open(os.path.join(self.tmp, "rc.scs"), "w") as fh:
            fh.write(RC)
        e = {k: v for k, v in os.environ.items() if not k.endswith("_DEFAULTS") and k not in ("VAMOS_BANNER",)}
        e.update(env or {})
        return run([LAUNCHER, "-spectre"] + args, cwd=self.tmp, env=e, timeout=600)

    def test_a_bad_vamos_banner_is_an_error_with_the_last_line(self):
        for env, want in (({"VAMOS_BANNER": "nosuchprofile"}, "no banner profile named 'nosuchprofile'"),
                          ({"VAMOS_BANNER": "/nonexistent/x.json"},
                           "cannot read banner profile '/nonexistent/x.json'")):
            with self.subTest(env=env):
                r = self.spectre_run(["rc.scs"], env)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn("Error from spectre during command-line processing.\n    %s\n" % want, r.stdout)
                self.assertTrue(r.stdout.endswith("spectre terminated prematurely due to fatal error.\n"), r.stdout)
        r = self.spectre_run(["+log", "v.log", "rc.scs"], {"VAMOS_BANNER": "nosuchprofile"})
        with open(os.path.join(self.tmp, "v.log")) as fh:
            self.assertIn("no banner profile named 'nosuchprofile'", fh.read())

    def test_a_profile_that_is_not_json_says_so(self):
        os.makedirs(os.path.join(self.tmp, ".vamos", "banners"))
        with open(os.path.join(self.tmp, ".vamos", "banners", "broken.json"), "w") as fh:
            fh.write("{ not json")
        for args, env in ((["--vamos-banner=broken", "rc.scs"], None), (["rc.scs"], {"VAMOS_BANNER": "broken"})):
            with self.subTest(args=args):
                r = self.spectre_run(args, env)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertRegex(r.stdout, r"banner profile \S*\.vamos/banners/broken\.json is not valid JSON: "
                                           r"Expecting property name enclosed in double quotes: line 1 column 3")
                self.assertNotIn("no banner profile named", r.stdout)


# -- F04: the engine runs' environment -------------------------------------------------------------------------------

FAKE = """#!/bin/sh
echo "[$*] PATH=$PATH STACK=${VAMOS_STACK-unset}" >> %s
case "$1" in --version|-v|-V) echo "fake 1.0"; exit 0;; esac
exit 1
"""


@unittest.skipUnless(LINUX, "the fake engines are /bin/sh scripts (Linux/WSL)")
class TestEngineEnv(TempDir):

    def test_the_engine_runs_get_the_scrubbed_path(self):
        record = os.path.join(self.tmp, "calls.txt")
        bindir = os.path.join(self.tmp, "fake")
        links = os.path.join(self.tmp, "links")
        os.makedirs(bindir)
        os.makedirs(links)
        for tool in ("vacask", "Xyce", "openvaf"):
            p = os.path.join(bindir, tool)
            with open(p, "w") as fh:
                fh.write(FAKE % record)
            os.chmod(p, 0o755)
        os.symlink(LAUNCHER, os.path.join(links, "specsim"))
        shims = os.path.join(os.path.dirname(os.path.dirname(LAUNCHER)), "shims")
        with open(os.path.join(self.tmp, "rc.scs"), "w") as fh:
            fh.write(RC)
        env = {k: v for k, v in os.environ.items() if not k.endswith("_DEFAULTS") and k not in ("VAMOS_BANNER",)}
        env.update(PATH=os.pathsep.join([links, shims, env.get("PATH", "")]), VAMOS_REDIRECT=links,
                   VAMOS_SPECTRE_NAMES="specsim", VAMOS_VACASK=os.path.join(bindir, "vacask"),
                   VAMOS_XYCE=os.path.join(bindir, "Xyce"), VAMOS_OPENVAF=os.path.join(bindir, "openvaf"))
        for engine, deck in (("vacask", "vamos.sim"), ("xyce", "vamos_a1.cir")):
            with self.subTest(engine=engine):
                if os.path.exists(record):
                    os.remove(record)
                run(["specsim", "--vamos-analog=" + engine, "rc.scs"], cwd=self.tmp, env=env, timeout=600)
                with open(record) as fh:
                    calls = [ln for ln in fh.read().splitlines() if deck in ln]
                self.assertTrue(calls, engine)
                for ln in calls:
                    path = ln.split(" PATH=", 1)[1].split(" STACK=", 1)[0].split(os.pathsep)
                    self.assertNotIn(links, path, ln)
                    self.assertNotIn(shims, path, ln)
                    self.assertTrue(ln.endswith(" STACK=spectre"), ln)


# -- F07: the status flags -------------------------------------------------------------------------------------------

@unittest.skipUnless(hasattr(signal, "SIGUSR1"), "needs SIGUSR1")
class TestStatusFlags(unittest.TestCase):

    def test_a_runner_takes_the_flow_s_pending_flags(self):
        from vamos.spectre import flow, run_vacask, run_xyce
        for make in (run_vacask._Interrupts, run_xyce.Signals):
            with self.subTest(runner=make.__module__):
                sig = flow._Signals()
                sig.install()
                try:
                    os.kill(os.getpid(), signal.SIGUSR1)
                    self.assertTrue(sig.pending())
                    with make() as intr:
                        self.assertEqual(intr.take_flags(), [signal.SIGUSR1])
                    self.assertFalse(sig.pending())
                finally:
                    sig.restore()


# -- S-2E's loud name gaps -------------------------------------------------------------------------------------------

class TestNameGaps(unittest.TestCase):

    def test_ends_names_a_subckt_whose_name_holds_an_escaped_blank(self):
        nl = parse_deck("// t\nsimulator lang=spectre\nV1 (in 0) vsource dc=1\nX1 (in 0) s\\ t\nsubckt s\\ t (p q)\n"
                        "  R1 (p q) resistor r=1k\nends s\\ t\nop1 dc\n")
        self.assertEqual([s.name for s in nl.body if hasattr(s, "ports")], ["s t"])
        from vamos.notes import NoteError
        with self.assertRaises(NoteError) as cm:
            parse_deck("// t\nsimulator lang=spectre\nsubckt s\\ t (p q)\n  R1 (p q) resistor r=1k\nends s\\ u\n")
        self.assertIn("ends s\\ u closes subckt s\\ t", str(cm.exception))

    def test_a_nodeset_of_a_name_that_ends_in_a_bang(self):
        nl = parse_deck("// t\nsimulator lang=spectre\nglobal 0 vdd!\nV1 (vdd! 0) vsource dc=1\n"
                        "R1 (vdd! a) resistor r=1k\n"
                        "R2 (a 0) resistor r=1k\nnodeset vdd!=0.4 a=0.2\nop1 dc\n")
        self.assertEqual(sorted(nl.nodesets), ["a", "vdd!"])

    STAR = ("// t\nsimulator lang=spectre\nV1 (in 0) vsource dc=1\nR1 (in s\\*t) resistor r=1k\nR2 (s\\*t sat) "
            "resistor r=1k\nR3 (sat 0) resistor r=1k\ns\\*t (sat 0) isource dc=0\nnodeset s\\*t=0.4\nic s\\*t=0.3\n"
            "op1 dc\n")

    def test_an_escaped_star_is_a_character(self):
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                _nl, _plan, _nl2, sm, notes = resolved(self.STAR + "save s\\*t s\\*t:1\n", engine)
                self.assertFalse([n for n in notes if n.severity in ("error", "warning")], notes)
                self.assertEqual([name for _r, name, _t in sm.per_step["vamos_a1"]], ["s*t", "s*t:1"])
                _nl, _plan, _nl2, sm, notes = resolved(self.STAR + "save s*t\n", engine)
                self.assertEqual(sorted(name for _r, name, _t in sm.per_step["vamos_a1"]), ["s*t", "sat"])

    def test_an_escaped_colon_in_an_instance_name(self):
        deck = ("// t\nsimulator lang=spectre\nV1 (in 0) vsource dc=1\np\\:q (in a) resistor r=1k\nR2 (a 0) resistor "
                "r=1k\nop1 dc\nsave p\\:q:1 a\n")
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                _nl, _plan, _nl2, sm, notes = resolved(deck, engine)
                self.assertFalse([n for n in notes if n.severity in ("error", "warning")], notes)
                self.assertEqual([name for _r, name, _t in sm.per_step["vamos_a1"]], ["p:q:1", "a"])


# -- psfcheck --------------------------------------------------------------------------------------------------------

class TestPsfcheckNames(TempDir):

    def test_a_sweep_child_whose_name_holds_an_underscore(self):
        from vamos.output import psf
        from vamos.spectre.results import AnalysisResult, Signal
        head = psf.PsfHead(version="0.1.0", date="1:02:03 AM, Mon Oct 19, 2026", design="// t")
        results = [AnalysisResult("s_w_d_1-sweep", "sweep", "s_w_d_1.sweep", tree="sweepNode",
                                  sweep=("rv", "", 1, [1000.0, 2000.0]), header={"tolerance.relative": 1e-3},
                                  description="Sweep parent")]
        for i, rv in enumerate((1000.0, 2000.0)):
            results.append(AnalysisResult("s_w-%03d_d_1-dc" % i, "dc", "s_w-%03d_d_1.dc" % i, parent="s_w_d_1-sweep",
                                          tree="leafNode", description="DC Analysis `s_w-%03d_d_1'" % i,
                                          header=dict(DC_HEADER),
                                          swept={"rv": rv}, data_type="scalar",
                                          signals=[Signal("out", "V", "V", [0.5])]))
        for res in results:
            psf.write_analysis(self.tmp, res, head)
        psf.write_logfile(self.tmp, results, head, stamp="Mon Oct 19 01:02:03 2026")
        log = psfcheck.check_logfile(os.path.join(self.tmp, "logFile"))
        self.assertEqual(log.keys(), ["s_w_d_1-sweep", "s_w-000_d_1-dc", "s_w-001_d_1-dc"])


if __name__ == "__main__":
    unittest.main()
