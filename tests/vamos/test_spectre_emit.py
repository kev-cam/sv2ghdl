"""S3: the emitter and table extensions of the spectre personality (docs/VAMOS_SPECTRE_DESIGN.md §3.9,
§4.4, §4.5, §7, §11 T0 "Emitter extensions" and the S3 half of "HSPICE-route regressions").

    python3 -m unittest discover -s tests/vamos -p 'test_spectre_emit.py' -v

The IR is built by hand from the frozen dataclasses (§10); a RunPlan, its AnalysisSteps and the
SignalMap are constructed directly, never through plan.build or signals.resolve (S2's).  Goldens live
in tests/vamos/fixtures/spectre/emit/; VAMOS_REGEN_GOLDEN=1 rewrites them.

TestSpectreCards        tables.model_params(dialect="spectre"): the SPECTRE_MASTERS card path (no HSPICE
                        defaults, the default rows and their note, strips, renames, errors, the type and
                        level folds, the engine fixes that stay)
TestSpectreInstances    tables.spectre_instance_params: the MasterRow.instance rows (defaults, renames,
                        instance_<name> without the prefix on Xyce, strips, errors)
TestSpectreBins         bin_bounds_spectre / bin_guard_spectre / select_bin_spectre, the Scope group order
TestCondWalks           every shared walk sees a Cond (E44, E56): reachable, Scope, item_exprs,
                        enclosing_reads, element_at, smoke_netlist, xyce._multiplied
TestPathEnvsSpectre     the dialect="spectre" leg of path_envs: a nested definition sees the enclosing
                        subckt's parameters (S6 when S1's evaluator is not there yet)
TestSharedData          SPECTRE_OPTIONS, the method maps, wave_t0, va_letter / va_polarity
TestVacaskSpectre       the VACASK goldens and texts of every §7.1 mapping row the emitter owns
TestXyceSpectre         the Xyce goldens and texts of every §7.2 mapping row, output_problems and
                        device_summary_problems
TestHspiceRouteRegressions   the S3 shared fixes that reach the HSPICE path (§4)
TestVacaskRuns, TestXyceRuns, TestCrossEngine (WSL)   the rendered decks on the engines, with the §13
                        values (E74, E17, E22, E35, E65, E32, E19, E18, E64, E67, E84)
"""

import copy
import os
import re
import unittest

from vamos_testlib import TempDir, fixture, needs_vacask, needs_xyce, run, vacask_bin, xyce_bin, xyce_env

from vamos.netlist import expr as X  # noqa: E402
from vamos.netlist import plan as P  # noqa: E402
from vamos.netlist import rawfile, signals, tables as T, vacask, xyce  # noqa: E402
from vamos.netlist.expr_ast import Binary, Name, Num, Str  # noqa: E402
from vamos.netlist.ir import (Analysis, Cond, Instance, Model, Netlist, Param, ParamTest, Source, Subckt,  # noqa: E402
                              SweepSpec, VaModule)
from vamos.notes import ERROR, NOTE, WARNING, NoteError  # noqa: E402

REGEN = bool(os.environ.get("VAMOS_REGEN_GOLDEN"))
EMIT = fixture("spectre", "emit")
FACTS = fixture("spectre", "engine_facts")
ALLPUB_V = signals.PSF_V
ALLPUB_I = signals.PSF_I


def I(name, kind, nodes, **kw):                                   # noqa: E743
    return Instance(name, kind, list(nodes), **kw)


def R(name, a, b, value, prim="resistor", **params):
    return I(name, "r", [a, b], value=Num(float(value)) if isinstance(value, (int, float)) else value,
             params={k: (v if isinstance(v, (Num, Str, Name, Binary)) else Num(float(v))) for k, v in params.items()},
             prim=prim)


def V(name, p, n, dc=None, prim="vsource", **kw):
    return I(name, "v", [p, n], prim=prim,
             source=Source(dc=None if dc is None else Num(float(dc)), **kw))


def card(name, kind, level, prim, origin="", **params):
    return Model(name, kind, None if level is None else float(level),
                 {k: (v if isinstance(v, (Num, Str, Name)) else Num(float(v))) for k, v in params.items()},
                 origin, prim=prim)


def sev(notes, severity):
    return [n.message for n in notes if n.severity == severity]


def spectre_eval_ready():
    """S1's expression dialect: the Xyce emitter evaluates a Spectre netlist's subckt parameters and
    conditions with it (tables.path_envs, dialect="spectre")."""
    try:
        X.evaluate(Binary("+", Num(1.0), Num(1.0)), {}, dialect="spectre")
    except NotImplementedError:
        return False
    return True


def golden(test, name, text):
    path = os.path.join(EMIT, name)
    if REGEN or not os.path.exists(path):
        os.makedirs(EMIT, exist_ok=True)
        with open(path, "w", newline="\n") as fh:
            fh.write(text)
        if not REGEN:
            test.fail("golden %s was missing; written (check it in)" % name)
    with open(path) as fh:
        want = fh.read()
    test.assertEqual(text, want, "deck differs from golden spectre/emit/%s (VAMOS_REGEN_GOLDEN=1 rewrites it)" % name)


def lines(text, pattern):
    return [l for l in text.splitlines() if re.search(pattern, l)]


# -- the plan pieces (frozen dataclasses, built directly: §10) ------------------------------------

def level(target, spec, lid="vamos_w1", name="", continuation=1):
    return P.SweepLevel(name, lid, target, spec, continuation=continuation)


def values_spec(*vals):
    return SweepSpec("param", mode="values", values=[Num(float(v)) for v in vals])


def freq_level(mode="dec", start=1.0, stop=1e6, count=10, lid="vamos_f1"):
    return level(("freq", "", ""), SweepSpec("freq", mode=mode, start=Num(start), stop=Num(stop), count=count), lid)


def step(sid, kind, name="", context=(), own=None, args=None, options=None, **kw):
    return P.AnalysisStep(sid, name or sid, kind, list(context), own, dict(args or {}), dict(options or {}), **kw)


def analysis(st):
    return P.Action("analysis", {}, st, st.id)


OPTIONS = {"temp": 27.0, "tnom": 27.0, "reltol": 1e-3, "vabstol": 1e-6, "iabstol": 1e-12, "gmin": 1e-12}


def plan_of(actions, variables=None, overridden=None, engine="vacask"):
    return P.RunPlan(list(actions), dict(variables or {}), {k: set(v) for k, v in (overridden or {}).items()},
                     dict(OPTIONS), [], set(variables or {}), engine)


def sigmap(per_step, allpub=()):
    return signals.SignalMap({k: list(v) for k, v in per_step.items()}, set(allpub))


# -- the decks -------------------------------------------------------------------------------------

def worked_example():
    """§7.3: the RC low-pass filter with its op, ac and swept tran (dialect spectre, case kept)."""
    rcsec = Subckt("rcsec", ["a", "b"], params=[Param("r", Num(1000.0))], body=[
        R("R1", "a", "b", Name("r")), I("C1", "c", ["b", "0"], value=Num(1e-6), prim="capacitor")])
    nl = Netlist(title="RC Low-Pass Filter", dialect="spectre")
    nl.body = [Param("rval", Num(1000.0)),
               I("V1", "v", ["in", "0"], prim="vsource", source=Source(
                   wave="pulse", args={"v1": Num(0.0), "v2": Num(1.0), "td": Num(1e-3), "tr": Num(1e-6),
                                       "tf": Num(1e-6), "pw": Num(4e-3), "per": Num(1e-2)}, ac=(Num(1.0), Num(0.0)))),
               I("X1", "x", ["in", "out"], master="rcsec", params={"r": Name("rval")}),
               rcsec]
    nl.values = {"rval": 1000.0}
    nl.temp, nl.tnom = 27.0, 27.0
    nl.analyses = [Analysis("tran", {"step": 5e-6, "stop": 5e-3}, "deck:12", name="tr")]
    return nl


def worked_plan(engine="vacask"):
    """The plan of §7.3's three analyses as §7.1's control block shows it."""
    op = step("vamos_a1", "op", "myop", options=OPTIONS)
    ac = step("vamos_a2", "ac", "myac", own=freq_level(), options=OPTIONS)
    swp = level(("variable", "rval", ""), values_spec(1000.0, 2000.0), "vamos_w1", "swp", continuation=0)
    tr = step("vamos_a3", "tran", "tr", context=[swp], options=OPTIONS,
              args={"step": 5e-6, "stop": 5e-3, "maxstep": 1e-4, "reltol": 1e-3, "relref": "sigglobal",
                    "method": "traponly", "lteratio": 3.5, "errpreset": "moderate"})
    actions = [P.Action("options", dict(OPTIONS), None, "opts"), P.Action("var", {"rval": 1000.0}, None, "var"),
               analysis(op), analysis(ac), analysis(tr)]
    return plan_of(actions, {"rval": 1000.0}, engine=engine), [op, ac, tr]


def allpub_map(steps, nodes=("in", "out", "X1.b"), currents=("V1",)):
    refs = [(("v", n), n, ALLPUB_V) for n in nodes] + [(("i", c), c + ":p", ALLPUB_I) for c in currents]
    return sigmap({s.id: refs for s in steps}, [s.id for s in steps])


def npn_mod_netlist(dialect="hspice"):
    """UG p.110: an inline subckt binning three BJT cards by area inside a structural if (E44, E56)."""
    prim = "bjt" if dialect == "spectre" else ""
    q10 = I("npn_mod", "q", ["c", "b", "e", "s"], master="npn10x10")
    q20 = I("npn_mod", "q", ["c", "b", "e", "s"], master="npn20x20")
    qd = I("npn_mod", "q", ["c", "b", "e", "s"], master="npn_default")
    chain = Cond([(Binary("<", Name("area"), Num(100e-12)), [q10])],
                 default=[Cond([(Binary("<", Name("area"), Num(400e-12)), [q20])], default=[qd])])
    sub = Subckt("npn_mod", ["c", "b", "e", "s"], params=[Param("area", Num(5e-12))], body=[
        chain,
        card("npn_default", "npn", 1, prim, **{"is": 3.2e-16, "vaf": 59.8}),
        card("npn10x10", "npn", 1, prim, **{"is": 3.5e-16, "vaf": 61.5}),
        card("npn20x20", "npn", 1, prim, **{"is": 3.77e-16, "vaf": 60.5}),
        ParamTest("chk", [("warnif", Binary("<", Name("area"), Num(0.0)))])], inline=True)
    nl = Netlist(title="UG p.110 npn_mod binning by area", dialect=dialect)
    nl.body = [V("vin", "1", "0", 1.0), V("vb", "2", "0", 0.7), V("vb3", "3", "0", 0.65), sub,
               I("q1", "x", ["1", "2", "0", "0"], master="npn_mod", params={"area": Num(350e-12)}),
               I("q2", "x", ["1", "3", "0", "0"], master="npn_mod", params={"area": Num(25e-12)}),
               I("q3", "x", ["1", "3", "0", "0"], master="npn_mod", params={"area": Num(1000e-12)})]
    nl.analyses = [Analysis("tran", {"step": 1e-9, "stop": 1e-8}, "deck:9")]
    nl.probes = [("tran", "v", "1"), ("tran", "i", "vin")]
    return nl


B3 = {"tox": 4e-9, "toxm": 4e-9, "u0": 0.04}


def group_netlist(nf=None, w=2e-6, l=0.5e-6):
    """A 12-entry Spectre model group (bin_rule="spectre") in which entries 2 and 10 overlap for the
    instance's geometry: group order selects entry 2 on both engines; entry 1 omits lmin and wmin."""
    def entry(k, lmin, lmax, wmin, wmax, vth0):
        p = dict(B3, vtho=vth0, lmax=lmax, wmax=wmax)
        if lmin is not None:
            p["lmin"] = lmin
        if wmin is not None:
            p["wmin"] = wmin
        m = card("nch.%d" % k, "nmos", 49, "bsim3v3", **p)
        m.base, m.bin_index, m.bin_rule = "nch", k, "spectre"
        return m
    entries = [entry(1, None, 0.2e-6, None, 1e-6, 0.30), entry(2, 0.2e-6, 1e-6, 1e-6, 1e-4, 0.42),
               entry(3, 1e-6, 1e-5, 0.1e-6, 1e-6, 0.44), entry(4, 1e-6, 1e-5, 1e-6, 1e-4, 0.45),
               entry(5, 1e-5, 1e-4, 0.1e-6, 1e-6, 0.46), entry(6, 1e-5, 1e-4, 1e-6, 1e-4, 0.47),
               entry(7, 1e-4, 1e-3, 0.1e-6, 1e-6, 0.48), entry(8, 1e-4, 1e-3, 1e-6, 1e-4, 0.49),
               entry(9, 0.1e-6, 0.2e-6, 1e-6, 1e-4, 0.50), entry(10, 0.2e-6, 1e-6, 1e-6, 1e-4, 0.60),
               entry(11, 1e-3, 1e-2, 0.1e-6, 1e-6, 0.51), entry(12, 1e-3, 1e-2, 1e-6, 1e-4, 0.52)]
    nl = Netlist(title="spectre model group", dialect="spectre")
    params = {"w": Num(w), "l": Num(l)}
    if nf is not None:
        params["nf"] = Num(float(nf))
    ref = card("ref2", "nmos", 49, "bsim3v3", vtho=0.42, **B3)
    nl.body = entries + [ref, V("vd", "d", "0", 1.0), V("vg", "g", "0", 1.0), V("vr", "r", "0", 1.0),
                         I("m1", "m", ["d", "g", "0", "0"], master="nch", params=dict(params), prim="bsim3v3"),
                         I("mr", "m", ["r", "g", "0", "0"], master="ref2", params=dict(params), prim="bsim3v3")]
    nl.temp, nl.tnom = 27.0, 27.0
    nl.analyses = [Analysis("tran", {"step": 1e-10, "stop": 1e-9}, "deck:20")]
    return nl


def op_plan(nl, refs, engine="vacask", options=None):
    st = step("vamos_a1", "op", "op1", options=dict(options or OPTIONS))
    pl = plan_of([P.Action("options", dict(options or OPTIONS), None, "opts"), analysis(st)], engine=engine)
    return pl, st, sigmap({"vamos_a1": refs})


def lift_netlist():
    """UG p.86: a nested definition s2 reads rr of its enclosing s1 (E65); on VACASK it is lifted."""
    s2 = Subckt("s2", ["x", "y"], body=[R("r1", "x", "y", Name("rr"))])
    s1 = Subckt("s1", ["a", "b"], params=[Param("rr", Num(1000.0))],
                body=[s2, I("xs", "x", ["a", "mid"], master="s2"), R("r2", "mid", "b", 1000.0)])
    nl = Netlist(title="nested definition inherits rr", dialect="spectre")
    nl.body = [s1, V("v1", "in", "0", 1.0), I("x1", "x", ["in", "out"], master="s1", params={"rr": Num(2000.0)}),
               R("rl", "out", "0", 1000.0)]
    nl.temp, nl.tnom = 27.0, 27.0
    nl.analyses = [Analysis("tran", {"step": 1e-9, "stop": 1e-8}, "deck:9")]
    return nl


