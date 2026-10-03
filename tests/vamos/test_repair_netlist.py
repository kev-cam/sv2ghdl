"""Netlist fixes of the repair round: two engine defects vamos now writes around.

- Xyce's diode (N_DEV_Diode.C, `if (tJctCap != 0.0)') computes no junction charge at all when
  CJO is 0: a sidewall capacitance (CJSW with PJ) and the transit-time charge TT*Id were lost,
  silently.  tables.model_params writes cjo=1e-30 on Xyce for such a card (a note says so);
  the area junction this adds is negligible.  VACASK computes both charges with CJO = 0 and
  gets no change.
- VACASK's sp_mos3 gives NaN with KAPPA exactly 0 (the Star-HSPICE manual's own LEVEL 3 example
  card failed: "NaN found in vector ... Homotopy failed"): kappa=0 is written as 1e-12 there.

    python3 -m unittest discover -s tests/vamos -p 'test_repair_netlist.py' -v

The unit classes run anywhere; the engine class needs VACASK and Xyce (Linux/WSL).
"""

from __future__ import annotations

import os
import unittest

from vamos_testlib import TempDir, needs_vacask, needs_xyce, openvaf_bin, run, vacask_bin, xyce_bin, xyce_env

from vamos.netlist import rawfile, spice, tables as T, vacask, xyce  # noqa: E402
from vamos.netlist.expr_ast import Num  # noqa: E402
from vamos.netlist.ir import Model, ParseOpts  # noqa: E402


def card(level=1, **params):
    return Model("dm", "d", float(level), {k: Num(v) for k, v in params.items()}, origin="d.sp:3")


class TestXyceDiodeCharge(unittest.TestCase):
    def params(self, m, engine):
        out, notes = T.model_params(m, engine)
        return dict(out), [n.message for n in notes]

    def test_sidewall_only(self):
        p, notes = self.params(card(level=1, cjsw=1e-12, pb=0.7), "xyce")
        self.assertEqual(p["cjo"].value, 1e-30)
        self.assertTrue(any("cjo=1e-30 written for Xyce" in n and "sidewall capacitance (CJSW)" in n
                            for n in notes), notes)
        p, notes = self.params(card(level=1, cjsw=1e-12, pb=0.7), "vacask")
        self.assertNotIn("cjo", p)
        self.assertFalse(any("1e-30" in n for n in notes))

    def test_transit_time_only_and_explicit_zero(self):
        p, notes = self.params(card(level=1, tt=1e-8), "xyce")
        self.assertEqual(p["cjo"].value, 1e-30)
        self.assertTrue(any("transit-time charge (TT)" in n for n in notes), notes)
        p, _ = self.params(card(level=1, tt=1e-8, cj0=0.0), "xyce")
        self.assertEqual(p["cjo"].value, 1e-30)
        self.assertNotIn("cj0", p)

    def test_untouched(self):
        for kw in ({"cjo": 1e-12, "tt": 1e-8}, {"is": 1e-14}, {"cjsw": 0.0, "tt": 0.0}):
            with self.subTest(card=kw):
                p, notes = self.params(card(level=1, **kw), "xyce")
                self.assertNotEqual(p.get("cjo", Num(0.0)).value, 1e-30)
                self.assertFalse(any("1e-30" in n for n in notes))


class TestVacaskMos3Kappa(unittest.TestCase):
    def test_kappa_zero(self):
        m = Model("nch", "nmos", 3.0, {"vto": Num(0.8), "kappa": Num(0.0)}, origin="m.sp:4")
        out, notes = T.model_params(m, "vacask")
        self.assertEqual(dict(out)["kappa"].value, 1e-12)
        self.assertTrue(any("kappa=0 written as kappa=1e-12 for VACASK" in n.message for n in notes))
        out, notes = T.model_params(m, "xyce")
        self.assertEqual(dict(out)["kappa"].value, 0.0)
        self.assertFalse(any("kappa=1e-12" in n.message for n in notes))

    def test_other_kappas_untouched(self):
        for params in ({"kappa": Num(0.2)}, {"vto": Num(0.7)}):
            out, notes = T.model_params(Model("nch", "nmos", 3.0, params), "vacask")
            self.assertNotEqual(dict(out).get("kappa", Num(0.2)).value, 1e-12)


