"""X, phase 2: the Xyce runner, vamos/spectre/run_xyce.py (docs/VAMOS_SPECTRE_DESIGN.md §7.2, §1 step 11,
§2.7, §10; the stopping contract is vamos/spectre/stop.py).

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_run_xyce

Both legs, no engine:
  TestReplay          the state replay (e2e 6: the PULSE in the tran deck only; a card and a group
                      parameter; the renames reversed; an r/c/l value; temp; a variable and its
                      dependents in the deck's .param)
  TestRenderDecks     render_decks reproduces the chain's render goldens of e2e 1-10; xf refused
  TestPlaceVa         the Verilog-A copies: base names, the relative includes beside them, a base-name
                      clash or a climbing include under vamos_va<k>/
  TestCheckOutput     §7.2's print-file rule on hand-written rawfiles and .PRINT tables
  TestHelpers         ended_by, diagnostics, trim_partial_line, planned, Signals
  TestRunFake         run() against a fake Xyce (a script named by VAMOS_XYCE that writes what
                      fake.json in the run directory says): the order of the runs, the -norun check,
                      a problem named by the alter or sweep that introduced it, a failed analysis
                      among good ones, a failed .STEP point, a crash, a kill from outside, the
                      verbose echo, a Xyce that cannot start
WSL (needs_xyce; needs_pyms for the Verilog-A decks):
  TestRunE2E          e2e 1-10 through spectre.parse -> plan.build -> signals.resolve -> run(), against
                      the xyce parts of their engine_expect.json (05_xf and 10's deck_cross are
                      refused at plan.build)
  TestRunXyceFailures a singular .OP (exit 1: that step failed, status 1); a tran failing part-way
                      among good analyses [E66]; a .STEP point failing part-way (Xyce goes on and
                      exits 0: failed_points); an alter and a sweep of a parameter Xyce does not
                      know (status 2 naming them)
  TestRunXyceSignals  SIGTERM and SIGINT on a long tran (status 3, the partial rawfile readable, the
                      later deck not run), a second signal (stop.Stop), SIGUSR1 (the status line, the
                      run goes on) and SIGUSR2 (the notice)
"""

import json
import os
import shutil
import signal
import stat
import sys
import threading
import time
import unittest
from datetime import datetime
from typing import Dict, List, Optional

from vamos_testlib import LINUX, TempDir, reap  # noqa: F401
from spectre_testlib import SPECTRE_FIXTURES, have_xyce, needs_pyms, needs_xyce  # noqa: E402

from vamos.banner import Banner  # noqa: E402
from vamos.netlist import ir, plan as PL, rawfile, signals as SG, spectre, xyce  # noqa: E402
from vamos.netlist.expr_ast import Num  # noqa: E402
from vamos.notes import ERROR, NoteError  # noqa: E402
from vamos.spectre import run_xyce as RX, stop  # noqa: E402
from vamos.spectre.job import Settings, SpectreJob  # noqa: E402
from vamos.spectre.log import SpectreLog  # noqa: E402
from vamos.spectre.results import EngineResult  # noqa: E402

E2E = os.path.join(SPECTRE_FIXTURES, "e2e")
CASES = sorted(d for d in os.listdir(E2E) if os.path.isdir(os.path.join(E2E, d)) and d[:2].isdigit()
               and 1 <= int(d[:2]) <= 10)


# -- the chain up to the runner ----------------------------------------------------------------------

class Chain:
    """spectre.parse -> plan.build -> signals.resolve for Xyce: the runner's inputs."""

    def __init__(self, deck: str, engine: str = "xyce") -> None:
        deck = os.path.abspath(deck)
        self.nl = spectre.parse(deck, os.path.dirname(deck), spectre.SpectreParseOpts())
        self.plan = PL.build(self.nl, engine, Settings())
        self.nl2, self.sigmap, self.notes = SG.resolve(self.nl, self.plan)

    @property
    def steps(self) -> List[PL.AnalysisStep]:
        return [a.step for a in self.plan.actions if a.op == "analysis" and a.step is not None]

    def column(self, names: Dict, step_id: str, spectre_name: str) -> str:
        for ref, sname, _ptype in self.sigmap.per_step[step_id]:
            if sname == spectre_name:
                return names[step_id][ref]
        raise KeyError("no signal %r in step %s" % (spectre_name, step_id))


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_deck(d: str, name: str, text: str) -> str:
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with open(path, "w") as fh:
        fh.write(text)
    return path


class LogFile:
    """A SpectreLog writing to a file only (=log), and its text; profile: the shipped banner
    profile (the analysis banners and times print), else none (they do not)."""

    def __init__(self, d: str, profile: bool = False) -> None:
        from vamos import banner
        self.path = os.path.join(d, "spectre.log")
        self.job = SpectreJob("spectre", ["deck.scs"], d, log_mode="file", log_path=self.path)
        prof = banner.load_profile("spectre", "spectre") if profile else None
        self.log = SpectreLog(self.job, Banner(prof, "spectre"), datetime.now())

    def text(self) -> str:
        self.log.close()
        with open(self.path, encoding="utf-8") as fh:
            return fh.read()


def run_chain(tc: TempDir, ch: Chain, name: str = "run", **kw) -> "tuple":
    """run_xyce.run on a chain in <tmp>/<name>; (EngineResult, the log text, the run directory).
    profile=True prints the analysis banners; the other keywords go to run()."""
    run_dir = os.path.join(tc.tmp, name)
    os.makedirs(run_dir, exist_ok=True)
    lf = LogFile(tc.tmp, kw.pop("profile", False))
    er = RX.run(ch.nl2, ch.plan, ch.sigmap, run_dir, lf.job, lf.log, **kw)
    return er, lf.text(), run_dir


# -- rawfiles written by hand (both legs) --------------------------------------------------------------

def raw_text(names: List[str], plots: List[List[tuple]], blank=(), cplx: bool = False,
             declared: Optional[Dict[int, int]] = None) -> str:
    """An ASCII rawfile: one plot per list of rows; `blank` plot indices get an empty No. Points,
    `declared` overrides a plot's count."""
    out = []
    for k, rows in enumerate(plots):
        n = "" if k in blank else str((declared or {}).get(k, len(rows)))
        out += ["Title: fake", "Date: today", "Plotname: fake plot %d" % k,
                "Flags: %s" % ("complex" if cplx else "real"), "No. Variables: %d" % len(names),
                "No. Points: %s" % n, "Variables:"]
        out += ["\t%d\t%s\t%s" % (i, nm, "time" if nm.lower() == "time" else "voltage")
                for i, nm in enumerate(names)]
        out.append("Values:")
        for i, row in enumerate(rows):
            vals = [("%r,0" % v) if cplx else repr(float(v)) for v in row]
            out.append("%d\t%s" % (i, vals[0]))
            out += ["\t%s" % v for v in vals[1:]]
    return "\n".join(out) + "\n"


def prn_text(names: List[str], tables: List[List[tuple]], end: bool = True) -> str:
    out = ["Index   " + "   ".join(names)]
    for rows in tables:
        for i, row in enumerate(rows):
            out.append("%d   %s" % (i, "   ".join("%.8e" % v for v in row)))
    if end:
        out.append("End of Xyce(TM) Parameter Sweep")
    return "\n".join(out) + "\n"


def print_columns(deck_text: str) -> List[str]:
    """The .PRINT items of a rendered deck, after FILE=."""
    for line in deck_text.splitlines():
        if line.startswith(".PRINT"):
            toks = line.split()
            k = next(i for i, t in enumerate(toks) if t.startswith("FILE="))
            return toks[k + 1:]
    return []


