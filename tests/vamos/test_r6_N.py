"""Round 6, netlist and engines (N): HSPICE's MOS junction defaults, COX, the netlist open
items, and the engine fixes (VACASK devices, Xyce DeviceModelPKG).

    python3 -m unittest discover -s tests/vamos -p 'test_r6_N.py' -v

R6N-01  MOS LEVEL 1/2/3 bulk junctions (Star-HSPICE 2001.2, 20-26..20-48): CJ defaults to
        sqrt(eps_si*q*NSUB/(2*PB)) (option ASPEC=0), FC is not used (linear forward bias: FC=0),
        PHP is removed (the targets use PB; a different PHP on a sidewall junction is a warning);
        .option aspec is refused; LEVEL 49/53 HSPICE-junction defaults CJ=5.79e-4, CJSW=0, and
        Berkeley's defaults (no JS=0) with ACM=10.
R6N-05  Engine fix: VACASK sp_diode and Xyce's diode add the sidewall charge with the sidewall's
        own F1 (FCS, PHP, MJSW), not the area's: a DCAP=1 diode with FC=0.5 and FCS=0 aborted
        ("Timestep too small").
R6N-06  Engine fix + card: LEVEL 3 channel-length modulation is HSPICE's (21-26), SPICE2's: both
        targets get badmos3=1 (a new model parameter of VACASK sp_mos3 and Xyce MOSFET level 3);
        VACASK's sp_mos3 no longer gives NaN when KAPPA*NSUB is 0.

The unit classes run anywhere; the run classes need VACASK and Xyce (Linux/WSL) with the round-6
engine builds (SIM_MODULE_PATH / VAMOS_XYCE_LIBS select private ones).
"""

from __future__ import annotations

import math
import os
import unittest

from vamos_testlib import TempDir, have_vacask, have_xyce, openvaf_bin, run, vacask_bin, xyce_bin, xyce_env

from vamos.netlist import rawfile, spice, tables as T, vacask, xyce  # noqa: E402
from vamos.netlist.expr_ast import Num  # noqa: E402
from vamos.netlist.ir import Instance, Model, ParseOpts  # noqa: E402
from vamos.notes import NoteError  # noqa: E402

needs_both = unittest.skipUnless(have_vacask() and have_xyce(), "needs VACASK and Xyce (Linux/WSL)")

EPS_SI = 1.035943139907e-10        # HSPICE's constants (21-29)
Q_E = 1.6021918e-19


def lines(text, origin="tb"):
    return [(l, "%s:%d" % (origin, k + 1)) for k, l in enumerate(text.strip("\n").split("\n"))]


def parse(text, cwd="."):
    return spice.parse([], lines(text), cwd, ParseOpts())


def card(name, kind, level, **params):
    return Model(name, kind, float(level), {k: Num(v) for k, v in params.items()}, origin="m.sp:1")


def mp(model, engine="vacask", **options):
    out, notes = T.model_params(model, engine, options={k: Num(v) for k, v in options.items()})
    return dict(out), notes


def sev(notes, severity):
    return [n.message for n in notes if n.severity == severity]


def cj_formula(nsub_cm3, pb=0.8):
    return math.sqrt(EPS_SI * Q_E * nsub_cm3 * 1e6 / (2 * pb))


class _Runs(TempDir):
    def vacask(self, nl, sub):
        d = os.path.join(self.tmp, sub + ".v")
        os.makedirs(d)
        with open(os.path.join(d, "deck.sim"), "w") as fh:
            fh.write(vacask.render(nl))
        r = run([vacask_bin(), "deck.sim"], cwd=d, env=dict(os.environ, SIM_OPENVAF=openvaf_bin()))
        self.assertEqual(r.returncode, 0, "VACASK failed:\n%s" % r.stdout[-3000:])
        return rawfile.read(os.path.join(d, "vamos_tran.raw"))

    def xyce(self, nl, sub):
        d = os.path.join(self.tmp, sub + ".x")
        os.makedirs(d)
        with open(os.path.join(d, "deck.cir"), "w") as fh:
            fh.write(xyce.render(nl))
        r = run([xyce_bin(), "deck.cir"], cwd=d, env=xyce_env())
        self.assertEqual(r.returncode, 0, "Xyce failed:\n%s" % r.stdout[-3000:])
        return rawfile.read(os.path.join(d, xyce.RAW))

    def both(self, nl, sub):
        return {"vacask": self.vacask(nl, sub), "xyce": self.xyce(nl, sub)}

    def check(self, raws, col, t, want, rel, sign=1.0):
        for engine, raw in raws.items():
            got = sign * raw.at(col, t)
            self.assertAlmostEqual(got, want, delta=rel * abs(want),
                                   msg="%s %s at %g: got %r, want %r" % (engine, col, t, got, want))


