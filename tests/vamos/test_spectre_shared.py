"""Phase-0 contracts of the shared netlist modules the spectre personality extends
(docs/VAMOS_SPECTRE_DESIGN.md §4.2-§4.5, §10, §11 T0, §12 phase 0; owner S0B).

    python3 -m unittest discover -s tests/vamos -p 'test_spectre_shared.py' -v

Runs on both legs (Cygwin Python 3.9, WSL Python 3.14), no engine.

TestExprContract      expr.py: DIALECTS, the dialect keywords of number/parse/evaluate/to_text
    (today's HSPICE behaviour unchanged, ValueError outside DIALECTS, NotImplementedError for the
    Spectre dialects in phase 0), SPECTRE_FUNCS, SPECTRE_CONSTANTS, EXTRA_FUNCS, RESERVED,
    VACASK_CONSTANTS
TestHspiceRouteRegressions   §4's shared fixes that reach the HSPICE path, each failing without
    its fix: .param M_DEGPERRAD=2 (case sensitive) printed as vamos_M_DEGPERRAD on VACASK;
    Netlist.left_out surviving dataclasses.replace; scale_source keeping Source.spectre
TestTablesContract    ParamRule, MasterRow, SPECTRE_MASTERS, model_params(dialect=), the
    *_spectre bin signatures, scale_source through dataclasses.replace
TestPathEnvs          tables.path_envs against the envs xyce._Deck computes today, on every HSPICE
    golden of tests/vamos/fixtures/netlist (integ_*.sp, the library tree, x-heep, PAMS p27/p349)
    and on hand-built IRs; a Cond resolved per path; the strict walk; values=; path_counts
TestSpiceContract     Decl, FileRef, Resolver, Fragment, the declare_fragment/parse_fragment/
    va_modules stubs, left_out as a field
TestEmitterContract   the render keywords (byte-identical output with plan/step None), names_for,
    xyce.output_problems and device_summary_problems
"""

import dataclasses
import inspect
import math
import os
import typing
import unittest

from vamos_testlib import TempDir, fixture

from vamos.netlist import expr as E  # noqa: E402
from vamos.netlist import ir, spice, tables as T, vacask, xyce  # noqa: E402
from vamos.netlist.expr_ast import Binary, Name, Num, Str  # noqa: E402
from vamos.netlist.ir import (Analysis, Cond, Instance, Model, Netlist, Param, ParamTest, ParseOpts,  # noqa: E402
                              Source, Subckt)
from vamos.notes import NoteError  # noqa: E402

FX = fixture("netlist")
XHEEP_CWD = fixture("netlist", "spice_xheep", "build", "openhwgroup.org_systems_core-v-mini-mcu_0", "sim-vcs")
XHEEP_ADC = "../../../hw/ip_examples/ams/analog/adc.sp"
SKY130 = os.environ.get("VAMOS_SKY130_LIB", "/opt/pdk/sky130A/libs.tech/ngspice/sky130.lib.spice")

P = E.parse


def lines(text, origin="tb"):
    """netlist_commands-style (line, origin) pairs."""
    return [(l, "%s:%d" % (origin, k + 1)) for k, l in enumerate(text.strip("\n").split("\n"))]


def I(name, kind, nodes, **kw):                                   # noqa: E743
    return Instance(name, kind, list(nodes), **kw)


def R(name, a, b, value, **params):
    return I(name, "r", [a, b], value=P(value) if isinstance(value, str) else Num(float(value)),
             params={k: P(v) if isinstance(v, str) else Num(float(v)) for k, v in params.items()})


def X(name, nodes, master, **params):
    return I(name, "x", nodes, master=master,
             params={k: P(v) if isinstance(v, str) else v if isinstance(v, Num) else Num(float(v))
                     for k, v in params.items()})


def V(name, p, n, dc):
    return I(name, "v", [p, n], source=Source(dc=Num(float(dc))))


def card(name, kind, level, **params):
    return Model(name, kind, None if level is None else float(level),
                 {k: P(v) if isinstance(v, str) else Num(float(v)) for k, v in params.items()})


# -- expr.py -------------------------------------------------------------------------------------

