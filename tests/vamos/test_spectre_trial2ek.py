"""The fixes of phase 2e's round 8 (FIX2E8: the follow-ups round 7' left, on PyMS 1.1.7; the sweeps' nominals on the grid
since PyMS 1.1.8).  Standard library only,
Python 3.9; the engine runs on WSL.

TestVaParamKinds          vamos's Verilog-A scan records which parameters a module declares (* type="instance" *)
                          (before `parameter' or before the `;', the last type= winning, an aliasparam of its
                          target's kind), as PyMS reads them
TestVaParamsOnXyce        Verilog-A card (model) and instance parameters run on Xyce (they were refused: "PyMS ignores
                          parameter overrides", true before PyMS 1.1.0): the model ones on a card of the instance, the
                          instance ones on the Y line, and give VACASK's values (several instances, a card shared by
                          two, integer and string values, two modules, a subckt parameter, a sweep of a given and of an
                          unset parameter, an alter); a module whose declarations vamos could not read keeps a refusal
TestVaDefineOnXyce        -va,define reaches PyMS as PYMS_DEFINES (it was refused: "PyMS takes no macro definitions"),
                          NAME and NAME=VALUE; runs with other macros in one directory and one pair of PyMS caches
                          never reuse a stale build"""

import os
import unittest

from vamos_testlib import TempDir, needs_vacask, needs_xyce  # noqa: E402

import test_spectre_trial2eh as eh  # noqa: E402  (its deck runner and value reader)

HEAD = '`include "disciplines.vams"\n`include "constants.vams"\n'
# out = 1k / (1k + Req), Req = 1 / (k/r + g + (sel == "two" ? 1m : 0)); the defaults give 0.5
A2 = HEAD + """module a2(p, n);
  inout p, n;
  electrical p, n;
  parameter real r = 1k from (0:inf);
  (* type="instance" *) parameter real g = 0;
  parameter integer k = 1;
  parameter string sel = "one";
  analog I(p, n) <+ V(p, n) * (k / r + g + ((sel == "two") ? 1m : 0));
endmodule
"""
B2 = HEAD + """module b2(p, n);
  inout p, n;
  electrical p, n;
  parameter real rb = 1k;
  analog I(p, n) <+ V(p, n) / rb;
endmodule
"""
FILES = {"a2.va": A2, "b2.va": B2}


def deck(body, tail="op1 dc"):
    return ("// FIX2E8: Verilog-A parameters\nsimulator lang=spectre\nahdl_include \"a2.va\"\nahdl_include \"b2.va\"\n"
            "V1 (in 0) vsource dc=1\n%s\n%s\n" % (body, tail))


# -- 1: the scan ---------------------------------------------------------------------------------------------------------

class TestVaParamKinds(unittest.TestCase):

    def test_instance_parameters_are_recorded(self):
        from vamos.netlist import spice
        body = """(p, n);
  inout p, n; electrical p, n;
  parameter real r = 1k;
  parameter real g = 0 (* type="instance" *);
  (* desc="width", type="instance" *) parameter real w = 1u, l = 2u;
  (* type="instance" *) parameter real q = 1 (* type="model" *);
  (* type="model" *) parameter real t = 1;
  parameter integer k = 1;
  aliasparam W2 = w;
  aliasparam R2 = r;
  analog I(p, n) <+ V(p, n) / r;
"""
        names, bad, inst, aliases = spice._va_params(body, True)
        self.assertEqual(names, ["r", "g", "w", "l", "q", "t", "k", "W2", "R2"])
        self.assertIsNone(bad)
        self.assertEqual(inst, {"g", "w", "l", "w2"})
        self.assertEqual(aliases, {"w2": "w", "r2": "r"})

    def test_the_module_carries_them(self):
        import tempfile
        from vamos.netlist import spice
        d = tempfile.mkdtemp(prefix="vamos-2ek-")
        p = os.path.join(d, "a2.va")
        with open(p, "w") as fh:
            fh.write(A2)
        mods, _files = spice.spectre_va_modules(p)
        self.assertEqual(mods["a2"].params, ["r", "g", "k", "sel"])
        self.assertEqual(mods["a2"].instance_params, ["g"])
        with open(p, "w") as fh:
            fh.write(A2.replace("parameter integer k = 1;", "`IPARAM(k, 1)"))
        mods, _files = spice.spectre_va_modules(p)
        self.assertIsNone(mods["a2"].params)                    # an undefined macro at a declaration: unknown
        self.assertIsNone(mods["a2"].instance_params)


# -- 2: card and instance parameters on Xyce -----------------------------------------------------------------------------