# -- R6N-01: MOS bulk junctions --------------------------------------------------------------------

class TestMosJunctionDefaults(unittest.TestCase):
    def test_cj_default_is_the_aspec0_formula(self):
        # NSUB given: the documented default for option ASPEC=0 (20-28), no warning
        p, notes = mp(card("n2", "nmos", 2, vto=0.7, kp=5e-5, tox=2e-8, nsub=1e16, capop=0))
        self.assertAlmostEqual(p["cj"].value, cj_formula(1e16), delta=1e-9 * cj_formula(1e16))
        self.assertEqual(sev(notes, "warning"), [])
        self.assertTrue(any("sqrt(eps_si*q*NSUB/(2*PB))" in n for n in sev(notes, "note")))
        # the card's PB (and its aliases) and the default NSUB 1e15 (LEVEL 1 without TOX too)
        p, _ = mp(card("n1", "nmos", 1, vto=0.7, kp=5e-5, phs=0.6))
        self.assertAlmostEqual(p["cj"].value, cj_formula(1e15, 0.6), delta=1e-9 * cj_formula(1e15))
        # NSUB derived from GAMMA (21-4): the derived one
        p, _ = mp(card("n3", "nmos", 3, vto=0.7, kp=5e-5, tox=2e-8, gamma=0.4, capop=0))
        nsub = (0.4 * 3.45314379969e-11 / 2e-8) ** 2 / (2 * Q_E * EPS_SI * 1e6)
        self.assertAlmostEqual(p["cj"].value, cj_formula(nsub), delta=1e-6 * cj_formula(nsub))

    def test_cj_not_written(self):
        for m, opts in ((card("a", "nmos", 2, vto=0.7, kp=5e-5, cj=3e-4), {}),         # given
                        (card("b", "nmos", 2, vto=0.7, kp=5e-5, cja=3e-4), {}),        # an alias
                        (card("c", "nmos", 2, vto=0.7, kp=5e-5, acm=1), {}),           # ACM=1: per width
                        (card("d", "nmos", 2, vto=0.7, kp=5e-5, gamma=0), {}),         # GAMMA=0: no NSUB
                        (card("e", "nmos", 2, vto=0.7, kp=5e-5), {"spice": 1.0})):     # SPICE: no NSUB
            with self.subTest(card=m.name):
                p, notes = mp(m, **opts)
                self.assertEqual(p.get("cj"), m.params.get("cj"))
                self.assertFalse(any("cj=" in n for n in sev(notes, "note")))
        # .option spice with NSUB given: SPICE2's rule (the same formula)
        p, _ = mp(card("f", "nmos", 2, vto=0.7, kp=5e-5, nsub=1e16), spice=1.0)
        self.assertAlmostEqual(p["cj"].value, cj_formula(1e16), delta=1e-9 * cj_formula(1e16))

    def test_fc_is_not_used(self):
        # any junction capacitance: fc=0, a given FC replaced (20-28: "not used"), on both engines
        for kw in ({"cj": 3e-4, "fc": 0.9}, {"cjsw": 1e-10}, {"cbd": 1e-14}, {"nsub": 1e16}):
            for engine in ("vacask", "xyce"):
                with self.subTest(card=kw, engine=engine):
                    p, notes = mp(card("n", "nmos", 2, vto=0.7, kp=5e-5, **kw), engine)
                    self.assertEqual(p["fc"], Num(0.0))
        p, notes = mp(card("n", "nmos", 2, vto=0.7, kp=5e-5, cj=3e-4, fc=0.9))
        self.assertTrue(any("fc=0.9 ignored" in n for n in sev(notes, "note")))
        p, _ = mp(card("n", "nmos", 2, vto=0.7, kp=5e-5, cj=0, fc=0.5), spice=1.0)
        self.assertEqual(p["fc"], Num(0.5))                                  # no capacitance: as given

    def test_php(self):
        # PHP equal to PB, or without a sidewall capacitance: removed, a note
        for kw in ({"cjsw": 1e-10, "pb": 0.7, "php": 0.7}, {"cj": 3e-4, "php": 0.6}):
            p, notes = mp(card("n", "nmos", 2, vto=0.7, kp=5e-5, capop=0, **kw))
            self.assertNotIn("php", p)
            self.assertEqual(sev(notes, "warning"), [], kw)
        # PHP other than PB on a sidewall junction: removed, a warning (simulated with PB)
        p, notes = mp(card("n", "nmos", 2, vto=0.7, kp=5e-5, cjsw=1e-10, php=0.6, capop=0))
        self.assertNotIn("php", p)
        (w,) = sev(notes, "warning")
        self.assertIn("php=0.6 differs from PB=0.8", w)

    def test_instance_warnings(self):
        m = card("n1", "nmos", 1, vto=0.7, kp=5e-5)
        area = Instance("m1", "m", ["d", "g", "0", "0"], master="n1", params={"as": Num(1e-12)})
        (w,) = T.mos_junction_warnings(area, m)
        self.assertIn("HSPICE's default CJ is ambiguous", w.message)
        self.assertIn("579.11 uF/m^2", w.message)
        # CBD/CBS with AD/AS: HSPICE uses CJ*AD (CJ defaults to nonzero), the targets CBD
        (w,) = T.mos_junction_warnings(area, card("n2", "nmos", 2, vto=0.7, nsub=1e16, cbs=1e-14))
        self.assertIn("CBS on a MOS LEVEL 2 card whose instances give AD/AS", w.message)
        self.assertEqual(T.mos_junction_warnings(area, card("n3", "nmos", 2, vto=0.7, cj=0, cbs=1e-14)), [])
        self.assertEqual(T.mos_junction_warnings(area, card("n4", "nmos", 2, vto=0.7, cbs=1e-14),
                                                 {"spice": Num(1.0)}), [])

    def test_bsim3_junction_defaults(self):
        # HSPICE's own junction model (LEVEL 49 default ACM=0, or ACM 0/2/3): CJ 5.79e-4, CJSW 0 (22-43)
        for m in (card("b", "nmos", 49, version=3.3), card("b", "nmos", 53, version=3.3, acm=2)):
            p, _ = mp(m)
            self.assertEqual((p["cj"], p["cjsw"]), (Num(5.79e-4), Num(0.0)), m.level)
        p, _ = mp(card("b", "nmos", 49, version=3.3, cj=1e-3, cjsw=1e-10))
        self.assertEqual((p["cj"], p["cjsw"]), (Num(1e-3), Num(1e-10)))
        # Berkeley's junctions (ACM=10, LEVEL 53's default): Berkeley's defaults, nothing written
        for m in (card("b", "nmos", 49, version=3.3, acm=10), card("b", "nmos", 53, version=3.3)):
            p, _ = mp(m)
            for k in ("cj", "cjsw", "js"):
                self.assertNotIn(k, p, m.level)

    def test_option_aspec_is_refused(self):
        with self.assertRaises(NoteError) as cm:
            parse(".option aspec\nr1 a 0 1k\nv1 a 0 1\n.tran 1n 10n")
        self.assertIn(".option aspec is not supported", str(cm.exception))
        parse(".option aspec=0\nr1 a 0 1k\nv1 a 0 1\n.tran 1n 10n")      # the default: fine


