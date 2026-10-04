"""N3 emitter tests: tables.py, the VACASK and Xyce decks, the smoke check (docs/VAMOS_AMS_DESIGN.md §4.3-§4.5, §4.7, §9).

    python3 -m unittest discover -s tests/vamos -p 'test_netlist_emit*.py' -v

The IR is built by hand, following the §4.2 conventions.  Golden decks live in
tests/vamos/fixtures/netlist/emit_*; VAMOS_REGEN_GOLDEN=1 rewrites them.

TestTables, TestVacaskText, TestXyceText   no engine: dispatch, polarity, levels,
    versions, stripped keys, scale, the multiplier, binning, sources, the
    control block, quoting and names, errors, the goldens.
TestVacaskRuns, TestXyceRuns (WSL)   every golden deck run on its engine (Xyce
    as plain `Xyce deck.cir`; the AMS goldens through their smoke form, the
    only difference being the bridge sources), the smoke check (pass, unknown
    model parameter, floating gate without its shunt, no-bin message),
    Verilog-A instances, the gated-conductance polarity.
TestCrossEngine (WSL, both engines)   the same IR on VACASK and Xyce: CMOS and
    PNP polarity, nested m=2 in m=3 (6x), G cur= / I / J in an m=3 subckt (3x),
    dependent subckt-parameter overrides, BSIM3 with VERSION, a level-3 diode
    (VACASK) and level 9 refused, binned BSIM4 (per-finger W, a bin edge, no
    ad/as), PULSE(0 1 1n 0.1n 0.1n) holding v2, every element of the basic
    deck, .option scale, the gated element 0.6667 / 0.
"""

import os
import re
import unittest

from vamos_testlib import (TempDir, fixture, needs_vacask, needs_xyce, openvaf_bin, run, vacask_bin,
                           xyce_bin, xyce_env)

from vamos.netlist import expr as X  # noqa: E402
from vamos.netlist import rawfile, tables as T, vacask, xyce  # noqa: E402
from vamos.netlist.expr_ast import Name, Num, Str  # noqa: E402
from vamos.netlist.ir import Analysis, Instance, Model, Netlist, Param, Source, Subckt  # noqa: E402
from vamos.notes import ERROR, NOTE, WARNING, NoteError, strict  # noqa: E402

P = X.parse
REGEN = bool(os.environ.get("VAMOS_REGEN_GOLDEN"))
OSDI_PLACEHOLDER = "/vamos/ams/va/0_vamos_ie.osdi"


def I(name, kind, nodes, **kw):                                   # noqa: E743
    return Instance(name, kind, list(nodes), **kw)


def V(name, p, n, dc=None, **kw):
    return I(name, "v", [p, n], source=Source(dc=dc if dc is None or not isinstance(dc, (int, float))
                                              else Num(float(dc)), **kw))


def R(name, a, b, value, **params):
    return I(name, "r", [a, b], value=P(value) if isinstance(value, str) else Num(float(value)),
             params={k: P(v) if isinstance(v, str) else Num(float(v)) for k, v in params.items()})


def pulse(**f):
    return Source(wave="pulse", args={k: Num(float(v)) for k, v in f.items()})


def tran(step, stop, **kw):
    a = {"step": step, "stop": stop}
    a.update(kw)
    return [Analysis("tran", a, "deck:99")]


def card(name, kind, level, origin="", **params):
    return Model(name, kind, None if level is None else float(level),
                 {k: P(v) if isinstance(v, str) else Num(float(v)) for k, v in params.items()}, origin)


def notes_text(notes):
    return "\n".join(n.text() for n in notes)


def mp(m, engine="xyce", **options):
    """(params, notes) of a card as an engine gets it; options as Netlist.options (numbers)."""
    p, notes = T.model_params(m, engine, options={k: Num(float(v)) for k, v in options.items()} or None)
    return dict(p), notes


def sev(notes, severity):
    return [n.message for n in notes if n.severity == severity]


# -- the decks ---------------------------------------------------------------------------

def basic_netlist():
    """Most element kinds in one runnable deck."""
    inv = Subckt("inv", ["a", "y", "vdd"], params=[Param("wp", P("2.5*wn"))], body=[
        I("mn", "m", ["y", "a", "ns", "0"], master="nch", params={"w": Name("wn"), "l": P("1u")}),
        R("rsrc", "ns", "0", 10),
        I("mp", "m", ["y", "a", "vdd", "vdd"], master="pch", params={"w": Name("wp"), "l": P("1u")}),
        I("cy", "c", ["y", "0"], value=P("10f")),
    ], orig_ports=["a", "y", "vdd"], origin="deck:5")
    nl = Netlist(title="vamos emitter golden: basic")
    nl.body = [
        Param("vdd", Num(1.8)), Param("wn", P("1u")), Param("gain", P("vdd/0.9")),
        card("nch", "nmos", 1, vto=0.5, kp=1e-4, **{"lambda": 0.02}),
        card("pch", "pmos", 1, vto=-0.5, kp=4e-5, **{"lambda": 0.02}),
        card("dd", "d", None, **{"is": 1e-14, "cjo": 1e-12}),
        card("qn", "npn", 1, **{"is": 1e-15, "bf": 100}),
        card("jn", "njf", 1, vto=-2.0, beta=1e-4),
        inv,
        I("vsup", "v", ["vdd", "0"], source=Source(dc=Name("vdd"))),
        I("vin", "v", ["in", "0"], source=Source(wave="pulse", args={
            "v1": Num(0.0), "v2": Name("vdd"), "td": Num(1e-9), "tr": Num(1e-10), "tf": Num(1e-10),
            "pw": Num(4e-9), "per": Num(1e-8)})),
        I("x1", "x", ["in", "out", "vdd"], master="inv", params={"wp": P("3u")}),
        I("x2", "x", ["out", "out2", "vdd"], master="inv", params={"m": Num(2.0)}),
        R("rl", "out2", "0", "100k"),
        R("r1", "vdd", "rb", "10k", tc1=1e-3),
        I("d1", "d", ["rb", "0"], master="dd"),
        I("vsin", "v", ["s", "0"], source=Source(wave="sin", args={
            "vo": Num(0.5), "va": Num(0.2), "freq": P("100meg"), "td": P("1n"), "theta": Num(0.0),
            "phase": Num(0.0)})),
        R("rs", "s", "sb", "1k"),
        I("q1", "q", ["sb", "sb", "0"], master="qn"),
        I("vexp", "v", ["e", "0"], source=Source(wave="exp", args={
            "v1": Num(0.0), "v2": Num(1.0), "td1": Num(1e-9), "tau1": Num(1e-9), "td2": Num(3e-9),
            "tau2": Num(1e-9)})),
        R("re", "e", "0", "1k"),
        I("vpwl", "v", ["p", "0"], source=Source(wave="pwl", args={"td": P("0.5n")},
                                                  points=[(Num(0.0), Num(0.0)), (P("1n"), Num(1.0)),
                                                          (P("2n"), Num(0.5))])),
        R("rp", "p", "0", "1k"),
        I("vj", "v", ["jd", "0"], source=Source(dc=Num(1.0))),
        I("j1", "j", ["jd", "0", "0"], master="jn"),
        I("e1", "e", ["ea", "0", "out", "0"], value=Name("gain")),
        R("rea", "ea", "0", "1k"),
        I("g1", "g", ["0", "ga", "out", "0"], value=P("1m")),
        R("rga", "ga", "0", "1k"),
        I("vprobe", "v", ["vdd", "vp"], source=Source(dc=Num(0.0))),
        R("rvp", "vp", "0", "10k"),
        I("f1", "f", ["0", "fa"], value=Num(2.0), ctrl=["vprobe"]),
        R("rfa", "fa", "0", "1k"),
        I("h1", "h", ["ha", "0"], value=P("1k"), ctrl=["vprobe"]),
        R("rha", "ha", "0", "1k"),
        I("ebeh", "b", ["bb", "0"], expr=P("v(out)*0.5"), expr_kind="v"),
        I("gbeh", "b", ["0", "bc"], expr=P("1e-3*v(in)"), expr_kind="i"),
        R("rbb", "bb", "0", "1k"), R("rbc", "bc", "0", "1k"),
        I("vl", "v", ["la", "0"], source=Source(wave="sin", args={
            "vo": Num(0.0), "va": Num(1.0), "freq": P("10meg"), "td": Num(0.0), "theta": Num(0.0),
            "phase": Num(0.0)})),
        I("l1", "l", ["la", "lb"], value=P("1u")),
        I("l2", "l", ["lc", "0"], value=P("4u")),
        I("k1", "k", [], value=Num(0.5), ctrl=["l1", "l2"]),
        R("rlb", "lb", "0", 10), R("rlc", "lc", "0", 100),
        I("i1", "i", ["0", "ib"], source=Source(dc=P("1u"))),
        R("rib", "ib", "0", "1k"),
    ]
    nl.analyses = tran(1e-11, 2e-8)
    nl.probes = [("tran", "v", "out"), ("tran", "v", "out2"), ("tran", "i", "vsup"),
                 ("tran", "v", "x1.ns"), ("tran", "v", "bc"), ("tran", "v", "fa"), ("tran", "v", "ha")]
    nl.globals = []
    nl.values = {"vdd": 1.8, "wn": 1e-6, "gain": 2.0}
    return nl


def inverter_netlist():
    """CMOS inverters at vin=1.8 and 0, and a PNP common-emitter bias point (polarity)."""
    nl = Netlist(title="polarity")
    nl.body = [
        card("nch", "nmos", 1, vto=0.5, kp=1e-4, **{"lambda": 0.02}),
        card("pch", "pmos", 1, vto=-0.5, kp=4e-5, **{"lambda": 0.02}),
        card("qp", "pnp", None, **{"is": 1e-15, "bf": 100}),
        V("vdd", "vdd", "0", 1.8), V("vhi", "hi", "0", 1.8),
        I("mn1", "m", ["out1", "hi", "0", "0"], master="nch", params={"w": P("1u"), "l": P("1u")}),
        I("mp1", "m", ["out1", "hi", "vdd", "vdd"], master="pch", params={"w": P("2.5u"), "l": P("1u")}),
        I("mn0", "m", ["out0", "0", "0", "0"], master="nch", params={"w": P("1u"), "l": P("1u")}),
        I("mp0", "m", ["out0", "0", "vdd", "vdd"], master="pch", params={"w": P("2.5u"), "l": P("1u")}),
        V("vcc", "vcc", "0", 5.0), R("rb", "b", "0", "430k"), R("rc", "c", "0", "1k"),
        I("q1", "q", ["c", "b", "vcc"], master="qp"),
    ]
    nl.analyses = tran(1e-10, 1e-9)
    nl.probes = [("tran", "v", "out1"), ("tran", "v", "out0"), ("tran", "v", "c")]
    return nl


def mfactor_netlist():
    """Nested m=2 in m=3 (6x), device m=2 in m=3 (6x), G cur= / I / J in an m=3 subckt (3x)."""
    leaf = Subckt("rleaf", ["x"], body=[R("r1", "x", "0", "1k")])
    mid = Subckt("rmid", ["x"], body=[I("xi", "x", ["x"], master="rleaf", params={"m": Num(2.0)})])
    rdev = Subckt("rdev", ["x"], body=[R("r1", "x", "0", "1k", m=2)])
    gsub = Subckt("gsub", ["x"], body=[I("g1", "b", ["0", "x"], expr=P("1e-3*v(s)"), expr_kind="i")])
    isub = Subckt("isub", ["x"], body=[I("i1", "i", ["0", "x"], source=Source(dc=P("1m")))])
    jsub = Subckt("jsub", ["d"], body=[I("j1", "j", ["d", "0", "0"], master="jn")])
    ksub = Subckt("ksub", ["a"], body=[
        R("r0", "a", "a1", 10), I("l1", "l", ["a1", "0"], value=P("1u")), I("l2", "l", ["b", "0"], value=P("1u")),
        I("k1", "k", [], value=Num(0.5), ctrl=["l1", "l2"]), R("r2", "b", "0", "1k"),
        I("e1", "b", ["c", "0"], expr=P("v(a)"), expr_kind="v"), R("r3", "c", "0", "1k")])
    nl = Netlist(title="multiplier")
    nl.globals = ["s"]
    nl.body = [
        card("jn", "njf", 1, vto=-2.0, beta=1e-4),
        leaf, mid, rdev, gsub, isub, jsub, ksub,
        V("vs", "s", "0", 1.0),
        V("vx", "nx", "0", 1.0), I("x1", "x", ["nx"], master="rmid", params={"m": Num(3.0)}),
        V("vy", "ny", "0", 1.0), I("x2", "x", ["ny"], master="rdev", params={"m": Num(3.0)}),
        R("rz", "nz", "0", "1k"), I("x3", "x", ["nz"], master="gsub", params={"m": Num(3.0)}),
        R("rw", "nw", "0", "1k"), I("x4", "x", ["nw"], master="isub", params={"m": Num(3.0)}),
        V("vj", "nj", "0", 1.0), I("x5", "x", ["nj"], master="jsub", params={"m": Num(3.0)}),
        V("vk", "nk", "0", 1.0), I("j2", "j", ["nk", "0", "0"], master="jn", value=Num(3.0)),
        V("vl", "nl", "0", 1.0), I("j3", "j", ["nl", "0", "0"], master="jn"),
        I("va", "v", ["na", "0"], source=Source(wave="sin", args={
            "vo": Num(0.0), "va": Num(1.0), "freq": P("10meg"), "td": Num(0.0), "theta": Num(0.0),
            "phase": Num(0.0)})),
        I("x6", "x", ["na"], master="ksub", params={"m": Num(2.0)}),
        I("x7", "x", ["na"], master="ksub"),
    ]
    nl.analyses = tran(1e-10, 1e-8)
    nl.probes = [("tran", "i", "vx"), ("tran", "i", "vy"), ("tran", "v", "nz"), ("tran", "v", "nw"),
                 ("tran", "i", "vj"), ("tran", "i", "vk"), ("tran", "i", "vl"), ("tran", "v", "x6.b"),
                 ("tran", "v", "x7.b")]
    return nl