def good_output(step: PL.AnalysisStep, deck_text: str) -> str:
    """The text of a complete print file for a step, as Xyce writes its shape (§7.2)."""
    cols = print_columns(deck_text)
    total, _per, rows = RX.planned(step)
    if step.kind == "noise":
        n = rows or 1
        return prn_text(["FREQ"] + cols, [[(1.0 + i,) + (1e-18,) * len(cols) for i in range(n)]] * total)
    if step.kind == "tran":
        t_stop = float(step.args["stop"])
        pts = [tuple([t_stop * i / 4.0] + [0.5] * len(cols)) for i in range(5)]
        return raw_text(["TIME"] + cols, [pts] * total)
    if step.kind == "ac":
        pts = [tuple([float(i + 1)] + [0.5] * len(cols)) for i in range(rows or 1)]
        return raw_text(["FREQUENCY"] + cols, [pts] * total, cplx=True)
    pts = [tuple([float(i)] + [0.5] * len(cols)) for i in range(rows or 1)]
    return raw_text(["sweep"] + cols, [pts] * total)


# -- the fake Xyce (both legs) -------------------------------------------------------------------------

FAKE = '''#!%(python)s
"""A fake Xyce for test_spectre_run_xyce: what it prints, writes and returns comes from fake.json
in its working directory, keyed by the deck ("norun <deck>" for the -norun check)."""
import json, os, signal, sys, time
args = sys.argv[1:]
norun = "-norun" in args
deck = [a for a in args if a != "-norun"][-1]
with open("fake.calls", "a") as fh:
    fh.write(" ".join(args) + "\\n")
try:
    with open("fake.json") as fh:
        spec = json.load(fh)
except (OSError, ValueError):
    spec = {}
b = spec.get(("norun " if norun else "") + deck, {})
sys.stdout.write(b.get("out", ""))
sys.stdout.flush()
if "write" in b:
    with open(b["file"], "w") as fh:
        fh.write(b["write"])
if b.get("ignore_term"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if b.get("sleep"):
    time.sleep(b["sleep"])
if b.get("signal"):
    os.kill(os.getpid(), getattr(signal, b["signal"]))
    time.sleep(10)
if b.get("abort"):
    os.abort()
sys.exit(b.get("rc", 0))
'''