# the measurements (FIX2E8 logs/m_cases_p_w.txt) as one deck: name -> (instance lines, node, value)
PARAMS = deck("""model c1 a2 r=2k g=1m
X1 (in o1) a2 r=2k
R1 (o1 0) resistor r=1k
X2 (in o2) c1
R2 (o2 0) resistor r=1k
X3 (in o3) c1 r=4k k=2 sel="two"
R3 (o3 0) resistor r=1k
X4 (in o4) b2 rb=4k
R4 (o4 0) resistor r=1k
X5 (in o5) a2
R5 (o5 0) resistor r=1k
X6 (in o6) s1 rv=4k
R6 (o6 0) resistor r=1k
subckt s1 (a b)
parameters rv=1k
Y1 (a b) a2 r=rv g=0
ends s1""")
# a model parameter on the instance (1/3); a card with a model and an instance parameter (Req 1/(0.5m+1m): 0.6); the
# card's r overridden, an integer and a string on the instance (Req 1/(0.5m+1m+1m) = 400: 1/1.4); another module (0.2);
# no parameter (0.5); a subckt parameter (0.2)
PARAMS_OUT = {"o1": 1 / 3.0, "o2": 0.6, "o3": 1 / 1.4, "o4": 0.2, "o5": 0.5, "o6": 0.2}

# X1's r swept from its own value, g that X1 does not give, card c1's r (X2 follows it, X3 gives its own), netlist
# parameter rr that X4's r reads, swept from its own value, then alters of X1's r and of c1's r (X2 follows the card;
# the card alone changed nothing on Xyce before: 0.5).  Each hook's nominal is the sweep's first point: PyMS 1.1.7 gave
# the declaration default there (BUGS.md A24, which round 8 dodged with an off-grid nominal), PyMS 1.1.8 its value
SWEEPS = deck("parameters rr=2k\nmodel c1 a2 r=1k\nX1 (in out) a2 r=2k\nR1 (out 0) resistor r=1k\nX2 (in o2) c1\n"
              "R2 (o2 0) resistor r=1k\nX3 (in o3) c1 r=4k\nR3 (o3 0) resistor r=1k\n"
              "X4 (in o4) a2 r=rr\nR4 (o4 0) resistor r=1k",
              "sw1 dc dev=X1 param=r values=[2k 4k]\nsw2 dc dev=X1 param=g values=[0 1m 3m]\n"
              "sw3 dc mod=c1 param=r values=[1k 2k 4k]\nsw4 dc param=rr values=[2k 4k]\n"
              "a1 alter dev=X1 param=r value=4k\na2 alter mod=c1 param=r value=2k\nop2 dc")
SWEEPS_OUT = {"sw1.dc": [("out", 2e3, 1 / 3.0), ("out", 4e3, 0.2)],
              "sw2.dc": [("out", 0.0, 1 / 3.0), ("out", 1e-3, 0.6), ("out", 3e-3, 1 / 1.285714285714286)],
              "sw3.dc": [("o2", 1e3, 0.5), ("o2", 2e3, 1 / 3.0), ("o2", 4e3, 0.2), ("o3", 1e3, 0.2), ("o3", 4e3, 0.2)],
              "sw4.dc": [("o4", 2e3, 1 / 3.0), ("o4", 4e3, 0.2)],
              "op2.dc": [("out", None, 0.2), ("o2", None, 1 / 3.0), ("o3", None, 0.2)]}