B4 = {"version": 4.8, "toxe": 2e-9, "u0": 0.04}


def bin_netlist(with_nobin=False):
    """BSIM4 bins under .option scale=1e-6: per-finger W, a bin edge, a device without ad/as."""
    def b4(name, k, lmin, lmax, wmin, wmax, vth0):
        m = card(name, "nmos", 54, lmin=lmin, lmax=lmax, wmin=wmin, wmax=wmax, vth0=vth0, **B4)
        m.base, m.bin_index = "nch", k
        return m
    nl = Netlist(title="binning")
    nl.options = {"scale": Num(1e-6)}
    nl.body = [
        b4("nch.1", 1, 1e-7, 2.2e-7, 1e-7, 1e-6, 0.3),
        b4("nch.2", 2, 1e-7, 2.2e-7, 1e-6, 1e-4, 0.6),
        b4("nch.3", 3, 2.2e-7, 1e-5, 1e-7, 1e-6, 0.45),
        b4("nch.4", 4, 2.2e-7, 1e-5, 1e-6, 1e-4, 0.5),
        card("ref1", "nmos", 54, vth0=0.3, **B4), card("ref2", "nmos", 54, vth0=0.6, **B4),
        card("ref3", "nmos", 54, vth0=0.45, **B4), card("ref4", "nmos", 54, vth0=0.5, **B4),
        V("vg", "g", "0", 1.0),
        # w=1.6u nf=2: total W 1.6u would pick nch.2, per-finger 0.8u picks nch.1
        V("vd1", "d1", "0", 1.0),
        I("m1", "m", ["d1", "g", "0", "0"], master="nch",
          params={"w": Num(1.6), "l": Num(0.2), "nf": Num(2.0), "ad": Num(1.0), "as": Num(1.0)}),
        V("vr1", "r1", "0", 1.0),
        I("mr1", "m", ["r1", "g", "0", "0"], master="ref1",
          params={"w": Num(1.6), "l": Num(0.2), "nf": Num(2.0), "ad": Num(1.0), "as": Num(1.0)}),
        # l = 0.22*1e-6 = 2.1999999999999998e-07, a hair below the 2.2e-7 edge: nch.2 (upper bound
        # exclusive) and, through the 1e-15 lower-bound tolerance, nch.4 both match; the first in
        # Xyce's order (nch.2) wins on both engines
        V("vd2", "d2", "0", 1.0),
        I("m2", "m", ["d2", "g", "0", "0"], master="nch", params={"w": Num(2.0), "l": P("0.22")}),
        V("vr2", "r2", "0", 1.0),
        I("mr2", "m", ["r2", "g", "0", "0"], master="ref2", params={"w": Num(2.0), "l": P("0.22")}),
        # no ad/as: BSIM4 computes them (the $param_given path)
        V("vd3", "d3", "0", 1.0),
        I("m3", "m", ["d3", "g", "0", "0"], master="nch", params={"w": Num(0.5), "l": Num(1.0)}),
        V("vr3", "r3", "0", 1.0),
        I("mr3", "m", ["r3", "g", "0", "0"], master="ref3", params={"w": Num(0.5), "l": Num(1.0)}),
    ]
    if with_nobin:
        nl.body.append(I("m9", "m", ["d3", "g", "0", "0"], master="nch", params={"w": Num(0.5), "l": Num(20.0)},
                         origin="deck:42"))
    nl.analyses = tran(1e-10, 1e-9)
    nl.probes = [("tran", "i", v) for v in ("vd1", "vr1", "vd2", "vr2", "vd3", "vr3")]
    return nl


def ams_netlist(engine):
    """What ams/deck.py adds: bridge sources, the gated D2A element, shunts, a supply source."""
    inv = Subckt("inv", ["a", "y", "vdd"], body=[
        I("mn", "m", ["y", "a", "0", "0"], master="nch", params={"w": P("1u"), "l": P("1u")}),
        I("mp", "m", ["y", "a", "vdd", "vdd"], master="pch", params={"w": P("2.5u"), "l": P("1u")}),
        I("cy", "c", ["y", "0"], value=P("10f"))])
    nl = Netlist(title="vamos ams deck")
    nl.body = [
        card("nch", "nmos", 1, vto=0.5, kp=1e-4, **{"lambda": 0.02}),
        card("pch", "pmos", 1, vto=-0.5, kp=4e-5, **{"lambda": 0.02}),
        inv,
        T.code_source("vd_n_u_a", "v", "n_u_a_d", "0", T.code_uri(engine, "d2a", "tb.u.a__d")),
        T.code_source("ve_n_u_a", "v", "n_u_a_e", "0", T.code_uri(engine, "d2a", "tb.u.a__e")),
        T.gcond("g_n_u_a", "n_u_a", "n_u_a_d", "n_u_a_e", 500.7),
        T.shunt("rsh_n_u_a", "n_u_a"),
        T.code_source("ia_n_u_y", "i", "n_u_y", "0", T.code_uri(engine, "a2d", "tb.u.y__a")),
        T.shunt("rsh_n_u_y", "n_u_y"),
        T.dc_source("vs_n_vdd", "n_vdd", "0", 1.8),
        I("xv_u", "x", ["n_u_a", "n_u_y", "n_vdd"], master="inv"),
    ]
    nl.analyses = tran(1e-11, 1e-6, maxstep=1e-8)
    nl.probes = [("tran", "v", "n_u_a"), ("tran", "v", "n_u_y")]
    return nl


def dependent_netlist():
    """HSPICE overrides of subckt parameters whose defaults depend on others: cell(w, l=k2*w, r=l*1000)."""
    cell = Subckt("cell", ["a"], params=[Param("w", Num(1.0)), Param("l", P("k2*w")), Param("r", P("l*1000"))],
                  body=[I("r1", "r", ["a", "0"], value=Name("r"))])
    nl = Netlist(title="dependent overrides")
    nl.body = [Param("k2", Num(2.0)), cell,
               V("v1", "n1", "0", 1.0), I("x1", "x", ["n1"], master="cell", params={"w": Num(2.0)}),
               V("v2", "n2", "0", 1.0), I("x2", "x", ["n2"], master="cell", params={"l": Num(5.0)}),
               V("v3", "n3", "0", 1.0), I("x3", "x", ["n3"], master="cell"),
               V("v4", "n4", "0", 1.0), I("x4", "x", ["n4"], master="cell", params={"r": Num(500.0)})]
    nl.values = {"k2": 2.0}
    nl.analyses = tran(1e-10, 1e-9)
    nl.probes = [("tran", "i", "v%d" % k) for k in range(1, 5)]
    return nl


def gcond_netlist(enable):
    nl = Netlist(title="gated conductance polarity")
    nl.body = [T.dc_source("vd_n", "n_d", "0", 1.0), T.dc_source("ve_n", "n_e", "0", enable),
               R("rl", "n", "0", "1k"), T.shunt("rsh_n", "n"), T.gcond("g_n", "n", "n_d", "n_e", 500.0)]
    nl.analyses = tran(1e-10, 1e-9)
    nl.probes = [("tran", "v", "n")]
    return nl


def pulse_netlist():
    """PULSE(0 1 1n 0.1n 0.1n) as spice.parse resolves it: pw=TSTOP, no period (§4.3.8)."""
    nl = Netlist(title="pulse holds v2")
    nl.body = [I("v1", "v", ["a", "0"], source=pulse(v1=0, v2=1, td=1e-9, tr=1e-10, tf=1e-10, pw=2e-8)),
               R("r1", "a", "0", "1k")]
    nl.analyses = tran(1e-11, 2e-8)
    nl.probes = [("tran", "v", "a")]
    return nl


def va_netlist(params=True):
    """A Verilog-A resistor (.hdl): y1 with r=500 and m=2 (VACASK only), y2 with its default r=2000."""
    nl = Netlist(title="verilog-a")
    nl.hdl = [fixture("netlist", "emit_vres.va")]
    nl.body = [V("v2", "t", "0", 1.0), I("y2", "y", ["t", "0"], master="vres")]
    if params:
        nl.body = [V("v1", "s", "0", 1.0),
                   I("y1", "y", ["s", "0"], master="vres", params={"r": Num(500.0), "m": Num(2.0)})] + nl.body
    nl.analyses = tran(1e-10, 1e-9)
    nl.probes = [("tran", "i", v) for v in (["v1", "v2"] if params else ["v2"])]
    return nl


# -- tables -------------------------------------------------------------------------------

