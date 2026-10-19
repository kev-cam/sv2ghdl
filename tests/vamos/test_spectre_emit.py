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
TestSpectreBins         bin_bounds_spectre / bin_guard_spectre / select_bin_spectre, the Scope group order,
                        the total w with nf > 1 (a bsim4 group, §12 phase 2)
TestCondWalks           every shared walk sees a Cond (E44, E56): reachable, Scope, item_exprs,
                        enclosing_reads, element_at, smoke_netlist, xyce._multiplied
TestPathEnvsSpectre     the dialect="spectre" leg of path_envs: a nested definition sees the enclosing
                        subckt's parameters (S6 when S1's evaluator is not there yet)
TestSharedData          SPECTRE_OPTIONS, the method maps, wave_t0, va_letter / va_polarity
TestVacaskSpectre       the VACASK goldens and texts of every §7.1 mapping row the emitter owns
TestXyceSpectre         the Xyce goldens and texts of every §7.2 mapping row, output_problems and
                        device_summary_problems; path_of's noise-input and sweep-hook matches against
                        §5.3's one-path rule for hand-built steps (phase 2; expected failures until the
                        xyce.py seam of that phase's report is applied)
TestXyceSeams2c         the xyce.py seams of phase 2c's integration: a descending grid prints the plan's points
                        (LIST, DATA=; R-emit-3), the noise gain is the output's VM (R-emit-13)