class FakeCase(TempDir):
    """A TempDir with the fake Xyce on VAMOS_XYCE."""

    def setUp(self):
        super().setUp()
        self.fake = os.path.join(self.tmp, "fake_xyce.py")
        with open(self.fake, "w") as fh:
            fh.write(FAKE % {"python": sys.executable})
        os.chmod(self.fake, os.stat(self.fake).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.environ["VAMOS_XYCE"] = self.fake
        os.environ.pop("VAMOS_VERBOSE", None)

    def behave(self, run_dir: str, ch: Chain, overrides: Optional[Dict[str, dict]] = None) -> Dict[str, dict]:
        """fake.json for a chain: every deck writes a complete print file, then the overrides
        (deck file -> fields; "norun <deck>" for the check)."""
        os.makedirs(run_dir, exist_ok=True)
        decks, _names = RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        spec: Dict[str, dict] = {}
        for d in decks:
            spec[d.file] = {"file": d.output, "write": good_output(d.step, d.text), "rc": 0}
        for key, fields in (overrides or {}).items():
            spec.setdefault(key, {}).update(fields)
        with open(os.path.join(run_dir, "fake.json"), "w") as fh:
            json.dump(spec, fh)
        return spec

    def calls(self, run_dir: str) -> List[str]:
        p = os.path.join(run_dir, "fake.calls")
        if not os.path.exists(p):
            return []
        with open(p) as fh:
            return fh.read().splitlines()


SWEEP3 = """// three .STEP points of a tran, then an op
simulator lang=spectre
parameters rval=1k
V1 (in 0) vsource dc=1
R1 (in out) resistor r=rval
C1 (out 0) capacitor c=1u
op1 dc
swp sweep param=rval values=[1k 2k 3k] {
  tr1 tran stop=1m
}
op2 dc
save out
"""

ALTER_TLEV = """// an alter of a diode parameter Xyce does not know (§7.2), then a sweep of it
simulator lang=spectre
V1 (b 0) vsource dc=1
R1 (b a) resistor r=1k
D1 (a 0) dmod
model dmod diode is=1e-14
op1 dc
a1 alter mod=dmod param=tlev value=1
op2 dc
op3 dc
save a
"""

SWEEP_TLEV = """// a sweep of a diode parameter Xyce does not know (§7.2 [E43])
simulator lang=spectre
V1 (b 0) vsource dc=1
R1 (b a) resistor r=1k
D1 (a 0) dmod
model dmod diode is=1e-14
op1 dc
swp sweep mod=dmod param=tlev values=[1 2] {
  op2 dc
}
save a
"""

NOPARAM = "Netlist warning: No model parameter TLEV found for model DMOD of type D, parameter ignored.\n"


# -- both legs -----------------------------------------------------------------------------------------

class TestReplay(TempDir):
    """§7.2's state replay (test_spectre_chain.replay is the reference)."""

    def test_alter_type_puts_the_pulse_in_the_tran_deck_only(self):
        ch = Chain(os.path.join(E2E, "06_alter_type", "deck.scs"))
        decks, _ = RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        text = {d.step.name: d.text for d in decks}
        self.assertEqual(sorted(text), ["op1", "op2", "tr"])
        self.assertIn("PULSE(", text["tr"])
        self.assertNotIn("PULSE(", text["op1"])
        self.assertNotIn("PULSE(", text["op2"])
        alters = [a for a in ch.plan.actions if a.op == "alter"]
        self.assertEqual([d.alters for d in decks], [[], [alters[0]], [alters[1]]])
        # replay works on a copy: the input IR keeps its dc source
        src = RX.find_instance(ch.nl2, "Vdif").source
        self.assertIsNone(src.wave)
        after = RX.replay(ch.nl2, alters[0])
        self.assertEqual(RX.find_instance(after, "Vdif").source.wave, "pulse")
        self.assertIsNone(RX.find_instance(ch.nl2, "Vdif").source.wave)

    def deck(self, text: str) -> Chain:
        return Chain(write_deck(self.tmp, "deck.scs", text))

    def test_model_parameter_renamed_by_the_masters_is_reversed(self):
        ch = self.deck("""simulator lang=spectre
V1 (b 0) vsource dc=1
R1 (b a) resistor r=1k
D1 (a 0) dmod
model dmod diode is=1e-14
op1 dc
a1 alter mod=dmod param=ik value=0.01
op2 dc
""")
        act = next(a for a in ch.plan.actions if a.op == "alter")
        self.assertEqual(act.args["target"], ("model", "dmod", "ikf"))      # plan.build's engine spelling
        out = RX.replay(ch.nl2, act)
        self.assertEqual(out.models()["dmod"].params["ik"], Num(0.01))      # the IR keeps Spectre's
        decks, _ = RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        self.assertNotIn("ikf", decks[0].text.lower())
        self.assertIn("ikf=0.01", decks[1].text.lower())

    def test_resistor_value_and_temperature(self):
        ch = self.deck("""simulator lang=spectre
V1 (b 0) vsource dc=1
R1 (b a) resistor r=1k
R2 (a 0) resistor r=1k
op1 dc
a1 alter dev=R2 param=r value=3k
a2 alter param=temp value=50
op2 dc
""")
        decks, _ = RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        self.assertIn("R2 a 0 1000.0", decks[0].text)
        self.assertIn("R2 a 0 3000.0", decks[1].text)
        self.assertIn("temp=27.0", decks[0].text)
        self.assertIn("temp=50.0", decks[1].text)
        work = RX.replay(ch.nl2, ch.plan.actions[[a.op for a in ch.plan.actions].index("alter") + 1])
        self.assertEqual(work.temp, 50.0)

    def test_variable_alter_changes_the_param_and_its_dependents(self):
        ch = self.deck("""simulator lang=spectre
parameters rval=1k r2=2*rval
V1 (b 0) vsource dc=1
R1 (b a) resistor r=rval
R2 (a 0) resistor r=r2
op1 dc
a1 alter param=rval value=3k
op2 dc
""")
        act = next(a for a in ch.plan.actions if a.op == "alter")
        self.assertEqual(act.args["target"], ("variable", "rval", ""))
        out = RX.replay(ch.nl2, act, ch.plan.dependents)
        self.assertEqual(out.values["rval"], 3000.0)
        self.assertEqual(out.values["r2"], 6000.0)
        self.assertEqual(next(p for p in out.body if isinstance(p, ir.Param) and p.name == "rval").expr, Num(3000.0))
        decks, _ = RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        self.assertIn(".param rval=1000.0", decks[0].text)
        self.assertIn(".param rval=3000.0", decks[1].text)
        self.assertEqual(ch.nl2.values["rval"], 1000.0)                    # the input IR is not changed

    def test_instance_rename_reversed(self):
        self.assertEqual(RX.ir_param("bsim3v3", "instance", "instance_nqsmod"), "nqsmod")
        self.assertEqual(RX.ir_param("diode", "instance", "pj"), "perim")
        self.assertEqual(RX.ir_param("diode", "card", "ikf"), "ik")
        self.assertEqual(RX.ir_param("nosuchmaster", "card", "x"), "x")
        self.assertEqual(RX.ir_value([1, 2]), [Num(1.0), Num(2.0)])

    def test_find_instance_through_subckts(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        self.assertEqual(RX.find_instance(ch.nl2, "X1.R1").name, "R1")
        with self.assertRaises(KeyError):
            RX.find_instance(ch.nl2, "X1.R9")


class TestRenderDecks(unittest.TestCase):
    def test_e2e_decks_equal_the_chain_goldens(self):
        for case in CASES:
            with self.subTest(case=case):
                deck = os.path.join(E2E, case, "deck.scs")
                try:
                    ch = Chain(deck)
                except NoteError as exc:
                    exp = load_json(os.path.join(E2E, case, "engine_expect.json"))["xyce"]
                    self.assertEqual(exp["status"], 2)
                    self.assertTrue(any(exp["refused"] in n.message for n in exc.notes), case)
                    continue
                hdl = [os.path.basename(h) for h in ch.nl2.hdl]
                notes: list = []
                decks, names = RX.render_decks(ch.nl2, ch.plan, ch.sigmap, hdl, notes)
                self.assertEqual(names, xyce.names_for(ch.nl2, ch.plan, ch.sigmap))
                gdir = os.path.join(E2E, case, "render", "xyce")
                self.assertEqual(sorted(d.file for d in decks), sorted(os.listdir(gdir)))
                for d in decks:
                    with open(os.path.join(gdir, d.file), encoding="utf-8") as fh:
                        self.assertEqual(d.text, fh.read(), "%s %s" % (case, d.file))
                self.assertFalse([n for n in notes if n.severity == ERROR])
                messages = [n.message for n in notes]
                self.assertEqual(len(messages), len(set(messages)), "each note once")

    def test_xf_is_refused(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        step = ch.steps[0]
        step.kind = "xf"
        with self.assertRaises(NoteError) as cm:
            RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        self.assertTrue(any("xf runs on VACASK only" in n.message for n in cm.exception.notes))


class TestPlaceVa(TempDir):
    def src(self, rel: str, text: str) -> str:
        return write_deck(os.path.dirname(os.path.join(self.tmp, "src", rel)), os.path.basename(rel), text)

    def test_base_names_and_the_includes_beside_them(self):
        a = self.src("lib/ires.va", '`include "disciplines.vams"\n`include "ires_defs.include"\nmodule ires(a, b); endmodule\n')
        self.src("lib/ires_defs.include", "`define RDEF 4000.0\n")
        b = self.src("other/vres.va", "module vres(a, b); endmodule\n")
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        self.assertEqual(RX.place_va([a, b, a], run_dir), ["ires.va", "vres.va"])
        self.assertEqual(sorted(os.listdir(run_dir)), ["ires.va", "ires_defs.include", "vres.va"])
        self.assertEqual(RX.va_closure(a), [os.path.abspath(a), os.path.join(os.path.dirname(os.path.abspath(a)),
                                                                            "ires_defs.include")])

    def test_a_base_name_clash_goes_into_a_subdirectory(self):
        a = self.src("one/r.va", "module r1(a, b); endmodule\n")
        b = self.src("two/r.va", "module r2(a, b); endmodule\n")
        c = self.src("three/r.va", "module r1(a, b); endmodule\n")       # the same text as one/r.va: shared
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        self.assertEqual(RX.place_va([a, b, c], run_dir), ["r.va", "vamos_va1/r.va", "r.va"])
        with open(os.path.join(run_dir, "vamos_va1", "r.va")) as fh:
            self.assertIn("r2", fh.read())

    def test_an_include_that_climbs_keeps_its_relative_place(self):
        a = self.src("models/m.va", '`include "../common/defs.include"\nmodule m(a, b); endmodule\n')
        self.src("common/defs.include", "`define X 1\n")
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        self.assertEqual(RX.place_va([a], run_dir), ["models/m.va"])
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "common", "defs.include")))

    def test_a_missing_source_keeps_its_path(self):
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        missing = os.path.join(self.tmp, "nope.va")
        self.assertEqual(RX.place_va([missing], run_dir), [missing])