class TestVaParamsOnXyce(TempDir):

    def test_the_deck_splits_them_by_kind(self):
        from vamos.netlist import plan as PL, signals as SG, spectre, xyce
        from vamos.spectre.job import Settings
        for fn, body in FILES.items():
            self.write(fn, body)
        path = self.write("p.scs", PARAMS)
        nl = spectre.parse(path, self.tmp, spectre.SpectreParseOpts())
        pl = PL.build(nl, "xyce", Settings())
        nl2, sm, _notes = SG.resolve(nl, pl)
        step = next(a.step for a in pl.actions if a.op == "analysis" and a.step is not None)
        text = xyce.render(nl2, step=step, sigmap=sm, names={})
        self.assertIn(".model X1__va a2 r=2000.0\nya2 X1 in o1 X1__va\n", text)
        self.assertIn(".model X2__va a2 r=2000.0\nya2 X2 in o2 X2__va g=0.001\n", text)
        self.assertIn('.model X3__va a2 r=4000.0 k=2.0 sel="two"\nya2 X3 in o3 X3__va g=0.001\n', text)
        self.assertIn(".model X4__va b2 rb=4000.0\nyb2 X4 in o4 X4__va\n", text)
        self.assertIn(".model a2__vamos a2\nya2 X5 in o5 a2__vamos\n", text)
        self.assertIn(".model Y1__va a2 r={rv}\nya2 Y1 a b Y1__va g=0.0\n", text)
        self.assertNotIn("PyMS ignores", text)

    def check(self, engine):
        d, r = eh.spectre(self, engine, PARAMS, engine, files=FILES)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        res = os.path.join(d, "n.raw", "op1.dc")
        for node, want in PARAMS_OUT.items():
            self.assertAlmostEqual(eh.value(res, node), want, places=7, msg="%s %s" % (engine, node))

    @needs_xyce
    def test_xyce_gives_the_values(self):
        self.check("xyce")

    @needs_vacask
    def test_vacask_gives_the_same(self):
        self.check("vacask")

    def check_sweeps(self, engine):
        d, r = eh.spectre(self, "s" + engine, SWEEPS, engine, files=FILES)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertNotIn("build_vae_so", r.stdout)              # PyMS's library per point: progress, not a warning
        raw = os.path.join(d, "n.raw")
        for res, rows in SWEEPS_OUT.items():
            for node, x, want in rows:
                self.assertAlmostEqual(eh.value(os.path.join(raw, res), node, x), want, places=7,
                                       msg="%s %s %s at %s" % (engine, res, node, x))

    def test_the_sweep_decks(self):
        """The hooks: X1's r and g on its card and line, c1's r on X2's card only, each nominal the sweep's first
        point, as is rr's; the alter deck's X2 card follows c1."""
        from vamos.netlist import plan as PL, signals as SG, spectre
        from vamos.spectre import run_xyce as RX
        from vamos.spectre.job import Settings
        for fn, body in FILES.items():
            self.write(fn, body)
        path = self.write("s.scs", SWEEPS)
        nl = spectre.parse(path, self.tmp, spectre.SpectreParseOpts())
        pl = PL.build(nl, "xyce", Settings())
        nl2, sm, _notes = SG.resolve(nl, pl)
        decks, _names = RX.render_decks(nl2, pl, sm)
        text = {d.step.name: d.text for d in decks}
        self.assertIn(".model X1__va a2 r={vamos_x_X1_r}\n", text["sw1"])
        self.assertIn(".param vamos_x_X1_r=2000.0\n.DC vamos_x_X1_r LIST 2000.0 4000.0\n", text["sw1"])
        self.assertIn("ya2 X1 in out X1__va g={vamos_x_X1_g}\n", text["sw2"])
        self.assertIn(".model X2__va a2 r={vamos_x_c1_r}\n", text["sw3"])
        self.assertIn(".model X3__va a2 r=4000.0\n", text["sw3"])
        self.assertIn(".param vamos_x_c1_r=1000.0\n.DC vamos_x_c1_r LIST 1000.0 2000.0 4000.0\n", text["sw3"])
        self.assertIn(".param rr=2000.0\n", text["sw4"])
        self.assertIn(".DC rr LIST 2000.0 4000.0\n", text["sw4"])
        self.assertIn(".model X1__va a2 r=4000.0\n", text["op2"])
        self.assertIn(".model X2__va a2 r=2000.0\n", text["op2"])
        self.assertIn(".model X3__va a2 r=4000.0\n", text["op2"])

    @needs_xyce
    def test_xyce_sweeps_and_alters_them(self):
        self.check_sweeps("xyce")

    @needs_vacask
    def test_vacask_sweeps_and_alters_them_the_same(self):
        self.check_sweeps("vacask")


# -- 3: -va,define on Xyce -----------------------------------------------------------------------------------------------

IFD = HEAD + "`ifdef BIGR\n`define RV 4k\n`else\n`define RV 1k\n`endif\n" + """module a1(p, n);
  inout p, n;
  electrical p, n;
  parameter real r = `RV;
  analog I(p, n) <+ V(p, n) / r;
endmodule
"""
A1_DECK = """// a1: out = 1000 / (r + 1000)
simulator lang=spectre
ahdl_include "a1.va"
V1 (in 0) vsource dc=1
X1 (in out) a1
R2 (out 0) resistor r=1k
op1 dc
"""


class TestVaDefineOnXyce(TempDir):

    @needs_xyce
    def test_defines_reach_pyms_and_never_share_a_build(self):
        """One directory, one pair of PyMS caches (warm after the first run): no define 0.5, BIGR 0.2 (the `ifdef
        branch), none again 0.5, then RV=2k 1/3 and RV=4k 0.2 (a value, which VACASK refuses: openvaf-r)."""
        env = {"PYMS_CACHE": os.path.join(self.tmp, "hdl"), "PYMS_HDL_CACHE": os.path.join(self.tmp, "hdl"),
               "PYMS_VAE_CACHE": os.path.join(self.tmp, "vae")}
        runs = (([], 0.5), (["BIGR"], 0.2), ([], 0.5), (["RV=2k"], 1 / 3.0), (["RV=4k"], 0.2))
        for defines, want in runs:
            args = [a for m in defines for a in ("-va,define", m)]
            src = IFD if not defines or "=" not in defines[0] else IFD.replace("`define RV 1k\n", "")
            d, r = eh.spectre(self, "d", A1_DECK, "xyce", args=args, env=env, files={"a1.va": src})
            self.assertEqual(r.returncode, 0, r.stdout[-3000:])
            self.assertNotIn("-va,define", r.stdout)
            self.assertAlmostEqual(eh.value(os.path.join(d, "n.raw", "op1.dc"), "out"), want, places=7,
                                   msg=str(defines))

    @needs_vacask
    def test_vacask_takes_a_name_alone(self):
        d, r = eh.spectre(self, "v", A1_DECK, "vacask", args=["-va,define", "BIGR"], files={"a1.va": IFD})
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertAlmostEqual(eh.value(os.path.join(d, "n.raw", "op1.dc"), "out"), 0.2, places=7)


if __name__ == "__main__":
    unittest.main()