TestRenders2c2          phase 2c2: VACASK's alter of isnoisy as noisy= (R-plan-19); a coupling as a sweep and
                        alter target (VACASK's k, Xyce's hook; the HSPICE path's K line unchanged)
TestHspiceRouteRegressions   the S3 shared fixes that reach the HSPICE path (§4)
TestGoldenHelper        golden() writes a missing golden to <name>.new and fails until it is checked in (2c)
TestRawfileStreaming    rawfile.iter_rows at a plot's byte offset and from a first row; the conversion reads each
                        Xyce plot and each VACASK group once (results.py passes both since the integration)
TestVacaskRuns, TestXyceRuns, TestCrossEngine (WSL)   the rendered decks on the engines, with the §13
                        values (E74, E17, E22, E35, E65, E32, E19, E18, E64, E67, E84); the bsim4 group with
                        nf > 1 runs as the entry its total w selects
TestFlowRuns, TestXyceFacts (WSL)   phase 2c: the review's decks through bin/vamos on both engines against their
                        analytic values (E126's swept gains, dev= gain and multiplier sweeps, multiplied sources,
                        Verilog-A targets, isnoisy through parameters, noise with a Verilog-A device, mag=0, the
                        tolerance notices of D2), and the E126 quirk itself on this Xyce
TestSeams2cRuns (WSL)   the integration's seams on both engines: descending grids (R-emit-3), spot frequency grids
                        in a sweep block (R-emit-4), the Xyce noise gain (R-emit-13), tnom in expressions
                        (R-lang-7), isnoisy parameters and the noise STRUCTs (R-emit-6)
TestFollowUpRuns2c2 (WSL)   phase 2c2 through bin/vamos on both engines: a resistance a sweep or alter drives to 0
                        (status 2), the alter of isnoisy against the unaltered reference runs and a short's
                        parameters (R-plan-19), a bound value parameter's sweep on VACASK (status 2) and the value
                        parameters' alters and a coupling sweep, two Verilog-A modules of one library on Xyce
"""

import copy
import itertools
import math
import os
import re
import struct
import unittest
from unittest import mock

from vamos_testlib import LAUNCHER, TempDir, fixture, needs_vacask, needs_xyce, run, vacask_bin, xyce_bin, xyce_env
from spectre_testlib import needs_pyms  # noqa: E402  (phase 2c2's F4 run)

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
    """The deck against tests/vamos/fixtures/spectre/emit/<name>.  Only VAMOS_REGEN_GOLDEN=1 writes a golden; a
    missing one is written to <name>.new beside it, and the test fails on every run until it is checked in
    (R-tests-18: writing it in place made the next run pass with an unreviewed golden)."""
    path = os.path.join(EMIT, name)
    if REGEN:
        os.makedirs(EMIT, exist_ok=True)
        with open(path, "w", newline="\n") as fh:
            fh.write(text)
    elif not os.path.exists(path):
        os.makedirs(EMIT, exist_ok=True)
        with open(path + ".new", "w", newline="\n") as fh:
            fh.write(text)
        test.fail("golden spectre/emit/%s is missing: the deck is in %s.new; review it and check it in as %s "
                  "(VAMOS_REGEN_GOLDEN=1 writes the goldens)" % (name, name, name))
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


B4 = {"toxe": 4e-9, "u0": 0.04}
BSIM4_REFS = [(("i", "vd"), "vd:p", "I"), (("i", "vr1"), "vr1:p", "I"), (("i", "vr2"), "vr2:p", "I")]


def bsim4_group_netlist(nf=4.0, w=2e-6, l=0.5e-6):
    """A Spectre bsim4 model group (bin_rule="spectre") whose two entries split at w = 1u.  The instance's
    total w selects the entry, also with nf > 1 (§3.9, §14 q.8): w=2u nf=4 selects entry 2, where HSPICE's
    per-finger rule (w/nf = 0.5u) would select entry 1.  Spectre's bsim3v3 instance has no nf, so this is the
    group golden with nf (§12 phase 2).  ref1 and ref2 are plain cards equal to the two entries, on instances
    of the same geometry, so an engine run shows which entry m1 ran as."""
    def entry(k, wmin, wmax, vth0):
        m = card("nch.%d" % k, "nmos", 54, "bsim4", vtho=vth0, lmin=1e-7, lmax=1e-5, wmin=wmin, wmax=wmax, **B4)
        m.base, m.bin_index, m.bin_rule = "nch", k, "spectre"
        return m
    params = {"w": Num(w), "l": Num(l), "nf": Num(float(nf))}
    nl = Netlist(title="spectre bsim4 model group, nf > 1", dialect="spectre")
    nl.body = [entry(1, 1e-7, 1e-6, 0.30), entry(2, 1e-6, 1e-4, 0.45),
               card("ref1", "nmos", 54, "bsim4", vtho=0.30, **B4), card("ref2", "nmos", 54, "bsim4", vtho=0.45, **B4),
               V("vd", "d", "0", 1.0), V("vg", "g", "0", 1.0), V("vr1", "r1", "0", 1.0), V("vr2", "r2", "0", 1.0),
               I("m1", "m", ["d", "g", "0", "0"], master="nch", params=dict(params), prim="bsim4"),
               I("mr1", "m", ["r1", "g", "0", "0"], master="ref1", params=dict(params), prim="bsim4"),
               I("mr2", "m", ["r2", "g", "0", "0"], master="ref2", params=dict(params), prim="bsim4")]
    nl.temp, nl.tnom = 27.0, 27.0
    nl.analyses = [Analysis("tran", {"step": 1e-10, "stop": 1e-9}, "deck:20")]
    return nl


def two_cells(shared):
    """The divider with subckt cell (r3, the source vb) under I2, and under I3 either cell again (shared: one
    definition, two instance paths) or `other`, a second definition whose body also holds an instance vb."""
    cell = Subckt("cell", ["a"], body=[R("r3", "a", "Mid", 1000.0), V("vb", "Mid", "0", dc=0.0)])
    extra = [cell]
    if not shared:
        extra.append(Subckt("other", ["a"], body=[R("r5", "a", "Mid", 1000.0), V("vb", "Mid", "0", dc=0.0)]))
    extra += [I("I2", "x", ["out"], master="cell"), I("I3", "x", ["out"], master="cell" if shared else "other")]
    return divider(extra=extra)


def hier_noise_netlist():
    """The divider with a noise input source vb inside subckt cell (I2) and a free node I2.Mid beside it, plus
    the top-level nodes '007' and 'net4<0>' that VACASK's netlist syntax quotes (§4.5 item 9)."""
    cell = Subckt("cell", ["a"], body=[R("r3", "a", "Mid", 1000.0), V("vb", "Mid", "M2", dc=0.0), R("r4", "M2", "0", 1000.0)])
    return divider(extra=[cell, I("I2", "x", ["out"], master="cell"), R("r7", "out", "007", 1000.0), R("r8", "007", "0", 1000.0),
                          R("rn", "out", "net4<0>", 1000.0), R("rn2", "net4<0>", "0", 1000.0)])


def hier_noise_plan():
    """Hand-built steps (§10): a noise with its input and output inside I2, an acxf of a pair with one node
    inside I2, and noises out of the two quoted top-level nodes."""
    nz = step("vamos_a1", "noise", options=OPTIONS, own=freq_level(count=1, start=1.0, stop=10.0), noise_input="I2.vb",
              args={"p": "I2.Mid", "n": "0"})
    xf = step("vamos_a2", "xf", options=OPTIONS, own=freq_level(count=1, start=1.0, stop=10.0, lid="vamos_f2"),
              args={"p": "I2.Mid", "n": "out"})
    n7 = step("vamos_a3", "noise", options=OPTIONS, own=freq_level(count=1, start=1.0, stop=10.0, lid="vamos_f3"),
              noise_input="v1", args={"p": "007", "n": "0"})
    n4 = step("vamos_a4", "noise", options=OPTIONS, own=freq_level(count=1, start=1.0, stop=10.0, lid="vamos_f4"),
              noise_input="v1", args={"p": "net4<0>", "n": "0"})
    return plan_of([P.Action("options", dict(OPTIONS), None, "o")] + [analysis(s) for s in (nz, xf, n7, n4)])


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


# -- phase 2c: the IR of the review's decks (sp2b; X-emit's findings) ------------------------------------

def gains_netlist(literal=False):
    """E126's circuits (R-plan-5, R-tests-1): a vcvs E1 gain=g and a vccs G1 gm=gm m=2 reading top-level
    parameters, a ccvs H1 rm=rm and a cccs F1 gain=fg probing Vs, and subckt amp's vcvs reading its parameter
    k, which X1 passes as sg (spectre.parse's IR of the gains deck).  literal: the gains are numbers (the
    dev= sweep deck)."""
    def gain(name, value):
        return Num(float(value)) if literal else Name(name)
    amp = Subckt("amp", ["i", "o"], params=[Param("k", Num(1.0))],
                 body=[I("E1", "e", ["o", "0", "i", "0"], value=Name("k"), prim="vcvs")])
    nl = Netlist(title="swept gains", dialect="spectre")
    nl.body = [Param("g", Num(0.5)), Param("gm", Num(1e-3)), Param("sg", Num(0.5)), Param("fg", Num(2.0)),
               Param("rm", Num(500.0)), amp, V("V1", "a", "0", 1.0),
               I("E1", "e", ["o1", "0", "a", "0"], value=gain("g", 0.5), prim="vcvs"), R("R2", "o1", "0", 1000.0),
               I("G1", "g", ["0", "o2", "a", "0"], value=gain("gm", 1e-3), params={"m": Num(2.0)}, prim="vccs"),
               R("R3", "o2", "0", 1000.0),
               I("X1", "x", ["a", "o5"], master="amp", params={"k": Name("sg")}, prim="subckt"), R("R5", "o5", "0", 1000.0),
               V("Vs", "a", "s", 0.0), R("Rs", "s", "0", 1000.0),
               I("H1", "h", ["o3", "0"], value=gain("rm", 500.0), ctrl=["Vs"], prim="ccvs"),
               I("F1", "f", ["0", "o4"], value=gain("fg", 2.0), ctrl=["Vs"], prim="cccs"), R("R4", "o4", "0", 1000.0)]
    nl.values = {"g": 0.5, "gm": 1e-3, "sg": 0.5, "fg": 2.0, "rm": 500.0}
    nl.temp, nl.tnom = 27.0, 27.0
    return nl


def nsy_netlist(second=True):
    """R-emit-6's deck nsy1: isnoisy through a netlist parameter (nflag=0) and a subckt parameter (nz=0 on X3,
    the default 1 on X4); spectre.py keeps a bare identifier written for isnoisy as a Str (§4.1 item 11)."""
    qr = Subckt("qr", ["a", "b"], params=[Param("nz", Num(1.0))],
                body=[R("R1", "a", "b", 1000.0, isnoisy=Str("nz"))])
    nl = Netlist(title="isnoisy through parameters", dialect="spectre")
    nl.body = [Param("nflag", Num(0.0)), V("V1", "in", "0", 0.0), R("R1", "in", "out", 1000.0),
               R("R2", "out", "0", 1000.0, isnoisy=Str("nflag")),
               I("X3", "x", ["out", "0"], master="qr", params={"nz": Num(0.0)}, prim="subckt"), qr]
    if second:
        nl.body.append(I("X4", "x", ["out", "0"], master="qr", prim="subckt"))
    nl.values = {"nflag": 0.0}
    nl.temp, nl.tnom = 27.0, 27.0
    return nl


def multiplied_netlist():
    """R-emit-7's deck mul1 with the other folded multipliers: Ia isource dc=1m m=2, Ib a pulse isource m=3,
    an isource inside X2 (m=2), a cccs F1 gain=2 m=2, a jfet J1 area=1 m=2 and a resistor R9 inside X5 (m=2)."""
    isub = Subckt("isub", ["p", "n"], body=[I("I1", "i", ["n", "p"], prim="isource", source=Source(dc=Num(1e-3)))])
    cell = Subckt("cell", ["p", "n"], body=[R("R9", "p", "n", 1000.0)])
    pulse = Source(wave="pulse", args={"v1": Num(1e-3), "v2": Num(2e-3), "td": Num(1e-6), "tr": Num(1e-6), "tf": Num(1e-6),
                                       "pw": Num(1e-6)})
    nl = Netlist(title="multiplied sources", dialect="spectre")
    nl.body = [isub, cell, V("V1", "d", "0", 1.0),
               I("Ia", "i", ["0", "a"], prim="isource", source=Source(dc=Num(1e-3), ac=(Num(1.0), Num(0.0))),
                 params={"m": Num(2.0)}), R("Ra", "a", "0", 1000.0),
               I("Ib", "i", ["0", "b"], prim="isource", source=pulse, params={"m": Num(3.0)}), R("Rb", "b", "0", 1000.0),
               I("X2", "x", ["c", "0"], master="isub", params={"m": Num(2.0)}, prim="subckt"), R("Rc", "c", "0", 1000.0),
               V("Vs", "d", "s", 0.0), R("Rs", "s", "0", 1000.0),
               I("F1", "f", ["0", "f"], value=Num(2.0), ctrl=["Vs"], params={"m": Num(2.0)}, prim="cccs"),
               R("Rf", "f", "0", 1000.0),
               card("jm", "njf", 1, "jfet", vto=-2.0, beta=1e-4),
               I("J1", "j", ["d", "0", "0"], master="jm", value=Num(1.0), params={"m": Num(2.0)}, prim="jfet"),
               I("X5", "x", ["d", "e"], master="cell", params={"m": Num(2.0)}, prim="subckt"), R("Re", "e", "0", 1000.0)]
    nl.temp, nl.tnom = 27.0, 27.0
    return nl


def va_card_netlist():
    """R-plan-4's Verilog-A decks: an instance y1 of the E64 resistor module with r=1k, a Verilog-A card vm
    whose parameters spectre.parse merged into its instance x7 (the same Expr objects, §3.9), and an
    instance y2 inside subckt cell (X2)."""
    nl = va_resistor(params={"R": Num(1000.0)})
    rv = Num(1000.0)
    nl.body += [card("vm", "resistor", None, "resistor", R=rv),
                I("x7", "y", ["a", "c"], master="resistor", params={"R": rv}, prim="resistor"), R("r7", "c", "0", 1000.0),
                Subckt("cell", ["p", "n"], body=[I("y2", "y", ["p", "n"], master="resistor", params={"R": Num(2000.0)},
                                                   prim="resistor")]),
                I("X2", "x", ["a", "d"], master="cell", prim="subckt"), R("r8", "d", "0", 1000.0)]
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

    def test_bsim4_group_with_nf_selects_on_the_total_w(self):
        """§3.9, §14 q.8: w is the instance's total w, also with nf > 1: w=2u nf=4 selects entry 2 (wmin=1u),
        where HSPICE's per-finger rule (w/nf = 0.5u) selects entry 1; with nf=1 the two rules agree."""
        nl = bsim4_group_netlist()
        bounds = [(b, T.bin_bounds_spectre(b, nl.values)) for b in T.Scope(nl.body).bins("nch")]
        self.assertEqual([b.name for b, _ in bounds], ["nch.1", "nch.2"])
        self.assertEqual(bounds[1][1], (1e-7, 1e-5, 1e-6, 1e-4))
        self.assertEqual(T.select_bin_spectre(bounds, 0.5e-6, 2e-6, 1.0).name, "nch.2")
        self.assertEqual(T.select_bin(bounds, 0.5e-6, 2e-6, 4.0, 1.0).name, "nch.1")       # HSPICE: w/nf
        self.assertEqual(T.select_bin(bounds, 0.5e-6, 2e-6, 1.0, 1.0).name, "nch.2")
        guards = [X.evaluate(T.bin_guard_spectre(bd, Num(0.5e-6), Num(2e-6), 1.0), {}) for _, bd in bounds]
        self.assertEqual(guards, [0.0, 1.0])                                            # no nf in the guard


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

    def test_golden_bsim4_group_nf(self):
        """§3.9, §4.5 item 3: a bsim4 group with nf=4 prints the @if chain in group order with exact guards on
        the total w: entry 1's guard is 0 and entry 2's 1 for w=2u (HSPICE's w/nf = 0.5u would hold entry
        1's), every copy keeping nf=4 for sp_bsim4v8; the entries print without their bounds."""
        text = vacask.render(bsim4_group_netlist())
        golden(self, "group_bsim4_nf_vacask.sim", text)
        self.assertIn("@if 0.0\n  m1 (d g 0 0) 'm_nch.1' w=2e-06 l=5e-07 nf=4.0 nrd=0.0 nrs=0.0\n"
                      "@elseif 1.0\n  m1 (d g 0 0) 'm_nch.2' w=2e-06 l=5e-07 nf=4.0 nrd=0.0 nrs=0.0\n"
                      "@else\n  m1 (d g 0 0) m_nch w=2e-06 l=5e-07 nf=4.0 nrd=0.0 nrs=0.0\n@end\n", text)
        self.assertIn('load "spice/bsim4v8.osdi"\n', text)
        self.assertIn("model 'm_nch.2' sp_bsim4v8 type=1.0 vth0=0.45 toxe=4e-09 u0=0.04 rdsmod=1.0 igbmod=1.0\n", text)
        self.assertNotIn("wmin", text)
        # nf=1: the same entry; a total w below entry 2's wmin selects entry 1 whatever nf
        self.assertIn("@if 0.0\n  m1 (d g 0 0) 'm_nch.1' w=2e-06 l=5e-07 nf=1.0", vacask.render(bsim4_group_netlist(nf=1.0)))
        self.assertIn("@if 1.0\n  m1 (d g 0 0) 'm_nch.1' w=5e-07 l=5e-07 nf=4.0", vacask.render(bsim4_group_netlist(w=0.5e-6)))

    def test_noise_and_xf_ports_print_unquoted_inside_their_strings(self):
        """E114's fact holds for the noise and acxf string arguments too: inside a string VACASK takes the
        name literally, so in="'I2:vb'" is "Instance ''I2:vb'' not found", out="'I2:Mid'", "'007'" and
        "'net4<0>'" are "Node ... not found", each analysis aborted, while out="007" and out="net4<0>" find
        the nodes the netlist quotes (WSL, 2026-10-17).  So in= and out= print the path as spath() does for
        sweep instance= and alter instance() (§4.5 item 5); today they carry the node quoting of item 9
        (seam H-2 of the phase-2 report: vacask.py; §5.6 writes in="'X1:V2'")."""
        text = vacask.render(hier_noise_netlist(), plan=hier_noise_plan(), sigmap=sigmap({}, []))
        self.assertIn('  analysis vamos_a1 noise out="I2:Mid" in="I2:vb" ', text)
        self.assertIn('  analysis vamos_a2 acxf out=["I2:Mid", "out"] ', text)
        self.assertIn('  analysis vamos_a3 noise out="007" in="v1" ', text)
        self.assertIn('  analysis vamos_a4 noise out="net4<0>" in="v1" ', text)

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
        # X1 binds r to rval: VACASK cannot sweep such a binding (exit 1, `Failed to bind sweep parameters', the 2c2
        # fixer's open run), so phase 2c4 (H1) prints it through a switchable hook and sweeps the hook's value; this
        # line pinned the instance sweep before.  Phase 2c7 (L3): the flag's statement sets the value to the first point
        self.assertIn("X1 (in out) rcsec r=(vamos_af1 ? vamos_av1 : rval)\n", text)
        self.assertIn('  var vamos_af1=1 vamos_av1=1000.0\n  sweep vamos_w1 variable="vamos_av1" values=[1000.0, 2000.0] '
                      'continuation=1\n', text)

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

    # -- phase 2c (X-emit) ------------------------------------------------------------------------------------

    def control_of(self, nl, actions):
        text = vacask.render(nl, plan=plan_of([P.Action("options", dict(OPTIONS), None, "o")] + list(actions)),
                             sigmap=sigmap({}, []))
        return text, text[text.index("control\n"):]

    def test_multiplier_and_gain_targets_take_vacask_spellings(self):
        """R-plan-4: VACASK reported `parameter 'm' of instance 'R1' not found' for every dev= sweep and alter
        of m, and `'gm'`/`'rm' ... not found' for a vccs/ccvs gain: the multiplier is VACASK's $mfactor (raw
        VACASK swept and altered it on a resistor, a subckt instance and an isource, X-emit 2c) and a controlled
        source's gain is VACASK's gain (dev-builtin-vccs.md, dev-builtin-ccvs.md)."""
        nl = gains_netlist(literal=True)
        nl.body.append(I("I1", "i", ["0", "c"], prim="isource", source=Source(dc=Num(1e-3)), params={"m": Num(2.0)}))
        nl.body.append(R("R6", "c", "0", 1000.0, m=1))
        sw = lambda sid, target, *vals: analysis(step(sid, "dc", options=OPTIONS,       # noqa: E731
                                                      own=level(target, values_spec(*vals), "vamos_w" + sid[-1])))
        _text, control = self.control_of(nl, [
            sw("vamos_a1", ("instance", "R6", "m"), 1, 2, 4), sw("vamos_a2", ("instance", "I1", "m"), 1, 2),
            sw("vamos_a3", ("instance", "G1", "gm"), 1e-3, 2e-3), sw("vamos_a4", ("instance", "H1", "rm"), 500, 1000),
            sw("vamos_a5", ("instance", "F1", "gain"), 2, 3), sw("vamos_a6", ("instance", "E1", "gain"), 0.5, 1),
            P.Action("alter", {"target": ("instance", "R6", "m"), "value": 4.0}, None, "a1"),
            P.Action("alter", {"target": ("instance", "G1", "gm"), "value": 2e-3}, None, "a2"),
            P.Action("alter", {"target": ("instance", "G1", "m"), "value": 3.0}, None, "a3")])
        self.assertEqual(lines(control, r"^  (sweep|alter)"), [
            '  sweep vamos_w1 instance="R6" parameter="$mfactor" values=[1.0, 2.0, 4.0] continuation=1',
            '  sweep vamos_w2 instance="I1" parameter="$mfactor" values=[1.0, 2.0] continuation=1',
            '  sweep vamos_w3 instance="G1" parameter="gain" values=[0.001, 0.002] continuation=1',
            '  sweep vamos_w4 instance="H1" parameter="gain" values=[500.0, 1000.0] continuation=1',
            '  sweep vamos_w5 instance="F1" parameter="gain" values=[2.0, 3.0] continuation=1',
            '  sweep vamos_w6 instance="E1" parameter="gain" values=[0.5, 1.0] continuation=1',
            '  alter instance("R6") $mfactor=4.0',
            '  alter instance("G1") gain=0.002',
            '  alter instance("G1") $mfactor=3.0'])
        # the m of an instance inside a multiplied subckt instance: VACASK's $mfactor of the device carries the
        # enclosing multiplier too (R9's is $mfactor=$mfactor, X5 m=2), so setting it would drop that: refused
        m = multiplied_netlist()
        with self.assertRaisesRegex(NoteError, r"X5\.R9: .*m=2"):
            self.control_of(m, [sw("vamos_a1", ("instance", "X5.R9", "m"), 1, 2)])
        # a multiplier folded into the element (a coupled inductor's l) cannot be swept: refused
        k = divider(extra=[I("L1", "l", ["out", "0"], value=Num(1e-6), params={"m": Num(2.0)}, prim="inductor"),
                           I("L2", "l", ["in", "0"], value=Num(1e-6), prim="inductor"),
                           I("K1", "k", [], value=Num(0.5), ctrl=["L1", "L2"], prim="mutual_inductor")])
        with self.assertRaisesRegex(NoteError, r"L1: .*folded"):
            self.control_of(k, [sw("vamos_a1", ("instance", "L1", "m"), 1, 2)])

    def test_verilog_a_targets_name_the_per_instance_card(self):
        """R-plan-4: a Verilog-A module's parameters are printed on the instance's own card <inst>__va (§4.4),
        so a dev= sweep or alter of one targets that card (`parameter 'r' of instance 'X1' not found` before;
        raw VACASK swept model="X1__va" and, inside a subckt, the hierarchical model="X2:Y1__va", X-emit 2c),
        lower-cased (§4.5 item 12); its m stays the instance's $mfactor.  A mod= target on a Verilog-A card
        (whose parameters spectre.parse merged into its instances, §3.9) names the cards of the instances that
        read the card's value: `model 'm_vm' not found` before."""
        nl = va_card_netlist()
        sw = lambda sid, target, *vals: analysis(step(sid, "dc", options=OPTIONS,       # noqa: E731
                                                      own=level(target, values_spec(*vals), "vamos_w" + sid[-1])))
        text, control = self.control_of(nl, [
            sw("vamos_a1", ("instance", "y1", "R"), 1e3, 2e3), sw("vamos_a2", ("instance", "X2.y2", "R"), 1e3, 2e3),
            sw("vamos_a3", ("model", "vm", "R"), 1e3, 3e3), sw("vamos_a4", ("instance", "y1", "m"), 1, 2),
            P.Action("alter", {"target": ("instance", "y1", "R"), "value": 3e3}, None, "a1"),
            P.Action("alter", {"target": ("model", "vm", "R"), "value": 3e3}, None, "a2"),
            P.Action("alter", {"target": ("instance", "X2.y2", "R"), "value": 3e3}, None, "a3")])
        self.assertIn("model y1__va resistor r=1000.0\ny1 (a b) y1__va\n", text)
        self.assertIn("model x7__va resistor r=1000.0\nx7 (a c) x7__va\n", text)
        self.assertIn("  model y2__va resistor r=2000.0\n  y2 (p n) y2__va $mfactor=$mfactor\n", text)
        self.assertEqual(lines(control, r"^  (sweep|alter)"), [
            '  sweep vamos_w1 model="y1__va" parameter="r" values=[1000.0, 2000.0] continuation=1',
            '  sweep vamos_w2 model="X2:y2__va" parameter="r" values=[1000.0, 2000.0] continuation=1',
            '  sweep vamos_w3 model="x7__va" parameter="r" values=[1000.0, 3000.0] continuation=1',
            '  sweep vamos_w4 instance="y1" parameter="$mfactor" values=[1.0, 2.0] continuation=1',
            '  alter model("y1__va") r=3000.0',
            '  alter model("x7__va") r=3000.0',
            '  alter model("X2:y2__va") r=3000.0'])
        # two instances read card vm's r: an alter names both cards, a sweep (one model per VACASK sweep) is refused
        nl.body.append(I("x8", "y", ["a", "c"], master="resistor", params={"R": nl.body[3].params["R"]}, prim="resistor"))
        _text, control = self.control_of(nl, [P.Action("alter", {"target": ("model", "vm", "R"), "value": 3e3}, None, "a2")])
        self.assertIn('  alter model("x7__va", "x8__va") r=3000.0\n', control)
        with self.assertRaisesRegex(NoteError, r"vm.* 2 instances"):
            self.control_of(nl, [sw("vamos_a3", ("model", "vm", "R"), 1e3, 3e3)])
        # an instance that overrides the card's r does not follow a mod= target of r (Spectre's instance value wins)
        nl.body[-1].params = {"R": Num(5e3)}
        _text, control = self.control_of(nl, [P.Action("alter", {"target": ("model", "vm", "R"), "value": 3e3}, None, "a2")])
        self.assertIn('  alter model("x7__va") r=3000.0\n', control)
        # a card that sets no parameter leaves nothing in its instances to tell them by: refused
        nl.body.append(card("vq", "resistor", None, "resistor"))
        with self.assertRaisesRegex(NoteError, "Verilog-A card vq cannot be told apart"):
            self.control_of(nl, [P.Action("alter", {"target": ("model", "vq", "R"), "value": 3e3}, None, "a4")])

    def test_isnoisy_through_a_parameter_prints_noisy_of_it(self):
        """R-emit-6: isnoisy=nflag (parameters nflag=0) and isnoisy=nz (a subckt parameter, nz=0 on X3) were
        dropped: spectre.py keeps a bare word for isnoisy as a Str, and only no/false/0 counted as off.  A word
        that is not no/yes names a parameter (Spectre numbers its enumerations from 0, UG p.81; isnoisy=no is
        0, ref5 p.629): VACASK prints noisy=<it>, which it evaluates per instance (noisy is an integer parameter:
        a real 0.0 there turned the resistor's noise off on raw VACASK, X-emit 2c)."""
        nl = nsy_netlist()
        text, _control = self.control_of(nl, [analysis(step("vamos_a1", "op", options=OPTIONS))])
        self.assertIn("R2 (out 0) vamos_resistor r=1000.0 noisy=nflag\n", text)
        self.assertIn("  R1 (a b) vamos_resistor r=1000.0 noisy=nz $mfactor=$mfactor\n", text)
        self.assertIn("R1 (in out) vamos_resistor r=1000.0\n", text)
        for word, tail in (("no", " noisy=0"), ("No", " noisy=0"), ("false", " noisy=0"), ("yes", ""), ("true", "")):
            nl.body[3].params["isnoisy"] = Str(word)
            text, _control = self.control_of(nl, [analysis(step("vamos_a1", "op", options=OPTIONS))])
            self.assertIn("R2 (out 0) vamos_resistor r=1000.0%s\n" % tail, text, word)
        nl.body[3].params["isnoisy"] = Num(0.0)
        self.assertIn("R2 (out 0) vamos_resistor r=1000.0 noisy=0\n", self.control_of(nl, [])[0])
        nl.body[3].params["isnoisy"] = Binary("*", Name("nflag"), Num(1.0))
        self.assertIn("R2 (out 0) vamos_resistor r=1000.0 noisy=nflag*1.0\n", self.control_of(nl, [])[0])


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

    def test_a_portless_subckt_is_refused(self):
        """Measured (phase 2, E2's e2e 23): Xyce's instances of a subckt without ports share its internal
        nodes and V(X2:M1) is undefined, so a Spectre netlist instantiating one is an error on Xyce (§0's
        one rule); VACASK keeps the instances apart and renders it.  The HSPICE route is not touched."""
        def netlist(dialect):
            s = Subckt("s2", [], body=[R("r1", "m1", "0", 7.0), V("v1", "m1", "0", 1.0)])
            nl = divider(extra=[s, I("X2", "x", [], master="s2"), I("X3", "x", [], master="s2")])
            nl.dialect = dialect
            nl.analyses = [Analysis("tran", {"step": 1e-9, "stop": 1e-8}, "deck:9")]
            return nl
        with self.assertRaisesRegex(NoteError, "X2: subckt s2 has no ports: on Xyce the instances of a portless "
                                               "subckt share its internal nodes"):
            xyce.render(netlist("spectre"))
        self.assertIn("\nX2 () s2\nX3 () s2\n", vacask.render(netlist("spectre")))
        self.assertIn("\nX2 s2\nX3 s2\n", xyce.render(netlist("hspice")))

    def test_golden_spectre_group(self):
        text = xyce.render(group_netlist())
        golden(self, "group_xyce.cir", text)
        self.assertIn("m1 d g 0 0 nch.2 w=2e-06 l=5e-07 nrd=0.0 nrs=0.0\n", text)         # bound per path, group order
        self.assertIn(".model nch.2 nmos level=9 tox=4e-09 toxm=4e-09 u0=0.04 vth0=0.42 lmax=1e-06 wmax=0.0001 lmin=2e-07 wmin=1e-06 capmod=2.0\n", text)
        self.assertEqual(len(lines(text, r"^\.model nch\.")), 1)                           # only the bound entry
        with self.assertRaisesRegex(NoteError, "no bin of nch for l=0.05"):
            xyce.render(group_netlist(l=0.05))

    def test_golden_bsim4_group_nf(self):
        """§3.9, §4.5 item 3: on Xyce the bsim4 group instance with nf=4 is bound to the entry its total w
        selects (nch.2; HSPICE's w/nf would give nch.1), nf printed on the M line, only that entry printed."""
        text = xyce.render(bsim4_group_netlist())
        golden(self, "group_bsim4_nf_xyce.cir", text)
        self.assertIn("m1 d g 0 0 nch.2 w=2e-06 l=5e-07 nf=4.0 nrd=0.0 nrs=0.0\n", text)
        self.assertEqual(lines(text, r"^\.model nch\."),
                         [".model nch.2 nmos level=54 vth0=0.45 lmin=1e-07 lmax=1e-05 wmin=1e-06 wmax=0.0001 toxe=4e-09 "
                          "u0=0.04 rdsmod=1.0 igbmod=1.0"])
        self.assertIn("m1 d g 0 0 nch.1 w=5e-07 l=5e-07 nf=4.0", xyce.render(bsim4_group_netlist(w=0.5e-6)))

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

    # xyce.path_of's noise-input match and the sweep hooks assume §5.3's one-path rule, which only plan.build
    # enforces (§12 phase 2): hand-built steps naming a source in a shared definition, or in a definition whose
    # instance name another definition reuses.  The expected failures are today's behaviour (seam H-4 of the
    # phase-2 report: xyce.py); the integration stage removes the decorators with the fix.

    def test_noise_input_match_checks_the_x_lines_master(self):
        """A hierarchical noise input I2.vb prints AC 1 0 in cell, the master of I2, not in other, whose body
        holds an instance vb too (path_of compares the X line's master)."""
        nz = step("vamos_a1", "noise", options=OPTIONS, own=freq_level(), noise_input="I2.vb", args={"p": "out", "n": "0"})
        text = xyce.render(two_cells(shared=False), step=nz, sigmap=sigmap({}, []))
        self.assertIn("vb Mid 0 DC 0.0 AC 1 0\n.ends cell\n", text)
        self.assertIn("vb Mid 0 DC 0.0\n.ends other\n", text)
        self.assertIn(".NOISE V(OUT) XI2:VB DEC 10 1.0 1000000.0\n", text)

    def test_noise_input_inside_a_shared_subckt_is_refused(self):
        """§5.3, §5.6: on Xyce a noise input inside a subckt that two instance paths reach is an error in v1.
        Today xyce.render prints AC 1 0 into the shared definition, so I3.vb is an input too: Xyce's INOISE is
        7.367e-18 V^2/Hz against 1.6576e-17 with the definition uniquified (ONOISE equal; WSL, 2026-10-16)."""
        nz = step("vamos_a1", "noise", options=OPTIONS, own=freq_level(), noise_input="I2.vb", args={"p": "out", "n": "0"})
        with self.assertRaisesRegex(NoteError, "I2, I3"):
            xyce.render(two_cells(shared=True), step=nz, sigmap=sigmap({}, []))

    def test_sweep_hook_inside_a_shared_subckt_is_refused(self):
        """§5.3: on Xyce a sweep of a field of a source inside a subckt that two instance paths reach is an
        error in v1.  Today the shared definition's vb reads the hook, so I3.vb is swept with I2.vb."""
        dc = step("vamos_a2", "dc", options=OPTIONS, own=level(("instance", "I2.vb", "dc"), values_spec(0.1, 0.2)))
        with self.assertRaisesRegex(NoteError, "I2, I3"):
            xyce.render(two_cells(shared=True), step=dc, sigmap=sigmap({}, []))

    def test_sweep_hook_reaches_only_its_own_path(self):
        """A dc of I2.vb's dc where `other` (I3) holds a vb too: only cell's vb may read the hook.  Today
        path_of matches the instance name alone, so other's vb reads it as well: Xyce sweeps V(XI3:MID) 0.1,
        0.2 where VACASK keeps I3:Mid at 0 (V(OUT) 0.3/0.35 against 0.275/0.3; WSL, 2026-10-16).  plan.build
        accepts the Spectre deck (cell has one path), so this reaches real runs."""
        dc = step("vamos_a2", "dc", options=OPTIONS, own=level(("instance", "I2.vb", "dc"), values_spec(0.1, 0.2)))
        text = xyce.render(two_cells(shared=False), step=dc, sigmap=sigmap({}, []))
        self.assertIn("vb Mid 0 DC {vamos_x_I2_vb_dc}\n.ends cell\n", text)
        self.assertIn("vb Mid 0 DC 0.0\n.ends other\n", text)

    def test_sub_hook_reaches_only_its_own_path(self):
        """The sub= form of the previous case (an X line's parameter, §7.2's generated .param): a sweep of
        I2.xs's w where `other` (I3) holds an X line xs too; only cell's xs may read the hook."""
        leaf = Subckt("leaf", ["a"], params=[Param("w", Num(1e-6))], body=[R("rl", "a", "0", Binary("*", Name("w"), Num(1e9)))])
        cell = Subckt("cell", ["a"], body=[I("xs", "x", ["a"], master="leaf", params={"w": Num(1e-6)})])
        other = Subckt("other", ["a"], body=[I("xs", "x", ["a"], master="leaf", params={"w": Num(1e-6)})])
        nl = divider(extra=[leaf, cell, other, I("I2", "x", ["out"], master="cell"), I("I3", "x", ["out"], master="other")])
        dc = step("vamos_a3", "dc", options=OPTIONS, own=level(("instance", "I2.xs", "w"), values_spec(1e-6, 3e-6)))
        text = xyce.render(nl, step=dc, sigmap=sigmap({}, []))
        self.assertIn("xs a leaf w={vamos_x_I2_xs_w} vamos_mfactor={vamos_mfactor}\n.ends cell\n", text)
        self.assertIn("xs a leaf w=1e-06 vamos_mfactor={vamos_mfactor}\n.ends other\n", text)

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
        # noise: the input source prints AC 1 0, the output node through its printed path, DNO() columns [E19, E73, E84]
        nz = step("vamos_a4", "noise", options=OPTIONS, own=freq_level(), noise_input="v1", args={"p": "I2.Mid", "n": "0"})
        nrefs = [(("noise", "r1"), "r1", "resistor"), (("noise", "I2.r3"), "I2.r3", "resistor"), (("onoise",), "out", "V/sqrt(Hz)"),
                 (("gain",), "gain", "V/V"), (("inoise",), "in", "V/sqrt(Hz)")]
        names = {}
        text = xyce.render(nl, step=nz, sigmap=sigmap({"vamos_a4": nrefs}), names=names)
        self.assertIn("v1 in 0 DC 1.0 AC 1 0\n", text)
        # the gain is the output's VM (R-emit-13, phase 2c; this line read ... ONOISE INOISE before)
        self.assertIn(".NOISE V(XI2:MID) V1 DEC 10 1.0 1000000.0\n"
                      ".PRINT NOISE FILE=vamos_a4.prn DNO(R1) DNO(XI2:R3) ONOISE VM(XI2:MID) INOISE\n", text)
        # the other sources print no AC in the noise deck: Xyce's .NOISE drives every AC source, Spectre's gain
        # is from the input alone (phase 2: gain 0.8 for 0.6 with a second mag=1 source); the ac deck keeps them
        nl2 = copy.deepcopy(nl)
        nl2.body.append(V("v2", "o9", "0", 1.0, ac=(Num(1.0), Num(0.0))))
        nl2.body.append(R("r9", "o9", "out", 1000.0))
        text = xyce.render(nl2, step=nz, sigmap=sigmap({"vamos_a4": nrefs}))
        self.assertIn("v1 in 0 DC 1.0 AC 1 0\n", text)
        self.assertIn("v2 o9 0 DC 1.0\n", text)
        self.assertIn("v2 o9 0 DC 1.0 AC 1.0 0.0\n", xyce.render(nl2, step=ac, sigmap=sigmap({"vamos_a1": refs})))
        # an output across two nodes is refused on Xyce: it aborts on .NOISE V(a,b) (measured in phase 2, H)
        nz2 = step("vamos_a4", "noise", name="nz", options=OPTIONS, own=freq_level(), noise_input="v1",
                   args={"p": "out", "n": "I2.Mid"})
        with self.assertRaisesRegex((T.TableError, NoteError), r"noise nz: the output across two nodes \(out, I2.Mid\) is "
                                                               r"not supported on Xyce in v1"):
            xyce.render(nl, step=nz2, sigmap=sigmap({"vamos_a4": nrefs}))
        self.assertEqual(names["vamos_a4"], {("noise", "r1"): "DNO(R1)", ("noise", "I2.r3"): "DNO(XI2:R3)", ("onoise",): "ONOISE",
                                             ("gain",): "VM(XI2:MID)", ("inoise",): "INOISE"})
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
        # the tolerance note of §0, for the keys the user set only (D2; phase 1 pinned three for plan.build's defaults)
        notes = []
        xyce.render(nl, step=step("vamos_a8", "op", options=OPTIONS), sigmap=sigmap({}, []), notes=notes)
        self.assertEqual(len([n for n in notes if "not mapped on Xyce" in n.message]), 0)
        st = step("vamos_a8", "op", options=OPTIONS)
        st.user_options = {"reltol", "vabstol", "iabstol"}
        notes = []
        xyce.render(nl, step=st, sigmap=sigmap({}, []), notes=notes)
        self.assertEqual(len([n for n in notes if "not mapped on Xyce" in n.message]), 3)

    def test_isnoisy_g_form_and_r_zero(self):
        nl = divider(r1=1000.0)
        nl.body[1].params["isnoisy"] = Str("no")
        nl.body.append(R("rshort", "out", "o2", 0.0))
        nl.body.append(R("rt", "o2", "0", 1000.0, isnoisy=Str("no"), tc1=1e-3))
        nl.body.append(R("rsq", "o2", "o3", 0.0, isnoisy=Str("no")))                    # a short with isnoisy=no
        notes = []
        text = xyce.render(nl, step=step("vamos_a1", "op", options=OPTIONS), sigmap=sigmap({}, []), notes=notes)
        self.assertIn("gr1 in out in out 0.001\n", text)                                     # [E32]
        self.assertIn("rshort out o2 0.0\n", text)                                           # Xyce takes R=0 [E22]
        # phase 2's integration: r=0 with isnoisy=no is the short, not a G form of 1/0 ("division by zero"),
        # and its current I(RSQ) needs no probe
        self.assertIn("rsq o2 o3 0.0\n", text)
        self.assertEqual(xyce.names_for(nl, plan_of([]), sigmap({"s": [(("i", "rsq"), "rsq:1", "I")]}))["s"],
                         {("i", "rsq"): "I(RSQ)"})
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
        # Xyce wraps a long path onto the next line after `from' (phase 2, C): still a registration
        wrapped = ("Netlist warning: .HDL: compiled and registered resistor from\n /a/long/path/toy_resistor.va\n"
                   "***** Device Count Summary ...\n   Total Devices  2\n")
        self.assertEqual(xyce.device_summary_problems(wrapped, nl), [])
        counted = "***** Device Count Summary ...\n       Total Devices 3\nNumber of resistor models: 1\nNumber of resistor instances: 1\n"
        self.assertEqual(xyce.device_summary_problems(counted, nl), [])
        self.assertIn("was not bound", xyce.device_summary_problems("***** Device Count Summary ...\n       Total Devices 3\n", nl)[0].message)
        self.assertEqual(xyce.device_summary_problems("", Netlist()), [])
        self.assertEqual(xyce.device_summary_problems("", va_resistor(attrs=False)), [])  # the Y form is not checked

    # -- phase 2c (X-emit) ------------------------------------------------------------------------------------

    def test_a_gain_read_from_a_swept_parameter_prints_the_value_form(self):
        """R-plan-5 [E126]: a .DC or .STEP of a .param that a linear E or G gain reads gives gain 0 at a first
        point equal to the .param's nominal (Xyce swp-000 b = 0 for VACASK's 1.0, rc 0); under a sweep of a .param
        the E and G lines print Xyce's behavioral form, right at that point (raw Xyce, X-emit 2c), the G with its
        multiplier folded in and no m=; F and H keep the linear form, which is right there; a deck that sweeps
        no .param keeps every linear form."""
        nl = gains_netlist()
        dc = step("vamos_a1", "dc", options=OPTIONS, own=level(("variable", "g", ""), values_spec(0.5, 0.75, 2)))
        text = xyce.render(nl, step=dc, sigmap=sigmap({}, []))
        self.assertIn("\nE1 o1 0 VALUE={g*v(a)}\n", text)
        self.assertIn("\nG1 0 o2 VALUE={gm*2.0*v(a)}\n", text)
        self.assertIn(".subckt amp i o params: k=1.0 vamos_mfactor=1.0\nE1 o 0 VALUE={k*v(i)}\n.ends amp\n", text)
        self.assertIn("\nH1 o3 0 Vs {rm}\n", text)
        self.assertIn("\nF1 0 o4 Vs {fg}\n", text)
        self.assertIn(".DC g LIST 0.5 0.75 2.0\n", text)
        text = xyce.render(nl, step=step("vamos_a2", "op", options=OPTIONS), sigmap=sigmap({}, []))
        self.assertIn("\nE1 o1 0 a 0 {g}\n", text)
        self.assertIn("\nG1 0 o2 a 0 {gm} m=2.0\n", text)
        self.assertIn("\nE1 o 0 i 0 {k}\n", text)
        # a literal gain stays linear under a sweep
        lit = gains_netlist(literal=True)
        text = xyce.render(lit, step=dc, sigmap=sigmap({}, []))
        self.assertIn("\nE1 o1 0 a 0 0.5\n", text)
        # the controlling pair: v(c+,c-) when c- is not ground
        nl.body.append(I("E2", "e", ["o6", "0", "a", "s"], value=Name("g"), prim="vcvs"))
        self.assertIn("\nE2 o6 0 VALUE={g*v(a,s)}\n", xyce.render(nl, step=dc, sigmap=sigmap({}, [])))

    def test_e126_reaches_grids_trans_and_subckt_gains(self):
        """R-tests-1: E126's reach beyond a values= dc: a start/stop/step dc from the nominal (Xyce out [0, 2, 3]),
        a tran under a sweep block (Xyce's first tran 0 V throughout) and a subckt's vcvs whose gain is the swept
        parameter passed on the X line (o [0, 0.75]) print the VALUE= form too; a dev= sweep of a gain goes through
        a hook (Xyce's E/G/F/H take no named gain: `Function or variable E1:GAIN is not defined', rc 1)."""
        nl = gains_netlist()
        grid = level(("variable", "g", ""), SweepSpec("param", mode="step", start=Num(0.5), stop=Num(1.5), step=Num(0.5)))
        text = xyce.render(nl, step=step("vamos_a1", "dc", options=OPTIONS, own=grid), sigmap=sigmap({}, []))
        self.assertIn("\nE1 o1 0 VALUE={g*v(a)}\n", text)
        self.assertIn(".DC g 0.5 1.5 0.5\n", text)
        blk = level(("variable", "g", ""), values_spec(0.5, 0.75), "vamos_w2", continuation=0)
        tr = step("vamos_a2", "tran", options=OPTIONS, context=[blk], args={"step": 1e-8, "stop": 1e-6})
        text = xyce.render(nl, step=tr, sigmap=sigmap({}, []))
        self.assertIn("\nE1 o1 0 VALUE={g*v(a)}\n", text)
        self.assertIn(".STEP g LIST 0.5 0.75\n.TRAN 1e-08 1e-06\n", text)
        sub = step("vamos_a3", "op", options=OPTIONS, context=[level(("variable", "sg", ""), values_spec(0.5, 0.75), "vamos_w3")])
        text = xyce.render(nl, step=sub, sigmap=sigmap({}, []))
        self.assertIn(".subckt amp i o params: k=1.0 vamos_mfactor=1.0\nE1 o 0 VALUE={k*v(i)}\n.ends amp\n", text)
        self.assertIn("\nX1 a o5 amp k={sg}\n", text)
        # dev= sweeps of each controlled source's gain (literal gains, the dev= deck)
        lit = gains_netlist(literal=True)
        for name, param, line, nominal in (
                ("E1", "gain", "E1 o1 0 VALUE={vamos_x_E1_gain*v(a)}", "0.5"),
                ("G1", "gm", "G1 0 o2 VALUE={vamos_x_G1_gm*2.0*v(a)}", "0.001"),
                ("H1", "rm", "H1 o3 0 Vs {vamos_x_H1_rm}", "500.0"),
                ("F1", "gain", "F1 0 o4 Vs {vamos_x_F1_gain}", "2.0")):
            own = level(("instance", name, param), values_spec(1, 2), "vamos_w4")
            text = xyce.render(lit, step=step("vamos_a4", "dc", options=OPTIONS, own=own), sigmap=sigmap({}, []))
            self.assertIn("\n%s\n" % line, text)
            self.assertIn(".param vamos_x_%s_%s=%s\n.DC vamos_x_%s_%s LIST 1.0 2.0\n" % (name, param, nominal, name, param),
                          text)

    def test_hooks_apply_before_the_multiplier(self):
        """R-emit-7: i_i folded the multiplier into the source and source() then replaced the swept field by its
        bare hook, so a dc sweep of I1 (m=2) ran half the current (Xyce a [1, 2] for VACASK's [2, 4]): the hooks are
        applied first and the multiplier after, also to a pulse field (m=3), an isource inside a subckt instance
        with m=2 (vamos_mfactor) and a swept ac magnitude."""
        nl = multiplied_netlist()

        def deck(target, *vals, kind="dc", args=None):
            own = level(target, values_spec(*vals))
            return xyce.render(nl, step=step("vamos_a1", kind, options=OPTIONS, own=own, args=dict(args or {})),
                               sigmap=sigmap({}, []))
        self.assertRegex(deck(("instance", "Ia", "dc"), 1e-3, 2e-3),
                         r"\nIa 0 a DC \{vamos_x_Ia_dc\*2\.0\} AC 2\.0 0\.0\n")
        self.assertRegex(deck(("instance", "Ib", "val0"), 1e-3, 3e-3),
                         r"\nIb 0 b DC \{vamos_x_Ib_val0\*3\.0\} PULSE\(\{vamos_x_Ib_val0\*3\.0\} 0\.006 ")
        self.assertRegex(deck(("instance", "X2.I1", "dc"), 1e-3, 2e-3),
                         r"\nI1 n p DC \{vamos_x_X2_I1_dc\*vamos_mfactor\}\n")
        self.assertRegex(deck(("instance", "Ia", "mag"), 1, 2, kind="ac", args={"freq": 1e3}),
                         r"\nIa 0 a DC 0\.002 AC \{vamos_x_Ia_mag\*2\.0\} 0\.0\n")
        # the operating point keeps the folded values
        text = xyce.render(nl, step=step("vamos_a2", "op", options=OPTIONS), sigmap=sigmap({}, []))
        self.assertIn("\nIa 0 a DC 0.002 AC 2.0 0.0\n", text)

    def test_folded_multipliers_sweep_through_hooks(self):
        """R-emit-7's other folded multipliers (X-emit 2c, measured): a direct .DC J1:AREA drops the m folded into
        the JFET's area (Xyce I(V1) [-0.3, -0.6] mA for VACASK's [-0.6, -1.2]); .DC F1:M multiplies the cccs gain,
        which already holds m, again (f [4, 8, 16] for [2, 4, 8]); .DC X5:R9:M sets the device's M, which holds the
        enclosing vamos_mfactor (b [0.5, 0.67, 0.8] for [0.67, 0.8, 0.89]); .DC I1:M and J1:M are `not defined'.
        Each goes through a hook that the printed value reads."""
        nl = multiplied_netlist()

        def deck(target, *vals):
            own = level(target, values_spec(*vals))
            return xyce.render(nl, step=step("vamos_a1", "dc", options=OPTIONS, own=own), sigmap=sigmap({}, []))
        text = deck(("instance", "J1", "area"), 1, 2)
        self.assertIn("\nJ1 d 0 0 jm {vamos_x_J1_area*2.0}\n", text)
        self.assertIn(".param vamos_x_J1_area=1.0\n.DC vamos_x_J1_area LIST 1.0 2.0\n", text)
        self.assertIn("\nJ1 d 0 0 jm {vamos_x_J1_m}\n", deck(("instance", "J1", "m"), 1, 2))
        self.assertIn("\nF1 0 f Vs {2.0*vamos_x_F1_m}\n", deck(("instance", "F1", "m"), 1, 2, 4))
        self.assertIn("\nF1 0 f Vs {vamos_x_F1_gain*2.0}\n", deck(("instance", "F1", "gain"), 2, 3))
        self.assertIn("\nIa 0 a DC {0.001*vamos_x_Ia_m} AC {vamos_x_Ia_m} 0.0\n", deck(("instance", "Ia", "m"), 1, 2))
        text = deck(("instance", "X5.R9", "m"), 1, 2, 4)
        self.assertIn("\nR9 p n 1000.0 m={vamos_mfactor*vamos_x_X5_R9_m}\n", text)
        self.assertIn(".param vamos_x_X5_R9_m=1.0\n.DC vamos_x_X5_R9_m LIST 1.0 2.0 4.0\n", text)
        # a top-level device's m stays the device parameter, right there (Xyce b [0.5, 0.67, 0.8])
        self.assertIn(".DC RA:M LIST 1.0 2.0\n", deck(("instance", "Ra", "m"), 1, 2))

    def test_a_coupled_inductors_folded_value_sweeps_through_its_hook(self):
        """R-emit-7's folds, the coupled inductor: Xyce's K pass keeps only L, the multiplier and TCs folded into it,
        so a .DC/.STEP of L1:L set the unfolded value (an ac sweep of l at its nominal gave b 0.280+0.393j where the
        ac at the nominal gives 0.105+0.252j, both engines, X-emit 2c) and L1:M is dropped: the folded value reads
        the hook; a TC sweep is refused."""
        nl = divider(extra=[I("L1", "l", ["out", "0"], value=Num(1e-3), params={"m": Num(2.0)}, prim="inductor"),
                            I("L2", "l", ["in", "c"], value=Num(1e-3), prim="inductor"), R("R5", "c", "0", 1000.0),
                            I("K1", "k", [], value=Num(0.5), ctrl=["L1", "L2"], prim="mutual_inductor")])

        def deck(target, *vals):
            own = level(target, values_spec(*vals))
            return xyce.render(nl, step=step("vamos_a1", "ac", options=OPTIONS, own=own, args={"freq": 1e5}),
                               sigmap=sigmap({}, []))
        text = deck(("instance", "L1", "l"), 1e-3, 2e-3)
        self.assertIn("\nL1 out 0 {vamos_x_L1_l/2.0}\n", text)
        self.assertIn(".param vamos_x_L1_l=0.001\n.STEP vamos_x_L1_l LIST 0.001 0.002\n", text)
        self.assertIn("\nL1 out 0 {0.001/vamos_x_L1_m}\n", deck(("instance", "L1", "m"), 1, 2))
        self.assertIn("\nL2 in c {vamos_x_L2_l}\n", deck(("instance", "L2", "l"), 1e-3, 2e-3))   # unfolded: the hook alone
        with self.assertRaisesRegex(NoteError, "L1: the tc1 of a coupled inductor is folded"):
            deck(("instance", "L1", "tc1"), 0, 1e-3)
        # VACASK cannot fold a swept value: refused there, an unfolded coupled inductor's l sweeps as before
        sw = analysis(step("vamos_a1", "ac", options=OPTIONS, own=level(("instance", "L1", "l"), values_spec(1e-3, 2e-3)),
                           args={"freq": 1e5}))
        with self.assertRaisesRegex(NoteError, "L1: a coupled inductor's multiplier and TCs are folded into its l on VACASK"):
            vacask.render(nl, plan=plan_of([P.Action("options", dict(OPTIONS), None, "o"), sw]), sigmap=sigmap({}, []))
        sw2 = analysis(step("vamos_a1", "ac", options=OPTIONS, own=level(("instance", "L2", "l"), values_spec(1e-3, 2e-3)),
                            args={"freq": 1e5}))
        self.assertIn('sweep vamos_w1 instance="L2" parameter="l" values=[0.001, 0.002]',
                      vacask.render(nl, plan=plan_of([P.Action("options", dict(OPTIONS), None, "o"), sw2]), sigmap=sigmap({}, [])))

    def test_noise_with_a_verilog_a_instance_is_refused(self):
        """R-emit-8: a noise deck printed DNO(<inst>) for a Verilog-A instance, which PyMS devices do not define
        (`Function or variable DNO(RX1) is not defined', status 2 after the earlier analyses ran), and without the
        DNO ONOISE leaves the module's noise out (4.14e-18 for 8.29e-18): an error at render time, before any
        engine run, on both routes; the other analyses of the circuit render."""
        nz = step("vamos_a1", "noise", name="n1", options=OPTIONS, own=freq_level(), noise_input="v1", args={"p": "b", "n": "0"})
        for nl in (va_resistor(params={"R": Num(3e3)}), va_resistor(attrs=False)):
            with self.assertRaisesRegex(NoteError, r"noise n1: the Verilog-A instance y1 \(module resistor\) .*VACASK runs it"):
                xyce.render(nl, step=nz, sigmap=sigmap({}, []))
        xyce.render(va_resistor(params={"R": Num(3e3)}), step=step("vamos_a2", "op", options=OPTIONS), sigmap=sigmap({}, []))
        inner = va_resistor(params={"R": Num(3e3)})
        inner.body = [inner.body[0], Subckt("cell", ["a", "b"], body=[inner.body[1]]),
                      I("X1", "x", ["a", "b"], master="cell", prim="subckt"), inner.body[2]]
        with self.assertRaisesRegex(NoteError, r"Verilog-A instance X1\.y1 "):
            xyce.render(inner, step=nz, sigmap=sigmap({}, []))

    def test_a_zero_ac_magnitude_prints_no_ac_field(self):
        """R-emit-12: mag=0 is Spectre's default (ref5 p.685): a source given mag=0 printed AC 0.0 0.0, and Xyce's
        `AC magnitude is set to 0.0' was counted as a Spectre warning: no AC field then (inside a multiplied subckt
        too); a swept magnitude keeps its AC field whatever its nominal; the HSPICE path is untouched."""
        isub = Subckt("isub", ["p", "n"], body=[I("I4", "i", ["n", "p"], prim="isource",
                                                   source=Source(dc=Num(1e-3), ac=(Num(0.0), Num(0.0))))])
        nl = divider(extra=[I("I1", "i", ["0", "out"], prim="isource", source=Source(dc=Num(0.0), ac=(Num(0.0), Num(0.0)))),
                            V("V2", "x", "0", 0.0, ac=(Num(0.0), Num(0.0))), R("R3", "x", "0", 1000.0), isub,
                            I("X5", "x", ["b", "0"], master="isub", params={"m": Num(2.0)}, prim="subckt"),
                            R("R5", "b", "0", 1000.0)])
        text = xyce.render(nl, step=step("vamos_a1", "ac", options=OPTIONS, own=freq_level()), sigmap=sigmap({}, []))
        self.assertIn("\nI1 0 out DC 0.0\n", text)
        self.assertIn("\nV2 x 0 DC 0.0\n", text)
        self.assertIn("\nI4 n p DC {0.001*vamos_mfactor}\n", text)
        self.assertIn("\nv1 in 0 DC 1.0 AC 1.0 0.0\n", text)
        own = level(("instance", "V2", "mag"), values_spec(0.0, 1.0))
        text = xyce.render(nl, step=step("vamos_a2", "ac", options=OPTIONS, own=own, args={"freq": 1e3}), sigmap=sigmap({}, []))
        self.assertIn("\nV2 x 0 DC 0.0 AC {vamos_x_V2_mag} 0.0\n", text)
        hs = Netlist(title="hspice")
        hs.body = [I("v1", "v", ["a", "0"], source=Source(dc=Num(1.0), ac=(Num(0.0), Num(0.0)))), I("r1", "r", ["a", "0"], value=Num(1.0))]
        hs.analyses = [Analysis("tran", {"step": 1e-9, "stop": 1e-6}, "deck:9")]
        self.assertIn("v1 a 0 DC 1.0 AC 0.0 0.0\n", xyce.render(hs))

    def test_tolerance_notes_only_for_the_options_the_user_set(self):
        """D2 (R-emit-15, with R-run-15 and R-plan-20): a deck without an options statement got three `option ...
        is not mapped on Xyce' notices from plan.build's defaults: the notes now name only the keys in the step's
        user_options (AnalysisStep.user_options, plan.build's: what the netlist, +paramdefault or the command line
        set).  The tran's `Xyce takes the method ... only' note stays plan.build's (gated there by the same rule),
        so render prints none: one notice per tran."""
        nl = divider()
        notes = []
        xyce.render(nl, step=step("vamos_a1", "op", options=OPTIONS), sigmap=sigmap({}, []), notes=notes)
        self.assertEqual([n.message for n in notes if "on Xyce" in n.message or "Xyce takes" in n.message], [])
        op = step("vamos_a2", "op", options=dict(OPTIONS, reltol=1e-4))
        op.user_options = {"reltol"}
        notes = []
        xyce.render(nl, step=op, sigmap=sigmap({}, []), notes=notes)
        self.assertEqual([n.message for n in notes if "not mapped on Xyce" in n.message],
                         ["option reltol=0.0001 is not mapped on Xyce: its tolerances do not have SPICE's meaning, so the "
                          "engine runs with its defaults (§0, §3.10)"])
        tr = step("vamos_a3", "tran", name="tr1", options=OPTIONS,
                  args={"step": 1e-7, "stop": 1e-5, "maxstep": 2e-7, "reltol": 1e-3, "relref": "sigglobal", "method": "traponly",
                        "lteratio": 3.5, "errpreset": "moderate"})
        for user in (set(), {"lteratio"}, {"errpreset", "method"}):
            tr.user_options = user
            notes = []
            xyce.render(nl, step=tr, sigmap=sigmap({}, []), notes=notes)
            self.assertEqual([n.message for n in notes if "Xyce" in n.message], [], user)

    def test_a_group_instance_whose_geometry_is_not_a_number_is_refused_whatever_nf(self):
        """D5 (R-plan-18, R-emit-16): Xyce binds a Spectre model-group instance to its entry per path (§4.5 item 3),
        so a geometry that is not a number on its path (one reading temper) stays an error in v1, without nf as
        with it (the docstring's `when nf is not 1' is the HSPICE path's rule); VACASK renders it."""
        for nl in (group_netlist(), bsim4_group_netlist(nf=4.0)):
            m1 = next(it for it in nl.body if isinstance(it, Instance) and it.name == "m1")
            m1.params["w"] = Binary("*", Num(2e-6), Binary("+", Num(1.0), Binary("*", Num(0.0), Name("temper"))))
            with self.assertRaisesRegex(NoteError, r"m1: model group nch needs l and w constant on the instance path on Xyce"):
                xyce.render(nl)
            vacask.render(nl)

    def test_isnoisy_is_evaluated_on_each_instance_path(self):
        """R-emit-6 on Xyce: isnoisy=nflag (nflag=0) prints R2's noise-free G form, and qr's R1 is the G form on X3's
        path (nz=0) and a noisy R on X4's (nz=1): two variants of qr; the current of a G form needs a probe and its
        noise has no DNO; a value that is not a number on its path is an error."""
        nl = nsy_netlist()
        nz = step("vamos_a1", "noise", name="n1", options=OPTIONS, own=freq_level(), noise_input="V1",
                  args={"p": "out", "n": "0"})
        refs = [(("noise", "R1"), "R1", "resistor"), (("noise", "R2"), "R2", "resistor"),
                (("noise", "X3.R1"), "X3.R1", "resistor"), (("noise", "X4.R1"), "X4.R1", "resistor"), (("onoise",), "out", "V")]
        names = {}
        text = xyce.render(nl, step=nz, sigmap=sigmap({"vamos_a1": refs}), names=names)
        self.assertIn("\ngR2 out 0 out 0 0.001\n", text)
        self.assertIn(".subckt qr a b params: nz=1.0 vamos_mfactor=1.0\ngR1 a b a b {0.001*vamos_mfactor}\n.ends qr\n", text)
        self.assertIn(".subckt qr__vb1 a b params: nz=1.0 vamos_mfactor=1.0\nR1 a b 1000.0 m={vamos_mfactor}\n.ends qr__vb1\n", text)
        self.assertIn("\nX3 out 0 qr nz=0.0\n", text)
        self.assertIn("\nX4 out 0 qr__vb1\n", text)
        self.assertIn(".PRINT NOISE FILE=vamos_a1.prn DNO(R1) DNO(X4:R1) ONOISE\n", text)
        self.assertEqual(names["vamos_a1"], {("noise", "R1"): "DNO(R1)", ("noise", "X4.R1"): "DNO(X4:R1)", ("onoise",): "ONOISE"})
        with self.assertRaisesRegex(T.TableError, "R2: the current of the noise-free G form"):
            xyce.names_for(nl, plan_of([]), sigmap({"s": [(("i", "R2"), "R2:1", "I")]}))
        self.assertEqual(xyce.names_for(nl, plan_of([]), sigmap({"s": [(("i", "X4.R1"), "X4.R1:1", "I")]}))["s"],
                         {("i", "X4.R1"): "I(X4:R1)"})
        nl.body[3].params["isnoisy"] = Str("nosuch")
        with self.assertRaisesRegex(NoteError, "R2: isnoisy=nosuch is not a number on its path"):
            xyce.render(nl, step=nz, sigmap=sigmap({}, []))

    def test_a_noise_switch_that_a_sweep_flips_is_refused(self):
        """R-emit-6 on Xyce: the G form is chosen once per deck, so a noise under a sweep of the parameter isnoisy
        reads (nflag 0 then 1), or under a sub= sweep of the X line that sets it, would run every point with the
        nominal's form: an error (VACASK's noisy=nflag follows the sweep); a sweep of another parameter keeps the
        G form."""
        nl = nsy_netlist(second=False)
        nl.body.insert(1, Param("rx", Num(1.0)))
        nl.values["rx"] = 1.0

        def noise(lv):
            return step("vamos_a1", "noise", name="n1", options=OPTIONS, context=[lv], own=freq_level(lid="vamos_f1"),
                        noise_input="V1", args={"p": "out", "n": "0"})
        with self.assertRaisesRegex(NoteError, r"R2: isnoisy=nflag of R2 reads a parameter that noise n1 sweeps \(sw\)"):
            xyce.render(nl, step=noise(level(("variable", "nflag", ""), values_spec(0, 1), "vamos_w1", "sw")),
                        sigmap=sigmap({}, []))
        with self.assertRaisesRegex(NoteError, r"R1: isnoisy=nz of X3\.R1 reads a parameter that noise n1 sweeps"):
            xyce.render(nl, step=noise(level(("instance", "X3", "nz"), values_spec(0, 1), "vamos_w1", "sw")),
                        sigmap=sigmap({}, []))
        text = xyce.render(nl, step=noise(level(("variable", "rx", ""), values_spec(1, 2), "vamos_w1", "sw")),
                           sigmap=sigmap({}, []))
        self.assertIn("\ngR2 out 0 out 0 0.001\n", text)
        # a sweep that keeps the switch off at every point renders too
        text = xyce.render(nl, step=noise(level(("variable", "nflag", ""), values_spec(0, 0), "vamos_w1", "sw")),
                           sigmap=sigmap({}, []))
        self.assertIn("\ngR2 out 0 out 0 0.001\n", text)


def _floats(fields):
    return [float(f) for f in fields]


class TestXyceSeams2c(unittest.TestCase):
    """The xyce.py seams the phase-2c integration applied for X-out's findings (R-emit-3, R-emit-13); the
    $tnom one (R-lang-7) is test_spectre_lang.TestTnomOnXyce."""

    def close(self, got, want):
        self.assertEqual(len(got), len(want), (got, want))
        for g, w in zip(got, want):
            self.assertAlmostEqual(g, w, delta=abs(w) * 1e-12, msg=(got, want))

    def test_a_descending_grid_prints_the_plans_points(self):
        """R-emit-3: Xyce's DEC with start > stop computes one step at start, and .AC/.NOISE refuse start > stop
        under DEC and LIN alike [E116], while plan.grid_points and VACASK run the whole descending grid: such a
        grid prints the plan's points, as a .DC/.STEP LIST and as the .AC/.NOISE DATA= table (a descending step
        grid too, whose table was empty); an ascending grid keeps DEC and LIN."""
        nl = divider()
        dec = SweepSpec("param", mode="dec", start=Num(1e-3), stop=Num(1e-6), count=1)
        want = P.grid_points(dec)
        self.close(want, [1e-3, 1e-4, 1e-5, 1e-6])
        text = xyce.render(nl, step=step("vamos_a1", "dc", options=OPTIONS, own=level(("instance", "r2", "r"), dec)),
                           sigmap=sigmap({}, []))
        [dc] = lines(text, r"^\.DC ")
        self.assertEqual(dc.split()[:3], [".DC", "R2:R", "LIST"])
        self.close(_floats(dc.split()[3:]), want)
        text = xyce.render(nl, step=step("vamos_a1", "op", options=OPTIONS, context=[level(("instance", "r2", "r"), dec)]),
                           sigmap=sigmap({}, []))
        [st] = lines(text, r"^\.STEP ")
        self.assertEqual(st.split()[:3], [".STEP", "R2:R", "LIST"])
        self.close(_floats(st.split()[3:]), want)

        def ac_table(spec):
            own = level(("freq", "", ""), spec, "vamos_f1")
            text = xyce.render(nl, step=step("vamos_a1", "ac", options=OPTIONS, own=own), sigmap=sigmap({}, []))
            self.assertIn("\n.AC DATA=vamos_f_vamos_a1\n", text)
            body = text.split(".data vamos_f_vamos_a1\n+ hertz\n", 1)[1].split(".enddata", 1)[0]
            return _floats(l[2:] for l in body.splitlines())
        for spec, pts in ((SweepSpec("freq", mode="dec", start=Num(1e6), stop=Num(1e3), count=1), [1e6, 1e5, 1e4, 1e3]),
                          (SweepSpec("freq", mode="lin", start=Num(1e6), stop=Num(1e3), count=3), [1e6, 667000.0, 334000.0, 1e3]),
                          (SweepSpec("freq", mode="step", start=Num(1e6), stop=Num(1e3), step=Num(-333000.0)),
                           [1e6, 667000.0, 334000.0, 1e3])):
            got = ac_table(spec)
            self.close(got, pts)
            self.close(got, P.grid_points(spec))
        text = xyce.render(nl, step=step("vamos_a1", "ac", options=OPTIONS, own=freq_level("dec", 1e3, 1e6, 1)),
                           sigmap=sigmap({}, []))
        self.assertIn("\n.AC DEC 1 1000.0 1000000.0\n", text)
        text = xyce.render(nl, step=step("vamos_a1", "ac", options=OPTIONS, own=freq_level("lin", 1e3, 4e3, 3)),
                           sigmap=sigmap({}, []))
        self.assertIn("\n.AC LIN 4 1000.0 4000.0\n", text)
        text = xyce.render(nl, step=step("vamos_a1", "dc", options=OPTIONS,
                                         own=level(("instance", "r2", "r"),
                                                   SweepSpec("param", mode="dec", start=Num(1e3), stop=Num(1e5), count=1))),
                           sigmap=sigmap({}, []))
        self.assertIn("\n.DC DEC R2:R 1000.0 100000.0 1\n", text)

    def test_the_noise_gain_is_the_outputs_vm(self):
        """R-emit-13: Xyce's gain was sqrt(ONOISE/INOISE): nan with no noisy device on the output, 1e-10 where Xyce
        clamps a zero transfer (INOISE = ONOISE/1e-20).  The noise deck prints VM(<output>), the output's magnitude
        under the input's AC 1 (every other source prints no AC there), and the name map sends ('gain',) to it, in
        render and in names_for alike; without an input probe there is no gain and no VM."""
        nl = divider()
        nz = step("vamos_a1", "noise", name="n1", options=OPTIONS, own=freq_level(), args={"p": "out", "n": "0"},
                  noise_input="v1")
        sm = sigmap({"vamos_a1": [(("noise", "r1"), "r1", "resistor"), (("onoise",), "out", "V/sqrt(Hz)"),
                                  (("gain",), "gain", "V/V"), (("inoise",), "in", "V/sqrt(Hz)")]})
        names = {}
        text = xyce.render(nl, step=nz, sigmap=sm, names=names)
        self.assertIn("\n.PRINT NOISE FILE=vamos_a1.prn DNO(R1) ONOISE VM(OUT) INOISE\n", text)
        self.assertEqual(names["vamos_a1"][("gain",)], "VM(OUT)")
        self.assertEqual(names["vamos_a1"][("inoise",)], "INOISE")
        self.assertEqual(xyce.names_for(nl, plan_of([analysis(nz)], engine="xyce"), sm), names)
        sm = sigmap({"vamos_a1": [(("onoise",), "out", "V/sqrt(Hz)")]})
        text = xyce.render(nl, step=nz, sigmap=sm)
        self.assertIn("\n.PRINT NOISE FILE=vamos_a1.prn ONOISE\n", text)


def coupled_netlist(dialect="spectre"):
    """An AC current into L1 (1 mH) coupled to L2 (9 mH) by K1, k=0.5 (e2e 30's transformer)."""
    nl = Netlist(title="k", dialect=dialect)
    nl.body = [I("iac", "i", ["0", "p"], source=Source(dc=Num(0.0), ac=(Num(1e-3), Num(0.0))), prim="isource"),
               I("l1", "l", ["p", "0"], value=Num(1e-3), prim="inductor"),
               I("l2", "l", ["s", "0"], value=Num(9e-3), prim="inductor"),
               I("k1", "k", [], value=Num(0.5), ctrl=["l1", "l2"], prim="mutual_inductor")]
    nl.temp, nl.tnom = 27.0, 27.0
    return nl


class TestRenders2c2(unittest.TestCase):
    """Phase 2c2: the alter of a resistor's isnoisy on VACASK (R-plan-19), and a mutual inductor's coupling as a
    sweep and alter target on both engines (VACASK's k; Xyce's positional K value through a hook)."""

    def test_vacask_alters_isnoisy_as_noisy(self):
        """`Parameter 'isnoisy' not found' at the alter before: VACASK's resistor and sp_resistor call it noisy, an
        integer; the words no and false and the number 0 turn it off, yes, true and other numbers on."""
        values = ["no", 0.0, "False", "yes", 2.0, "1"]
        acts = [P.Action("alter", {"target": ("instance", "r1", "isnoisy"), "value": v}, None, "a%d" % k)
                for k, v in enumerate(values)]
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o"), analysis(step("vamos_a1", "op", options=OPTIONS))]
                     + acts + [analysis(step("vamos_a2", "op", options=OPTIONS))])
        text = vacask.render(divider(), plan=pl, sigmap=sigmap({}, []))
        self.assertEqual(lines(text, r"^  alter "), ['  alter instance("r1") noisy=%s' % n for n in "000111"])
        bad = plan_of([P.Action("options", dict(OPTIONS), None, "o"),
                       P.Action("alter", {"target": ("instance", "r1", "isnoisy"), "value": "maybe"}, None, "a9"),
                       analysis(step("vamos_a1", "op", options=OPTIONS))])
        with self.assertRaisesRegex(NoteError, "isnoisy=maybe is not yes, no, true, false or a number"):
            vacask.render(divider(), plan=bad, sigmap=sigmap({}, []))

    def test_a_coupling_target_on_both_engines(self):
        """VACASK reported `Parameter 'coupling' not found' at an alter and could not bind a sweep (its mutual
        calls it k); Xyce's .STEP K1:COUPLING was `Function or variable K1:COUPLING is not defined', so the K line
        prints the hook the sweep sets.  The HSPICE path keeps its K line."""
        ac = step("vamos_a2", "ac", options=OPTIONS, own=level(("instance", "k1", "coupling"), values_spec(0.25, 0.5),
                                                                "vamos_w2"), args={"freq": 1e3})
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o"),
                      P.Action("alter", {"target": ("instance", "k1", "coupling"), "value": 0.25}, None, "a1"),
                      analysis(ac)])
        text = vacask.render(coupled_netlist(), plan=pl, sigmap=sigmap({}, []))
        self.assertIn('  alter instance("k1") k=0.25\n', text)
        self.assertIn('  sweep vamos_w2 instance="k1" parameter="k" values=[0.25, 0.5] continuation=1\n', text)
        text = xyce.render(coupled_netlist(), step=ac, sigmap=sigmap({"vamos_a2": []}), names={})
        self.assertIn("\nk1 l1 l2 {vamos_x_k1_coupling}\n", text)
        self.assertIn("\n.param vamos_x_k1_coupling=0.5\n", text)
        self.assertIn("\n.STEP vamos_x_k1_coupling LIST 0.25 0.5\n", text)
        hs = coupled_netlist("hspice")
        hs.analyses = [Analysis("tran", {"step": 1e-9, "stop": 1e-6}, "deck:9")]
        self.assertIn("\nk1 l1 l2 0.5\n", xyce.render(hs))


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