def pulse_source(dc=None, v1=0.0, name="v1", p="a", n="0", prim="vsource"):
    return I(name, "v", [p, n], prim=prim, source=Source(
        dc=None if dc is None else Num(float(dc)), wave="pulse",
        args={"v1": Num(float(v1)), "v2": Num(1.0), "td": Num(1e-6), "tr": Num(1e-9), "tf": Num(1e-9), "pw": Num(1e30)}))


def divider(src=None, extra=(), r1=1000.0, r2=1000.0):
    nl = Netlist(title="divider", dialect="spectre")
    nl.body = [src or V("v1", "in", "0", 1.0, ac=(Num(1.0), Num(0.0))), R("r1", "in", "out", r1), R("r2", "out", "0", r2)]
    nl.body += list(extra)
    nl.temp, nl.tnom = 27.0, 27.0
    return nl


def va_resistor(dialect="spectre", params=None, attrs=True, m=None, name="y1"):
    """A Verilog-A resistor instance of the E64 toy_resistor.va module (xyceModelGroup Resistor, level 6); the
    path is the bare file name (the goldens hold it; the engine runs copy the file beside the deck)."""
    path = "toy_resistor.va"
    nl = Netlist(title="verilog-a letter device", dialect=dialect)
    nl.hdl = [path]
    mod = VaModule("resistor", path, ["R"],
                   {"xyceModelGroup": "Resistor", "xyceSpiceDeviceName": "R", "xyceLevelNumber": "6"} if attrs else {})
    nl.va_modules = {"resistor": mod}
    p = dict(params or {})
    if m is not None:
        p["m"] = Num(float(m))
    nl.body = [V("v1", "a", "0", 1.0), I(name, "y", ["a", "b"], master="resistor", params=p, prim="resistor"),
               R("r2", "b", "0", 1000.0)]
    nl.analyses = [Analysis("tran", {"step": 1e-10, "stop": 1e-9}, "deck:9")]
    nl.probes = [("tran", "v", "b")]
    nl.temp, nl.tnom = 27.0, 27.0
    return nl


# =============================================================================================
# tables.py
# =============================================================================================

class TestSpectreCards(unittest.TestCase):
    def test_every_card_needs_a_master(self):
        with self.assertRaisesRegex(T.TableError, "no Spectre master"):
            T.model_params(Model("nch", "nmos", 1.0, {}), "vacask", dialect="spectre")
        with self.assertRaisesRegex(T.TableError, "no SPECTRE_MASTERS row"):
            T.model_params(card("x", "nmos", 1, "bsim6"), "xyce", dialect="spectre")
        with self.assertRaises(ValueError):
            T.model_params(card("x", "nmos", 1, "mos1"), "vacask", dialect="spectre-spice")

    def test_mos3_defaults_and_no_hspice_rewrite(self):
        """§3.9: nsub=1.13e16 phi=0.7 gamma=0 vto=0 mjsw=1/3 badmos3=1 written; no HSPICE ld/cgso/eta/kp."""
        m = card("nch", "nmos", 3, "mos3", origin="lib:3", kp=5e-5, tox=2e-8, xj=0.2e-6, eta=0.1)
        for engine in T.ENGINES:
            p, notes = T.model_params(m, engine, dialect="spectre")
            d = dict(p)
            self.assertEqual((d["nsub"], d["phi"], d["gamma"], d["vto"], d["badmos3"]),
                             (Num(1.13e16), Num(0.7), Num(0.0), Num(0.0), Num(1.0)))
            self.assertAlmostEqual(d["mjsw"].value, 1.0 / 3.0)
            self.assertEqual(d["eta"], Num(0.1))                        # no 8.14/8.15 scaling
            self.assertEqual(d["kp"], Num(5e-5))
            for k in ("ld", "cgso", "cgdo", "cj", "fc", "uo", "capop"):
                self.assertNotIn(k, d, k)
            self.assertNotIn("level", d)
            note = [n for n in notes if n.severity == NOTE and "applied, Spectre's documented default" in n.message]
            self.assertEqual(len(note), 1, notes)
            self.assertIn("nsub=1.13e+16", note[0].message)
            self.assertIn("badmos3=1", note[0].message)
            self.assertEqual(note[0].origin, "lib:3")
            w = sev(notes, WARNING)
            self.assertEqual(len(w), 1, w)
            self.assertIn("badmos3=1 applied", w[0])
            if engine == "vacask":
                self.assertEqual(p[0], ("type", Num(1.0)))
            else:
                self.assertNotIn("type", d)

    def test_given_nsub_derivation_warnings(self):
        m = card("nch", "nmos", 1, "mos1", nsub=1e16, kp=1e-4)
        p, notes = T.model_params(m, "vacask", dialect="spectre")
        d = dict(p)
        self.assertEqual(d["nsub"], Num(1e16))
        for k in ("phi", "gamma", "vto"):
            self.assertNotIn(k, d)
        w = sev(notes, WARNING)
        self.assertEqual(sorted(x.split(":")[1].split()[0] for x in w), ["gamma", "phi", "vto"])
        self.assertTrue(all("is not given" in x for x in w))
        # mos1 alone writes tox=1e-7 and tpg=1 (§3.9 table)
        self.assertEqual((d["tox"], d["tpg"]), (Num(1e-7), Num(1.0)))
        d3 = dict(T.model_params(card("n3", "nmos", 3, "mos3", nsub=1e16), "xyce", dialect="spectre")[0])
        self.assertNotIn("tox", d3)
        self.assertNotIn("tpg", d3)

    def test_capmod_and_hcomp_value_rules(self):
        base = dict(kp=1e-4, nsub=1e16, vto=0.5, phi=0.7, gamma=0.3)
        for value, expect in ((None, "silent"), (Str("bsim"), "note"), (Str("meyer"), "note"), (Str("none"), "error"),
                              (Name("yang"), "error")):
            extra = {} if value is None else {"capmod": value}
            m = card("n", "nmos", 2, "mos2", **dict(base, **extra))
            if expect == "error":
                with self.assertRaisesRegex(T.TableError, "capmod"):
                    T.model_params(m, "vacask", dialect="spectre")
                continue
            p, notes = T.model_params(m, "vacask", dialect="spectre")
            self.assertNotIn("capmod", dict(p))
            removed = [n for n in notes if "capmod" in n.message]
            self.assertEqual(len(removed), 1 if expect == "note" else 0, (value, notes))
        d = card("d1", "d", 1, "diode", **{"js": 1e-14, "hcomp": 0.0})
        p, notes = T.model_params(d, "xyce", dialect="spectre")
        self.assertNotIn("hcomp", dict(p))
        self.assertEqual(len([n for n in notes if "hcomp=0" in n.message and n.severity == NOTE]), 1)
        with self.assertRaisesRegex(T.TableError, "hcomp=1"):
            T.model_params(card("d2", "d", 1, "diode", hcomp=1.0), "xyce", dialect="spectre")

    def test_diode_rows(self):
        d = card("dd", "d", 1, "diode", origin="lib:7", **{"js": 1e-14, "cjo": 1e-12, "fc": 0.4, "ik": 1e-3, "jsw": 1e-15,
                                                           "n": 1.1, "pb": 0.9, "pta": 1e-3, "area": 1.0, "perim": 0.5})
        for engine in T.ENGINES:
            p, notes = T.model_params(d, engine, dialect="spectre")
            k = dict(p)
            self.assertEqual(k["eg"], Num(1.124481))                     # Spectre's 27 C default
            self.assertEqual(k["fcs"], Num(0.4))                         # fcs = fc
            self.assertEqual(k["ikp"], Num(1e-3))                        # ikp = ik with ik and jsw
            self.assertEqual(k["ikf"], Num(1e-3))                        # the rename
            self.assertEqual(k["nbv"], Num(1.0))                         # Spectre's nz=1 under the targets' name
            self.assertEqual(k["vj"], Num(0.9))                          # pb -> vj
            self.assertEqual(k["tpb"], Num(1e-3))                        # pta -> tpb
            self.assertEqual(k["model_pj"], Num(0.5))                    # perim -> model_pj (VACASK's name)
            for absent in ("pb", "pta", "ik", "nz", "area", "php", "dcap"):
                self.assertNotIn(absent, k, absent)
            self.assertEqual(k["fc"], Num(0.4))                          # never rewritten to 0
            stripped = [n for n in notes if "area=1" in n.message]
            self.assertEqual(len(stripped), 1)
            self.assertEqual(sev(notes, WARNING), [])
        with self.assertRaisesRegex(T.TableError, "level=2"):
            T.model_params(Model("d2", "d", 2.0, {"level": Num(2.0), "js": Num(1e-14)}, prim="diode"), "vacask", dialect="spectre")
        # a Spectre diode card prints no HSPICE default (E59): cjo alone gives no fc=0, no vj=0.8
        k = dict(T.model_params(card("d3", "d", 1, "diode", cjo=1e-12), "xyce", dialect="spectre")[0])
        self.assertEqual(sorted(k), ["cjo", "eg"])
        # the Xyce engine fix stays: a sidewall-only diode gets cjo=1e-30 there
        k = dict(T.model_params(card("d4", "d", 1, "diode", cjsw=1e-13), "xyce", dialect="spectre")[0])
        self.assertEqual(k["cjo"], Num(T.XYCE_TINY_CJO))
        self.assertNotIn("cjo", dict(T.model_params(card("d4", "d", 1, "diode", cjsw=1e-13), "vacask",
                                                    dialect="spectre")[0]))

    def test_resistor_rows(self):
        m = card("rm", "r", None, "resistor", origin="lib:1", kf=1e-20, wdexp=1.5, ldexp=0.5, fexp=1.0, weexp=0.0,
                 scaler=1.0, trise=0.0, tnom=27.0, tc1=1e-3)
        p, notes = T.model_params(m, "vacask", dialect="spectre")
        k = dict(p)
        self.assertEqual((k["af"], k["wf"], k["lf"], k["ef"], k["kf"], k["tc1"], k["tnom"]),
                         (Num(2.0), Num(1.5), Num(0.5), Num(1.0), Num(1e-20), Num(1e-3), Num(27.0)))
        for absent in ("wdexp", "ldexp", "fexp", "weexp", "scaler", "trise"):
            self.assertNotIn(absent, k, absent)
        removed = sorted(n.message.split("removed")[0].split()[-1] for n in notes if "removed" in n.message)
        self.assertEqual(removed, ["scaler=1.0", "trise=0.0", "weexp=0.0"])
        self.assertEqual(sev(notes, WARNING), [])
        with self.assertRaisesRegex(T.TableError, "scaler=2"):
            T.model_params(card("r2", "r", None, "resistor", scaler=2.0), "vacask", dialect="spectre")
        with self.assertRaisesRegex(T.TableError, "trise=10"):
            T.model_params(card("r3", "r", None, "resistor", trise=10.0), "xyce", dialect="spectre")
        with self.assertRaisesRegex(T.TableError, "c=1e-12"):
            T.model_params(card("r4", "r", None, "resistor", c=1e-12), "xyce", dialect="spectre")
        # a non-constant value of a 0-or-error parameter is not provably 0: the error row applies
        with self.assertRaisesRegex(T.TableError, "trise=p"):
            T.model_params(card("r5", "r", None, "resistor", trise=Name("p")), "xyce", dialect="spectre")
        # the folded card geometry (spectre.py's) is consumed, never printed
        k = dict(T.model_params(card("r6", "r", None, "resistor", r=1000.0, w=1e-6, l=2e-6, rsh=10.0, tc2=1e-6),
                                "xyce", dialect="spectre")[0])
        self.assertEqual(k, {"tc2": Num(1e-6)})

    def test_type_and_level_folds(self):
        p, _ = T.model_params(card("p1", "pmos", 1, "mos1", type=Str("p"), vto=-0.5), "vacask", dialect="spectre")
        self.assertEqual(p[0], ("type", Num(-1.0)))
        self.assertNotIn("type", dict(p[1:]))
        with self.assertRaisesRegex(T.TableError, "contradicts"):
            T.model_params(card("p2", "nmos", 1, "mos1", type=Str("p")), "vacask", dialect="spectre")
        with self.assertRaisesRegex(T.TableError, "unknown polarity"):
            T.model_params(card("p3", "nmos", 1, "mos1", type=Str("x")), "vacask", dialect="spectre")
        k = dict(T.model_params(card("q1", "npn", 1, "bjt", type=Name("npn"), bf=100.0), "xyce", dialect="spectre")[0])
        self.assertEqual(k, {"bf": Num(100.0), "fc": Num(0.5), "mje": Num(1.0 / 3.0), "mjc": Num(1.0 / 3.0)})

    def test_e59_cards_print_no_hspice_key(self):
        """§3.9: Spectre mos3, bsim3v3, diode and bjt cards reach the engines without an HSPICE-written key."""
        cards = [card("m3", "nmos", 3, "mos3", nsub=1e16, vto=0.5, kp=1e-4, phi=0.7, gamma=0.3, xj=1e-7),
                 card("b3", "nmos", 49, "bsim3v3", vtho=0.4, **B3),
                 card("dd", "d", 1, "diode", cjo=1e-12, eg=1.11), card("qq", "npn", 1, "bjt", cjs=1e-12, fc=0.5)]
        hspice_keys = {"cj", "cjsw", "xpart", "ld", "cgso", "cgdo", "mjs", "vj", "php", "fcs"}
        for m in cards:
            for engine in T.ENGINES:
                p, notes = T.model_params(m, engine, dialect="spectre")
                keys = {k for k, _ in p}
                self.assertFalse(keys & hspice_keys, (m.name, engine, keys & hspice_keys))
                self.assertFalse(any("HSPICE" in n.message for n in notes), (m.name, notes))
        # bsim3v3: Spectre's capmod=2 written (the targets' own is 3), xpart/cj/cjsw never
        k = dict(T.model_params(cards[1], "vacask", dialect="spectre")[0])
        self.assertEqual(k["capmod"], Num(2.0))
        # the bins' bounds: dropped on VACASK, kept on Xyce (the existing bin handling)
        b = card("nch.1", "nmos", 49, "bsim3v3", lmin=1e-7, lmax=1e-6, wmin=1e-7, wmax=1e-6, vtho=0.4)
        b.base, b.bin_index, b.bin_rule = "nch", 1, "spectre"
        self.assertNotIn("lmin", dict(T.model_params(b, "vacask", binned=True, dialect="spectre")[0]))
        self.assertIn("lmin", dict(T.model_params(b, "xyce", binned=True, dialect="spectre")[0]))

    def test_unknown_parameter_is_an_error(self):
        with self.assertRaisesRegex(T.TableError, "bogus=1.0 is not a parameter of Spectre's mos1"):
            T.model_params(card("n", "nmos", 1, "mos1", bogus=1.0), "vacask", dialect="spectre")

    def test_hspice_path_is_untouched(self):
        m = Model("nch", "nmos", 1.0, {"vto": Num(0.5), "kp": Num(1e-4)})
        for engine in T.ENGINES:
            self.assertEqual(T.model_params(m, engine), T.model_params(m, engine, dialect="hspice"))
            self.assertIn("gamma", dict(T.model_params(m, engine)[0]))    # HSPICE's derivation