# Off MOSFETs whose drains ramp from 1 V to -0.5 V in 1 us: the drain current is the bulk-drain
# junction's C(vbd)*dvbd/dt, vbd = -vd.  The gate holds no drain charge: no TOX, or Meyer's model
# (CAPOP=0, no overlap) in accumulation, where Cgs = Cgd = 0 in either drain/source orientation.
JUNCTIONS = """
.option delmax=1n
vg g 0 -5
vd1 d1 0 pwl(0 1 1u -0.5)
m1 d1 g 0 0 n1 w=10u l=1u ad=100p
.model n1 nmos level=2 vto=0.7 kp=50u tox=2e-8 nsub=1e16 is=1e-30 capop=0
vd2 d2 0 pwl(0 1 1u -0.5)
m2 d2 g 0 0 n2 w=10u l=1u ad=100p
.model n2 nmos level=1 vto=0.7 kp=50u is=1e-30
vd3 d3 0 pwl(0 1 1u -0.5)
m3 d3 g 0 0 n3 w=10u l=1u ad=100p
.model n3 nmos level=2 vto=0.7 kp=50u tox=2e-8 nsub=1e16 cj=2e-4 mj=0.5 fc=0.9 is=1e-30 capop=0
.tran 1n 1u
.print tran i(vd1) i(vd2) i(vd3)
"""