class TestGoldenHelper(TempDir):
    """R-tests-18: golden() wrote a missing golden into tests/vamos/fixtures/spectre/emit/ and failed once, so the
    next run passed with an unreviewed golden (a whole-suite run wrote goldens into the tree): without
    VAMOS_REGEN_GOLDEN=1 the deck goes to <name>.new beside it, and the test fails on every run until the golden
    is checked in."""

    def test_a_missing_golden_fails_every_run_and_writes_only_new(self):
        d = os.path.join(self.tmp, "emit")
        with mock.patch.dict(globals(), {"EMIT": d, "REGEN": False}):
            for _run in (1, 2):
                with self.assertRaises(self.failureException) as cm:
                    golden(self, "x_vacask.sim", "deck\n")
                self.assertIn("x_vacask.sim.new", str(cm.exception))
            self.assertFalse(os.path.exists(os.path.join(d, "x_vacask.sim")))
            with open(os.path.join(d, "x_vacask.sim.new")) as fh:
                self.assertEqual(fh.read(), "deck\n")
            with open(os.path.join(d, "x_vacask.sim"), "w") as fh:     # checked in: it compares
                fh.write("deck\n")
            golden(self, "x_vacask.sim", "deck\n")
            with self.assertRaises(self.failureException):
                golden(self, "x_vacask.sim", "other\n")
        with mock.patch.dict(globals(), {"EMIT": d, "REGEN": True}):       # VAMOS_REGEN_GOLDEN=1 writes in place
            golden(self, "y_xyce.cir", "regen\n")
            with open(os.path.join(d, "y_xyce.cir")) as fh:
                self.assertEqual(fh.read(), "regen\n")


# =============================================================================================
# rawfile.py: the streamed reader of the conversion (R-emit-9)
# =============================================================================================

def write_binary_raw(path, plots, rows=1, names=("sweep", "V(OUT)"), first=None):
    """A binary rawfile of `plots` plots of `rows` rows each, as Xyce writes one plot per .STEP point (and VACASK
    one plot holding every group): row i of plot k holds first(k, i) for column 0 (k + i/1000 by default) and -k."""
    first = first or (lambda k, i: k + i / 1000.0)
    with open(path, "wb") as fh:
        for k in range(plots):
            head = ("Title: t\nDate: d\nPlotname: Step %d\nFlags: real\nNo. Variables: %d\nNo. Points: %d\nVariables:\n"
                    % (k, len(names), rows))
            head += "".join("\t%d\t%s\tvoltage\n" % (j, n) for j, n in enumerate(names)) + "Binary:\n"
            fh.write(head.encode("latin-1"))
            for i in range(rows):
                vals = [first(k, i)] + [-float(k)] * (len(names) - 1)
                fh.write(struct.pack("<%dd" % len(names), *vals))


class TestRawfileStreaming(TempDir):
    """R-emit-9: the conversion time grew with the square of the sweep points: rawfile.iter_rows re-scanned every
    earlier plot to reach plot k (results._raw_plots reads each Xyce .STEP plot by its number: n^2/2 plot scans,
    16.5 s for n = 2000 against Xyce's 0.4 s), and results._Step.row_source read each group of a swept VACASK plot
    from row 0 (n^2/2 groups of rows, 8.8 s for 100 tran leaves against one pass's 0.18 s).  iter_rows takes the
    plot's byte offset (start=, from scan's next_start chain) and a first row (first=: binary rows are seeked,
    ASCII rows skipped by the token walk); results passes both (_raw_plots and _Step.row_source)."""

    def counting_scans(self):
        calls = {"plots": 0}
        real = rawfile._scan_plot

        def scan_plot(mm, pos, path):
            calls["plots"] += 1
            return real(mm, pos, path)
        return calls, mock.patch.object(rawfile, "_scan_plot", scan_plot)

    def test_iter_rows_reads_a_plot_at_its_byte_offset(self):
        p = os.path.join(self.tmp, "steps.raw")
        write_binary_raw(p, 200)
        plots = rawfile.scan(p)
        starts = [0] + [pl.next_start for pl in plots[:-1]]
        calls, patch = self.counting_scans()
        with patch:
            got = [list(rawfile.iter_rows(p, k, [1, 0], start=s)) for k, s in enumerate(starts)]
        self.assertEqual(got, [[(-float(k), float(k))] for k in range(200)])
        self.assertLessEqual(calls["plots"], 2 * 200)                   # was 19900: every earlier plot per plot
        self.assertEqual(list(rawfile.iter_rows(p, 7, [0])), [(7.0,)])  # by number, as before
        with self.assertRaises(rawfile.RawError):
            list(rawfile.iter_rows(p, 1, [0], start=starts[1] - 4))       # inside plot 0's data: not a header
        with self.assertRaises(rawfile.RawError):
            list(rawfile.iter_rows(p, 1, [0], start=os.path.getsize(p)))

    def test_iter_rows_starts_at_a_first_row(self):
        p = os.path.join(self.tmp, "groups.raw")
        write_binary_raw(p, 2, rows=5000)
        self.assertEqual(list(itertools.islice(rawfile.iter_rows(p, 0, [0], first=4000), 3)), [(4.0,), (4.001,), (4.002,)])
        start = rawfile.scan(p)[0].next_start
        self.assertEqual(list(rawfile.iter_rows(p, 1, [0, 1], start=start, first=4999)), [(1.0 + 4.999, -1.0)])
        self.assertEqual(list(rawfile.iter_rows(p, 0, [0], first=5000)), [])
        # ASCII: the same rows, the earlier ones skipped by the token walk
        a = os.path.join(self.tmp, "groups.txt")
        with open(a, "w") as fh:
            fh.write("Title: t\nPlotname: p\nFlags: real\nNo. Variables: 2\nNo. Points: 4\nVariables:\n\t0\ttime\ttime\n"
                     "\t1\tout\tvoltage\nValues:\n" + "".join(" %d\t%d.0\n\t%d.5\n" % (i, i, i) for i in range(4)))
        self.assertEqual(list(rawfile.iter_rows(a, 0, [1, 0], first=2)), [(2.5, 2.0), (3.5, 3.0)])

    def test_the_conversion_reads_each_xyce_plot_once(self):
        """Every .STEP plot of a 300-point Xyce op sweep through results.collect: at most 2n plot scans (45150
        before)."""
        from vamos.spectre import results
        n = 300
        p = os.path.join(self.tmp, "vamos_a1.raw")
        write_binary_raw(p, n, names=("V(OUT)",), first=lambda k, i: 0.5 * k)
        st = step("vamos_a1", "op", "op1", options=OPTIONS,
                  context=[level(("variable", "g", ""), values_spec(*range(n)), "vamos_w1", "sw", continuation=0)])
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o"), analysis(st)], engine="xyce")
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V")]})
        er = results.EngineResult(rc=0, status={"vamos_a1": "ok"}, files={"vamos_a1": p},
                                  names={"vamos_a1": {("v", "out"): "V(OUT)"}})
        calls, patch = self.counting_scans()
        with patch:
            leaves = [r for r in results.collect(pl, sm, er, "modern") if r.atype == "dc"]
        self.assertEqual([r.signals[0].values[0] for r in leaves], [0.5 * k for k in range(n)])
        self.assertLessEqual(calls["plots"], 2 * n)

    def test_the_conversion_reads_each_vacask_group_once(self):
        """A 100-leaf tran sweep in one VACASK plot (50 rows a leaf) through results.collect: reading every leaf
        yields as many rows from rawfile.iter_rows as the plot holds (252500 before: each leaf from row 0)."""
        from vamos.spectre import results
        groups, per = 100, 50
        p = os.path.join(self.tmp, "vamos_a1.raw")
        write_binary_raw(p, 1, rows=groups * per, names=("vamos_w1", "time", "out"))
        with open(p, "r+b") as fh:                       # time restarts each group, the sweep column holds the group
            data = bytearray(fh.read())
            start = data.index(b"Binary:\n") + len(b"Binary:\n")
            for i in range(groups * per):
                struct.pack_into("<3d", data, start + 24 * i, float(i // per), (i % per) * 1e-9, float(i // per))
            fh.seek(0)
            fh.write(data)
        st = step("vamos_a1", "tran", "tr", options=OPTIONS, args={"step": 1e-9, "stop": (per - 1) * 1e-9},
                  context=[level(("variable", "g", ""), values_spec(*range(groups)), "vamos_w1", "sw", continuation=0)])
        pl = plan_of([P.Action("options", dict(OPTIONS), None, "o"), analysis(st)])
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V")]})
        er = results.EngineResult(rc=0, status={"vamos_a1": "ok"}, files={"vamos_a1": p}, names={"vamos_a1": {("v", "out"): "out"}})
        produced = {"rows": 0}
        real = rawfile.iter_rows

        def counting(*args, **kw):
            for row in real(*args, **kw):
                produced["rows"] += 1
                yield row
        with mock.patch.object(rawfile, "iter_rows", counting):
            leaves = [r for r in results.collect(pl, sm, er, "modern") if r.atype == "tran"]
            produced["rows"] = 0                         # the group bounds pass (one pass over time) is done
            outs = [[v[0] for _x, v in r.rows()] for r in leaves]
        self.assertEqual([o[0] for o in outs], [float(g) for g in range(groups)])
        self.assertEqual(sum(len(o) for o in outs), groups * per)
        self.assertLessEqual(produced["rows"], groups * per)


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

    def test_bsim4_group_nf_runs_as_the_total_w_entry(self):
        """§3.9, §14 q.8: m1 (group nch, w=2u nf=4) runs as entry 2: its drain current equals mr2's (the entry-2
        card, -6.893e-5 A measured 2026-10-16) and not mr1's (entry 1, which HSPICE's per-finger rule selects)."""
        nl = bsim4_group_netlist()
        pl, st, sm = op_plan(nl, BSIM4_REFS)
        names = {}
        d, _ = self.vacask(vacask.render(nl, plan=pl, sigmap=sm, names=names), "bsim4")
        p = _plot(os.path.join(d, "vamos_a1.raw"))
        im1, imr1, imr2 = (p.column(names["vamos_a1"][ref])[0] for ref, _n, _t in BSIM4_REFS)
        self.assertLess(im1, -1e-6)
        self.assertAlmostEqual(im1, imr2, delta=abs(imr2) * 1e-9)
        self.assertGreater(abs(imr1 - imr2), 0.5 * abs(imr2))
        self.assertAlmostEqual(im1, -6.893e-05, delta=1e-7)

    def test_noise_and_xf_ports_inside_a_subckt_run(self):
        """The hand-built noise (input I2.vb, output I2.Mid), acxf (I2.Mid against out) and noises out of
        '007' and 'net4<0>' each write their points; today the quoted in=/out= strings abort every one, rc 0
        under `abort except analysis`, leaving header-only rawfiles under strictoutput=0 (seam H-2: vacask.py)."""
        d, out = self.vacask(vacask.render(hier_noise_netlist(), plan=hier_noise_plan(), sigmap=sigmap({}, [])), "hier")
        self.assertNotIn("not found", out)
        for sid in ("vamos_a1", "vamos_a2", "vamos_a3", "vamos_a4"):
            path = os.path.join(d, sid + ".raw")
            self.assertTrue(os.path.exists(path) and _plot(path).points, "%s aborted:\n%s" % (sid, out[-1500:]))
        self.assertGreater(_plot(os.path.join(d, "vamos_a1.raw")).column("onoise")[0].real, 0.0)


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

    def test_bsim4_group_nf_runs_as_the_total_w_entry(self):
        """§3.9, §4.5 item 3: m1 bound to nch.2 (w=2u nf=4, the total w) equals mr2 (the entry-2 card) and not
        mr1; the deck's output has no problem the output scan reports (BSIM4's own warnings only)."""
        nl = bsim4_group_netlist()
        names = {}
        d, out = self.xyce(xyce.render(nl, step=step("vamos_a1", "op", options=OPTIONS), sigmap=sigmap({"vamos_a1": BSIM4_REFS}),
                                       names=names), "bsim4", "vamos_a1.cir")
        self.assertEqual(xyce.output_problems(out, nl), [])
        p = _plot(os.path.join(d, "vamos_a1.raw"))
        im1, imr1, imr2 = (p.column(names["vamos_a1"][ref])[0] for ref, _n, _t in BSIM4_REFS)
        self.assertLess(im1, -1e-6)
        self.assertAlmostEqual(im1, imr2, delta=abs(imr2) * 1e-9)
        self.assertGreater(abs(imr1 - imr2), 0.5 * abs(imr2))
        self.assertAlmostEqual(im1, -6.893e-05, delta=1e-7)

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

    def test_bsim4_group_nf_agrees(self):
        """The bsim4 group with nf=4 on both engines (sp_bsim4v8 and Xyce's BSIM4 are Berkeley's code): the
        three drain currents agree to 1e-6 (1e-11 measured 2026-10-16)."""
        nl = bsim4_group_netlist()
        pl, st, sm = op_plan(nl, BSIM4_REFS)
        names_v, names_x = {}, {}
        d, _ = self.vacask(vacask.render(nl, plan=pl, sigmap=sm, names=names_v), "v4")
        pv = _plot(os.path.join(d, "vamos_a1.raw"))
        d, _ = self.xyce(xyce.render(nl, step=st, sigmap=sm, names=names_x), "x4", "vamos_a1.cir")
        px = _plot(os.path.join(d, "vamos_a1.raw"))
        for ref, _n, _t in BSIM4_REFS:
            a, b = pv.column(names_v["vamos_a1"][ref])[0], px.column(names_x["vamos_a1"][ref])[0]
            self.assertAlmostEqual(a, b, delta=abs(b) * 1e-6, msg=ref)


# =============================================================================================
# phase 2c: the review's decks through bin/vamos (WSL)
# =============================================================================================

GAINS_SCS = """// X-emit 2c gains: every controlled source's gain read from a parameter, each swept from its nominal (E126)
simulator lang=spectre
parameters g=0.5 gm=1m sg=0.5 fg=2 rm=500 vdd=1
subckt amp (i o)
  parameters k=1
  E1 (o 0 i 0) vcvs gain=k
ends amp
subckt half (p q)
  parameters h=vdd/2
  E1 (q 0 p 0) vcvs gain=h
ends half
V2 (p2 0) vsource dc=vdd
X2 (p2 o6) half
R7 (o6 0) resistor r=1k
V1 (a 0) vsource dc=1
E1 (o1 0 a 0) vcvs gain=g
R2 (o1 0) resistor r=1k
G1 (0 o2 a 0) vccs gm=gm m=2
R3 (o2 0) resistor r=1k
X1 (a o5) amp k=sg
R5 (o5 0) resistor r=1k
Vs (a s) vsource dc=0
Rs (s 0) resistor r=1k
H1 (o3 0) ccvs rm=rm probe=Vs
R6 (o3 0) resistor r=1k
F1 (0 o4) cccs gain=fg probe=Vs
R4 (o4 0) resistor r=1k
swg dc param=g values=[0.5 0.75 2]
swgm dc param=gm values=[1m 2m]
sws dc param=sg values=[0.5 0.75]
swrm dc param=rm values=[500 1k]
swfg dc param=fg values=[2 3]
swlin dc param=g start=0.5 stop=1.5 step=0.5
swv dc param=vdd values=[1 2 4]
blk sweep param=g values=[0.5 0.75] {
  op1 dc
  tr tran stop=1u
}
blkgm sweep param=gm values=[1m 2m] {
  op2 dc
}
save o1 o2 o3 o4 o5 o6
"""
# the analytic values: o1 = g, o2 = gm*m*1k, o5 = sg, o3 = rm*1 mA, o4 = fg*1 mA*1k, o6 = vdd/2*vdd (the subckt
# default h=vdd/2 reads the swept parameter: R-plan-5's subdefault deck)
GAINS_EXPECT = {"swg": ("o1", [0.5, 0.75, 2.0]), "swgm": ("o2", [2.0, 4.0]), "sws": ("o5", [0.5, 0.75]),
                "swrm": ("o3", [0.5, 1.0]), "swfg": ("o4", [2.0, 3.0]), "swlin": ("o1", [0.5, 1.0, 1.5]),
                "swv": ("o6", [0.5, 2.0, 8.0])}

GAINSDEV_SCS = """// X-emit 2c gainsdev: dev= sweeps of each controlled source's gain and multiplier, from the nominal
simulator lang=spectre
V1 (a 0) vsource dc=1
E1 (o1 0 a 0) vcvs gain=0.5
R2 (o1 0) resistor r=1k
G1 (0 o2 a 0) vccs gm=1m m=2
R3 (o2 0) resistor r=1k
Vs (a s) vsource dc=0
Rs (s 0) resistor r=1k
H1 (o3 0) ccvs rm=500 probe=Vs
R6 (o3 0) resistor r=1k
F1 (0 o4) cccs gain=2 probe=Vs m=2
R4 (o4 0) resistor r=1k
swe dc dev=E1 param=gain values=[0.5 1]
swg dc dev=G1 param=gm values=[1m 2m]
swgm dc dev=G1 param=m values=[2 4]
swh dc dev=H1 param=rm values=[500 1k]
swf dc dev=F1 param=gain values=[2 3]
swfm dc dev=F1 param=m values=[1 2 4]
save o1 o2 o3 o4
"""
GAINSDEV_EXPECT = {"swe": ("o1", [0.5, 1.0]), "swg": ("o2", [2.0, 4.0]), "swgm": ("o2", [2.0, 4.0]),
                   "swh": ("o3", [0.5, 1.0]), "swf": ("o4", [4.0, 6.0]), "swfm": ("o4", [2.0, 4.0, 8.0])}

MULT_SCS = """// X-emit 2c mult: sweeps of fields the multiplier is folded into (R-emit-7)
simulator lang=spectre
Ia (0 a) isource dc=1m m=2
Ra (a 0) resistor r=1k
Ib (0 b) isource type=pulse val0=1m val1=2m delay=1u rise=1u fall=1u width=1u m=3
Rb (b 0) resistor r=1k
subckt isub (p n)
  Iq (n p) isource dc=1m
ends isub
Xc (c 0) isub m=2
Rc (c 0) resistor r=1k
V1 (d 0) vsource dc=1
J1 (d 0 0) jm area=1 m=2
model jm jfet type=n vto=-2 beta=1e-4
op0 dc
da dc dev=Ia param=dc values=[1m 2m]
db dc dev=Ib param=val0 values=[1m 3m]
dc1 dc dev=Xc.Iq param=dc values=[1m 2m]
dj dc dev=J1 param=area values=[1 2]
dim dc dev=Ia param=m values=[1 2 4]
save a b c V1:p
"""
# J1 in its linear region (vgs - vto = 2 > vds = 1): id = beta*(2*2*1 - 1)*area*m = 0.3 mA*area*m
MULT_EXPECT = {"da": ("a", [2.0, 4.0]), "db": ("b", [3.0, 9.0]), "dc1": ("c", [2.0, 4.0]),
               "dj": ("V1:p", [-6e-4, -1.2e-3]), "dim": ("a", [1.0, 2.0, 4.0])}

MSUB_SCS = """// X-emit 2c msub: a dev= sweep of the m of a resistor inside a subckt instance with m=2
simulator lang=spectre
V1 (a 0) vsource dc=1
subckt cell (p n)
  R9 (p n) resistor r=1k
ends cell
X1 (a b) cell m=2
R2 (b 0) resistor r=1k
sw dc dev=X1.R9 param=m values=[1 2 4]
save b
"""

KFOLD_SCS = """// X-emit 2c kfold: a dev= sweep of a coupled inductor's l when its multiplier is folded into it
simulator lang=spectre
V1 (a 0) vsource dc=0 mag=1
R1 (a b) resistor r=1k
L1 (b 0) inductor l=1m m=2
L2 (c 0) inductor l=1m
R2 (c 0) resistor r=1k
K1 mutual_inductor ind1=L1 ind2=L2 coupling=0.5
ac0 ac values=[100k]
swl ac dev=L1 param=l values=[1m 2m] freq=100k
save b c
"""

M_SCS = """// X-emit 2c m: a dev= sweep and an alter of a resistor's multiplier (R-plan-4)
simulator lang=spectre
V1 (a 0) vsource dc=1
R1 (a b) resistor r=1k m=1
R2 (b 0) resistor r=1k
sw dc dev=R1 param=m values=[1 2 4]
op1 dc
am alter dev=R1 param=m value=4
op2 dc
save b
"""

VA_SCS = """// X-emit 2c va: a dev= sweep and an alter of a Verilog-A parameter, and a mod= sweep and alter of a card's
simulator lang=spectre
ahdl_include "vres2c.va"
V1 (a 0) vsource dc=1
X1 (a b) vres2c r=1k
R2 (b 0) resistor r=1k
model vm vres2c r=1k
X2 (a c) vm
R3 (c 0) resistor r=1k
sw1 dc dev=X1 param=r values=[1k 2k 3k]
sw2 dc mod=vm param=r values=[1k 3k]
op1 dc
al alter dev=X1 param=r value=3k
am alter mod=vm param=r value=2k
op2 dc
save b c
"""
VRES_VA = """`include "disciplines.vams"
module vres2c(a, b);
  inout a, b; electrical a, b;
  parameter real r = 2000.0;
  analog I(a, b) <+ V(a, b) / r;
endmodule
"""

NSY_SCS = """// X-emit 2c nsy: isnoisy through a netlist parameter and a subckt parameter (R-emit-6)
simulator lang=spectre
parameters nflag=0
V1 (in 0) vsource dc=0
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k isnoisy=nflag
X3 (out 0) qr nz=0
X4 (out 0) qr
subckt qr (a b)
  parameters nz=1
  R1 (a b) resistor r=1k isnoisy=nz
ends qr
n1 (out 0) noise start=1k stop=1k lin=1 iprobe=V1
save out
"""
# R1 and X4.R1 noisy, R2 and X3.R1 not, the four in parallel at out (250 ohm): sqrt(2 * 4kT/1k * 250^2) at 27 C
NSY_OUT = 1.43945e-09

NSYSW_SCS = """// X-emit 2c nsysw: isnoisy reading a parameter that a sweep block sweeps (R-emit-6)
simulator lang=spectre
parameters nflag=0
V1 (in 0) vsource dc=0
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k isnoisy=nflag
sw sweep param=nflag values=[0 1] {
  n1 (out 0) noise values=[1k] iprobe=V1
}
save out
"""

VANOISE_SCS = """// X-emit 2c vanoise: a noise analysis of a circuit holding a noisy Verilog-A resistor (R-emit-8)
simulator lang=spectre
ahdl_include "vnres2c.va"
V1 (a 0) vsource dc=1 mag=1
X1 (a b) vnres2c
R2 (b 0) resistor r=1k
op1 dc
n1 (b 0) noise start=1k stop=1k lin=1 iprobe=V1
save b
"""
VNRES_VA = """`include "disciplines.vams"
`include "constants.vams"
(*xyceModelGroup="Resistor", xyceSpiceDeviceName="R", xyceLevelNumber="6"*)
module vnres2c (p,n);
   electrical p,n;
   inout p,n;
   (*desc="Resistance", type="instance"*) parameter real R=1k from (0:inf);
   analog begin
      I(p,n) <+ V(p,n)/R;
      I(p,n) <+ white_noise(4.0*`P_K*$temperature/R, "thermal");
   end
endmodule
"""

MAG0_SCS = """// X-emit 2c mag0: sources with an explicit mag=0 beside the ac input (R-emit-12)
simulator lang=spectre
V1 (in 0) vsource dc=1 mag=1
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
I1 (0 out) isource dc=0 mag=0
V2 (x 0) vsource dc=0 mag=0
R3 (x 0) resistor r=1k
ac1 ac start=1k stop=1M dec=1
save out
"""

TOL_SCS = """// X-emit 2c tol: a deck that sets no option (D2)
simulator lang=spectre
V1 (in 0) vsource dc=1
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
op1 dc
sw dc dev=V1 param=dc values=[1 2]
save out
"""


class _Flow(TempDir):
    """bin/vamos -spectre runs of the review's decks in a scratch directory (X-emit 2c)."""

    def spectre(self, name, text, engine, files=None, rc=0):
        d = os.path.join(self.tmp, "%s_%s" % (name, engine))
        os.makedirs(d)
        with open(os.path.join(d, name + ".scs"), "w") as fh:
            fh.write(text)
        for fn, body in (files or {}).items():
            with open(os.path.join(d, fn), "w") as fh:
                fh.write(body)
        env = dict(os.environ)
        env.pop("VAMOS_ANALOG", None)
        r = run([LAUNCHER, "-spectre", "--vamos-analog=" + engine, name + ".scs"], cwd=d, env=env, timeout=900)
        if rc is not None:
            self.assertEqual(r.returncode, rc, "%s on %s:\n%s" % (name, engine, r.stdout[-4000:]))
        return d, r.stdout

    def psf(self, d, name, fn):
        import psfcheck
        return psfcheck.read(os.path.join(d, name + ".raw", fn))

    def check_swept(self, d, name, expect, rel=1e-6, engine=""):
        for analysis, (signal, want) in sorted(expect.items()):
            got = self.psf(d, name, analysis + ".dc").column(signal)
            self.assertEqual(len(got), len(want), (engine, analysis))
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, w, delta=abs(w) * rel + 1e-12, msg="%s %s %s: %s" % (engine, analysis, signal, got))


class TestFlowRuns(_Flow):
    """The review's decks through bin/vamos on the engines, against their analytic values."""

    @needs_vacask
    @needs_xyce
    def test_gains_swept_from_their_nominal(self):
        """R-plan-5, R-tests-1 [E126]: every gain read from a swept parameter is right at the first point, the
        nominal, on Xyce as on VACASK (Xyce gave o1 [0, 0.75, 2], o2 [0, 4] and the subckt's o5 [0, 0.75], rc 0);
        so are a start/stop/step dc and the op and tran children of a sweep block."""
        for engine in ("vacask", "xyce"):
            d, _out = self.spectre("gains", GAINS_SCS, engine)
            self.check_swept(d, "gains", GAINS_EXPECT, engine=engine)
            for k, g in ((0, 0.5), (1, 0.75)):
                self.assertAlmostEqual(self.psf(d, "gains", "blk-%03d_op1.dc" % k).value("o1"), g, delta=1e-9, msg=engine)
                tr = self.psf(d, "gains", "blk-%03d_tr.tran.tran" % k).column("o1")
                self.assertTrue(tr and all(abs(v - g) < 1e-9 for v in tr), (engine, k, tr[:4]))
            for k, o2 in ((0, 2.0), (1, 4.0)):                      # the vccs under a .STEP (R-plan-5's vccsgain)
                self.assertAlmostEqual(self.psf(d, "gains", "blkgm-%03d_op2.dc" % k).value("o2"), o2, delta=1e-9,
                                       msg=engine)

    @needs_vacask
    @needs_xyce
    def test_dev_sweeps_of_gains_and_multipliers(self):
        """R-plan-4 on VACASK (`parameter 'gm' of instance 'G1' not found', `'rm'', `'m''), R-plan-5/R-emit-7 on Xyce
        (`Function or variable E1:GAIN is not defined', and .DC F1:M multiplying the folded gain again)."""
        for engine in ("vacask", "xyce"):
            d, _out = self.spectre("gainsdev", GAINSDEV_SCS, engine)
            self.check_swept(d, "gainsdev", GAINSDEV_EXPECT, engine=engine)

    @needs_vacask
    @needs_xyce
    def test_multiplied_sources_keep_their_multiplier_under_a_sweep(self):
        """R-emit-7: Xyce gave da a [1, 2], db b [1, 3], dc1 c [1, 2] and dj [-0.3, -0.6] mA, half or a third of
        VACASK's; dim (an isource's m) was refused on both engines."""
        for engine in ("vacask", "xyce"):
            d, _out = self.spectre("mult", MULT_SCS, engine)
            self.assertAlmostEqual(self.psf(d, "mult", "op0.dc").value("a"), 2.0, delta=1e-9)
            self.check_swept(d, "mult", MULT_EXPECT, rel=1e-5, engine=engine)

    @needs_vacask
    @needs_xyce
    def test_the_m_of_an_instance_in_a_multiplied_subckt(self):
        """R-emit-7 / R-plan-4: Xyce's .DC X1:R9:M dropped X1's m=2 (b [0.5, 0.67, 0.8]); with the hook b = 2m/(2m+1).
        VACASK's $mfactor of R9 carries X1's multiplier too, so the sweep is refused there (status 2)."""
        d, _out = self.spectre("msub", MSUB_SCS, "xyce")
        self.check_swept(d, "msub", {"sw": ("b", [2.0 / 3.0, 0.8, 8.0 / 9.0])}, engine="xyce")
        _d, out = self.spectre("msub", MSUB_SCS, "vacask", rc=2)
        self.assertIn("X1.R9", out)

    @needs_vacask
    @needs_xyce
    def test_a_coupled_inductors_folded_l_under_a_sweep(self):
        """R-emit-7's folds: a sweep of a coupled inductor's l (m=2, folded into l) at its nominal gave b = 0.280+0.393j
        on both engines where the ac at the nominal gives 0.105+0.252j: Xyce sweeps the folded value through its hook
        now, VACASK refuses it (status 2)."""
        d, _out = self.spectre("kfold", KFOLD_SCS, "xyce")
        nominal = self.psf(d, "kfold", "ac0.ac").column("b")[0]
        swept = self.psf(d, "kfold", "swl.ac").column("b")
        self.assertAlmostEqual(abs(swept[0] - nominal), 0.0, delta=1e-9 * abs(nominal))
        self.assertGreater(abs(swept[1] - nominal), 0.01)
        _d, out = self.spectre("kfold", KFOLD_SCS, "vacask", rc=2)
        self.assertIn("L1: a coupled inductor's multiplier and TCs are folded into its l on VACASK", out)

    @needs_vacask
    def test_vacask_sweeps_and_alters_m_and_verilog_a_parameters(self):
        """R-plan-4: every dev=/mod= sweep or alter of m or of a Verilog-A parameter failed on VACASK (exit 1, or a
        fatal exit 2 at the alter)."""
        d, _out = self.spectre("m", M_SCS, "vacask")
        self.check_swept(d, "m", {"sw": ("b", [0.5, 2.0 / 3.0, 0.8])})
        self.assertAlmostEqual(self.psf(d, "m", "op2.dc").value("b"), 0.8, delta=1e-9)
        d, _out = self.spectre("va", VA_SCS, "vacask", files={"vres2c.va": VRES_VA})
        self.check_swept(d, "va", {"sw1": ("b", [0.5, 1.0 / 3.0, 0.25]), "sw2": ("c", [0.5, 0.25])})
        self.assertAlmostEqual(self.psf(d, "va", "op1.dc").value("b"), 0.5, delta=1e-9)
        self.assertAlmostEqual(self.psf(d, "va", "op2.dc").value("b"), 0.25, delta=1e-9)
        self.assertAlmostEqual(self.psf(d, "va", "op2.dc").value("c"), 1.0 / 3.0, delta=1e-9)

    @needs_vacask
    @needs_xyce
    def test_isnoisy_through_parameters(self):
        """R-emit-6: isnoisy=nflag (nflag=0) and isnoisy=nz (nz=0 on X3) were noisy on both engines, out 2.35e-9
        V/sqrt(Hz) for the three-resistor deck where R1 alone is noisy."""
        for engine in ("vacask", "xyce"):
            d, _out = self.spectre("nsy", NSY_SCS, engine)
            out = self.psf(d, "nsy", "n1.noise").column("out")[0]
            self.assertAlmostEqual(out, NSY_OUT, delta=NSY_OUT * 1e-3, msg=engine)

    @needs_vacask
    @needs_xyce
    def test_a_swept_noise_switch(self):
        """R-emit-6: VACASK's noisy=nflag follows a sweep of nflag (R2 noise-free at 0: out = sqrt(4kT/1k)*500 ohm,
        noisy at 1: sqrt(2) times that); Xyce's G form is fixed per deck, so the sweep is refused there (status 2)."""
        d, _out = self.spectre("nsysw", NSYSW_SCS, "vacask")
        one = math.sqrt(4 * 1.380649e-23 * 300.15 / 1000.0) * 500.0
        for k, want in ((0, one), (1, one * math.sqrt(2.0))):
            out = self.psf(d, "nsysw", "sw-%03d_n1.noise" % k).column("out")[0]
            self.assertAlmostEqual(out, want, delta=want * 1e-3, msg=k)
        _d, out = self.spectre("nsysw", NSYSW_SCS, "xyce", rc=2)
        self.assertIn("isnoisy=nflag of R2 reads a parameter that noise n1 sweeps", out)

    @needs_vacask
    @needs_xyce
    def test_noise_with_a_verilog_a_device_is_refused_on_xyce_only(self):
        """R-emit-8: on Xyce the noise deck's DNO of the Verilog-A instance stopped the run (status 2) after op1 ran;
        it is refused before any engine run now, naming the instance.  VACASK runs it (out 2.878896e-9 V/sqrt(Hz),
        the analytic value)."""
        files = {"vnres2c.va": VNRES_VA}
        d, out = self.spectre("vanoise", VANOISE_SCS, "xyce", files=files, rc=2)
        self.assertIn("noise n1: the Verilog-A instance X1 (module vnres2c)", out)
        self.assertFalse(os.path.exists(os.path.join(d, "vanoise.raw", "op1.dc")), "an engine ran before the refusal")
        d, _out = self.spectre("vanoise", VANOISE_SCS, "vacask", files=files)
        self.assertAlmostEqual(self.psf(d, "vanoise", "n1.noise").column("out")[0], 2.878896e-09, delta=3e-13)

    @needs_xyce
    def test_a_zero_mag_source_gives_no_xyce_warning(self):
        """R-emit-12: `Device instance I1: AC magnitude is set to 0.0' was counted as a Spectre warning (two here)."""
        _d, out = self.spectre("mag0", MAG0_SCS, "xyce")
        self.assertNotIn("AC magnitude", out)
        self.assertIn("spectre completes with 0 errors, 0 warnings", out)

    @needs_xyce
    def test_a_deck_that_sets_no_option_gets_no_tolerance_notice(self):
        """D2 (R-emit-15): every Xyce run printed three `option ... is not mapped on Xyce' notices from plan.build's
        defaults."""
        _d, out = self.spectre("tol", TOL_SCS, "xyce")
        self.assertNotIn("is not mapped on Xyce", out)
        self.assertIn("spectre completes with 0 errors, 0 warnings, and 0 notices", out)

    @needs_xyce
    def test_an_option_the_netlist_sets_gets_its_notice(self):
        """D2: an options statement's reltol is noted once (the option the netlist set; plan.build's
        AnalysisStep.user_options marks it), and a tran that sets lteratio gets the method note once."""
        _d, out = self.spectre("tol2", TOL_SCS.replace("op1 dc", "o1 options reltol=1e-4\nop1 dc"), "xyce")
        self.assertEqual(out.count("option reltol=0.0001 is not mapped on Xyce"), 1, out[-3000:])
        self.assertNotIn("option vabstol", out)
        _d, out = self.spectre("tol3", TOL_SCS.replace("op1 dc", "tr1 tran stop=1u lteratio=10\nop1 dc"), "xyce")
        self.assertEqual(out.count("Xyce takes the method"), 1, out[-3000:])
        self.assertNotIn("is not mapped on Xyce", out)


# -- phase 2c's integration: the seams on the engines (INTEGRATE1) -------------------------------------------

DESC_SCS = """// 2c integration: descending grids (R-emit-3): a dc's own dec and lin, a sweep block's dec, and
// descending ac grids under dec, lin and step
simulator lang=spectre
parameters rv=1k
I1 (0 out) isource dc=1m
R1 (out 0) resistor r=rv
V2 (in 0) vsource dc=0 mag=1
R2 (in o2) resistor r=1k
C2 (o2 0) capacitor c=1n
d4 dc dev=I1 param=dc start=1m stop=1u dec=1
dl dc dev=I1 param=dc start=1m stop=0 lin=4
swd sweep param=rv start=10k stop=100 dec=1 {
  op1 dc
}
acd ac start=1M stop=1k dec=1
acl ac start=1M stop=1k lin=3
acs ac start=1M stop=1k step=-333k
save out o2
"""

SPOT_SCS = """// 2c integration: a spot noise and a spot ac (start = stop) inside a sweep block (R-emit-4)
simulator lang=spectre
parameters rv=1k
V1 (in 0) vsource dc=0 mag=1
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
sw sweep param=rv values=[1k 2k] {
  n1 (out 0) noise start=1k stop=1k lin=1 iprobe=V1
  a1 ac start=1k stop=1k lin=1
}
save out
"""

GAIN_SCS = """// 2c integration: the noise gain on Xyce (R-emit-13): no noisy device on the output (nan before), no path from the
// input probe (1e-10 before), and a divider
simulator lang=spectre
V1 (in 0) vsource dc=0
Rq (in out) resistor r=1k isnoisy=no
Rs (out 0) resistor r=1k isnoisy=no
V2 (i2 0) vsource dc=0
R5 (i2 0) resistor r=1k
R6 (o5 0) resistor r=1k
R7 (in o7) resistor r=1k
R8 (o7 0) resistor r=1k
nq (out 0) noise start=1k stop=1k lin=1 iprobe=V1
n5 (o5 0) noise start=1k stop=1k lin=1 iprobe=V2
nd (o7 0) noise start=1k stop=1k lin=1 iprobe=V1
save out o5 o7
"""

TNOM_SCS = """// 2c integration: tnom in expressions (R-lang-7; UG p.93's example, ref19 p.507): a value, a netlist parameter
// and a user function reading it, at temp=55 and tnom=20
simulator lang=spectre
model res resistor
real tk(real x) { return x+temp-tnom; }
parameters rt=1k*(1+0.01*(temp-tnom))
v1 (n1 0) vsource dc=1
r1 (n1 0) res r=(temp-tnom)*15+10k
v2 (n2 0) vsource dc=1
r2 (n2 0) resistor r=rt
v3 (n3 0) vsource dc=1
r3 (n3 0) resistor r=1k+tk(0)
o1 options temp=55 tnom=20
op1 dc
"""


def _rc_lowpass(f):
    return 1.0 / complex(1.0, 2 * math.pi * f * 1e-6)


class TestSeams2cRuns(_Flow):
    """The seams the phase-2c integration applied, through bin/vamos on both engines (WSL)."""

    @needs_vacask
    @needs_xyce
    def test_descending_grids_run_the_plans_points(self):
        """R-emit-3: Xyce ran one point of a descending dec (`DEC with start > stop computes one step') and refused a
        descending ac (`End frequency must not be less than start frequency', rc 1); render prints the plan's points
        (LIST, DATA=), as VACASK runs them."""
        for engine in ("vacask", "xyce"):
            d, out = self.spectre("desc", DESC_SCS, engine)
            self.check_swept(d, "desc", {"d4": ("out", [1.0, 0.1, 0.01, 0.001]),
                                         "dl": ("out", [1.0, 0.75, 0.5, 0.25, 0.0])}, engine=engine)
            for k, v in enumerate((10.0, 1.0, 0.1)):
                self.assertAlmostEqual(self.psf(d, "desc", "swd-%03d_op1.dc" % k).value("out"), v, delta=v * 1e-9)
            for name, freqs in (("acd", [1e6, 1e5, 1e4, 1e3]), ("acl", [1e6, 667e3, 334e3, 1e3]),
                                ("acs", [1e6, 667e3, 334e3, 1e3])):
                f = self.psf(d, "desc", name + ".ac")
                got = [x.real if isinstance(x, complex) else x for x in f.sweep_values()]
                self.assertEqual(len(got), 4, (engine, name, got))
                for g, w in zip(got, freqs):
                    self.assertAlmostEqual(g, w, delta=w * 1e-9, msg=(engine, name, got))
                for g, w in zip(f.column("o2"), freqs):
                    self.assertAlmostEqual(abs(g - _rc_lowpass(w)), 0.0, delta=1e-6, msg=(engine, name))

    @needs_vacask
    @needs_xyce
    def test_spot_frequency_grids_inside_a_sweep_block(self):
        """R-emit-4: VACASK's spot noise and spot ac (two rows at 1 kHz per point) were split at every frequency that
        did not increase (`its rawfile holds 4 of the 2 planned sweep groups', rc 1); both engines write the four
        leaves now."""
        for engine in ("vacask", "xyce"):
            d, _out = self.spectre("spot", SPOT_SCS, engine)
            for k, g in ((0, 0.5), (1, 1.0 / 3.0)):
                ac = self.psf(d, "spot", "sw-%03d_a1.ac" % k)
                self.assertEqual(len(ac.sweep_values()), 2, engine)
                self.assertTrue(all(abs(v - g) < 1e-6 for v in ac.column("out")), (engine, k, ac.column("out")))
                nz = self.psf(d, "spot", "sw-%03d_n1.noise" % k)
                self.assertTrue(all(abs(v - g) < 1e-6 for v in nz.column("gain")), (engine, k, nz.column("gain")))

    @needs_vacask
    @needs_xyce
    def test_the_noise_gain_on_xyce_is_the_outputs_magnitude(self):
        """R-emit-13: Xyce's gain was sqrt(ONOISE/INOISE): nan with no noisy device on the output (nq), 1e-10 and in =
        40.7 V/sqrt(Hz) with no path from the input probe (n5); it is VM(<output>) now, VACASK's values."""
        got = {}
        for engine in ("vacask", "xyce"):
            d, _out = self.spectre("gain", GAIN_SCS, engine)
            got[engine] = {n: self.psf(d, "gain", n + ".noise") for n in ("nq", "n5", "nd")}
            nq, n5, nd = got[engine]["nq"], got[engine]["n5"], got[engine]["nd"]
            self.assertAlmostEqual(nq.column("gain")[0], 0.5, delta=1e-9, msg=engine)
            self.assertEqual(nq.column("out")[0], 0.0, engine)
            self.assertEqual(nq.column("in")[0], 0.0, engine)
            self.assertEqual(n5.column("gain")[0], 0.0, engine)
            self.assertTrue(math.isnan(n5.column("in")[0]), (engine, n5.column("in")))
            self.assertAlmostEqual(nd.column("gain")[0], 0.5, delta=1e-9, msg=engine)
            self.assertAlmostEqual(nd.column("in")[0], nd.column("out")[0] / 0.5, delta=1e-15, msg=engine)
        self.assertAlmostEqual(got["xyce"]["nd"].column("out")[0], got["vacask"]["nd"].column("out")[0],
                               delta=got["vacask"]["nd"].column("out")[0] * 1e-4)

    @needs_vacask
    @needs_xyce
    def test_tnom_in_expressions_on_both_engines(self):
        """R-lang-7: every expression reading tnom stopped Xyce (`tnom has no xyce form in an expression', status 2):
        Xyce reads the deck's tnom now, VACASK its $tnom, so r1 = (55-20)*15+10k, rt = 1350 and tk(0) = 35."""
        for engine in ("vacask", "xyce"):
            d, _out = self.spectre("tnom", TNOM_SCS, engine)
            op = self.psf(d, "tnom", "op1.dc")
            for src, r in (("v1:p", 10525.0), ("v2:p", 1350.0), ("v3:p", 1035.0)):
                self.assertAlmostEqual(op.value(src), -1.0 / r, delta=1e-9 / r, msg=(engine, src))

    @needs_vacask
    @needs_xyce
    def test_isnoisy_parameters_leave_the_noise_structs(self):
        """R-emit-6's signals seam: R2 (isnoisy=nflag, nflag=0) and X3.R1 (isnoisy=nz, nz=0) were listed as noise STRUCTs,
        with no column on Xyce (`has no column') and zero contributions on VACASK, and Xyce refused the saved R2:1 (`the
        current of the noise-free G form of R2 needs a probe', status 2); the STRUCTs are R1 and X4.R1 now, R2:1 read
        through a probe."""
        nsy = NSY_SCS.replace("n1 (out 0) noise", "op1 dc\nn1 (out 0) noise").replace("save out\n", "save out R2:1\n")
        for engine in ("vacask", "xyce"):
            d, out = self.spectre("nsyp", nsy, engine)
            self.assertNotIn("has no column", out)
            nz = self.psf(d, "nsyp", "n1.noise")
            self.assertEqual([n for n in nz.names() if n not in ("out", "gain", "in")], ["R1", "X4.R1"], engine)
            self.assertAlmostEqual(nz.column("out")[0], NSY_OUT, delta=NSY_OUT * 1e-3, msg=engine)
            self.assertEqual(self.psf(d, "nsyp", "op1.dc").value("R2:1"), 0.0, engine)


@needs_xyce
class TestXyceFacts(_Engines):
    """The Xyce quirk E126 pins on this build (§13): the linear E/G form's gain is 0 at a first .DC/.STEP point equal
    to the .param's nominal; the behavioral VALUE= form vamos prints is right there (X-emit 2c)."""

    DECK = """* E126: linear and VALUE= E/G forms under .DC of a .param from its nominal
.param g=0.5
V1 a 0 DC 1
Elin o1 0 a 0 {g}
R1 o1 0 1000.0
Eval o2 0 VALUE={g*v(a)}
R2 o2 0 1000.0
Glin 0 o3 a 0 {g*0.001}
R3 o3 0 1000.0
Gval 0 o4 VALUE={g*0.001*v(a)}
R4 o4 0 1000.0
.DC g LIST 0.5 1.0
.PRINT DC FORMAT=RAW FILE=e126.raw V(O1) V(O2) V(O3) V(O4)
.end
"""

    def test_e126_linear_gain_is_zero_at_the_nominal_first_point(self):
        d, _out = self.xyce(self.DECK, "e126", "e126.cir")
        p = _plot(os.path.join(d, "e126.raw"))
        self.assertEqual([round(v, 9) for v in p.column("V(O1)")], [0.0, 1.0])     # the quirk
        self.assertEqual([round(v, 9) for v in p.column("V(O3)")], [0.0, 1.0])
        self.assertEqual([round(v, 9) for v in p.column("V(O2)")], [0.5, 1.0])     # the form vamos prints
        self.assertEqual([round(v, 9) for v in p.column("V(O4)")], [0.5, 1.0])


# -- phase 2c2: the four follow-up items through bin/vamos on both engines --------------------------------------

ZERO_HEAD = """// 2c2 F1: a divider whose upper resistance a sweep or alter can drive to 0 (Spectre's short gives out = 1 V)
simulator lang=spectre
parameters rv=1k
V1 (in 0) vsource dc=1
R1 (in out) resistor r=%s
R2 (out 0) resistor r=1k
save out
"""
NZ_DECK = """// 2c2 F2 (R-plan-19): the divider's noise, an alter of R1's isnoisy, the noise again
simulator lang=spectre
V1 (in 0) vsource dc=0
R1 (in out) resistor r=1k%s
R2 (out 0) resistor r=1k
save R1:1 out
nz1 (out 0) noise iprobe=V1 start=1k stop=10k dec=1
%snz2 (out 0) noise iprobe=V1 start=1k stop=10k dec=1
op2 dc
"""
SHORT_DECK = """// 2c2 F2 (R-plan-19): an r=0 resistor (a short, sec. 4.5 item 10)
simulator lang=spectre
V1 (a 0) vsource dc=1
R1 (a b) resistor r=%s
R2 (b 0) resistor r=1k
save R1:1 b
%s
"""
BOUND_DECK = """// 2c2 F3: controlled sources, a coupling, an area and a source dc, bound to parameters or constants
simulator lang=spectre
parameters g=2 vv=1
V1 (in 0) vsource dc=vv
E1 (out 0 in 0) vcvs gain=g
R1 (out 0) resistor r=1k
Vc (c 0) vsource dc=1
Vs (c s1) vsource dc=0
Rs1 (s1 0) resistor r=1k
G1 (0 o1 c 0) vccs gm=1m
Ro1 (o1 0) resistor r=1k
H1 (o2 0) ccvs rm=1k probe=Vs
F1 (0 o4) cccs gain=2 probe=Vs
Ro4 (o4 0) resistor r=1k
Iac (0 p) isource dc=0 mag=1m
L1 (p 0) inductor l=1m
L2 (s 0) inductor l=9m
K1 mutual_inductor coupling=0.5 ind1=L1 ind2=L2
Vd (dd 0) vsource dc=1
Rd (dd a) resistor r=1k
D1 (a 0) dmod area=%s
model dmod diode is=1e-14
save out o1 o2 o4 a s
%s
"""
VARES_LIB_VA = """`include "disciplines.vams"
`include "../inc/rdefs.include"
(*xyceModelGroup="Resistor", xyceSpiceDeviceName="R", xyceLevelNumber="6"*)
module vares (p,n);
   electrical p,n;
   inout p,n;
   (*desc="Resistance", type="instance"*) parameter real R=`RDEF from (0:inf);
   analog I(p,n) <+ V(p,n)/R;
endmodule
"""
VRES_LIB_VA = """`include "disciplines.vams"
`include "../inc/rdefs.include"
module vres(a, b);
  inout a, b; electrical a, b;
  parameter real r = `RDEF;
  analog I(a, b) <+ V(a, b) / r;
endmodule
"""
TWOLIB_SCS = """// 2c2 F4: two Verilog-A modules of one library sharing ../inc: b = 0.25 (R=3k), c = 0.2 (the include's r = 4k)
simulator lang=spectre
ahdl_include "lib/vares.va"
ahdl_include "lib/vres.va"
V1 (a 0) vsource dc=1
X1 (a b) vares R=3k
R2 (b 0) resistor r=1k
X2 (a c) vres
R3 (c 0) resistor r=1k
save b c
op1 dc
"""


class TestFollowUpRuns2c2(_Flow):
    """Phase 2c2 through bin/vamos: F1 status 2 for a resistance a sweep or alter drives to 0 (Xyce gave out = 0 at
    rv = 0, exit 0; VACASK NaN, status 1); F2 the alter of isnoisy against the unaltered reference runs, the short's
    parameters; F3 VACASK's status 2 before the run for a bound value parameter, Xyce unchanged, the alters of the
    value parameters on Xyce (status 2 before); F4 two modules of one library on Xyce, against VACASK."""

    def run2c2(self, name, text, engine, files=None, rc=0):
        d = os.path.join(self.tmp, "%s_%s" % (name, engine))
        for fn, body in dict(files or {}, **{name + ".scs": text}).items():
            p = os.path.join(d, fn)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write(body)
        env = dict(os.environ)
        env.pop("VAMOS_ANALOG", None)
        r = run([LAUNCHER, "-spectre", "--vamos-analog=" + engine, name + ".scs"], cwd=d, env=env, timeout=900)
        if rc is not None:
            self.assertEqual(r.returncode, rc, "%s on %s:\n%s" % (name, engine, r.stdout[-4000:]))
        return d, r.stdout

    def refused(self, name, text, engine, *pieces):
        """Status 2 before any engine ran: no data file, an error in initial setup holding every piece."""
        d, out = self.run2c2(name, text, engine, rc=2)
        self.assertIn("Error from spectre during initial setup.", out)
        flat = " ".join(out.split())
        for piece in pieces:
            self.assertIn(piece, flat, "%s on %s:\n%s" % (name, engine, out[-3000:]))
        res = os.path.join(d, name + ".raw")
        self.assertFalse(os.path.isdir(res) and any(f != "logFile" for f in os.listdir(res)), out)
        return out

    @needs_vacask
    @needs_xyce
    def test_f1_a_resistance_driven_to_zero_is_refused_before_the_run(self):
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                self.refused("zsweep", ZERO_HEAD % "rv" + "sw1 sweep param=rv stop=3k step=1k {\n  op1 dc\n}\n", engine,
                             "dc op1 at rv=0 (sweep sw1): resistor R1 on path top level: r=rv evaluates to 0",
                             "a non-constant r that evaluates to 0 is an error in v1")
                self.refused("zdev", ZERO_HEAD % "1k" + "dc1 dc dev=R1 param=r start=0 stop=2k step=1k\n", engine,
                             "dc dc1 at R1:r=0: resistor R1 on path top level: its r, set by a sweep or alter, is 0")
                self.refused("zalter", ZERO_HEAD % "rv" + "op1 dc\na1 alter param=rv value=0\nop2 dc\n", engine,
                             "dc op2 (after alter a1): resistor R1 on path top level: r=rv evaluates to 0")
                # what stays: the static short (out = 1 V) and a sweep from 1k
                d, _out = self.run2c2("zstatic", ZERO_HEAD % "0" + "sw1 sweep param=rv stop=3k step=1k {\n  op1 dc\n}\n",
                                      engine)
                for k in range(4):
                    self.assertAlmostEqual(self.psf(d, "zstatic", "sw1-%03d_op1.dc" % k).value("out"), 1.0, delta=1e-9)
                d, _out = self.run2c2("zfrom1k", ZERO_HEAD % "rv" + "dc1 dc param=rv start=1k stop=3k step=1k\n", engine)
                self.check_swept(d, "zfrom1k", {"dc1": ("out", [0.5, 1.0 / 3.0, 0.25])}, rel=1e-9, engine=engine)

    @needs_vacask
    @needs_xyce
    def test_f2_an_alter_of_isnoisy_equals_the_unaltered_reference(self):
        """isnoisy on to off and off to on: each noise analysis after the alter equals the run of a deck with the
        altered value from the start (out and the STRUCTs listed), on both engines; the saved current R1:1 too."""
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                d, _ = self.run2c2("off", NZ_DECK % ("", "a1 alter dev=R1 param=isnoisy value=no\n"), engine)
                ref, _ = self.run2c2("offref", NZ_DECK % (" isnoisy=no", ""), engine)
                on, _ = self.run2c2("on", NZ_DECK % (" isnoisy=no", "a1 alter dev=R1 param=isnoisy value=yes\n"), engine)
                onref, _ = self.run2c2("onref", NZ_DECK % ("", ""), engine)
                for got, gname, want, wname, nz, noisy in ((d, "off", ref, "offref", "nz2", ["R2"]),
                                                           (d, "off", onref, "onref", "nz1", ["R1", "R2"]),
                                                           (on, "on", onref, "onref", "nz2", ["R1", "R2"]),
                                                           (on, "on", ref, "offref", "nz1", ["R2"])):
                    g, w = self.psf(got, gname, nz + ".noise"), self.psf(want, wname, nz + ".noise")
                    self.assertEqual([n for n in g.names() if n.startswith("R")], noisy, (engine, gname, nz))
                    self.assertEqual([n for n in w.names() if n.startswith("R")], noisy, (engine, wname, nz))
                    for a, b in zip(g.column("out"), w.column("out")):
                        self.assertAlmostEqual(a, b, delta=1e-12 * abs(b), msg=(engine, gname, nz))
                    # each noisy 1k contributes 4kTR/4 = 4.144e-18 V^2/Hz at the divider's output (E8)
                    self.assertAlmostEqual(g.column("out")[0], math.sqrt(len(noisy) * 4.144e-18), delta=1e-12)
                for name, root in (("off", d), ("on", on)):
                    self.assertEqual(self.psf(root, name, "op2.dc").value("R1:1"), 0.0)
                dc = SHORT_DECK % ("1k", "op1 dc\na1 alter dev=R1 param=isnoisy value=0\nop2 dc")
                d, _ = self.run2c2("isnalt", dc, engine)
                for op in ("op1", "op2"):
                    self.assertAlmostEqual(self.psf(d, "isnalt", op + ".dc").value("R1:1"), 5e-4, delta=1e-12)
                    self.assertAlmostEqual(self.psf(d, "isnalt", op + ".dc").value("b"), 0.5, delta=1e-12)
                self.refused("isnswp", SHORT_DECK % ("1k", "sw1 sweep dev=R1 param=isnoisy values=[1 0] {\n  op1 dc\n}"),
                             engine, "a sweep of the isnoisy of R1 is not supported in v1")

    @needs_vacask
    @needs_xyce
    def test_f2_the_parameters_of_a_short(self):
        """VACASK stopped at the alter (`Parameter 'r' not found', status 2 after op1); Xyce's replay runs it and
        gives the reference's b = 0.5; a sweep of the short's r left b at 1 V on Xyce and failed on VACASK."""
        alt = SHORT_DECK % ("0", "op1 dc\na1 alter dev=R1 param=r value=1k\nop2 dc")
        self.refused("r0alt", alt, "vacask", "alter a1: R1 prints as a short",
                     "an alter of its r is not supported in v1 on VACASK (Xyce runs it)")
        d, _ = self.run2c2("r0alt", alt, "xyce")
        ref, _ = self.run2c2("r0ref", SHORT_DECK % ("1k", "op2 dc"), "xyce")
        for sig in ("b", "R1:1"):
            self.assertAlmostEqual(self.psf(d, "r0alt", "op2.dc").value(sig), self.psf(ref, "r0ref", "op2.dc").value(sig),
                                   delta=1e-12)
        self.assertAlmostEqual(self.psf(d, "r0alt", "op1.dc").value("b"), 1.0, delta=1e-12)
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                self.refused("r0swp", SHORT_DECK % ("0", "dc1 dc dev=R1 param=r start=1k stop=3k step=1k"), engine,
                             "dc dc1: R1 prints as a short", "a sweep of its r is not supported in v1")

    @needs_vacask
    @needs_xyce
    def test_f3_a_bound_value_parameter_and_the_value_parameters_alters(self):
        """VACASK: a dev= sweep of a gain or a source dc bound to a parameter is status 2 before the run (exit 1,
        `Failed to bind sweep parameters', before); Xyce sweeps them as before.  The alters of every value parameter
        (Xyce: status 2 at read-in before, `E1: parameter gain not supported on a E element') and a coupling's sweep
        (`Parameter 'coupling' not found' on VACASK, `K1:COUPLING is not defined' on Xyce) equal the reference."""
        sweep_gain = BOUND_DECK % ("1", "dc1 dc dev=E1 param=gain values=[1 2 3]")
        sweep_dc = BOUND_DECK % ("1", "dc1 dc dev=V1 param=dc values=[1 2]")
        # 2c2 refused both on VACASK before the run (status 2, `on VACASK gain of E1 is bound to the expression g');
        # phase 2c4 (H1) sweeps each one's switchable hook there, and VACASK gives Xyce's values
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine, construct="bound sweeps"):
                d, _ = self.run2c2("bgain", sweep_gain, engine)
                self.check_swept(d, "bgain", {"dc1": ("out", [1.0, 2.0, 3.0])}, rel=1e-9, engine=engine)
                d, _ = self.run2c2("bdc", sweep_dc, engine)
                self.check_swept(d, "bdc", {"dc1": ("out", [2.0, 4.0])}, rel=1e-9, engine=engine)
        alters = ("op1 dc\na1 alter dev=E1 param=gain value=3\na2 alter dev=G1 param=gm value=2m\n"
                  "a3 alter dev=H1 param=rm value=2k\na4 alter dev=F1 param=gain value=4\n"
                  "a5 alter dev=D1 param=area value=2\nop2 dc\nac1 ac start=1k stop=1k lin=1\n"
                  "a6 alter dev=K1 param=coupling value=0.25\nac2 ac start=1k stop=1k lin=1\n"
                  "ac3 ac dev=K1 param=coupling values=[0.5 0.25] freq=1k")
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                d, _ = self.run2c2("alters", BOUND_DECK % ("1", alters), engine)
                ref, _ = self.run2c2("altref", BOUND_DECK.replace("gain=g", "gain=3").replace("gm=1m", "gm=2m")
                                     .replace("rm=1k", "rm=2k").replace("gain=2 probe", "gain=4 probe")
                                     % ("2", "op2 dc"), engine)
                op2, want = self.psf(d, "alters", "op2.dc"), self.psf(ref, "altref", "op2.dc")
                for sig, value in (("out", 3.0), ("o1", 2.0), ("o2", 2.0), ("o4", 4.0)):
                    self.assertAlmostEqual(op2.value(sig), value, delta=1e-9, msg=(engine, sig))
                self.assertAlmostEqual(op2.value("a"), want.value("a"), delta=1e-12, msg=engine)
                self.assertLess(op2.value("a"), self.psf(d, "alters", "op1.dc").value("a") - 0.01)   # the area did act
                full = 2 * math.pi * 1e3 * 0.5 * math.sqrt(1e-3 * 9e-3) * 1e-3            # |jwM I| at k = 0.5
                self.assertAlmostEqual(abs(self.psf(d, "alters", "ac1.ac").column("s")[0]), full, delta=1e-9 * full)
                self.assertAlmostEqual(abs(self.psf(d, "alters", "ac2.ac").column("s")[0]), full / 2, delta=1e-9 * full)
                swept = [abs(v) for v in self.psf(d, "alters", "ac3.ac").column("s")]
                self.assertEqual(len(swept), 2)
                self.assertAlmostEqual(swept[0], full, delta=1e-9 * full)
                self.assertAlmostEqual(swept[1], full / 2, delta=1e-9 * full)

    @needs_vacask
    @needs_xyce
    def test_the_current_of_an_instance_whose_m_a_sweep_or_alter_sets(self):
        """R-plan-19's kind for m: VACASK's native current of R1 after an alter of its m to 2 was one unit's (1/3 mA)
        where Spectre's terminal current, and Xyce's, is the total, 2/3 mA; the probe measures the total."""
        deck = SHORT_DECK % ("1k", "op1 dc\na1 alter dev=R1 param=m value=2\nop2 dc\ndc1 dc dev=R1 param=m values=[1 3]")
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                d, _ = self.run2c2("malter", deck, engine)
                op2 = self.psf(d, "malter", "op2.dc")
                self.assertAlmostEqual(op2.value("b"), 2.0 / 3.0, delta=1e-9)
                self.assertAlmostEqual(op2.value("R1:1"), 2.0 / 3.0 * 1e-3, delta=1e-12)
                self.check_swept(d, "malter", {"dc1": ("R1:1", [0.5e-3, 0.75e-3])}, rel=1e-9, engine=engine)

    @needs_vacask
    @needs_xyce
    @needs_pyms
    def test_f4_two_modules_of_one_library_on_xyce_equal_vacask(self):
        """Xyce ended with status 2 ("Unrecognized parameter R for device RX1", "... A for device YVRES!X2"): both
        modules lost their parameters in run_dir/lib/; now b and c equal VACASK's (0.25 and 0.2 exactly)."""
        files = {"lib/vares.va": VARES_LIB_VA, "lib/vres.va": VRES_LIB_VA, "inc/rdefs.include": "`define RDEF 4000.0\n"}
        got = {}
        for engine in ("vacask", "xyce"):
            d, out = self.run2c2("twolib", TWOLIB_SCS, engine, files=files)
            self.assertNotIn("Unrecognized parameter", out)
            got[engine] = {s: self.psf(d, "twolib", "op1.dc").value(s) for s in ("b", "c")}
        for s, value in (("b", 0.25), ("c", 0.2)):
            self.assertAlmostEqual(got["vacask"][s], value, delta=1e-12, msg=s)
            self.assertAlmostEqual(got["xyce"][s], got["vacask"][s], delta=1e-8, msg=s)