class TestExprContract(unittest.TestCase):
    def test_dialects_and_warn(self):
        self.assertEqual(E.DIALECTS, ("hspice", "spectre", "spectre-spice"))
        self.assertEqual(E.Warn, typing.Callable[[str, str], None])

    def test_number_keeps_todays_reader_for_hspice(self):
        self.assertEqual(E.number("1.0D+3"), 1000.0)
        self.assertEqual(E.number("0.22u", "hspice"), 2.2e-07)
        self.assertEqual(E.number("2meg", dialect="hspice", warn=None), 2e6)
        with self.assertRaises(ValueError):
            E.number("1k", dialect="spice")                         # spice.py maps "spice" first
        with self.assertRaises(NotImplementedError):
            E.number("1k", dialect="spectre")
        with self.assertRaises(NotImplementedError):
            E.number("1k", dialect="spectre-spice")

    def test_parse_keeps_todays_hspice_semantics(self):
        self.assertEqual(E.evaluate(P("-2**2"), {}), -4.0)           # test_netlist_expr.py:94
        self.assertEqual(E.evaluate(P("0**0"), {}), 0.0)             # :199
        self.assertEqual(P("a^b"), P("a**b"))                        # :81
        self.assertEqual(P("x*2", "lower", "hspice", None, None), P("x*2"))
        self.assertEqual(E.evaluate(P("pow(2, 1.5)"), {}), 2.0)     # HSPICE's truncating pow
        with self.assertRaises(ValueError):
            P("1", dialect="hspice2")
        for d in ("spectre", "spectre-spice"):
            with self.assertRaises(NotImplementedError):
                P("1+1", dialect=d)
        with self.assertRaises(NotImplementedError):                 # S1: the Spectre dialects inline funcs
            P("f(1)", funcs={"f": (["a"], Name("a"))})
        with self.assertRaises(ValueError):
            P("1", case="mixed")

    def test_evaluate_and_to_text_dialect_keyword(self):
        e = P("a + b*2")
        self.assertEqual(E.evaluate(e, {"a": 1, "b": 2}, dialect="hspice"), 5.0)
        self.assertEqual(E.to_text(e, dialect="hspice"), E.to_text(e))
        self.assertEqual(E.to_text(e), "a + b * 2.0")
        for fn in (lambda: E.evaluate(e, {"a": 1, "b": 2}, dialect="spectre"),
                   lambda: E.to_text(e, dialect="spectre"),
                   lambda: E.to_text(e, dialect="spectre-spice")):
            with self.assertRaises(NotImplementedError):
                fn()
        with self.assertRaises(ValueError):
            E.evaluate(e, {}, dialect="xyce")
        with self.assertRaises(ValueError):
            E.to_text(e, dialect="")

    def test_hspice_rejects_the_extra_functions(self):
        # §4.2: the HSPICE dialect rejects calls to cpow, hypot and fmod as unknown functions
        for text in ("cpow(2,3)", "hypot(3,4)", "fmod(-7,3)"):
            with self.assertRaises(E.EvalError):
                E.evaluate(P(text), {})
            with self.assertRaises(E.PrintError):
                E.to_vacask(P(text))

    def test_reserved_gains_tnom(self):
        self.assertEqual(E.RESERVED, ("time", "temper", "hertz", "$tnom"))
        self.assertEqual(E.names(Binary("+", Name("$tnom"), Name("p"))), {"p"})
        self.assertEqual(E.fold(Name("$tnom"), {"$tnom": 27.0}), Name("$tnom"))   # non-constant
        with self.assertRaises(E.EvalError):
            E.evaluate(Name("$tnom"), {"$tnom": 27.0})
        with self.assertRaises(E.ExprError):                          # HSPICE text can never produce it
            P("$tnom")

    def test_spectre_funcs(self):
        one = ("log", "ln", "log10", "exp", "sqrt", "abs", "int", "floor", "ceil", "sgn", "sin", "cos", "tan",
               "asin", "acos", "atan", "sinh", "cosh", "tanh", "asinh", "acosh", "atanh")
        two = ("min", "max", "pow", "fmod", "hypot", "atan2", "sign")
        self.assertEqual(set(E.SPECTRE_FUNCS), set(one) | set(two))
        self.assertEqual({E.SPECTRE_FUNCS[f] for f in one}, {(1, 1)})
        self.assertEqual({E.SPECTRE_FUNCS[f] for f in two}, {(2, 2)})
        for absent in ("db", "pwr", "nint", "trunc", "if", "limit", "dmin", "dmax", "agauss", "gauss", "aunif",
                       "unif", "cpow"):
            self.assertNotIn(absent, E.SPECTRE_FUNCS)
        self.assertEqual(E.EXTRA_FUNCS, {"cpow": (2, 2), "hypot": (2, 2), "fmod": (2, 2)})
        self.assertNotIn("hypot", E.FUNCS)                            # FUNCS stays HSPICE's table
        self.assertNotIn("fmod", E.FUNCS)

    def test_spectre_constants(self):
        c = E.SPECTRE_CONSTANTS
        self.assertEqual(len(c), 22)
        self.assertEqual(set(c), set(E.VACASK_CONSTANTS))             # VACASK defines the same 22 names
        self.assertEqual(c["M_PI"], math.pi)
        self.assertEqual(c["M_DEGPERRAD"], 180.0 / math.pi)
        self.assertAlmostEqual(c["M_DEGPERRAD"], 57.2957795130823208772, places=12)
        self.assertEqual(c["M_TWO_PI"], 2 * math.pi)
        self.assertAlmostEqual(c["M_2_SQRTPI"], 1.12837916709551257390, places=15)
        self.assertAlmostEqual(c["M_SQRT1_2"], 0.70710678118654752440, places=15)
        self.assertEqual(c["P_Q"], 1.6021918e-19)
        self.assertEqual(c["P_C"], 2.997924562e8)
        self.assertEqual(c["P_K"], 1.3806226e-23)
        self.assertEqual(c["P_H"], 6.6260755e-34)
        self.assertEqual(c["P_EPS0"], 8.85418792394420013968e-12)
        self.assertEqual(c["P_U0"], 4.0e-7 * math.pi)
        self.assertEqual(c["P_CELSIUS0"], 273.15)
        self.assertTrue(all(isinstance(v, float) and math.isfinite(v) for v in c.values()))

    def test_vacask_constants_gain_m_degperrad(self):
        self.assertIn("M_DEGPERRAD", E.VACASK_CONSTANTS)
        self.assertEqual(E.param_ident("M_DEGPERRAD", "vacask"), "vamos_M_DEGPERRAD")
        self.assertEqual(E.param_ident("M_DEGPERRAD", "xyce"), "M_DEGPERRAD")
        self.assertEqual(E.param_ident("m_degperrad", "vacask"), "m_degperrad")   # exact case, as VACASK