@needs_both
class TestMosJunctionRuns(_Runs):
    def test_junction_capacitance(self):
        """HSPICE's C = C0*(1-v/PB)^-MJ below 0 V and C0*(1+MJ*v/PB) above (20-47, FC not used),
        C0 = CJ*AD with CJ = sqrt(eps_si*q*NSUB/(2*PB)) when not given: before round 6 both targets
        simulated CJ=0 (no current at all) and the given fc=0.9 (13% low at v=0.4)."""
        raws = self.both(parse(JUNCTIONS), "mj")
        slope = 1.5e6
        for col, cj in (("i(vd1)", cj_formula(1e16)), ("i(vd2)", cj_formula(1e15)), ("i(vd3)", 2e-4)):
            for v in (-0.6, -0.2, 0.2, 0.4):
                c = cj * 100e-12 * ((1 - v / 0.8) ** -0.5 if v < 0 else 1 + 0.5 * v / 0.8)
                t = (1.0 + v) / slope                                         # vbd = -vd = v
                self.check(raws, col, t, c * slope, rel=3e-3)


# -- R6N-05: diode sidewall charge --------------------------------------------------------------

SIDEWALL_FCS = """
.option dcap=1
v1 a 0 pwl(0 -1 1u 0.7)
d1 a 0 dsw pj=1
.model dsw d is=1e-30 cjo=1p m=0.5 vj=0.8 fc=0.5 cjsw=1p mjsw=0.33 php=0.6 fcs=0
.tran 1n 1u
.option delmax=1n
.print tran i(v1)
"""


def diode_cap(v, cj=1e-12, m=0.5, pb=0.8, fc=0.5, cjsw=1e-12, mjsw=0.33, php=0.6, fcs=0.0):
    """SPICE's depletion capacitance (DCAP=1), the area and the sidewall each with its own FC."""
    a = cj * (1 - v / pb) ** -m if v < fc * pb else \
        cj / (1 - fc) ** (1 + m) * (1 - fc * (1 + m) + m * v / pb)
    s = cjsw * (1 - v / php) ** -mjsw if v < fcs * php else \
        cjsw / (1 - fcs) ** (1 + mjsw) * (1 - fcs * (1 + mjsw) + mjsw * v / php)
    return a + s


@needs_both
class TestDiodeSidewallRuns(_Runs):
    def test_sidewall_fcs_differs_from_fc(self):
        """Before round 6 both engines added czeroSW*F1 (the area's F1) to the sidewall charge
        above FCS*PHP: a charge step of 0.47 pC at 0 V here, VACASK "Timestep too small", Xyce
        failed too."""
        raws = self.both(parse(SIDEWALL_FCS), "dsw")
        slope = 1.7e6
        for v in (-0.8, -0.3, -0.01, 0.01, 0.2, 0.39, 0.41, 0.6):
            self.check(raws, "i(v1)", (v + 1.0) / slope, diode_cap(v) * slope, rel=3e-3, sign=-1)
        for engine, raw in raws.items():                                      # no spike anywhere
            self.assertLess(max(abs(x) for x in raw.column("i(v1)")), 1.01 * diode_cap(0.7) * slope, engine)