# -- phase 2c3: G1 and G3 through bin/vamos on both engines ------------------------------------------------------------

ALTTHEN_HEAD = """// 2c3 G1: R1's r is bound to rv; out = 1k/(R1+1k)
simulator lang=spectre
parameters rv=1k other=1
V1 (in 0) vsource dc=1
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
"""
P_ALTSW = """// 2c3 G1 (2c2 skeptic's p_altsw): an alter of the bound gain, then a sweep of what it reads
simulator lang=spectre
parameters g=2
V1 (in 0) vsource dc=1
E1 (out 0 in 0) vcvs gain=g
R1 (out 0) resistor r=1k
save out
op1 dc
a1 alter dev=E1 param=gain value=3
op2 dc
%s
"""
ALTER_TARGETS = """// 2c3 G1: test_spectre_plan TestAlters.test_alter_targets as a deck; out = 1k/(R1+1k)
simulator lang=spectre
parameters rval=1k
V1 (in 0) vsource dc=1
X1 (in out) rcsec r=rval
subckt rcsec (a b)
parameters r=1k
R1 (a b) resistor r=r
C1 (b 0) capacitor c=1u
ends rcsec
R2 (out 0) resistor r=1k
save out
op1 dc
a1 alter param=rval value=2000
a2 alter param=temp value=50
a3 alter sub=X1 param=r value=3000
a4 alter dev=X1.R1 param=r value=4000
op2 dc
"""
G1_BOTH = ("whether Spectre keeps the altered value or re-evaluates the expression is not known (VACASK re-evaluates it, "
           "Xyce keeps the alter): not supported in v1")
# phase 2c5 (R4): for a target vamos's VACASK deck prints through a switchable hook (2c4's H1) that deck keeps the alter
G1_BOTH_HOOKED = ("whether Spectre keeps the altered value or re-evaluates the expression is not known (VACASK and Xyce "
                  "keep the alter): not supported in v1")
G1_VACASK_ONLY = ("VACASK re-evaluates such a binding at the next analysis after a change of a netlist parameter, temp or "
                  "tnom", "not supported on VACASK in v1 (Xyce runs it)")


class TestFollowUpRuns2c3(_Flow):
    """Phase 2c3 through bin/vamos: G1 the decks whose engines disagreed silently (altthen: VACASK NaN, Xyce 0.5 at
    every point; altthen2: 0.333, 0.25 against 0.5, 0.5; p_altsw: [1, 2, 3] against [3, 3, 3]) are status 2 on both
    engines before any engine runs, the controls run with today's values, and what VACASK alone re-evaluates is status
    2 there and runs on Xyce; G3 a constant expression equal to 0 runs as the r=0 short (F1 refused it)."""

    run2c2 = TestFollowUpRuns2c2.run2c2
    refused = TestFollowUpRuns2c2.refused

    @needs_vacask
    @needs_xyce
    def test_g1_the_decks_whose_engines_disagreed_are_refused_on_both(self):
        """Phase 2c5 (R4): R1's r and E1's gain print through switchable hooks on VACASK (2c4), whose deck keeps the
        alter, so the message says both engines keep it (G1_BOTH_HOOKED)."""
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                self.refused("altthen", ALTTHEN_HEAD + "a1 alter dev=R1 param=r value=1k\ndc1 dc param=rv stop=3k "
                             "step=1k\n", engine, "dc dc1 at rv=0: alter a1 set r of R1, which the netlist binds to the "
                             "expression rv", "dc dc1 (its sweep of rv) changes rv", G1_BOTH_HOOKED)
                self.refused("altthen2", ALTTHEN_HEAD + "a1 alter dev=R1 param=r value=1k\ndc1 dc param=rv start=2k "
                             "stop=3k step=1k\n", engine, "dc dc1 at rv=2000: alter a1 set r of R1", G1_BOTH_HOOKED)
                self.refused("p_altsw", P_ALTSW % "dc2 dc param=g values=[1 2 3]", engine,
                             "dc dc2 at g=1: alter a1 set gain of E1, which the netlist binds to the expression g",
                             "dc dc2 (its sweep of g) changes g", G1_BOTH_HOOKED)
                self.refused("inputalter", ALTTHEN_HEAD + "op0 dc\na1 alter dev=R1 param=r value=3k\na2 alter param=rv "
                             "value=2k\nop1 dc\n", engine, "dc op1: alter a1 set r of R1", "alter a2 changes rv",
                             G1_BOTH_HOOKED)
                # a target without a hook (a pulse's rise) keeps the words of VACASK's own reading
                self.refused("risealter", RISE_2C5 + "op0 dc\na1 alter dev=V1 param=rise value=2u\na2 alter param=rt "
                             "value=3u\ntr1 tran stop=3u\n", engine, "tran tr1: alter a1 set rise of V1, which the netlist "
                             "binds to the expression rt", "alter a2 changes rt after the alter", G1_BOTH)

    @needs_vacask
    @needs_xyce
    def test_g1_an_alter_whose_inputs_do_not_change_keeps_today_s_values(self):
        """X-follow2's m01, m06, m07, m15 and an alter of the bound gain without a later sweep: status 0 and the values
        both engines gave before phase 2c3."""
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                d, _ = self.run2c2("m01", ALTTHEN_HEAD + "a1 alter dev=R1 param=r value=3k\nop1 dc\n", engine)
                self.assertAlmostEqual(self.psf(d, "m01", "op1.dc").value("out"), 0.25, delta=1e-9)
                d, _ = self.run2c2("m06", ALTTHEN_HEAD + "op0 dc\na1 alter dev=R1 param=r value=3k\ndc1 dc dev=R2 "
                                   "param=r values=[1k 3k]\nop2 dc\n", engine)
                self.check_swept(d, "m06", {"dc1": ("out", [0.25, 0.5])}, rel=1e-9, engine=engine)
                self.assertAlmostEqual(self.psf(d, "m06", "op0.dc").value("out"), 0.5, delta=1e-9)
                self.assertAlmostEqual(self.psf(d, "m06", "op2.dc").value("out"), 0.25, delta=1e-9)
                d, _ = self.run2c2("m07", ALTTHEN_HEAD + "op0 dc\na1 alter dev=R1 param=r value=3k\na2 alter param=rv "
                                   "value=1k\nop1 dc\n", engine)
                self.assertAlmostEqual(self.psf(d, "m07", "op1.dc").value("out"), 0.25, delta=1e-9)
                d, _ = self.run2c2("m15", ALTTHEN_HEAD + "dc1 dc param=rv values=[1k 2k]\na1 alter dev=R1 param=r "
                                   "value=3k\nop2 dc\n", engine)
                self.check_swept(d, "m15", {"dc1": ("out", [0.5, 1.0 / 3.0])}, rel=1e-9, engine=engine)
                self.assertAlmostEqual(self.psf(d, "m15", "op2.dc").value("out"), 0.25, delta=1e-9)
                d, _ = self.run2c2("gain", P_ALTSW % "", engine)
                self.assertAlmostEqual(self.psf(d, "gain", "op1.dc").value("out"), 2.0, delta=1e-9)
                self.assertAlmostEqual(self.psf(d, "gain", "op2.dc").value("out"), 3.0, delta=1e-9)

    @needs_vacask
    @needs_xyce
    def test_g1_what_vacask_alone_re_evaluates(self):
        """m02 (an unrelated variable), m16 (rv changed before the alter, after the last analysis) and TestAlters'
        deck: VACASK gave op1 0.5, 0.333 and op2 0.333 where Xyce (and Spectre under either reading) give 0.25, 0.25 and
        0.2: status 2 on VACASK before the run in 2c3; Xyce runs them with those values.  Phase 2c4 (H1): VACASK's deck
        prints the bindings through switchable hooks, which its re-evaluation keeps, and VACASK gives Xyce's values
        (the pieces name what VACASK re-elaborates on, the rule (b) of 2c3 that the hooks retire)."""
        decks = (("m02", ALTTHEN_HEAD + "op0 dc\na1 alter dev=R1 param=r value=3k\na2 alter param=other value=2\nop1 dc\n",
                  "op1", 0.25, "dc op1: on VACASK, alter a1 set r of R1", "alter a2 changes other"),
                 ("m16", ALTTHEN_HEAD + "op0 dc\na0 alter param=rv value=2k\na1 alter dev=R1 param=r value=3k\nop1 dc\n",
                  "op1", 0.25, "dc op1: on VACASK, alter a1 set r of R1", "alter a0 changes rv"),
                 ("targets", ALTER_TARGETS, "op2", 0.2, "dc op2: on VACASK, alter a4 set r of X1.R1",
                  "or of a parameter of the subckt instance X1 that holds it"))
        for name, text, op, value, *pieces in decks:
            for engine in ("vacask", "xyce"):
                with self.subTest(deck=name, engine=engine, retired=pieces):
                    d, out = self.run2c2(name, text, engine)
                    self.assertNotIn(G1_VACASK_ONLY[0], " ".join(out.split()))
                    self.assertAlmostEqual(self.psf(d, name, op + ".dc").value("out"), value, delta=1e-9)

    @needs_vacask
    @needs_xyce
    def test_g3_a_constant_expression_equal_to_0_runs_as_the_short(self):
        """F1 refused r=1k-1k, r=(0) and r=2*0 (status 2) where Spectre makes them a short: out = 1 V and the short's
        current R1:1 = 1 mA, exactly the r=0 reference deck's, on both engines; r=0*rv keeps F1's status 2."""
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                ref, _ = self.run2c2("r0", SHORT_DECK % ("0", "op1 dc"), engine)
                want = self.psf(ref, "r0", "op1.dc")
                self.assertAlmostEqual(want.value("b"), 1.0, delta=1e-12)
                self.assertAlmostEqual(want.value("R1:1"), 1e-3, delta=1e-15)
                for k, r in enumerate(("1k-1k", "(0)", "2*0")):
                    name = "rc%d" % k
                    d, _ = self.run2c2(name, SHORT_DECK % (r, "op1 dc"), engine)
                    got = self.psf(d, name, "op1.dc")
                    for sig in ("b", "R1:1"):
                        self.assertEqual(got.value(sig), want.value(sig), (engine, r, sig))
                self.refused("r0rv", ZERO_HEAD % "0*rv" + "op1 dc\n", engine,
                             "dc op1: resistor R1 on path top level: r=0.0 * rv evaluates to 0")


# -- phase 2c4: H1-H4 through bin/vamos on both engines ----------------------------------------------------------------