class TestCheckOutput(TempDir):
    """§7.2: a print file that is missing, blank or short is a failed analysis."""

    def steps(self, text: str) -> List[PL.AnalysisStep]:
        return Chain(write_deck(self.tmp, "deck.scs", text)).steps

    def setUp(self):
        super().setUp()
        st = self.steps(SWEEP3.replace("op2 dc", "op2 dc\nac1 ac start=1 stop=100 dec=1\ndc1 dc dev=V1 param=dc "
                                               "start=0 stop=1 step=0.25"))
        self.op, self.tran, self.op2, self.ac, self.dc = st

    def file(self, name: str, text: str) -> str:
        return write_deck(self.tmp, name, text)

    def test_complete_files(self):
        self.assertEqual(RX.planned(self.op), (1, 1, 1))
        self.assertEqual(RX.planned(self.tran), (3, 1, None))
        self.assertEqual(RX.planned(self.ac), (1, 1, 3))
        self.assertEqual(RX.planned(self.dc), (1, 1, 5))
        tr = [tuple([t, 0.5]) for t in (0.0, 5e-4, 1e-3)]
        self.assertTrue(RX.check_output(self.tran, self.file("t.raw", raw_text(["TIME", "V(OUT)"], [tr] * 3))).complete)
        self.assertTrue(RX.check_output(self.op, self.file("o.raw", raw_text(["sweep", "V(OUT)"], [[(0, 1)]]))).complete)
        dc = [(v / 4.0, 1.0) for v in range(5)]
        self.assertTrue(RX.check_output(self.dc, self.file("d.raw", raw_text(["sweep", "V(OUT)"], [dc]))).complete)

    def test_missing_blank_short_and_unreadable(self):
        chk = RX.check_output(self.op, os.path.join(self.tmp, "none.raw"))
        self.assertEqual((chk.complete, chk.first_bad), (False, 0))
        self.assertIn("wrote no", chk.why)
        header_only = raw_text(["TIME", "V(OUT)"], [[]], blank=(0,))
        chk = RX.check_output(self.tran, self.file("h.raw", header_only))
        self.assertEqual((chk.complete, chk.first_bad), (False, 0))
        self.assertIn("blank No. Points", chk.why)
        dc = [(v / 4.0, 1.0) for v in range(4)]
        chk = RX.check_output(self.dc, self.file("d.raw", raw_text(["sweep", "V(OUT)"], [dc])))
        self.assertIn("4 rows where 5 were planned", chk.why)
        chk = RX.check_output(self.op, self.file("x.raw", "not a rawfile\n"))
        self.assertFalse(chk.complete)
        self.assertIn("unreadable", chk.why)

    def test_a_tran_short_of_stop_or_a_missing_step_point(self):
        tr = [tuple([t, 0.5]) for t in (0.0, 5e-4, 1e-3)]
        short = [tuple([t, 0.5]) for t in (0.0, 5e-4)]
        chk = RX.check_output(self.tran, self.file("s.raw", raw_text(["TIME", "V(OUT)"], [tr, short, tr])))
        self.assertEqual((chk.complete, chk.first_bad), (False, 1))
        self.assertIn("not at stop", chk.why)
        chk = RX.check_output(self.tran, self.file("m.raw", raw_text(["TIME", "V(OUT)"], [tr, tr])))
        self.assertEqual((chk.complete, chk.first_bad), (False, 2))
        self.assertIn("2 of the 3", chk.why)

    def test_a_failed_step_point_merged_into_the_next(self):
        # Xyce goes on after a failed .STEP point and appends the next point to its plot (measured)
        tr = [tuple([t, 0.5]) for t in (0.0, 5e-4, 1e-3)]
        merged = [tuple([t, 0.5]) for t in (0.0, 2e-4)] + tr
        chk = RX.check_output(self.tran, self.file("g.raw", raw_text(["TIME", "V(OUT)"], [tr, merged])))
        self.assertEqual((chk.complete, chk.first_bad), (False, 1))
        self.assertIn("restarts its time axis", chk.why)

    def test_declared_count_differs_and_too_many_plots(self):
        tr = [tuple([t, 0.5]) for t in (0.0, 5e-4, 1e-3)]
        chk = RX.check_output(self.tran, self.file("c.raw", raw_text(["TIME", "V(OUT)"], [tr] * 3, declared={2: 7})))
        self.assertEqual(chk.first_bad, 2)
        chk = RX.check_output(self.tran, self.file("p.raw", raw_text(["TIME", "V(OUT)"], [tr] * 4)))
        self.assertEqual((chk.complete, chk.first_bad), (False, None))
        chk = RX.check_output(self.op, self.file("o2.raw", raw_text(["sweep", "V(OUT)"], [[(0, 1), (0, 1)]])))
        self.assertIn("2 rows where 1 were planned", chk.why)

    def test_noise_tables(self):
        st = self.steps("""simulator lang=spectre
V1 (in 0) vsource dc=0
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
swp sweep dev=R2 param=r values=[1k 2k] {
  nz (out 0) noise iprobe=V1 start=1 stop=100 dec=1
}
""")
        nz = st[0]
        self.assertEqual(RX.planned(nz), (2, 1, 3))
        rows = [(1.0, 1e-18), (10.0, 1e-18), (100.0, 1e-18)]
        good = prn_text(["FREQ", "ONOISE"], [rows, rows])
        self.assertTrue(RX.check_output(nz, self.file("n.prn", good)).complete)
        chk = RX.check_output(nz, self.file("n2.prn", prn_text(["FREQ", "ONOISE"], [rows, rows[:2]])))
        self.assertEqual((chk.complete, chk.first_bad), (False, 1))
        chk = RX.check_output(nz, self.file("n3.prn", prn_text(["FREQ", "ONOISE"], [rows])))
        self.assertEqual(chk.first_bad, 1)


class TestHelpers(TempDir):
    def test_ended_by(self):
        self.assertEqual(RX.ended_by(-15), 15)
        self.assertEqual(RX.ended_by(-11), 11)
        self.assertEqual(RX.ended_by(128 + signal.SIGTERM), signal.SIGTERM)     # E72's 143
        self.assertEqual(RX.ended_by(128 + signal.SIGINT), signal.SIGINT)       # and 130
        self.assertIsNone(RX.ended_by(0))
        self.assertIsNone(RX.ended_by(1))
        self.assertIsNone(RX.ended_by(200))

    def test_diagnostics(self):
        text = """***** Welcome to the Xyce(TM) Parallel Electronic Simulator
***** Beginning Transient Calculation...
Netlist warning: Numerically singular matrix found by Amesos, returning zero
 solution to nonlinear solver!
Netlist warning: Numerically singular matrix found by Amesos, returning zero
 solution to nonlinear solver!
	Number Failed Steps Attempted:		1
	Failed DC sweep steps:
		DC Step # 1
Time step too small near step number: 464  Exiting transient loop.
function OneStep::rejectStep:
   Maximum number of failures at time 1e-06
*** Xyce Abort ***
  Netlist Import                             1        0.552 (60.87%)        0.039 (47.31%)
/tmp/pyms_vae_cache/vae_x.so.build/eval.cpp:10:24: error: '$abstime' was not declared in this scope
build_vae_so: full build failed (g++ -O2 ...); retrying without zero-valued params
"""
        self.assertEqual(RX.diagnostics(text), [
            "Netlist warning: Numerically singular matrix found by Amesos, returning zero solution to nonlinear solver!",
            "Failed DC sweep steps: DC Step # 1",
            "Time step too small near step number: 464  Exiting transient loop.",
            "function OneStep::rejectStep: Maximum number of failures at time 1e-06",
            "*** Xyce Abort ***",
            "/tmp/pyms_vae_cache/vae_x.so.build/eval.cpp:10:24: error: '$abstime' was not declared in this scope",
            "build_vae_so: full build failed (g++ -O2 ...); retrying without zero-valued params"])

    def test_trim_partial_line(self):
        p = write_deck(self.tmp, "x.prn", "Index FREQ ONOISE\n0 1.0 2.0\n1 2.0 3.")
        self.assertTrue(RX.trim_partial_line(p))
        with open(p) as fh:
            self.assertEqual(fh.read(), "Index FREQ ONOISE\n0 1.0 2.0\n")
        self.assertFalse(RX.trim_partial_line(p))
        self.assertEqual(len(rawfile.read_prn(p)[0].points), 1)
        q = write_deck(self.tmp, "y.prn", "Index FREQ" + " " * 70000)
        self.assertTrue(RX.trim_partial_line(q))
        self.assertEqual(os.path.getsize(q), 0)

    def test_signals_is_the_stop_contract(self):
        s = RX.Signals(grace=0.25)
        self.assertEqual(s.handled, stop.HANDLED)
        self.assertEqual(s.forward, stop.FORWARD)
        self.assertEqual(s.flags, stop.FLAGS)
        self.assertEqual(s.grace, 0.25)
        self.assertIsNone(s.second)
        s._on_signal(signal.SIGINT, None)
        s._on_signal(signal.SIGTERM, None)
        self.assertEqual((s.signum, s.second, s.killed), (signal.SIGINT, signal.SIGTERM, False))
        s._on_alarm(None, None)
        self.assertTrue(s.expired)