# -- the HSPICE-route regressions of §4's shared fixes ------------------------------------------

class TestHspiceRouteRegressions(TempDir):
    def parse(self, text, **opts):
        return spice.parse([self.write("t.sp", text)], [], self.tmp, ParseOpts(**opts))

    def test_m_degperrad_parameter_prints_prefixed_on_vacask(self):
        # §4, §4.2: without M_DEGPERRAD in VACASK_CONSTANTS a parameter of that name would shadow
        # VACASK's constant (expr.py param_ident)
        nl = self.parse("* t\n.param M_DEGPERRAD=2\nv1 a 0 1\nr1 a 0 'M_DEGPERRAD*1k'\n.tran 1n 10n\n",
                        case="sensitive")
        self.assertEqual(nl.values, {"M_DEGPERRAD": 2.0})
        text = vacask.render(nl)
        self.assertIn("parameters vamos_M_DEGPERRAD=2.0", text)
        self.assertNotIn("parameters M_DEGPERRAD=", text)
        r1 = [l for l in text.splitlines() if l.startswith("r1 ")]
        self.assertEqual(len(r1), 1)
        self.assertIn("vamos_M_DEGPERRAD", r1[0])
        # Xyce has no such constant: the name is printed as written
        self.assertIn(".param M_DEGPERRAD=2.0", xyce.render(nl))

    def test_left_out_is_a_real_field_that_survives_replace(self):
        lib = ("* t\n.subckt varcap a b\ncg a b q='1p*v(a,b)'\n.ends\n"
               ".subckt user a b\nxv a b varcap\n.ends\n.subckt good a b\nr1 a b 1k\n.ends\n")
        nl = self.parse(lib + "x1 n 0 good\n")
        self.assertIn("left_out", {f.name for f in dataclasses.fields(Netlist)})
        self.assertEqual(sorted(spice.left_out(nl)), ["user", "varcap"])
        self.assertEqual(sorted(nl.left_out), ["user", "varcap"])
        self.assertFalse(hasattr(nl, "_vamos_left_out"))
        copy = dataclasses.replace(nl)
        self.assertEqual(sorted(spice.left_out(copy)), ["user", "varcap"])
        self.assertEqual(spice.left_out(copy)["varcap"].message, nl.left_out["varcap"].message)
        self.assertIsNot(spice.left_out(nl), nl.left_out)             # a copy, as today
        self.assertEqual(spice.left_out(Netlist()), {})

    def test_scale_source_keeps_source_spectre(self):
        src = Source(dc=Num(1.0), ac=(Num(1.0), Num(0.0)), wave="pulse",
                     args={"v1": Num(0.0), "v2": Num(1.0), "td": Num(0.0), "tr": Num(1e-9), "tf": Num(1e-9),
                           "pw": Num(1e-6)},
                     spectre={"type": Str("pulse"), "val1": Num(1.0), "wave": [Num(0.0), Num(1.0)]})
        out = T.scale_source(src, Num(2.0))
        self.assertEqual(out.spectre, src.spectre)
        self.assertEqual((out.dc, out.ac, out.wave), (Num(2.0), (Num(2.0), Num(0.0)), "pulse"))
        self.assertEqual((out.args["v1"], out.args["v2"], out.args["tr"]), (Num(0.0), Num(2.0), Num(1e-9)))
        self.assertIsNone(out.code_uri)
        self.assertEqual(src.args["v2"], Num(1.0))                    # the input is untouched
        pwl = Source(wave="pwl", args={"td": Num(1e-9)}, points=[(Num(0.0), Num(1.0)), (Num(1e-9), Num(3.0))],
                     code_uri=None, spectre={"wave": [Num(0.0), Num(1.0)]})
        out = T.scale_source(pwl, Name("k"))
        self.assertEqual(out.points, [(Num(0.0), Name("k")),                 # tables.times: 1.0*k is k
                                      (Num(1e-9), Binary("*", Num(3.0), Name("k")))])
        self.assertEqual(out.spectre, pwl.spectre)
        self.assertEqual({f.name for f in dataclasses.fields(out)}, {f.name for f in dataclasses.fields(Source)})


# -- tables.py -----------------------------------------------------------------------------------

