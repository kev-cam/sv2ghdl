"""e2e 25 (docs/VAMOS_SPECTRE_DESIGN.md §11.2 item 25): an ADE-style netlist with the subckt instance
I2 (ADE names instances I0, I2, ...): an explicit `save I2.Mid` under `saveOptions options
save=allpub`, `ic I2.Mid=0.3`, and a noise analysis whose per-instance STRUCTs include `I2.r3`.  On
Xyce the instance prints as XI2, so its node is V(XI2:MID), the ic `.IC V(XI2:MID)=0.3` and the noise
DNO(XI2:R3) (§4.5 item 15, E84); engine_expect.json pins those columns for the phase-1b chain.

The circuit: V0 (1 V) - r3 (1k) - Mid - r4 (1k) - out - R0 (2k) - ground, C0 (1 nF) from Mid to ground.
  op: Mid = 0.75, out = 0.5 (the ic leaves the op alone: Spectre's ic is a transient initial condition)
  noise at out (1-100 Hz, C0 negligible): onoise = 4kT * (2k || 2k); R0 gives half, r3 and r4 a quarter
  each; gain = 0.5; in = out / gain
  tran: Mid(t) = 0.75 - 0.45 exp(-t/tau), tau = (r3 || (r4 + R0)) * C0 = 750 ns, out = 2/3 Mid

This module checks what expect.json cannot say: the noise file's TRACE order (the STRUCTs in IR
instance order, then out, gain, in; §6.4, §8.4), the STRUCT members per engine (VACASK: rn and total,
the resistor contribution renamed; Xyce: total only, with its note), and the transient against the
analytic relaxation at every point the engine wrote (no interpolation).

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_e2e_25
"""

import math
import os
import shutil
import unittest

import psfcheck
from spectre_testlib import SPECTRE_FIXTURES, SpectreCase, needs_engines, needs_flow

CASE = os.path.join(SPECTRE_FIXTURES, "e2e", "25_ade_instance")
FOURKT = 4.0 * 1.380649e-23 * 300.15
TAU = 750.0 * 1e-9


def mid(t: float) -> float:
    return 0.75 - 0.45 * math.exp(-t / TAU)


class TestE2e25(SpectreCase):
    """WSL: the deck through vamos -spectre on each installed engine; the PSF read with psfcheck."""

    def run_case(self, engine: str) -> str:
        d = os.path.join(self.tmp, engine)
        shutil.copytree(CASE, d)
        r = self.run_spectre(d, "deck.scs", engine=engine)
        self.output = r.stdout
        return os.path.join(d, "deck.raw")

    def each_engine(self):
        return self.engines()

    @needs_flow
    @needs_engines
    def test_noise_structs_and_their_members(self):
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                raw = self.run_case(engine)
                nz = psfcheck.check(os.path.join(raw, "noise.noise"))
                self.assertEqual(nz.names(), ["R0", "I2.r3", "I2.r4", "out", "gain", "in"])
                self.assertEqual([t.type for t in nz.traces[:3]], ["resistor"] * 3)
                members = ["rn", "total"] if engine == "vacask" else ["total"]
                for name, share in (("R0", 500.0), ("I2.r3", 250.0), ("I2.r4", 250.0)):
                    for row in nz.column(name):
                        self.assertEqual(list(row), members, name)
                        self.assertLess(abs(row["total"] / (FOURKT * share) - 1.0), 3e-5, name)
                        if engine == "vacask":
                            self.assertEqual(row["rn"], row["total"], name)
                out, gain, inp = nz.column("out"), nz.column("gain"), nz.column("in")
                for o, g, i in zip(out, gain, inp):
                    self.assertLess(abs(o / math.sqrt(FOURKT * 1e3) - 1.0), 3e-5)
                    self.assertLess(abs(g - 0.5), 1e-6)
                    self.assertLess(abs(i / (o / g) - 1.0), 1e-9)
                if engine == "xyce":
                    self.assertIn("carry only `total'", self.output)

    @needs_flow
    @needs_engines
    def test_ic_relaxation_and_an_untouched_op(self):
        for engine in self.each_engine():
            with self.subTest(engine=engine):
                raw = self.run_case(engine)
                op = psfcheck.check(os.path.join(raw, "dcOp.dc"))
                self.assertEqual(op.names(), ["I2.Mid", "net1", "out", "V0:p"])   # allpub, sorted; I2.Mid once
                self.assertAlmostEqual(op.value("I2.Mid"), 0.75, places=9)
                tr = psfcheck.check(os.path.join(raw, "tran.tran.tran"))
                ts, ms, outs = tr.sweep_values(), tr.column("I2.Mid"), tr.column("out")
                self.assertEqual(ts[0], 0.0)
                self.assertAlmostEqual(ts[-1], 5e-6, delta=1e-15)
                # the integration error at reltol 1e-3 (measured: 2.1e-4 V VACASK, 1.2e-4 V Xyce)
                worst = max(abs(m - mid(t)) for t, m in zip(ts, ms))
                self.assertLess(worst, 5e-4, "Mid(t) against 0.75 - 0.45 exp(-t/750 ns)")
                for m, o in zip(ms, outs):
                    self.assertAlmostEqual(o, m * 2.0 / 3.0, delta=1e-6)


if __name__ == "__main__":
    unittest.main()