class TestSpectreInstances(unittest.TestCase):
    def test_mos_defaults_written_with_a_note(self):
        inst = I("m1", "m", ["d", "g", "s", "b"], master="nch", origin="t:4")
        for master, wl in (("mos1", 3e-6), ("mos3", 3e-6), ("bsim3v3", 5e-6), ("bsim4", 5e-6)):
            params, notes = T.spectre_instance_params(inst, master, "vacask")
            self.assertEqual(params, {"w": Num(wl), "l": Num(wl), "nrd": Num(0.0), "nrs": Num(0.0)}, master)
            self.assertEqual([n.severity for n in notes], [NOTE])
            self.assertIn("w=%s" % T._txt(Num(wl)), notes[0].message)
            self.assertEqual(notes[0].origin, "t:4")
        given = I("m2", "m", ["d", "g", "s", "b"], master="nch", params={"w": Num(1e-6), "l": Num(1e-6), "nrd": Num(2.0)})
        params, notes = T.spectre_instance_params(given, "mos1", "xyce")
        self.assertEqual(params, {"w": Num(1e-6), "l": Num(1e-6), "nrd": Num(2.0), "nrs": Num(0.0)})

    def test_renames_strips_errors(self):
        inst = I("m1", "m", ["d", "g", "s", "b"], master="nch", params={
            "w": Num(1e-6), "l": Num(1e-6), "trnqsmod": Num(1.0), "rbpb": Num(50.0), "trise": Num(10.0),
            "region": Str("sat"), "mulmu0": Num(1.1), "m": Num(2.0)})
        params, notes = T.spectre_instance_params(inst, "bsim4", "vacask")
        self.assertEqual(params["instance_trnqsmod"], Num(1.0))
        self.assertEqual(params["instance_rbpb"], Num(50.0))
        self.assertEqual(params["dtemp"], Num(10.0))
        self.assertEqual(params["mulu0"], Num(1.1))
        self.assertEqual(params["m"], Num(2.0))
        self.assertNotIn("region", params)
        self.assertEqual(len([n for n in notes if "region" in n.message]), 1)
        params, _ = T.spectre_instance_params(inst, "bsim4", "xyce")
        self.assertIn("trnqsmod", params)
        self.assertNotIn("instance_trnqsmod", params)
        self.assertIn("rbpb", params)
        with self.assertRaisesRegex(T.TableError, "ld=1e-06"):
            T.spectre_instance_params(I("m3", "m", ["d", "g", "s", "b"], params={"ld": Num(1e-6)}), "mos3", "xyce")
        with self.assertRaisesRegex(T.TableError, "bogus"):
            T.spectre_instance_params(I("m4", "m", ["d", "g", "s", "b"], params={"bogus": Num(1.0)}), "mos3", "xyce")

    def test_resistor_capacitor_source_rows(self):
        r = I("r1", "r", ["a", "b"], value=Num(1000.0), params={"w": Num(1e-6), "l": Num(2e-6), "scale": Num(2.0),
                                                              "trise": Num(0.0), "isnoisy": Str("no")}, prim="resistor")
        params, notes = T.spectre_instance_params(r, "resistor", "vacask")
        self.assertEqual(params, {"w": Num(1e-6), "l": Num(2e-6), "isnoisy": Str("no")})   # scale folded, trise stripped
        self.assertEqual(len([n for n in notes if "trise=0" in n.message]), 1)
        with self.assertRaisesRegex(T.TableError, "c=1e-12"):
            T.spectre_instance_params(I("r2", "r", ["a", "b"], params={"c": Num(1e-12)}), "resistor", "xyce")
        c = I("c1", "c", ["a", "b"], value=Num(1e-12), params={"w": Num(1e-6), "area": Num(1e-12), "m": Num(3.0)},
              prim="capacitor")
        self.assertEqual(T.spectre_instance_params(c, "capacitor", "vacask")[0], {"m": Num(3.0)})
        e = I("e1", "e", ["p", "n", "ps", "ns"], value=Num(2.0), params={"stretch": Num(1.0), "tc1": Num(0.0)}, prim="vcvs")
        params, notes = T.spectre_instance_params(e, "vcvs", "vacask")
        self.assertEqual(params, {})
        self.assertEqual(len(notes), 2)
        with self.assertRaisesRegex(T.TableError, "tc1=0.01"):
            T.spectre_instance_params(I("e2", "e", [], params={"tc1": Num(0.01)}), "vcvs", "vacask")
        # an instance whose master has no row keeps its parameters (a Verilog-A module)
        y = I("y1", "y", ["a", "b"], master="mymod", params={"R": Num(3e3)}, prim="mymod")
        self.assertEqual(T.spectre_instance_params(y, "mymod", "vacask"), ({"R": Num(3e3)}, []))
        self.assertEqual(T.spectre_master_of(y, None), "")
        self.assertEqual(T.spectre_master_of(I("m1", "m", [], master="nch"), card("nch", "nmos", 3, "mos3")), "mos3")
        self.assertEqual(T.spectre_master_of(I("m1", "m", [], master="nch", prim="nch"), card("nch", "nmos", 3, "mos3")),
                         "mos3")


class TestSpectreBins(unittest.TestCase):
    def test_bounds_defaults_and_exactness(self):
        m = card("nch.1", "nmos", 49, "bsim3v3", lmax=0.2e-6, wmax=1e-6)
        m.base, m.bin_index, m.bin_rule = "nch", 1, "spectre"
        self.assertEqual(T.bin_bounds_spectre(m, {}), (0.0, 0.2e-6, 0.0, 1e-6))          # lmin=wmin=0 [ref5 p.200]
        m2 = card("nch.2", "nmos", 49, "bsim3v3", lmin=Name("lo"))
        m2.base, m2.bin_index, m2.bin_rule = "nch", 2, "spectre"
        self.assertEqual(T.bin_bounds_spectre(m2, {"lo": 1e-7}), (1e-7, 1.0, 0.0, 1.0))  # lmax=wmax=1 m
        with self.assertRaisesRegex(T.TableError, "not constant"):
            T.bin_bounds_spectre(m2, {})
        with self.assertRaisesRegex(T.TableError, "no default lmin"):
            T.bin_bounds_spectre(card("r.1", "r", None, "resistor"), {})
        bins = [(m, (1e-7, 2.2e-7, 1e-7, 1e-6)), (m2, (2.2e-7, 1e-5, 1e-7, 1e-6))]
        # exact bounds: 0.22*1e-6 = 2.1999999999999998e-07 is below 2.2e-7, so only the first entry holds it
        self.assertIs(T.select_bin_spectre(bins, 0.22, 0.5, 1e-6), m)
        self.assertIsNone(T.select_bin_spectre(bins[1:], 0.22, 0.5, 1e-6))           # no 1e-15 tolerance
        self.assertIs(T.select_bin(bins[1:], 0.22, 0.5, 1.0, 1e-6), m2)              # HSPICE's rule still has it
        # total w, also with nf > 1: 1.6u is outside wmax=1u whatever nf
        self.assertIsNone(T.select_bin_spectre(bins, 0.2, 1.6, 1e-6))
        g = T.bin_guard_spectre((2.2e-7, 1e-5, 1e-7, 1e-6), Name("ll"), Num(1.6), 1e-6)
        self.assertEqual(X.evaluate(g, {"ll": 0.5}), 0.0)                              # w=1.6u > wmax
        g = T.bin_guard_spectre((2.2e-7, 1e-5, 1e-7, 1e-6), Name("ll"), Num(0.5), 1e-6)
        self.assertEqual((X.evaluate(g, {"ll": 0.5}), X.evaluate(g, {"ll": 0.21}), X.evaluate(g, {"ll": 10.0})),
                         (1.0, 0.0, 1.0))
        self.assertEqual(X.evaluate(g, {"ll": 0.22}), 0.0)                              # exact lower bound
        self.assertNotIn("abs", X.to_text(g))

    def test_group_order_and_scope(self):
        nl = group_netlist()
        scope = T.Scope(nl.body)
        order = [m.bin_index for m in scope.bins("nch")]
        self.assertEqual(order, list(range(1, 13)))                                    # group order, not nch.1 nch.10 nch.2
        bounds = [(b, T.bin_bounds_spectre(b, nl.values)) for b in scope.bins("nch")]
        self.assertEqual(T.select_bin_spectre(bounds, 0.5e-6, 2e-6, 1.0).name, "nch.2")  # entries 2 and 10 hold it
        self.assertEqual(T.select_bin_spectre(bounds[9:], 0.5e-6, 2e-6, 1.0).name, "nch.10")
        # an HSPICE bin set keeps Xyce's name order
        hs = [Model("nch.%d" % k, "nmos", 54.0, {}, base="nch", bin_index=k) for k in (1, 2, 10)]
        self.assertEqual([m.name for m in T.Scope(hs).bins("nch")], ["nch.1", "nch.10", "nch.2"])


class TestCondWalks(unittest.TestCase):
    def test_reachable_sees_cards_used_in_branches(self):
        nl = npn_mod_netlist()
        used = T.reachable(nl)
        cards = {m.name for s in T.all_subckts(nl) for m in s.body if isinstance(m, Model) and id(m) in used}
        self.assertEqual(cards, {"npn_default", "npn10x10", "npn20x20"})           # E44: empty before

    def test_scope_item_exprs_enclosing_reads_element_at(self):
        k = I("k1", "k", [], value=Num(0.5), ctrl=["l1", "l2"])
        l1 = I("l1", "l", ["a", "0"], value=Num(1e-6))
        c = Cond([(Binary(">", Name("sel"), Num(0.0)), [k, l1])], default=[I("l2", "l", ["b", "0"], value=Name("outer"))])
        s = Subckt("s", ["a", "b"], body=[c, ParamTest("pt", [("errorif", Binary("<", Name("outer"), Num(0.0)))])])
        self.assertEqual(T.Scope(s.body).coupled, {"l1", "l2"})
        self.assertEqual(T.item_exprs(c), [Binary(">", Name("sel"), Num(0.0))])
        self.assertEqual(T.item_exprs(s.body[1]), [Binary("<", Name("outer"), Num(0.0))])
        self.assertEqual(T.enclosing_reads(s, ("outer", "sel", "unused")), ["outer", "sel"])
        self.assertIs(T.element_at(s.body, T.Scope(s.body), "l1"), l1)
        b = I("b1", "b", ["x", "0"], expr=X.parse("i(l2)*2"), expr_kind="v")
        self.assertEqual(T.current_ref_errors(b, s.body, T.Scope(s.body)), [])    # l2 (in a branch) has a branch
        bad = I("b2", "b", ["x", "0"], expr=X.parse("i(r9)"), expr_kind="v")
        s.body[0].default.append(I("r9", "r", ["q", "0"], value=Num(1.0)))
        self.assertEqual(len(T.current_ref_errors(bad, s.body, T.Scope(s.body))), 1)

    def test_smoke_netlist_and_multiplied_see_branches(self):
        src = T.code_source("vd", "v", "n", "0", T.code_uri("vacask", "d2a", "tb.a__d"))
        nl = Netlist(body=[Cond([(Num(1.0), [src])]),
                           Subckt("s", ["a"], body=[Cond([(Num(1.0), [I("xi", "x", ["a"], master="t", params={"m": Num(2.0)})])])]),
                           Subckt("t", ["a"], body=[R("r", "a", "0", 1.0)]), I("x1", "x", ["n"], master="s")])
        out = T.smoke_netlist(nl)
        self.assertEqual(out.body[0].branches[0][1][0].source.dc, Num(0.0))
        self.assertIsNone(out.body[0].branches[0][1][0].source.code_uri)
        self.assertEqual(xyce._multiplied(nl), {"t"})                                   # E56: [] before
        self.assertEqual(T.path_counts(nl), {"s": 1, "t": 1})


class TestPathEnvsSpectre(unittest.TestCase):
    def nested(self):
        inner = Subckt("inner", ["x"], params=[Param("q", Binary("*", Name("rr"), Num(2.0)))],
                       body=[R("r1", "x", "0", Name("q"))])
        outer = Subckt("outer", ["a"], params=[Param("rr", Num(1000.0))],
                       body=[inner, I("xi", "x", ["a"], master="inner")])
        return Netlist(body=[outer, I("x1", "x", ["n"], master="outer", params={"rr": Num(2000.0)})],
                       values={}, dialect="spectre")

    def test_hspice_leg_hides_the_enclosing_parameter(self):
        by = {pe.path: pe for pe in T.path_envs(self.nested(), dialect="hspice")}
        self.assertEqual(by["x1.xi"].hidden, {"q"})
        self.assertNotIn("rr", by["x1.xi"].env)
        with self.assertRaises(X.EvalError):
            list(T.path_envs(self.nested(), strict=True, dialect="hspice"))

    def test_spectre_leg_sees_the_enclosing_parameter(self):
        if not spectre_eval_ready():
            self.skipTest("S6 integration: expr.evaluate(dialect='spectre') is S1's and is not there yet")
        by = {pe.path: pe for pe in T.path_envs(self.nested(), strict=True, dialect="spectre")}
        self.assertEqual(by["x1.xi"].hidden, set())
        self.assertEqual(dict(by["x1.xi"].env)["q"], 4000.0)
        self.assertEqual(dict(by["x1.xi"].env)["rr"], 2000.0)                         # the enclosing instance's
        # a top-level definition still falls back to the top level only
        top = Subckt("top", ["a"], params=[Param("q", Name("rr"))], body=[R("r1", "a", "0", Name("q"))])
        nl = Netlist(body=[Subckt("outer", ["a"], params=[Param("rr", Num(1.0))], body=[I("xt", "x", ["a"], master="top")]),
                           top, I("x1", "x", ["n"], master="outer")], values={}, dialect="spectre")
        by = {pe.path: pe for pe in T.path_envs(nl, dialect="spectre")}
        self.assertEqual(by["x1.xt"].hidden, {"q"})