class TestTablesContract(unittest.TestCase):
    def test_param_rule(self):
        r = T.ParamRule("capmod", "strip", match=("absent", "bsim"), warn="analyses=ac,noise,xf,tran")
        self.assertEqual((r.name, r.action, r.value, r.when, r.match, r.warn, r.cite),
                         ("capmod", "strip", "", "", ("absent", "bsim"), "analyses=ac,noise,xf,tran", ""))
        self.assertEqual([f.name for f in dataclasses.fields(T.ParamRule)],
                         ["name", "action", "value", "when", "match", "warn", "cite"])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            r.action = "pass"
        self.assertEqual(T.ParamRule("eg", "default", "1.124481", "absent:eg", warn="temp!=tnom").when, "absent:eg")
        self.assertEqual(T.ParamRule("phi", "default", "0.7", "absent:phi,absent:nsub").value, "0.7")
        self.assertTrue(hash(T.ParamRule("hcomp", "error", match=("nonzero",))))

    def test_master_row(self):
        row = T.MasterRow("mos3", "m", 3, "type", (("n", "nmos"), ("p", "pmos")), ("d", "g", "s", "b"),
                          (("w", 3e-6), ("l", 3e-6), ("lmin", 0.0), ("lmax", 1.0), ("wmin", 0.0), ("wmax", 1.0)),
                          (T.ParamRule("badmos3", "default", "1", "absent:badmos3", warn="card"),),
                          (T.ParamRule("w", "pass"),))
        self.assertEqual([f.name for f in dataclasses.fields(T.MasterRow)],
                         ["master", "element", "level", "polarity_key", "kinds", "terminals", "geometry",
                          "params", "instance"])
        self.assertEqual(row.kinds[0], ("n", "nmos"))
        self.assertEqual(T.MasterRow("resistor", "r").level, None)
        self.assertEqual(T.MasterRow("resistor", "r").instance, ())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            row.level = 1

    def test_spectre_masters_is_the_data_table(self):
        self.assertIsInstance(T.SPECTRE_MASTERS, dict)
        for master, row in T.SPECTRE_MASTERS.items():
            self.assertIsInstance(master, str)
            self.assertIsInstance(row, T.MasterRow)
            self.assertEqual(row.master, master)
            for rule in row.params + row.instance:
                self.assertIsInstance(rule, T.ParamRule)

    def test_model_params_dialect_keyword(self):
        m = card("nch", "nmos", 1, vto=0.5, kp=1e-4)
        for engine in ("vacask", "xyce"):
            self.assertEqual(T.model_params(m, engine), T.model_params(m, engine, dialect="hspice"))
            self.assertEqual(T.model_params(m, engine, False, None), T.model_params(m, engine, False, None, "hspice"))
            with self.assertRaises(NotImplementedError):               # S3
                T.model_params(m, engine, dialect="spectre")
            with self.assertRaises(ValueError):
                T.model_params(m, engine, dialect="spectre-spice")
            with self.assertRaises(ValueError):
                T.model_params(m, engine, dialect="spice")
        self.assertEqual(inspect.signature(T.model_params).parameters["dialect"].default, "hspice")

    def test_spectre_bin_signatures(self):
        self.assertEqual(list(inspect.signature(T.bin_bounds_spectre).parameters), ["model", "values"])
        self.assertEqual(list(inspect.signature(T.bin_guard_spectre).parameters), ["bounds", "l", "w", "s"])
        self.assertEqual(list(inspect.signature(T.select_bin_spectre).parameters), ["bins", "l", "w", "s"])
        m = card("nch.1", "nmos", 49, lmin=0.1e-6, lmax=1e-6, wmin=0.1e-6, wmax=1e-6)
        with self.assertRaises(NotImplementedError):
            T.bin_bounds_spectre(m, {})
        with self.assertRaises(NotImplementedError):
            T.bin_guard_spectre((0.0, 1.0, 0.0, 1.0), Name("l"), Name("w"), 1.0)
        with self.assertRaises(NotImplementedError):
            T.select_bin_spectre([(m, (0.0, 1.0, 0.0, 1.0))], 1e-6, 1e-6, 1.0)
        # today's HSPICE rule is untouched
        self.assertIs(T.select_bin([(m, (0.0, 1.0, 0.0, 1.0))], 1e-6, 1e-6, 1.0, 1.0), m)

    def test_scale_source_still_scales_as_today(self):
        src = Source(dc=Num(1.0), ac=(Num(2.0), Num(30.0)), wave="sin",
                     args={"vo": Num(0.5), "va": Num(1.0), "freq": Num(1e6), "td": Num(0.0), "theta": Num(0.0),
                           "phase": Num(0.0)})
        out = T.scale_source(src, Num(3.0))
        self.assertEqual((out.dc, out.ac), (Num(3.0), (Num(6.0), Num(30.0))))
        self.assertEqual((out.args["vo"], out.args["va"], out.args["freq"]), (Num(1.5), Num(3.0), Num(1e6)))
        self.assertEqual(out.spectre, {})
        self.assertEqual(T.scale_source(Source(code_uri="code:x"), Num(2.0)).code_uri, "code:x")


# -- tables.path_envs ------------------------------------------------------------------------------

def deck_envs(nl):
    """(subckt name, X instance name, values, hidden) of every xyce._Deck.child_env call, in the
    order the elaboration walk makes them: today's per-path rule, the oracle of path_envs (§4.4)."""
    seen = []
    orig = xyce._Deck.child_env

    def spy(self, sub, inst, env):
        out = orig(self, sub, inst, env)
        seen.append((sub.name, inst.name, dict(out), set(out.hidden)))
        return out
    xyce._Deck.child_env = spy
    try:
        xyce._Deck(nl, (), False)
    finally:
        xyce._Deck.child_env = orig
    return seen


def path_view(pe):
    return (pe.subckt.name, pe.path.rsplit(".", 1)[-1], dict(pe.env), set(pe.hidden))