class TestTables(unittest.TestCase):
    def test_dispatch_rows(self):
        cases = {("nmos", 1): ("sp_mos1", "spice/mos1.osdi", 1), ("pmos", 3): ("sp_mos3", "spice/mos3.osdi", 3),
                 ("nmos", 49): ("sp_bsim3v3", "spice/bsim3v3.osdi", 9), ("pmos", 53): ("sp_bsim3v3", "spice/bsim3v3.osdi", 9),
                 ("nmos", 54): ("sp_bsim4v8", "spice/bsim4v8.osdi", 54), ("d", 1): ("sp_diode", "spice/diode.osdi", 1),
                 ("npn", None): ("sp_bjt", "spice/bjt.osdi", 1), ("pjf", 1): ("sp_jfet1", "spice/jfet1.osdi", 1)}
        for (kind, lv), (mod, osdi, xl) in cases.items():
            row = T.target(card("m", kind, lv))
            self.assertEqual((row.vacask_module, row.vacask_osdi, row.xyce_level), (mod, osdi, xl), (kind, lv))
        self.assertIsNone(T.target(card("d3", "d", 3)).xyce_level)        # no Xyce geometric diode

    def test_levels_without_a_faithful_target_are_refused(self):
        for kind, lv in (("nmos", 6), ("nmos", 9), ("pmos", 10), ("nmos", 13), ("nmos", 50), ("nmos", 72),
                         ("npn", 4), ("pnp", 2), ("d", 2), ("d", 6), ("njf", 2)):
            with self.assertRaises(T.TableError, msg=(kind, lv)) as cm:
                T.target(card("bad", kind, lv))
            self.assertIn("level %d" % lv, str(cm.exception))
        with self.assertRaises(T.TableError):
            T.target(card("half", "nmos", 1.5))

    def test_polarity_level_version(self):
        p, notes = T.model_params(card("p1", "pmos", 1, vto=-0.5), "vacask")
        self.assertEqual(p[0], ("type", Num(-1.0)))
        self.assertNotIn("level", dict(p))
        p, _ = T.model_params(card("q", "npn", 1), "vacask")
        self.assertEqual(dict(p)["type"], Num(1.0))
        p, _ = T.model_params(card("d", "d", 3, **{"is": 1e-14}), "vacask")
        self.assertEqual(dict(p)["level"], Num(3.0))                    # sp_diode keeps level
        self.assertNotIn("type", dict(p))
        p, notes = T.model_params(card("b3", "nmos", 53, version=3.2, tox=4e-9), "vacask")
        self.assertNotIn("version", dict(p))
        self.assertEqual([n.severity for n in notes], [NOTE])
        self.assertIn("BSIM3v3.3", notes[0].message)
        p, notes = T.model_params(card("b3", "nmos", 53, version=3.2), "xyce")
        self.assertEqual(dict(p)["version"], Num(3.2))
        self.assertEqual(notes, [])
        p, notes = T.model_params(card("b4", "nmos", 54, version=4.8), "vacask")
        self.assertEqual(dict(p)["version"], Num(4.8))                  # printed as a string (STRING_PARAMS)
        self.assertEqual(notes, [])                                      # 4.8: the card's own equations
        for engine, runs in (("vacask", "4.8.3"), ("xyce", "4.6.1")):
            b = card("b4.3", "nmos", 54, version=4.5, origin="lib:9")
            b.base, b.bin_index = "b4", 3
            _, notes = T.model_params(b, engine, binned=True)
            self.assertEqual([(n.severity, n.origin) for n in notes], [(NOTE, "lib:9")])
            self.assertIn("binned model b4: BSIM4 version=4.5 is simulated as BSIM4 %s" % runs, notes[0].message)
        self.assertEqual([T.bsim4_version("xyce", v) for v in (4.5, 4.61, 4.7, 4.8, 4.82, 4.9)],
                         ["4.6.1", "4.6.1", "4.7.0", "4.8.2", "4.8.2", "4.8.2"])
        p, _ = T.model_params(card("x", "pmos", 1), "xyce")
        self.assertNotIn("type", dict(p))                               # Xyce keeps the pmos keyword

    def test_hspice_only_keys_are_stripped_with_a_warning(self):
        for engine in T.ENGINES:
            p, notes = T.model_params(card("n", "nmos", 53, acm=12, hdif=0.5e-6, vth0=0.4, origin="lib:3"),
                                      engine)
            self.assertEqual(sorted(dict(p)), ["vth0"] if engine == "xyce" else ["type", "vth0"])
            self.assertEqual(sorted(n.message.split(":")[1].split("=")[0].split()[-1] for n in notes
                                    if n.severity == WARNING), ["acm", "hdif"])
            self.assertTrue(all(n.origin == "lib:3" for n in notes))

    def test_type_on_the_card(self):
        p, _ = T.model_params(card("n", "nmos", 1, type=1.0), "vacask")
        self.assertEqual([k for k, _ in p].count("type"), 1)
        with self.assertRaises(T.TableError):
            T.model_params(card("n", "nmos", 1, type=-1.0), "vacask")

    # -- HSPICE's model defaults (tables.hspice_card; Star-HSPICE 2001.2 pages in the comments) --

    def test_hspice_mos_level1_defaults(self):
        # KP: the LEVEL 1 defaults 2.0718e-5 (NMOS) / 8.632e-6 (PMOS), 21-2; GAMMA and PHI from the
        # default NSUB=1e15 (20-50), written because the target ignores NSUB without TOX
        p, notes = mp(card("na", "nmos", 1, vto=0.7))
        self.assertEqual(p["kp"], Num(2.0718e-5))
        self.assertAlmostEqual(p["gamma"].value, 0.527625, places=6)
        self.assertAlmostEqual(p["phi"].value, 0.576036, delta=1e-6)
        self.assertNotIn("nsub", p)
        self.assertEqual(sev(notes, WARNING), [])
        self.assertEqual(len(notes), 1)
        self.assertIn("model na: written as HSPICE simulates it: kp=2.0718e-05 (HSPICE's LEVEL 1 default KP "
                      "for NMOS)", notes[0].message)
        self.assertEqual(mp(card("pb", "pmos", 1, vto=-0.7))[0]["kp"], Num(8.632e-6))
        # TOX above 1 is in Angstrom (20-69): KP = UO*COX on both; NSUB and PHI for the target
        p, notes = mp(card("nc", "nmos", 1, vto=0.7, tox=200, uo=600, capop=0))
        self.assertEqual((p["tox"], p["nsub"]), (Num(2e-8), Num(1e15)))
        self.assertNotIn("kp", p)
        self.assertNotIn("capop", p)
        self.assertEqual(sev(notes, WARNING), [])
        p, _ = mp(card("nt", "nmos", 1, vto=0.7, kp=1e-4, tox="toxp", capop=0))
        self.assertEqual(X.evaluate(p["tox"], {"toxp": 200.0}), 2e-8)
        self.assertEqual(X.evaluate(p["tox"], {"toxp": 2e-8}), 2e-8)
        # GAMMA, PHI and VTO given with TOX: nothing to derive; GAMMA alone: NSUB from it (21-4)
        self.assertNotIn("nsub", mp(card("nf", "nmos", 1, vto=1, gamma=0.5, phi=0.6, kp=5e-5, tox=1e-8))[0])
        p, _ = mp(card("ng", "nmos", 1, vto=1, kp=5e-5, gamma=0.5276252190748421, tox=1e-7, capop=0))
        self.assertAlmostEqual(p["nsub"].value / 1e15, 1.0, places=9)
        self.assertAlmostEqual(p["phi"].value, 0.576036, delta=1e-6)
        # KP with only one of UO and TOX: the manual is ambiguous, a warning
        p, notes = mp(card("pt", "pmos", 1, vto=-0.7, tox=2e-8, capop=0))
        self.assertEqual(p["uo"], Num(250.0))
        self.assertIn("KP is not given and only TOX is", sev(notes, WARNING)[0])
        p, notes = mp(card("nu", "nmos", 1, vto=0.7, uo=500))
        self.assertAlmostEqual(p["kp"].value, 500e-4 * T.MOS_COX)
        self.assertIn("KP is not given and only UO is", sev(notes, WARNING)[0])
        # no VTO at LEVEL 1 without TOX: HSPICE computes it, the target uses 0
        self.assertIn("VTO is not given", sev(mp(card("nv", "nmos", 1, kp=1e-4))[1], WARNING)[0])
        # GAMMA=0: no NSUB to derive; PHI keeps its documented default
        p, notes = mp(card("g0", "nmos", 1, vto=0.5, kp=1e-4, gamma=0))
        self.assertEqual(p["phi"], Num(T.PHI_DEFAULT))
        self.assertNotIn("nsub", p)
        self.assertEqual(sev(notes, WARNING), [])
        # .option spice (9-14): SPICE's NSUB rule, CAPOP=0; KP is still HSPICE's
        p, notes = mp(card("ns", "nmos", 1, vto=0.7, tox=2e-8, kp=1e-4), spice=1.0)
        self.assertEqual(sorted(p), ["kp", "tox", "vto"])
        self.assertEqual(notes, [])
        p, _ = mp(card("ns", "nmos", 1, vto=0.7), spice=1.0)
        self.assertEqual(sorted(p), ["kp", "vto"])

    def test_hspice_mos_level2_3_defaults(self):
        cox = T.EPS_OX / 2e-8
        # no KP: ambiguous (2.0e-5 or UO*COX) -> a warning; PMOS UO 250 (21-11); NSUB 1e15 and PHI
        p, notes = mp(card("p2", "pmos", 2, vto=-0.7, capop=0))
        self.assertEqual((p["uo"], p["nsub"]), (Num(250.0), Num(1e15)))
        self.assertAlmostEqual(p["phi"].value, 0.576036, delta=1e-6)
        self.assertEqual(len(sev(notes, WARNING)), 1)
        self.assertIn("KP is not given", sev(notes, WARNING)[0])
        # KP given: UO from it (21-22); LD = 0.75*XJ (20-70); CGSO/CGDO = (LD+METO)*COX (20-73), METO
        # removed; ETA*8.14/8.15 (21-29)
        p, notes = mp(card("n3", "nmos", 3, vto=0.7, kp=5e-5, tox=2e-8, xj=0.2e-6, nsub=1e16, gamma=0.4,
                           phi=0.7, eta=0.1, meto=0.05e-6, capop=0))
        self.assertAlmostEqual(p["uo"].value, 5e-5 / (cox * 1e-4))
        self.assertAlmostEqual(p["ld"].value, 0.15e-6)
        self.assertAlmostEqual(p["cgso"].value, 0.2e-6 * cox)
        self.assertEqual(p["cgdo"], p["cgso"])
        self.assertNotIn("meto", p)
        self.assertAlmostEqual(p["eta"].value, 0.1 * 8.14 / 8.15)
        self.assertEqual((p["nsub"], p["phi"]), (Num(1e16), Num(0.7)))
        self.assertEqual(sev(notes, WARNING), [])
        # given LD and CGSO kept; CGBO = 2*WD*COX; XJ below 0.05u is a warning at LEVEL 3
        p, notes = mp(card("n3b", "nmos", 3, vto=0.7, kp=5e-5, uo=600, tox=2e-8, xj=0.02e-6, ld=0.1e-6,
                           cgso=1e-10, wd=0.1e-6, capop=0))
        self.assertEqual((p["ld"], p["cgso"]), (Num(1e-7), Num(1e-10)))
        self.assertAlmostEqual(p["cgdo"].value, 0.1e-6 * cox)
        self.assertAlmostEqual(p["cgbo"].value, 2 * 0.1e-6 * cox)
        self.assertIn("xj=2e-08 is below 0.05u", sev(notes, WARNING)[0])
        # .option spice: LD=0 and no NSUB unless given (so no CJ default either); LEVEL 3's CLM is
        # HSPICE's (badmos3=1) under every option
        p, _ = mp(card("n3s", "nmos", 3, vto=0.7, kp=5e-5, uo=600, tox=2e-8, xj=0.2e-6), spice=1.0)
        self.assertEqual(sorted(p), ["badmos3", "kp", "tox", "uo", "vto", "xj"])

    def test_hspice_capop(self):
        warn = "HSPICE's default CAPOP=2 gate capacitance (parameterized modified Meyer) is simulated as SPICE's"
        for c in (card("c1", "nmos", 1, vto=1, kp=5e-5, tox=1e-8), card("c2", "nmos", 2, vto=1, kp=5e-5, uo=600),
                  card("c3", "pmos", 3, vto=-1, kp=5e-5, uo=250, tox=1e-8)):
            self.assertEqual(len([m for m in sev(mp(c)[1], WARNING) if warn in m]), 1, c.name)
        # LEVEL 1 without TOX has no gate capacitance in HSPICE either (20-56); .option spice is CAPOP=0
        self.assertEqual(sev(mp(card("c4", "nmos", 1, vto=1, kp=5e-5))[1], WARNING), [])
        self.assertEqual(sev(mp(card("c5", "nmos", 2, vto=1, kp=5e-5, uo=600), spice=1.0)[1], WARNING), [])
        # capop=0 is what both engines simulate (removed, a note); another CAPOP: removed, a warning
        p, notes = mp(card("c6", "nmos", 2, vto=1, kp=5e-5, uo=600, capop=0))
        self.assertNotIn("capop", p)
        self.assertEqual(sev(notes, WARNING), [])
        p, notes = mp(card("c7", "nmos", 2, vto=1, kp=5e-5, uo=600, capop=4))
        self.assertNotIn("capop", p)
        self.assertEqual(sev(notes, WARNING), ["model c7: HSPICE's CAPOP=4 gate capacitance is not available on "
                                               "either engine; simulated with SPICE's Meyer model (CAPOP=0)"])
        self.assertEqual([n.severity for n in strict(notes, True) if "CAPOP" in n.message], [ERROR])

    def test_hspice_bsim3_defaults(self):
        p, notes = mp(card("b49", "nmos", 49, version=3.1, tox=4e-9))
        self.assertEqual((p["xpart"], p["capmod"]), (Num(1.0), Num(2.0)))
        w = sev(notes, WARNING)
        self.assertEqual(len(w), 2)
        self.assertIn("LEVEL 49 default ACM=0 source/drain junction model", w[0])
        self.assertIn("LEVEL 49 VERSION 3.1 default CAPMOD=0", w[1])
        p, notes = mp(card("b53", "nmos", 53, version=3.1, tox=4e-9))
        self.assertEqual(p["capmod"], Num(2.0))
        self.assertNotIn("xpart", p)
        self.assertEqual(sev(notes, WARNING), [])
        self.assertEqual(mp(card("b30", "nmos", 53, version=3.0))[0]["capmod"], Num(1.0))
        for v in (None, 3.2, 3.24, 3.3):
            self.assertNotIn("capmod", mp(card("b32", "nmos", 49, acm=10, **({} if v is None else {"version": v})))[0])
        p, notes = mp(card("b49a", "nmos", 49, acm=10, capmod=0))
        self.assertEqual((p["capmod"], p["xpart"]), (Num(0.0), Num(1.0)))
        # ACM=10: Berkeley's junction defaults, which both targets have (22-19: ACM=10 is LEVEL 49's
        # "compliance with Berkeley BSIM3v3"); 22-43's JS=0, CJ=5.79e-4, CJSW=0 are ACM=0's
        for k in ("js", "cj", "cjsw"):
            self.assertNotIn(k, p)
        self.assertNotIn("acm", p)
        self.assertEqual(sev(notes, WARNING), [])
        self.assertIn("acm=10 removed", sev(notes, NOTE)[0])
        self.assertIn("version=t is not a constant", sev(mp(card("bv", "nmos", 53, version="t"))[1], WARNING)[0])

    def test_hspice_junction_defaults(self):
        # DCAP=2 (HSPICE's default, 15-27): SPICE's formula with FC=0, any FC replaced; PB 0.8 (15-12)
        p, notes = mp(card("d1", "d", None, cjo=1e-12, m=0.5, fc=0.5))
        self.assertEqual((p["fc"], p["vj"]), (Num(0.0), Num(0.8)))
        self.assertNotIn("fcs", p)
        self.assertIn("HSPICE ignores fc=0.5 under DCAP=2", notes[0].message)
        # DCAP=1 (.option dcap=1, .option spice, dcap=1 on the card): SPICE's formula, FC as given
        for opts, extra in (({"dcap": 1.0}, {}), ({"spice": 1.0}, {}), ({}, {"dcap": 1.0})):
            p, _ = mp(card("d2", "d", None, cjo=1e-12, fc=0.5, **extra), **opts)
            self.assertEqual(p["fc"], Num(0.5), opts)
            self.assertNotIn("dcap", p)
        self.assertEqual(mp(card("d3", "d", None, cjo=1e-12, dcap=2), dcap=1.0)[0]["fc"], Num(0.0))
        with self.assertRaisesRegex(T.TableError, "DCAP=3"):
            mp(card("d4", "d", None, cjo=1e-12), dcap=3.0)
        with self.assertRaisesRegex(T.TableError, "dcap=4 must be 1, 2 or 3"):
            mp(card("d5", "d", None, dcap=4))
        self.assertEqual(mp(card("d6", "d", None, **{"is": 1e-14})), ({"is": Num(1e-14)}, []))
        # PB under an alias becomes vj; a sidewall gets PHP=PB and FCS=0 (and FC=0: the targets'
        # sidewall charge needs FC to match)
        p, _ = mp(card("d7", "d", None, cjsw=1e-13, pb=0.7))
        self.assertEqual((p["vj"], p["php"], p["fc"], p["fcs"]), (Num(0.7), Num(0.7), Num(0.0), Num(0.0)))
        self.assertNotIn("pb", p)
        # BJT: FC=0 for CJE/CJC (16-35), MJS 0.5 with CJS (16-11), not under .option spice (MJS=0)
        p, _ = mp(card("q1", "npn", 1, cje=1e-12, cjs=1e-12))
        self.assertEqual((p["fc"], p["mjs"]), (Num(0.0), Num(0.5)))
        p, _ = mp(card("q2", "npn", 1, cje=1e-12, cjs=1e-12), spice=1.0)
        self.assertEqual(sorted(p), ["cje", "cjs"])
        # JFET: PB 0.8 with a gate capacitance (17-18); capop=0 removed, another CAPOP a warning
        p, notes = mp(card("j1", "njf", 1, cgs=1e-12, capop=0))
        self.assertEqual((p["pb"], p["fc"]), (Num(0.8), Num(0.0)))
        self.assertNotIn("capop", p)
        self.assertIn("JFET CAPOP=2 gate capacitance", sev(mp(card("j2", "njf", 1, capop=2))[1], WARNING)[0])

    def test_coupled_inductance(self):
        l1 = I("l1", "l", ["a", "0"], value=P("1u"))
        self.assertIsNone(T.coupled_inductance(l1, None, None, 25.0, 25.0))
        self.assertEqual(T.coupled_inductance(l1, Num(2.0), None, 25.0, 25.0), Num(5e-7))
        self.assertEqual(X.to_text(T.coupled_inductance(l1, Name("$mfactor"), None, 25.0, 25.0)), "1e-06 / $mfactor")
        tc = I("l2", "l", ["a", "0"], value=P("1u"), params={"tc1": Num(0.01), "tc2": Num(1e-4), "dtemp": Num(5.0)})
        dt = 125.0 + 5.0 - 25.0
        self.assertAlmostEqual(T.coupled_inductance(tc, Num(2.0), None, 125.0, 25.0).value,
                               1e-6 * (1 + 0.01 * dt + 1e-4 * dt * dt) / 2)
        with self.assertRaisesRegex(T.TableError, "needs its inductance on the element"):
            T.coupled_inductance(I("l3", "l", ["a", "0"], master="lm"), Num(2.0), None, 25.0, 25.0)
        with self.assertRaisesRegex(T.TableError, "TC1/TC2"):
            T.coupled_inductance(l1, None, card("lm", "l", None, tc1=0.01), 25.0, 25.0)
        self.assertEqual(T.Scope([l1, tc, I("k1", "k", [], value=Num(0.5), ctrl=["l1", "l2"])]).coupled, {"l1", "l2"})
        nl = Netlist()
        self.assertEqual(T.run_temps(nl), (25.0, 25.0))
        nl.options = {"spice": Num(1.0)}
        self.assertEqual(T.run_temps(nl), (27.0, 27.0))

    def test_binning_bounds_dropped_for_vacask_only(self):
        m = card("n.1", "nmos", 54, lmin=1e-7, lmax=1e-6, wmin=1e-7, wmax=1e-6, vth0=0.4)
        self.assertEqual(sorted(dict(T.model_params(m, "vacask", binned=True)[0])), ["type", "vth0"])
        self.assertEqual(sorted(dict(T.model_params(m, "xyce", binned=True)[0])),
                         ["lmax", "lmin", "vth0", "wmax", "wmin"])

    def test_scale_and_multiplier(self):
        self.assertEqual(T.scaled("m", "w", Num(2.0), 1e-6), Num(2e-6))
        self.assertEqual(T.scaled("m", "ad", Num(3.0), 1e-6), Num(3.0 * 1e-12))
        self.assertEqual(T.scaled("m", "nf", Num(2.0), 1e-6), Num(2.0))
        self.assertEqual(T.scaled("d", "area", Num(4.0), 1e-3, 3), Num(4.0 * 1e-6))     # geometric diode
        self.assertEqual(T.scaled("d", "pj", Num(4.0), 1e-3, 3), Num(4.0 * 1e-3))
        self.assertEqual(T.scaled("d", "area", Num(4.0), 1e-3, 1), Num(4.0))            # LEVEL 1: unitless
        self.assertEqual(T.scaled("d", "pj", Num(4.0), 1e-3), Num(4.0))
        self.assertEqual(X.to_text(T.scaled("m", "l", Name("ll"), 1e-6)), "ll * 1e-06")
        self.assertIs(T.scaled("m", "w", Name("w"), 1.0).__class__, Name)
        self.assertIsNone(T.multiplier(None, False, "vacask"))
        self.assertEqual(X.to_vacask(T.multiplier(Num(2.0), True, "vacask")), "$mfactor*2.0")
        self.assertEqual(X.to_xyce(T.multiplier(None, True, "xyce")), "vamos_mfactor")
        self.assertEqual(T.multiplier(Num(3.0), False, "xyce"), Num(3.0))
        for engine in T.ENGINES:
            for k in ("v", "e", "h", "k"):
                self.assertEqual(T.MULT[engine][k], "none")
        self.assertEqual((T.MULT["xyce"]["i"], T.MULT["xyce"]["f"], T.MULT["xyce"]["j"]),
                         ("values", "gain", "area"))

    def test_bin_selection_and_guard(self):
        bins = [(card("n.1", "nmos", 54), (1e-7, 2.2e-7, 1e-7, 1e-6)),
                (card("n.2", "nmos", 54), (2.2e-7, 1e-5, 1e-7, 1e-6))]
        self.assertEqual(T.select_bin(bins, 0.22, 0.5, 1.0, 1e-6).name, "n.1")   # 2.1999999999999998e-07 < hi
        self.assertEqual(T.select_bin(bins[1:], 0.22, 0.5, 1.0, 1e-6).name, "n.2")  # the lower-bound tolerance
        self.assertEqual(T.select_bin(bins, 0.2, 1.6, 2.0, 1e-6).name, "n.1")    # per-finger 0.8u
        self.assertIsNone(T.select_bin(bins, 0.2, 1.6, 1.0, 1e-6))
        self.assertIsNone(T.select_bin(bins, 20.0, 0.5, 1.0, 1e-6))
        g = T.bin_guard((2.2e-7, 1e-5, 1e-7, 1e-6), Name("ll"), Num(1.6), Name("nf"), 1e-6)
        self.assertEqual(X.evaluate(g, {"ll": 0.22, "nf": 2.0}), 1.0)
        self.assertEqual(X.evaluate(g, {"ll": 0.21, "nf": 2.0}), 0.0)
        self.assertEqual(X.evaluate(g, {"ll": 0.5, "nf": 1.0}), 0.0)

    def test_sources(self):
        ok = Source(wave="pulse", args={k: Num(v) for k, v in
                                        dict(v1=0.0, v2=1.0, td=0.0, tr=1e-9, tf=1e-9, pw=5e-9).items()})
        self.assertNotIn("per", T.wave(ok, "v1"))
        bad = Source(wave="pulse", args=dict(ok.args, tr=Num(0.0)))
        with self.assertRaisesRegex(T.TableError, "never rise=0"):
            T.wave(bad, "v1")
        with self.assertRaisesRegex(T.TableError, "unresolved"):
            T.wave(Source(wave="pulse", args={"v1": Num(0.0)}), "v1")
        with self.assertRaisesRegex(T.TableError, "period"):
            T.wave(Source(wave="pulse", args=dict(ok.args, per=Num(6e-9))), "v1")
        exp = Source(wave="exp", args={k: Num(v) for k, v in
                                       dict(v1=0.0, v2=1.0, td1=2e-9, tau1=1e-9, td2=1e-9, tau2=1e-9).items()})
        with self.assertRaisesRegex(T.TableError, "td2"):
            T.wave(exp, "v2")
        pwl = Source(wave="pwl", points=[(Num(0.0), Num(0.0)), (Num(0.0), Num(1.0))])
        with self.assertRaisesRegex(T.TableError, "increasing"):
            T.wave(pwl, "v3")
        s = T.scale_source(Source(dc=Num(1.0), wave="sin", args={"vo": Num(0.5), "va": Num(1.0), "freq": Num(1e6)},
                                  ac=(Num(2.0), Num(90.0))), Num(3.0))
        self.assertEqual((s.dc, s.args["vo"], s.args["va"], s.args["freq"], s.ac),
                         (Num(3.0), Num(1.5), Num(3.0), Num(1e6), (Num(6.0), Num(90.0))))

    def test_deck_level_elements(self):
        uri = T.code_uri("vacask", "d2a", "tb.u.a<1>__d")
        self.assertEqual(uri, "code:libcosim_bridge.so:vacask_bridge_init:d2a:tb.u.a<1>__d")
        self.assertEqual(T.code_uri("xyce", "a2d", "x__a"), "code:libcosim_bridge.so:nvc_bridge_init:a2d:x__a")
        self.assertEqual(T.parse_code_uri(uri), ("vacask_bridge_init", "d2a", "tb.u.a<1>__d"))
        with self.assertRaises(T.TableError):
            T.code_uri("vacask", "d2x", "a")
        with self.assertRaises(T.TableError):
            T.code_source("v", "r", "a", "0", uri)
        g = T.gcond("g_n", "n", "n_d", "n_e", 500.7)
        self.assertEqual((g.kind, g.nodes, g.master, g.params), ("y", ["n", "n_d", "n_e"], "vamos_gcond",
                                                               {"rr": Num(500.7)}))
        sh = T.shunt("rsh_n", "n")
        self.assertEqual((sh.kind, sh.nodes, sh.value), ("r", ["n", "0"], Num(1e12)))
        from vamos.ams import names
        self.assertEqual(T.BRIDGE_SUFFIX_EN, names.BRIDGE_SUFFIX["en"])
        self.assertTrue(os.path.isfile(T.VA_SOURCE))
        with open(T.VA_SOURCE) as fh:
            self.assertIn("module vamos_gcond(n, nd, ne)", fh.read())