# -- R6N-06: LEVEL 3 channel-length modulation ---------------------------------------------------

class TestLevel3Clm(unittest.TestCase):
    def test_badmos3_written(self):
        for engine in ("vacask", "xyce"):
            p, notes = mp(card("n3", "nmos", 3, vto=0.7, kp=5e-5, capop=0), engine)
            self.assertEqual(p["badmos3"], Num(1.0))
            self.assertTrue(any("badmos3=1" in n for n in sev(notes, "note")))
            p, _ = mp(card("n2", "nmos", 2, vto=0.7, kp=5e-5, capop=0), engine)
            self.assertNotIn("badmos3", p)


L3_DECK = """
vg g 0 1.7
vd d 0 2
m1 d g 0 0 na l=1u w=10u
.model na nmos level=3 vto=0.7 kp=50u tox=2e-8 nsub=1e16 gamma=0 phi=0.7 capop=0
vd2 d2 0 2
m2 d2 g 0 0 nv l=1u w=10u
.model nv nmos level=3 vto=0.7 kp=50u tox=2e-8 xj=0.2u nsub=1e16 gamma=0.4 phi=0.7 vmax=1.5e5 theta=0.05
+ eta=0.1 capop=0
vd3 d3 0 2
m3 d3 g 0 0 nk l=1u w=10u
.model nk nmos level=3 vto=0.7 kp=50u tox=2e-8 gamma=0 phi=0.7 capop=0
.tran 1n 10n
.print tran i(vd) i(vd2) i(vd3)
"""


@needs_both
class TestLevel3ClmRuns(_Runs):
    def test_hspice_channel_length_modulation(self):
        """21-26, VMAX=0: dL = Xd*sqrt(KAPPA*(vds-vdsat)), Xd^2 = 2*eps_si/(q*NSUB); here vdsat =
        vgs-vto = 1 (GAMMA=0), I = (KP/2)(W/L)*1^2/(1-dL/L) = 2.97910e-4 A.  VACASK's default (ngspice's
        modified CLM) gave 3.01414e-4 (+1.2%).  VMAX>0: Ep without KAPPA (SPICE2, SPICE3 badmos3):
        ngspice-45.2 with .options badmos3 gives 4.37142e-4 A; Xyce's SPICE3f KAPPA*Ep gave another
        value.  No NSUB: no CLM, and no NaN on VACASK (KAPPA*alpha = 0)."""
        raws = self.both(parse(L3_DECK), "l3")
        dl = math.sqrt(2 * 11.7 * 8.854214871e-12 / (Q_E * 1e22) * 0.2 * 1.0)
        self.check(raws, "i(vd)", 5e-9, 2.5e-4 / (1 - dl / 1e-6), rel=2e-4, sign=-1)
        self.check(raws, "i(vd2)", 5e-9, 4.37142e-4, rel=2e-4, sign=-1)
        self.check(raws, "i(vd3)", 5e-9, 2.5e-4, rel=1e-5, sign=-1)


# -- R6N-02: COX on a MOS 1/2/3 card ----------------------------------------------------------------

EPS_OX = 3.45314379969e-11