class TestPathEnvs(unittest.TestCase):
    def check_against_deck(self, nl, min_paths=0):
        pes = list(T.path_envs(nl))
        top = pes[0]
        self.assertEqual((top.path, top.subckt, top.hidden), ("", None, set()))
        self.assertIs(top.env, nl.values)
        self.assertEqual(top.items, [it for it in nl.body if isinstance(it, Instance)])
        self.assertEqual(top.scope.path(), "")
        self.assertEqual([path_view(pe) for pe in pes[1:]], deck_envs(nl))
        for pe in pes[1:]:
            self.assertIs(pe.scope.subckt, pe.subckt)
            self.assertEqual(pe.items, [it for it in pe.subckt.body if isinstance(it, Instance)])
            self.assertTrue(pe.path and "." not in pe.path.rsplit(".", 1)[-1])
            self.assertTrue(set(pe.hidden).isdisjoint(pe.env))
        self.assertGreaterEqual(len(pes) - 1, min_paths)
        return pes

    # -- the HSPICE goldens of tests/vamos/fixtures/netlist
    def test_integ_decks(self):
        for name in sorted(os.listdir(FX)):
            if not (name.startswith("integ_") and name.endswith(".sp")):
                continue
            with self.subTest(deck=name):
                try:
                    nl = spice.parse([os.path.join(FX, name)], [], FX, ParseOpts())
                except NoteError:
                    nl = spice.parse([os.path.join(FX, name)], [], FX, ParseOpts(parhier_local=True))
                self.check_against_deck(nl)

    def test_library_tree(self):
        nl = spice.parse([], lines(".lib 'lib.spice' tt\nx1 d g 0 0 nfet w=1 l=0.15 nf=2\n"
                                   "x2 d g 0 0 nfet w=2 l=0.15\nx3 d g 0 0 nfet w='vdd_nom*3' l=0.5 m=2\n"
                                   "vd d 0 1\nvg g 0 1\n.tran 1n 10n\n"),
                         fixture("netlist", "spice_libtree"))
        pes = self.check_against_deck(nl, 3)
        by = {pe.path: pe for pe in pes}
        self.assertEqual((dict(by["x1"].env)["nf"], dict(by["x2"].env)["nf"]), (2.0, 1.0))
        self.assertEqual(dict(by["x3"].env)["w"], 1.8 * 3)
        self.assertEqual([pe.hidden for pe in pes], [set()] * 4)

    def test_xheep_adc(self):
        nl = spice.parse([XHEEP_ADC], lines("xadc gnd out sel1 sel0 vdd ams_adc_1b\nvs1 sel1 0 0.0\n"
                                            "vs0 sel0 0 0.0\n.tran 1n 3u\n"), XHEEP_CWD)
        self.check_against_deck(nl, 1)

    def test_pams_examples(self):
        nl = spice.parse([fixture("netlist", "spice_pams_p27", "test.spi")],
                         lines("xt a b c d test\nva a 0 pulse(0 3.3 2n 0.1n 0.1n 4n 10n)\nvb b 0 3.3\nvc c 0 1\n"
                               "rd d 0 1meg\n.tran 0.01n 10n\n"), fixture("netlist", "spice_pams_p27"))
        self.check_against_deck(nl, 1)
        nl = spice.parse([fixture("netlist", "spice_pams_p349", "01_test.spi")],
                         lines("x1 in o1 sp1\nx2 o1 o2 sp2\nvin in 0 pulse(0 3.3 2n 0.1n 0.1n 5n 10n)\n"
                               ".tran 0.01n 20n\n"), fixture("netlist", "spice_pams_p349"))
        self.check_against_deck(nl, 2)

    @unittest.skipUnless(os.path.isfile(SKY130), "needs the sky130 PDK (%s)" % SKY130)
    def test_sky130_inverter(self):
        nl = spice.parse([], lines(".lib '%s' tt\nxn out in 0 0 sky130_fd_pr__nfet_01v8 w=1 l=0.15\n"
                                   "xp out in vdd vdd sky130_fd_pr__pfet_01v8 w=2 l=0.15\nvdd vdd 0 1.8\n"
                                   "vin in 0 1\ncl out 0 5f\n.tran 0.01n 6n\n" % SKY130), os.path.dirname(SKY130))
        self.check_against_deck(nl, 2)

    # -- hand-built IRs: the shapes of the emitter goldens
    def hierarchy(self):
        """Overrides, defaults reading other defaults and top-level values, shadowing, a parameter
        that does not evaluate on one path, nesting, m= (never an override), two paths to one body."""
        cell = Subckt("cell", ["a", "b"], params=[Param("w", Num(1e-6)), Param("l", P("w/2")), Param("q", P("zz*2"))],
                      body=[R("r1", "a", "b", "l*1k"), I("mn", "m", ["a", "b", "0", "0"], master="nch",
                                                           params={"w": Name("w"), "l": Name("l")})])
        outer = Subckt("outer", ["p", "n"], params=[Param("w", P("wtop*2")), Param("zz", Num(5.0))],
                       body=[X("xa", ["p", "n"], "cell", w="w"), X("xb", ["p", "n"], "cell", w="w*3", l=Num(7e-7)),
                             R("rp", "p", "n", 1)])
        nl = Netlist(title="path envs")
        nl.body = [Param("wtop", Num(2e-6)), Param("gain", P("wtop*1e6")),
                   card("nch", "nmos", 1, vto=0.5, kp=1e-4),
                   outer, cell,
                   X("x1", ["n1", "0"], "cell", m=2), X("x2", ["n1", "0"], "outer"), X("x3", ["n1", "0"], "outer", w="4e-6"),
                   V("v1", "n1", "0", 1)]
        nl.values = {"wtop": 2e-6, "gain": 2.0}
        nl.analyses = [Analysis("tran", {"step": 1e-9, "stop": 1e-6})]
        return nl

    def test_hand_built_hierarchy_matches_the_deck(self):
        nl = self.hierarchy()
        pes = self.check_against_deck(nl, 7)
        paths = [pe.path for pe in pes]
        self.assertEqual(paths, ["", "x1", "x2", "x2.xa", "x2.xb", "x3", "x3.xa", "x3.xb"])
        by = {pe.path: pe for pe in pes}
        self.assertEqual(by["x1"].hidden, {"q"})                       # zz is not defined on this path
        self.assertEqual(dict(by["x1"].env), {"w": 1e-6, "l": 5e-7, "wtop": 2e-6, "gain": 2.0})
        self.assertNotIn("q", by["x1"].env)
        self.assertEqual(dict(by["x2"].env), {"w": 4e-6, "zz": 5.0, "wtop": 2e-6, "gain": 2.0})
        self.assertEqual(dict(by["x2.xa"].env)["w"], 4e-6)             # the override, evaluated where the X line is
        self.assertEqual(dict(by["x2.xa"].env)["l"], 2e-6)             # the default, from the override
        self.assertEqual(by["x2.xa"].hidden, {"q"})                    # zz: the enclosing subckt's, not visible here
        self.assertEqual(dict(by["x2.xb"].env)["w"], 12e-6)
        self.assertEqual(dict(by["x2.xb"].env)["l"], 7e-7)
        self.assertEqual(dict(by["x3"].env)["w"], 4e-6)
        self.assertEqual([it.name for it in by["x2"].items], ["xa", "xb", "rp"])
        self.assertIs(by["x2.xa"].subckt, by["x3.xa"].subckt)

    def test_undefined_and_recursive_subckts_are_not_entered(self):
        loop = Subckt("loop", ["a"], body=[X("xl", ["a"], "loop"), R("r1", "a", "0", 1)])
        nl = Netlist(body=[loop, X("x1", ["n"], "loop"), X("x2", ["n"], "nowhere")])
        self.assertEqual([pe.path for pe in T.path_envs(nl)], ["", "x1"])
        self.assertEqual([path_view(pe) for pe in list(T.path_envs(nl))[1:]], deck_envs(nl))

    def test_values_argument_replaces_the_top_level_values(self):
        nl = self.hierarchy()
        reduced = {"gain": 2.0}                                        # wtop removed, as a render copy would
        pes = list(T.path_envs(nl, values=reduced))
        self.assertIs(pes[0].env, reduced)
        by = {pe.path: pe for pe in pes}
        self.assertEqual(by["x2"].hidden, {"w"})                       # w=wtop*2 no longer evaluates
        self.assertEqual(by["x2.xa"].hidden, {"w", "l", "q"})          # and nothing that reads it does
        self.assertNotIn("wtop", by["x1"].env)

    def test_strict_walk_raises_naming_the_path(self):
        nl = self.hierarchy()
        with self.assertRaises(E.EvalError) as cm:
            list(T.path_envs(nl, strict=True))
        msg = str(cm.exception)
        self.assertIn("x1", msg)
        self.assertIn("q", msg)
        self.assertIn("zz * 2.0", msg)
        self.assertIn("cell", msg)
        good = Netlist(body=[Subckt("s", ["a"], params=[Param("w", P("wtop*2"))], body=[R("r", "a", "0", "w")]),
                             X("x1", ["n"], "s"), X("x2", ["n"], "s", w="3")], values={"wtop": 1.0})
        self.assertEqual([dict(pe.env) for pe in T.path_envs(good, strict=True)][1:],
                         [{"w": 2.0, "wtop": 1.0}, {"w": 3.0, "wtop": 1.0}])
        self.assertEqual([pe.hidden for pe in T.path_envs(good, strict=True)], [set(), set(), set()])

    def test_dialect_keyword_reaches_evaluate(self):
        nl = self.hierarchy()
        self.assertEqual([pe.path for pe in T.path_envs(nl, dialect="hspice")], [pe.path for pe in T.path_envs(nl)])
        with self.assertRaises(ValueError):
            list(T.path_envs(nl, dialect="verilog"))
        with self.assertRaises(NotImplementedError):                   # S1: the Spectre evaluator
            list(T.path_envs(nl, dialect="spectre"))

    def test_cond_resolved_per_path(self):
        big, small, r0 = R("rbig", "a", "0", 1), R("rsmall", "a", "0", 2), R("r0", "a", "0", 3)
        mid = R("rmid", "a", "0", 4)
        chain = Cond([(Binary(">", Name("w"), Num(1.0)), [big])],
                     default=[Cond([(Binary(">", Name("w"), Num(0.5)), [mid])], default=[small])])
        cell = Subckt("cell", ["a"], params=[Param("w", Num(1.0))],
                      body=[chain, r0, ParamTest("pt", [("warnif", Binary("<", Name("w"), Num(0.0)))])])
        top_cond = Cond([(Binary("==", Name("sel"), Num(1.0)), [V("v1", "n", "0", 1)])], default=[V("v2", "n", "0", 2)])
        nl = Netlist(body=[Param("sel", Num(1.0)), cell, top_cond, X("x1", ["n"], "cell", w="2"),
                           X("x2", ["n"], "cell", w="0.7"), X("x3", ["n"], "cell", w="0.1")], values={"sel": 1.0})
        by = {pe.path: pe for pe in T.path_envs(nl)}
        self.assertEqual([it.name for it in by[""].items], ["v1", "x1", "x2", "x3"])
        self.assertEqual([it.name for it in by["x1"].items], ["rbig", "r0"])
        self.assertEqual([it.name for it in by["x2"].items], ["rmid", "r0"])
        self.assertEqual([it.name for it in by["x3"].items], ["rsmall", "r0"])
        self.assertEqual(sorted(by), ["", "x1", "x2", "x3"])
        nl.values = {"sel": 0.0}
        self.assertEqual([it.name for it in next(iter(T.path_envs(nl))).items], ["v2", "x1", "x2", "x3"])
        # a Cond chooses the X lines walked too
        cond_x = Cond([(Binary(">", Name("w"), Num(1.0)), [X("xc", ["a"], "leaf", w="w")])], default=[])
        leaf = Subckt("leaf", ["a"], params=[Param("w", Num(1.0))], body=[R("r", "a", "0", 1)])
        wrap = Subckt("wrap", ["a"], params=[Param("w", Num(1.0))], body=[cond_x])
        nl = Netlist(body=[leaf, wrap, X("x1", ["n"], "wrap", w="2"), X("x2", ["n"], "wrap", w="0.5")])
        self.assertEqual([pe.path for pe in T.path_envs(nl)], ["", "x1", "x1.xc", "x2"])
        self.assertEqual(T.path_counts(nl), {"leaf": 1, "wrap": 2})

    def test_condition_that_is_not_a_number_raises(self):
        cell = Subckt("cell", ["a"], params=[Param("w", Num(1.0))],
                      body=[Cond([(Binary(">", Name("zz"), Num(1.0)), [R("r1", "a", "0", 1)])])])
        nl = Netlist(body=[cell, X("x1", ["n"], "cell")])
        for strict in (False, True):
            with self.assertRaises(E.EvalError) as cm:
                list(T.path_envs(nl, strict=strict))
            self.assertIn("x1", str(cm.exception))
            self.assertIn("zz > 1.0", str(cm.exception))
        with self.assertRaises(E.EvalError) as cm:
            list(T.path_envs(Netlist(body=[Cond([(Name("nope"), [])])])))
        self.assertIn("top level", str(cm.exception))

    def test_items_outside_item_types_raise(self):
        with self.assertRaises(T.TableError):
            list(T.path_envs(Netlist(body=["junk"])))
        for it in ir.ITEM_TYPES:
            self.assertTrue(issubclass(it, (Instance, Model, Param, Subckt, Cond, ParamTest)))

    def test_path_counts(self):
        nl = self.hierarchy()
        nl.body.append(Subckt("lonely", ["a"], body=[R("r", "a", "0", 1)]))
        self.assertEqual(T.path_counts(nl), {"cell": 5, "outer": 2, "lonely": 0})
        self.assertEqual(T.path_counts(Netlist()), {})

    def test_path_env_dataclass(self):
        self.assertEqual([f.name for f in dataclasses.fields(T.PathEnv)],
                         ["path", "subckt", "scope", "env", "items", "hidden"])
        pe = T.PathEnv("", None, T.Scope([]), {})
        self.assertEqual((pe.items, pe.hidden), ([], set()))
        self.assertEqual(list(inspect.signature(T.path_envs).parameters), ["nl", "values", "strict", "dialect"])
        self.assertEqual(list(inspect.signature(T.path_counts).parameters), ["nl"])