# The basic deck's cards as HSPICE simulates them (tables.hspice_card): notes only, no warning
# (the MOS CJ default needs no warning: no instance gives AD/AS).
BASIC_NOTES = [("note", "model nch", ["gamma=0.527625", "phi=0.576037", "cj=0.000101851", "fc=0"]),
               ("note", "model pch", ["gamma=0.527625", "phi=0.576037", "cj=0.000101851", "fc=0"]),
               ("note", "model dd", ["fc=0", "vj=0.8"])]


def basic_notes(notes):
    """(severity, card, the written key=value pairs) of each note."""
    return [(n.severity, n.message.split(":")[0], re.findall(r"(\w+=[-\w.+]+) \(", n.message)) for n in notes]


# -- VACASK text ---------------------------------------------------------------------------------

def _golden(test, name, text):
    path = fixture("netlist", name)
    if REGEN or not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(text)
        if not REGEN:
            test.fail("golden %s was missing; written (check it in)" % name)
    with open(path) as fh:
        want = fh.read()
    test.assertEqual(text, want, "deck differs from golden %s (VAMOS_REGEN_GOLDEN=1 rewrites it)" % name)


def _lines(text, pattern):
    return [l for l in text.splitlines() if re.search(pattern, l)]


class TestVacaskText(unittest.TestCase):
    def test_golden_basic(self):
        notes = []
        text = vacask.render(basic_netlist(), notes=notes)
        _golden(self, "emit_vacask_basic.sim", text)
        self.assertEqual(basic_notes(notes), BASIC_NOTES)

    def test_golden_ams_and_smoke(self):
        nl = ams_netlist("vacask")
        _golden(self, "emit_vacask_ams.sim", vacask.render(nl, osdi=[OSDI_PLACEHOLDER]))
        smoke = vacask.render(vacask.smoke_netlist(nl), vacask.SMOKE_ANALYSIS, [OSDI_PLACEHOLDER], op=True)
        _golden(self, "emit_vacask_smoke.sim", smoke)
        self.assertIn('vd_n_u_a (n_u_a_d 0) vamos_vsource dc=0.0', smoke)
        self.assertIn('ve_n_u_a (n_u_a_e 0) vamos_vsource dc=1.0', smoke)
        self.assertIn('ia_n_u_y (n_u_y 0) vamos_isource dc=0.0', smoke)
        self.assertIn("analysis vamos_smoke op", smoke)
        # The bridge sources run only under nvc; the smoke golden (run by TestVacaskRuns) is the
        # same deck except for them and the analysis.
        ams, smk = self.golden_pair("emit_vacask_ams.sim", "emit_vacask_smoke.sim")
        self.assertEqual(sorted(set(ams) - set(smk)), sorted(
            [l for l in ams if 'file="code:' in l] + ["  analysis vamos_tran tran step=1e-11 stop=1e-06 maxstep=1e-08"]))

    def golden_pair(self, a, b):
        with open(fixture("netlist", a)) as fa, open(fixture("netlist", b)) as fb:
            return fa.read().splitlines(), fb.read().splitlines()

    def test_golden_bin(self):
        _golden(self, "emit_vacask_bin.sim", vacask.render(bin_netlist()))

    def test_header_and_loads(self):
        text = vacask.render(basic_netlist())
        head = text.splitlines()
        self.assertEqual(head[0], "vamos emitter golden: basic")
        self.assertEqual(head[2], "ground 0")
        for f in ("spice/mos1.osdi", "spice/diode.osdi", "spice/bjt.osdi", "spice/jfet1.osdi",
                  "resistor.osdi", "capacitor.osdi", "inductor.osdi", "spice/resistor.osdi"):
            self.assertEqual(len(_lines(text, r'^load "%s"$' % re.escape(f))), 1, f)
        for m in ("vamos_vsource vsource", "vamos_mutual mutual", "vamos_cccs cccs", "vamos_ccvs ccvs"):
            self.assertEqual(len(_lines(text, "^model " + m + "$")), 1, m)

    def test_quoting_predicate(self):
        nl = Netlist(title="q")
        nl.body = [V("load", "vdd!", "0", 1.0), R("control", "vdd!", "d<0>", "1k"),
                   R("xi0<3>", "d<0>", "x1.n", "1k"), R("r1", "x1.n", "0", "1k")]
        nl.globals = ["vdd!"]
        nl.analyses = tran(1e-9, 1e-8)
        text = vacask.render(nl)
        self.assertIn("global 'vdd!'", text)
        self.assertIn("'load' ('vdd!' 0) vamos_vsource dc=1.0", text)
        self.assertIn("'control' ('vdd!' 'd<0>') vamos_resistor r=1000.0", text)
        self.assertIn("'xi0<3>' ('d<0>' 'x1.n') vamos_resistor r=1000.0", text)

    def test_mfactor(self):
        text = vacask.render(mfactor_netlist())
        self.assertIn("subckt rleaf (x)\n  parameters $mfactor=1.0\n  r1 (x 0) vamos_resistor r=1000.0 "
                      "$mfactor=$mfactor\nends", text)
        self.assertIn("  xi (x) rleaf $mfactor=$mfactor*2.0", text)
        self.assertIn("  r1 (x 0) vamos_resistor r=1000.0 $mfactor=$mfactor*2.0", text)
        self.assertIn("x1 (nx) rmid $mfactor=3.0", text)
        self.assertIn("  g1 (0 x) i=0.001*v(s)*$mfactor", text)
        self.assertIn("  i1 (0 x) vamos_isource dc=0.001 $mfactor=$mfactor", text)
        self.assertIn("  j1 (d 0 0) m_jn $mfactor=$mfactor", text)
        for l in _lines(text, r"\bvamos_mutual\b|\) [iv]="):
            self.assertNotIn("$mfactor=", l, l)                    # no $mfactor on b or mutual lines
        self.assertIn('  k1 () vamos_mutual k=0.5 ind1="l1" ind2="l2"', text)
        self.assertIn("  e1 (c 0) v=v(a)", text)
        # coupled inductors: the multiplier is folded into l= (the mutual divides by $mfactor itself)
        self.assertIn("  l1 (a1 0) vamos_inductor l=1e-06/$mfactor\n  l2 (b 0) vamos_inductor l=1e-06/$mfactor\n",
                      text)
        nl = Netlist(title="coupled at top level")
        nl.body = [I("l1", "l", ["p", "0"], value=P("1u"), params={"m": Num(2.0)}),
                   I("l2", "l", ["s", "0"], value=P("1u"), params={"tc1": Num(0.01), "dtemp": Num(5.0)}),
                   I("l3", "l", ["t", "0"], value=P("1u"), params={"m": Num(2.0), "tc1": Num(0.01)}),
                   I("k1", "k", [], value=Num(0.5), ctrl=["l1", "l2"])]
        nl.temp = 125.0
        nl.analyses = tran(1e-9, 1e-6)
        text = vacask.render(nl)
        self.assertIn("l1 (p 0) vamos_inductor l=5e-07\n", text)
        self.assertIn("l2 (s 0) vamos_inductor l=2.05e-06\n", text)            # 1u*(1+0.01*(125+5-25))
        self.assertIn("l3 (t 0) vamos_sp_inductor l=1e-06 tc1=0.01 $mfactor=2.0\n", text)   # not coupled
        nl.body[-1] = I("k1", "k", [], value=Num(0.5), ctrl=["l1", "l9"])
        nl.body.append(I("l9", "l", ["q", "0"], master="lm", params={"m": Num(2.0)}))
        nl.body.append(card("lm", "l", None))
        with self.assertRaisesRegex(NoteError, "l9: a coupled inductor needs its inductance on the element: "
                                               "neither engine couples an inductor with a multiplier"):
            vacask.render(nl)
        with self.assertRaisesRegex(NoteError, "l9: a coupled inductor needs its inductance on the element"):
            xyce.render(nl)

    def test_scale_applied_once(self):
        text = vacask.render(bin_netlist())
        self.assertIn("mr1 (r1 g 0 0) m_ref1 w=1.6e-06 l=2e-07 nf=2.0 ad=1e-12 as=1e-12", text)
        self.assertIn("mr2 (r2 g 0 0) m_ref2 w=2e-06 l=2.1999999999999998e-07", text)
        self.assertNotIn("scale", text.split("control")[1])

    def test_binning_chain(self):
        text = vacask.render(bin_netlist())
        self.assertIn("model 'm_nch.1' sp_bsim4v8 type=1.0 vth0=0.3 version=\"4.8\"", text)
        self.assertNotIn("lmin", text)
        chain = text[text.index("@if", text.index("vd1 ")):text.index("@end", text.index("vd1 "))]
        self.assertEqual(len(_lines(chain, r"^\s*m1 ")), 5)               # four bins and the @else
        self.assertIn("@else\nm1 (d1 g 0 0) m_nch w=1.6e-06 l=2e-07 nf=2.0 ad=1e-12 as=1e-12".replace("\nm1", "\n  m1"),
                      text)
        # constant geometry: the guards fold, and exactly one is true
        self.assertEqual(_lines(chain, r"^@(?:else)?if "), ["@if 1.0", "@elseif 0.0", "@elseif 0.0",
                                                            "@elseif 0.0"])

    def test_binned_geometry_from_subckt_parameters(self):
        nl = bin_netlist()
        cell = Subckt("cell", ["d", "g"], params=[Param("ll", Num(0.2)), Param("nfin", Num(2.0))], body=[
            I("mx", "m", ["d", "g", "0", "0"], master="nch",
              params={"w": Num(1.6), "l": Name("ll"), "nf": Name("nfin")})])
        nl.body.insert(4, cell)
        nl.body.append(I("xc", "x", ["d1", "g"], master="cell", params={"ll": Num(1.0)}))
        text = vacask.render(nl)
        self.assertIn("  @if real(((ll*1e-06>=1e-07||abs(ll*1e-06-1e-07)<1e-15)&&ll*1e-06<2.2e-07)"
                      "&&((1.6e-06/nfin>=1e-07||abs(1.6e-06/nfin-1e-07)<1e-15)&&1.6e-06/nfin<1e-06))\n",
                      text)
        self.assertIn("    mx (d g 0 0) 'm_nch.1' w=1.6e-06 l=ll*1e-06 nf=nfin $mfactor=$mfactor", text)

    def test_subckt_parameters_shadow_top_level_values(self):
        nl = bin_netlist()
        nl.body.insert(0, Param("ll", Num(30.0)))          # no bin fits l=30u ...
        nl.values = {"ll": 30.0}
        cell = Subckt("cell", ["d", "g"], params=[Param("ll", Num(0.2)), Param("l2", P("2*ll"))], body=[
            I("mx", "m", ["d", "g", "0", "0"], master="nch", params={"w": Num(1.6), "l": Name("ll")})])
        nl.body.insert(5, cell)                               # ... but the cell's own ll does
        nl.body.append(I("xc", "x", ["d1", "g"], master="cell"))
        text = vacask.render(nl)
        self.assertIn("  parameters l2=2.0*ll\n", text)       # not folded to 60.0
        self.assertIn("    mx (d g 0 0) 'm_nch.1' w=1.6e-06 l=ll*1e-06", text)
        self.assertIn("m2 d2 g 0 0 nch w=2e-06", xyce.render(nl))

    def test_no_bin_for_constant_geometry_is_an_error(self):
        with self.assertRaises(NoteError) as cm:
            vacask.render(bin_netlist(with_nobin=True))
        self.assertEqual([n.origin for n in cm.exception.notes], ["deck:42"])
        self.assertEqual(cm.exception.notes[0].message, "no bin of nch for l=2e-05, w=5e-07 (instance m9)")

    def test_sources_text(self):
        text = vacask.render(basic_netlist())
        self.assertIn('vin (in 0) vamos_vsource type="pulse" val0=0.0 val1=vdd delay=1e-09 rise=1e-10 '
                      'fall=1e-10 width=4e-09 period=1e-08', text)
        self.assertIn('vsin (s 0) vamos_vsource type="sine" sinedc=0.5 ampl=0.2 freq=100000000.0 delay=1e-09 '
                      'theta=0.0 tdphase=0.0', text)
        self.assertIn('vexp (e 0) vamos_vsource type="exp" val0=0.0 val1=1.0 delay=1e-09 tau1=1e-09 '
                      'td2=1.9999999999999997e-09 tau2=1e-09', text)      # td2 relative to delay
        self.assertIn('vpwl (p 0) vamos_vsource type="pwl" wave=[0.0, 0.0, 1e-09, 1.0, 2e-09, 0.5] '
                      'delay=5e-10', text)

    def test_control_block_floats(self):
        nl = pulse_netlist()
        nl.analyses = [Analysis("tran", {"step": P("1n"), "stop": P("tsim"), "maxstep": 1e-10, "uic": True})]
        nl.values = {"tsim": 2e-8}
        nl.temp, nl.tnom = 85.0, None
        nl.ics = {"a": 0.5, "x1.n": 0.25}
        nl.nodesets = {"b": 0.1}
        nl.options = {"gmin": Num(1e-13)}
        ctl = vacask.render(nl).split("control\n")[1]
        self.assertEqual(ctl, '  abort always\n  options tran_lteimplicit=0 temp=85.0 tnom=25.0 gmin=1e-13\n'
                         '  save v(a)\n  analysis vamos_tran tran step=1e-09 stop=2e-08 maxstep=1e-10 '
                         'icmode="uic" ic={"a", 0.5, "x1:n", 0.25} nodeset={"b", 0.1}\nendc\n')
        nl.temp, nl.options = None, {"spice": Num(1.0)}
        self.assertIn("temp=27.0 tnom=27.0", vacask.render(nl))

    def test_solver_options(self):
        nl = pulse_netlist()
        nl.options = {"gmin": Num(1e-13), "method": Str("GEAR"), "reltol": Num(1e-4), "vntol": Num(1e-7)}
        notes = []
        self.assertIn('options tran_lteimplicit=0 temp=25.0 tnom=25.0 gmin=1e-13 tran_method="gear"\n',
                      vacask.render(nl, notes=notes))
        self.assertEqual([n.message.split(" is ")[0] for n in notes], [".option reltol=0.0001", ".option vntol=1e-07"])
        notes = []
        text = xyce.render(nl, notes=notes)
        self.assertIn(".options device temp=25.0 tnom=25.0 gmin=1e-13\n.options timeint method=gear\n", text)
        self.assertEqual(len(notes), 2)
        nl.options = {"method": Str("trap")}
        notes = []
        self.assertNotIn("tran_method", vacask.render(nl, notes=notes))
        self.assertEqual(notes, [])
        nl.options = {"method": Str("bdf")}
        self.assertEqual(len(T.solver_options(nl)[1]), 1)

    def test_dependent_parameters_stay_overridable(self):
        text = vacask.render(dependent_netlist())
        self.assertIn("subckt cell (a)\n  parameters w=1.0\n"
                      "  parameters l=-1.2345678901234568e-300\n"
                      "  parameters l__v=((l==(-1.2345678901234568e-300) ? 2.0*w : l))\n"
                      "  parameters r=-1.2345678901234568e-300\n"
                      "  parameters r__v=((r==(-1.2345678901234568e-300) ? l__v*1000.0 : r))\n"
                      "  parameters $mfactor=1.0\n"
                      "  r1 (a 0) vamos_resistor r=r__v $mfactor=$mfactor\nends", text)
        basic = vacask.render(basic_netlist())
        self.assertIn("  parameters wp=2.4999999999999998e-06\n", basic)     # top-level wn folded: primary

    def test_errors_are_collected(self):
        nl = Netlist(title="bad")
        nl.body = [card("m9", "nmos", 9, origin="lib:1"), R("r1", "a", "0", "1k"),
                   I("v1", "v", ["a", "0"], source=Source(wave="pulse", args={"v1": Num(0.0)}), origin="deck:3"),
                   I("x1", "x", ["a"], master="nosuch", origin="deck:4"),
                   I("c1", "c", ["a", "0"], value=Num(1e-12), params={"ic": Num(0.5)}, origin="deck:5"),
                   I("b1", "b", ["a", "0"], expr=P("vm(a)"), expr_kind="v", origin="deck:6"),
                   I("m1", "m", ["a", "a", "0", "0"], master="m9", params={"w": P("1u"), "l": P("1u")},
                     origin="deck:7")]
        nl.analyses = tran(1e-9, 1e-8)
        with self.assertRaises(NoteError) as cm:
            vacask.render(nl)
        got = {n.origin: n.message for n in cm.exception.notes}
        self.assertEqual(sorted(got), ["deck:3", "deck:4", "deck:5", "deck:6", "deck:7", "lib:1"])
        self.assertIn("level 9", got["lib:1"])
        self.assertIn("level 9", got["deck:7"])
        self.assertIn("unresolved", got["deck:3"])
        self.assertIn("IC=", got["deck:5"])

    def test_unused_definitions_are_not_printed(self):
        """A library defines more than a deck uses: only reachable subckts and cards are printed,
        so a construct no target honours fails only a deck that uses it (§4.3.6, sky130)."""
        lib = Subckt("libcell", ["a"], body=[card("inner", "nmos", 1, vto=0.5),
                                             I("m1", "m", ["a", "a", "0", "0"], master="m9",
                                               params={"w": P("1u"), "l": P("1u")})])
        used = Subckt("usedcell", ["a"], body=[card("own", "d", 1, **{"is": 1e-14}),
                                               card("spare", "d", 1, **{"is": 2e-14}),
                                               I("d1", "d", ["a", "0"], master="own")])
        nl = Netlist(title="library")
        nl.body = [card("m9", "nmos", 9, origin="lib:1"), card("d3", "d", 3, origin="lib:2"),
                   card("top", "d", 1, **{"is": 1e-15}), lib, used,
                   V("v1", "a", "0", 0.6), I("x1", "x", ["a"], master="usedcell"),
                   I("d2", "d", ["a", "0"], master="top")]
        nl.analyses = tran(1e-9, 1e-8)
        for render in (vacask.render, xyce.render):
            text = render(nl)
            self.assertNotIn("libcell", text)
            self.assertNotRegex(text, r"(?m)^\s*(?:model|\.model) (?:m_)?(?:m9|d3|spare|inner) ")
            self.assertRegex(text, r"(?m)^\s*(?:model|\.model) (?:m_)?own ")
            self.assertRegex(text, r"(?m)^\s*(?:model|\.model) (?:m_)?top ")

    def test_code_uri_must_be_for_vacask(self):
        nl = ams_netlist("xyce")
        with self.assertRaisesRegex(NoteError, "not for VACASK"):
            vacask.render(nl, osdi=[OSDI_PLACEHOLDER])

    def test_probes(self):
        nl = basic_netlist()
        nl.probes = [("tran", "v", "*"), ("tran", "v", "a,b"), ("tran", "i", "x1.rsrc"), ("tran", "i", "ebeh"),
                     ("op", "v", "zz")]
        save = _lines(vacask.render(nl), "^  save ")
        self.assertEqual(save, ["  save default v(a) v(b) p('x1:rsrc', i) i(ebeh)"])
        nl.probes = [("tran", "i", "mn")]
        with self.assertRaisesRegex(NoteError, "no such element"):
            vacask.render(nl)

    def test_verilog_a_instances(self):
        nl = va_netlist()
        text = vacask.render(nl)
        self.assertIn('load "%s"\n' % fixture("netlist", "emit_vres.va"), text)    # no osdi: VACASK compiles
        self.assertIn("model y1__va vres r=500.0\ny1 (s 0) y1__va $mfactor=2.0\n", text)
        self.assertIn("model y2__va vres\ny2 (t 0) y2__va\n", text)
        text = vacask.render(nl, osdi=["/abs/1_vres.osdi"])
        self.assertIn('load "/abs/1_vres.osdi"\n', text)
        self.assertNotIn("emit_vres.va", text)
        nl.body.insert(0, Subckt("y1__va", ["a"], body=[R("r", "a", "0", 1)]))
        self.assertIn("model y1__va_1 vres r=500.0\ny1 (s 0) y1__va_1 $mfactor=2.0\n", vacask.render(nl))