DIV_2C4 = """// 2c4: R1's r is bound to rv; out = 1k/(R1+1k)
simulator lang=spectre
parameters rv=1k other=1 rc=1k
V1 (in 0) vsource dc=1 mag=1
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
"""
DIODE_2C4 = """// 2c4: a card's is bound to isv; d the diode's voltage at 1 mA
simulator lang=spectre
parameters isv=1e-14 other=1
I1 (0 d) isource dc=1m
D1 (d 0) dmod
model dmod diode is=isv
R3 (d 0) resistor r=other*1G
save d
"""
HALF_2C4 = """// 2c4: X1.R1 r=rr in subckt half (one path); out = 1k/(R1/m+1k)
simulator lang=spectre
parameters rv=1k other=1
subckt half (a b)
parameters rr=1k q=1
R1 (a b) resistor r=rr
R9 (a 0) resistor r=q*1k
ends half
V1 (in 0) vsource dc=1
X1 (in out) half rr=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
"""
KINDS_2C4 = """// 2c4: a source dc, an m and an X-line binding bound to parameters
simulator lang=spectre
parameters vv=1 mm=1 rv=1k g=2 other=1
subckt half (a b)
parameters rr=1k
R1 (a b) resistor r=rr
ends half
V1 (in 0) vsource dc=vv
R1 (in out) resistor r=1k m=mm
R2 (out 0) resistor r=1k
V4 (i4 0) vsource dc=1
X1 (i4 o4) half rr=rv
R42 (o4 0) resistor r=1k
E1 (o5 0 i4 0) vcvs gain=g
R5 (o5 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out o4 o5
"""
TWO_PATHS_2C4 = """// 2c4: two paths of subckt half; out = 1k/(X1.R1+1k), out2 = 1k/(X2.R1+1k)
simulator lang=spectre
subckt half (a b)
parameters rr=1k
R1 (a b) resistor r=rr
ends half
V1 (in 0) vsource dc=1
X1 (in out) half rr=1k
X2 (in out2) half rr=2k
R2 (out 0) resistor r=1k
R4 (out2 0) resistor r=1k
save out out2
"""
NESTED_2C4 = """// 2c4: X0.X1.R1 two levels down, X0b the other path; out = 1k/(R1+1k)
simulator lang=spectre
parameters other=1
subckt inner (a b)
parameters rr=1k
R1 (a b) resistor r=rr
ends inner
subckt outer (a b)
parameters p=1
X1 (a b) inner rr=p*1k
ends outer
V1 (in 0) vsource dc=1
X0 (in out) outer p=1
X0b (in outb) outer p=2
R2 (out 0) resistor r=1k
R4 (outb 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out outb
"""
SRC_2C4 = """// 2c4: sources with fields bound to parameters, altered or switched in the control block
simulator lang=spectre
parameters vv=1 mm=1
V1 (in 0) vsource dc=vv mag=mm
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
V2 (i2 0) vsource type=pulse dc=0.5 val0=0 val1=vv delay=1u rise=1n fall=1n width=1u period=4u
R21 (i2 o2) resistor r=1k
R22 (o2 0) resistor r=1k
save out o2
"""
EXPR_2C4 = """// 2c4: a netlist parameter defined by an expression (rw=2*rv) or a constant (rk); out = 1k/(R1+1k)
simulator lang=spectre
parameters rv=1k other=1 rw=2*rv rk=2k
V1 (in 0) vsource dc=1
R1 (in out) resistor r=%s
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
"""
SUBDEF_2C4 = """// 2c4: a subckt parameter rr whose default is an expression (q*1k) or a constant; out = 1k/(R1+1k)
simulator lang=spectre
parameters qv=1 other=1
subckt half (a b)
parameters q=1 rr=%s
R1 (a b) resistor r=rr
R9 (a 0) resistor r=q*1k
ends half
V1 (in 0) vsource dc=1
X1 (in out) half%s
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
"""
HOLDER_M_2C4 = """// 2c4 H3: a subckt instance's m (q_holder_m); out = 1k/(R1/m+1k)
simulator lang=spectre
subckt half (a b)
parameters rr=1k
R1 (a b) resistor r=rr
ends half
V1 (in 0) vsource dc=1
X1 (in out) half rr=1k
R2 (out 0) resistor r=1k
save out
"""
A1 = "op0 dc\na1 alter dev=R1 param=r value=3k\n"
HA1 = "op0 dc\na1 alter dev=X1.R1 param=r value=3k\n"
# 2c3's rule-(b) decks (TestFollowUp2c3's VACASK cases, its fixer's m20, m21, m23 and TestAlters' deck) and the 2c3
# skeptic's E151 decks (v01-v10, u01-u04): (name, deck, the value of out or d the hook keeps, the analysis)
RULE_B_2C4 = [
    ("t01", DIV_2C4 + A1 + "a2 alter param=other value=2\nop1 dc\n", 0.25, "op1"),
    ("t02", DIV_2C4 + A1 + "dc1 dc param=other values=[1 2]\nop2 dc\n", 0.25, "op2"),
    ("t03", DIV_2C4 + A1 + "sw1 sweep param=other values=[1 2] {\n  opx dc\n}\n", 0.25, "sw1-001_opx"),
    ("t04", DIV_2C4 + A1 + "a2 alter param=temp value=50\nop1 dc\n", 0.25, "op1"),
    ("t05", DIV_2C4 + A1 + "dc1 dc param=temp values=[27 50]\nop2 dc\n", 0.25, "op2"),
    ("t06", DIV_2C4 + A1 + "s1 set temp=50\nop1 dc\n", 0.25, "op1"),
    ("t07", DIV_2C4.replace("r=rv", "r=rc") + A1 + "a2 alter param=other value=2\nop1 dc\n", 0.25, "op1"),
    ("t08", DIODE_2C4 + "op0 dc\na1 alter mod=dmod param=is value=1e-13\na2 alter param=other value=2\nop1 dc\n",
     0.5955, "op1"),
    ("t09", HALF_2C4 + HA1 + "dc1 dc sub=X1 param=q values=[1 2]\nop2 dc\n", 0.25, "op2"),
    ("t10", HALF_2C4 + HA1 + "a2 alter param=other value=2\nop1 dc\n", 0.25, "op1"),
    ("t11", HALF_2C4 + HA1 + "a2 alter dev=X1 param=m value=2\nop1 dc\n", 0.4, "op1"),
    ("t12", HALF_2C4 + HA1 + "dc1 dc dev=X1 param=m values=[1 2]\nop2 dc\n", 0.25, "op2"),
    ("t13", DIV_2C4 + "op0 dc\na0 alter param=other value=2\na1 alter dev=R1 param=r value=3k\nop1 dc\n", 0.25, "op1"),
    ("t14", DIV_2C4 + "op0 dc\na0 alter param=rv value=2k\na1 alter dev=R1 param=r value=3k\nop1 dc\n", 0.25, "op1"),
    ("t15", DIV_2C4 + "a1 alter dev=R1 param=r value=3k\na2 alter param=rv value=2k\na3 alter dev=R1 param=r "
     "value=3k\nop1 dc\n", 0.25, "op1"),
    ("t16", ALTER_TARGETS, 0.2, "op2"),
    ("m20", KINDS_2C4 + "op0 dc\na1 alter dev=V1 param=dc value=2\na2 alter param=other value=2\nop1 dc\n", 1.0, "op1"),
    ("m21", KINDS_2C4 + "op0 dc\na1 alter dev=R1 param=m value=2\na2 alter param=other value=2\nop1 dc\n", 2.0 / 3.0,
     "op1"),
    ("m23", KINDS_2C4 + "op0 dc\na1 alter sub=X1 param=rr value=3k\na2 alter param=other value=2\nop1 dc\n", None, "op1"),
    ("v01", DIV_2C4 + "o1 options temp=50\na1 alter dev=R1 param=r value=3k\nop1 dc\n", 0.25, "op1"),
    ("v02", DIV_2C4 + "o1 options tnom=25\na1 alter dev=R1 param=r value=3k\nop1 dc\n", 0.25, "op1"),
    ("v03", DIV_2C4 + "o1 options temp=50\nop0 dc\na1 alter dev=R1 param=r value=3k\nop1 dc\n", 0.25, "op1"),
    ("v04", KINDS_2C4 + "o1 options temp=50\na1 alter dev=E1 param=gain value=3\nop1 dc\n", None, "op1"),
    ("v05", DIODE_2C4 + "o1 options temp=50\na1 alter mod=dmod param=is value=1e-13\nop1 dc\n", 0.5488, "op1"),
    ("v06", DIV_2C4 + "o1 options temp=50\na1 alter dev=R1 param=r value=3k\ntr1 tran stop=1u\n", 0.25, "tr1"),
    ("v07", DIV_2C4.replace("r=rv", "r=2k") + "o1 options temp=50\na1 alter dev=R1 param=r value=3k\nop1 dc\n", 0.25,
     "op1"),
    ("v08", DIV_2C4 + "o1 options temp=27\na1 alter dev=R1 param=r value=3k\nop1 dc\n", 0.25, "op1"),
    ("v09", DIV_2C4 + "a0 alter param=temp value=50\na1 alter dev=R1 param=r value=3k\nop1 dc\nop2 dc\n", 0.25, "op2"),
    ("v10", DIV_2C4 + "a0 alter param=other value=2\na9 alter param=temp value=50\na1 alter dev=R1 param=r value=3k\n"
     "op1 dc\n", 0.25, "op1"),
    ("u01", DIV_2C4 + "a0 alter param=temp value=50\na1 alter dev=R1 param=r value=3k\nop1 dc\n", 0.25, "op1"),
    ("u02", DIV_2C4 + "s0 set temp=50\na1 alter dev=R1 param=r value=3k\nop1 dc\n", 0.25, "op1"),
    ("u03", DIV_2C4 + "a0 alter param=tnom value=50\na1 alter dev=R1 param=r value=3k\nop1 dc\n", 0.25, "op1"),
    ("u04", DIV_2C4 + "a0 alter param=temp value=50\na1 alter dev=R2 param=r value=1k\na2 alter dev=R1 param=r "
     "value=3k\nop1 dc\n", 0.25, "op1"),
]


class TestFollowUpRuns2c4(_Flow):
    """Phase 2c4 through bin/vamos: H1 every rule-(b) deck of 2c3 (status 2 on VACASK) and the E151 decks (VACASK 0.5,
    2, d 0.6130 where Xyce gives 0.25, 3, 0.5488, exit 0) run on VACASK with Xyce's values through the switchable
    hooks, and so do the dev=, sub= and mod= sweeps of bound parameters; H2 status 2 on both engines for an alter of an
    expression-defined netlist parameter or subckt default whose inputs change afterwards; H3 Xyce's .DC of a subckt
    instance's m equals VACASK's; H4 the multiplier message names the vsource."""

    run2c2 = TestFollowUpRuns2c2.run2c2
    refused = TestFollowUpRuns2c2.refused

    def both(self, name, text):
        """Run a deck on both engines (status 0 each); (VACASK's run directory, Xyce's)."""
        dv, _ = self.run2c2(name, text, "vacask")
        dx, _ = self.run2c2(name, text, "xyce")
        return dv, dx

    def same_as_xyce(self, name, dv, dx, rel=1e-9):
        """Every PSF file Xyce wrote equals VACASK's, signal by signal (rel; a tran at its first and last times and at
        a quarter, a half and three quarters of the run, the engines' time points differing; a complex value by its
        real and imaginary parts); returns the number of values compared."""
        import psfcheck
        n = 0
        rx, rv = os.path.join(dx, name + ".raw"), os.path.join(dv, name + ".raw")
        files = sorted(f for f in os.listdir(rx) if f != "logFile")
        self.assertEqual(files, sorted(f for f in os.listdir(rv) if f != "logFile"), name)
        for fn in files:
            fx, fv = psfcheck.read(os.path.join(rx, fn)), psfcheck.read(os.path.join(rv, fn))
            for sig in fx.names():
                if fx.swept and fn.endswith(".tran"):
                    stop = fx.sweep_values()[-1]
                    times = [fx.sweep_values()[0], 0.25 * stop, 0.5 * stop, 0.75 * stop, stop]
                    gx, gv = [fx.at(sig, t) for t in times], [fv.at(sig, t) for t in times]
                elif fx.swept:
                    gx, gv = fx.column(sig), fv.column(sig)
                else:
                    gx, gv = [fx.value(sig)], [fv.value(sig)]
                self.assertEqual(len(gx), len(gv), (name, fn, sig))
                for a, b in zip(gv, gx):
                    pairs = ([(a.real, b.real), (a.imag, b.imag)] if isinstance(b, complex)
                             else [(a, b)] if isinstance(b, (int, float)) else [])
                    for x, y in pairs:
                        self.assertAlmostEqual(x, y, delta=abs(y) * rel + 1e-12, msg="%s %s %s: %s %s" % (name, fn, sig, gv, gx))
                        n += 1
        return n

    def value(self, d, name, analysis, sig):
        f = self.psf(d, name, analysis + (".tran.tran" if analysis.startswith("tr") else ".dc"))
        return f.column(sig)[-1] if f.swept else f.value(sig)

    @needs_vacask
    @needs_xyce
    def test_h1_the_rule_b_and_e151_decks_run_on_vacask_with_xyces_values(self):
        """Each deck: status 0 on both engines, VACASK's every value equal to Xyce's (rel 1e-9; 2e-4 for the diode
        decks, whose models differ by that much), and the value the alter sets (the one VACASK lost: 0.5 for 0.25, 0.333
        for 0.2, d 0.6551 for 0.5955, 0.6130 for 0.5488) at the analysis named."""
        runs = {}
        for name, text, want, analysis in RULE_B_2C4:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                runs[name] = dv
                diode = "is=isv" in text
                self.assertGreater(self.same_as_xyce(name, dv, dx, rel=2e-4 if diode else 1e-9), 0)
                sig = "d" if diode else "out"
                if want is not None:
                    for d in (dv, dx):
                        self.assertAlmostEqual(self.value(d, name, analysis, sig), want, delta=abs(want) * 3e-4)
        # m23's X-line binding and v04's gain act on o4 (0.25 with rr = 3k) and o5 (3 with gain 3)
        for name, sig, want in (("m23", "o4", 0.25), ("v04", "o5", 3.0)):
            self.assertIn(name, runs)
            self.assertAlmostEqual(self.value(runs[name], name, "op1", sig), want, delta=1e-9)

    @needs_vacask
    @needs_xyce
    def test_h1_sweeps_of_bound_parameters_through_the_hook_equal_xyce(self):
        """E102/F3 refused a dev= sweep of a bound parameter on VACASK; a sub= sweep of an X-line binding and a mod=
        sweep of a card's failed at VACASK's run (exit 1, `Failed to bind sweep parameters', `is bound to an
        expression'): each now sweeps the hook, with Xyce's values; an alter in force comes back after the sweep."""
        decks = [("sw_dev", DIV_2C4 + "dc1 dc dev=R1 param=r values=[1k 3k]\nop2 dc\n", {"dc1": [0.5, 0.25]}, 0.5),
                 ("sw_sub", KINDS_2C4 + "dc1 dc sub=X1 param=rr values=[1k 3k]\nop2 dc\n", None, None),
                 ("sw_mod", DIODE_2C4 + "dc1 dc mod=dmod param=is values=[1e-14 1e-13]\nop2 dc\n", None, None),
                 ("sw_gain", KINDS_2C4 + "dc1 dc dev=E1 param=gain values=[1 3]\nop2 dc\n", None, None),
                 ("sw_alt", DIV_2C4 + "a1 alter dev=R1 param=r value=3k\ndc1 dc dev=R1 param=r values=[1k 2k]\nop2 dc\n",
                  {"dc1": [0.5, 1.0 / 3.0]}, 0.25),
                 ("sw_blk", DIV_2C4 + "sw1 sweep dev=R1 param=r values=[1k 3k] {\n  op1 dc\n}\nop2 dc\n", None, 0.5),
                 # the 2c2 fixer's f3j and its skeptic's p_modbound: VACASK exit 1 (`Sweep `dc1': parameter 'rr' of
                 # instance 'X1' is bound to an expression. Failed to bind sweep parameters.'), status 1, before
                 ("f3j", "// 2c2 f3j: a sub= sweep of a subckt parameter the X line binds to an expression\n"
                  "simulator lang=spectre\nsubckt half (a b)\nparameters rr=1k\nR1 (a b) resistor r=rr\nends half\n"
                  "parameters rv=2k\nV1 (in 0) vsource dc=1\nX1 (in out) half rr=rv\nR2 (out 0) resistor r=1k\nsave out\n"
                  "dc1 dc sub=X1 param=rr values=[1k 3k]\n", {"dc1": [0.5, 0.25]}, None),
                 ("p_modbound", "// 2c2 p_modbound: a mod= sweep of a diode card parameter bound to an expression\n"
                  "simulator lang=spectre\nparameters isv=1e-14\nVd (dd 0) vsource dc=1\nRd (dd a) resistor r=1k\n"
                  "D1 (a 0) dmod\nmodel dmod diode is=isv\nsave a\ndc1 dc mod=dmod param=is values=[1e-14 2e-14]\n",
                  None, None)]
        runs = {}
        for name, text, swept, op2 in decks:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                runs[name] = dv
                diode = "is=isv" in text
                self.assertGreater(self.same_as_xyce(name, dv, dx, rel=2e-4 if diode else 1e-9), 0)
                if swept:
                    self.check_swept(dv, name, {k: ("out", v) for k, v in swept.items()}, rel=1e-9, engine="vacask")
                if op2 is not None:
                    self.assertAlmostEqual(self.value(dv, name, "op2", "out"), op2, delta=1e-9)
        for name in ("sw_sub", "sw_gain", "sw_blk", "sw_mod"):
            self.assertIn(name, runs)
        self.check_swept(runs["sw_sub"], "sw_sub", {"dc1": ("o4", [0.5, 0.25])}, rel=1e-9, engine="vacask")
        self.check_swept(runs["sw_gain"], "sw_gain", {"dc1": ("o5", [1.0, 3.0])}, rel=1e-9, engine="vacask")
        for k, want in enumerate((0.5, 0.25)):
            self.assertAlmostEqual(self.value(runs["sw_blk"], "sw_blk", "sw1-%03d_op1" % k, "out"), want, delta=1e-9)
        d = self.psf(runs["sw_mod"], "sw_mod", "dc1.dc").column("d")
        self.assertEqual(len(d), 2)
        self.assertLess(d[1], d[0] - 0.05)                     # is = 1e-13 lowers the diode's voltage by about 60 mV

    @needs_vacask
    def test_h1_hooks_down_the_x_lines_keep_to_their_path(self):
        """A body site reached by two paths (X1 and X2 of half), and one two levels down (X0.X1.R1, X0b the other
        path): the hook is passed down the altered path's X lines only (Xyce refuses these alters, §5.3)."""
        d, _ = self.run2c2("twop", TWO_PATHS_2C4 + "op0 dc\na1 alter dev=X1.R1 param=r value=3k\na2 alter param=temp "
                           "value=50\nop1 dc\n", "vacask")
        op0, op1 = self.psf(d, "twop", "op0.dc"), self.psf(d, "twop", "op1.dc")
        self.assertAlmostEqual(op0.value("out"), 0.5, delta=1e-9)
        self.assertAlmostEqual(op1.value("out"), 0.25, delta=1e-9)
        for f in (op0, op1):
            self.assertAlmostEqual(f.value("out2"), 1.0 / 3.0, delta=1e-9)
        d, _ = self.run2c2("nest", NESTED_2C4 + "op0 dc\na1 alter dev=X0.X1.R1 param=r value=3k\na2 alter param=other "
                           "value=2\nop1 dc\nsw1 sweep param=other values=[1 3] {\n  op2 dc\n}\n", "vacask")
        self.assertAlmostEqual(self.psf(d, "nest", "op0.dc").value("out"), 0.5, delta=1e-9)
        self.assertAlmostEqual(self.psf(d, "nest", "op1.dc").value("out"), 0.25, delta=1e-9)
        for k in range(2):
            self.assertAlmostEqual(self.psf(d, "nest", "sw1-%03d_op2.dc" % k).value("out"), 0.25, delta=1e-9)
            self.assertAlmostEqual(self.psf(d, "nest", "sw1-%03d_op2.dc" % k).value("outb"), 1.0 / 3.0, delta=1e-9)

    @needs_vacask
    @needs_xyce
    def test_h1_sources_altered_beside_a_bound_field_and_a_variable_defined_by_an_expression(self):
        """VACASK stopped with exit 1 (`Variable or constant 'vv' not defined', status 2) when the control block's
        alter or source switch printed a field bound to a netlist parameter (an alter of mag beside dc=vv, of dc beside
        mag=mm, a pulse's val1=vv around a tran): such a field is now left out of the statement, which keeps it; and
        parameters rw=2*rv swept through rv before its alter follows rv on VACASK (0.333, 0.333 before; Xyce 0.333,
        0.2)."""
        decks = [("s01", SRC_2C4 + "op1 dc\na1 alter dev=V1 param=mag value=2\nop2 dc\nac2 ac start=1k stop=1k lin=1\n"),
                 ("s02", SRC_2C4 + "op1 dc\ntr1 tran stop=3u\nop2 dc\n"),
                 ("s03", SRC_2C4 + "op1 dc\na1 alter dev=V1 param=dc value=2\nop2 dc\n"),
                 ("c03", EXPR_2C4 % "rw" + "dc1 dc param=rv values=[1k 2k]\na1 alter param=rw value=3k\nop1 dc\n")]
        for name, text in decks:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                self.assertGreater(self.same_as_xyce(name, dv, dx), 0)
        dv, _ = self.run2c2("s03", decks[2][1], "vacask")
        self.assertAlmostEqual(self.psf(dv, "s03", "op2.dc").value("out"), 1.0, delta=1e-9)
        dv, _ = self.run2c2("c03", decks[3][1], "vacask")
        self.check_swept(dv, "c03", {"dc1": ("out", [1.0 / 3.0, 0.2])}, rel=1e-9, engine="vacask")
        self.assertAlmostEqual(self.psf(dv, "c03", "op1.dc").value("out"), 0.25, delta=1e-9)

    @needs_vacask
    @needs_xyce
    def test_h2_an_alter_of_an_expression_defined_parameter_then_a_change_of_its_inputs_is_refused_on_both(self):
        """u08 and u06 of the 2c3 skeptic: both engines kept the alter (0.25, 0.25), whether Spectre re-evaluates is
        not known: status 2 before any engine runs; the controls keep today's values on both engines."""
        refused = [("u08", EXPR_2C4 % "rw" + "a1 alter param=rw value=3k\ndc1 dc param=rv values=[1k 2k]\n",
                    "dc dc1 at rv=2000: alter a1 set rw, which the netlist binds to the expression 2.0 * rv (it reads rv), "
                    "and dc dc1 (its sweep of rv) changes rv after the alter"),
                   ("w02", EXPR_2C4 % "rw" + "op0 dc\na1 alter param=rw value=3k\na2 alter param=rv value=2k\nop1 dc\n",
                    "dc op1: alter a1 set rw", "alter a2 changes rv after the alter"),
                   ("u06", SUBDEF_2C4 % ("q*1k", "") + "op0 dc\na1 alter sub=X1 param=rr value=3k\ndc1 dc sub=X1 "
                    "param=q values=[1 2]\nop2 dc\n", "dc dc1 at X1:q=2: alter a1 set rr of X1, which subckt half binds "
                    "to the default q * 1000.0 (it reads q)", "dc dc1 (its sweep of X1:q) changes q after the alter"),
                   ("w07", SUBDEF_2C4 % ("q*1k", " q=qv") + "a1 alter sub=X1 param=rr value=3k\ndc1 dc param=qv "
                    "values=[1 2]\n", "dc dc1 at qv=2: alter a1 set rr of X1", "changes q after the alter")]
        both_keep = ("whether Spectre keeps the altered value or re-evaluates the expression is not known (VACASK and "
                     "Xyce keep the alter): not supported in v1")
        for engine in ("vacask", "xyce"):
            for name, text, *pieces in refused:
                with self.subTest(deck=name, engine=engine):
                    self.refused(name, text, engine, both_keep, *pieces)
            with self.subTest(engine=engine, construct="controls"):
                d, _ = self.run2c2("c01", EXPR_2C4 % "rk" + "a1 alter param=rk value=3k\ndc1 dc param=rv "
                                   "values=[1k 2k]\n", engine)
                self.check_swept(d, "c01", {"dc1": ("out", [0.25, 0.25])}, rel=1e-9, engine=engine)
                d, _ = self.run2c2("c02", EXPR_2C4 % "rw" + "a1 alter param=rw value=3k\nop1 dc\ndc1 dc param=other "
                                   "values=[1 2]\n", engine)
                self.assertAlmostEqual(self.psf(d, "c02", "op1.dc").value("out"), 0.25, delta=1e-9)
                self.check_swept(d, "c02", {"dc1": ("out", [0.25, 0.25])}, rel=1e-9, engine=engine)
                d, _ = self.run2c2("c05", SUBDEF_2C4 % ("q*1k", "") + "a1 alter sub=X1 param=rr value=3k\nop1 dc\n"
                                   "dc1 dc param=other values=[1 2]\n", engine)
                self.assertAlmostEqual(self.psf(d, "c05", "op1.dc").value("out"), 0.25, delta=1e-9)
                self.check_swept(d, "c05", {"dc1": ("out", [0.25, 0.25])}, rel=1e-9, engine=engine)

    @needs_vacask
    @needs_xyce
    def test_h3_a_sweep_of_a_subckt_instance_m_on_xyce_equals_vacask(self):
        """q_holder_m: Xyce ended with status 2 (`Device instance X1:R1: Multiplicity Factor (M) must be
        non-negative') where VACASK gives [0.5, 0.667]; now Xyce gives VACASK's values, as a sweep block and from a
        nominal m=2 too."""
        decks = [("hm", HOLDER_M_2C4 + "op0 dc\ndc1 dc dev=X1 param=m values=[1 2]\nop2 dc\n"),
                 ("hmblk", HOLDER_M_2C4 + "sw1 sweep dev=X1 param=m values=[1 2] {\n  op1 dc\n}\n"),
                 ("hm2", HOLDER_M_2C4.replace("half rr=1k", "half rr=1k m=2") + "dc1 dc dev=X1 param=m values=[1 3]\n"
                  "op2 dc\n"),
                 ("hmalt", HOLDER_M_2C4 + "op0 dc\na1 alter dev=X1 param=m value=2\nop1 dc\n")]
        for name, text in decks:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                self.assertGreater(self.same_as_xyce(name, dv, dx), 0)
        dx, _ = self.run2c2("hm", decks[0][1], "xyce")
        self.check_swept(dx, "hm", {"dc1": ("out", [0.5, 2.0 / 3.0])}, rel=1e-9, engine="xyce")
        self.assertAlmostEqual(self.psf(dx, "hm", "op2.dc").value("out"), 0.5, delta=1e-9)
        dx, _ = self.run2c2("hm2", decks[2][1], "xyce")
        self.check_swept(dx, "hm2", {"dc1": ("out", [0.5, 0.75])}, rel=1e-9, engine="xyce")
        self.assertAlmostEqual(self.psf(dx, "hm2", "op2.dc").value("out"), 2.0 / 3.0, delta=1e-9)

    @needs_vacask
    @needs_xyce
    def test_h1_the_hook_on_branches_lifted_definitions_names_sources_cards_and_coupled_inductors(self):
        """R1 in both branches of an if (VACASK refused, rule (b); Xyce's replay altered the untaken branch's R1, op1
        0.5 where Spectre gives 0.25), a lifted nested definition, a netlist parameter named vamos_af1, two hooks on one
        resistor (its r altered, its m swept and altered), a pulse's dc and val1 around trans, a dev= sweep in a body
        two paths share, a card in a subckt, a coupled inductor's l: VACASK equals Xyce."""
        cond = """// 2c4: R1 in both branches of an if; the taken one (sel=0: else) reads rr; out = 1k/(R1+1k)
simulator lang=spectre
parameters other=1
subckt half (a b)
parameters rr=1k sel=0
if (sel > 0) {
R1 (a b) resistor r=1k
} else {
R1 (a b) resistor r=rr
}
ends half
V1 (in 0) vsource dc=1
X1 (in out) half rr=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
op0 dc
a1 alter dev=X1.R1 param=r value=3k
a2 alter param=other value=2
op1 dc
"""
        lifted = NESTED_2C4.replace("subckt inner (a b)\nparameters rr=1k\nR1 (a b) resistor r=rr\nends inner\n", "") \
            .replace("X1 (a b) inner rr=p*1k\n", "subckt inner (c d)\nR1 (c d) resistor r=p*1k\nends inner\nX1 (a b) inner\n") \
            .replace("X0b (in outb) outer p=2\n", "").replace("R4 (outb 0) resistor r=1k\n", "").replace("save out outb", "save out")
        decks = [("cond", cond, 0.25, "op1"),
                 ("lifted", lifted + "op0 dc\na1 alter dev=X0.X1.R1 param=r value=3k\na2 alter param=other value=2\nop1 dc\n"
                  "sw1 sweep param=other values=[1 3] {\n  op2 dc\n}\n", 0.25, "op1"),
                 ("names", DIV_2C4.replace("rc=1k", "rc=1k vamos_af1=5 vamos_av1=7") + A1 + "a2 alter param=other value=2\n"
                  "op1 dc\n", 0.25, "op1"),
                 ("twohooks", DIV_2C4.replace("rc=1k", "rc=1k mm=1").replace("r=rv\n", "r=rv m=mm\n") + A1
                  + "a2 alter param=other value=2\nop1 dc\ndc1 dc dev=R1 param=m values=[1 2 3]\nop2 dc\na3 alter dev=R1 "
                  "param=m value=3\nop3 dc\n", 0.5, "op3"),
                 ("pulsehooks", SRC_2C4.replace("val1=vv", "val1=v1").replace("parameters vv=1 mm=1",
                  "parameters vv=1 mm=1 v1=1 other=1").replace("dc=0.5", "dc=vv/2") + "R9 (i2 0) resistor r=other*1G\n"
                  "op0 dc\na1 alter dev=V2 param=val1 value=2\na2 alter param=other value=2\ntr1 tran stop=3u\n"
                  "a3 alter dev=V2 param=dc value=0.8\nop1 dc\ntr2 tran stop=3u\n", None, None),
                 ("shared", TWO_PATHS_2C4 + "dc1 dc dev=X1.R1 param=r values=[1k 3k]\nop2 dc\n", 0.5, "op2"),
                 ("cardsub", "// 2c4: a card in a subckt\nsimulator lang=spectre\nparameters other=1\nsubckt dsub (a)\n"
                  "parameters isx=1e-14\nmodel dmod diode is=isx\nD1 (a 0) dmod\nends dsub\nI1 (0 d) isource dc=1m\n"
                  "X1 (d) dsub\nR3 (d 0) resistor r=other*1G\nsave d\nop0 dc\na1 alter mod=dmod param=is value=1e-13\n"
                  "a2 alter param=other value=2\nop1 dc\n", None, None),
                 ("coupled", "// 2c4: a coupled inductor's l bound to lv, its m=2 folded into it\nsimulator lang=spectre\n"
                  "parameters lv=1m other=1\nIac (0 p) isource dc=0 mag=1m\nL1 (p 0) inductor l=lv m=2\nL2 (s 0) "
                  "inductor l=9m\nK1 mutual_inductor coupling=0.5 ind1=L1 ind2=L2\nR3 (p 0) resistor r=other*1G\nsave s\n"
                  "ac1 ac start=1k stop=1k lin=1\na1 alter dev=L1 param=l value=4m\na2 alter param=other value=2\n"
                  "ac2 ac start=1k stop=1k lin=1\n", None, None),
                 ("holderm", HOLDER_M_2C4.replace("half rr=1k", "half rr=1k m=mm").replace(
                     "subckt half", "parameters mm=1 other=1\nsubckt half") + "R3 (in 0) resistor r=other*1k\nop0 dc\n"
                  "a1 alter dev=X1 param=m value=2\na2 alter param=other value=2\nop1 dc\ndc1 dc dev=X1 param=m "
                  "values=[1 3]\nop2 dc\n", 2.0 / 3.0, "op2"),
                 ("srcbody", "// 2c4: a source in a subckt, dc=vd\nsimulator lang=spectre\nparameters other=1\n"
                  "subckt cell (o)\nparameters vd=1\nV1 (i 0) vsource dc=vd\nR1 (i o) resistor r=1k\nR2 (o 0) resistor "
                  "r=1k\nends cell\nX1 (out) cell vd=1\nR3 (out 0) resistor r=other*1G\nsave out\nop0 dc\na1 alter "
                  "dev=X1.V1 param=dc value=2\na2 alter param=other value=2\nop1 dc\ndc1 dc dev=X1.V1 param=dc "
                  "values=[3 4]\nop2 dc\n", None, None),
                 ("acmag", "// 2c4: an ac magnitude bound to mm\nsimulator lang=spectre\nparameters mm=1 other=1\n"
                  "V1 (in 0) vsource dc=0 mag=mm\nR1 (in out) resistor r=1k\nR2 (out 0) resistor r=1k\nR3 (in 0) "
                  "resistor r=other*1k\nsave out\nac0 ac start=1k stop=1k lin=1\na1 alter dev=V1 param=mag value=4\n"
                  "a2 alter param=other value=2\nac1 ac start=1k stop=1k lin=1\nac2 ac dev=V1 param=mag values=[2 6] "
                  "freq=1k\n", None, None),
                 # a model group whose entry 2 holds a constant vtho: both entries take the alter (Vd2:p -3.18e-5 ->
                 # -1.95e-5 on both engines; BSIM4 on VACASK and Xyce agree to about 1e-6)
                 ("group", "// 2c4: a BSIM4 model group, entry 1's vtho bound to vt\nsimulator lang=spectre\n"
                  "parameters vt=0.40 other=1\nmodel nch bsim4 {\n  1: type=n lmin=0.1u lmax=1u wmin=0.1u wmax=10u "
                  "vtho=vt toxe=2n\n  2: type=n lmin=1u lmax=10u wmin=0.1u wmax=10u vtho=0.45 toxe=2n\n}\n"
                  "Vg (g 0) vsource dc=1.2\nVd1 (d1 0) vsource dc=1.2\nVd2 (d2 0) vsource dc=1.2\nM1 (d1 g 0 0) nch "
                  "w=0.5u l=0.5u\nM2 (d2 g 0 0) nch w=0.5u l=2u\nR3 (g 0) resistor r=other*1G\nsave Vd1:p Vd2:p\n"
                  "op0 dc\na1 alter mod=nch param=vtho value=0.6\na2 alter param=other value=2\nop1 dc\n", None, None)]
        runs = {}
        for name, text, want, analysis in decks:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                runs[name] = dv
                rel = 2e-4 if "diode" in text else 1e-6 if "bsim4" in text else 1e-9
                self.assertGreater(self.same_as_xyce(name, dv, dx, rel=rel), 0)
                if want is not None:
                    for d in (dv, dx):
                        self.assertAlmostEqual(self.value(d, name, analysis, "out"), want, delta=1e-9)
        self.assertIn("coupled", runs)
        a1, a2 = (abs(self.psf(runs["coupled"], "coupled", f + ".ac").column("s")[0]) for f in ("ac1", "ac2"))
        self.assertAlmostEqual(a2, 2 * a1, delta=1e-9 * a2)             # l 1m -> 4m: M = k sqrt(L1 L2) doubles
        self.assertIn("group", runs)
        g0, g1 = (self.psf(runs["group"], "group", f + ".dc") for f in ("op0", "op1"))
        self.assertLess(abs(g1.value("Vd2:p")), 0.7 * abs(g0.value("Vd2:p")))   # entry 2 took vtho 0.6 as well

    @needs_vacask
    def test_h1_verilog_a_instance_and_card_parameters_through_the_hook(self):
        """A Verilog-A instance's r=rv and a card's r=rm (per-instance cards on VACASK; Xyce refuses Verilog-A parameters,
        PyMS ignoring them): a dev= and a mod= sweep, and alters followed by an unrelated variable, give the divider's
        1k/(r+1k) (VACASK refused the sweeps, E102, and the alters, rule (b))."""
        text = """// 2c4: Verilog-A parameters bound to netlist parameters; b, c = 1k/(r+1k)
simulator lang=spectre
ahdl_include "vres2c.va"
parameters rv=1k rm=1k other=1
V1 (a 0) vsource dc=1
X1 (a b) vres2c r=rv
R2 (b 0) resistor r=1k
model vm vres2c r=rm
X2 (a c) vm
R3 (c 0) resistor r=1k
R4 (a 0) resistor r=other*1k
save b c
sw1 dc dev=X1 param=r values=[1k 3k]
sw2 dc mod=vm param=r values=[1k 3k]
op1 dc
al alter dev=X1 param=r value=3k
am alter mod=vm param=r value=3k
a2 alter param=other value=2
op2 dc
"""
        d, _ = self.run2c2("vahook", text, "vacask", files={"vres2c.va": VRES_VA})
        self.check_swept(d, "vahook", {"sw1": ("b", [0.5, 0.25]), "sw2": ("c", [0.5, 0.25])}, rel=1e-9, engine="vacask")
        for op, want in (("op1", 0.5), ("op2", 0.25)):
            for sig in ("b", "c"):
                self.assertAlmostEqual(self.psf(d, "vahook", op + ".dc").value(sig), want, delta=1e-9, msg=(op, sig))

    @needs_vacask
    def test_h4_the_multiplier_message_names_the_vsource(self):
        """`V1: the multiplier of a v is not ...' named the kind letter; the message names the master (exit 2)."""
        _d, out = self.run2c2("vm", DIV_2C4 + "op1 dc\na1 alter dev=V1 param=m value=2\nop2 dc\n", "vacask", rc=2)
        flat = " ".join(out.split())
        self.assertIn("V1: the multiplier of a vsource is not a device parameter on VACASK (folded into the element, or "
                      "without effect); a sweep or alter of m is not supported in v1", flat)
        self.assertNotIn("multiplier of a v is", flat)