# -- spice.py --------------------------------------------------------------------------------------

class TestSpiceContract(unittest.TestCase):
    def test_decl_and_fileref(self):
        self.assertEqual([f.name for f in dataclasses.fields(spice.Decl)],
                         ["kind", "name", "master", "ports", "params", "origin"])
        d = spice.Decl("model", "nch", master="bsim3v3", origin="lib.sp:3")
        self.assertEqual((d.kind, d.name, d.master, d.ports, d.params), ("model", "nch", "bsim3v3", None, []))
        s = spice.Decl("subckt", "inv", ports=3, params=["wn", "wp"])
        self.assertEqual((s.ports, s.params), (3, ["wn", "wp"]))
        self.assertIsNot(spice.Decl("param", "a").params, spice.Decl("param", "b").params)
        self.assertEqual([f.name for f in dataclasses.fields(spice.FileRef)],
                         ["keyword", "path", "section", "subckt", "origin"])
        f = spice.FileRef(".lib", "models/sky130.lib", section="tt", origin="t.sp:2")
        self.assertEqual((f.keyword, f.path, f.section, f.subckt), (".lib", "models/sky130.lib", "tt", ""))

    def test_resolver_protocol(self):
        self.assertTrue(issubclass(spice.Resolver, typing.Protocol))
        for meth in ("subckt", "model", "param", "va_module"):
            self.assertTrue(callable(getattr(spice.Resolver, meth)))
            self.assertEqual(list(inspect.signature(getattr(spice.Resolver, meth)).parameters), ["self", "name"])

        class Table:
            def subckt(self, name):
                return spice.Decl("subckt", name, ports=2)

            def model(self, name):
                return None

            def param(self, name):
                return None

            def va_module(self, name):
                return ir.VaModule(name, "/x.va")
        t = Table()
        self.assertEqual(t.subckt("inv").ports, 2)
        self.assertEqual(t.va_module("res").name, "res")

    def test_fragment(self):
        self.assertEqual([f.name for f in dataclasses.fields(spice.Fragment)],
                         ["items", "controls", "notes", "left_out", "pending"])
        fr = spice.Fragment()
        self.assertEqual((fr.items, fr.controls, fr.notes, fr.left_out, fr.pending), ([], [], [], {}, []))
        fr.controls.append((".ic", [("v(b)", "0.5")], "t.sp:7"))
        fr.pending.append(("model", "rmod", "t.sp:3"))
        self.assertEqual(spice.Fragment().controls, [])
        self.assertEqual(spice.Fragment(items=[Param("a", Num(1.0))]).items[0].name, "a")

    def test_stubs(self):
        self.assertEqual(list(inspect.signature(spice.declare_fragment).parameters), ["lines", "opts"])
        self.assertEqual(list(inspect.signature(spice.parse_fragment).parameters), ["lines", "cwd", "opts", "resolver"])
        sig = inspect.signature(spice.va_modules)
        self.assertEqual(list(sig.parameters), ["path", "search", "defines"])
        self.assertEqual((sig.parameters["search"].default, sig.parameters["defines"].default), ((), ()))
        opts = ParseOpts(dialect="spectre-spice")
        with self.assertRaises(NotImplementedError):
            spice.declare_fragment(lines("r1 a 0 1k"), opts)
        with self.assertRaises(NotImplementedError):
            spice.parse_fragment(lines("r1 a 0 1k"), ".", opts, None)
        with self.assertRaises(NotImplementedError):
            spice.va_modules("/nonexistent.va")

    def test_parse_still_refuses_spectre_spice(self):
        with self.assertRaises(NoteError) as cm:
            spice.parse([], lines("r1 a 0 1k"), ".", ParseOpts(dialect="spectre-spice"))
        self.assertIn("dialect spectre-spice is not supported", "\n".join(n.message for n in cm.exception.notes))
        nl = spice.parse([], lines("r1 a 0 1k\nv1 a 0 1\n.tran 1n 1u"), ".", ParseOpts(dialect="spice"))
        self.assertEqual((nl.dialect, nl.left_out, nl.va_modules), ("hspice", {}, {}))