class TestXyceText(unittest.TestCase):
    def test_golden_basic(self):
        notes = []
        _golden(self, "emit_xyce_basic.cir", xyce.render(basic_netlist(), notes=notes))
        self.assertEqual(basic_notes(notes), BASIC_NOTES)

    def test_golden_ams_and_smoke(self):
        nl = ams_netlist("xyce")
        text = xyce.render(nl)
        _golden(self, "emit_xyce_ams.cir", text)
        self.assertIn('vd_n_u_a n_u_a_d 0 PWL FILE "code:libcosim_bridge.so:nvc_bridge_init:d2a:tb.u.a__d"', text)
        self.assertIn("bg_n_u_a n_u_a_d n_u_a I={v(n_u_a_e)*(v(n_u_a_d)-v(n_u_a))/500.7}", text)
        self.assertIn("rsh_n_u_a n_u_a 0 1000000000000.0", text)
        smoke = xyce.render(vacask.smoke_netlist(nl), op=True)
        _golden(self, "emit_xyce_smoke.cir", smoke)
        self.assertIn("ve_n_u_a n_u_a_e 0 DC 1.0", smoke)
        self.assertNotIn(".print", smoke)
        self.assertIn("\n.op\n.end\n", smoke)
        # As on VACASK: the run smoke golden differs only in the bridge sources and the analysis.
        with open(fixture("netlist", "emit_xyce_ams.cir")) as fa, open(fixture("netlist", "emit_xyce_smoke.cir")) as fb:
            ams, smk = fa.read().splitlines(), fb.read().splitlines()
        self.assertEqual(sorted(set(ams) - set(smk)), sorted(
            [l for l in ams if 'PWL FILE "code:' in l] + [
                ".tran 1e-11 1e-06 0.0 1e-08", ".print tran format=raw file=vamos_tran.raw v(n_u_a) v(n_u_y)"]))

    def test_golden_bin(self):
        _golden(self, "emit_xyce_bin.cir", xyce.render(bin_netlist()))

    def test_plain_xyce_syntax(self):
        text = xyce.render(basic_netlist())
        self.assertEqual(text.splitlines()[0], "* vamos emitter golden: basic")
        self.assertIn(".param gain={vdd/0.9}", text)
        self.assertIn(".subckt inv a y vdd params: wp={2.5*wn} vamos_mfactor=1.0", text)
        self.assertIn("vsup vdd 0 DC {vdd}", text)
        self.assertIn("vin in 0 PULSE(0.0 {vdd} 1e-09 1e-10 1e-10 4e-09 1e-08)", text)
        self.assertIn("vexp e 0 EXP(0.0 1.0 1e-09 1e-09 3e-09 1e-09)", text)   # td2 absolute
        # PWL TD=: the delay is added to the times (Xyce's TD= gives 0, not v1, before it)
        self.assertIn("vpwl p 0 PWL 5e-10 0.0 1.5000000000000002e-09 1.0 2.5e-09 0.5", text)
        self.assertNotIn("TD=", text)
        self.assertIn("bebeh bb 0 V={v(out)*0.5}", text)
        self.assertIn("bgbeh 0 bc I={0.001*v(in)}", text)
        self.assertIn("k1 l1 l2 0.5", text)
        self.assertIn("f1 0 fa vprobe 2.0", text)
        self.assertIn("q1 sb sb 0 qn", text)
        self.assertIn(".options device temp=25.0 tnom=25.0", text)
        self.assertIn(".print tran format=raw file=vamos_tran.raw v(out) v(out2) i(vsup) v(x1:ns) v(bc) v(fa) "
                      "v(ha)", text)
        self.assertTrue(text.endswith(".end\n"))
        self.assertNotRegex(text, r"\^|\blog\(")
        self.assertEqual(xyce.RAW, "vamos_tran.raw")
        from vamos.ams import layout
        self.assertEqual(xyce.RAW, layout.RAW)

    def test_verilog_a_instances(self):
        nl = va_netlist(params=False)
        text = xyce.render(nl)
        self.assertIn('.hdl "%s"\n' % fixture("netlist", "emit_vres.va"), text)
        self.assertIn(".model vres__vamos vres\nyvres y2 t 0 vres__vamos\n", text)
        nl = va_netlist(params=False)
        sub = Subckt("vcell", ["a"], body=[I("yy", "y", ["a", "0"], master="vres", origin="deck:8")])
        nl.body += [sub, I("xa", "x", ["t"], master="vcell"), I("xb", "x", ["t"], master="vcell",
                                                                   params={"m": Num(2.0)})]
        with self.assertRaisesRegex(NoteError, "takes no multiplier"):
            xyce.render(nl)

    def test_unsupported_element_parameters(self):
        nl = pulse_netlist()
        nl.body[0].params["tc1"] = Num(1e-3)
        nl.body.append(I("e1", "b", ["b", "0"], expr=P("v(a)"), expr_kind="v", params={"smooth": Num(1.0)}))
        for emitter in (vacask.render, xyce.render):
            with self.assertRaises(NoteError) as cm:
                emitter(nl)
            self.assertEqual(sorted(n.message for n in cm.exception.notes),
                             ["e1: parameter smooth not supported on a B element",
                              "v1: parameter tc1 not supported on a V element"])

    def test_message_parsing(self):
        out = ("***** Reading and parsing netlist...\n"
               "Netlist warning in file smoke.cir at or near line 2\n"
               " No model parameter LMIN found for model NCH.1 of type NMOS, parameter\n"
               " ignored.\n"
               "Netlist warning in file smoke.cir at or near line 3\n"
               " No model parameter BOGUS found for model NCH of type NMOS, parameter ignored.\n"
               "Netlist error in file smoke.cir at or near line 5\n"
               " Unrecognized parameter FOO for device M1\n"
               "\n")
        msgs = xyce._messages(out)
        self.assertEqual(msgs[0], "Netlist warning in file smoke.cir at or near line 2 No model parameter LMIN "
                                  "found for model NCH.1 of type NMOS, parameter ignored.")
        self.assertEqual(len(msgs), 3)
        self.assertEqual([xyce._bad_param(m, {"nch.1"}) for m in msgs], [False, True, False])
        self.assertEqual([xyce._bad_param(m, set()) for m in msgs], [True, True, False])
        self.assertTrue(xyce._UNREC.search(msgs[2]))

    def test_mfactor_is_carried_explicitly(self):
        text = xyce.render(mfactor_netlist())
        self.assertIn(".subckt rmid x params: vamos_mfactor=1.0\nxi x rleaf vamos_mfactor={vamos_mfactor*2.0}", text)
        self.assertIn("x1 nx rmid vamos_mfactor=3.0", text)
        self.assertIn("r1 x 0 1000.0 m={vamos_mfactor*2.0}", text)
        self.assertIn("bg1 0 x I={0.001*v(s)*vamos_mfactor}", text)
        self.assertIn("i1 0 x DC {0.001*vamos_mfactor}", text)        # Xyce ignores m on I sources
        self.assertIn("j1 d 0 0 jn {vamos_mfactor}", text)             # and drops it on JFETs
        self.assertNotRegex(text, r"(?m)^x\S* .* m=")                  # never X-line m=
        for l in _lines(text, r"^(?:k1|be1) "):
            self.assertNotIn("vamos_mfactor", l)
        # coupled inductors: Xyce's K pass drops m=, so the multiplier is folded into the value
        self.assertIn("l1 a1 0 {1e-06/vamos_mfactor}\nl2 b 0 {1e-06/vamos_mfactor}\nk1 l1 l2 0.5\n", text)
        nl = Netlist(title="coupled at top level")
        nl.body = [I("l1", "l", ["p", "0"], value=P("1u"), params={"m": Num(2.0)}),
                   I("l2", "l", ["s", "0"], value=P("1u"), params={"tc1": Num(0.01), "tc2": Num(0.0)}),
                   I("k1", "k", [], value=Num(0.5), ctrl=["l1", "l2"])]
        nl.temp = 125.0
        nl.analyses = tran(1e-9, 1e-6)
        self.assertIn("l1 p 0 5e-07\nl2 s 0 2e-06\nk1 l1 l2 0.5\n", xyce.render(nl))

    def test_binning(self):
        text = xyce.render(bin_netlist())
        self.assertIn(".model nch.1 nmos level=54 lmin=1e-07 lmax=2.2e-07 wmin=1e-07 wmax=1e-06 vth0=0.3", text)
        self.assertIn("m1 d1 g 0 0 nch.1 w=1.6e-06 l=2e-07 nf=2.0", text)   # nf=2: vamos picks the bin
        self.assertIn("m2 d2 g 0 0 nch w=2e-06 l=2.1999999999999998e-07", text)   # native binning
        with self.assertRaisesRegex(NoteError, "no bin of nch for l=2e-05"):
            xyce.render(bin_netlist(with_nobin=True))
        # nf=2 inside a subckt: l, w and nf are evaluated per instance path (sky130 wrappers pass
        # nf={nf}); each distinct binding gets its own copy of the subckt
        nl = bin_netlist()
        cell = Subckt("cell", ["d", "g"], params=[Param("ll", Num(0.2))], body=[
            I("mx", "m", ["d", "g", "0", "0"], master="nch", params={"w": Num(1.6), "l": Name("ll"),
                                                                     "nf": Num(2.0)}, origin="deck:7")])
        nl.body.insert(4, cell)
        nl.body += [I("xa", "x", ["d1", "g"], master="cell"),
                    I("xb", "x", ["d2", "g"], master="cell", params={"ll": Num(0.5)}),
                    I("xc", "x", ["d3", "g"], master="cell", params={"ll": Num(0.2)})]
        text = xyce.render(nl)
        self.assertIn(".subckt cell__vb1 d g params: ll=0.2 vamos_mfactor=1.0\n"
                      "mx d g 0 0 nch.1 w=1.6e-06 l={ll*1e-06} nf=2.0 m={vamos_mfactor}\n.ends cell__vb1\n", text)
        self.assertIn(".subckt cell__vb2 d g params: ll=0.2 vamos_mfactor=1.0\n"
                      "mx d g 0 0 nch.3 w=1.6e-06 l={ll*1e-06} nf=2.0 m={vamos_mfactor}\n.ends cell__vb2\n", text)
        self.assertIn("xa d1 g cell__vb1\n", text)
        self.assertIn("xb d2 g cell__vb2 ll=0.5\n", text)
        self.assertIn("xc d3 g cell__vb1 ll=0.2\n", text)
        self.assertNotIn(".subckt cell ", text)              # every instance needed a binding
        cell.params[0] = Param("ll", P("0.2+temper*0"))       # not a number at compile time
        with self.assertRaisesRegex(NoteError, "constant at compile time"):
            xyce.render(nl)

    def test_refusals(self):
        nl = Netlist(title="refuse")
        nl.body = [card("d3", "d", 3, origin="lib:2", **{"is": 1e-14}), V("v1", "a", "0", 0.7),
                   I("d1", "d", ["a", "0"], master="d3", origin="deck:2"),
                   I("y1", "y", ["a", "0"], master="myres", params={"r": Num(1e3)}, origin="deck:3")]
        nl.analyses = tran(1e-9, 1e-8)
        with self.assertRaises(NoteError) as cm:
            xyce.render(nl)
        got = {n.origin: n.message for n in cm.exception.notes}
        self.assertIn("geometric diode", got["lib:2"])
        self.assertIn("PyMS ignores", got["deck:3"])
        self.assertIn("deck:2", got)

    def test_names_and_case(self):
        nl = Netlist(title="names")
        nl.body = [V("v1", "A", "0", 1.0), R("r1", "a", "0", "1k")]
        nl.analyses = tran(1e-9, 1e-8)
        with self.assertRaisesRegex(NoteError, "differ only in case"):
            xyce.render(nl)
        nl.body = [V("v1", "a", "0", 1.0), Param("vamos_mfactor", Num(1.0))]
        with self.assertRaisesRegex(NoteError, "reserved"):
            xyce.render(nl)

    def test_ic_and_nodeset(self):
        nl = pulse_netlist()
        nl.ics, nl.nodesets = {"a": 0.5}, {"a": 0.1}
        notes = []
        text = xyce.render(nl, notes=notes)
        self.assertIn(".ic v(a)=0.5", text)
        self.assertNotIn(".nodeset", text)
        self.assertEqual([n.severity for n in notes], [NOTE])
        nl.ics = {}
        self.assertIn(".nodeset v(a)=0.1", xyce.render(nl))
        nl.analyses = [Analysis("tran", {"step": 1e-9, "stop": 2e-8, "maxstep": 1e-10, "uic": True})]
        self.assertIn(".tran 1e-09 2e-08 0.0 1e-10 UIC", xyce.render(nl))