class TestCox(unittest.TestCase):
    def test_cox_becomes_tox(self):
        for kw in ({"cox": 3.453e-4}, {"co": 3.453e-4}, {"cox": 3.453e-4, "tox": 5e-8}):
            for engine in ("vacask", "xyce"):
                with self.subTest(card=kw, engine=engine):
                    p, notes = mp(card("n", "nmos", 2, vto=0.7, kp=5e-5, capop=0, **kw), engine)
                    self.assertNotIn("cox", p)
                    self.assertNotIn("co", p)
                    self.assertAlmostEqual(p["tox"].value, EPS_OX / 3.453e-4, delta=1e-20)
                    self.assertEqual(sev(notes, "warning"), [])
        _, notes = mp(card("n", "nmos", 2, vto=0.7, kp=5e-5, capop=0, cox=3.453e-4, tox=5e-8))
        self.assertTrue(any("the given tox=5e-08 is replaced" in n for n in sev(notes, "note")))
        # LEVEL 1: the Meyer capacitance (and KP=UO*COX) of a COX-derived TOX is not documented
        p, notes = mp(card("n1", "nmos", 1, vto=0.7, kp=5e-5, capop=0, cox=3.453e-4))
        self.assertIn("tox", p)
        (w,) = sev(notes, "warning")
        self.assertIn("cox=0.0003453 with LEVEL 1: HSPICE invokes the Meyer gate capacitance only when TOX is "
                      "specified", w)
        with self.assertRaises(T.TableError):
            mp(card("n0", "nmos", 2, vto=0.7, kp=5e-5, cox=0))


@needs_both
class TestCoxRuns(_Runs):
    def test_cox_card_equals_tox_card(self):
        """Both targets rejected COX (VACASK: unknown parameter; Xyce: no model parameter COX)."""
        deck = ("vg g 0 1.5\nvd d 0 2\nm1 d g 0 0 na l=1u w=10u\n.model na nmos level=2 vto=0.7 %s nsub=1e16 "
                "capop=0\n.tran 1n 10n\n.print tran i(vd)")
        a = self.both(parse(deck % "cox=1.7265e-3"), "cox")
        b = self.both(parse(deck % ("tox=%r" % (EPS_OX / 1.7265e-3))), "tox")
        for engine in a:
            self.assertAlmostEqual(a[engine].at("i(vd)", 5e-9), b[engine].at("i(vd)", 5e-9),
                                   delta=1e-9 * abs(b[engine].at("i(vd)", 5e-9)), msg=engine)


# -- R6N-03: netlist open items --------------------------------------------------------------------