class TestSharedData(unittest.TestCase):
    def test_options_and_methods(self):
        self.assertEqual(T.SPECTRE_OPTIONS["vacask"], {"temp": "temp", "tnom": "tnom", "gmin": "gmin", "reltol": "reltol",
                                                       "vabstol": "vntol", "iabstol": "abstol", "chargeabstol": "chgtol"})
        self.assertEqual(T.SPECTRE_OPTIONS["xyce"], {"temp": "temp", "tnom": "tnom", "gmin": "gmin"})
        for m in ("trap", "traponly", "gear2", "gear2only", "euler", "gear3", "gear3only", "gear2gear3", "trapgear2",
                  "trapeuler"):
            self.assertIn(m, T.VACASK_METHODS)
            self.assertIn(m, T.XYCE_METHODS)
        self.assertEqual(T.VACASK_METHODS["gear3"][:2], ("gear", 3))
        self.assertEqual(T.VACASK_METHODS["trapgear2"][0], "trap")
        self.assertTrue(T.VACASK_METHODS["trapgear2"][2])
        self.assertEqual((T.XYCE_METHODS["gear2"], T.XYCE_METHODS["euler"], T.XYCE_METHODS["trapgear2"]),
                         (("GEAR", 2), ("GEAR", 1), ("TRAP", None)))
        self.assertEqual(T.VACASK_OPTION_DEFAULTS["relref"], "alllocal")

    def test_wave_t0(self):
        self.assertEqual(T.wave_t0(pulse_source(v1=0.3).source), Num(0.3))
        sin = Source(wave="sin", args={"vo": Num(0.5), "va": Num(1.0), "freq": Num(1e3), "td": Num(0.0),
                                       "theta": Num(0.0), "phase": Num(90.0)})
        self.assertAlmostEqual(T.wave_t0(sin).value, 1.5)
        sin.args["phase"] = Num(0.0)
        self.assertEqual(T.wave_t0(sin), Num(0.5))
        self.assertEqual(T.wave_t0(Source(wave="pwl", points=[(Num(1e-6), Num(0.6)), (Num(2e-6), Num(1.0))])), Num(0.6))
        self.assertIsNone(T.wave_t0(Source(dc=Num(1.0))))

    def test_va_letter_and_polarity(self):
        res = VaModule("resistor", "/x.va", ["R"], {"xyceModelGroup": "Resistor", "xyceLevelNumber": "6"})
        self.assertEqual(T.va_letter(res), ("r", ("r",), 6))
        self.assertEqual(T.va_polarity(res, {"R": Num(3e3)}), ("r", None))
        mos = VaModule("bsim6", "/b.va", None, {"xyceModelGroup": "MOSFET", "xyceLevelNumber": "77",
                                                 "xyceTypeVariable": "TYPE"})
        self.assertEqual(T.va_letter(mos), ("m", ("nmos", "pmos"), 77))
        self.assertEqual(T.va_polarity(mos, {}), ("nmos", None))
        self.assertEqual(T.va_polarity(mos, {"type": Num(-1.0)}), ("pmos", "type"))
        self.assertEqual(T.va_polarity(mos, {"TYPE": Num(1.0)}), ("nmos", "TYPE"))
        cmg = VaModule("bsimcmg", "/c.va", None, {"xyceModelGroup": "MOSFET", "xyceLevelNumber": "107",
                                                   "xyceTypeVariable": "DEVTYPE", "xycePTypeValue": "0"})
        self.assertEqual(T.va_polarity(cmg, {"devtype": Num(0.0)}), ("pmos", "devtype"))
        self.assertIsNone(T.va_letter(VaModule("plain", "/p.va")))
        self.assertIsNone(T.va_letter(VaModule("odd", "/o.va", None, {"xyceModelGroup": "JFET", "xyceLevelNumber": "3"})))


# =============================================================================================
# vacask.py
# =============================================================================================