# -- engine runs -------------------------------------------------------------------------------------

def _compile_ie(tmp):
    osdi = os.path.join(tmp, "vamos_ie.osdi")
    if not os.path.exists(osdi):
        r = run([openvaf_bin(), T.VA_SOURCE, "-o", osdi], cwd=tmp)
        if r.returncode != 0:
            raise AssertionError("openvaf-r failed:\n" + r.stdout)
    return osdi


class _Engines(TempDir):
    def vacask(self, text, sub="v"):
        d = os.path.join(self.tmp, sub)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "deck.sim"), "w") as fh:
            fh.write(text)
        env = dict(os.environ)
        env.setdefault("SIM_OPENVAF", openvaf_bin())
        r = run([vacask_bin(), "deck.sim"], cwd=d, env=env)
        self.assertEqual(r.returncode, 0, "VACASK failed:\n%s\n--- deck\n%s" % (r.stdout[-3000:], text))
        return rawfile.read(os.path.join(d, "vamos_tran.raw"))

    def xyce(self, text, sub="x"):
        d = os.path.join(self.tmp, sub)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "deck.cir"), "w") as fh:
            fh.write(text)
        r = run([xyce_bin(), "deck.cir"], cwd=d, env=xyce_env())
        self.assertEqual(r.returncode, 0, "Xyce failed:\n%s\n--- deck\n%s" % (r.stdout[-3000:], text))
        return rawfile.read(os.path.join(d, xyce.RAW))

    def golden(self, name):
        with open(fixture("netlist", name)) as fh:
            return fh.read()