class TestNetlistItems(unittest.TestCase):
    def test_stress_distances_under_scale(self):
        inst = Instance("m1", "m", ["d", "g", "0", "0"], master="n4", params={"sa": Num(0.5), "sd": Num(0.3)},
                        origin="tb:3")
        (w,) = T.mos_scale_warnings(inst, {"scale": Num(1e-6)})
        self.assertEqual(w.origin, "tb:3")
        self.assertIn("sa/sd under .option scale=1e-06", w.message)
        self.assertEqual(T.mos_scale_warnings(inst, {}), [])
        self.assertEqual(T.mos_scale_warnings(Instance("m2", "m", [], params={"sa": Num(0.0)}),
                                              {"scale": Num(1e-6)}), [])

    def test_bin_bounds_from_subckt_parameters(self):
        nl = parse(".subckt cell d g s b lmn=0.5u\n.model nch.1 nmos level=1 vto=0.7 kp=50u lmin=lmn lmax=10u "
                   "wmin=1u wmax=100u\nm1 d g s b nch w=10u l=1u\n.ends\nx1 d g 0 0 cell\nvd d 0 1\nvg g 0 1.5\n"
                   ".tran 1n 10n")
        for render in (vacask.render, xyce.render):
            with self.assertRaises(NoteError) as cm:
                render(nl)
            self.assertIn("bin bounds with the top-level parameters only", str(cm.exception))

    def test_node_names(self):
        # '.' is HSPICE's hierarchy separator (3-17): refused in a net name, kept in references
        with self.assertRaises(NoteError) as cm:
            parse("r1 a x1.n1 1k\nr2 x1.n1 0 1k\nv1 a 0 1\n.tran 1n 10n")
        self.assertIn("node name 'x1.n1' contains '.'", str(cm.exception))
        # ':' merges with an instance's internal node on both engines: refused where it would
        clash = (".subckt sub a\nr1 a n1 1k\nr2 n1 0 1k\n.ends\nx1 in sub\nv1 in 0 1\nv2 x1:n1 0 0.2\n"
                 "r3 x1:n1 0 1meg\n.tran 1n 10n\n")
        with self.assertRaises(NoteError) as cm:
            parse(clash)
        self.assertIn("node x1:n1: VACASK and Xyce also call the internal node n1 of instance x1", str(cm.exception))
        parse(clash.replace("x1:n1", "x1:n2"))                  # no such internal node: no clash
        parse(clash.replace("x1:n1", "a:n1"))                   # no instance a
        parse(".subckt sub a\nr1 a n1 1k\n.ends\nx1 in sub\nv1 in 0 1\n.tran 1n 10n\n.print tran v(x1.n1)")

    def test_pulse_defaults(self):
        """pw omitted -> TSTOP, per omitted -> no repetition within the run: SPICE3's, and HSPICE's own
        (B-2008.09 S&A: "pw ... Default=TSTOP", "per ... Default=TSTOP"; the 2001.2 manual's TSTEP is
        superseded)."""
        nl = parse("v1 a 0 pulse(0 1 1n 1n 1n)\nr1 a 0 1k\n.tran 1n 100n")
        (src,) = [it.source for it in nl.body if isinstance(it, Instance) and it.name == "v1"]
        self.assertEqual(src.args["pw"], Num(100e-9))
        self.assertNotIn("per", src.args)

    def test_current_references(self):
        base = ("v1 a 0 1\nr1 a b 1k\nc1 b 0 1p\nl1 b c 1n\nr2 c 0 1k\nh1 h 0 v1 100\nrh h 0 1k\n"
                "x1 a sub\n.subckt sub a\nvs a s1 0\nrs s1 0 1k\n.ends\n"
                "e9 o 0 vol='i(%s)*1k'\nro o 0 1k\n.tran 1n 10n\n")
        for target in ("r1", "c1", "x1.rs"):
            nl = parse(base % target)
            for render in (vacask.render, xyce.render):
                with self.subTest(target=target, engine=render.__module__):
                    with self.assertRaises(NoteError) as cm:
                        render(nl)
                    self.assertIn("i(%s) in the expression of e9: the" % target, str(cm.exception))
        for target in ("v1", "l1", "h1", "x1.vs"):
            nl = parse(base % target)
            for render in (vacask.render, xyce.render):
                render(nl)


# -- R6N-04: unverified items --------------------------------------------------------------------------

class TestWireModels(unittest.TestCase):
    def test_resistor_card(self):
        m = card("rm", "r", 1, rsh=100, dw=1e-7, dlr=1e-7, tc1r=1e-3, tc2r=1e-6, tref=30)
        pv, _ = mp(m, "vacask")
        self.assertEqual(sorted(pv), ["dlr", "dw", "rsh", "tc1r", "tc2r", "tnom"])
        px, _ = mp(m, "xyce")
        self.assertEqual(sorted(px), ["narrow", "rsh", "tc1", "tc2", "tnom"])
        self.assertAlmostEqual(px["narrow"].value, 2e-7)
        with self.assertRaises(T.TableError) as cm:
            mp(card("rm", "r", 1, rsh=100, dw=1e-7, dlr=5e-8), "xyce")
        self.assertIn("Xyce's resistor subtracts one NARROW", str(cm.exception))
        mp(card("rm", "r", 1, rsh=100, dw=1e-7, dlr=5e-8), "vacask")              # exact on VACASK
        for engine in ("vacask", "xyce"):
            with self.assertRaises(T.TableError) as cm:
                mp(card("rm", "r", 1, rsh=100, cox=1e-3), engine)
            self.assertIn("wire capacitance (COX", str(cm.exception))
        with self.assertRaises(T.TableError):
            mp(card("rm", "r", 1, res=1e3), "xyce")                                 # a multiplier on Xyce

    def test_capacitor_card(self):
        m = card("cm", "c", 1, cox=1e-3, capsw=1e-10, tc1=0, w=1e-5, tnom=25, **{"del": 0.0})
        pv, _ = mp(m, "vacask")
        self.assertEqual(sorted(pv), ["capsw", "cox", "defw", "del", "model_tc1", "tnom"])
        px, _ = mp(m, "xyce")
        self.assertEqual(sorted(px), ["cj", "cjsw", "defw", "tc1", "tnom"])
        p, notes = mp(card("cm", "c", 1, thick=1e-8, di=3.9), "xyce")
        self.assertAlmostEqual(p["cj"].value, 3.9 * 8.8542149e-12 / 1e-8)
        with self.assertRaises(T.TableError):
            mp(card("cm", "c", 1, cap=1e-12), "xyce")