class TestVacaskSpectre(unittest.TestCase):
    def test_golden_cond_npn(self):
        text = vacask.render(npn_mod_netlist())
        golden(self, "cond_npn_vacask.sim", text)
        self.assertIn("  @if real(area<1e-10)\n    npn_mod (c b e s) m_npn10x10 $mfactor=$mfactor\n  @else\n"
                      "    @if real(area<4e-10)\n      npn_mod (c b e s) m_npn20x20 $mfactor=$mfactor\n    @else\n"
                      "      npn_mod (c b e s) m_npn_default $mfactor=$mfactor\n    @end\n  @end\n", text)
        self.assertEqual(len(lines(text, r"^  model m_npn")), 3)                       # E44: none before

    def test_golden_spectre_group(self):
        nl = group_netlist()
        text = vacask.render(nl)
        golden(self, "group_vacask.sim", text)
        chain = lines(text, r"^@(?:else)?if |^  m1 ")
        self.assertEqual(len(chain), 25)                                                  # 12 guards, 13 copies
        self.assertEqual(chain[0], "@if 0.0")
        self.assertEqual(chain[2], "@elseif 1.0")                                       # entry 2
        self.assertEqual(chain[18], "@elseif 1.0")                                      # entry 10 also holds it
        self.assertIn("'m_nch.2'", chain[3])
        self.assertNotIn("lmin", text)
        self.assertIn("model 'm_nch.1' sp_bsim3v3 type=1.0 tox=4e-09 toxm=4e-09 u0=0.04 vth0=0.3 capmod=2.0", text)
        with self.assertRaisesRegex(NoteError, "no bin of nch for l=0.05, w=2e-06"):
            vacask.render(group_netlist(l=0.05))

    def test_golden_worked_example(self):
        """§7.1's control block for §7.3, byte for byte."""
        nl = worked_example()
        pl, steps = worked_plan()
        names = {}
        text = vacask.render(nl, plan=pl, sigmap=allpub_map(steps), names=names)
        golden(self, "worked_vacask.sim", text)
        control = text[text.index("control\n"):]
        self.assertEqual(control, (
            "control\n"
            "  abort except analysis\n"
            '  options rawfile="binary" strictoutput=0 strictsave=2 tran_lteimplicit=0 temp=27.0 tnom=27.0 '
            "reltol=0.001 vntol=1e-06 abstol=1e-12 gmin=1e-12\n"
            "  var vamos_v_rval=1000.0\n"
            "  save default\n"
            "  analysis vamos_a1 op\n"
            '  analysis vamos_a2 ac from=1.0 to=1000000.0 mode="dec" points=10\n'
            '  options relref="sigglobal" tran_method="trap" tran_lteratio=3.5\n'
            '  sweep vamos_w1 variable="vamos_v_rval" values=[1000.0, 2000.0] continuation=0\n'
            "    analysis vamos_a3 tran step=5e-06 stop=0.005 maxstep=0.0001\n"
            '  options relref="alllocal"\n'
            "endc\n"))
        self.assertIn("parameters rval=vamos_v_rval\n", text)                            # the variable hook
        self.assertIn('V1 (in 0) vamos_vsource type="pulse" val0=0.0 val1=1.0 delay=0.001 rise=1e-06 fall=1e-06 '
                      "width=0.004 period=0.01 mag=1.0 phase=0.0\n", text)             # derived dc: no dc=
        self.assertEqual(names["vamos_a1"][("v", "X1.b")], "X1:b")
        self.assertEqual(names["vamos_a3"][("i", "V1")], "V1:flow(br)")
        self.assertEqual(vacask.names_for(nl, pl, allpub_map(steps)), names)
        self.assertEqual(vacask.render(nl), vacask.render(nl, plan=None, sigmap=None, names=None))

    def test_per_type_saves_and_strictsave(self):
        nl = divider(extra=[Subckt("cell", ["a"], body=[R("r3", "a", "0", 1000.0)]),
                            I("x1", "x", ["out"], master="cell")])
        op = step("vamos_a1", "op", options=OPTIONS)
        ac = step("vamos_a2", "ac", own=freq_level(), options=OPTIONS)
        nz = step("vamos_a3", "noise", own=freq_level(lid="vamos_f2"), options=OPTIONS, noise_input="v1",
                  args={"p": "out", "n": "0"})
        op2 = step("vamos_a4", "op", options=OPTIONS)
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o")] + [analysis(s) for s in (op, ac, nz, op2)])
        sm = sigmap({
            "vamos_a1": [(("v", "out"), "out", "V"), (("i", "v1"), "v1:p", "I"), (("i", "r1"), "r1:1", "I"),
                         (("v", "x1.a"), "x1.a", "V")],
            "vamos_a2": [(("v", "out"), "out", "V"), (("i", "v1"), "v1:p", "I")],
            "vamos_a3": [(("noise", "r1"), "r1", "resistor"), (("noise", "x1.r3"), "x1.r3", "resistor"),
                         (("onoise",), "out", "V/sqrt(Hz)"), (("gain",), "gain", "V/V"), (("inoise",), "in", "V/sqrt(Hz)")],
            "vamos_a4": [(("v", "out"), "out", "V"), (("i", "v1"), "v1:p", "I"), (("i", "r1"), "r1:1", "I"),
                         (("v", "x1.a"), "x1.a", "V")]})
        names = {}
        text = vacask.render(nl, plan=pl, sigmap=sm, names=names)
        control = text[text.index("control\n"):]
        self.assertIn("strictsave=2", control)
        self.assertEqual(lines(control, r"^  (save|clear)"), [
            "  save v(out) i(v1) p(r1, i) v('x1:a')",
            "  clear saves", "  save dv(out) di(v1)",
            "  clear saves", "  save nc(r1) nc('x1:r3')",
            "  clear saves", "  save v(out) i(v1) p(r1, i) v('x1:a')"])
        self.assertIn('  analysis vamos_a3 noise out="out" in="v1" from=1.0 to=1000000.0 mode="dec" points=10\n', control)
        self.assertEqual(names["vamos_a1"], {("v", "out"): "out", ("i", "v1"): "v1:flow(br)", ("i", "r1"): "r1.i",
                                             ("v", "x1.a"): "x1:a"})
        self.assertEqual(names["vamos_a3"], {("noise", "r1"): "n(r1)", ("noise", "x1.r3"): "n(x1:r3)",
                                             ("onoise",): "onoise", ("gain",): "gain", ("inoise",): "gain"})
        # allpub plus a resistor current: save default p(r1, i) (§6.1) [E84]
        sm2 = sigmap({"vamos_a1": sm.per_step["vamos_a1"]}, ["vamos_a1"])
        pl2 = plan_of([P.Action("options", dict(OPTIONS), None, "o"), analysis(op)])
        self.assertIn("  save default p(r1, i)\n", vacask.render(nl, plan=pl2, sigmap=sm2))

    def test_options_lines_and_set(self):
        nl = divider()
        op = step("vamos_a1", "op", options=OPTIONS)
        hot = dict(OPTIONS, temp=85.0, chargeabstol=1e-14)
        op2 = step("vamos_a2", "op", options=hot)
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o"), analysis(op),
                      P.Action("options", {"temp": 85.0, "chargeabstol": 1e-14}, None, "set"), analysis(op2)])
        text = vacask.render(nl, plan=pl, sigmap=sigmap({}, []))
        self.assertIn("  options temp=85.0 chgtol=1e-14\n", text)
        self.assertNotIn("abort always", text)
        # an options action without temp/tnom: 27 on both (§3.10)
        pl = plan_of([P.Action("options", {"gmin": 1e-12}, None, "o"), analysis(op)])
        self.assertIn('options rawfile="binary" strictoutput=0 strictsave=2 tran_lteimplicit=0 temp=27.0 tnom=27.0 gmin=1e-12\n',
                      vacask.render(nl, plan=pl, sigmap=sigmap({}, [])))
        # tran settings: gear3 with maxord, pointlocal, the restore of what changed, the trapgear2 note
        tr = step("vamos_a3", "tran", options=OPTIONS, args={"step": 1e-6, "stop": 1e-3, "maxstep": 1e-5, "reltol": 1e-4,
                                                               "relref": "pointlocal", "method": "gear3", "lteratio": 10.0,
                                                               "skipdc": "yes"})
        tr2 = step("vamos_a4", "tran", options=OPTIONS, args={"step": 1e-6, "stop": 1e-3, "method": "trapgear2",
                                                                "relref": "sigglobal", "lteratio": 3.5, "reltol": 1e-2})
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o"), analysis(tr), analysis(tr2)])
        notes = []
        text = vacask.render(nl, plan=pl, sigmap=sigmap({}, []), notes=notes)
        control = text[text.index("control\n"):]
        self.assertEqual(lines(control, r"^  (options|  analysis|analysis)"), [
            '  options rawfile="binary" strictoutput=0 strictsave=2 tran_lteimplicit=0 temp=27.0 tnom=27.0 reltol=0.001 '
            "vntol=1e-06 abstol=1e-12 gmin=1e-12",
            '  options reltol=0.0001 relrefsol="pointlocal" relrefres="pointlocal" relreflte="pointlocal" '
            'tran_method="gear" tran_maxord=3 tran_lteratio=10.0',
            '  analysis vamos_a3 tran step=1e-06 stop=0.001 maxstep=1e-05 icmode="uic"',
            '  options reltol=0.001 relrefsol="relref" relrefres="relref" relreflte="relref" tran_method="trap" '
            "tran_maxord=2 tran_lteratio=3.5",
            '  options reltol=0.01 relref="sigglobal"',
            "  analysis vamos_a4 tran step=1e-06 stop=0.001",
            '  options reltol=0.001 relref="alllocal"'])
        self.assertTrue(any("trapgear2" in n.message for n in notes))

    def test_alters_sources_and_sweep_targets(self):
        nl = divider(src=pulse_source(dc=0.9, p="in"), extra=[card("rm", "r", None, "resistor", tc1=1e-3),
                                                      I("r3", "r", ["out", "0"], master="rm", value=Num(2000.0), prim="resistor")])
        nl.body.insert(1, Param("rval", Num(1000.0)))
        nl.values = {"rval": 1000.0}
        src = copy.deepcopy(nl.body[0].source)
        src.args["v1"] = Num(0.2)
        op = step("vamos_a1", "op", options=OPTIONS)
        tr = step("vamos_a2", "tran", options=OPTIONS, args={"step": 1e-7, "stop": 1e-5, "maxstep": 1e-6})
        dc = step("vamos_a3", "dc", options=OPTIONS, own=level(("instance", "r1", "r"), values_spec(1000, 3000), "vamos_w2"))
        ac = step("vamos_a4", "ac", options=OPTIONS, own=level(("instance", "v1", "val0"), values_spec(0.2, 0.4), "vamos_w3"),
                  args={"freq": 1e3})
        pl = plan_of([
            P.Action("options", dict(OPTIONS), None, "o"), P.Action("var", {"rval": 1000.0}, None, "v"),
            analysis(op),
            P.Action("alter", {"target": ("instance", "r1", "r"), "value": 2000.0}, None, "a1"),
            P.Action("alter", {"target": ("model", "rm", "tc1"), "value": 2e-3}, None, "a2"),
            P.Action("alter", {"target": ("option", "temp", ""), "value": 50.0}, None, "a3"),
            P.Action("alter", {"target": ("variable", "rval", ""), "value": 1500.0}, None, "a4"),
            P.Action("alter", {"target": ("instance", "v1", "val0"), "value": 0.2, "source": src}, None, "a5"),
            P.Action("source", {"path": "v1", "type": "pulse", "source": src}, None, "s1"),
            analysis(tr),
            P.Action("source", {"path": "v1", "type": "dc", "source": src}, None, "s2"),
            analysis(dc), analysis(ac)], {"rval": 1000.0})
        text = vacask.render(nl, plan=pl, sigmap=sigmap({}, []))
        control = text[text.index("control\n"):]
        self.assertIn('v1 (in 0) vamos_vsource type="dc" val0=0.0 val1=1.0 delay=1e-06 rise=1e-09 fall=1e-09 width=1e+30 dc=0.9\n',
                      text)
        self.assertEqual(lines(control, r"^  (alter|var |options temp|sweep|  analysis)"), [
            "  var vamos_v_rval=1000.0",
            '  alter instance("r1") r=2000.0',
            '  alter model("m_rm") tc1=0.002',
            "  options temp=50.0",
            "  var vamos_v_rval=1500.0",
            '  alter instance("v1") type="dc" val0=0.2 val1=1.0 delay=1e-06 rise=1e-09 fall=1e-09 width=1e+30 dc=0.9',
            '  alter instance("v1") type="pulse" val0=0.2 val1=1.0 delay=1e-06 rise=1e-09 fall=1e-09 width=1e+30 dc=0.9',
            '  alter instance("v1") type="dc" val0=0.2 val1=1.0 delay=1e-06 rise=1e-09 fall=1e-09 width=1e+30 dc=0.9',
            '  sweep vamos_w2 instance="r1" parameter="r" values=[1000.0, 3000.0] continuation=1',
            "    analysis vamos_a3 op",
            '  sweep vamos_w3 instance="v1" parameter="val0" values=[0.2, 0.4] continuation=1',
            "    analysis vamos_a4 ac values=[1000.0]"])
        # a model group alter names every entry; a group sweep is refused; a sub= target is overridable
        g = group_netlist()
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o"),
                      P.Action("alter", {"target": ("model", "nch", "vth0"), "value": 0.5}, None, "a"),
                      analysis(step("vamos_a1", "op", options=OPTIONS))])
        text = vacask.render(g, plan=pl, sigmap=sigmap({}, []))
        self.assertIn('  alter model("m_nch.1", "m_nch.2", "m_nch.3", "m_nch.4", "m_nch.5", "m_nch.6", "m_nch.7", "m_nch.8", '
                      '"m_nch.9", "m_nch.10", "m_nch.11", "m_nch.12") vth0=0.5\n', text)
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o"),
                      analysis(step("vamos_a1", "dc", options=OPTIONS, own=level(("model", "nch", "vth0"), values_spec(0.4, 0.5))))])
        with self.assertRaisesRegex(NoteError, "model group"):
            vacask.render(g, plan=pl, sigmap=sigmap({}, []))
        # sub= target: the subckt parameter becomes overridable (NOT_GIVEN) when it is dependent
        nl = worked_example()
        nl.body[-1].params = [Param("r", Binary("*", Name("rbase"), Num(2.0))), Param("rbase", Num(500.0))]
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o"),
                      analysis(step("vamos_a1", "dc", options=OPTIONS,
                                    own=level(("instance", "X1", "r"), values_spec(1000, 2000), "vamos_w1")))],
                     overridden={"rcsec": {"r"}})
        text = vacask.render(nl, plan=pl, sigmap=sigmap({}, []))
        self.assertIn("  parameters r=-1.2345678901234568e-300\n", text)
        self.assertIn('  sweep vamos_w1 instance="X1" parameter="r" values=[1000.0, 2000.0] continuation=1\n', text)

    def test_sources_quoting_zero_resistor_isnoisy_va_names(self):
        nl = Netlist(title="spectre instance rules", dialect="spectre")
        nl.hdl = ["toy_resistor.va"]
        nl.body = [pulse_source(dc=0.9, name="vgiven", p="a"), pulse_source(name="vderived", p="b", v1=0.3),
                   I("isrc", "i", ["0", "c"], prim="isource", source=Source(dc=Num(1e-3), wave="sin", args={
                       "vo": Num(0.0), "va": Num(1e-3), "freq": Num(1e3), "td": Num(0.0), "theta": Num(0.0), "phase": Num(0.0)})),
                   V("v7", "007", "0", 2.0), V("v00", "00", "0", -1.5), V("v5", "7", "0", 1.0),
                   R("r7", "007", "0", 1000.0), R("r00", "00", "0", 1000.0), R("r5", "7", "0", 1000.0),
                   R("rshort", "a", "a2", 0.0), R("ra", "a2", "0", 1000.0), R("rb", "b", "0", 1000.0),
                   R("rq", "c", "0", 1000.0, isnoisy=Str("no")), R("rqy", "c", "0", 2000.0, isnoisy=Str("yes")),
                   R("rqt", "c", "0", 3000.0, isnoisy=Str("no"), tc1=1e-3),
                   I("y1", "y", ["a", "0"], master="resistor", params={"R": Num(3e3)}, prim="resistor")]
        nl.va_modules = {"resistor": VaModule("resistor", nl.hdl[0], ["R"], {"xyceModelGroup": "Resistor", "xyceLevelNumber": "6"})}
        nl.temp, nl.tnom = 27.0, 27.0
        notes = []
        text = vacask.render(nl, plan=plan_of([P.Action("options", dict(OPTIONS), None, "o"),
                                               analysis(step("vamos_a1", "op", options=OPTIONS))]),
                             sigmap=sigmap({}, []), notes=notes)
        golden(self, "instances_vacask.sim", text)
        self.assertIn('vgiven (a 0) vamos_vsource type="dc" val0=0.0 val1=1.0 delay=1e-06 rise=1e-09 fall=1e-09 width=1e+30 dc=0.9\n', text)
        self.assertIn('vderived (b 0) vamos_vsource type="pulse" val0=0.3 val1=1.0 delay=1e-06 rise=1e-09 fall=1e-09 width=1e+30\n', text)
        self.assertIn('isrc (0 c) vamos_isource type="dc" sinedc=0.0 ampl=0.001 freq=1000.0 delay=0.0 theta=0.0 tdphase=0.0 dc=0.001\n', text)
        self.assertIn("v7 ('007' 0) vamos_vsource", text)
        self.assertIn("v00 ('00' 0) vamos_vsource", text)
        self.assertIn("v5 (7 0) vamos_vsource", text)
        self.assertIn("r7 ('007' 0) vamos_resistor r=1000.0\n", text)
        self.assertIn("rshort (a a2) vamos_vsource dc=0.0\n", text)
        self.assertIn("rq (c 0) vamos_resistor r=1000.0 noisy=0\n", text)
        self.assertIn("rqy (c 0) vamos_resistor r=2000.0\n", text)
        self.assertIn("rqt (c 0) vamos_sp_resistor r=3000.0 tc1=0.001 noisy=0\n", text)
        self.assertIn("model y1__va resistor r=3000.0\ny1 (a 0) y1__va\n", text)       # lower-cased [E64]
        self.assertTrue(any("r=0 printed as a 0 V" in n.message for n in notes))
        names = vacask.names_for(nl, plan_of([]), sigmap({"s": [(("i", "rshort"), "rshort:1", "I"), (("i", "rq"), "rq:1", "I"),
                                                                 (("v", "007"), "007", "V")]}))
        self.assertEqual(names["s"], {("i", "rshort"): "rshort:flow(br)", ("i", "rq"): "rq.i", ("v", "007"): "007"})

    def test_golden_nested_lift(self):
        nl = lift_netlist()
        text = vacask.render(nl)
        golden(self, "lift_vacask.sim", text)
        self.assertIn("subckt s1 (a b)\n  parameters rr=1000.0\n  parameters $mfactor=1.0\n"
                      "  xs (a mid) s1__s2 rr=rr $mfactor=$mfactor\n  r2 (mid b) vamos_resistor r=1000.0 $mfactor=$mfactor\nends\n", text)
        self.assertIn("subckt s1__s2 (x y)\n  parameters rr=0.0\n  parameters $mfactor=1.0\n"
                      "  r1 (x y) vamos_resistor r=rr $mfactor=$mfactor\nends\n", text)
        self.assertNotIn("subckt s2 ", text)
        # deeper nesting lifts recursively: s3 inside s2 reading rr needs s2 to carry rr too
        nl = lift_netlist()
        s1 = nl.body[0]
        s2 = s1.body[0]
        s3 = Subckt("s3", ["p", "q"], body=[R("r9", "p", "q", Name("rr"))])
        s2.body = [s3, I("xd", "x", ["x", "y"], master="s3")]
        text = vacask.render(nl)
        self.assertIn("subckt s1__s2 (x y)\n  parameters rr=0.0\n  parameters $mfactor=1.0\n  xd (x y) s2__s3 rr=rr $mfactor=$mfactor\nends\n", text)
        self.assertIn("subckt s2__s3 (p q)\n  parameters rr=0.0\n", text)
        # the HSPICE path keeps today's refusal
        nl = lift_netlist()
        nl.dialect = "hspice"
        with self.assertRaisesRegex(NoteError, "does not pass an enclosing subckt's parameters"):
            vacask.render(nl)

    def test_sweep_target_forms(self):
        nl = divider(src=pulse_source(dc=0.9, p="in"), extra=[card("rm", "r", None, "resistor", tc1=1e-3),
                                                      I("r3", "r", ["out", "0"], master="rm", value=Num(2000.0), prim="resistor")])
        lv_temp = level(("option", "temp", ""), SweepSpec("param", mode="lin", start=Num(0.0), stop=Num(100.0), count=4), "vamos_w1")
        lv_mod = level(("model", "rm", "tc1"), SweepSpec("param", mode="step", start=Num(0.0), stop=Num(1e-2), step=Num(1e-3)), "vamos_w2")
        lv_dec = level(("instance", "v1", "dc"), SweepSpec("param", mode="dec", start=Num(1.0), stop=Num(100.0), count=5), "vamos_w3")
        st = step("vamos_a1", "dc", options=OPTIONS, context=[lv_temp, lv_mod], own=lv_dec)
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o"), analysis(st)])
        control = vacask.render(nl, plan=pl, sigmap=sigmap({}, []))
        self.assertIn('  sweep vamos_w1 option="temp" from=0.0 to=100.0 mode="lin" points=4 continuation=1\n'
                      '    sweep vamos_w2 model="m_rm" parameter="tc1" from=0.0 to=0.01 step=0.001 continuation=1\n'
                      '      sweep vamos_w3 instance="v1" parameter="dc" from=1.0 to=100.0 mode="dec" points=5 continuation=1\n'
                      "        analysis vamos_a1 op\n", control)
        bad = step("vamos_a1", "dc", options=OPTIONS, own=level(("instance", "v1", "td1"), values_spec(1, 2)))
        with self.assertRaisesRegex(NoteError, "cannot be swept or altered directly on VACASK"):
            vacask.render(nl, plan=plan_of([P.Action("options", dict(OPTIONS), None, "o"), analysis(bad)]), sigmap=sigmap({}, []))

    def test_ic_nodeset_stores(self):
        nl = divider()
        nl.ics = {"out": 0.2, "x1.mid": Num(0.3)}
        nl.nodesets = {"in": 0.9}
        tr = step("vamos_a1", "tran", options=OPTIONS, args={"step": 1e-7, "stop": 1e-5}, stores="vamos_s1")
        ac = step("vamos_a2", "ac", options=OPTIONS, own=freq_level(), uses="vamos_s1", args={"prevoppoint": "yes"})
        tr2 = step("vamos_a3", "tran", options=OPTIONS, args={"step": 1e-7, "stop": 1e-5, "ic": "dc", "useprevic": "yes"}, uses="vamos_s1")
        tr3 = step("vamos_a4", "tran", options=OPTIONS, args={"step": 1e-7, "stop": 1e-5, "useprevic": "ns"}, uses="vamos_s1")
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o")] + [analysis(s) for s in (tr, ac, tr2, tr3)])
        control = vacask.render(nl, plan=pl, sigmap=sigmap({}, []))
        self.assertIn('  analysis vamos_a1 tran step=1e-07 stop=1e-05 ic={"out", 0.2, "x1:mid", 0.3} nodeset={"in", 0.9} store="vamos_s1"\n', control)
        self.assertIn('  analysis vamos_a2 ac from=1.0 to=1000000.0 mode="dec" points=10 nodeset="vamos_s1" opsolve=0\n', control)
        self.assertIn('  analysis vamos_a3 tran step=1e-07 stop=1e-05 ic="vamos_s1"\n', control)
        self.assertIn('  analysis vamos_a4 tran step=1e-07 stop=1e-05 ic={"out", 0.2, "x1:mid", 0.3} nodeset="vamos_s1"\n', control)

    def test_xf_and_noise_pair(self):
        nl = divider()
        xf = step("vamos_a1", "xf", options=OPTIONS, own=freq_level(), args={"p": "out", "n": "in"})
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o"), analysis(xf)])
        names = {}
        control = vacask.render(nl, plan=pl, sigmap=sigmap({"vamos_a1": [(("tf", "v1"), "v1:p", "V/V")]}), names=names)
        self.assertIn('  analysis vamos_a1 acxf out=["out", "in"] from=1.0 to=1000000.0 mode="dec" points=10\n', control)
        self.assertEqual(lines(control, r"^  save"), [])                                  # acxf writes every tf()
        self.assertEqual(names["vamos_a1"], {("tf", "v1"): "tf(v1)"})
        nz = step("vamos_a2", "noise", options=OPTIONS, own=freq_level(), args={})
        with self.assertRaisesRegex(NoteError, "without an output"):
            vacask.render(nl, plan=plan_of([P.Action("options", dict(OPTIONS), None, "o"), analysis(nz)]), sigmap=sigmap({}, []))


# =============================================================================================
# xyce.py
# =============================================================================================