# -- phase 2c5 (the repair of 2c4), through bin/vamos on both engines --------------------------------------------------
RISE_2C5 = """// 2c5: a pulse whose rise is bound to rt, R1's r to rv; in(1.5u) = 0.25 with rise 2u (0.5 with 1u)
simulator lang=spectre
parameters rt=1u rv=1k other=1
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=1u rise=rt fall=1u width=5u period=20u
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
CELL_2C5 = """// 2c5: the pulse and R1 inside X1; out = in/4 with R1 3k
simulator lang=spectre
parameters rt=1u other=1
subckt cell (o)
parameters rr=1k
V1 (i 0) vsource type=pulse val0=0 val1=1 delay=1u rise=rt fall=1u width=5u period=20u
R1 (i o) resistor r=rr
R2 (o 0) resistor r=1k
ends cell
X1 (out) cell rr=1k
R3 (out 0) resistor r=other*1G
save out
"""
NOISY_2C5 = """// 2c5: R1's isnoisy bound to nflag, R2's r to rv
simulator lang=spectre
parameters nflag=1 rv=1k other=1
V1 (in 0) vsource dc=0 mag=1
R1 (in out) resistor r=1k isnoisy=nflag
R2 (out 0) resistor r=rv
R3 (in 0) resistor r=other*1k
save out
"""
GROUP_2C5 = """// 2c5: a BSIM4 group whose entry 2 lacks vtho (entry 1 binds it to vt), R5's r bound to rv
simulator lang=spectre
parameters vt=0.40 other=1 rv=1k
model nch bsim4 {
  1: type=n lmin=0.1u lmax=1u wmin=0.1u wmax=10u vtho=vt toxe=2n
  2: type=n lmin=1u lmax=10u wmin=0.1u wmax=10u toxe=2n
}
Vg (g 0) vsource dc=1.2
Vd1 (d1 0) vsource dc=1.2
Vd2 (d2 0) vsource dc=1.2
M1 (d1 g 0 0) nch w=0.5u l=0.5u
M2 (d2 g 0 0) nch w=0.5u l=2u
V5 (i5 0) vsource dc=1
R5 (i5 o5) resistor r=rv
R6 (o5 0) resistor r=1k
R3 (g 0) resistor r=other*1G
save Vd1:p Vd2:p o5
"""
CONDX_2C5 = """// 2c5: X1 is half in the taken branch, other in the untaken one, X2 another other; out = 1k/(R1+1k)
simulator lang=spectre
parameters sel=%s
subckt half (a b)
R1 (a b) resistor r=1k
ends half
subckt other (a b)
R1 (a b) resistor r=1k
ends other
V1 (in 0) vsource dc=1
if (sel > 0) {
X1 (in out) %s
} else {
X1 (in out) %s
}
X2 (in out2) other
R2 (out 0) resistor r=1k
R4 (out2 0) resistor r=1k
save out out2
op0 dc
a1 alter dev=X1.R1 param=r value=3k
op1 dc
"""
BODYM_2C5 = """// 2c5: R1 in subckt half without an m; out = 1k/(1k/m+1k)
simulator lang=spectre
parameters other=1
subckt half (a b)
R1 (a b) resistor r=1k
ends half
V1 (in 0) vsource dc=1
X1 (in out) half
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
"""
EXP_2C5 = """// 2c5: an exp whose td1 reads td; in(3u) = exp(-1) - exp(-4) with td2 2u
simulator lang=spectre
parameters td=1u t2=10u rv=1k other=1
V1 (in 0) vsource type=exp val0=0 val1=1 td1=td tau1=0.5u td2=%s tau2=1u
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""


class TestFollowUpRuns2c5(_Flow):
    """Phase 2c5 through bin/vamos: R1 S-2c4's A01-A11 (VACASK status 0 with the alter of a target the hook does not
    print lost after a hooked alter or sweep: in 0.5 against 0.25, noise 3.526e-9 against 1.763e-9) are status 2 on
    VACASK, Xyce runs them, and the controls and O01 (refused before) run with Xyce's values; R2 B01-B04 (Xyce out2 0.25
    where 0.5) give VACASK's values on Xyce; R3 G01-G06 (VACASK 0.5 where Xyce 0.667, a sweep exit 1) give Xyce's
    values on VACASK; R4 the probe1 decks (VACASK exit 1) give Xyce's values, the one field the control block can
    neither print nor leave out is status 2 on VACASK."""

    run2c2 = TestFollowUpRuns2c2.run2c2
    refused = TestFollowUpRuns2c2.refused
    both = TestFollowUpRuns2c4.both
    same_as_xyce = TestFollowUpRuns2c4.same_as_xyce
    value = TestFollowUpRuns2c4.value

    def at(self, d, name, analysis, sig, t):
        return self.psf(d, name, analysis + ".tran.tran").at(sig, t)

    @needs_vacask
    @needs_xyce
    def test_r1_a_target_the_hook_does_not_print_is_refused_on_vacask_after_a_hooked_change(self):
        a1, a2 = "a1 alter dev=V1 param=rise value=2u\n", "a2 alter dev=R1 param=r value=3k\n"
        tr1 = "tr1 tran stop=3u\n"
        refused = [
            ("A01", RISE_2C5 + a1 + a2 + tr1, "tran tr1: on VACASK, alter a1 set rise of V1", "alter a2 changes r of R1"),
            ("A02", RISE_2C5 + a1 + "dc1 dc dev=R1 param=r values=[1k 3k]\n" + tr1,
             "dc dc1 at R1:r=1000: on VACASK, alter a1 set rise of V1", "dc dc1 (its sweep of R1:r) changes r of R1"),
            ("A03", RISE_2C5 + "op0 dc\n" + a1 + a2 + tr1, "tran tr1: on VACASK, alter a1 set rise of V1",
             "alter a2 changes r of R1"),
            ("A04", CELL_2C5 + "a1 alter dev=X1.V1 param=rise value=2u\na2 alter dev=X1.R1 param=r value=3k\n" + tr1,
             "tran tr1: on VACASK, alter a1 set rise of X1.V1", "alter a2 changes r of X1.R1"),
            ("A06", RISE_2C5 + "op0 dc\n" + a2 + a1 + tr1, "tran tr1: on VACASK, alter a1 set rise of V1",
             "alter a2 changes r of R1"),
            ("A07", RISE_2C5 + a1 + "tr0 tran stop=3u\n" + a2 + tr1, "tran tr1: on VACASK, alter a1 set rise of V1",
             "alter a2 changes r of R1"),
            ("A09", NOISY_2C5 + "a1 alter dev=R1 param=isnoisy value=0\na2 alter dev=R2 param=r value=3k\n"
             "n1 (out 0) noise start=1k stop=1k lin=1 iprobe=V1\n", "noise n1: on VACASK, alter a1 set isnoisy of R1",
             "alter a2 changes r of R2"),
            ("A10", RISE_2C5 + a1 + "sw1 sweep dev=R1 param=r values=[1k 3k] {\n  op1 dc\n}\n" + tr1,
             "dc op1 at R1:r=1000: on VACASK, alter a1 set rise of V1", "sweep sw1 changes r of R1"),
            ("A11", GROUP_2C5 + "op0 dc\na1 alter mod=nch param=vtho value=0.6\na2 alter dev=R5 param=r value=3k\n"
             "op1 dc\n", "dc op1: on VACASK, alter a1 set vtho of model nch", "alter a2 changes r of R5"),
            ("A12", RISE_2C5 + "dc1 dc dev=R1 param=r values=[1k 3k]\n" + a1 + tr1,
             "tran tr1: on VACASK, alter a1 set rise of V1", "dc dc1 (its sweep of R1:r) changes r of R1"),
            ("X07", RISE_2C5.replace("other=1", "other=1 vv=1") + "V5 (i5 0) vsource dc=vv\nR5 (i5 0) resistor r=1k\n"
             "dc1 dc dev=V5 param=dc values=[1 2]\n" + a1 + tr1, "tran tr1: on VACASK, alter a1 set rise of V1",
             "dc dc1 (its sweep of V5:dc) changes dc of V5"),
            ("X13", RISE_2C5.replace("other=1", "other=1 rw=2*rv").replace("r=rv", "r=rw")
             + "dc0 dc param=rv values=[1k 2k]\ndc1 dc param=rw values=[1k 3k]\n" + a1 + tr1,
             "tran tr1: on VACASK, alter a1 set rise of V1", "dc dc1 (its sweep of rw) changes rw")]
        xyce_values = {"A01": ("in", 0.25), "A03": ("in", 0.25), "A06": ("in", 0.25), "A07": ("in", 0.25),
                       "A04": ("out", 0.0625), "X07": ("in", 0.25), "X13": ("in", 0.25)}
        for name, text, *pieces in refused:
            with self.subTest(deck=name):
                self.refused(name, text, "vacask", "which the VACASK deck sets through a variable",
                             "not supported on VACASK in v1 (Xyce runs it)", *pieces)
                dx, _ = self.run2c2(name, text, "xyce")
                if name in xyce_values:
                    sig, want = xyce_values[name]                    # A04: R3's 1G load takes 1e-6 of it
                    self.assertAlmostEqual(self.at(dx, name, "tr1", sig, 1.5e-6), want, delta=2e-6 * want)
        dx, _ = self.run2c2("A09", refused[6][1], "xyce")
        self.assertAlmostEqual(abs(self.psf(dx, "A09", "n1.noise").column("out")[0]), 1.763e-9, delta=2e-12)
        dx, _ = self.run2c2("A11", refused[8][1], "xyce")
        self.assertAlmostEqual(self.psf(dx, "A11", "op1.dc").value("Vd1:p"), -6.555e-5, delta=2e-8)
        # X02: isnoisy=nflag altered with the word no, then a variable change (VACASK's noise 2.879e-9, Xyce's 2.036e-9)
        x02 = (NOISY_2C5 + "a1 alter dev=R1 param=isnoisy value=no\na2 alter param=other value=2\n"
               "n1 (out 0) noise start=1k stop=1k lin=1 iprobe=V1\n")
        self.refused("X02", x02, "vacask", "noise n1: on VACASK, alter a1 set isnoisy of R1, which the netlist binds to "
                     "the expression nflag", "alter a2 changes other", "not supported on VACASK in v1 (Xyce runs it)")
        dx, _ = self.run2c2("X02", x02, "xyce")
        self.assertAlmostEqual(abs(self.psf(dx, "X02", "n1.noise").column("out")[0]), 2.0357e-9, delta=2e-12)

    @needs_vacask
    @needs_xyce
    def test_r1_the_controls_and_o01_run_on_both_with_xyces_values(self):
        a1, tr1 = "a1 alter dev=V1 param=rise value=2u\n", "tr1 tran stop=3u\n"
        decks = [("A01c", RISE_2C5.replace("R3 (in 0)", "R5 (out 0) resistor r=1G\nR3 (in 0)") + a1
                  + "a2 alter dev=R5 param=r value=2G\n" + tr1, 0.25),
                 ("A05", RISE_2C5 + "a2 alter dev=R1 param=r value=3k\n" + a1 + tr1, 0.25),
                 ("A07c", RISE_2C5.replace("r=rv", "r=1k") + a1 + "tr0 tran stop=3u\na2 alter dev=R1 param=r value=3k\n"
                  + tr1, 0.25),
                 ("O01", RISE_2C5 + "a0 alter dev=R1 param=r value=3k\na1 alter param=other value=2\na2 alter dev=V1 "
                  "param=rise value=2u\n" + tr1, 0.25),
                 ("A12c", RISE_2C5 + "dc1 dc dev=R1 param=r values=[1k 3k]\nop9 dc\n" + a1 + tr1, 0.25),
                 ("A14", RISE_2C5 + "a0 alter dev=R1 param=r value=3k\ndc1 dc dev=R1 param=r values=[1k 3k]\n" + a1
                  + tr1, 0.25),
                 ("A09c", NOISY_2C5.replace("r=rv", "r=3k").replace("R3 (in 0)", "R5 (out 0) resistor r=1G\nR3 (in 0)")
                  + "a1 alter dev=R1 param=isnoisy value=0\na2 alter dev=R5 param=r value=2G\n"
                  "n1 (out 0) noise start=1k stop=1k lin=1 iprobe=V1\n", None),
                 ("X03", NOISY_2C5 + "a1 alter dev=R1 param=isnoisy value=no\n"
                  "n1 (out 0) noise start=1k stop=1k lin=1 iprobe=V1\n", None),
                 ("X13c", RISE_2C5.replace("other=1", "other=1 rw=2*rv").replace("r=rv", "r=rw")
                  + "dc0 dc param=rv values=[1k 2k]\ndc1 dc param=rw values=[1k 3k]\nop9 dc\n" + a1 + tr1, 0.25),
                 ("A11c", GROUP_2C5 + "op0 dc\na1 alter mod=nch param=vtho value=0.6\nop1 dc\n", None)]
        for name, text, in15 in decks:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                self.assertGreater(self.same_as_xyce(name, dv, dx, rel=1e-6 if "bsim4" in text else 1e-9), 0)
                if in15 is not None:
                    self.assertAlmostEqual(self.at(dv, name, "tr1", "in", 1.5e-6), in15, delta=1e-9)

    @needs_vacask
    @needs_xyce
    def test_r2_xyce_alters_the_taken_branch_only(self):
        b04 = (CONDX_2C5.replace("R1 (a b) resistor r=1k", "parameters rr=1k\nR1 (a b) resistor r=rr")
               .replace("parameters sel=%s", "parameters sel=%s other=1")
               .replace("save out out2", "R3 (in 0) resistor r=other*1k\nsave out out2")
               .replace("op1 dc", "a2 alter param=other value=2\nop1 dc"))
        b02 = CONDX_2C5.replace("if (sel > 0) {", "subckt top (a b)\nparameters sel=1\nif (sel > 0) {") \
            .replace("X1 (in out) %s\n}\nX2", "X1 (a b) %s\n}\nends top\nXT (in out) top\nX2") \
            .replace("X1 (in out) %s\n} else", "X1 (a b) %s\n} else").replace("dev=X1.R1", "dev=XT.X1.R1")
        cond = ("// 2c4 e01: R1 in both branches of an if in half; the taken one (sel=0: else) reads rr\n"
                "simulator lang=spectre\nparameters other=1\nsubckt half (a b)\nparameters rr=1k sel=0\nif (sel > 0) {\n"
                "R1 (a b) resistor r=1k\n} else {\nR1 (a b) resistor r=rr\n}\nends half\nV1 (in 0) vsource dc=1\n"
                "X1 (in out) half rr=1k\nR2 (out 0) resistor r=1k\nR3 (in 0) resistor r=other*1k\nsave out\nop0 dc\n"
                "a1 alter dev=X1.R1 param=r value=3k\na2 alter param=other value=2\nop1 dc\n")
        decks = [("B01", CONDX_2C5 % ("1", "half", "other")), ("B02", b02 % ("1", "half", "other")),
                 ("B03", CONDX_2C5 % ("0", "other", "half")), ("B04", b04 % ("1", "half", "other")), ("e01", cond)]
        for name, text in decks:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                self.assertGreater(self.same_as_xyce(name, dv, dx), 0)
                op1 = self.psf(dx, name, "op1.dc")
                self.assertAlmostEqual(op1.value("out"), 0.25, delta=1e-9)
                if name != "e01":
                    self.assertAlmostEqual(op1.value("out2"), 0.5, delta=1e-9)

    @needs_vacask
    @needs_xyce
    def test_r3_the_m_of_an_instance_in_a_body_on_vacask_equals_xyce(self):
        nested = BODYM_2C5.replace("subckt half (a b)\nR1", "subckt inner (a b)\nR1").replace(
            "ends half", "ends inner\nsubckt outer (a b)\nX1 (a b) inner\nends outer").replace(
            "X1 (in out) half", "X0 (in out) outer")
        decks = [("G01", BODYM_2C5 + "op0 dc\na1 alter dev=X1.R1 param=m value=2\na2 alter param=other value=2\nop1 dc\n",
                  2.0 / 3.0),
                 ("G01c", BODYM_2C5 + "op0 dc\na1 alter dev=X1.R1 param=m value=2\nop1 dc\n", 2.0 / 3.0),
                 ("G02", BODYM_2C5.replace("r=1k\nends", "r=1k m=1\nends") + "op0 dc\na1 alter dev=X1.R1 param=m value=2\n"
                  "a2 alter param=other value=2\nop1 dc\n", 2.0 / 3.0),
                 ("G04", nested + "op0 dc\na1 alter dev=X0.X1 param=m value=2\na2 alter param=other value=2\nop1 dc\n",
                  2.0 / 3.0),
                 ("G05", BODYM_2C5 + "op0 dc\na1 alter dev=X1.R1 param=m value=2\na2 alter param=temp value=50\nop1 dc\n",
                  2.0 / 3.0),
                 ("G06", BODYM_2C5 + "dc1 dc dev=X1.R1 param=m values=[1 2]\nop1 dc\n", 0.5),
                 ("E02", BODYM_2C5.replace("subckt half (a b)\nR1 (a b) resistor r=1k", "subckt half (a b)\n"
                  "parameters rr=1k\nR1 (a b) resistor r=rr").replace("X1 (in out) half", "X1 (in out) half rr=1k")
                  + "dc1 dc dev=X1.R1 param=m values=[1 2]\nop1 dc\n", 0.5),
                 # X1's m altered to 3 and swept (no m on its line): the holder's $mfactor times R1's own 2
                 ("G08", BODYM_2C5 + "op0 dc\na0 alter dev=X1 param=m value=3\na1 alter dev=X1.R1 param=m value=2\n"
                  "dc1 dc dev=X1 param=m values=[1 2]\nop1 dc\n", 1.0 / (1.0 / 6.0 + 1.0))]
        for name, text, op1 in decks:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                self.assertGreater(self.same_as_xyce(name, dv, dx), 0)
                self.assertAlmostEqual(self.value(dv, name, "op1", "out"), op1, delta=1e-9)
        dv, _ = self.run2c2("G06", decks[5][1], "vacask")
        self.check_swept(dv, "G06", {"dc1": ("out", [0.5, 2.0 / 3.0])}, rel=1e-9, engine="vacask")
        dv, _ = self.run2c2("G08", decks[7][1], "vacask")
        self.check_swept(dv, "G08", {"dc1": ("out", [2.0 / 3.0, 0.8])}, rel=1e-9, engine="vacask")
        # X1's own m=3 on its line: VACASK's $mfactor of R1 carries it, so the alter of R1's m stays refused there
        # (status 2 before any engine runs); Xyce gives 1k/(1k/6+1k)
        g07 = (BODYM_2C5.replace("X1 (in out) half", "X1 (in out) half m=3") + "op0 dc\na1 alter dev=X1.R1 param=m "
               "value=2\na2 alter param=other value=2\nop1 dc\n")
        d, out = self.run2c2("G07", g07, "vacask", rc=2)             # refused at the deck's render (read-in)
        self.assertIn("X1.R1: VACASK sets the device's $mfactor, which also carries the multiplier of the enclosing "
                      "instance X1 (m=3.0)", " ".join(out.split()))
        res = os.path.join(d, "G07.raw")
        self.assertFalse(os.path.isdir(res) and any(f != "logFile" for f in os.listdir(res)), out)
        dx, _ = self.run2c2("G07", g07, "xyce")
        self.assertAlmostEqual(self.psf(dx, "G07", "op0.dc").value("out"), 0.75, delta=1e-9)
        self.assertAlmostEqual(self.psf(dx, "G07", "op1.dc").value("out"), 1.0 / (1.0 / 6.0 + 1.0), delta=1e-9)

    @needs_vacask
    @needs_xyce
    def test_r4_control_block_statements_on_vacask_equal_xyce(self):
        exp3 = math.exp(-1.0) - math.exp(-4.0)
        decks = [("val1rise", RISE_2C5.replace("rv=1k ", "").replace("r=rv", "r=1k") + "a1 alter dev=V1 param=val1 value=2\n"
                  "tr1 tran stop=3u\n", 1e-9, ("in", 1.5e-6, 1.0)),
                 ("width", RISE_2C5.replace("rt=1u", "wd=1u rt=1u").replace("delay=1u", "delay=wd").replace(
                     "width=5u", "width=wd") + "a1 alter dev=V1 param=width value=2u\na2 alter param=other value=2\n"
                  "tr1 tran stop=5u\n", 1e-9, ("in", 3.5e-6, 1.0)),
                 ("exptd2", EXP_2C5 % "t2" + "a1 alter dev=V1 param=td2 value=2u\na2 alter param=other value=2\n"
                  "tr1 tran stop=3u\n", 5e-3, ("in", 3e-6, exp3)),
                 ("pdc", RISE_2C5.replace("type=pulse", "type=pulse dc=0.5") + "op0 dc\ntr1 tran stop=3u\nop2 dc\n", 1e-9,
                  ("in", 1.5e-6, 0.5)),
                 ("pdcalt", RISE_2C5.replace("type=pulse", "type=pulse dc=0.5") + "op0 dc\na1 alter dev=V1 param=val1 "
                  "value=2\na2 alter param=other value=2\ntr1 tran stop=3u\nop2 dc\n", 1e-9, ("in", 1.5e-6, 1.0)),
                 ("pwl", RISE_2C5.replace("rt=1u", "vp=1").replace("type=pulse val0=0 val1=1 delay=1u rise=rt fall=1u "
                                                                   "width=5u period=20u", "type=pwl dc=0 wave=[0 0 1u vp 3u vp]")
                  + "op0 dc\ntr1 tran stop=3u\nop2 dc\n", 1e-9, ("in", 0.5e-6, 0.5))]
        for name, text, rel, (sig, t, want) in decks:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                self.assertGreater(self.same_as_xyce(name, dv, dx, rel=rel), 0)
                for d in (dv, dx):
                    self.assertAlmostEqual(self.at(d, name, "tr1", sig, t), want, delta=1e-9 if rel < 1e-6 else 2e-3)
                    if name == "exptd2":
                        self.assertAlmostEqual(self.psf(d, name, "tr1.tran.tran").column("in")[-1], exp3, delta=1e-6)

    @needs_vacask
    @needs_xyce
    def test_r4_a_field_the_control_block_cannot_leave_out_is_refused_on_vacask(self):
        text = EXP_2C5 % "10u" + "a1 alter dev=V1 param=td2 value=2u\ntr1 tran stop=3u\n"
        self.refused("exp10", text, "vacask", "alter a1: on VACASK the control block's alter of V1 would print td2=",
                     "not supported on VACASK in v1 (Xyce runs it)")
        dx, _ = self.run2c2("exp10", text, "xyce")
        self.assertAlmostEqual(self.psf(dx, "exp10", "tr1.tran.tran").column("in")[-1],
                               math.exp(-1.0) - math.exp(-4.0), delta=1e-6)
        # X14: a dc source with pulse fields (val1=vv, vv=2) switched to pulse by an alter: 2c4 left val1 out of the
        # alter, and VACASK took its default 1 (in 1.0 where Xyce gives 2.0 at 2.5u); X15, the fields constant, runs
        x14 = (RISE_2C5.replace("rt=1u", "vv=2").replace("type=pulse val0=0 val1=1 delay=1u rise=rt",
                                                          "type=dc dc=0 val0=0 val1=vv delay=1u rise=1u")
               + "a1 alter dev=V1 param=type value=pulse\ntr1 tran stop=3u\n")
        self.refused("x14", x14, "vacask", "alter a1: on VACASK the control block's alter of V1 would print val1=vv, "
                     "which reads vv", "not supported on VACASK in v1 (Xyce runs it)")
        dx, _ = self.run2c2("x14", x14, "xyce")
        self.assertAlmostEqual(self.at(dx, "x14", "tr1", "in", 2.5e-6), 2.0, delta=1e-9)
        x15 = x14.replace("val1=vv", "val1=2")
        dv, dx = self.both("x15", x15)
        self.assertGreater(self.same_as_xyce("x15", dv, dx), 0)
        self.assertAlmostEqual(self.at(dv, "x15", "tr1", "in", 2.5e-6), 2.0, delta=1e-9)


# -- phase 2c6 (the repair of 2c4 and 2c5), through bin/vamos on both engines ------------------------------------------
PWL_SMALL_2C6 = """// 2c6: V1's wave reads vp; out = in*1k/(R1+1k)
simulator lang=spectre
parameters vp=1 rv=1k other=1
V1 (in 0) vsource type=pwl wave=[0 0 1u vp 3u vp]
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
PWL_BIG_2C6 = """// 2c6: V1's wave reads vp, a second source and a subckt; out = in*1k/(R1+1k)
simulator lang=spectre
parameters vp=1 rv=1k other=1 vv=1
subckt half (a b)
R1 (a b) resistor r=1k
ends half
V1 (in 0) vsource type=pwl wave=[0 0 1u vp 3u vp]
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
V5 (i5 0) vsource dc=vv
X5 (i5 0) half
save in out
"""
PWL_XLINE_2C6 = """// 2c6: V1's wave reads vp, X1's rr=rq; out = in*1k/(rr+1k)
simulator lang=spectre
parameters vp=1 rq=1k other=1
subckt half (a b)
parameters rr=1k
R1 (a b) resistor r=rr
ends half
V1 (in 0) vsource type=pwl wave=[0 0 1u vp 3u vp]
X1 (in out) half rr=rq
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
PWL_BODYM_2C6 = """// 2c6: V1's wave reads vp, R1 in subckt half without an m; out = in/2
simulator lang=spectre
parameters vp=1 other=1
subckt half (a b)
R1 (a b) resistor r=1k
ends half
V1 (in 0) vsource type=pwl wave=[0 0 1u vp 3u vp]
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
V5 (i5 0) vsource dc=1
X5 (i5 0) half
R3 (in 0) resistor r=other*1k
save in out
"""
WAVE_A1_2C6 = "a1 alter dev=V1 param=wave value=[0 0 1u 2 3u 2]\n"
TR1_2C6 = "tr1 tran stop=3u\n"
# S-2c5's decks (name, deck, R1 in force at tr1): status 0 on both engines with VACASK's in(2u) 1 where Xyce's is 2
K1_RUNS_2C6 = [
    ("N427", PWL_SMALL_2C6.replace("r=rv", "r=1k").replace(" rv=1k", "") + WAVE_A1_2C6 + "a2 alter param=other value=2\n"
     + TR1_2C6, 1e3),
    ("N601", PWL_SMALL_2C6 + WAVE_A1_2C6 + "a2 alter dev=R1 param=r value=3k\n" + TR1_2C6, 3e3),
    ("N602", PWL_SMALL_2C6 + WAVE_A1_2C6 + "a2 alter param=temp value=50\n" + TR1_2C6, 1e3),
    ("N603", PWL_SMALL_2C6 + "op0 dc\n" + WAVE_A1_2C6 + "a2 alter param=other value=2\n" + TR1_2C6, 1e3),
    ("N606", PWL_SMALL_2C6 + WAVE_A1_2C6 + "dc1 dc param=other values=[1 2]\n" + TR1_2C6, 1e3),
    ("N701", PWL_BIG_2C6 + WAVE_A1_2C6 + "a2 alter dev=R1 param=r value=3k\n" + TR1_2C6, 3e3),
    ("N702", PWL_BIG_2C6 + WAVE_A1_2C6 + "dc1 dc dev=R1 param=r values=[1k 3k]\n" + TR1_2C6, 1e3),
    ("N703", PWL_BIG_2C6 + WAVE_A1_2C6 + "a2 alter dev=X5.R1 param=m value=2\n" + TR1_2C6, 1e3),
    ("N704", PWL_BIG_2C6 + WAVE_A1_2C6 + "a2 alter dev=V5 param=dc value=2\n" + TR1_2C6, 1e3),
    ("N705", PWL_BIG_2C6 + "op0 dc\n" + WAVE_A1_2C6 + "a2 alter dev=R1 param=r value=3k\n" + TR1_2C6, 3e3),
    ("N803", PWL_SMALL_2C6.replace("rv=1k", "rv=1k rw=2*rv").replace("r=rv", "r=rw") + "dc0 dc param=rv values=[1k 2k]\n"
     + WAVE_A1_2C6 + "a2 alter param=rw value=3k\n" + TR1_2C6, 3e3),
    ("N804", PWL_XLINE_2C6 + WAVE_A1_2C6 + "a2 alter dev=X1 param=rr value=3k\n" + TR1_2C6, 3e3),
    ("N805", PWL_BODYM_2C6 + WAVE_A1_2C6 + "dc1 dc dev=X5.R1 param=m values=[1 2]\n" + TR1_2C6, 1e3),
    ("N806", PWL_BODYM_2C6 + "op0 dc\na2 alter dev=X5.R1 param=m value=2\n" + WAVE_A1_2C6 + TR1_2C6, 1e3),
]
SINE_2C6 = """// 2c6: a sine whose sinedc defaults to its dc, bound to vv; in(t) = sinedc + 0.5 sin(2 pi 1e6 t)
simulator lang=spectre
parameters vv=0.5 other=1
V1 (in 0) vsource type=sine dc=vv ampl=0.5 freq=1e6
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
EXP_2C6 = """// 2c6: an exp whose td2 reads t2 (altered: hooked on VACASK), td1 1u (altered to 0.5u)
simulator lang=spectre
parameters td=1u t2=10u rv=1k other=1
V1 (in 0) vsource type=exp val0=0 val1=1 td1=%s tau1=0.5u td2=%s tau2=1u
R1 (in out) resistor r=rv
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
GROUP_2C6 = """// 2c6: a BSIM4 group whose entry 1 binds its threshold (vtho or vth0) to vt, entry 2 lacks it
simulator lang=spectre
parameters vt=0.40 other=1 rv=1k
model nch bsim4 {
  1: type=n lmin=0.1u lmax=1u wmin=0.1u wmax=10u %s=vt toxe=2n
  2: type=n lmin=1u lmax=10u wmin=0.1u wmax=10u toxe=2n
}
Vg (g 0) vsource dc=1.2
Vd1 (d1 0) vsource dc=1.2
Vd2 (d2 0) vsource dc=1.2
M1 (d1 g 0 0) nch w=0.5u l=0.5u
M2 (d2 g 0 0) nch w=0.5u l=2u
V5 (i5 0) vsource dc=1
R5 (i5 o5) resistor r=rv
R6 (o5 0) resistor r=1k
R3 (g 0) resistor r=other*1G
save Vd1:p Vd2:p o5
"""
COND_SRC_2C6 = """// 2c6 (S-2c5's N607): V1 in both branches of an if; in = val1 on the flat top (1u-3u), out = in/2
simulator lang=spectre
parameters sel=%s vv=2 other=1
if (sel > 0) {
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=0.5u rise=0.5u fall=0.5u width=1u period=20u
} else {
V1 (in 0) vsource type=pulse val0=0 val1=vv delay=0.5u rise=0.5u fall=0.5u width=1u period=20u
}
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
a1 alter dev=V1 param=%s value=%s
a2 alter param=other value=2
tr1 tran stop=4u
"""


class TestFollowUpRuns2c6(_Flow):
    """Phase 2c6 through bin/vamos: K1 S-2c5's decks, an alter of a pwl wave followed by a change VACASK re-elaborates
    on (VACASK in(2u) 1 where Xyce and the analytic value give 2, status 0 on both), are status 2 on VACASK and give the
    analytic values on Xyce, N605 (a change of what the wave reads) is status 2 on both, the controls give equal values;
    the audit's other forms (a sine's sinedc defaulting to a bound dc, an exp's td2 derived through a hook, a card's other
    spelling, the sweeps of a bound or derived target) end refused or right; K2 a source in both branches of an if, each
    branch taken in turn, with an alter of a field, runs on both engines with the analytic values (N607 was refused at
    VACASK's render)."""

    run2c2 = TestFollowUpRuns2c2.run2c2
    refused = TestFollowUpRuns2c2.refused
    both = TestFollowUpRuns2c4.both
    same_as_xyce = TestFollowUpRuns2c4.same_as_xyce
    value = TestFollowUpRuns2c4.value
    at = TestFollowUpRuns2c5.at

    @needs_vacask
    @needs_xyce
    def test_k1_a_pwl_wave_alter_is_refused_on_vacask_and_right_on_xyce(self):
        for name, text, r1 in K1_RUNS_2C6:
            with self.subTest(deck=name):
                self.refused(name, text, "vacask", "on VACASK, alter a1 set wave of V1, which the netlist binds to the "
                             "expression [0.0 0.0 1e-06 vp 3e-06 vp] (it reads vp)",
                             "not supported on VACASK in v1 (Xyce runs it)")
                dx, _ = self.run2c2(name, text, "xyce")
                self.assertAlmostEqual(self.at(dx, name, "tr1", "in", 2e-6), 2.0, delta=1e-9)
                self.assertAlmostEqual(self.at(dx, name, "tr1", "out", 2e-6), 2.0 * 1e3 / (r1 + 1e3), delta=2e-9)
        n605 = PWL_SMALL_2C6 + WAVE_A1_2C6 + "a2 alter param=vp value=3\n" + TR1_2C6
        for engine in ("vacask", "xyce"):
            with self.subTest(deck="N605", engine=engine):
                self.refused("N605", n605, engine, "tran tr1: alter a1 set wave of V1, which the netlist binds to the "
                             "expression [0.0 0.0 1e-06 vp 3e-06 vp] (it reads vp), and alter a2 changes vp after the alter",
                             "not supported in v1")

    @needs_vacask
    @needs_xyce
    def test_k1_the_wave_controls_run_on_both_with_equal_values(self):
        """S-2c5's N428 (no later change), N604 (a constant wave), N706 (the hooked alter first), N707 (a constant alter
        after), and an alter to the values the binding gives: in(2u) = 2 (1 for the last), equal on both engines."""
        decks = [("N428", PWL_SMALL_2C6.replace("r=rv", "r=1k").replace(" rv=1k", "") + WAVE_A1_2C6 + TR1_2C6, 2.0),
                 ("N604", PWL_SMALL_2C6.replace("1u vp 3u vp", "1u 1 3u 1") + WAVE_A1_2C6 + "a2 alter param=other value=2\n"
                  + TR1_2C6, 2.0),
                 ("N706", PWL_BIG_2C6 + "a2 alter dev=R1 param=r value=3k\n" + WAVE_A1_2C6 + TR1_2C6, 2.0),
                 ("N707", PWL_BIG_2C6 + WAVE_A1_2C6 + "a2 alter dev=R2 param=r value=3k\n" + TR1_2C6, 2.0),
                 ("same", PWL_SMALL_2C6 + "a1 alter dev=V1 param=wave value=[0 0 1u 1 3u 1]\na2 alter param=other value=2\n"
                  + TR1_2C6, 1.0)]
        for name, text, in2 in decks:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                self.assertGreater(self.same_as_xyce(name, dv, dx), 0)
                for d in (dv, dx):
                    self.assertAlmostEqual(self.at(d, name, "tr1", "in", 2e-6), in2, delta=1e-9)

    @needs_vacask
    @needs_xyce
    def test_k1_the_audit_s_other_forms_end_refused_or_right(self):
        """A sine's sinedc altered where it defaults to dc=vv, then a variable (VACASK in 0.5 + ... where Xyce 1 + ...); an
        exp's td1 altered beside a hooked td2, then a variable (VACASK's line gave td2 1u where 1.5u); a group's threshold
        altered under its other spelling, then a hooked change (VACASK Vd1:p -1.185e-4 where -6.555e-5): status 2 on
        VACASK, Xyce right.  A sweep of R1's r=rv around a sweep of rv: status 2 on both.  A sweep of the derived sinedc:
        status 2 on VACASK (exit 1 before), Xyce [0.25, 1]; of an exp's delay: status 2 on both (VACASK set its absolute
        td1, Xyce nothing).  The controls run equal."""
        a2 = "a2 alter param=other value=2\n"
        sine = SINE_2C6 + "a1 alter dev=V1 param=sinedc value=1\n" + a2 + "tr1 tran stop=1u\n"
        self.refused("sinedc", sine, "vacask", "alter a1 set sinedc of V1, whose default, the dc, the netlist binds to the "
                     "expression vv (it reads vv)", "not supported on VACASK in v1 (Xyce runs it)")
        dx, _ = self.run2c2("sinedc", sine, "xyce")
        self.assertAlmostEqual(self.at(dx, "sinedc", "tr1", "in", 0.0), 1.0, delta=1e-9)
        exp = EXP_2C6 % ("1u", "t2") + "a1 alter dev=V1 param=td2 value=2u\na2 alter dev=V1 param=td1 value=0.5u\n"
        self.refused("exptd", exp + "a3 alter param=other value=2\ntr1 tran stop=3u\n", "vacask",
                     "alter a2 set td1 of V1, so the control block's statement of V1 prints td2=1.5e-06, which its instance "
                     "line binds to the expression t2 - 1e-06 (it reads t2)", "not supported on VACASK in v1 (Xyce runs it)")
        dx, _ = self.run2c2("exptd", exp + "a3 alter param=other value=2\ntr1 tran stop=3u\n", "xyce")
        in3 = math.exp(-1.0) - math.exp(-5.0)                 # td1 0.5u tau1 0.5u, td2 2u tau2 1u, at 3u
        self.assertAlmostEqual(self.at(dx, "exptd", "tr1", "in", 3e-6), in3, delta=2e-3)
        dv, dx = self.both("exptdc", exp + "tr1 tran stop=3u\n")
        self.assertGreater(self.same_as_xyce("exptdc", dv, dx, rel=5e-3), 0)
        for card, alter in (("vtho", "vth0"), ("vth0", "vtho")):
            name = "grp_" + card
            text = GROUP_2C6 % card + ("op0 dc\na1 alter mod=nch param=%s value=0.6\na2 alter dev=R5 param=r value=3k\n"
                                       "op1 dc\n" % alter)
            with self.subTest(card=card, alter=alter):
                self.refused(name, text, "vacask", "dc op1: on VACASK, alter a1 set %s of model nch, which the netlist binds "
                             "to the expression vt (it reads vt)" % alter, "not supported on VACASK in v1 (Xyce runs it)")
                dx, _ = self.run2c2(name, text, "xyce")
                self.assertAlmostEqual(self.psf(dx, name, "op1.dc").value("Vd1:p"), -6.555e-5, delta=2e-8)
        div = ("// 2c6: R1's r=rv; out = 1k/(R1+1k)\nsimulator lang=spectre\nparameters rv=1k other=1\nV1 (in 0) vsource dc=1\n"
               "R1 (in out) resistor r=rv\nR2 (out 0) resistor r=1k\nR3 (in 0) resistor r=other*1k\nsave in out\n")
        # the nests of a bound target around what it reads: VACASK kept the swept value, Xyce re-evaluated the X line's
        # rr=rv and the source's dc=vv at the first point (out 0.333, in 2) and r=1k*(1+0.01*(temp-27)) at every point
        nests = [("nest", div + "sw1 sweep dev=R1 param=r values=[1k 3k] {\n  dc1 dc param=rv values=[1k 2k]\n}\n",
                  "sweep sw1 sets r of R1, which the netlist binds to the expression rv (it reads rv), and dc dc1 (its "
                  "sweep of rv) changes rv inside it"),
                 ("nestx", div.replace("R1 (in out) resistor r=rv", "subckt half (a b)\nparameters rr=1k\nR1 (a b) resistor "
                                       "r=rr\nends half\nX1 (in out) half rr=rv")
                  + "sw1 sweep sub=X1 param=rr values=[1k 3k] {\n  dc1 dc param=rv values=[1k 2k]\n}\n",
                  "sweep sw1 sets rr of X1, which the netlist binds to the expression rv (it reads rv)"),
                 ("nestv", div.replace("rv=1k", "rv=1k vv=1").replace("vsource dc=1", "vsource dc=vv").replace("r=rv",
                                                                                                          "r=1k")
                  + "sw1 sweep dev=V1 param=dc values=[1 3] {\n  dc1 dc param=vv values=[1 2]\n}\n",
                  "sweep sw1 sets dc of V1, which the netlist binds to the expression vv (it reads vv)"),
                 ("nestt", div.replace("r=rv", "r=1k*(1+0.01*(temp-27))")
                  + "sw1 sweep dev=R1 param=r values=[1k 3k] {\n  dc1 dc param=temp values=[27 127]\n}\n",
                  "sweep sw1 sets r of R1, which the netlist binds to the expression")]
        for engine in ("vacask", "xyce"):
            for name, text, head in nests:
                with self.subTest(construct=name, engine=engine):
                    self.refused(name, text, engine, head, "VACASK keeps it, Xyce re-evaluates the expression at some "
                                 "points", "not supported in v1")
            with self.subTest(construct="exp delay", engine=engine):
                self.refused("expdl", EXP_2C6 % ("1u", "10u") + "sw1 sweep dev=V1 param=delay values=[0 0.5u] {\n"
                             "  tr1 tran stop=3u\n}\n", engine, "the delay of the exp source V1 is folded into its td1 and "
                             "td2", "not supported in v1")
        dv, dx = self.both("nestc", div + "sw1 sweep param=rv values=[1k 2k] {\n  dc1 dc dev=R1 param=r values=[1k 3k]\n}\n")
        self.assertGreater(self.same_as_xyce("nestc", dv, dx), 0)
        # a hooked field altered, then swept through its hook (S-2c4's e12, a source in a body, and e13): the sweep sets the
        # field, so the statement's constant is no loss; out = V1/2, ac out = mag/2 (this round's first cut refused both)
        e12 = ("// 2c6 (S-2c4's e12): X1.V1's dc reads vd; out = V1/2\nsimulator lang=spectre\nparameters other=1\n"
               "subckt cell (o)\nparameters vd=1\nV1 (i 0) vsource dc=vd\nR1 (i o) resistor r=1k\nR2 (o 0) resistor r=1k\n"
               "ends cell\nX1 (out) cell vd=1\nR3 (out 0) resistor r=other*1G\nsave out\nop0 dc\n"
               "a1 alter dev=X1.V1 param=dc value=2\na2 alter param=other value=2\nop1 dc\n"
               "dc1 dc dev=X1.V1 param=dc values=[3 4]\nop2 dc\n")
        dv, dx = self.both("e12", e12)
        self.assertGreater(self.same_as_xyce("e12", dv, dx, rel=1e-6), 0)
        self.check_swept(dv, "e12", {"dc1": ("out", [1.5, 2.0])}, rel=1e-6, engine="vacask")
        for op, want in (("op0", 0.5), ("op1", 1.0), ("op2", 1.0)):
            self.assertAlmostEqual(self.psf(dv, "e12", op + ".dc").value("out"), want, delta=1e-6)
        e13 = ("// 2c6 (S-2c4's e13): V1's mag reads mm; ac out = mag/2\nsimulator lang=spectre\nparameters mm=1 other=1\n"
               "V1 (in 0) vsource dc=0 mag=mm\nR1 (in out) resistor r=1k\nR2 (out 0) resistor r=1k\n"
               "R3 (in 0) resistor r=other*1k\nsave out\nac0 ac start=1k stop=1k lin=1\na1 alter dev=V1 param=mag value=4\n"
               "a2 alter param=other value=2\nac1 ac start=1k stop=1k lin=1\nac2 ac dev=V1 param=mag values=[2 6] freq=1k\n")
        dv, dx = self.both("e13", e13)
        self.assertGreater(self.same_as_xyce("e13", dv, dx), 0)
        self.assertAlmostEqual(abs(self.psf(dv, "e13", "ac1.ac").column("out")[0]), 2.0, delta=1e-9)
        self.assertEqual([round(abs(x), 9) for x in self.psf(dv, "e13", "ac2.ac").column("out")], [1.0, 3.0])
        sweep = SINE_2C6 + "sw1 sweep dev=V1 param=sinedc values=[0.25 1] {\n  tr1 tran stop=1u\n}\n"
        self.refused("sinesw", sweep, "vacask", "on VACASK sinedc of V1 is printed on its instance line as sinedc=vv",
                     "VACASK cannot sweep such a parameter")
        dx, _ = self.run2c2("sinesw", sweep, "xyce")
        for k, want in ((0, 0.25), (1, 1.0)):
            self.assertAlmostEqual(self.psf(dx, "sinesw", "sw1-%03d_tr1.tran.tran" % k).at("in", 0.0), want, delta=1e-9)

    @needs_vacask
    @needs_xyce
    def test_k2_a_source_in_both_branches_of_an_if_on_both_engines(self):
        """Each branch taken in turn (sel=0: the else, val1=vv=2; sel=1: the if, val1=1), an alter of the width (N607 was
        refused at VACASK's render with an `(internal)' message) or of the bound val1 (S-2c5's N608), then a variable:
        status 0 on both engines, VACASK equal to Xyce, in = val1 on the flat top (1u-3u with width 2u, 1u-2u with
        width 1u) and 0 after the fall."""
        cases = [("w0", ("0", "width", "2u"), 2.5e-6, 2.0, 3.75e-6), ("w1", ("1", "width", "2u"), 2.5e-6, 1.0, 3.75e-6),
                 ("v0", ("0", "val1", "3"), 1.5e-6, 3.0, 3e-6), ("v1", ("1", "val1", "3"), 1.5e-6, 3.0, 3e-6)]
        for name, args, t_top, top, t_low in cases:
            with self.subTest(deck=name):
                dv, dx = self.both(name, COND_SRC_2C6 % args)
                self.assertGreater(self.same_as_xyce(name, dv, dx), 0)
                for d in (dv, dx):
                    self.assertAlmostEqual(self.at(d, name, "tr1", "in", t_top), top, delta=1e-9)
                    self.assertAlmostEqual(self.at(d, name, "tr1", "out", t_top), top / 2.0, delta=1e-9)
                    self.assertAlmostEqual(self.at(d, name, "tr1", "in", t_low), 0.0, delta=1e-9)