class TestNestedScope(unittest.TestCase):
    def test_vacask_refuses_enclosing_parameter(self):
        nl = parse(".subckt outer a p=2k\n.subckt inner a\nr1 a 0 p\n.ends inner\nx1 a inner\n.ends outer\n"
                   "x1 a outer p=3k\nv1 a 0 1\n.tran 1n 10n")
        with self.assertRaises(NoteError) as cm:
            vacask.render(nl)
        self.assertIn("subckt inner, defined inside subckt outer, reads p of the enclosing subckt",
                      str(cm.exception))
        xyce.render(nl)                                       # Xyce reads it as HSPICE does


WIRES = """
.temp 125
.model rm r rsh=100 dw=0.1u dlr=0.1u tc1r=1e-3 tref=25
r1 a 0 rm w=1u l=10u
v1 a 0 1
.model cm c cox=1e-3 capsw=1e-10 del=0.1u tc1=0 tc2=0 w=10u tnom=25
c1 b 0 cm l=10u
v2 b 0 pwl(0 0 1u 1)
.model lm l
l1 p 0 lm l=1u
l2 s 0 1u
k1 l1 l2 0.5
i1 0 p pwl(0 0 1u 1)
rp p 0 1meg
r2 s 0 1meg
.tran 1n 1u
.option delmax=1n
.print tran i(v1) i(v2) v(s) v(p)
"""

NESTED = """
.subckt outer a p=2k
.subckt inner a
r1 a 0 p
.ends inner
x1 a inner
.ends outer
x9 n9 outer p=3k
v9 n9 0 1
.tran 1n 10n
.print tran i(v9)
"""


@needs_both
class TestWireRuns(_Runs):
    def test_hspice_wire_models_and_coupling(self):
        """R = RSH*(L-2*DLR)/(W-2*DW)*(1+TC1R*(T-TREF)) = 100*9.8/0.8*1.1 = 1347.5 Ohm (14-5); C =
        COX*Leff*Weff + 2*CAPSW*(Leff+Weff), Leff = Weff = 10u-2*DEL (W the model's default) = 9.996e-14 F
        (14-10): before round 6 Xyce ignored DW, DLR, TC1R, TREF, COX, CAPSW, DEL silently (R=1000,
        C=0) and VACASK rejected the C card.  A K couples an inductor with a model card on both
        engines (VACASK's mutual over sp_inductor): v(p)=L*di/dt=1, v(s)=k*L*di/dt=0.5."""
        raws = self.both(parse(WIRES), "w")
        self.check(raws, "i(v1)", 0.5e-6, 1 / 1347.5, rel=1e-6, sign=-1)
        self.check(raws, "i(v2)", 0.5e-6, 9.996e-14 * 1e6, rel=1e-4, sign=-1)
        self.check(raws, "p", 0.5e-6, 1.0, rel=1e-3)
        self.check(raws, "s", 0.5e-6, 0.5, rel=1e-3)

    def test_nested_subckt_reads_enclosing_parameter_on_xyce(self):
        """HSPICE's lexical scope: inner reads outer's p, overridden to 3k on x9 (VACASK refuses the
        deck, TestNestedScope)."""
        raw = self.xyce(parse(NESTED), "n")
        self.check({"xyce": raw}, "i(v9)", 5e-9, 1 / 3e3, rel=1e-9, sign=-1)


if __name__ == "__main__":
    unittest.main()
