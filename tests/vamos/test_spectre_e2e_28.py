"""e2e 28 (docs/VAMOS_SPECTRE_DESIGN.md §11.2 item 28, E101): a real Spectre run, rebuilt.  psf-parser's
tests/data is one Spectre 23.1.0.242 run of the design "#RC Low-Pass Filter", vendored under
fixtures/spectre/psf/real/; deck.scs reconstructs its netlist from those outputs, without the
montecarlo block (an error in v1) and the info statements (a note, no file), and every vamos run of it
is compared with the real run:

  * structurally, through compare_results_dir (S4's checker, spectre_testlib's once phase 2 moves it
    there): the set of files and their names; logFile's keys, parents, PROPs and order; each file's
    header keys and values, TYPE list, TRACE names and order, SWEEP variable, units and grid, and the
    descriptions, except version and date; the left-out entries are expect.json's real.omit;
  * by value: the operating point (in = out = 1 V), the dc sweeps and every ac point against the real
    files (both are exact here: the dc and ac answers of a linear RC are the analytic ones, 1e-13 on
    both engines, measured), and the transient's in(t) = 1 + sin(2 pi 1 kHz t) at every point the engine
    wrote (5e-13 measured); out(t) against the analytic RC response within the moderate preset's
    accuracy: the real run itself is up to 0.024 V off it (its first steps), 3.5e-3 V after 0.4 s, and
    its out(0.5 s) is 0.84796 against the analytic 0.84478 (VACASK 0.84710, Xyce 0.84490).

The deck's points per decade come from the real files: myac.ac holds 21 points 10^0.3 apart (log=20),
the ac1/ac2 leaves 121 points 10^0.05 apart (dec=20).  Every real file traces in and out only, which
allpub (§6.1, ref19 p.188) would extend by Vin:p, so the deck saves them explicitly.

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_e2e_28
"""

import json
import math
import os
import shutil
import unittest

import psfcheck
from spectre_testlib import SPECTRE_FIXTURES, SpectreCase, needs_flow

try:                                                   # §12 phase 2 moves it into spectre_testlib
    from spectre_testlib import compare_results_dir
except ImportError:                                    # until then it is S4's, in test_spectre_outputs
    from test_spectre_outputs import compare_results_dir

CASE = os.path.join(SPECTRE_FIXTURES, "e2e", "28_real_rc")
W = 2.0 * math.pi * 1e3


def load_expect() -> dict:
    with open(os.path.join(CASE, "expect.json"), encoding="utf-8") as fh:
        return json.load(fh)


def real_paths():
    real = load_expect()["real"]
    return (os.path.normpath(os.path.join(CASE, real["dir"])), os.path.normpath(os.path.join(CASE, real["logfile"])),
            list(real["omit"]))


def out_rc(t: float, r: float = 1e3, c: float = 1e-6) -> float:
    """out(t) of the RC low-pass for in(t) = 1 + sin(w t) from out(0) = 1 (the operating point)."""
    tau = r * c
    a, ph = 1.0 / math.sqrt(1.0 + (W * tau) ** 2), -math.atan(W * tau)
    return 1.0 + a * math.sin(W * t + ph) - a * math.sin(ph) * math.exp(-t / tau)


def close(a, b, rel: float, abs_: float = 0.0) -> bool:
    a, b = complex(a), complex(b)
    return abs(a - b) <= abs_ + rel * abs(b)