class TestXyceSpectre(unittest.TestCase):
    def test_golden_cond_npn(self):
        text = xyce.render(npn_mod_netlist())
        golden(self, "cond_npn_xyce.cir", text)
        # three paths, three resolutions: each variant prints its branch's instance and the card it uses
        self.assertIn(".subckt npn_mod c b e s params: area=5e-12 vamos_mfactor=1.0\nqnpn_mod c b e s npn20x20 m={vamos_mfactor}\n"
                      ".model npn20x20 npn level=1 is=3.77e-16 vaf=60.5\n.ends npn_mod\n", text)
        self.assertIn(".subckt npn_mod__vb1 c b e s params: area=5e-12 vamos_mfactor=1.0\nqnpn_mod c b e s npn10x10 m={vamos_mfactor}\n"
                      ".model npn10x10 npn level=1 is=3.5e-16 vaf=61.5\n.ends npn_mod__vb1\n", text)
        self.assertIn("qnpn_mod c b e s npn_default m={vamos_mfactor}\n.model npn_default npn level=1 is=3.2e-16 vaf=59.8\n.ends npn_mod__vb2\n", text)
        self.assertIn("xq1 1 2 0 0 npn_mod area=3.5e-10\nxq2 1 3 0 0 npn_mod__vb1 area=2.5e-11\nxq3 1 3 0 0 npn_mod__vb2 area=1e-09\n", text)
        nl = npn_mod_netlist()
        nl.body[3].body[0].branches[0] = (Name("nowhere"), nl.body[3].body[0].branches[0][1])
        with self.assertRaisesRegex(NoteError, "condition nowhere is not a number"):
            xyce.render(nl)

    def test_golden_spectre_group(self):
        text = xyce.render(group_netlist())
        golden(self, "group_xyce.cir", text)
        self.assertIn("m1 d g 0 0 nch.2 w=2e-06 l=5e-07 nrd=0.0 nrs=0.0\n", text)         # bound per path, group order
        self.assertIn(".model nch.2 nmos level=9 tox=4e-09 toxm=4e-09 u0=0.04 vth0=0.42 lmax=1e-06 wmax=0.0001 lmin=2e-07 wmin=1e-06 capmod=2.0\n", text)
        self.assertEqual(len(lines(text, r"^\.model nch\.")), 1)                           # only the bound entry
        with self.assertRaisesRegex(NoteError, "no bin of nch for l=0.05"):
            xyce.render(group_netlist(l=0.05))

    def test_golden_worked_example_decks(self):
        if not spectre_eval_ready():
            self.skipTest("S6 integration: expr.evaluate(dialect='spectre') is S1's and is not there yet")
        nl = worked_example()
        pl, steps = worked_plan("xyce")
        sm = allpub_map(steps)
        names = {}
        texts = [xyce.render(nl, step=s, sigmap=sm, names=names) for s in steps]
        for s, text in zip(steps, texts):
            golden(self, "worked_%s_xyce.cir" % s.kind, text)
        op, ac, tr = texts
        self.assertIn("V1 in 0 DC 0.0 AC 1.0 0.0 PULSE(0.0 1.0 0.001 1e-06 1e-06 0.004 0.01)\n", op)   # derived DC: val0
        self.assertIn(".options device temp=27.0 tnom=27.0 gmin=1e-12\n.OP\n.PRINT DC FORMAT=RAW FILE=vamos_a1.raw V(*) I(V1)\n.end\n", op)
        self.assertIn(".AC DEC 10 1.0 1000000.0\n.PRINT AC FORMAT=RAW FILE=vamos_a2.raw V(*) I(V1)\n", ac)
        self.assertIn(".options timeint method=TRAP\n.STEP rval LIST 1000.0 2000.0\n.TRAN 5e-06 0.005 0.0 0.0001\n"
                      ".PRINT TRAN FORMAT=RAW FILE=vamos_a3.raw V(*) I(V1)\n", tr)
        self.assertEqual(names["vamos_a1"], {("v", "in"): "V(IN)", ("v", "out"): "V(OUT)", ("v", "X1.b"): "V(X1:B)",
                                             ("i", "V1"): "I(V1)"})
        self.assertEqual(xyce.names_for(nl, pl, sm), names)
        self.assertEqual(xyce.render(nl), xyce.render(nl, step=None, sigmap=None, names=None))

    def test_sweeps_steps_and_hooks(self):
        nl = divider(src=pulse_source(dc=0.9, p="in"), extra=[Subckt("cell", ["a"], params=[Param("w", Num(1e-6))],
                                                             body=[R("rc", "a", "0", Binary("*", Name("w"), Num(1e9)))]),
                                                      I("x1", "x", ["out"], master="cell", params={"w": Num(2e-6)}),
                                                      card("rm", "r", None, "resistor", tc1=1e-3),
                                                      I("r3", "r", ["out", "0"], master="rm", value=Num(2000.0), prim="resistor")])
        if not spectre_eval_ready():
            self.skipTest("S6 integration: expr.evaluate(dialect='spectre') is S1's and is not there yet")
        lv_r = level(("instance", "r1", "r"), values_spec(1000, 3000), "vamos_w1")
        lv_t = level(("option", "temp", ""), SweepSpec("param", mode="lin", start=Num(0.0), stop=Num(100.0), count=4), "vamos_w2")
        lv_m = level(("model", "rm", "tc1"), SweepSpec("param", mode="dec", start=Num(1e-4), stop=Num(1e-2), count=1), "vamos_w3")
        lv_x = level(("instance", "x1", "w"), values_spec(1e-6, 3e-6), "vamos_w4")
        lv_s = level(("instance", "v1", "val0"), values_spec(0.2, 0.4), "vamos_w5")
        lv_p = level(("variable", "rval", ""), values_spec(1, 2), "vamos_w6")
        # a dc sweep of a resistor under two .STEPs: the context innermost first [E20]
        st = step("vamos_a1", "dc", options=OPTIONS, context=[lv_t, lv_m], own=lv_r)
        text = xyce.render(nl, step=st, sigmap=sigmap({"vamos_a1": [(("v", "out"), "out", "V")]}))
        self.assertIn(".STEP DEC RM:TC1 0.0001 0.01 1\n.STEP TEMP 0.0 100.0 25.0\n.DC R1:R LIST 1000.0 3000.0\n"
                      ".PRINT DC FORMAT=RAW FILE=vamos_a1.raw V(OUT)\n", text)
        # an op under two .STEPs is .DC vamos_op LIST 0; under one .STEP it is .OP [E20]
        text = xyce.render(nl, step=step("vamos_a2", "op", options=OPTIONS, context=[lv_t, lv_p]), sigmap=sigmap({}, []))
        self.assertIn(".STEP rval LIST 1.0 2.0\n.STEP TEMP 0.0 100.0 25.0\n.param vamos_op=0\n.DC vamos_op LIST 0\n", text)
        text = xyce.render(nl, step=step("vamos_a3", "op", options=OPTIONS, context=[lv_t]), sigmap=sigmap({}, []))
        self.assertIn(".STEP TEMP 0.0 100.0 25.0\n.OP\n", text)
        # sub= and source-field hooks [E20, E71]
        text = xyce.render(nl, step=step("vamos_a4", "dc", options=OPTIONS, own=lv_x), sigmap=sigmap({}, []))
        self.assertIn("x1 out cell w={vamos_x_x1_w}\n", text)
        self.assertIn(".param vamos_x_x1_w=2e-06\n.DC vamos_x_x1_w LIST 1e-06 3e-06\n", text)
        text = xyce.render(nl, step=step("vamos_a5", "tran", options=OPTIONS, context=[lv_s], args={"step": 1e-7, "stop": 1e-5}),
                           sigmap=sigmap({}, []))
        self.assertIn("v1 in 0 DC 0.9 PULSE({vamos_x_v1_val0} 1.0 1e-06 1e-09 1e-09 1e+30)\n", text)
        self.assertIn(".param vamos_x_v1_val0=0.0\n.STEP vamos_x_v1_val0 LIST 0.2 0.4\n", text)
        derived = divider(src=pulse_source(v1=0.3, p="in"))
        lv_dc = level(("instance", "v1", "dc"), values_spec(0.2, 0.4), "vamos_w7")
        text = xyce.render(derived, step=step("vamos_a6", "dc", options=OPTIONS, own=lv_dc), sigmap=sigmap({}, []))
        self.assertIn("v1 in 0 DC {vamos_x_v1_dc} PULSE(0.3 1.0 1e-06 1e-09 1e-09 1e+30)\n", text)
        self.assertIn(".param vamos_x_v1_dc=0.3\n.DC vamos_x_v1_dc LIST 0.2 0.4\n", text)
        self.assertIn("v1 in 0 DC 0.3 PULSE(0.3", xyce.render(derived, step=step("vamos_a7", "op", options=OPTIONS), sigmap=sigmap({}, [])))

    def test_noise_input_inside_a_subckt_prints_ac_1_0_in_the_body(self):
        """§5.6/§8.4: a hierarchical iprobe= (plan.build's step.noise_input "I2.vb") is a source in the subckt
        body: its line gets AC 1 0 and .NOISE names it XI2:VB; plan.build's §5.3 rule guarantees the one path
        (merge, phase 1: the top-level-only match was S3's seam)."""
        inner = Subckt("cell", ["a"], body=[R("r3", "a", "Mid", 1000.0), V("vb", "Mid", "0", dc=0.0)])
        nl = divider(extra=[inner, I("I2", "x", ["out"], master="cell")])
        nz = step("vamos_a1", "noise", options=OPTIONS, own=freq_level(), noise_input="I2.vb", args={"p": "out", "n": "0"})
        text = xyce.render(nl, step=nz, sigmap=sigmap({}, []))
        self.assertIn("vb Mid 0 DC 0.0 AC 1 0\n", text)
        self.assertIn(".NOISE V(OUT) XI2:VB DEC 10 1.0 1000000.0\n", text)
        self.assertNotIn("AC 1 0", text.split("vb Mid 0 DC 0.0 AC 1 0")[0])          # not the top-level v1
        nz2 = step("vamos_a2", "noise", options=OPTIONS, own=freq_level(), noise_input="v1", args={"p": "out", "n": "0"})
        text = xyce.render(nl, step=nz2, sigmap=sigmap({}, []))
        self.assertIn("vb Mid 0 DC 0.0\n", text)                                     # the body's source untouched
        self.assertIn("v1 in 0 DC 1.0 AC 1 0\n", text)

    def test_ac_noise_forms_and_printed_paths(self):
        inner = Subckt("cell", ["a"], body=[R("r3", "a", "Mid", 1000.0), R("r4", "Mid", "0", 1000.0)])
        nl = divider(extra=[inner, I("I2", "x", ["out"], master="cell")])
        nl.ics = {"out": 0.2, "I2.Mid": 0.1}
        nl.nodesets = {"in": 0.9}
        refs = [(("v", "out"), "out", "V"), (("v", "I2.Mid"), "I2.Mid", "V"), (("i", "v1"), "v1:p", "I"), (("i", "I2.r3"), "I2.r3:1", "I")]
        ac = step("vamos_a1", "ac", options=OPTIONS, own=freq_level("lin", 1.0, 1e3, 4))
        names = {}
        text = xyce.render(nl, step=ac, sigmap=sigmap({"vamos_a1": refs}), names=names)
        self.assertIn(".NODESET V(IN)=0.9\n.AC LIN 5 1.0 1000.0\n.PRINT AC FORMAT=RAW FILE=vamos_a1.raw V(OUT) V(XI2:MID) I(V1) I(XI2:R3)\n", text)
        self.assertNotIn(".IC", text)
        self.assertEqual(names["vamos_a1"], {("v", "out"): "V(OUT)", ("v", "I2.Mid"): "V(XI2:MID)", ("i", "v1"): "I(V1)",
                                             ("i", "I2.r3"): "I(XI2:R3)"})
        # values: a .DATA table; a parameter sweep at a fixed frequency: .STEP plus one-row DATA
        ac2 = step("vamos_a2", "ac", options=OPTIONS, own=level(("freq", "", ""), SweepSpec("freq", mode="values", values=[Num(10.0), Num(20.0)]), "vamos_f2"))
        text = xyce.render(nl, step=ac2, sigmap=sigmap({}, []))
        self.assertIn(".data vamos_f_vamos_a2\n+ hertz\n+ 10.0\n+ 20.0\n.enddata\n.AC DATA=vamos_f_vamos_a2\n", text)
        ac3 = step("vamos_a3", "ac", options=OPTIONS, own=level(("instance", "r1", "r"), values_spec(1000, 2000)), args={"freq": 1e3})
        text = xyce.render(nl, step=ac3, sigmap=sigmap({}, []))
        self.assertIn(".STEP R1:R LIST 1000.0 2000.0\n.data vamos_f_vamos_a3\n+ hertz\n+ 1000.0\n.enddata\n.AC DATA=vamos_f_vamos_a3\n", text)
        # noise: the input source prints AC 1 0, the output pair through printed paths, DNO() columns [E19, E73, E84]
        nz = step("vamos_a4", "noise", options=OPTIONS, own=freq_level(), noise_input="v1", args={"p": "out", "n": "I2.Mid"})
        nrefs = [(("noise", "r1"), "r1", "resistor"), (("noise", "I2.r3"), "I2.r3", "resistor"), (("onoise",), "out", "V/sqrt(Hz)"),
                 (("gain",), "gain", "V/V"), (("inoise",), "in", "V/sqrt(Hz)")]
        names = {}
        text = xyce.render(nl, step=nz, sigmap=sigmap({"vamos_a4": nrefs}), names=names)
        self.assertIn("v1 in 0 DC 1.0 AC 1 0\n", text)
        self.assertIn(".NOISE V(OUT,XI2:MID) V1 DEC 10 1.0 1000000.0\n.PRINT NOISE FILE=vamos_a4.prn DNO(R1) DNO(XI2:R3) ONOISE INOISE\n", text)
        self.assertEqual(names["vamos_a4"], {("noise", "r1"): "DNO(R1)", ("noise", "I2.r3"): "DNO(XI2:R3)", ("onoise",): "ONOISE",
                                             ("gain",): "INOISE", ("inoise",): "INOISE"})
        # .IC only in a tran deck whose ic is all or node, dropping the nodeset with a note [E18]
        tr = step("vamos_a5", "tran", options=OPTIONS, args={"step": 1e-7, "stop": 1e-5, "method": "gear2", "skipdc": "yes"})
        notes = []
        text = xyce.render(nl, step=tr, sigmap=sigmap({}, []), notes=notes)
        self.assertIn(".options timeint method=GEAR maxord=2\n.IC V(OUT)=0.2 V(XI2:MID)=0.1\n.TRAN 1e-07 1e-05 UIC\n", text)
        self.assertNotIn(".NODESET", text)
        self.assertTrue(any(".nodeset dropped" in n.message for n in notes))
        tr2 = step("vamos_a6", "tran", options=OPTIONS, args={"step": 1e-7, "stop": 1e-5, "ic": "dev"})
        text = xyce.render(nl, step=tr2, sigmap=sigmap({}, []))
        self.assertIn(".NODESET V(IN)=0.9\n.TRAN 1e-07 1e-05\n", text)
        self.assertNotIn(".IC", text)
        with self.assertRaisesRegex(NoteError, "xf runs on VACASK only"):
            xyce.render(nl, step=step("vamos_a7", "xf", options=OPTIONS, own=freq_level(), args={"p": "out", "n": "0"}), sigmap=sigmap({}, []))
        # the tolerance note of §0
        notes = []
        xyce.render(nl, step=step("vamos_a8", "op", options=OPTIONS), sigmap=sigmap({}, []), notes=notes)
        self.assertEqual(len([n for n in notes if "not mapped on Xyce" in n.message]), 3)

    def test_isnoisy_g_form_and_r_zero(self):
        nl = divider(r1=1000.0)
        nl.body[1].params["isnoisy"] = Str("no")
        nl.body.append(R("rshort", "out", "o2", 0.0))
        nl.body.append(R("rt", "o2", "0", 1000.0, isnoisy=Str("no"), tc1=1e-3))
        notes = []
        text = xyce.render(nl, step=step("vamos_a1", "op", options=OPTIONS), sigmap=sigmap({}, []), notes=notes)
        self.assertIn("gr1 in out in out 0.001\n", text)                                     # [E32]
        self.assertIn("rshort out o2 0.0\n", text)                                           # Xyce takes R=0 [E22]
        self.assertIn("rt o2 0 1000.0 tc1=0.001\n", text)
        self.assertTrue(any("isnoisy=no with tc1/tc2" in n.message for n in notes))
        # an options-free step prints temp=27 tnom=27 (§3.10), and the plan-less render of a Spectre netlist
        # takes no HSPICE tolerance note (§4.4)
        self.assertIn(".options device temp=27.0 tnom=27.0\n", xyce.render(nl, step=step("vamos_a2", "op"), sigmap=sigmap({}, [])))
        nl.options = {"reltol": Num(1e-4), "gmin": Num(1e-13)}
        nl.analyses = [Analysis("tran", {"step": 1e-9, "stop": 1e-8}, "deck:9")]
        for render in (vacask.render, xyce.render):
            notes = []
            text = render(nl, notes=notes)
            self.assertIn("temp=27.0 tnom=27.0 gmin=1e-13", text)
            self.assertEqual([n for n in notes if "not mapped" in n.message], [])
        with self.assertRaisesRegex(T.TableError, "needs a probe"):
            xyce.names_for(nl, plan_of([]), sigmap({"s": [(("i", "r1"), "r1:1", "I")]}))

    def test_golden_verilog_a_letter_device(self):
        nl = va_resistor(params={"R": Num(3e3)})
        text = xyce.render(nl)
        golden(self, "va_letter_xyce.cir", text)
        self.assertIn(".model resistor__vamos r level=6\nry1 a b resistor__vamos R=3000.0\n", text)
        self.assertNotIn("yresistor", text)
        # a MOSFET module: the polarity from the card's TYPE value, consumed; one card per polarity
        mod = VaModule("bsim6", "/b.va", None, {"xyceModelGroup": "MOSFET", "xyceLevelNumber": "77", "xyceTypeVariable": "TYPE"})
        nl = Netlist(title="va mos", dialect="spectre", hdl=["/b.va"], va_modules={"bsim6": mod})
        nl.body = [V("vd", "d", "0", 1.0), V("vg", "g", "0", 1.0),
                   I("m1", "y", ["d", "g", "0", "0"], master="bsim6", params={"type": Num(-1.0), "l": Num(1e-7)}, prim="bsim6"),
                   I("m2", "y", ["d", "g", "0", "0"], master="bsim6", params={"TYPE": Num(1.0)}, prim="bsim6"),
                   I("m3", "y", ["d", "g", "0", "0"], master="bsim6", params={}, prim="bsim6")]
        nl.analyses = [Analysis("tran", {"step": 1e-10, "stop": 1e-9}, "deck:9")]
        text = xyce.render(nl)
        self.assertIn(".model bsim6__vamos_pmos pmos level=77\nm1 d g 0 0 bsim6__vamos_pmos l=1e-07\n"
                      ".model bsim6__vamos nmos level=77\nm2 d g 0 0 bsim6__vamos\nm3 d g 0 0 bsim6__vamos\n", text)
        # an unattributed module keeps the Y form and its refusals; a multiplier is refused on both routes
        text = xyce.render(va_resistor(attrs=False))
        self.assertIn(".model resistor__vamos resistor\nyresistor y1 a b resistor__vamos\n", text)
        with self.assertRaisesRegex(NoteError, "PyMS ignores"):
            xyce.render(va_resistor(attrs=False, params={"R": Num(3e3)}))
        with self.assertRaisesRegex(NoteError, "takes no multiplier"):
            xyce.render(va_resistor(params={"R": Num(3e3)}, m=2))
        # the HSPICE route (vcs-ams) prints the letter device too (§4, §11 HSPICE-route regressions)
        text = xyce.render(va_resistor(dialect="hspice", params={"R": Num(3e3)}))
        self.assertIn(".model resistor__vamos r level=6\nry1 a b resistor__vamos R=3000.0\n", text)
        self.assertEqual(xyce.printed(nl.body[2], {"bsim6": mod}), "m1")
        self.assertEqual(xyce.printed(I("q9", "y", ["d", "g", "0", "0"], master="bsim6"), {"bsim6": mod}), "mq9")
        self.assertEqual(xyce.printed(I("q9", "y", ["d", "g", "0", "0"], master="bsim6")), "q9")

    def test_output_and_device_summary_problems(self):
        nl = va_resistor()
        out = ("***** Reading and parsing netlist...\n"
               "Netlist warning in file smoke.cir at or near line 2\n"
               " No model parameter LMIN found for model NCH.1 of type NMOS, parameter\n"
               " ignored.\n"
               "Netlist warning in file smoke.cir at or near line 3\n"
               " No model parameter BOGUS found for model NCH of type NMOS, parameter ignored.\n"
               "Netlist error in file smoke.cir at or near line 5\n"
               " Unrecognized parameter FOO for device M1\n"
               "Netlist error in file smoke.cir at or near line 7\n"
               " Could not find node OUT2\n")
        b = card("nch.1", "nmos", 49, "bsim3v3", lmin=1e-7)
        b.base, b.bin_index = "nch", 1
        probs = xyce.output_problems(out, Netlist(body=[b]))
        self.assertEqual([n.severity for n in probs], [ERROR] * 3)
        self.assertIn("BOGUS", probs[0].message)
        self.assertIn("Unrecognized parameter FOO", probs[1].message)
        self.assertIn("Could not find node", probs[2].message)
        self.assertEqual(len(xyce.output_problems(out, Netlist())), 4)                    # LMIN not exempt without the bin
        self.assertEqual(xyce.output_problems("***** Reading and parsing netlist...\n***** Setting up topology...\n", nl), [])
        summary_ok = ("Netlist warning: .HDL: compiled and registered resistor from toy_resistor.va\n"
                      "***** Device Count Summary ...\n"
                      "       R level 1 (Resistor)                   1\n"
                      "       RESISTOR (resistor)                    1\n"
                      "       V level 1 (Independent Voltage Source) 1\n"
                      "       ----------------------------------------\n"
                      "       Total Devices                          3\n")
        self.assertEqual(xyce.device_summary_problems(summary_ok, nl), [])
        builtin = summary_ok.replace("       RESISTOR (resistor)                    1\n", "       R level 6 (Thermal Resistor) 1\n")
        probs = xyce.device_summary_problems(builtin, nl)
        self.assertEqual(len(probs), 1)
        self.assertIn("built-in Thermal Resistor at level 6", probs[0].message)
        mos = Netlist(dialect="spectre", va_modules={"bsim6": VaModule("bsim6", "/b.va", None, {"xyceModelGroup": "MOSFET",
                                                                                                   "xyceLevelNumber": "77"})},
                      body=[I("m1", "y", ["d", "g", "0", "0"], master="bsim6")])
        cmc = ("***** Device Count Summary ...\n       M level 77 (BSIM6)                     1\n       Total Devices 1\n")
        probs = xyce.device_summary_problems(cmc, mos)
        self.assertEqual(len(probs), 1)
        self.assertIn("built-in BSIM6 at level 77", probs[0].message)
        missing = summary_ok.replace("       RESISTOR (resistor)                    1\n", "")
        self.assertIn("not listed under its own name", xyce.device_summary_problems(missing, nl)[0].message)
        total_only = ("Netlist warning: .HDL: compiled and registered resistor from toy_resistor.va\n"
                      "***** Device Count Summary ...\n       Total Devices                          3\n")
        self.assertEqual(xyce.device_summary_problems(total_only, nl), [])                 # the registration line stands
        counted = "***** Device Count Summary ...\n       Total Devices 3\nNumber of resistor models: 1\nNumber of resistor instances: 1\n"
        self.assertEqual(xyce.device_summary_problems(counted, nl), [])
        self.assertIn("was not bound", xyce.device_summary_problems("***** Device Count Summary ...\n       Total Devices 3\n", nl)[0].message)
        self.assertEqual(xyce.device_summary_problems("", Netlist()), [])
        self.assertEqual(xyce.device_summary_problems("", va_resistor(attrs=False)), [])  # the Y form is not checked