# -- vacask.py / xyce.py ---------------------------------------------------------------------------

def small_netlist():
    nl = Netlist(title="emitter keywords")
    nl.body = [Param("rr", Num(1000.0)), R("r1", "a", "0", "rr"), I("c1", "c", ["a", "0"], value=Num(1e-12)),
               V("v1", "a", "0", 1)]
    nl.values = {"rr": 1000.0}
    nl.analyses = [Analysis("tran", {"step": 1e-9, "stop": 1e-6, "maxstep": 1e-8})]
    return nl


class TestEmitterContract(unittest.TestCase):
    def test_render_keywords(self):
        self.assertEqual(list(inspect.signature(vacask.render).parameters),
                         ["nl", "analysis_name", "osdi", "notes", "op", "plan", "sigmap", "names"])
        self.assertEqual(list(inspect.signature(xyce.render).parameters),
                         ["nl", "osdi", "notes", "op", "step", "sigmap", "names"])
        for p in ("plan", "sigmap", "names"):
            self.assertIsNone(inspect.signature(vacask.render).parameters[p].default)
        for p in ("step", "sigmap", "names"):
            self.assertIsNone(inspect.signature(xyce.render).parameters[p].default)

    def test_output_is_byte_identical_with_the_keywords_none(self):
        nl = small_netlist()
        self.assertEqual(vacask.render(nl), vacask.render(nl, plan=None, sigmap=None, names=None))
        self.assertEqual(vacask.render(nl, "vamos_tran", (), None, False, None, None, None), vacask.render(nl))
        self.assertEqual(xyce.render(nl), xyce.render(nl, step=None, sigmap=None, names=None))
        self.assertEqual(xyce.render(nl, (), None, False, None, None, None), xyce.render(nl))
        self.assertEqual(vacask.render(nl, op=True), vacask.render(nl, op=True, plan=None))
        self.assertEqual(xyce.render(nl, op=True), xyce.render(nl, op=True, step=None))

    def test_plan_rendering_is_phase_1(self):
        nl = small_netlist()
        for kw in ({"plan": object()}, {"sigmap": object()}, {"names": {}}):
            with self.assertRaises(NotImplementedError):
                vacask.render(nl, **kw)
        for kw in ({"step": object()}, {"sigmap": object()}, {"names": {}}):
            with self.assertRaises(NotImplementedError):
                xyce.render(nl, **kw)

    def test_names_for_and_the_xyce_checks(self):
        for mod in (vacask, xyce):
            self.assertEqual(list(inspect.signature(mod.names_for).parameters), ["nl", "plan", "sigmap"])
            with self.assertRaises(NotImplementedError):
                mod.names_for(small_netlist(), None, None)
        for fn in (xyce.output_problems, xyce.device_summary_problems):
            self.assertEqual(list(inspect.signature(fn).parameters), ["text", "nl"])
            with self.assertRaises(NotImplementedError):
                fn("Netlist error: ...", small_netlist())

    def test_smoke_signatures_unchanged(self):
        self.assertEqual(list(inspect.signature(vacask.smoke).parameters), ["nl", "dir", "osdi", "nvc_libdir", "timeout"])
        self.assertEqual(list(inspect.signature(xyce.smoke).parameters), ["nl", "dir", "osdi", "nvc_libdir", "timeout"])
        self.assertEqual(list(inspect.signature(vacask.emit).parameters), ["nl", "path", "analysis_name", "osdi", "notes"])
        self.assertEqual(list(inspect.signature(xyce.emit).parameters), ["nl", "path", "osdi", "notes"])


if __name__ == "__main__":
    unittest.main()