@needs_vacask
class TestVacaskRuns(_Engines):
    def test_goldens_run(self):
        raw = self.vacask(self.golden("emit_vacask_basic.sim"), "basic")
        self.assertAlmostEqual(raw.at("v(bc)", 0.0), 0.0, places=6)
        self.assertGreater(len(raw.points), 50)
        self.vacask(self.golden("emit_vacask_bin.sim"), "bin")
        osdi = _compile_ie(self.tmp)
        smoke = self.golden("emit_vacask_smoke.sim").replace(OSDI_PLACEHOLDER, osdi)
        d = os.path.join(self.tmp, "smoke")
        os.makedirs(d)
        with open(os.path.join(d, "smoke.sim"), "w") as fh:
            fh.write(smoke)
        r = run([vacask_bin(), "smoke.sim"], cwd=d, env=dict(os.environ, SIM_OPENVAF=openvaf_bin()))
        self.assertEqual(r.returncode, 0, r.stdout)
        op = rawfile.read(os.path.join(d, "vamos_smoke.raw"))
        self.assertAlmostEqual(op.column("n_u_a")[0], 0.0, places=6)      # value 0 through the gcond
        self.assertGreater(op.column("n_u_y")[0], 1.79)                   # inverter output high

    def test_smoke_check(self):
        osdi = _compile_ie(self.tmp)
        self.assertEqual(vacask.smoke(ams_netlist("vacask"), os.path.join(self.tmp, "deck"), [osdi]), [])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "deck", "smoke.log")))
        nl = ams_netlist("vacask")
        nl.body[0].params["bogus"] = Num(1.0)
        n = vacask.smoke(nl, os.path.join(self.tmp, "deck2"), [osdi])
        self.assertEqual([x.severity for x in n], [ERROR])
        self.assertIn("bogus", n[0].message)

    def test_smoke_maps_no_bin(self):
        nl = bin_netlist()
        cell = Subckt("cell", ["d", "g"], params=[Param("ll", Num(0.2))], body=[
            I("mx", "m", ["d", "g", "0", "0"], master="nch", params={"w": Num(1.6), "l": Name("ll")})])
        nl.body.insert(4, cell)
        nl.body.append(I("xc", "x", ["d1", "g"], master="cell", params={"ll": Num(30.0)}))
        n = vacask.smoke(nl, os.path.join(self.tmp, "nb"))
        self.assertEqual(len(n), 1, n)
        self.assertIn("no bin of nch for l=ll, w=1.6 (scale 1e-06; instance mx in subckt cell)", n[0].message)

    def test_gcond_polarity(self):
        osdi = _compile_ie(self.tmp)
        for en, want in ((1.0, 1000.0 / 1500.0), (0.0, 0.0)):
            raw = self.vacask(vacask.render(gcond_netlist(en), osdi=[osdi]), "g%d" % en)
            self.assertAlmostEqual(raw.at("n", 0.0), want, places=4)

    def test_floating_gate_needs_its_shunt(self):
        nl = inverter_netlist()
        nl.body.append(I("mf", "m", ["out1", "nc_1", "0", "0"], master="nch", params={"w": P("1u"), "l": P("1u")}))
        n = vacask.smoke(nl, os.path.join(self.tmp, "f1"))
        self.assertEqual([x.severity for x in n], [ERROR])
        nl.body.append(T.shunt("rsh_nc_1", "nc_1"))
        self.assertEqual(vacask.smoke(nl, os.path.join(self.tmp, "f2")), [])

    def test_verilog_a(self):
        raw = self.vacask(vacask.render(va_netlist()), "va")
        self.assertAlmostEqual(raw.at("i(v1)", 0.0), -2.0 / 500.0, delta=1e-12)    # model card r=500, m=2
        self.assertAlmostEqual(raw.at("i(v2)", 0.0), -1.0 / 2000.0, delta=1e-12)   # the module default


