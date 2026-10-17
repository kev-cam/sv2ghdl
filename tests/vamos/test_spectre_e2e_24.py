"""e2e 24 (docs/VAMOS_SPECTRE_DESIGN.md §11.2 item 24, §3.9's defaults table): a diode with `cjsw`
and `fc=0.3` but no `fcs`.  Spectre's `fcs` defaults to `fc` [M ref5 p.305], both targets' to 0.5, so
vamos writes `fcs=0.3` on the card (with its note).  The deck biases the diode at eight voltages with
an ac analysis at 1 MHz: the only charge is the sidewall junction's (cjo=0, tt=0), so V1's AC current
is -(g + j w Csw(V)) per volt and Csw(V) = -Im(V1:p) / (2 pi f).  Above fcs*vjsw Spectre's depletion
capacitance is the linear extrapolation of (1 - V/vjsw)^-mjsw from fcs (the SPICE form):

    Csw(V) = cjsw*perim * (1 - V/vjsw)^-mjsw                                     V <  fcs*vjsw
    Csw(V) = cjsw*perim / (1 - fcs)^(1 + mjsw) * (1 - fcs*(1 + mjsw) + mjsw*V/vjsw)  V >= fcs*vjsw

expect.json holds the values at five biases; this module checks the formula at every bias on both
engines (an analytic check, 1e-6), which expect.json cannot say, and that the targets' fcs=0.5 would
fail it.  Measured on the phase-1b chain (2026-10-16, VACASK 0.3.4-91, Xyce DEVELOPMENT-202610100047):
both engines equal the formula to 1e-9.

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_e2e_24
"""

import math
import os
import shutil
import unittest

import psfcheck
from spectre_testlib import SPECTRE_FIXTURES, SpectreCase, needs_flow

CASE = os.path.join(SPECTRE_FIXTURES, "e2e", "24_diode_fcs")
CJSW, VJSW, MJSW, FC, FREQ = 2e-12, 0.8, 0.5, 0.3, 1e6        # cjsw=1p on perim=2
BIASES = [-1.0, 0.0, 0.2, 0.24, 0.3, 0.4, 0.5, 0.6]


def csw(v: float, fcs: float = FC) -> float:
    """Spectre's sidewall depletion capacitance at the bias v (the module docstring's formula)."""
    if v < fcs * VJSW:
        return CJSW * (1.0 - v / VJSW) ** (-MJSW)
    return CJSW / (1.0 - fcs) ** (1.0 + MJSW) * (1.0 - fcs * (1.0 + MJSW) + MJSW * v / VJSW)


class TestFormula(unittest.TestCase):
    """Both legs, no engine: the oracle itself."""

    def test_continuous_at_the_threshold_and_sensitive_to_fcs(self):
        v0 = FC * VJSW
        self.assertAlmostEqual(csw(v0 - 1e-12) / csw(v0), 1.0, places=9)
        # the deck tells fcs=0.3 from the targets' own 0.5: 0.45 % at 0.3 V, 12 % at 0.6 V
        self.assertGreater(csw(0.3, 0.5) / csw(0.3) - 1.0, 4e-3)
        self.assertGreater(csw(0.6, 0.5) / csw(0.6) - 1.0, 0.1)
        self.assertEqual(csw(0.2, 0.5), csw(0.2))                 # below both thresholds they agree


class TestE2e24(SpectreCase):
    """WSL: the deck through vamos -spectre on each installed engine; cv.ac read with psfcheck."""

    def run_case(self, engine: str) -> "psfcheck.PsfFile":
        d = os.path.join(self.tmp, engine)
        shutil.copytree(CASE, d)
        self.run_spectre(d, "deck.scs", engine=engine)
        return psfcheck.check(os.path.join(d, "deck.raw", "cv.ac"))

    @needs_flow
    def test_sidewall_capacitance_follows_spectre_fcs(self):
        engines = self.engines()
        if not engines:
            self.skipTest("needs VACASK or Xyce (Linux/WSL)")
        for engine in engines:
            with self.subTest(engine=engine):
                pf = self.run_case(engine)
                self.assertEqual(pf.sweep.name, "dc")
                self.assertEqual([float(v) for v in pf.sweep_values()], BIASES)
                for v, i in zip(pf.sweep_values(), pf.column("V1:p")):
                    c = -complex(i).imag / (2.0 * math.pi * FREQ)
                    self.assertLess(abs(c / csw(v) - 1.0), 1e-6, "Csw(%g) = %r, Spectre's %r" % (v, c, csw(v)))
                    if v > FC * VJSW:
                        self.assertGreater(abs(c / csw(v, 0.5) - 1.0), 3e-3, "Csw(%g) follows fcs=0.5" % v)


if __name__ == "__main__":
    unittest.main()