class TestDeck(unittest.TestCase):
    """Both legs, no engine: the expectation files agree with the real run they describe."""

    def test_real_paths_and_omitted_entries(self):
        ascii_dir, logfile, omit = real_paths()
        self.assertTrue(os.path.isdir(ascii_dir), ascii_dir)
        log = psfcheck.check_logfile(logfile, datadir=ascii_dir)
        keys = log.keys()
        for k in omit:
            self.assertIn(k, keys)
        kept = [e for e in log.entries if e.key not in omit]
        self.assertEqual(sorted([e.data_file for e in kept] + ["logFile"]), sorted(load_expect()["files"]))
        self.assertTrue(all(not k.startswith(("myinfo", "mymonte")) and k not in ("dc2-dc", "tran1-tran")
                            for k in (e.key for e in kept)))

    def test_the_deck_has_the_real_grids(self):
        ascii_dir, _logfile, _omit = real_paths()
        myac = psfcheck.check(os.path.join(ascii_dir, "myac.ac"))
        xs = myac.sweep_values()
        self.assertEqual(len(xs), 21)                                       # log=20 over six decades
        self.assertTrue(all(close(b / a, 10.0 ** 0.3, 1e-12) for a, b in zip(xs, xs[1:])))
        ac1 = psfcheck.check(os.path.join(ascii_dir, "mysweep-000_ac1.ac"))
        self.assertEqual(len(ac1.sweep_values()), 121)                      # dec=20
        with open(os.path.join(CASE, "deck.scs"), encoding="utf-8") as fh:
            deck = fh.read()
        self.assertTrue(deck.startswith("#RC Low-Pass Filter\n"))          # the "design" of every real file
        self.assertIn("myac ac start=1 stop=1M log=20", deck)
        self.assertEqual(deck.count("dec=20"), 2)


class TestE2e28(SpectreCase):
    """WSL: the deck through vamos -spectre on each installed engine against the real run."""

    def run_case(self, engine: str) -> str:
        d = os.path.join(self.tmp, engine)
        shutil.copytree(CASE, d)
        self.run_spectre(d, "deck.scs", engine=engine)
        return os.path.join(d, "deck.raw")

    def each_engine(self):
        engines = self.engines()
        if not engines:
            self.skipTest("needs VACASK or Xyce (Linux/WSL)")
        return engines

    @needs_flow
    def test_structure_equals_the_real_run(self):
        ascii_dir, logfile, omit = real_paths()
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                raw = self.run_case(engine)
                self.assertEqual(compare_results_dir(raw, ascii_dir, omit_keys=omit, want_logfile=logfile), [])

    @needs_flow
    def test_values_equal_the_real_run(self):
        ascii_dir, logfile, omit = real_paths()
        log = psfcheck.check_logfile(logfile, datadir=ascii_dir)
        files = [e.data_file for e in log.entries if e.key not in omit]
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                raw = self.run_case(engine)
                for f in files:
                    want = psfcheck.check(os.path.join(ascii_dir, f))
                    got = psfcheck.check(os.path.join(raw, f))
                    if f == "mytran.tran.tran":
                        self.check_tran(got, want)
                        continue
                    if not want.swept:
                        for n in want.names():
                            self.assertTrue(close(got.value(n), want.value(n), 1e-9), "%s %s" % (f, n))
                        continue
                    self.assertEqual(len(got.sweep_values()), len(want.sweep_values()), f)
                    for a, b in zip(got.sweep_values(), want.sweep_values()):
                        self.assertTrue(close(a, b, 1e-9, 1e-15), "%s: sweep %r != %r" % (f, a, b))
                    for n in want.names():
                        for a, b in zip(got.column(n), want.column(n)):
                            self.assertTrue(close(a, b, 1e-6, 1e-15), "%s %s: %r != %r" % (f, n, a, b))

    def check_tran(self, got, want):
        ts, ins, outs = got.sweep_values(), got.column("in"), got.column("out")
        self.assertEqual((ts[0], ins[0], outs[0]), (0.0, 1.0, 1.0))
        self.assertTrue(close(ts[-1], 0.5, 1e-12))
        self.assertLess(max(abs(i - 1.0 - math.sin(W * t)) for t, i in zip(ts, ins)), 1e-9)
        self.assertLess(max(abs(o - out_rc(t)) for t, o in zip(ts, outs)), 0.03)
        self.assertLess(max(abs(o - out_rc(t)) for t, o in zip(ts, outs) if t > 0.4), 0.01)
        self.assertTrue(close(outs[-1], want.column("out")[-1], 5e-3), "out(0.5 s) %r, real %r"
                        % (outs[-1], want.column("out")[-1]))


if __name__ == "__main__":
    unittest.main()