@needs_xyce
class TestXyceRuns(_Engines):
    def test_verilog_a(self):
        raw = self.xyce(xyce.render(va_netlist(params=False)), "va")
        self.assertAlmostEqual(raw.at("i(v2)", 0.0), -1.0 / 2000.0, delta=1e-12)

    def test_smoke_maps_no_bin(self):
        nl = bin_netlist()
        cell = Subckt("cell", ["d", "g"], params=[Param("ll", Num(0.2))], body=[
            I("mx", "m", ["d", "g", "0", "0"], master="nch", params={"w": Num(1.6), "l": Name("ll")})])
        nl.body.insert(4, cell)
        nl.body.append(I("xc", "x", ["d1", "g"], master="cell", params={"ll": Num(30.0)}))
        # the geometry is a number on this instance path: vamos says so before Xyce runs
        n = xyce.smoke(nl, os.path.join(self.tmp, "nb"))
        self.assertEqual(len(n), 1, n)
        self.assertIn("no bin of nch for l=3e-05, w=1.6e-06 (instance mx in subckt cell)", n[0].message)
        # not a number at compile time: Xyce bins natively and its message is mapped
        nl.body[-1] = I("xc", "x", ["d1", "g"], master="cell", params={"ll": P("30+temper*0")})
        n = xyce.smoke(nl, os.path.join(self.tmp, "nb2"))
        self.assertEqual(len(n), 1, n)
        self.assertIn("no bin of nch for l=ll, w=1.6 (scale 1e-06; instance mx in subckt cell)", n[0].message)

    def test_goldens_run(self):
        raw = self.xyce(self.golden("emit_xyce_basic.cir"), "basic")
        self.assertGreater(len(raw.points), 50)
        self.xyce(self.golden("emit_xyce_bin.cir"), "bin")
        d = os.path.join(self.tmp, "smoke")
        os.makedirs(d)
        with open(os.path.join(d, "smoke.cir"), "w") as fh:
            fh.write(self.golden("emit_xyce_smoke.cir"))
        r = run([xyce_bin(), "smoke.cir"], cwd=d, env=xyce_env())
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_smoke_check(self):
        self.assertEqual(xyce.smoke(ams_netlist("xyce"), os.path.join(self.tmp, "deck")), [])
        nl = ams_netlist("xyce")
        nl.body[0].params["bogus"] = Num(1.0)           # Xyce would only warn and ignore it
        n = xyce.smoke(nl, os.path.join(self.tmp, "deck2"))
        self.assertEqual([x.severity for x in n], [ERROR])
        self.assertIn("No model parameter BOGUS found for model NCH", n[0].message)
        self.assertEqual(xyce.smoke(bin_netlist(), os.path.join(self.tmp, "bin")), [])   # bounds exempt

    def test_gcond_polarity(self):
        for en, want in ((1.0, 1000.0 / 1500.0), (0.0, 0.0)):
            raw = self.xyce(xyce.render(gcond_netlist(en)), "g%d" % en)
            self.assertAlmostEqual(raw.at("n", 0.0), want, places=4)

    def test_floating_gate_needs_its_shunt(self):
        nl = inverter_netlist()
        nl.body.append(I("mf", "m", ["out1", "nc_1", "0", "0"], master="nch", params={"w": P("1u"), "l": P("1u")}))
        n = xyce.smoke(nl, os.path.join(self.tmp, "f1"))
        self.assertEqual([x.severity for x in n], [ERROR])
        nl.body.append(T.shunt("rsh_nc_1", "nc_1"))
        self.assertEqual(xyce.smoke(nl, os.path.join(self.tmp, "f2")), [])


class _Both(_Engines):
    """Physics checks that need both engines: same deck, same answer."""

    def both(self, nl, sub, osdi=None):
        o = [osdi] if osdi else []
        return self.vacask(vacask.render(nl, osdi=o), sub + "_v"), self.xyce(xyce.render(nl), sub + "_x")


@needs_vacask
@needs_xyce
class TestCrossEngine(_Both):
    def test_polarity(self):
        rv, rx = self.both(inverter_netlist(), "pol")
        for raw in (rv, rx):
            self.assertLess(raw.at("out1", 1e-9), 0.01)               # vin=1.8: ~0 V (NMOS on, PMOS off)
            self.assertGreater(raw.at("out0", 1e-9), 1.79)            # vin=0: ~1.8 V
        self.assertAlmostEqual(rv.at("c", 1e-9), rx.at("c", 1e-9), delta=1e-4)   # the PNP bias point
        self.assertAlmostEqual(rv.at("c", 1e-9), 0.9966, delta=2e-3)

    def test_mfactor(self):
        rv, rx = self.both(mfactor_netlist(), "mf")
        for raw in (rv, rx):
            self.assertAlmostEqual(raw.at("i(vx)", 0.0), -6e-3, delta=1e-9)   # nested m=2 in m=3
            self.assertAlmostEqual(raw.at("i(vy)", 0.0), -6e-3, delta=1e-9)   # device m=2 in m=3
            self.assertAlmostEqual(raw.at("nz", 0.0), 3.0, delta=1e-6)        # G cur= in m=3
            self.assertAlmostEqual(raw.at("nw", 0.0), 3.0, delta=1e-6)        # I in m=3
            single = raw.at("i(vl)", 0.0)
            self.assertAlmostEqual(raw.at("i(vj)", 0.0), 3 * single, delta=abs(single) * 1e-6)   # J in m=3
            self.assertAlmostEqual(raw.at("i(vk)", 0.0), 3 * single, delta=abs(single) * 1e-6)   # J area=3
            # a transformer in an m=2 subckt: two parallel copies, each one's secondary as in the m=1
            # copy (VACASK halved the coupling, Xyce kept the full primary before the fold)
            peak = max(abs(raw.at("x7.b", t * 1e-10)) for t in range(1, 100))
            self.assertGreater(peak, 0.1)
            for t in (2.5e-9, 5e-9, 7.5e-9):
                self.assertAlmostEqual(raw.at("x6.b", t), raw.at("x7.b", t), delta=1e-4 * peak, msg=t)

    def test_dependent_parameter_overrides(self):
        rv, rx = self.both(dependent_netlist(), "dep")
        for raw in (rv, rx):
            for k, want in ((1, -0.25e-3), (2, -0.2e-3), (3, -0.5e-3), (4, -2e-3)):
                self.assertAlmostEqual(raw.at("i(v%d)" % k, 0.0), want, delta=1e-12, msg=k)

    def test_bsim3_version_and_level3_diode(self):
        nl = Netlist(title="bsim3 + diode")
        nl.body = [card("n3", "nmos", 49, version=3.1, tox=4e-9, vth0=0.4),
                   V("vd", "d", "0", 1.0), V("vg", "g", "0", 1.0),
                   I("m1", "m", ["d", "g", "0", "0"], master="n3", params={"w": P("1u"), "l": P("0.18u")})]
        nl.analyses = tran(1e-10, 1e-9)
        nl.probes = [("tran", "i", "vd")]
        notes = []
        text = vacask.render(nl, notes=notes)
        # HSPICE's LEVEL 49 defaults: XPART=1, and for VERSION 3.1 its own CAPMOD=0 (warning: simulated
        # with BSIM3's 2); the ACM=0 junction model is a warning too, its CJ/CJSW defaults are written
        self.assertIn("model m_n3 sp_bsim3v3 type=1.0 tox=4e-09 vth0=0.4 xpart=1.0 cj=0.000579 cjsw=0.0 capmod=2.0\n",
                      text)
        self.assertEqual([n.severity for n in notes], [WARNING, WARNING, NOTE, NOTE])
        iv = self.vacask(text, "b3").at("i(vd)", 0.0)
        ix = self.xyce(xyce.render(nl), "b3x").at("i(vd)", 0.0)
        self.assertLess(iv, -1e-5)
        self.assertAlmostEqual(iv, ix, delta=abs(ix) * 0.05)           # BSIM3v3.3 vs Xyce's v3.2.2
        d = Netlist(title="diode level 3")
        d.body = [card("dg", "d", 3, **{"is": 1e-14}), V("va", "a", "0", 0.7),
                  I("d1", "d", ["a", "0"], master="dg", params={"w": P("1u"), "l": P("1u")})]
        d.analyses = tran(1e-10, 1e-9)
        d.probes = [("tran", "i", "va")]
        text = vacask.render(d)
        self.assertIn("model m_dg sp_diode level=3.0 is=1e-14", text)
        self.assertLess(self.vacask(text, "d3").at("i(va)", 0.0), 0.0)
        with self.assertRaisesRegex(NoteError, "geometric diode"):
            xyce.render(d)
        nine = Netlist(title="level 9")
        nine.body = [card("m9", "nmos", 9, vto=0.5), V("vd", "d", "0", 1.0),
                     I("m1", "m", ["d", "d", "0", "0"], master="m9", params={"w": P("1u"), "l": P("1u")})]
        nine.analyses = tran(1e-10, 1e-9)
        for emitter in (vacask.render, xyce.render):
            with self.assertRaisesRegex(NoteError, "level 9 has no faithful"):
                emitter(nine)

    def test_binning(self):
        rv, rx = self.both(bin_netlist(), "bin")
        for raw in (rv, rx):
            for k in ("1", "2", "3"):
                d, r = raw.at("i(vd%s)" % k, 0.0), raw.at("i(vr%s)" % k, 0.0)
                self.assertAlmostEqual(d, r, delta=abs(r) * 1e-9, msg="device %s is not in its bin" % k)
                self.assertLess(d, -1e-6)
        for k in ("1", "2", "3"):
            self.assertAlmostEqual(rv.at("i(vd%s)" % k, 0.0), rx.at("i(vd%s)" % k, 0.0),
                                   delta=abs(rx.at("i(vd%s)" % k, 0.0)) * 0.02, msg=k)

    def test_pulse_holds_v2(self):
        nl = pulse_netlist()
        nl.options = {"method": Str("gear")}         # both engines take the mapped option
        rv, rx = self.both(nl, "pulse")
        for raw in (rv, rx):
            self.assertAlmostEqual(raw.at("a", 0.5e-9), 0.0, places=9)
            for t in (1.2e-9, 1e-8, 1.99e-8):
                self.assertAlmostEqual(raw.at("a", t), 1.0, places=9, msg=t)

    def test_basic_deck_agrees(self):
        """Every element of the basic deck: sources exactly, devices within a few per cent."""
        nl = basic_netlist()
        nodes = ("in", "s", "e", "p", "out", "out2", "bc", "bb", "fa", "ha", "ga", "ea", "rb", "sb", "lc", "ib")
        nl.probes = [("tran", "v", n) for n in nodes] + [("tran", "i", "vj"), ("tran", "i", "vsup")]
        rv, rx = self.both(nl, "src")
        for t in (0.0, 0.7e-9, 1.05e-9, 1.5e-9, 2.2e-9, 3.3e-9, 5.0e-9, 12e-9, 19e-9):
            for n in ("in", "s", "e", "p"):
                self.assertAlmostEqual(rv.at(n, t), rx.at(n, t), delta=2e-3, msg="%s at %g" % (n, t))
        for t in (0.0, 8e-9, 19e-9):
            for n in nodes[4:] + ("i(vj)", "i(vsup)"):
                a, b = rv.at(n, t), rx.at(n, t)
                self.assertAlmostEqual(a, b, delta=max(0.03 * abs(b), 0.02 if n[0] != "i" else 1e-7),
                                       msg="%s at %g: VACASK %g, Xyce %g" % (n, t, a, b))
        # the controlled sources against their laws (vprobe carries vdd/10k)
        for raw in (rv, rx):
            self.assertAlmostEqual(raw.at("fa", 19e-9), 2.0 * 1.8 / 10e3 * 1e3, delta=1e-6)
            self.assertAlmostEqual(raw.at("ha", 19e-9), 1e3 * 1.8 / 10e3, delta=1e-6)
            self.assertAlmostEqual(raw.at("ea", 19e-9), 2.0 * raw.at("out", 19e-9), delta=1e-6)
            self.assertAlmostEqual(raw.at("bb", 19e-9), 0.5 * raw.at("out", 19e-9), delta=1e-6)
            self.assertAlmostEqual(raw.at("bc", 19e-9), raw.at("in", 19e-9), delta=1e-6)
            self.assertAlmostEqual(raw.at("ib", 19e-9), 1e-3, delta=1e-9)

    def test_scale(self):
        def deck(scaled):
            nl = Netlist(title="scale")
            if scaled:
                nl.options = {"scale": Num(1e-6)}
            nl.body = [card("n1", "nmos", 1, vto=0.5, kp=1e-4, ld=0.2e-6), V("vd", "d", "0", 1.0), V("vg", "g", "0", 1.0),
                       I("m1", "m", ["d", "g", "0", "0"], master="n1",
                         params={"w": Num(2.0), "l": Num(1.0)} if scaled else {"w": P("2u"), "l": P("1u")})]
            nl.analyses = tran(1e-10, 1e-9)
            nl.probes = [("tran", "i", "vd")]
            return nl
        for (rv, rx) in (self.both(deck(True), "s1"), self.both(deck(False), "s0")):
            for raw in (rv, rx):
                self.assertAlmostEqual(raw.at("i(vd)", 0.0), -4.1667e-5, delta=1e-8)

    def test_gcond_polarity(self):
        osdi = _compile_ie(self.tmp)
        for en, want in ((1.0, 2.0 / 3.0), (0.0, 0.0)):
            rv, rx = self.both(gcond_netlist(en), "g%d" % en, osdi)
            self.assertAlmostEqual(rv.at("n", 0.0), want, places=4)
            self.assertAlmostEqual(rx.at("n", 0.0), want, places=4)


if __name__ == "__main__":
    unittest.main()