# -- phase 2c7: L1 the branch the run takes with the plan's variables, L2 §3.7's nominal structure, L3 a hooked
# sweep's value before its flag, L4 the source constraints in every state (bin/vamos on both engines) ------------------

# V1 in both branches of an if, the else's val1 reading vv; %s: the condition, then the statements before a1
COND_2C7 = """// 2c7: V1 in both branches of an if; in = val1 on the flat top (1u-3u), out = in/2, in(3.75u) = 0
simulator lang=spectre
parameters sel=0 vv=2 other=1
if (%s) {
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=0.5u rise=0.5u fall=0.5u width=1u period=20u
} else {
V1 (in 0) vsource type=pulse val0=0 val1=vv delay=0.5u rise=0.5u fall=0.5u width=1u period=20u
}
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
%sa1 alter dev=V1 param=width value=2u
a2 alter param=other value=2
tr1 tran stop=4u
"""
KINDS_2C7 = """// 2c7: X1 of two subckts in an if, X1.Z1 a resistor in cella, an inductor in cellb (sel=0)
simulator lang=spectre
parameters sel=%s other=1
subckt cella (a b)
Z1 (a b) resistor r=1k
ends cella
subckt cellb (a b)
Z1 (a b) inductor l=1u
ends cellb
V1 (in 0) vsource dc=1
if (sel > 0) {
X1 (in out) cella
} else {
X1 (in out) cellb
}
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out X1.Z1:1
%sop1 dc
"""
XM_2C7 = """// 2c7: X1 in both branches of an if with its own m each; out = 1k/(1k + 1k/(m_X1 * m_R1))
simulator lang=spectre
parameters sel=%s other=1
subckt half (a b)
R1 (a b) resistor r=1k
ends half
V1 (in 0) vsource dc=1
if (sel > 0) {
X1 (in out) half%s
} else {
X1 (in out) half%s
}
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
op0 dc
a1 alter dev=X1.R1 param=m value=2
op1 dc
"""
COUPLED_2C7 = """// 2c7: X1 of two subckts; cellb's L1 (l=1m m=2) alone; ac out = 500/(500 + j w L)
simulator lang=spectre
parameters sel=%s other=1
subckt cella (a b)
L1 (a b) inductor l=1m
L2 (b c) inductor l=1m
K1 mutual_inductor coupling=0.5 ind1=L1 ind2=L2
R9 (b 0) resistor r=1k
R10 (c 0) resistor r=1k
ends cella
subckt cellb (a b)
L1 (a b) inductor l=1m m=2
R9 (b 0) resistor r=1k
ends cellb
V1 (in 0) vsource dc=0 mag=1
if (sel > 0) {
X1 (in out) cella
} else {
X1 (in out) cellb
}
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save out
ac0 ac start=1k stop=1k lin=1
a1 alter dev=X1.L1 param=l value=2m
ac1 ac start=1k stop=1k lin=1
"""
TEMP_COND_2C7 = """// 2c7: R8 in both branches of an if reading temp (1k above 40 C, else 3k); o8 = 1k/(R8 + 1k)
simulator lang=spectre
parameters other=1
V1 (in 0) vsource dc=1
if (temp > 40) {
R8 (in o8) resistor r=1k
} else {
R8 (in o8) resistor r=3k
}
R9 (o8 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save o8
"""
BODY_COND_2C7 = """// 2c7: an if in a body chosen by the subckt's s (R1 1k for s > 0, else 3k); o1 = 1k/(R1 + 1k)
simulator lang=spectre
parameters other=1
subckt cellc (o)
parameters s=0
V1 (i 0) vsource dc=1
if (s > 0) {
R1 (i o) resistor r=1k
} else {
R1 (i o) resistor r=3k
}
R2 (o 0) resistor r=1k
ends cellc
X1 (o1) cellc
R3 (o1 0) resistor r=other*1G
save o1
"""
EXP_TD2_2C7 = """// 2c7 (S-2c6's S04, E08): an exp whose td2 reads t2 (hooked on VACASK); td1 0.5u, tau1 = tau2 = 2u
simulator lang=spectre
parameters t2=2u other=1
V1 (in 0) vsource type=exp val0=0 val1=1 td1=0.5u tau1=2u td2=t2 tau2=2u
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
SINE_FR_2C7 = """// 2c7 (S-2c6's E06): a sine whose freq reads fr (hooked on VACASK); in = 0.5 sin(2 pi f t)
simulator lang=spectre
parameters fr=100k other=1
V1 (in 0) vsource type=sine sinedc=0 ampl=0.5 freq=fr
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
R0224_2C7 = """// 2c7 (S-2c6's R0224): I1's td2 swept inside a sweep of t2; then tau1 altered: in = I1 * 375 (R0 || R3 || R1+R2)
simulator lang=spectre
parameters rv=1k other=1 td=0.5u tau=2u t2=2u
I1 (0 in) isource type=exp val0=0 val1=0.001 td1=td tau1=tau td2=t2 tau2=tau
R0 (in 0) resistor r=1k
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
sw1 sweep param=t2 values=[2 3] {
  dc1 dc dev=I1 param=td2 values=[2.5e-06 3.75e-06]
}
a1 alter dev=I1 param=tau1 value=3e-06
a2 alter dev=R2 param=r value=500
dc2 dc dev=R1 param=r values=[1000 3000]
tr1 tran stop=3u
"""
PULSE_WD_2C7 = """// 2c7: a pulse whose width, rise and fall read wd and rt, period 20u
simulator lang=spectre
parameters wd=1u rt=0.5u other=1
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=0.5u rise=rt fall=rt width=wd period=20u%s
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
"""
TR_2C7 = "tr1 tran stop=3u\n"


def _exp_2c7(t, td1, tau1, td2, tau2):
    """Spectre's exp from 0 to 1 (ref5 p.685): the rise from td1, the fall from td2."""
    v = 0.0 if t < td1 else 1.0 - math.exp(-(t - td1) / tau1)
    return v - (0.0 if t < td2 else 1.0 - math.exp(-(t - td2) / tau2))


class TestFollowUpRuns2c7(_Flow):
    """Phase 2c7 through bin/vamos.  L1: S-2c6's K2b1, K2b2 and K2b4 (an if whose condition reads a netlist parameter the
    deck alters or sweeps) were refused on VACASK with an `(internal)' message where Xyce gave the analytic values; a
    current save of a name an if and its else give two kinds failed on both engines (VACASK `Output variable 'i' ... not
    found', Xyce `undefined symbol in .PRINT command'); an X line's m and a coupled inductor took the first match.  L2:
    S-2c6's H05h gave in(2u) = 2 on both engines where 1 (an alter of the condition's parameter before the first analysis,
    taken as the nominal one).  L3: S04, E06, E08, R0224 (a hooked sweep of an exp's td2 or a sine's freq) stopped VACASK
    with exit 1.  L4: R0080 (a pulse's width altered past its period) stopped VACASK with exit 1 while Xyce ran it."""

    run2c2 = TestFollowUpRuns2c2.run2c2
    refused = TestFollowUpRuns2c2.refused
    both = TestFollowUpRuns2c4.both
    same_as_xyce = TestFollowUpRuns2c4.same_as_xyce
    value = TestFollowUpRuns2c4.value
    at = TestFollowUpRuns2c5.at

    def render_refused(self, name, text, engine, *pieces):
        """Status 2 at the render, before the engine ran: an error during circuit read-in holding every piece, no
        analysis data file (a sweep's PSF header may be there)."""
        d, out = self.run2c2(name, text, engine, rc=2)
        self.assertIn("Error from spectre during circuit read-in.", out)
        flat = " ".join(out.split())
        for piece in pieces:
            self.assertIn(piece, flat, "%s on %s:\n%s" % (name, engine, out[-3000:]))
        res = os.path.join(d, name + ".raw")
        self.assertFalse(os.path.isdir(res) and any(not f.endswith((".sweep", "logFile")) for f in os.listdir(res)), out)

    @needs_vacask
    @needs_xyce
    def test_l1_the_branch_the_run_takes_on_both_engines(self):
        """K2b4 (`if (other > 5)', other altered), K2b1 (sel swept by a dc keeping the else), K2b2 (sel altered to -1),
        K2b3 (sel=1 altered to 2) and an if kept by a condition on other: status 0 on both engines, VACASK equal to Xyce,
        in = val1 on the flat top (2 for the else, 1 for the if), out = in/2, 0 after the fall."""
        cases = [("k2b4", COND_2C7 % ("other > 5", ""), 2.0),
                 ("k2b1", COND_2C7 % ("sel > 0", "dc0 dc param=sel values=[-1 -0.5]\n"), 2.0),
                 ("k2b2", COND_2C7 % ("sel > 0", "a0 alter param=sel value=-1\n"), 2.0),
                 ("k2b3", (COND_2C7 % ("sel > 0", "a0 alter param=sel value=2\n")).replace("sel=0", "sel=1"), 1.0),
                 ("ifother", COND_2C7 % ("other > 0.5", ""), 1.0)]
        for name, text, top in cases:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                self.assertGreater(self.same_as_xyce(name, dv, dx), 0)
                for d in (dv, dx):
                    self.assertAlmostEqual(self.at(d, name, "tr1", "in", 2.5e-6), top, delta=1e-9)
                    self.assertAlmostEqual(self.at(d, name, "tr1", "out", 2.5e-6), top / 2.0, delta=1e-9)
                    self.assertAlmostEqual(self.at(d, name, "tr1", "in", 3.75e-6), 0.0, delta=1e-9)

    @needs_vacask
    @needs_xyce
    def test_l1_kinds_multipliers_and_coupled_inductors_on_the_taken_branch(self):
        """X1.Z1 a resistor in cella, an inductor in cellb: the current save is the taken kind's on both engines (1 mA through
        the inductor, 0.5 mA through the resistor), VACASK equal to Xyce.  X lines with their own m: the alter of X1.R1's m is
        refused on VACASK naming the taken X1's m, Xyce gives 1k/(1k + 1k/(m_X1*2)); with m on one X line only both engines
        give it.  cellb's lone L1 altered: both engines give 500/(500 + j w L); cella's coupled L1 stays refused on VACASK."""
        for sel, pre, cur, out in (("0", "", 1e-3, 1.0), ("1", "", 0.5e-3, 0.5), ("0", "a0 alter param=sel value=-1\n", 1e-3, 1.0)):
            name = "kinds%s%s" % (sel, "a" if pre else "")
            with self.subTest(deck=name):
                dv, dx = self.both(name, KINDS_2C7 % (sel, pre))
                self.assertGreater(self.same_as_xyce(name, dv, dx), 0)
                for d in (dv, dx):
                    self.assertAlmostEqual(self.psf(d, name, "op1.dc").value("X1.Z1:1"), cur, delta=1e-12)
                    self.assertAlmostEqual(self.psf(d, name, "op1.dc").value("out"), out, delta=1e-9)
        for sel, m in (("0", 3.0), ("1", 2.0)):
            name = "xm%s" % sel
            with self.subTest(deck=name):
                self.render_refused(name, XM_2C7 % (sel, " m=2", " m=3"), "vacask", "X1.R1: VACASK sets the device's "
                                    "$mfactor, which also carries the multiplier of the enclosing instance X1 (m=%s)" % m,
                                    "not supported on VACASK in v1 (Xyce runs it)")
                dx, _ = self.run2c2(name, XM_2C7 % (sel, " m=2", " m=3"), "xyce")
                for op, k in (("op0", 1.0), ("op1", 2.0)):
                    self.assertAlmostEqual(self.psf(dx, name, op + ".dc").value("out"), 1e3 / (1e3 + 1e3 / (m * k)),
                                           delta=1e-9)
        for sel, m in (("0", 3.0), ("1", 1.0)):
            name = "xm1%s" % sel
            with self.subTest(deck=name):
                dv, dx = self.both(name, XM_2C7 % (sel, "", " m=3"))
                self.assertGreater(self.same_as_xyce(name, dv, dx), 0)
                for d in (dv, dx):
                    for op, k in (("op0", 1.0), ("op1", 2.0)):
                        self.assertAlmostEqual(self.psf(d, name, op + ".dc").value("out"), 1e3 / (1e3 + 1e3 / (m * k)),
                                               delta=1e-9)
        dv, dx = self.both("coupled0", COUPLED_2C7 % "0")
        self.assertGreater(self.same_as_xyce("coupled0", dv, dx), 0)
        w = 2 * math.pi * 1e3
        for d in (dv, dx):
            for ac, ell in (("ac0", 0.5e-3), ("ac1", 1e-3)):
                got = self.psf(d, "coupled0", ac + ".ac").column("out")[0]
                want = 500.0 / complex(500.0, w * ell)
                self.assertAlmostEqual(abs(got - want), 0.0, delta=1e-9, msg=(ac, got, want))
        self.render_refused("coupled1", COUPLED_2C7 % "1", "vacask", "X1.L1: a coupled inductor's multiplier and TCs are "
                            "folded into its l on VACASK")
        self.run2c2("coupled1", COUPLED_2C7 % "1", "xyce")

    @needs_vacask
    @needs_xyce
    def test_l2_an_alter_that_changes_a_branch_before_the_first_analysis_is_refused_on_both(self):
        """H05h (sel altered to 1 before the first analysis, then V1's width: both engines gave in(2u) = 2 where Spectre's
        if branch gives 1), the same with val1=vv (H05), temp, a subckt's s: status 2 on both engines before any engine
        runs, naming the alter, the condition and the branches.  An alter that keeps the branch runs with the values of
        its taken branch: s=-1 (o1 1k/(3k + 1k), both engines); temp 30 under 40 (o8 0.25) and R8's r in the else (o8
        1/3) on VACASK, Xyce's emitter refusing an if that reads temp at read-in (as before this round, open)."""
        h05h = (COND_2C7.replace("val1=vv", "val1=2") % ("sel > 0", "a0 alter param=sel value=1\n")).replace(
            "a2 alter param=other value=2\n", "")
        # phase 2c8 (M4) re-decided the condition's text to the netlist's spelling (2c7: sel > 0.0, s > 0.0)
        cases = [("h05h", h05h, "top level", "sel > 0"),
                 ("h05", COND_2C7 % ("sel > 0", "a0 alter param=sel value=1\n"), "top level", "sel > 0"),
                 ("tmp", TEMP_COND_2C7 + "a0 alter param=temp value=50\nop1 dc\n", "top level", "temp > 40"),
                 ("bdy", BODY_COND_2C7 + "a0 alter sub=X1 param=s value=1\nop1 dc\n", "X1", "s > 0")]
        for engine in ("vacask", "xyce"):
            for name, text, path, cond in cases:
                with self.subTest(deck=name, engine=engine):
                    self.refused(name, text, engine, "alter a0 changes a structural condition on path %s: the condition %s"
                                 % (path, cond), "takes its if branch, where it takes its else branch at the netlist's own "
                                 "values", "vamos refuses a sweep or alter that changes an if branch, as Spectre does")
        rp = 1.0 / (1e-3 + 1e-9)                          # R2 || R3 (other*1G) under the body's R1
        keep = [("bdyk", BODY_COND_2C7 + "a0 alter sub=X1 param=s value=-1\nop1 dc\n", "o1", rp / (3e3 + rp), True),
                # Xyce's emitter refuses any if that reads temp at read-in (`temper is not a constant', open): VACASK only
                ("tmpk", TEMP_COND_2C7 + "a0 alter param=temp value=30\nop1 dc\n", "o8", 0.25, False),
                ("rk", TEMP_COND_2C7 + "a0 alter dev=R8 param=r value=2k\nop1 dc\n", "o8", 1.0 / 3.0, False)]
        for name, text, sig, want, on_xyce in keep:
            with self.subTest(deck=name):
                if on_xyce:
                    dv, dx = self.both(name, text)
                    self.assertGreater(self.same_as_xyce(name, dv, dx), 0)
                    runs = (dv, dx)
                else:
                    runs = (self.run2c2(name, text, "vacask")[0],)
                for d in runs:
                    self.assertAlmostEqual(self.psf(d, name, "op1.dc").value(sig), want, delta=1e-9)

    @needs_vacask
    @needs_xyce
    def test_l3_hooked_sweeps_of_fields_vacask_checks_at_setup_run(self):
        """S04 (an exp's td2 swept through its hook), E08 (the same in a dc, then a tran at the nominal), R0224 (td2 swept
        inside a sweep of t2): VACASK stopped with exit 1, the hook's value 0 when its flag was set; it runs now with the
        analytic values, and Xyce refuses the td2 sweep at render as before (its deck prints a derived value for td2).
        E06 (a sine's freq): both engines, equal to 0.5 sin(2 pi f t)."""
        s04 = EXP_TD2_2C7 + "sw1 sweep dev=V1 param=td2 values=[1.5u 2.5u] {\n  tr1 tran stop=3u\n}\n"
        dv, _ = self.run2c2("s04", s04, "vacask")
        for k, td2 in ((0, 1.5e-6), (1, 2.5e-6)):
            f = self.psf(dv, "s04", "sw1-%03d_tr1.tran.tran" % k)
            for t in (1e-6, 2e-6, 2.5e-6, 3e-6):
                self.assertAlmostEqual(f.at("in", t), _exp_2c7(t, 0.5e-6, 2e-6, td2, 2e-6), delta=2e-3, msg=(k, t))
        self.render_refused("s04", s04, "xyce", "V1: the vsource field td2 cannot be swept on Xyce in v1")
        e08 = EXP_TD2_2C7 + "dc1 dc dev=V1 param=td2 values=[1.5u 2.5u]\ntr1 tran stop=3u\n"
        dv, _ = self.run2c2("e08", e08, "vacask")
        self.assertEqual([round(x, 12) for x in self.psf(dv, "e08", "dc1.dc").column("in")], [0.0, 0.0])
        for t in (1e-6, 2e-6, 2.5e-6):
            self.assertAlmostEqual(self.at(dv, "e08", "tr1", "in", t), _exp_2c7(t, 0.5e-6, 2e-6, 2e-6, 2e-6), delta=2e-3)
        self.render_refused("e08", e08, "xyce", "V1: the vsource field td2 cannot be swept on Xyce in v1")
        dv, _ = self.run2c2("r0224", R0224_2C7, "vacask")
        for k in (0, 1):
            self.assertEqual([round(x, 12) for x in self.psf(dv, "r0224", "sw1-%03d_dc1.dc" % k).column("in")], [0.0, 0.0])
        for t in (1e-6, 2e-6, 2.5e-6):
            self.assertAlmostEqual(self.at(dv, "r0224", "tr1", "in", t), 1e-3 * 375.0 * _exp_2c7(t, 0.5e-6, 3e-6, 2e-6, 2e-6),
                                   delta=1e-3)
        self.render_refused("r0224", R0224_2C7, "xyce", "I1: the isource field td2 cannot be swept on Xyce in v1")
        e06 = SINE_FR_2C7 + "sw1 sweep dev=V1 param=freq values=[100k 200k] {\n  tr1 tran stop=3u\n}\n"
        dv, dx = self.both("e06", e06)
        for d in (dv, dx):
            for k, fr in ((0, 1e5), (1, 2e5)):
                f = self.psf(d, "e06", "sw1-%03d_tr1.tran.tran" % k)
                for t in (0.5e-6, 1.25e-6, 2e-6, 3e-6):
                    self.assertAlmostEqual(f.at("in", t), 0.5 * math.sin(2 * math.pi * fr * t), delta=2e-3, msg=(k, t))

    @needs_vacask
    @needs_xyce
    def test_l4_a_state_that_breaks_a_source_constraint_is_refused(self):
        """R0080 (width=wd altered to 2 s under period=20u, VACASK exit 1, Xyce ran it), a width altered or swept past the
        period, a negative rise, pwl times that do not increase, an exp's td2 before its td1: status 2 on both engines,
        no data file, naming the source, the constraint and the state (phase 2c8 re-decided p10, a pwl's first time point
        before 0, to both engines: vamos refuses the constant on both; and p04, a derived dc's pulse broken for an op: both
        engines read the waveform's value at t = 0).  A sine's freq altered to 0 and VACASK's first elaboration at an
        alter instance after a var broke the pulse: status 2 on VACASK, Xyce running them (in = 0.45 for the sine's
        constant).  The
        controls run equal: a valid width, a given dc's broken pulse under an op (VACASK prints type="dc"), a broken state
        an alter undoes before the tran."""
        r0080 = (PULSE_WD_2C7 % " dc=0.5" + "a1 alter param=rt value=2.5e-07\na2 alter param=wd value=2\n"
                 "sw1 sweep param=other values=[1 2] {\n  tr1 tran stop=3u\n}\na3 alter dev=R2 param=r value=2000\n"
                 "a4 alter param=other value=3\ndc1 dc dev=R3 param=r values=[1000 2000]\ntr2 tran stop=3u\n")
        period = "an explicit period no longer than rise+fall+width is an error (§3.8.1"
        both = [("r0080", r0080, "tran tr1 at other=1 (sweep sw1; after alter a2): the pulse of V1 has period=2e-05 s, not "
                 "longer than rise+fall+width = 2.0000005 s", period),
                ("p01", PULSE_WD_2C7 % "" + "a1 alter param=wd value=30u\n" + TR_2C7, "tran tr1 (after alter a1): the pulse "
                 "of V1 has period=2e-05 s", period),
                ("p02", PULSE_WD_2C7.replace("width=wd", "width=1u") % "" + "sw1 sweep dev=V1 param=width values=[1u 25u] "
                 "{\n  tr1 tran stop=3u\n}\n", "tran tr1 at V1:width=2.5e-05 (sweep sw1): the pulse of V1 has period=2e-05 s",
                 period),
                ("p08", PULSE_WD_2C7 % "" + "a1 alter param=rt value=-0.1u\n" + TR_2C7, "tran tr1 (after alter a1): the pulse "
                 "of V1 has rise=-1e-07 s", "a negative rise is an error"),
                ("p09", PULSE_WD_2C7.replace("type=pulse val0=0 val1=1 delay=0.5u rise=rt fall=rt width=wd period=20u",
                                             "type=pwl wave=[0 0 tp 1 3u 1]").replace("wd=1u", "tp=1u") % ""
                 + "a1 alter param=tp value=4u\n" + TR_2C7, "tran tr1 (after alter a1): the pwl of V1 has time points that do "
                 "not increase (4e-06 s, then 3e-06 s)", "pwl time points must increase"),
                ("p07", EXP_TD2_2C7.replace("tau1=2u", "tau1=1u") + "a1 alter param=t2 value=0.25u\n" + TR_2C7,
                 "tran tr1 (after alter a1): the exp of V1 has td2=2.5e-07 s, not later than its td1=5e-07 s",
                 "an exp whose td2 is not later than its td1 is an error in v1"),
                # phase 2c8 (M1) re-decided p10 from VACASK only: vamos refuses a constant pwl time point before 0 on both
                # engines, so it refuses one in every state on both (Xyce ran it)
                ("p10", PULSE_WD_2C7.replace("type=pulse val0=0 val1=1 delay=0.5u rise=rt fall=rt width=wd period=20u",
                                             "type=pwl wave=[t0 0 1u 1 3u 1]").replace("wd=1u", "t0=0") % ""
                 + "a1 alter param=t0 value=-1u\n" + TR_2C7, "tran tr1 (after alter a1): the pwl of V1 has its first time "
                 "point at -1e-06 s", "a pwl time point before 0 is an error, as the constant is (§3.8.1"),
                # phase 2c8 (M1, M4) re-decided p04 from VACASK only: an op whose dc is derived reads the waveform on both
                # engines, so the rule refuses it on both (Xyce ran it)
                ("p04", PULSE_WD_2C7 % "" + "a1 alter param=wd value=30u\nop1 dc\n", "dc op1 (after alter a1): the pulse of "
                 "V1 has period=2e-05 s", period + "; its dc is the waveform's value at t = 0)")]
        for engine in ("vacask", "xyce"):
            for name, text, head, piece in both:
                with self.subTest(deck=name, engine=engine):
                    self.refused(name, text, engine, head, piece)
        vacask_only = [("p05", SINE_FR_2C7.replace("sinedc=0", "sinedc=0.2").replace("freq=fr", "freq=fr sinephase=30")
                        + "a1 alter param=fr value=0\n" + TR_2C7, "tran tr1 (after alter a1): on VACASK the sine of V1 has "
                        "freq=0 Hz"),
                       ("p12", PULSE_WD_2C7 % "" + "a1 alter param=wd value=30u\na2 alter dev=R2 param=r value=2k\n"
                        "a3 alter param=wd value=1u\n" + TR_2C7, "alter a2, VACASK's first elaboration (after alter a1): on "
                        "VACASK the pulse of V1 has period=2e-05 s")]
        for name, text, head in vacask_only:
            with self.subTest(deck=name, engine="vacask"):
                self.refused(name, text, "vacask", head, "not supported on VACASK in v1 (Xyce runs it)")
            with self.subTest(deck=name, engine="xyce"):
                dx, _ = self.run2c2(name, text, "xyce")
                if name == "p05":
                    self.assertAlmostEqual(self.at(dx, name, "tr1", "in", 1e-6), 0.45, delta=1e-9)
                if name == "p12":                         # the final state: width 1u, R2 2k
                    self.assertAlmostEqual(self.at(dx, name, "tr1", "out", 1.5e-6), 2.0 / 3.0, delta=1e-9)
        controls = [("c01", PULSE_WD_2C7 % "" + "a1 alter param=wd value=1.5u\n" + TR_2C7, "tr1", 2e-6, 1.0),
                    ("c02", PULSE_WD_2C7 % " dc=0.5" + "a1 alter param=wd value=30u\nop1 dc\n", "op1", None, 0.5),
                    ("c03", PULSE_WD_2C7 % "" + "a1 alter param=wd value=30u\na2 alter param=wd value=1u\n" + TR_2C7, "tr1",
                     1.5e-6, 1.0)]
        for name, text, analysis, t, want in controls:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                self.assertGreater(self.same_as_xyce(name, dv, dx), 0)
                for d in (dv, dx):
                    got = self.at(d, name, analysis, "in", t) if t is not None else self.psf(d, name, "op1.dc").value("in")
                    self.assertAlmostEqual(got, want, delta=1e-9)


# -- phase 2c8: the 2c7 skeptic's decks (its sets p7c, p7d, p7e, g7a, g7b); V1 drives in, out = in/2
HEAD_2C8 = "simulator lang=spectre\nparameters other=1 ft=0.5u wd=2u tau=1u fr=100k dl=0.5u\n"
LOAD_2C8 = "R1 (in out) resistor r=1k\nR2 (out 0) resistor r=1k\nR3 (in 0) resistor r=other*1k\nsave in out\n"
TR_2C8 = "tr1 tran stop=3.5u maxstep=2n\n"


def _pulse_2c8(fall="0.5u", width="2u", tail=TR_2C8, delay="0.5u", rise="0.5u"):
    return (HEAD_2C8 + "V1 (in 0) vsource type=pulse val0=0 val1=1 delay=%s rise=%s fall=%s width=%s period=20u\n"
            % (delay, rise, fall, width) + LOAD_2C8 + tail)


def _sine_2c8(fields="sinedc=0.2 ampl=0.5 freq=100k", tail=TR_2C8):
    return HEAD_2C8 + "V1 (in 0) vsource type=sine %s\n" % fields + LOAD_2C8 + tail


def _exp_2c8(tau1="1u", tau2="1u", tail=TR_2C8, td1="0.5u"):
    return (HEAD_2C8 + "V1 (in 0) vsource type=exp val0=0 val1=1 td1=%s tau1=%s td2=2u tau2=%s\n" % (td1, tau1, tau2)
            + LOAD_2C8 + tail)


def _sweep_2c8(head, values):
    return "sw1 sweep %s values=[%s] {\n  tr1 tran stop=3.5u maxstep=2n\n}\n" % (head, values)


S0290_RUN_2C8 = """// S-2c7's S0290 (g7a): fall=rt altered to -1e-07, the width past the period and back to 2u, R2 2k
simulator lang=spectre
parameters other=1 wd=1u rt=0.5u per=20u fr=100k dm=0 td=0.5u t2=2u tp=1u t0=0
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=0.5u rise=0.5u fall=rt width=1u period=per mag=1
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
a1 alter param=rt value=-1e-07
a2 alter dev=V1 param=width value=3e-05
a3 alter dev=R2 param=r value=2000
a4 alter dev=V1 param=width value=2e-06
tr1 tran stop=3u maxstep=2n
tr2 tran stop=3u maxstep=2n
"""
S0013_RUN_2C8 = """// S-2c7's S0013 (g7a): sinedc=0 ampl=0.5 sinephase=30 dc=0.5, freq altered to 0: op 0.5, tran 0.25
simulator lang=spectre
parameters other=1 wd=1u rt=0.5u per=20u fr=100k dm=0 td=0.5u t2=2u tp=1u t0=0
V1 (in 0) vsource type=sine sinedc=0 ampl=0.5 freq=fr sinephase=30 dc=0.5
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
a1 alter dev=V1 param=freq value=0
tr1 tran stop=3u maxstep=2n
op1 dc
tr2 tran stop=3u maxstep=2n
"""
S0110_RUN_2C8 = """// S-2c7's S0110 (g7a): a dev= sweep of a sine's freq to -50k
simulator lang=spectre
parameters other=1 wd=1u rt=0.5u per=20u fr=100k dm=0 td=0.5u t2=2u tp=1u t0=0
V1 (in 0) vsource type=sine sinedc=0.2 ampl=0.5 freq=fr
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
R3 (in 0) resistor r=other*1k
save in out
sw1 sweep dev=V1 param=freq values=[200000 -50000] {
  tr1 tran stop=3u maxstep=2n
}
tr2 tran stop=3u maxstep=2n
tr3 tran stop=3u maxstep=2n
"""