class TestHspiceRouteRegressions(unittest.TestCase):
    """§4: the S3 shared fixes that reach the HSPICE path, each failing without its fix."""

    def test_cond_free_hspice_decks_are_byte_identical(self):
        for name in ("emit_vacask_basic.sim", "emit_vacask_bin.sim", "emit_xyce_basic.cir", "emit_xyce_bin.cir"):
            self.assertTrue(os.path.isfile(fixture("netlist", name)))                      # test_netlist_emit pins them

    def test_vcs_ams_style_letter_device_and_its_summary_check(self):
        """A vcs-ams deck (dialect hspice) prints an xyceModelGroup module as its letter device, and the
        smoke's summary check refuses a built-in binding at the module's level."""
        nl = va_resistor(dialect="hspice", params={"R": Num(3e3)})
        text = xyce.render(nl)
        self.assertIn("ry1 a b resistor__vamos R=3000.0\n", text)
        self.assertIn(".model resistor__vamos r level=6\n", text)
        bad = "***** Device Count Summary ...\n       R level 6 (Thermal Resistor) 1\n       Total Devices 2\n"
        self.assertEqual([n.severity for n in xyce.device_summary_problems(bad, nl)], [ERROR])
        # a Verilog-A module without attributes keeps vcs-ams's Y form and refusal
        with self.assertRaisesRegex(NoteError, "PyMS ignores"):
            xyce.render(va_resistor(dialect="hspice", attrs=False, params={"R": Num(3e3)}))

    def test_hspice_decks_do_not_take_the_spectre_source_or_quote_rules(self):
        nl = Netlist(title="hspice")
        nl.body = [I("v1", "v", ["a", "0"], source=Source(dc=Num(0.9), wave="pulse", args={
                       "v1": Num(0.0), "v2": Num(1.0), "td": Num(1e-6), "tr": Num(1e-9), "tf": Num(1e-9), "pw": Num(1e-5)})),
                   I("r1", "r", ["a", "0"], value=Num(0.0)), I("r2", "r", ["a", "007"], value=Num(1.0))]
        nl.analyses = [Analysis("tran", {"step": 1e-9, "stop": 1e-6}, "deck:9")]
        text = vacask.render(nl)
        self.assertIn('v1 (a 0) vamos_vsource type="pulse" val0=0.0 val1=1.0 delay=1e-06 rise=1e-09 fall=1e-09 width=1e-05 dc=0.9\n', text)
        self.assertIn("r1 (a 0) vamos_resistor r=0.0\n", text)
        self.assertIn("v1 a 0 DC 0.9 PULSE(0.0 1.0 1e-06 1e-09 1e-09 1e-05)\n", xyce.render(nl))
        self.assertIn("  abort always\n", text)


# =============================================================================================
# the engines (WSL)
# =============================================================================================

def _plot(path, k=0):
    return rawfile.read_all(path)[k]


class _Engines(TempDir):
    def vacask(self, text, sub, deck="vamos.sim"):
        from vamos.ams import engines
        d = os.path.join(self.tmp, sub)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, deck), "w") as fh:
            fh.write(text)
        r = run([vacask_bin(), deck], cwd=d, env=engines.env_for("vacask", None, dict(os.environ)))
        self.assertEqual(r.returncode, 0, "VACASK failed:\n%s\n--- deck\n%s" % (r.stdout[-3000:], text))
        return d, r.stdout

    def xyce(self, text, sub, deck="vamos.cir", rc=0):
        from vamos.ams import engines
        d = os.path.join(self.tmp, sub)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, deck), "w") as fh:
            fh.write(text)
        env = engines.env_for("xyce", None, dict(os.environ))
        env["PYMS_CACHE"] = os.path.join(self.tmp, "pyms")
        r = run([xyce_bin(), deck], cwd=d, env=env)
        if rc is not None:
            self.assertEqual(r.returncode, rc, "Xyce rc %d:\n%s\n--- deck\n%s" % (r.returncode, r.stdout[-3000:], text))
        return d, r.stdout

    def copy_va(self, d, *parts):
        import shutil
        shutil.copy(os.path.join(FACTS, *parts), d)