class TestRunFake(FakeCase):
    """run() against the fake Xyce: the control flow of §7.2 and §2.7 on both legs."""

    def test_every_deck_runs_after_the_norun_check(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch)
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 0, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "ok", "vamos_a3": "ok"})
        self.assertEqual(er.files, {s: os.path.join(run_dir, s + ".raw") for s in ("vamos_a1", "vamos_a2", "vamos_a3")})
        self.assertEqual(er.failed_points, {})
        self.assertEqual(er.names, xyce.names_for(ch.nl2, ch.plan, ch.sigmap))
        self.assertEqual(self.calls(run_dir), ["-norun vamos_a1.cir", "vamos_a1.cir", "vamos_a2.cir", "vamos_a3.cir"])
        for k in (1, 2, 3):
            with open(os.path.join(E2E, "01_rc", "render", "xyce", "vamos_a%d.cir" % k)) as g, \
                    open(os.path.join(run_dir, "vamos_a%d.cir" % k)) as fh:
                self.assertEqual(fh.read(), g.read())
            self.assertTrue(os.path.isfile(os.path.join(run_dir, "vamos_a%d.log" % k)))
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "vamos_a1.norun.log")))
        self.assertTrue(any(line.startswith("vamos_a3.cir: vamos_a3.cir -> exit 0") for line in er.log), er.log)
        self.assertNotIn("Error from", log)

    def test_a_norun_problem_stops_before_any_run(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", ALTER_TLEV.replace("is=1e-14", "is=1e-14 tlev=1")))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"norun vamos_a1.cir": {"out": NOPARAM}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual((er.rc, er.status), (2, {}), log)
        self.assertEqual(self.calls(run_dir), ["-norun vamos_a1.cir"])
        self.assertIn("Error from spectre during initial setup.", log)
        self.assertIn("No model parameter TLEV found for model DMOD", log)

    def test_a_problem_after_an_alter_names_the_alter(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", ALTER_TLEV))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {"out": NOPARAM}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 2, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed"})       # op3 never ran
        self.assertEqual(self.calls(run_dir), ["-norun vamos_a1.cir", "vamos_a1.cir", "vamos_a2.cir"])
        self.assertIn("alter `a1' sets a parameter Xyce does not know", log)
        self.assertIn("Error from spectre during DC analysis `op2'.", log)

    def test_a_problem_in_a_sweep_names_the_sweep(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", SWEEP_TLEV))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {
            "out": "Netlist error: Function or variable DMOD:TLEV is not defined\n*** Xyce Abort ***\n", "rc": 1}})
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 2, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed"})
        self.assertIn("sweep `swp' sets a parameter Xyce does not know", log)

    def test_an_unattributed_netlist_error_names_the_analysis(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {"out": "Netlist error: something Xyce dislikes\n", "rc": 1}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 2, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed"})
        self.assertIn("Xyce, the deck of analysis `myac' (vamos_a2.cir): Netlist error: something Xyce dislikes", log)

    def test_a_failed_analysis_among_good_ones(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        header = raw_text(["FREQUENCY", "V(IN)"], [[]], blank=(0,), cplx=True)
        self.behave(run_dir, ch, {"vamos_a2.cir": {"write": header, "rc": 1,
                                                   "out": "Time step too small near step number: 4  Exiting transient loop.\n"}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 1, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed", "vamos_a3": "ok"})
        self.assertEqual(self.calls(run_dir)[-1], "vamos_a3.cir")
        self.assertIn("analysis `myac' failed in Xyce (exit status 1; vamos_a2.raw has a blank No. Points (0 rows))", log)
        self.assertIn("Time step too small", log)
        self.assertIn("vamos_a2", er.files)

    def test_a_missing_print_file_with_exit_0_is_a_failure(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        spec = self.behave(run_dir, ch)
        del spec["vamos_a1.cir"]["write"]
        with open(os.path.join(run_dir, "fake.json"), "w") as fh:
            json.dump(spec, fh)
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 1, log)
        self.assertEqual(er.status["vamos_a1"], "failed")
        self.assertNotIn("vamos_a1", er.files)
        self.assertIn("Xyce wrote no vamos_a1.raw", log)

    def test_a_failed_step_point_gives_failed_points(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", SWEEP3))
        run_dir = os.path.join(self.tmp, "run")
        tr = [tuple([t, 0.5]) for t in (0.0, 5e-4, 1e-3)]
        merged = [tuple([t, 0.5]) for t in (0.0, 2e-4)] + tr
        self.behave(run_dir, ch, {"vamos_a2.cir": {"write": raw_text(["TIME", "V(OUT)"], [tr, merged])}})
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 1, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed", "vamos_a3": "ok"})
        self.assertEqual(er.failed_points, {"vamos_a2": 2})
        self.assertIn("the sweep points before point 2 are written", log)

    def test_a_first_step_point_failed_fails_the_whole_step(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", SWEEP3))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {"write": raw_text(["TIME", "V(OUT)"], [[]], blank=(0,)),
                                                   "rc": 1}})
        er, log, _ = run_chain(self, ch)
        self.assertEqual((er.rc, er.status["vamos_a2"], er.failed_points), (1, "failed", {}), log)

    @unittest.skipUnless(LINUX, "a self-inflicted signal of the fake Xyce (Linux/WSL)")
    def test_a_crash_is_status_2(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {"abort": True}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 2, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed"})
        self.assertIn("Xyce crashed (ended by SIGABRT)", log)
        self.assertEqual(self.calls(run_dir)[-1], "vamos_a2.cir")

    @unittest.skipUnless(LINUX, "a self-inflicted signal of the fake Xyce (Linux/WSL)")
    def test_a_kill_from_outside_is_status_3(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {"signal": "SIGKILL"}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 3, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "interrupted"})
        self.assertIn("analysis `myac' was interrupted (SIGKILL from outside vamos); its partial results "
                      "(vamos_a2.raw) are written", log)
        self.assertNotIn("vamos_a3.cir", self.calls(run_dir))

    @unittest.skipUnless(LINUX, "signals to the test process (Linux/WSL)")
    def test_the_grace_kill_is_converted_as_status_3(self):
        """stop.py: a Xyce still running proc.INTERRUPT_GRACE seconds after the forwarded SIGTERM is
        killed; that is not a second signal: the run is converted (rc 3), no stop.Stop."""
        from vamos import proc
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {"ignore_term": True, "sleep": 60}})
        late: List[int] = []
        saved = signal.signal(signal.SIGTERM, lambda s, f: late.append(s))
        grace, proc.INTERRUPT_GRACE = proc.INTERRUPT_GRACE, 0.5

        def send() -> None:
            t0 = time.time()
            while time.time() - t0 < 60 and "vamos_a2.cir" not in self.calls(run_dir):
                time.sleep(0.02)
            time.sleep(0.3)
            os.kill(os.getpid(), signal.SIGTERM)
        th = threading.Thread(target=send, daemon=True)
        th.start()
        t0 = time.time()
        try:
            er, log, _ = run_chain(self, ch)
        finally:
            proc.INTERRUPT_GRACE = grace
            signal.signal(signal.SIGTERM, saved)
        self.assertLess(time.time() - t0, 30)
        self.assertEqual(er.rc, 3, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "interrupted"})
        self.assertIn("analysis `myac' was interrupted (SIGTERM)", log)
        self.assertTrue(any("vamos_a2.cir -> exit -9" in line for line in er.log), er.log)
        self.assertEqual(late, [])

    def test_verbose_echo_and_no_analyses(self):
        os.environ["VAMOS_VERBOSE"] = "1"
        ch = Chain(os.path.join(E2E, "06_alter_type", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch)
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 0, log)
        self.assertIn("vamos: + (cd %s && %s -norun vamos_a1.cir)" % (run_dir, self.fake), log)
        self.assertIn("vamos: + (cd %s && %s vamos_a3.cir)" % (run_dir, self.fake), log)
        empty = PL.RunPlan(actions=[], engine="xyce")
        lf = LogFile(self.tmp)
        er = RX.run(ch.nl2, empty, ch.sigmap, os.path.join(self.tmp, "empty"), lf.job, lf.log)
        self.assertEqual((er.rc, er.status, er.files), (0, {}, {}))
        self.assertEqual(lf.text(), "")

    def test_a_xyce_that_cannot_start(self):
        os.environ["VAMOS_XYCE"] = os.path.join(self.tmp, "no", "Xyce")
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        er, log, _ = run_chain(self, ch)
        self.assertEqual((er.rc, er.status), (2, {}), log)
        self.assertIn("cannot run", log)

    def test_xf_guard(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        ch.steps[1].kind = "xf"
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual((er.rc, er.status), (2, {}))
        self.assertIn("xf runs on VACASK only", log)
        self.assertEqual(self.calls(run_dir), [])

    def test_render_notes_are_reported_once(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch)
        er, log, _ = run_chain(self, ch)
        self.assertEqual(log.count("option reltol=0.001 is not mapped on Xyce"), 1, log)
        self.assertIn("Notice from spectre during circuit read-in.", log)
        self.assertEqual(er.rc, 0)

    def test_each_deck_between_its_banner_and_its_time(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch)
        er, log, _ = run_chain(self, ch, profile=True)
        self.assertEqual(er.rc, 0, log)
        marks = ["DC Analysis `myop'", "Total time required for dc analysis `myop' was ",
                 "AC Analysis `myac': freq = (1 Hz -> 1 MHz)", "Total time required for ac analysis `myac' was ",
                 "Transient Analysis `tr': time = (0 s -> 5 ms)", "Total time required for tran analysis `tr' was "]
        at = [log.find(m) for m in marks]
        self.assertTrue(all(a >= 0 for a in at), "%s\n%s" % (at, log))
        self.assertEqual(at, sorted(at), log)

    def test_xyce_messages_once_per_run_without_the_registrations(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        out = ("Netlist warning: .HDL: compiled and registered vres from vres.va\n"
               "Netlist warning in file vamos_a1.cir at or near line 3 Device model DM: Given pbswgs is less than\n"
               " 0.1. Pbswgs is set to 0.1\n")
        self.behave(run_dir, ch, {"vamos_a1.cir": {"out": out}, "vamos_a2.cir": {"out": out}})
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 0, log)
        self.assertEqual(log.count("Xyce: Netlist warning in file vamos_a1.cir at or near line 3 Device model DM: "
                                   "Given pbswgs is less than 0.1. Pbswgs is set to 0.1"), 1, log)
        self.assertIn("Warning from spectre during DC analysis `myop'.", log)
        self.assertNotIn("compiled and registered", log)
        self.assertIn("vamos_a2.log: Netlist warning: .HDL: compiled and registered vres from vres.va", er.log)

    def test_strict_makes_render_warnings_errors(self):
        deck = write_deck(self.tmp, "deck.scs", """// a noise-free resistor with a temperature coefficient: render warns
simulator lang=spectre
V1 (a 0) vsource dc=1
R1 (a b) resistor r=1k isnoisy=no tc1=0.001
R2 (b 0) resistor r=1k
op1 dc
""")
        ch = Chain(deck)
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch)
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 0, log)
        self.assertIn("Warning from spectre during circuit read-in.", log)
        self.assertIn("isnoisy=no with tc1/tc2", log)
        er, log, run_dir = run_chain(self, ch, "strict", strict=True)
        self.assertEqual((er.rc, er.status), (2, {}), log)
        self.assertIn("Error from spectre during circuit read-in.", log)
        self.assertEqual(self.calls(run_dir), [])