class TestMosJunctionDefaults(unittest.TestCase):
    """HSPICE's MOS bulk-junction defaults (Star-HSPICE 20-27/20-28): MJSW 0.33 is written where
    a card gives CJSW (sp_mos1/2 and Xyce LEVEL 1/2 default it to 0.5); the default CJ is not
    written (the manual gives two values) but is a warning where an instance gives AD/AS."""

    def test_mjsw_default(self):
        from vamos.netlist.ir import Netlist
        m = Model("n1", "nmos", 1.0, {"vto": Num(0.7), "cjsw": Num(1e-10)}, origin="m.sp:2")
        for engine in ("vacask", "xyce"):
            out, notes = T.model_params(m, engine)
            self.assertEqual(dict(out)["mjsw"].value, 0.33)
            self.assertTrue(any("mjsw=0.33 (HSPICE's default MJSW)" in n.message for n in notes), notes)
        out, _ = T.model_params(Model("n1", "nmos", 1.0, {"cjsw": Num(1e-10), "mjsw": Num(0.5)}), "vacask")
        self.assertEqual(dict(out)["mjsw"].value, 0.5)
        out, _ = T.model_params(m, "vacask", options=Netlist(options={"spice": Num(1.0)}).options)
        self.assertNotIn("mjsw", dict(out))

    def test_cj_default_is_a_warning_where_it_matters(self):
        from vamos.netlist.ir import Instance
        m = Model("n1", "nmos", 1.0, {"vto": Num(0.7)}, origin="m.sp:2")
        with_area = Instance("m1", "m", ["d", "g", "0", "0"], master="n1", params={"ad": Num(1e-12)})
        no_area = Instance("m2", "m", ["d", "g", "0", "0"], master="n1")
        (w,) = T.mos_junction_warnings(with_area, m)
        self.assertEqual((w.severity, w.origin), ("warning", "m.sp:2"))
        self.assertIn("no CJ on a MOS LEVEL 1 card whose instances give AD/AS", w.message)
        self.assertEqual(T.mos_junction_warnings(no_area, m), [])
        self.assertEqual(T.mos_junction_warnings(with_area, Model("n1", "nmos", 1.0, {"CJ": Num(3e-4)})), [])
        self.assertEqual(T.mos_junction_warnings(with_area, m, {"spice": Num(1.0)}), [])
        self.assertEqual(T.mos_junction_warnings(with_area, Model("n9", "nmos", 54.0, {})), [])

    def test_emitters_warn_once_per_card(self):
        import shutil
        import tempfile
        text = ("mos junctions\nvd d 0 1\nvg g 0 1\nm1 d g 0 0 n1 w=1u l=1u ad=1p as=1p\n"
                "m2 d g 0 0 n1 w=1u l=1u ad=2p as=2p\n.model n1 nmos level=1 vto=0.7\n.tran 1n 10n\n.end\n")
        d = tempfile.mkdtemp(prefix="vamos-mosj-")
        try:
            sp = os.path.join(d, "m.sp")
            with open(sp, "w") as fh:
                fh.write(text)
            nl = spice.parse([sp], [], d, ParseOpts())
            for render in (vacask.render, xyce.render):
                notes = []
                render(nl, notes=notes)
                warns = [n for n in notes if "no CJ on a MOS LEVEL 1 card" in n.message]
                self.assertEqual(len(warns), 1, [n.message for n in notes])
        finally:
            shutil.rmtree(d, ignore_errors=True)


MANUAL_LEVEL3 = """level 3 example of the Star-HSPICE manual
vd d 0 5
vg g 0 2
m1 d g 0 0 nch w=10u L=1u
.model nch nmos LEVEL=3 uo=600 tox=172.6572 vto=0.8 gamma=0.8 phi=0.64 capop=0 kappa=0 xj=0 nsub=1e16 rsh=0
.tran 1n 10n
.print tran i(vd)
.end
"""


SIDEWALL = """diode sidewall only
v1 a 0 pwl(0 -1 1u 0.6)
d1 a 0 dsw pj=1
.model dsw d is=1e-30 cjo=0 m=0.5 cjsw=1p mjsw=0.33 pb=0.7
.tran 1n 1u
.option delmax=1n
.print tran i(v1)
.end
"""

TRANSIT = """diode transit time only
v1 a 0 pwl(0 0.6 1u 0.7)
d1 a 0 dtt
.model dtt d is=1e-14 tt=10n
.tran 1n 1u
.option delmax=1n
.print tran i(v1)
.end
"""


@needs_vacask
@needs_xyce
class TestXyceDiodeChargeRuns(TempDir):
    def both(self, text, sub):
        p = self.write(sub + ".sp", text)
        nl = spice.parse([p], [], self.tmp, ParseOpts())
        out = {}
        d = os.path.join(self.tmp, sub + ".v")
        os.makedirs(d)
        with open(os.path.join(d, "deck.sim"), "w") as fh:
            fh.write(vacask.render(nl))
        r = run([vacask_bin(), "deck.sim"], cwd=d, env=dict(os.environ, SIM_OPENVAF=openvaf_bin()))
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        out["vacask"] = rawfile.read(os.path.join(d, "vamos_tran.raw"))
        d = os.path.join(self.tmp, sub + ".x")
        os.makedirs(d)
        with open(os.path.join(d, "deck.cir"), "w") as fh:
            fh.write(xyce.render(nl))
        r = run([xyce_bin(), "deck.cir"], cwd=d, env=xyce_env())
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        out["xyce"] = rawfile.read(os.path.join(d, xyce.RAW))
        return out

    def test_sidewall_only(self):
        raws = self.both(SIDEWALL, "dsw0")
        for vd in (-0.5, 0.4):
            # HSPICE (DCAP=2): C = CJSW*(1-v/PHP)^-MJSW below 0, CJSW*(1+MJSW*v/PHP) above; dv/dt = 1.6e6
            c = 1e-12 * ((1 - vd / 0.7) ** -0.33 if vd < 0 else (1 + 0.33 * vd / 0.7))
            t = (vd + 1.0) / 1.6e6
            for engine, raw in raws.items():
                with self.subTest(engine=engine, vd=vd):
                    self.assertAlmostEqual(-raw.at("i(v1)", t), c * 1.6e6, delta=5e-3 * c * 1.6e6)

    def test_manual_level3_kappa_zero(self):
        raws = self.both(MANUAL_LEVEL3, "l3k0")
        for engine, raw in raws.items():
            with self.subTest(engine=engine):
                self.assertAlmostEqual(-raw.at("i(vd)", 5e-9), 6.912e-4, delta=1e-3 * 6.912e-4)

    def test_transit_time_only(self):
        raws = self.both(TRANSIT, "dtt")
        for t in (0.5e-6, 0.9e-6):
            a, b = -raws["vacask"].at("i(v1)", t), -raws["xyce"].at("i(v1)", t)
            self.assertAlmostEqual(a, b, delta=5e-3 * abs(a), msg="t=%g: VACASK %r, Xyce %r" % (t, a, b))


if __name__ == "__main__":
    unittest.main()