@needs_vacask
class TestVacaskRuns(_Engines):
    def test_worked_example(self):
        """§7.3's reference values [E74]: out(5 ms) 0.98173 (1k) and 0.86469 (2k), 61 AC points to 1e6."""
        nl = worked_example()
        pl, steps = worked_plan()
        text = vacask.render(nl, plan=pl, sigmap=allpub_map(steps))
        d, _ = self.vacask(text, "worked")
        op = _plot(os.path.join(d, "vamos_a1.raw"))
        self.assertAlmostEqual(op.column("out")[0], 0.0, places=9)
        ac = _plot(os.path.join(d, "vamos_a2.raw"))
        self.assertEqual(len(ac.points), 61)
        self.assertAlmostEqual(ac.column("frequency")[-1].real, 1e6, delta=1e-3)
        o = ac.column("out")[0]
        self.assertAlmostEqual(o.real, 0.99996, delta=2e-5)
        self.assertAlmostEqual(o.imag, -0.00628, delta=2e-5)
        tr = _plot(os.path.join(d, "vamos_a3.raw"))
        sweep = [v.real for v in tr.column("vamos_w1")]
        times = [v.real for v in tr.column("time")]
        outs = [v.real for v in tr.column("out")]
        self.assertEqual(sorted(set(sweep)), [1000.0, 2000.0])
        for rv, want in ((1000.0, 0.98173), (2000.0, 0.86469)):
            last = max(i for i, s in enumerate(sweep) if s == rv)
            self.assertAlmostEqual(times[last], 5e-3, delta=1e-9)
            self.assertAlmostEqual(outs[last], want, delta=2e-4, msg=rv)

    def test_cond_group_sources_quoting_short_lift(self):
        if not spectre_eval_ready():
            self.skipTest("S6 integration: expr.evaluate(dialect='spectre') is S1's and is not there yet")
        # the UG p.110 Cond deck runs with its three cards selected
        op_plan_ = op_plan(npn_mod_netlist(), [(("v", "1"), "1", "V"), (("i", "vb"), "vb:p", "I"), (("i", "vb3"), "vb3:p", "I")])
        nl = npn_mod_netlist()
        text = vacask.render(nl, plan=op_plan_[0], sigmap=op_plan_[2])
        d, _ = self.vacask(text, "cond")
        p = _plot(os.path.join(d, "vamos_a1.raw"))
        self.assertLess(p.column("vb:flow(br)")[0], 0.0)
        # the model group: m1 (entry 2) equals the reference card
        nl = group_netlist()
        pl, st, sm = op_plan(nl, [(("i", "vd"), "vd:p", "I"), (("i", "vr"), "vr:p", "I")])
        d, _ = self.vacask(vacask.render(nl, plan=pl, sigmap=sm), "group")
        p = _plot(os.path.join(d, "vamos_a1.raw"))
        idv, ivr = p.column("vd:flow(br)")[0], p.column("vr:flow(br)")[0]
        self.assertLess(idv, -1e-6)
        self.assertAlmostEqual(idv, ivr, delta=abs(ivr) * 1e-9)
        # a given dc under type="dc" is the operating point [E17]; a derived one follows val0 [E38]
        nl = divider(src=pulse_source(dc=0.9, p="in"))
        nl.body.append(pulse_source(name="v2", p="b", v1=0.3))
        nl.body.append(R("rb", "b", "0", 1000.0))
        pl, st, sm = op_plan(nl, [(("v", "in"), "in", "V"), (("v", "b"), "b", "V")])
        d, _ = self.vacask(vacask.render(nl, plan=pl, sigmap=sm), "src")
        p = _plot(os.path.join(d, "vamos_a1.raw"))
        self.assertAlmostEqual(p.column("in")[0], 0.9, places=9)
        self.assertAlmostEqual(p.column("b")[0], 0.3, places=9)
        # '007', '00' and 7 are distinct nodes [E35]; r = 0 is a short [E22]
        nl = Netlist(title="quoting", dialect="spectre")
        nl.body = [V("v7", "007", "0", 2.0), V("v00", "00", "0", -1.5), V("v5", "7", "0", 1.0), V("vs", "s", "0", 1.0),
                   R("r7", "007", "0", 1000.0), R("r00", "00", "0", 1000.0), R("r5", "7", "0", 1000.0),
                   R("rshort", "s", "s2", 0.0), R("rs", "s2", "0", 1000.0)]
        nl.temp = nl.tnom = 27.0
        pl, st, sm = op_plan(nl, [(("v", "007"), "007", "V"), (("v", "00"), "00", "V"), (("v", "7"), "7", "V"),
                                  (("i", "rshort"), "rshort:1", "I")])
        names = {}
        d, _ = self.vacask(vacask.render(nl, plan=pl, sigmap=sm, names=names), "quote")
        p = _plot(os.path.join(d, "vamos_a1.raw"))
        self.assertEqual([p.column(c)[0] for c in ("007", "00", "7")], [2.0, -1.5, 1.0])
        self.assertAlmostEqual(p.column(names["vamos_a1"][("i", "rshort")])[0], 1e-3, delta=1e-12)
        # the nested-definition lift: UG p.86's inheritance, V(out) = 0.5 with rr = 2k passed on the X line [E65]
        nl = lift_netlist()
        pl, st, sm = op_plan(nl, [(("v", "out"), "out", "V")])
        d, _ = self.vacask(vacask.render(nl, plan=pl, sigmap=sm), "lift")
        self.assertAlmostEqual(_plot(os.path.join(d, "vamos_a1.raw")).column("out")[0], 0.25, places=6)

    def test_per_type_saves_and_noise_contributions(self):
        """The saves each analysis type reads [E67] and nc() contributions [E84]; strictsave=2 fails a bad save."""
        nl = divider(extra=[Subckt("cell", ["a"], body=[R("r3", "a", "0", 1000.0)]), I("x1", "x", ["out"], master="cell")])
        op = step("vamos_a1", "op", options=OPTIONS)
        ac = step("vamos_a2", "ac", own=freq_level(count=1, start=1.0, stop=10.0), options=OPTIONS)
        nz = step("vamos_a3", "noise", own=freq_level(count=1, start=1e3, stop=1e3, lid="vamos_f2", mode="values"), options=OPTIONS,
                  noise_input="v1", args={"p": "out", "n": "0"})
        nz.own.spec.values = [Num(1e3)]
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o")] + [analysis(s) for s in (op, ac, nz)])
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V"), (("i", "v1"), "v1:p", "I"), (("i", "r1"), "r1:1", "I")],
                     "vamos_a2": [(("v", "out"), "out", "V"), (("i", "v1"), "v1:p", "I")],
                     "vamos_a3": [(("noise", "r1"), "r1", "resistor"), (("noise", "x1.r3"), "x1.r3", "resistor"),
                                  (("onoise",), "out", "V/sqrt(Hz)")]})
        names = {}
        d, _ = self.vacask(vacask.render(nl, plan=pl, sigmap=sm, names=names), "saves")
        op_raw = _plot(os.path.join(d, "vamos_a1.raw"))
        self.assertEqual(sorted(op_raw.names()), ["out", "r1.i", "v1:flow(br)"])
        self.assertAlmostEqual(op_raw.column(names["vamos_a1"][("i", "r1")])[0], 1.0 / 1500.0, delta=1e-9)
        ac_raw = _plot(os.path.join(d, "vamos_a2.raw"))
        self.assertEqual(sorted(ac_raw.names()), ["frequency", "out", "v1:flow(br)"])
        nz_raw = _plot(os.path.join(d, "vamos_a3.raw"))
        self.assertIn("n(r1)", nz_raw.names())
        self.assertIn("n(x1:r3)", nz_raw.names())
        self.assertIn("n(r1,thermal)", nz_raw.names())
        self.assertNotIn("n(r2)", nz_raw.names())
        self.assertGreater(nz_raw.column("onoise")[0].real, 0.0)
        bad = sigmap({"vamos_a1": [(("v", "nosuch"), "nosuch", "V")]})
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o"), analysis(op)])
        d2 = os.path.join(self.tmp, "bad")
        os.makedirs(d2)
        with open(os.path.join(d2, "vamos.sim"), "w") as fh:
            fh.write(vacask.render(nl, plan=pl, sigmap=bad))
        from vamos.ams import engines
        r = run([vacask_bin(), "vamos.sim"], cwd=d2, env=engines.env_for("vacask", None, dict(os.environ)))
        self.assertIn("nosuch", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(d2, "vamos_a1.raw")) and
                         rawfile.read_all(os.path.join(d2, "vamos_a1.raw"))[0].points)  # strictsave=2: never a zero column


@needs_xyce
class TestXyceRuns(_Engines):
    def test_worked_example_decks(self):
        if not spectre_eval_ready():
            self.skipTest("S6 integration: expr.evaluate(dialect='spectre') is S1's and is not there yet")
        nl = worked_example()
        pl, steps = worked_plan("xyce")
        sm = allpub_map(steps)
        d, _ = self.xyce(xyce.render(nl, step=steps[0], sigmap=sm), "op", "vamos_a1.cir")
        self.assertAlmostEqual(_plot(os.path.join(d, "vamos_a1.raw")).column("V(OUT)")[0], 0.0, places=9)
        d, _ = self.xyce(xyce.render(nl, step=steps[1], sigmap=sm), "ac", "vamos_a2.cir")
        ac = _plot(os.path.join(d, "vamos_a2.raw"))
        self.assertEqual(len(ac.points), 61)
        o = ac.column("V(OUT)")[0]
        self.assertAlmostEqual(o.real, 0.99996, delta=2e-5)
        self.assertAlmostEqual(o.imag, -0.00628, delta=2e-5)
        d, _ = self.xyce(xyce.render(nl, step=steps[2], sigmap=sm), "tran", "vamos_a3.cir")
        plots = rawfile.read_all(os.path.join(d, "vamos_a3.raw"))
        self.assertEqual(len(plots), 2)
        for plot, want in zip(plots, (0.98173, 0.86469)):
            self.assertAlmostEqual(plot.at("V(OUT)", 5e-3), want, delta=2e-4)

    def test_sources_noise_ic_group_letter_device(self):
        if not spectre_eval_ready():
            self.skipTest("S6 integration: expr.evaluate(dialect='spectre') is S1's and is not there yet")
        # a given dc is the operating point, a derived one its t = 0 value [E17, E70]
        nl = divider(src=pulse_source(dc=0.9, p="in"))
        nl.body += [pulse_source(name="v2", p="b", v1=0.3), R("rb", "b", "0", 1000.0)]
        st = step("vamos_a1", "op", options=OPTIONS)
        sm = sigmap({"vamos_a1": [(("v", "in"), "in", "V"), (("v", "b"), "b", "V")]})
        d, _ = self.xyce(xyce.render(nl, step=st, sigmap=sm), "src", "vamos_a1.cir")
        p = _plot(os.path.join(d, "vamos_a1.raw"))
        self.assertAlmostEqual(p.column("V(IN)")[0], 0.9, places=9)
        self.assertAlmostEqual(p.column("V(B)")[0], 0.3, places=9)
        # noise: the input prints AC 1 0, so INOISE is per unit input (3.3152e-17 for the divider, E19); DNO per path
        inner = Subckt("cell", ["a"], body=[R("r3", "a", "mid", 1000.0), R("r4", "mid", "0", 1000.0)])
        nl = divider(extra=[inner, I("I2", "x", ["out"], master="cell")])
        nl.body[0].source.ac = (Num(2.0), Num(0.0))
        nz = step("vamos_a2", "noise", options=OPTIONS, own=freq_level("values", lid="vamos_f1"), noise_input="v1", args={"p": "out", "n": "0"})
        nz.own.spec.values = [Num(1e3)]
        nrefs = [(("noise", "r1"), "r1", "resistor"), (("noise", "I2.r3"), "I2.r3", "resistor"), (("onoise",), "out", "V/sqrt(Hz)"),
                 (("inoise",), "in", "V/sqrt(Hz)")]
        d, out = self.xyce(xyce.render(nl, step=nz, sigmap=sigmap({"vamos_a2": nrefs})), "noise", "vamos_a2.cir")
        with open(os.path.join(d, "vamos_a2.prn")) as fh:
            head, row = [l.split() for l in fh.read().splitlines()[:2]]
        self.assertEqual(head[1:], ["FREQ", "DNO(R1)", "DNO(XI2:R3)", "ONOISE", "INOISE"])
        onoise, inoise = float(row[head.index("ONOISE")]), float(row[head.index("INOISE")])
        self.assertGreater(float(row[head.index("DNO(XI2:R3)")]), 0.0)
        # out = in * (r2 || (r3+r4)) / (r1 + r2 || (r3+r4)) = 0.4: INOISE/ONOISE = 1/gain^2 = 6.25 with the input
        # printed AC 1 0; the netlist's mag=2 would have made it 1.5625 [E19]
        self.assertAlmostEqual(inoise / onoise, 6.25, delta=0.05)
        # .IC only in the tran deck: the op of an RC divider with ic out=0.2 is 0.5 [E18]
        nl = divider(extra=[I("c1", "c", ["out", "0"], value=Num(1e-9), prim="capacitor")])
        nl.ics = {"out": 0.2}
        d, _ = self.xyce(xyce.render(nl, step=step("vamos_a3", "op", options=OPTIONS), sigmap=sigmap({"vamos_a3": [(("v", "out"), "out", "V")]})),
                         "ic", "vamos_a3.cir")
        self.assertAlmostEqual(_plot(os.path.join(d, "vamos_a3.raw")).column("V(OUT)")[0], 0.5, places=6)
        # the model group bound per path equals the reference card
        nl = group_netlist()
        d, _ = self.xyce(xyce.render(nl, step=step("vamos_a4", "op", options=OPTIONS),
                                     sigmap=sigmap({"vamos_a4": [(("i", "vd"), "vd:p", "I"), (("i", "vr"), "vr:p", "I")]})), "group", "vamos_a4.cir")
        p = _plot(os.path.join(d, "vamos_a4.raw"))
        self.assertAlmostEqual(p.column("I(VD)")[0], p.column("I(VR)")[0], delta=abs(p.column("I(VR)")[0]) * 1e-9)
        # the G form of isnoisy=no: 0.5 V and ONOISE of R2 only, 4.144e-18 [E32]
        nl = divider()
        nl.body[1].params["isnoisy"] = Str("no")
        nz = step("vamos_a5", "noise", options=OPTIONS, own=freq_level("values", lid="vamos_f1"), noise_input="v1", args={"p": "out", "n": "0"})
        nz.own.spec.values = [Num(1e3)]
        d, _ = self.xyce(xyce.render(nl, step=nz, sigmap=sigmap({"vamos_a5": [(("onoise",), "out", "V/sqrt(Hz)")]})), "gform", "vamos_a5.cir")
        with open(os.path.join(d, "vamos_a5.prn")) as fh:
            head, row = [l.split() for l in fh.read().splitlines()[:2]]
        self.assertAlmostEqual(float(row[head.index("ONOISE")]), 4.144e-18, delta=2e-20)
        # the letter-device route: R=3k on the device line gives V(B) = 0.25 [E64], and the summary lists the module
        nl = va_resistor(params={"R": Num(3e3)})
        d = os.path.join(self.tmp, "va")
        os.makedirs(d)
        self.copy_va(d, "E64", "toy_resistor.va")
        text = xyce.render(nl, step=step("vamos_a6", "op", options=OPTIONS), sigmap=sigmap({"vamos_a6": [(("v", "b"), "b", "V")]}))
        d, out = self.xyce(text, "va", "vamos_a6.cir")
        self.assertAlmostEqual(_plot(os.path.join(d, "vamos_a6.raw")).column("V(B)")[0], 0.25, places=6)
        self.assertEqual(xyce.device_summary_problems(out, nl), [])
        self.assertEqual(xyce.output_problems(out, nl), [])
        # a model-typed parameter on the device line fails loudly through the output scan [S3 2026-10-10]
        d2 = os.path.join(self.tmp, "vabad")
        os.makedirs(d2)
        self.copy_va(d2, "E63", "mydio.va")
        mod = VaModule("mydio", "mydio.va", ["r"], {"xyceModelGroup": "DIODE", "xyceLevelNumber": "2002"})
        bad = Netlist(title="va card param", dialect="spectre", hdl=["mydio.va"], va_modules={"mydio": mod})
        bad.body = [V("v1", "a", "0", 1.0), I("d1", "y", ["a", "0"], master="mydio", params={"r": Num(2e3)}, prim="mydio")]
        bad.temp = bad.tnom = 27.0
        text = xyce.render(bad, step=step("vamos_a7", "op", options=OPTIONS), sigmap=sigmap({"vamos_a7": [(("i", "v1"), "v1:p", "I")]}))
        self.assertIn(".model mydio__vamos d level=2002\nd1 a 0 mydio__vamos r=2000.0\n", text)
        _d, out = self.xyce(text, "vabad", "vamos_a7.cir", rc=None)
        probs = xyce.output_problems(out, bad)
        self.assertTrue(any("Unrecognized parameter R" in n.message for n in probs), out[-1500:])

    def test_smoke_accepts_the_letter_device(self):
        nl = va_resistor(dialect="hspice", params={"R": Num(3e3)})
        nl.hdl = [os.path.join(FACTS, "E64", "toy_resistor.va")]
        self.assertEqual(xyce.smoke(nl, os.path.join(self.tmp, "smoke")), [])
        with open(os.path.join(self.tmp, "smoke", xyce.SMOKE_NORUN_LOG)) as fh:
            self.assertIn("RESISTOR (resistor)", fh.read())


@needs_vacask
@needs_xyce
class TestCrossEngine(_Engines):
    def test_cond_npn_agrees(self):
        if not spectre_eval_ready():
            self.skipTest("S6 integration: expr.evaluate(dialect='spectre') is S1's and is not there yet")
        refs = [(("i", "vb"), "vb:p", "I"), (("i", "vb3"), "vb3:p", "I"), (("i", "vin"), "vin:p", "I")]
        nl = npn_mod_netlist("spectre")
        pl, st, sm = op_plan(nl, refs)
        names_v, names_x = {}, {}
        d, _ = self.vacask(vacask.render(nl, plan=pl, sigmap=sm, names=names_v), "v")
        pv = _plot(os.path.join(d, "vamos_a1.raw"))
        d, _ = self.xyce(xyce.render(nl, step=st, sigmap=sm, names=names_x), "x", "vamos_a1.cir")
        px = _plot(os.path.join(d, "vamos_a1.raw"))
        for ref, _n, _t in refs:
            a, b = pv.column(names_v["vamos_a1"][ref])[0], px.column(names_x["vamos_a1"][ref])[0]
            self.assertLess(a, 0.0, ref)
            self.assertAlmostEqual(a, b, delta=abs(b) * 0.01, msg=ref)
        # q1 (20x20) and q2/q3 (10x10 / default) differ, so the branches were taken per instance
        self.assertNotAlmostEqual(pv.column(names_v["vamos_a1"][("i", "vb")])[0], pv.column(names_v["vamos_a1"][("i", "vb3")])[0])


if __name__ == "__main__":
    unittest.main()