# -- WSL: the real Xyce --------------------------------------------------------------------------------

def _close(got, want, rel: float, abs_: float) -> bool:
    if isinstance(want, (list, tuple)):
        g = complex(got)
        return _close(g.real, want[0], rel, abs_) and _close(g.imag, want[1], rel, abs_)
    got = got.real if isinstance(got, complex) else got
    return abs(got - want) <= abs_ + rel * abs(want)


def _plots(path: str) -> List[rawfile.Raw]:
    return rawfile.read_prn(path) if path.endswith(".prn") else rawfile.read_all(path)


def _value(plot: rawfile.Raw, col: str, chk: dict):
    k = plot.exact(col)
    rows = plot.points
    if "row" in chk:
        return rows[chk["row"]][k]
    x = chk["at"]
    a = next(i for i, (name, _t) in enumerate(plot.variables) if name.lower() in ("time", "frequency", "freq"))
    xs = [(p[a].real if isinstance(p[a], complex) else p[a]) for p in rows]
    for i in range(1, len(rows)):
        if xs[i - 1] <= x <= xs[i]:
            y0, y1 = rows[i - 1][k], rows[i][k]
            return y1 if xs[i] == xs[i - 1] else y0 + (y1 - y0) * (x - xs[i - 1]) / (xs[i] - xs[i - 1])
    if rows and abs(xs[-1] - x) <= 1e-9 * max(abs(x), 1e-30):
        return rows[-1][k]
    raise AssertionError("%s: x=%s outside the plot" % (plot.path, x))