class TestFollowUpRuns2c8(_Flow):
    """Phase 2c8 through bin/vamos on both engines.  M1: a negative pulse fall through a netlist parameter, a param= or a
    dev= sweep ran on both engines with status 0 and different values (VACASK never fell, Xyce ended the plateau at 2.9
    us); a negative width likewise; a swept rise or fall of 0 stopped VACASK (rise) or left it at val1 (fall).  M2: a
    sine whose freq is the constant 0 gave its folded constant to every DC-type solve, where Spectre gives the given dc.
    M3: an exp time constant of 0 stopped VACASK with NaN.  M4: a negative freq was VACASK's refusal alone while Xyce ran
    a sweep to it."""

    run2c2 = TestFollowUpRuns2c2.run2c2
    refused = TestFollowUpRuns2c2.refused
    both = TestFollowUpRuns2c4.both
    same_as_xyce = TestFollowUpRuns2c4.same_as_xyce
    at = TestFollowUpRuns2c5.at

    def refused_both(self, cases):
        for engine in ("vacask", "xyce"):
            for name, text, *pieces in cases:
                with self.subTest(deck=name, engine=engine):
                    self.refused(name, text, engine, *pieces)

    def op_in(self, d, name, analysis="op1"):
        return self.psf(d, name, analysis + ".dc").value("in")

    @needs_vacask
    @needs_xyce
    def test_m1_negative_falls_and_widths_are_refused_and_a_swept_edge_of_0_runs(self):
        """N07, N09, N10, N12, S0290 and its reference (a negative fall), C01 and N02 (a negative width): status 2 on both
        engines, no data file.  A dev= sweep of the fall or the rise through 0: status 0 on both engines, VACASK equal to
        Xyce, the 0 point Spectre's default edge (the fall at 3 us within 4 ns, the rise at 0.5 us)."""
        fall = "a negative fall is an error (§3.8.1"
        width = "a negative width is an error in v1"
        ref = S0290_RUN_2C8.replace("rt=0.5u", "rt=-1e-07").replace("width=1u", "width=2e-06").replace(
            "R2 (out 0) resistor r=1k", "R2 (out 0) resistor r=2000").split("a1 alter")[0] + "ref tran stop=3u maxstep=2n\n"
        self.refused_both([
            ("n07", _pulse_2c8("ft", tail="a1 alter param=ft value=-0.1u\n" + TR_2C8), "tran tr1 (after alter a1): the "
             "pulse of V1 has fall=-1e-07 s", fall),
            ("n09", _pulse_2c8(tail="a1 alter dev=V1 param=fall value=-0.1u\n" + TR_2C8), "tran tr1 (after alter a1): the "
             "pulse of V1 has fall=-1e-07 s", fall),
            ("n10", _pulse_2c8(tail=_sweep_2c8("dev=V1 param=fall", "-0.1u 0.5u")), "tran tr1 at V1:fall=-1e-07 (sweep "
             "sw1)", fall),
            ("n12", _pulse_2c8("ft", tail=_sweep_2c8("param=ft", "0.5u -0.1u")), "tran tr1 at ft=-1e-07 (sweep sw1)", fall),
            ("s0290", S0290_RUN_2C8, "tran tr1 (after alter a1): the pulse of V1 has fall=-1e-07 s", fall),
            ("s0290r", ref, "tran ref: the pulse of V1 has fall=-1e-07 s", fall),
            ("c01", _pulse_2c8(width="-1u"), "tran tr1: the pulse of V1 has width=-1e-06 s", width),
            ("n02", _pulse_2c8(width="wd", tail="a1 alter param=wd value=-1u\n" + TR_2C8), "tran tr1 (after alter a1): the "
             "pulse of V1 has width=-1e-06 s", width)])
        for param, t_on, t_edge in (("fall", 2.0e-6, 3.0e-6), ("rise", 0.6e-6, 0.5e-6)):
            name = "edge0_" + param
            with self.subTest(deck=name):
                dv, dx = self.both(name, _pulse_2c8(tail=_sweep_2c8("dev=V1 param=%s" % param, "0 0.5u")))
                for d in (dv, dx):
                    f = self.psf(d, name, "sw1-000_tr1.tran.tran")
                    self.assertAlmostEqual(f.at("in", t_on), 1.0, delta=1e-6, msg=(param, d))
                    if param == "fall":
                        self.assertAlmostEqual(f.at("in", t_edge + 4e-9), 0.0, delta=1e-6, msg=d)
                    else:
                        self.assertAlmostEqual(f.at("in", t_edge - 1e-8), 0.0, delta=1e-6, msg=d)
                    g = self.psf(d, name, "sw1-001_tr1.tran.tran")
                    self.assertAlmostEqual(g.at("in", 0.75e-6), 0.5, delta=1e-6, msg=d)    # the 0.5u edges: mid-rise

    @needs_vacask
    @needs_xyce
    def test_m2_a_constant_sine_keeps_its_given_dc_on_both_engines(self):
        """Z01 (freq=0 with dc=0.25: op 0.25, tran 0), Z02 and Z06 (freq altered to 0), S0013 (sinephase 30, dc 0.5: tran
        0.25, op 0.5), the references S0013__op1 and T0255's (op of a constant sine with a given dc), and the same sine
        without a dc (op = tran = sinedc + ampl sin(30 deg)): status 0 on both engines, VACASK equal to Xyce.  A dev=
        sweep of its ampl, sinedc, sinephase, delay or freq runs the sine at each point on both engines (a freq point of
        0 the constant); a damp point other than 0 is refused on both."""
        c30 = 0.5 * math.sin(math.pi / 6.0)
        cases = [("z01", _sine_2c8("sinedc=0 ampl=0.5 freq=0 dc=0.25", "op1 dc\n" + TR_2C8), {"op1": 0.25}, {"tr1": 0.0}),
                 ("z02", _sine_2c8("sinedc=0 ampl=0.5 freq=100k dc=0.25", "a1 alter dev=V1 param=freq value=0\nop1 dc\n"
                                   + TR_2C8), {"op1": 0.25}, {"tr1": 0.0}),
                 ("z06", _sine_2c8("sinedc=0 ampl=0.5 freq=100k dc=0.25", "op0 dc\na1 alter dev=V1 param=freq value=0\n"
                                   "op1 dc\n"), {"op0": 0.25, "op1": 0.25}, {}),
                 ("s0013", S0013_RUN_2C8, {"op1": 0.5}, {"tr1": c30, "tr2": c30}),
                 ("s0013r", S0013_RUN_2C8.replace("freq=fr sinephase", "freq=0 sinephase").split("a1 alter")[0] + "op1 dc\n",
                  {"op1": 0.5}, {}),
                 ("t0255r", _sine_2c8("sinedc=0 ampl=0.5 freq=0 dc=0.25", "op1 dc\n"), {"op1": 0.25}, {}),
                 ("nodc", _sine_2c8("sinedc=0.2 ampl=0.5 freq=0 sinephase=30", "op1 dc\n" + TR_2C8), {"op1": 0.2 + c30},
                  {"tr1": 0.2 + c30})]
        for name, text, ops, trans in cases:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                self.assertGreater(self.same_as_xyce(name, dv, dx), 0)
                for d in (dv, dx):
                    for op, want in ops.items():
                        self.assertAlmostEqual(self.op_in(d, name, op), want, delta=1e-9, msg=(name, op, d))
                    for tr, want in trans.items():
                        for t in (0.0, 1e-6, 2.9e-6):
                            self.assertAlmostEqual(self.at(d, name, tr, "in", t), want, delta=1e-9, msg=(name, tr, t))
        # a dev= sweep of its fields reaches the sine itself on both engines (2c7: the nominal constant at every point):
        # sinedc + ampl sin(sinephase) at each point, before the delay and after it; a freq point a real sine
        fields = "sinedc=0.2 ampl=0.5 freq=0 sinephase=30"
        swept = [("swampl", "ampl", "0.5 1", [0.2 + c30, 0.2 + 2.0 * c30]),
                 ("swdc", "sinedc", "0 0.3", [c30, 0.3 + c30]),
                 ("swphase", "sinephase", "0 90", [0.2, 0.7]),
                 ("swdelay", "delay", "0 0.3u", [0.2 + c30, 0.2 + c30]),
                 ("swdamp0", "damp", "0 0", [0.2 + c30, 0.2 + c30])]
        for name, field, values, wants in swept:
            with self.subTest(deck=name):
                dv, dx = self.both(name, _sine_2c8(fields, _sweep_2c8("dev=V1 param=%s" % field, values)))
                for d in (dv, dx):
                    for k, want in enumerate(wants):
                        f = self.psf(d, name, "sw1-%03d_tr1.tran.tran" % k)
                        for t in (0.0, 1e-6, 2.9e-6):
                            self.assertAlmostEqual(f.at("in", t), want, delta=1e-9, msg=(name, d, k, t))
        with self.subTest(deck="swfreq"):              # a point of 0: the constant (VACASK runs its own 1e-30)
            dv, dx = self.both("swfreq", _sine_2c8(fields, _sweep_2c8("dev=V1 param=freq", "0 100k 200k")))
            for d in (dv, dx):
                for k, f0 in ((0, 0.0), (1, 1e5), (2, 2e5)):
                    f = self.psf(d, "swfreq", "sw1-%03d_tr1.tran.tran" % k)
                    for t in (0.0, 1e-6, 2.9e-6):
                        want = 0.2 + 0.5 * math.sin(2.0 * math.pi * f0 * t + math.pi / 6.0)
                        self.assertAlmostEqual(f.at("in", t), want, delta=1e-4, msg=(d, k, t))
        self.refused_both([("swdamp", _sine_2c8(fields, _sweep_2c8("dev=V1 param=damp", "0 1e5")),
                            "sweep sw1 at V1:damp=100000: the sine of V1 has freq=0 and damp=100000: ",
                            "a sine with freq=0 and damp != 0 is not a constant: give the frequency (§3.8.1)")])

    @needs_vacask
    @needs_xyce
    def test_m3_an_exp_time_constant_of_0_is_refused_on_both_engines(self):
        """C05 (tau1=0), N05 (tau=1u altered to 0), a constant tau2=0: status 2 on both engines (VACASK stopped with NaN,
        Xyce made a step for tau1 and stopped for tau2: 'time step too small'), naming the field, and an op whose derived
        dc reads the exp at its start (VACASK's NaN; Xyce printed the first value); with a given dc the op runs.  A
        negative tau runs equal on both engines (the exp's formula)."""
        rule = "an exp time constant of 0 is an error in v1"
        self.refused_both([
            ("c05", _exp_2c8("0"), "tran tr1: the exp of V1 has tau1=0 s", rule),
            ("n05", _exp_2c8("tau", tail="a1 alter param=tau value=0\n" + TR_2C8), "tran tr1 (after alter a1): the exp of "
             "V1 has tau1=0 s", rule),
            ("t2c", _exp_2c8(tau2="0"), "tran tr1: the exp of V1 has tau2=0 s", rule),
            ("opdc", _exp_2c8("tau", tail="a1 alter param=tau value=0\nop1 dc\n", td1="0"), "dc op1 (after alter a1): the "
             "exp of V1 has tau1=0 s", rule + " (ref5 p.685 gives no exp formula that defines it; its dc is the waveform's "
             "value at t = 0)")])
        dv, dx = self.both("opgiven", _exp_2c8("tau dc=0.3", tail="a1 alter param=tau value=0\nop1 dc\n", td1="0"))
        for d in (dv, dx):
            self.assertAlmostEqual(self.op_in(d, "opgiven"), 0.3, delta=1e-9, msg=d)      # a given dc reads no waveform
        dv, dx = self.both("negtau", _exp_2c8("-1u"))
        for d in (dv, dx):
            for t in (0.4e-6, 1.0e-6, 1.6e-6):
                want = 1.0 - math.exp((t - 0.5e-6) / 1e-6) if t > 0.5e-6 else 0.0
                self.assertAlmostEqual(self.at(d, "negtau", "tr1", "in", t), want, delta=2e-3 * max(1.0, abs(want)))
        # a dev= sweep of tau1 (Xyce refused it at render before): both engines, the exp's formula at each point
        dv, dx = self.both("tausw", _exp_2c8(tail=_sweep_2c8("dev=V1 param=tau1", "1u 2u")))
        for d in (dv, dx):
            for k, tau in ((0, 1e-6), (1, 2e-6)):
                f = self.psf(d, "tausw", "sw1-%03d_tr1.tran.tran" % k)
                for t in (1.0e-6, 1.8e-6, 2.5e-6):
                    want = 1.0 - math.exp(-(t - 0.5e-6) / tau) - (1.0 - math.exp(-(t - 2e-6) / 1e-6) if t > 2e-6 else 0.0)
                    self.assertAlmostEqual(f.at("in", t), want, delta=2e-3, msg=(d, k, t))

    @needs_vacask
    @needs_xyce
    def test_m4_a_negative_freq_is_refused_on_both_and_a_negative_delay_s_dc_is_its_t0_value(self):
        """N13 (an alter of freq to -50k), N14 and S0110 (a dev= sweep to -50k): status 2 on both engines with the
        constant's rule.  A negative delay puts t = 0 inside the waveform: an op after it reads the waveform's t = 0 value
        on both engines (Xyce printed the first value: 0 where 0.4 for the pulse, 0.1 for the sine, 0 for the pwl and the
        exp), through a constant and through an altered netlist parameter (a pwl's delay too); a pwl's first time point
        altered below 0 is the rule's refusal on both engines before such an op."""
        rule = "a sine with a negative freq is an error in v1, as the constant is"
        self.refused_both([
            ("n13", _sine_2c8(tail="a1 alter dev=V1 param=freq value=-50k\n" + TR_2C8), "tran tr1 (after alter a1): the "
             "sine of V1 has freq=-50000 Hz", rule),
            ("n14", _sine_2c8(tail=_sweep_2c8("dev=V1 param=freq", "100k -50k")), "tran tr1 at V1:freq=-50000 (sweep sw1)",
             rule),
            ("s0110", S0110_RUN_2C8, "tran tr1 at V1:freq=-50000 (sweep sw1)", rule)])
        pwl = HEAD_2C8 + "V1 (in 0) vsource type=pwl wave=[0 0 1u 1 3u 1] delay=-0.5u\n" + LOAD_2C8 + "op1 dc\n"
        cases = [("pdl", _pulse_2c8(delay="-0.2u", tail="op1 dc\n"), 0.4),
                 ("pdlp", _pulse_2c8(delay="dl", tail="op0 dc\na1 alter param=dl value=-0.2u\nop1 dc\n"), 0.4),
                 ("sdl", _sine_2c8("sinedc=0.1 ampl=0.5 freq=100k delay=-0.3u", "op1 dc\n"),
                  0.1 + 0.5 * math.sin(2 * math.pi * 1e5 * 0.3e-6)),
                 ("wdl", pwl, 0.5),
                 ("edl", _exp_2c8(td1="-0.5u", tail="op1 dc\n"), 1.0 - math.exp(-0.5))]
        for name, text, want in cases:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                for d in (dv, dx):
                    self.assertAlmostEqual(self.op_in(d, name), want, delta=1e-9, msg=(name, d))
        # a pwl whose delay reads a parameter altered to -0.5u: the op after it reads the value at t = 0 between (-0.5u, 0)
        # and (0.5u, 1), 0.5, on both engines (Xyce's dc a ternary on the shifted time points); a first time point that
        # reads a parameter altered to -0.5u is the rule's refusal on both (VACASK alone before, Xyce giving 1/3)
        pwld = (HEAD_2C8.replace("dl=0.5u", "dl=0") + "V1 (in 0) vsource type=pwl wave=[0 0 1u 1 3u 1] delay=dl\n"
                + LOAD_2C8 + "op0 dc\na1 alter param=dl value=-0.5u\nop1 dc\n")
        dv, dx = self.both("pwld", pwld)
        for d in (dv, dx):
            self.assertAlmostEqual(self.op_in(d, "pwld", "op0"), 0.0, delta=1e-9, msg=d)
            self.assertAlmostEqual(self.op_in(d, "pwld"), 0.5, delta=1e-9, msg=d)
        pwlp = (HEAD_2C8.replace("dl=0.5u", "dl=0.5u t0=0") + "V1 (in 0) vsource type=pwl wave=[t0 0 1u 1 3u 1]\n"
                + LOAD_2C8 + "op0 dc\na1 alter param=t0 value=-0.5u\nop1 dc\n")
        self.refused_both([("pwlp", pwlp, "dc op1 (after alter a1): the pwl of V1 has its first time point at -5e-07 s",
                            "a pwl time point before 0 is an error, as the constant is (§3.8.1; its dc is the waveform's "
                            "value at t = 0)")])


# -- phase 2c9: the 2c8 skeptic's decks (its sets p8, p8b, p8c, p8e, g8sa, g8sb) -----------------
SINE_2C9 = "sinedc=0.2 ampl=0.5 freq=200k sinephase=30"
ALTER0_2C9 = "a1 alter dev=V1 param=freq value=0\n"
C45_2C9 = 0.2 + 0.5 * math.sin(math.pi / 6.0)           # the constant sine sinedc=0.2 ampl=0.5 sinephase=30: 0.45


def _block_2c9(head, values, body="tr1 tran stop=3.5u maxstep=2n"):
    return "sw1 sweep %s values=[%s] {\n  %s\n}\n" % (head, values, body)


def _src_2c9(line, tail, params=""):
    return HEAD_2C8.replace("dl=0.5u", "dl=0.5u" + params) + line + "\n" + LOAD_2C8 + tail


def _sine_at_2c9(t, dc, freq, ampl=0.5, phase_deg=0.0):
    return dc + ampl * math.sin(2.0 * math.pi * freq * t + math.radians(phase_deg))


class TestFollowUpRuns2c9(_Flow):
    """Phase 2c9 through bin/vamos on both engines.  N1: a sine an alter made the constant sine, then a dev= sweep of its
    freq through 0, stopped VACASK (`Frequency of sinusoidal transient must be greater than 0', exit 1; S-2c8's P873
    and Q05 ran right in 2c7).  N2: a dc analysis sweeping a rise, a fall or the constant sine's freq through 0 wrote
    the engines' transres or 1e-30 to the PSF axis.  N3: a dev= sweep of dc left a sinedc that defaults to dc where it
    was (P824, P825).  N4: Xyce repeated a pulse without a period whose delay is negative (P868, G10127).  N5: VACASK
    stopped at its setup before a sweep's first point (G20073), and tau2's refusal gave tau1's clause."""

    run2c2 = TestFollowUpRuns2c2.run2c2
    refused = TestFollowUpRuns2c2.refused
    both = TestFollowUpRuns2c4.both
    same_as_xyce = TestFollowUpRuns2c4.same_as_xyce
    at = TestFollowUpRuns2c5.at
    op_in = TestFollowUpRuns2c8.op_in

    def tran_is(self, d, name, fn, want, times=(0.0, 0.4e-6, 1.0e-6, 2.1e-6, 2.9e-6), delta=1e-4):
        f = self.psf(d, name, fn)
        for t in times:
            self.assertAlmostEqual(f.at("in", t), want(t), delta=delta, msg=(d, fn, t))

    def axis(self, d, name, fn):
        return self.psf(d, name, fn).sweep_values()

    @needs_vacask
    @needs_xyce
    def test_n1_a_sine_an_alter_made_the_constant_sine_sweeps_its_freq_through_0_on_both_engines(self):
        """P873 (a dc analysis), Q05 (around an op), P870 (around a tran), P871 (a given dc) and P874 (an alter of the
        type to sine without a freq): status 0 on both engines, VACASK equal to Xyce, the point of 0 the constant 0.45
        (the dc analysis's and op's derived dc, the tran's constant), the 100 kHz point the sine.  P872 (the constant sine
        an alter made a real sine, swept through 0): VACASK's refusal alone, Xyce's SIN at freq 0 the constant."""
        const = lambda t: C45_2C9                                                      # noqa: E731
        sine = lambda t: _sine_at_2c9(t, 0.2, 1e5, phase_deg=30.0)                     # noqa: E731
        p873 = _sine_2c8(SINE_2C9, ALTER0_2C9 + "dc1 dc dev=V1 param=freq values=[0 100k]\n")
        dv, dx = self.both("p873", p873)
        self.assertGreater(self.same_as_xyce("p873", dv, dx), 0)
        for d in (dv, dx):
            f = self.psf(d, "p873", "dc1.dc")
            self.assertEqual(f.sweep_values(), [0.0, 1e5])                                # N2: the deck's 0
            for v in f.column("in"):
                self.assertAlmostEqual(v, C45_2C9, delta=1e-9, msg=d)
        q05 = _sine_2c8(SINE_2C9, ALTER0_2C9 + _block_2c9("dev=V1 param=freq", "0 100k", "op1 dc"))
        dv, dx = self.both("q05", q05)
        self.assertGreater(self.same_as_xyce("q05", dv, dx), 0)
        for d in (dv, dx):
            for k in (0, 1):
                self.assertAlmostEqual(self.op_in(d, "q05", "sw1-%03d_op1" % k), C45_2C9, delta=1e-9, msg=(d, k))
        trans = [("p870", _sine_2c8(SINE_2C9, ALTER0_2C9 + _block_2c9("dev=V1 param=freq", "0 100k")), "tr1", {}),
                 ("p871", _sine_2c8(SINE_2C9 + " dc=0.25", ALTER0_2C9 + "op1 dc\n" + _block_2c9(
                     "dev=V1 param=freq", "0 100k", "tr2 tran stop=3.5u maxstep=2n")), "tr2", {"op1": 0.25}),
                 ("p874", _src_2c9("V1 (in 0) vsource type=dc dc=0.25 sinedc=0.2 ampl=0.5 sinephase=30",
                                   "a1 alter dev=V1 param=type value=sine\nop1 dc\n" + _block_2c9(
                                       "dev=V1 param=freq", "0 100k", "tr2 tran stop=3.5u maxstep=2n")), "tr2",
                  {"op1": 0.25})]
        for name, text, tr, ops in trans:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                self.assertGreater(self.same_as_xyce(name, dv, dx, rel=1e-4), 0)
                for d in (dv, dx):
                    for op, want in ops.items():
                        self.assertAlmostEqual(self.op_in(d, name, op), want, delta=1e-9, msg=(name, d))
                    self.tran_is(d, name, "sw1-000_%s.tran.tran" % tr, const)
                    self.tran_is(d, name, "sw1-001_%s.tran.tran" % tr, sine)
        p872 = _sine_2c8("sinedc=0.2 ampl=0.5 freq=0 sinephase=30", "a1 alter dev=V1 param=freq value=100k\n"
                         + _block_2c9("dev=V1 param=freq", "0 200k"))
        self.refused("p872", p872, "vacask", "tran tr1 at V1:freq=0 (sweep sw1; after alter a1): on VACASK the sine of "
                     "V1 has freq=0 Hz", "not supported on VACASK in v1 (Xyce runs it)")
        dx, _ = self.run2c2("p872", p872, "xyce")
        self.tran_is(dx, "p872", "sw1-000_tr1.tran.tran", const)
        self.tran_is(dx, "p872", "sw1-001_tr1.tran.tran", lambda t: _sine_at_2c9(t, 0.2, 2e5, phase_deg=30.0))

    @needs_vacask
    @needs_xyce
    def test_n2_the_sweep_axis_holds_the_deck_s_0_on_every_analysis_kind(self):
        """E01 (a dc analysis sweeping a rise through 0), E04 (a fall, with a tran: transres 3.5e-15), E02 and P832 (the
        constant sine's freq through 0, without and with a given dc), an ac and a noise at a fixed frequency sweeping a
        rise, an xf (VACASK) sweeping the constant sine's freq, T0132 (a bound rise, Xyce; VACASK refuses the sweep of a
        bound rise): the PSF axis reads the deck's 0 on both engines (2c8: 1e-09, 3.5e-15 or 1e-30), the circuit values
        as before."""
        pulse = "V1 (in 0) vsource type=pulse val0=0 val1=1 delay=0.5u rise=0.5u fall=0.5u width=2u period=20u%s"
        csine = "V1 (in 0) vsource type=sine sinedc=0.2 ampl=0.5 freq=0 sinephase=30%s"
        cases = [("e01", _src_2c9(pulse % "", "dc1 dc dev=V1 param=rise values=[0 0.5u]\n"), "dc1.dc", [0.0, 5e-7], 0.0),
                 ("e04", _src_2c9(pulse % "", "dc1 dc dev=V1 param=fall values=[0 0.5u]\n" + TR_2C8), "dc1.dc",
                  [0.0, 5e-7], 0.0),
                 ("e02", _src_2c9(csine % "", "dc1 dc dev=V1 param=freq values=[0 100k]\n"), "dc1.dc", [0.0, 1e5],
                  C45_2C9),
                 ("p832", _src_2c9(csine % " dc=0.25", "dc1 dc dev=V1 param=freq values=[0 100k]\n"), "dc1.dc",
                  [0.0, 1e5], 0.25),
                 ("ac", _src_2c9(pulse % " mag=1", "ac1 ac dev=V1 param=rise values=[0 0.5u] freq=1k\n"), "ac1.ac",
                  [0.0, 5e-7], None),
                 ("noise", _src_2c9(pulse % " mag=1", "n1 (out 0) noise dev=V1 param=rise values=[0 0.5u] freq=1k "
                                                     "iprobe=V1\n"), "n1.noise", [0.0, 5e-7], None)]
        for name, text, fn, axis, dc in cases:
            with self.subTest(deck=name):
                dv, dx = self.both(name, text)
                for d in (dv, dx):
                    self.assertEqual(self.axis(d, name, fn), axis, d)
                    if dc is not None:
                        for v in self.psf(d, name, fn).column("in"):
                            self.assertAlmostEqual(v, dc, delta=1e-9, msg=(name, d))
        xf = _src_2c9(csine % " dc=0.25", "x1 (out 0) xf dev=V1 param=freq values=[0 100k] freq=1k\n")
        dv, _ = self.run2c2("xf", xf, "vacask")
        self.assertEqual(self.axis(dv, "xf", "x1.xf"), [0.0, 1e5])
        # a sweep block's parent wrote the plan's own points already (S-2c8's E03, the control)
        dv, dx = self.both("e03", _src_2c9(pulse % "", _block_2c9("dev=V1 param=fall", "0 0.5u", "op1 dc")))
        for d in (dv, dx):
            self.assertEqual(self.axis(d, "e03", "sw1_op1.sweep"), [0.0, 5e-7])
        t0132 = _src_2c9("V1 (in 0) vsource type=pulse val0=0 val1=1 delay=0.5u rise=rt fall=0.5u width=1u period=20u "
                         "dc=0.25", "dc1 dc dev=V1 param=rise values=[0 3e-07]\nop1 dc\n", " rt=0.5u")
        dx, _ = self.run2c2("t0132", t0132, "xyce")
        self.assertEqual(self.axis(dx, "t0132", "dc1.dc"), [0.0, 3e-7])
        self.refused("t0132", t0132, "vacask", "dc dc1: on VACASK rise of V1 is bound to the expression rt")

    @needs_vacask
    @needs_xyce
    def test_n3_a_dev_sweep_of_dc_moves_a_sinedc_that_defaults_to_dc_on_both_engines(self):
        """P824 (dc=0.25 swept to 0.5 around a tran, sinedc left to its default): the 0.5 point centred on 0.5 on both
        engines (2c8: 0.25 on both, VACASK 0.75 at 2.5 us where 1.0); P825 (the constant sine, sinephase 30): 0.5 then
        0.75 (2c8: 0.5 at both points); the same without a dc and on an isource, each equal to its alter-free reference
        (the swept dc written on the instance line); an op beside the tran in the block reads the dc; a given sinedc
        stays where it is."""
        cases = [("p824", "ampl=0.5 freq=100k dc=0.25", lambda t, dc: _sine_at_2c9(t, dc, 1e5)),
                 ("p825", "ampl=0.5 freq=0 sinephase=30 dc=0.25", lambda t, dc: dc + 0.25),
                 ("nodc", "ampl=0.5 freq=100k", lambda t, dc: _sine_at_2c9(t, dc, 1e5))]
        for name, fields, want in cases:
            with self.subTest(deck=name):
                dv, dx = self.both(name, _sine_2c8(fields, _block_2c9("dev=V1 param=dc", "0.25 0.5")))
                self.assertGreater(self.same_as_xyce(name, dv, dx, rel=1e-4), 0)
                for d in (dv, dx):
                    for k, dc in ((0, 0.25), (1, 0.5)):
                        self.tran_is(d, name, "sw1-%03d_tr1.tran.tran" % k, lambda t, dc=dc: want(t, dc))
                ref = _sine_2c8(fields.replace(" dc=0.25", "") + " dc=0.5", TR_2C8)
                rv, rx = self.both(name + "r", ref)
                for d, r in ((dv, rv), (dx, rx)):
                    got, exp = self.psf(d, name, "sw1-001_tr1.tran.tran"), self.psf(r, name + "r", "tr1.tran.tran")
                    for t in (0.3e-6, 1.7e-6, 3.1e-6):
                        self.assertAlmostEqual(got.at("in", t), exp.at("in", t), delta=1e-6, msg=(name, d, t))
        isrc = _src_2c9("I1 (0 in) isource type=sine ampl=0.5m freq=100k dc=0.25m",
                        _block_2c9("dev=I1 param=dc", "0.25m 0.5m"))
        iref = _src_2c9("I1 (0 in) isource type=sine ampl=0.5m freq=100k dc=0.5m", TR_2C8)
        dv, dx = self.both("isrc", isrc)
        rv, rx = self.both("iref", iref)
        for d, r in ((dv, rv), (dx, rx)):
            got, exp = self.psf(d, "isrc", "sw1-001_tr1.tran.tran"), self.psf(r, "iref", "tr1.tran.tran")
            for t in (0.3e-6, 1.7e-6, 3.1e-6):
                self.assertAlmostEqual(got.at("in", t), exp.at("in", t), delta=1e-6, msg=(d, t))
        block = _sine_2c8("ampl=0.5 freq=100k dc=0.25", "sw1 sweep dev=V1 param=dc values=[0.25 0.5] {\n  op1 dc\n"
                          "  tr1 tran stop=3.5u maxstep=2n\n}\n")
        dv, dx = self.both("block", block)
        for d in (dv, dx):
            self.assertAlmostEqual(self.op_in(d, "block", "sw1-001_op1"), 0.5, delta=1e-9, msg=d)
            self.tran_is(d, "block", "sw1-001_tr1.tran.tran", lambda t: _sine_at_2c9(t, 0.5, 1e5))
        given = _sine_2c8("sinedc=0.1 ampl=0.5 freq=100k dc=0.25", _block_2c9("dev=V1 param=dc", "0.25 0.5"))
        dv, dx = self.both("given", given)
        for d in (dv, dx):
            for k in (0, 1):
                self.tran_is(d, "given", "sw1-%03d_tr1.tran.tran" % k, lambda t: _sine_at_2c9(t, 0.1, 1e5))
        # the other default §3.8.1 derives, an absent dc (the waveform's value at t = 0), follows the fields it reads
        pul = _src_2c9("V1 (in 0) vsource type=pulse val0=0 val1=1 delay=0.5u rise=0.5u fall=0.5u width=2u period=20u",
                       "dc1 dc dev=V1 param=val0 values=[0 0.3]\n")
        sin = _sine_2c8("sinedc=0.2 ampl=0.5 freq=100k sinephase=30", "dc1 dc dev=V1 param=sinedc values=[0 0.3]\n")
        for name, text, want in (("dval0", pul, [0.0, 0.3]), ("dsinedc", sin, [0.25, 0.55])):
            dv, dx = self.both(name, text)
            for d in (dv, dx):
                got = self.psf(d, name, "dc1.dc").column("in")
                self.assertEqual(len(got), 2, (name, d))
                for g, w in zip(got, want):
                    self.assertAlmostEqual(g, w, delta=1e-9, msg=(name, d, got))

    @needs_vacask
    @needs_xyce
    def test_n4_a_pulse_without_a_period_does_not_repeat_on_xyce(self):
        """P868 (delay=-2.7u, no period, a 3.5 us tran): Xyce's PULSE repeated at TSTOP, in(1.35 us) = 1 where 0; now 0
        on both engines, Xyce equal to VACASK over the tran, the op at the t = 0 value 0.6; G10127 (an isource, delay
        -0.2u): 0 at 3.5 us on both (Xyce 0.27 before); a delay that reads a parameter altered to -2.7u likewise."""
        line = "V1 (in 0) vsource type=pulse val0=0 val1=1 delay=%s rise=0.5u fall=0.5u width=2u"
        dv, dx = self.both("p868", _src_2c9(line % "-2.7u", "op1 dc\n" + TR_2C8))
        self.assertGreater(self.same_as_xyce("p868", dv, dx, rel=1e-6), 0)
        for d in (dv, dx):
            self.assertAlmostEqual(self.op_in(d, "p868"), 0.6, delta=1e-9, msg=d)
            for t, want in ((0.1e-6, 0.4), (1.35e-6, 0.0), (2.5e-6, 0.0), (3.4e-6, 0.0)):
                self.assertAlmostEqual(self.at(d, "p868", "tr1", "in", t), want, delta=1e-6, msg=(d, t))
        g = _src_2c9("I1 (0 in) isource type=pulse val0=0 val1=0.001 delay=-0.2u rise=0.5u fall=0.5u width=1u "
                     "dc=0.0003", TR_2C8 + "op2 dc\n")
        dv, dx = self.both("g10127", g)
        for d in (dv, dx):
            for t in (2.5e-6, 3.3e-6, 3.5e-6):
                self.assertAlmostEqual(self.at(d, "g10127", "tr1", "in", t), 0.0, delta=1e-9, msg=(d, t))
        dl = _src_2c9(line % "dl", "a1 alter param=dl value=-2.7u\n" + TR_2C8)
        dv, dx = self.both("dl", dl)
        for d in (dv, dx):
            self.assertAlmostEqual(self.at(d, "dl", "tr1", "in", 1.35e-6), 0.0, delta=1e-6, msg=d)

    @needs_vacask
    @needs_xyce
    def test_n5_tau2_s_clause_and_vacask_s_setup_before_a_sweep(self):
        """tau2 = 0 (S-2c8's Q01) is refused on both engines with its own clause (VACASK's NaN at the fall's start, Xyce's
        `Time step too small'), tau1 = 0 with tau1's.  G20073 (freq=qfr, qfr 0 at the netlist's values, swept to 100k and
        200k): VACASK exit 1 at its initial setup before; refused on VACASK now, Xyce right (0.5 at both points); an
        alter of qfr to 0 before such a sweep likewise; a given dc around an op runs on VACASK (type="dc")."""
        exp = "V1 (in 0) vsource type=exp val0=0 val1=1 td1=0.5u tau1=%s td2=2u tau2=%s"
        self.refused_both([("q01", _src_2c9(exp % ("1u", "0"), TR_2C8), "tran tr1: the exp of V1 has tau2=0 s",
                            "VACASK would divide by it at the fall's start, delay + td2, and stop with 'NaN found in "
                            "vector', and Xyce would stop with 'Time step too small')"),
                           ("c05", _src_2c9(exp % ("0", "1u"), TR_2C8), "tran tr1: the exp of V1 has tau1=0 s",
                            "VACASK would divide by it at the exp's start and stop with 'NaN found in vector', and "
                            "Xyce would make a step or, for some start times, stop with 'Time step too small')")])
        head = "simulator lang=spectre\nparameters other=1 qfr=%s\n"
        sine = "V1 (in 0) vsource type=sine sinedc=0 ampl=0.5 freq=qfr sinephase=90%s\n"
        g20073 = head % "0" + sine % "" + LOAD_2C8 + _block_2c9("param=qfr", "100k 200k", "op1 dc")
        x4 = head % "50k" + sine % "" + LOAD_2C8 + "a1 alter param=qfr value=0\n" + _block_2c9("param=qfr", "100k",
                                                                                             "op1 dc")
        for name, text, piece in (("g20073", g20073, "dc op1, VACASK's setup before the first point of sweep sw1: on "
                                   "VACASK the sine of V1 has freq=0 Hz"),
                                  ("x4", x4, "dc op1, VACASK's setup before the first point of sweep sw1 (after alter a1): "
                                   "on VACASK the sine of V1 has freq=0 Hz")):
            with self.subTest(deck=name):
                self.refused(name, text, "vacask", piece, "not supported on VACASK in v1 (Xyce runs it)")
                dx, _ = self.run2c2(name, text, "xyce")
                self.assertAlmostEqual(self.op_in(dx, name, "sw1-000_op1"), 0.5, delta=1e-9)
        given = head % "0" + sine % " dc=0.1" + LOAD_2C8 + _block_2c9("param=qfr", "100k 200k", "op1 dc")
        dv, dx = self.both("given", given)
        for d in (dv, dx):
            self.assertAlmostEqual(self.op_in(d, "given", "sw1-001_op1"), 0.1, delta=1e-9, msg=d)

    def refused_both(self, cases):
        for engine in ("vacask", "xyce"):
            for name, text, *pieces in cases:
                with self.subTest(deck=name, engine=engine):
                    self.refused(name, text, engine, *pieces)


PHASE_ONLY_2C9D = """simulator lang=spectre
V1 (in 0) vsource dc=0 phase=45
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
save out
ac1 ac start=1k stop=1k
"""
ALTER_PHASE_2C9D = """simulator lang=spectre
V1 (in 0) vsource dc=0
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
save out
a1 alter dev=V1 param=phase value=45
ac1 ac start=1k stop=1k
"""
SWEEP_PHASE_2C9D = """simulator lang=spectre
V1 (in 0) vsource dc=0
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
save out
sw1 sweep dev=V1 param=phase values=[0 45] {
  ac1 ac start=1k stop=1k
}
"""
MAG_ONLY_2C9D = PHASE_ONLY_2C9D.replace("phase=45", "mag=1 phase=45")


class TestDriverRuns2c9(_Flow):
    """The driver's fix after the 2c9 skeptic, through bin/vamos on both engines: a source's mag defaults to 0 when
    the netlist gives only phase (ref5 p.685), in the parse, after an alter of phase and in a sweep of phase; out was
    0.5 at 45 degrees on both engines (the 2c9 skeptic's K61).  The control: mag=1 phase=45 gives 0.5 at 45 degrees."""

    @needs_vacask
    @needs_xyce
    def test_a_phase_alone_excites_nothing_on_both_engines(self):
        import cmath
        for engine in ("vacask", "xyce"):
            for name, text, files in (("ph1", PHASE_ONLY_2C9D, ("ac1.ac",)), ("ph2", ALTER_PHASE_2C9D, ("ac1.ac",)),
                                      ("ph3", SWEEP_PHASE_2C9D, ("sw1-000_ac1.ac", "sw1-001_ac1.ac"))):
                d, _out = self.spectre(name, text, engine)
                for fn in files:
                    for v in self.psf(d, name, fn).column("out"):
                        self.assertLessEqual(abs(complex(v)), 1e-12, msg=(engine, name, fn, v))
            d, _out = self.spectre("ph4", MAG_ONLY_2C9D, engine)
            v = complex(self.psf(d, "ph4", "ac1.ac").column("out")[0])
            self.assertAlmostEqual(abs(v), 0.5, delta=1e-9, msg=engine)
            self.assertAlmostEqual(cmath.phase(v), cmath.pi / 4, delta=1e-9, msg=engine)


if __name__ == "__main__":
    unittest.main()