class PymsCache(TempDir):
    """One PyMS cache for the whole class (a Verilog-A module compiles once, ~15 s)."""
    cache = ""

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.cache = tempfile.mkdtemp(prefix="vamos-test-pyms-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.cache, ignore_errors=True)

    def setUp(self):
        super().setUp()
        os.environ["PYMS_CACHE"] = self.cache
        os.environ.pop("VAMOS_VERBOSE", None)


@needs_xyce
class TestRunE2E(PymsCache):
    """e2e 1-10 on Xyce through run(), against engine_expect.json (§11 T2 1-10)."""

    def check_case(self, case: str) -> None:
        exp_all = load_json(os.path.join(E2E, case, "engine_expect.json"))
        entries = [("deck.scs", exp_all.get("xyce"))] + [(a["deck"], a.get("xyce")) for a in exp_all.get("also", [])]
        for deck, exp in entries:
            if exp is None:
                continue
            with self.subTest(deck=deck):
                path = os.path.join(E2E, case, deck)
                if exp.get("status") == 2 and "refused" in exp:
                    with self.assertRaises(NoteError) as cm:
                        Chain(path)
                    msgs = [n.message for n in cm.exception.notes if n.severity == ERROR]
                    self.assertTrue(any(exp["refused"] in m for m in msgs), msgs)
                    continue
                ch = Chain(path)
                er, log, run_dir = run_chain(self, ch, "run_" + os.path.splitext(deck)[0])
                self.assertEqual(er.rc, exp["status"], log + "\n".join(er.log[-20:]))
                self.check(ch, er, exp)
                from vamos.spectre import results                         # what results.collect reads
                notes: list = []
                res = list(results.collect(ch.plan, ch.sigmap, er, "modern", notes=notes))
                self.assertTrue(res)
                self.assertEqual([r.key for r in res if r.status != "ok"], [])
                self.assertEqual([n.message for n in notes if n.severity == "warning"], [])
                for k, d in enumerate(ch.steps):            # the decks Xyce ran are the chain's goldens
                    gold = os.path.join(E2E, case, "render", "xyce", d.id + ".cir")
                    if deck == "deck.scs" and os.path.exists(gold):
                        with open(gold) as g, open(os.path.join(run_dir, d.id + ".cir")) as fh:
                            self.assertEqual(fh.read(), g.read(), d.id)

    def check(self, ch: Chain, er: EngineResult, exp: dict) -> None:
        plots: Dict[str, List[rawfile.Raw]] = {}
        for sid, se in exp.get("steps", {}).items():
            self.assertEqual(er.status.get(sid), "ok", sid)
            self.assertTrue(os.path.isfile(er.files.get(sid, "")), sid)
            plots[sid] = _plots(er.files[sid])
            for sname, col in se.get("columns", {}).items():
                self.assertEqual(ch.column(er.names, sid, sname), col)
                plots[sid][0].exact(col)
        for chk in exp.get("checks", []):
            label = json.dumps(chk)
            sid = chk["step"]
            if "plots" in chk:
                self.assertEqual(len(plots[sid]), chk["plots"], label)
                continue
            plot = plots[sid][chk.get("plot", 0)]
            if "points" in chk:
                self.assertEqual(len(plot.points), chk["points"], label)
                continue
            rel, abs_ = chk.get("rel", 1e-6), chk.get("abs", 0.0)
            if "equal" in chk:
                vals = [_value(plot, ch.column(er.names, sid, s), chk) for s in chk["equal"]]
                for v in vals[1:]:
                    self.assertTrue(_close(v, vals[0].real if isinstance(vals[0], complex) else vals[0], rel, abs_),
                                    "%s: %s" % (label, vals))
                continue
            col = chk["col"] if "col" in chk else ch.column(er.names, sid, chk["signal"])
            got = _value(plot, col, chk)
            self.assertTrue(_close(got, chk["value"], rel, abs_), "%s: got %r" % (label, got))


def _e2e_test(case):
    def test(self):
        self.check_case(case)
    test.__name__ = "test_" + case
    test.__doc__ = "e2e %s on Xyce through run_xyce.run (§11 T2 %d)" % (case, int(case[:2]))
    return test


for _case in CASES:
    setattr(TestRunE2E, "test_" + _case, _e2e_test(_case))


NANC_VA = """`include "disciplines.vams"
// test_spectre_run_xyce: a source that goes NaN once node c passes 0.5 V (PyMS has no $abstime), so a
// tran fails part-way where the RC node crosses (E66's pattern on Xyce), vamos-authored
module nanc(a, c);
  inout a, c; electrical a, c;
  analog V(a) <+ (V(c) > 0.5) ? sqrt(-1.0 - V(a)*0) : 1.0;
endmodule
"""

NAN_HEAD = """// a Verilog-A NaN past the RC node's threshold: the tran fails at 0.69 us for r = 1k
simulator lang=spectre
ahdl_include "nanc.va"
parameters rval=1k
Vs (in 0) vsource type=pulse val0=0 val1=1 delay=0 rise=1n fall=1n width=1 period=2
R1 (in c) resistor r=rval
C1 (c 0) capacitor c=1n
N1 (a c) nanc
R2 (a 0) resistor r=1k
save a c
"""


@needs_xyce
class TestRunXyceFailures(PymsCache):
    """§7.2's failure rules on the real Xyce."""

    def test_a_singular_op_is_a_failed_analysis(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", """// two parallel sources: a singular operating point
simulator lang=spectre
V1 (a 0) vsource dc=1
V2 (a 0) vsource dc=2
R1 (a 0) resistor r=1k
op1 dc
"""))
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 1, log)
        self.assertEqual(er.status, {"vamos_a1": "failed"})
        self.assertNotIn("vamos_a1", er.files)                            # Xyce writes no print file [E13]
        self.assertIn("analysis `op1' failed in Xyce (exit status 1", log)

    @needs_pyms
    def test_a_tran_failing_part_way_among_good_analyses(self):
        write_deck(self.tmp, "nanc.va", NANC_VA)
        ch = Chain(write_deck(self.tmp, "deck.scs", NAN_HEAD + "op1 dc\ntr1 tran stop=3u\nop2 dc\n"))
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 1, log + "\n".join(er.log))
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed", "vamos_a3": "ok"})
        plots = rawfile.scan(er.files["vamos_a2"])
        self.assertEqual([(p.declared, p.n) for p in plots], [(None, 0)])  # header only [E66]
        self.assertIn("analysis `tr1' failed in Xyce (exit status 1", log)
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "nanc.va")))   # beside the decks [E104]
        with open(os.path.join(run_dir, "vamos_a1.cir")) as fh:
            self.assertIn('.hdl "nanc.va"', fh.read())
        op = rawfile.read(er.files["vamos_a3"])
        self.assertAlmostEqual(op.points[0][op.exact("V(A)")], 1.0, delta=0.01)    # PyMS: 1.001 (measured)

    @needs_pyms
    def test_a_step_point_failing_part_way(self):
        write_deck(self.tmp, "nanc.va", NANC_VA)
        ch = Chain(write_deck(self.tmp, "deck.scs", NAN_HEAD + "swp sweep param=rval values=[10k 1k 20k] {\n"
                              "  tr1 tran stop=3u\n}\nop2 dc\n"))
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 1, log + "\n".join(er.log))
        self.assertEqual(er.status, {"vamos_a1": "failed", "vamos_a2": "ok"})
        self.assertEqual(er.failed_points, {"vamos_a1": 2})               # Xyce went on and exited 0
        self.assertIn("restarts its time axis", log)
        self.assertEqual(len(rawfile.scan(er.files["vamos_a1"])), 2)
        from vamos.spectre import results                                 # the leaves before it are written
        status = {r.key: r.status for r in results.collect(ch.plan, ch.sigmap, er, "modern", notes=[])}
        self.assertEqual(status["swp-000_tr1-tran"], "ok")
        self.assertEqual(status["swp-001_tr1-tran"], "failed")
        self.assertEqual(status["swp-002_tr1-tran"], "failed")
        self.assertEqual(status["op2-dc"], "ok")

    def test_an_alter_of_a_parameter_xyce_does_not_know(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", ALTER_TLEV))
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 2, log + "\n".join(er.log))
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed"})
        self.assertIn("alter `a1' sets a parameter Xyce does not know", log)
        self.assertIn("TLEV", log)

    def test_a_sweep_of_a_parameter_xyce_does_not_know(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", SWEEP_TLEV))
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 2, log + "\n".join(er.log))
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed"})
        self.assertIn("sweep `swp' sets a parameter Xyce does not know", log)


LONG = """// a long tran for the signal tests (E72's circuit)
simulator lang=spectre
V1 (in 0) vsource type=sine ampl=1 freq=1k
R1 (in out) resistor r=1k
C1 (out 0) capacitor c=1u
op1 dc
tr1 tran stop=%s step=1e-7 maxstep=1e-7
op2 dc
save out
"""


class Sender(threading.Thread):
    """Sends signals to this process once the second deck runs (its log names `trigger`; None:
    its log exists, which run_xyce creates just before Xyce starts), `delay` seconds later, then
    waits for the run to end; a run that outlives `limit` seconds after the signals gets its Xyce
    killed (reap).  Xyce's output to its log is block-buffered: a trigger line shows up when a
    buffer fills (a tran's progress lines fill it at once; a noise analysis prints none)."""

    def __init__(self, run_dir: str, sigs: List[int], limit: float = 90.0, gap: float = 0.0,
                 trigger: Optional[str] = "Beginning Transient Calculation", delay: float = 0.3) -> None:
        super().__init__(daemon=True)
        self.run_dir, self.sigs, self.limit, self.gap = run_dir, sigs, limit, gap
        self.trigger, self.delay = trigger, delay
        self.done = threading.Event()
        self.sent = False

    def run(self) -> None:
        log = os.path.join(self.run_dir, "vamos_a2.log")
        t0 = time.time()
        while time.time() - t0 < 120:
            try:
                with open(log) as fh:
                    if self.trigger is None or self.trigger in fh.read():
                        break
            except OSError:
                pass
            time.sleep(0.02)
        else:
            reap(self.run_dir)
            return
        time.sleep(self.delay)
        for s in self.sigs:
            os.kill(os.getpid(), s)
            if self.gap:
                time.sleep(self.gap)
        self.sent = True
        if not self.done.wait(self.limit):
            reap(self.run_dir)


@needs_xyce
class TestRunXyceSignals(PymsCache):
    """§2.7 and stop.py on the real Xyce: the first signal is passed on as SIGTERM, the run is
    converted as status 3; a second one raises stop.Stop; SIGUSR1/SIGUSR2 only report."""

    SIGS = (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1, signal.SIGUSR2)

    def setUp(self):
        super().setUp()
        for s in self.SIGS:
            if signal.getsignal(s) == signal.SIG_IGN:
                self.skipTest("%s is ignored in this process (a background launch, E81)" % signal.Signals(s).name)
        # a signal that came after run() returned must not end the test process: a recording
        # handler outside run() (proc.Interrupts installs its own during the run and restores this)
        self.late: List[int] = []
        self.saved = {s: signal.signal(s, lambda signum, frame: self.late.append(signum)) for s in self.SIGS}

    def tearDown(self):
        for s, h in self.saved.items():
            signal.signal(s, h)
        super().tearDown()

    def interrupted(self, sig: int) -> None:
        ch = Chain(write_deck(self.tmp, "deck.scs", LONG % "0.5"))
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        snd = Sender(run_dir, [sig])
        snd.start()
        try:
            er, log, _ = run_chain(self, ch)
        finally:
            snd.done.set()
        self.assertTrue(snd.sent)
        self.assertEqual(er.rc, 3, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "interrupted"})   # op2 never ran
        plots = rawfile.scan(er.files["vamos_a2"])
        self.assertEqual(len(plots), 1)
        self.assertIsNone(plots[0].declared)                              # readable after a signal [E72]
        self.assertGreater(plots[0].n, 10)
        self.assertLess(plots[0].last, 0.5)
        self.assertEqual(len(rawfile.read(er.files["vamos_a2"]).points), plots[0].n)
        self.assertIn("analysis `tr1' was interrupted at time = ", log)
        self.assertIn(" (%s); its partial results (vamos_a2.raw) are written" % signal.Signals(sig).name, log)
        self.assertNotIn("vamos_a3.cir", "\n".join(er.log))
        self.assertEqual(self.late, [])
        from vamos.spectre import results                                 # the partial tran converts
        res = {r.key: r for r in results.collect(ch.plan, ch.sigmap, er, "modern", notes=[])}
        self.assertEqual(res["tr1-tran"].status, "interrupted")
        self.assertEqual(sum(1 for _ in res["tr1-tran"].rows()), plots[0].n)
        self.assertEqual(res["op1-dc"].status, "ok")

    def test_sigterm(self):
        self.interrupted(signal.SIGTERM)

    def test_sigint(self):
        self.interrupted(signal.SIGINT)

    def test_an_interrupted_noise_prn_is_converted(self):
        """§14 q.26, measured 2026-10-16: after SIGTERM Xyce's noise .prn holds complete rows (each
        line written whole, no `End of Xyce' line); a cut last line would be dropped."""
        ch = Chain(write_deck(self.tmp, "deck.scs", """// a long noise analysis (2.7e6 points, ~70 s)
simulator lang=spectre
V1 (in 0) vsource dc=0
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
C1 (out 0) capacitor c=1n
op1 dc
nz1 (out 0) noise iprobe=V1 start=1 stop=1G dec=300000
op2 dc
"""))
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        snd = Sender(run_dir, [signal.SIGTERM], trigger=None, delay=4.0)
        snd.start()
        try:
            er, log, _ = run_chain(self, ch)
        finally:
            snd.done.set()
        self.assertTrue(snd.sent)
        self.assertEqual(er.rc, 3, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "interrupted"})
        with open(er.files["vamos_a2"], "rb") as fh:
            data = fh.read()
        self.assertTrue(data.endswith(b"\n"))
        self.assertNotIn(b"End of Xyce", data)
        tables = rawfile.read_prn(er.files["vamos_a2"])
        self.assertEqual(len(tables), 1)
        self.assertGreater(len(tables[0].points), 100)
        self.assertLess(len(tables[0].points), 2700001)
        from vamos.spectre import results
        notes: list = []
        res = {r.key: r for r in results.collect(ch.plan, ch.sigmap, er, "modern", notes=notes)}
        self.assertEqual(res["nz1-noise"].status, "interrupted")
        self.assertEqual(sum(1 for _ in res["nz1-noise"].rows()), len(tables[0].points))
        self.assertEqual(res["op1-dc"].status, "ok")
        self.assertEqual(self.late, [])

    def test_a_second_signal_raises_stop(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", LONG % "0.5"))
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        snd = Sender(run_dir, [signal.SIGINT, signal.SIGTERM])
        snd.start()
        lf = LogFile(self.tmp)
        before = signal.getsignal(signal.SIGTERM)
        try:
            with self.assertRaises(stop.Stop) as cm:
                RX.run(ch.nl2, ch.plan, ch.sigmap, run_dir, lf.job, lf.log)
        finally:
            snd.done.set()
            lf.text()
        self.assertTrue(snd.sent)
        self.assertEqual(cm.exception.signum, signal.SIGTERM)
        self.assertIs(signal.getsignal(signal.SIGTERM), before)          # the handlers are restored
        self.assertEqual(self.late, [])
        self.assertEqual(reap(run_dir), [])                                # Xyce was killed

    def test_sigusr1_and_sigusr2_report_and_the_run_goes_on(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", LONG % "0.03"))
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        snd = Sender(run_dir, [signal.SIGUSR1, signal.SIGUSR2], gap=0.3)
        snd.start()
        try:
            er, log, _ = run_chain(self, ch)
        finally:
            snd.done.set()
        self.assertTrue(snd.sent)
        self.assertEqual(er.rc, 0, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "ok", "vamos_a3": "ok"})
        self.assertIn("spectre status: analysis `tr1' running, Xyce deck vamos_a2.cir (2 of 3)", log)
        self.assertIn("checkpoint not supported: SIGUSR2 is ignored and the run continues", log)
        self.assertIn("Notice from spectre during transient analysis `tr1'.", log)
        self.assertEqual(self.late, [])


if __name__ == "__main__":
    unittest.main()
