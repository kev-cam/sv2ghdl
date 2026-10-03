"""vcs-ams end to end: the deck-group fixes (docs/VAMOS_AMS_DESIGN.md §1.4, §2.2, §3.3,
§4.3.2, §4.7, §5.4, §6).

  * TestE2EPortConnectInst*: `port_connect -cell c -inst <path>` wires that instance only
    (beside a cell-level statement in either order, in the in-group form, or alone): two
    inverters on 1.2 V and 3.3 V, checked in the deck, the rawfile, the IE levels and the
    digital outputs.  An instance no statement covers is an error naming the -inst one.
  * TestE2EPortConnectNets: a port_connect net that is not in the deck (a typo, a node
    inside a SPICE instance, a Verilog-only net, `real`) is an error naming the statement;
    <instance>.<port> still works.  A ground-alias port connected elsewhere is an error.
  * TestE2EFrontEnd: a second or misspelt -top; a cell bound to a left-out subckt (one
    error with the reason); a multi-view parameter override whose name only occurs inside
    a range parameter's name; the variable warning (one SPICE port: none; two: with the
    fix); --vamos-analog-stop's clamp warning once; --vamos-analog-maxstep's note.
  * TestE2EReference*: ie_reference_voltage on a regulator output that is not an ideal
    source sets the levels (it used to fall to the highest deck source, 5 V); the last
    entry on a net wins; an entry no trace reaches is a warning, not TNF; a supply vamos
    cannot evaluate (temper) is an error naming it.
  * TestE2ESaves*: a .print of a node the deck lacks is a compile error on both engines
    (Xyce used to fail at run time); a subckt-port alias is saved as its node (VACASK used
    to reject it); XA cfg probe_waveform_voltage patterns are resolved.
  * TestE2EEngineOverrides: VAMOS_OPENVAF / VAMOS_XYCE naming no executable is an error,
    never a traceback or a fallback.

Linux/WSL only (nvc, iverilog, VACASK and/or Xyce).
"""

import os
import re
import unittest
from typing import Dict, List, Optional

from ams_e2e_lib import AmsCase, engines_available, needs_ams

INV_MODELS = """\
.model nch nmos level=1 vto=0.4 kp=200u
.model pch pmos level=1 vto=-0.4 kp=100u
"""
INV = INV_MODELS + """\
.subckt inv a y vdd vss
mp y a vdd vdd pch w=2u l=0.2u
mn y a vss vss nch w=1u l=0.2u
cl y vss 5f
.ends
"""

CELLS_TWO_RAILS = "* two inverters, two rails\n" + INV + """\
vcore vdd_core 0 1.2
vio vdd_io 0 3.3
.tran 0.1n 100n
"""

TB_TWO = """\
`timescale 1ns/1ps
module tb;
  reg a1 = 0;
  reg a2 = 0;
  wire y1, y2;
  inv u1 (.a(a1), .y(y1));
  inv u2 (.a(a2), .y(y2));
  initial begin
    #20 a1 = 1; a2 = 1;
    #20 $display("%0t a=1 y1=%b y2=%b", $time, y1, y2);
    #20 a1 = 0; a2 = 0;
    #20 $display("%0t a=0 y1=%b y2=%b", $time, y1, y2);
    $finish;
  end
endmodule
"""

TB_ONE = """\
`timescale 1ns/1ps
module tb;
  reg a = 0;
  wire y;
  inv u1 (.a(a), .y(y));
  initial begin
    #20 $display("%0t a=0 y=%b", $time, y);
    a = 1;
    #20 $display("%0t a=1 y=%b", $time, y);
    $finish;
  end
endmodule
"""

INST_INITS = {
    "mixed": "port_connect -cell inv (vdd => vdd_io, vss => 0);\n"
             "port_connect -cell inv -inst tb.u1 (vdd => vdd_core);\n",
    "mixedrev": "port_connect -cell inv -inst tb.u1 (vdd => vdd_core);\n"
                "port_connect -cell inv (vdd => vdd_io, vss => 0);\n",
    "ingroup": "port_connect -cell inv (vdd => vdd_io, vss => 0);\n"
               "port_connect -cell inv (-inst tb.u1 vdd => vdd_core);\n",
    "instonly": "port_connect -cell inv -inst tb.u1 (vdd => vdd_core, vss => 0);\n"
                "port_connect -cell inv -inst tb.u2 (vdd => vdd_io, vss => 0);\n",
}


# -- helpers --------------------------------------------------------------------------------

def x_lines(d: str) -> Dict[str, List[str]]:
    """{xv_ name: nodes} from the emitted deck, either engine."""
    out = {}
    for name in ("vamos.sim", "vamos.cir"):
        p = os.path.join(d, "simv.daidir", "ams", "deck", name)
        if not os.path.isfile(p):
            continue
        with open(p) as fh:
            for ln in fh:
                if ln.lower().startswith("xv_"):
                    toks = ln.replace("(", " ").replace(")", " ").split()
                    out[toks[0].lower()] = [t.lower() for t in toks[1:-1]]
    return out


def ie_levels(text: str) -> Dict[str, Dict[str, str]]:
    """{canonical: {key: value}} of the d2a/a2d lines of the IE report."""
    out: Dict[str, Dict[str, str]] = {}
    for ln in text.splitlines():
        m = re.match(r"^(d2a|a2d)\s+(.*?);\s*$", ln.strip())
        if not m:
            continue
        keys = dict(tok.partition("=")[::2] for tok in m.group(2).split())
        out.setdefault(keys.pop("node", "").lower(), {}).update(keys)
    return out


def column_max(raw, node: str) -> float:
    names = [n for n in raw.names() if re.sub(r"^v\((.*)\)$", r"\1", n.lower()) == node.lower()]
    if len(names) != 1:
        raise AssertionError("%s: no single column %s: %s" % (raw.path, node, raw.names()))
    return max(raw.column(names[0]))


def plain(raw) -> List[str]:
    return [re.sub(r"^v\((.*)\)$", r"\1", n.lower()).replace(":", ".") for n in raw.names()]


class _Case(AmsCase):
    def first_engine(self) -> str:
        return self.engines()[0]

    def errors(self, out: str) -> List[str]:
        return [ln[len("vamos: error: "):] for ln in out.splitlines() if ln.startswith("vamos: error: ")]

    def build(self, name: str, files: Dict[str, str], engine: str, *args: str, rc: int = 0,
              env: Optional[Dict[str, str]] = None):
        d = self.case("%s_%s" % (name, engine), files)
        srcs = [f for f in files if f.endswith(".sv")]
        c = self.compile(d, "-sverilog", *(srcs + list(args)), engine=engine, expect_rc=rc, env=env)
        self.assertNotIn("Traceback", c.stdout)
        return d, c


# =============================================================================================
# Ideck-01: port_connect -inst
# =============================================================================================

class _PortConnectInst:
    """Engine-parametrized tests (a mixin: the concrete classes below add _Case)."""

    ENGINE = ""

    def setUp(self):
        super().setUp()
        if self.ENGINE not in engines_available():
            self.skipTest("analog engine %s is not available" % self.ENGINE)

    def files(self, init: str) -> Dict[str, str]:
        return {"tb.sv": TB_TWO, "cells.sp": CELLS_TWO_RAILS, "vcsAD.init": "choose xa cells.sp;\n" + init}

    def check_deck(self, d: str) -> None:
        x = x_lines(d)
        self.assertEqual(x["xv_u1"][2:], ["vdd_core", "0"], x)
        self.assertEqual(x["xv_u2"][2:], ["vdd_io", "0"], x)

    def run_and_check(self, variant: str) -> None:
        d, c = self.build("inst_" + variant, self.files(INST_INITS[variant]), self.ENGINE)
        self.check_deck(d)
        r = self.simv(d)
        self.assertIn("40000 a=1 y1=0 y2=0", r.stdout)
        self.assertIn("80000 a=0 y1=1 y2=1", r.stdout)
        raw = self.raw(d)
        self.assertAlmostEqual(column_max(raw, "n_u1_y"), 1.2, delta=0.02)    # was 3.3 (vdd_io)
        self.assertAlmostEqual(column_max(raw, "n_u2_y"), 3.3, delta=0.02)
        lv = ie_levels(self.report(d))
        self.assertEqual((lv["tb.u1.a"]["hiv"], lv["tb.u1.y"]["loth"], lv["tb.u1.y"]["hith"]),
                         ("1.2", "0.6", "0.6"))
        self.assertEqual((lv["tb.u2.a"]["hiv"], lv["tb.u2.y"]["loth"]), ("3.3", "1.65"))

    def test_mixed(self):
        self.run_and_check("mixed")

    def test_inst_statements_only(self):
        self.run_and_check("instonly")

    def test_other_forms_wire_the_same_deck(self):
        for variant in ("mixedrev", "ingroup"):
            with self.subTest(variant=variant):
                d, c = self.build("inst_" + variant, self.files(INST_INITS[variant]), self.ENGINE,
                                  "--vamos-no-deck-check")
                self.check_deck(d)

    def test_uncovered_instance(self):
        d, c = self.build("inst_partial", self.files(
            "port_connect -cell inv -inst tb.u1 (vdd => vdd_core, vss => 0);\n"), self.ENGINE, rc=1)
        self.assertIn("tb.u2: port vdd of subckt inv is port_connect'ed only by -inst statements that do "
                      "not match tb.u2 (port_connect -cell inv -inst tb.u1 (vdd => vdd_core) at "
                      "vcsAD.init:2); add a cell-level port_connect -cell inv or an -inst for tb.u2",
                      self.errors(c.stdout))


@needs_ams
class TestE2EPortConnectInstVacask(_PortConnectInst, _Case):
    ENGINE = "vacask"


@needs_ams
class TestE2EPortConnectInstXyce(_PortConnectInst, _Case):
    ENGINE = "xyce"


# =============================================================================================
# Ideck-02 / Ideck-06: port_connect nets
# =============================================================================================

CELLS_PWR = "* a power block whose rails are subckt ports, and one whose rails are internal\n" + INV + """\
.subckt pwrblk vout gout
v1 vout 0 1.2
v2 gout 0 0
.ends
.subckt pwrint en
v1 vdd 0 1.2
ren en 0 1meg
.ends
vsup vdd 0 1.8
.tran 0.1n 60n
"""

TB_PWR = """\
`timescale 1ns/1ps
module tb;
  real my_vdd = 1.2;
  reg a = 0;
  wire y, vo, go, en;
  wire vdd_w;
  assign vdd_w = 1'b1;
  pwrblk ipwr (.vout(vo), .gout(go));
  pwrint iint (.en(en));
  inv u1 (.a(a), .y(y));
  initial begin
    #20 $display("%0t a=0 y=%b", $time, y);
    a = 1;
    #20 $display("%0t a=1 y=%b", $time, y);
    $finish;
  end
endmodule
"""

CELLS_GND = "* an inverter whose ground pin is called gnd\n" + INV_MODELS + """\
.subckt inv a y vdd gnd
mp y a vdd vdd pch w=2u l=0.2u
mn y a gnd gnd nch w=1u l=0.2u
cl y gnd 5f
.ends
vcore vdd_core 0 1.2
vlow vlow 0 0.3
.tran 0.1n 50n
"""

RULE = ("no net vdx in the deck; a port_connect net is a .global or top-level net of the netlist, "
        "ground, or <SPICE instance>.<port> of a port a Verilog net connects (a Verilog-only net is not "
        "in v1); nearest: vdd")


@needs_ams
class TestE2EPortConnectNets(_Case):
    def pwr(self, conn: str, rc: int, engine: Optional[str] = None):
        files = {"tb.sv": TB_PWR, "cells.sp": CELLS_PWR,
                 "vcsAD.init": "choose xa cells.sp;\nport_connect -cell inv (%s);\n" % conn}
        name = re.sub(r"\W+", "_", conn)[:40]
        return self.build("net_" + name, files, engine or self.first_engine(), rc=rc)

    def test_nets_not_in_the_deck_are_errors(self):
        for conn, want in (
                ("vdd => vdx, vss => 0", "vcsAD.init:2: port_connect -cell inv (vdd => vdx): " + RULE),
                ("vdd => tb.iint.vdd, vss => 0",
                 "vcsAD.init:2: port_connect -cell inv (vdd => tb.iint.vdd): tb.iint.vdd is node vdd inside "
                 "SPICE instance tb.iint; in v1 a port_connect net is a .global or top-level net of the "
                 "netlist, ground, or <SPICE instance>.<port> of a port a Verilog net connects: make vdd a "
                 "port of its subckt and connect it from Verilog, or declare it .global"),
                ("real vdd => tb.my_vdd, vss => 0",
                 "vcsAD.init:2: port_connect -cell inv (real vdd => tb.my_vdd): real-number interface "
                 "elements are not supported in v1"),
                ("vdd => tb.vdd_w, vss => 0",
                 "vcsAD.init:2: port_connect -cell inv (vdd => tb.vdd_w): no net tb.vdd_w in the deck; a "
                 "port_connect net is a .global or top-level net of the netlist, ground, or <SPICE "
                 "instance>.<port> of a port a Verilog net connects (a Verilog-only net is not in v1); "
                 "nearest: vdd")):
            with self.subTest(conn=conn):
                d, c = self.pwr(conn, 1)
                self.assertEqual(self.errors(c.stdout), [want, "AMS compile failed at the analog deck"])

    def test_instance_port_still_works(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d, c = self.pwr("vdd => tb.ipwr.vout, vss => tb.ipwr.gout", 0, engine)
                self.assertEqual(x_lines(d)["xv_u1"][2:], ["n_ipwr_vout", "n_ipwr_gout"])
                r = self.simv(d)
                self.assertIn("20000 a=0 y=1", r.stdout)
                self.assertIn("40000 a=1 y=0", r.stdout)

    def test_ground_alias_port(self):
        files = {"tb.sv": TB_ONE, "cells.sp": CELLS_GND}
        files["vcsAD.init"] = "choose xa cells.sp;\nport_connect -cell inv (vdd => vdd_core, gnd => vlow);\n"
        d, c = self.build("gnd_bad", files, self.first_engine(), rc=1)
        self.assertIn("vcsAD.init:2: port_connect -cell inv (gnd => vlow): port gnd of subckt inv is a ground "
                      "alias, which is ground inside the subckt, so it cannot be connected to vlow; connect it "
                      "to ground or rename the port", self.errors(c.stdout))
        files["vcsAD.init"] = "choose xa cells.sp;\nport_connect -cell inv (vdd => vdd_core, gnd => gnd);\n"
        d, c = self.build("gnd_ok", files, self.first_engine(), "--vamos-no-deck-check")
        self.assertNotIn("ground alias", c.stdout)


# =============================================================================================
# Ideck-03, 04, 05, 11, 12: front-end checks (engine-independent: the first engine)
# =============================================================================================

TB_DAC = """\
`timescale 1ns/1ps
module dac #(parameter NG = 2, parameter G = 1) (input [NG-1:0] d, output y);
  assign y = |d;
endmodule
module tb;
  reg [1:0] d = 0;
  wire y;
  dac #(%s) u1 (.d(d), .y(y));
  initial begin #20 d = 2'b11; #20 $display("%%0t y=%%b", $time, y); $finish; end
endmodule
"""

CELLS_DAC = """\
* multi-view dac
.subckt dac d[1] d[0] y
r1 d[1] y 1k
r2 d[0] y 1k
c1 y 0 10f
.ends
vsup vdd 0 1.8
.tran 0.1n 50n
"""

CELLS_LEFT = "* inverter left out: an S element is not supported\n" + INV_MODELS + """\
.model sw1 sw vt=0.5
.subckt inv a y vdd vss
mp y a vdd vdd pch w=2u l=0.2u
mn y a vss vss nch w=1u l=0.2u
s1 y vss a vss sw1
.ends
vcore vdd_core 0 1.2
.tran 0.1n 50n
"""

TB_VAR = """\
`timescale 1ns/1ps
module tb;
  reg a = 0;
  reg b = 0;
  logic seen;
  logic link;
  wire y2;
  inv u1 (.a(b), .y(seen));
  inv u2 (.a(a), .y(link));
  inv u3 (.a(link), .y(y2));
  initial begin #20 a = 1; #20 $display("%0t link=%b y2=%b", $time, link, y2); $finish; end
endmodule
"""

CELLS_CORE = "* inverters on a 1.2 V rail\n" + INV + "vcore vdd_core 0 1.2\n.tran 0.1n 50n\n"
INIT_CORE = "choose xa cells.sp;\nport_connect -cell inv (vdd => vdd_core, vss => 0);\n"


@needs_ams
class TestE2EFrontEnd(_Case):
    def test_top_checks(self):
        files = {"tb.sv": TB_ONE + "module tb2;\n  initial $display(\"tb2 runs\");\nendmodule\n",
                 "cells.sp": CELLS_CORE, "vcsAD.init": INIT_CORE}
        d, c = self.build("top_two", files, self.first_engine(), "-top", "tb", "-top", "tb2", rc=1)
        self.assertEqual(self.errors(c.stdout)[0], "-top: an AMS design has one top module; -top was given "
                                                   "2 times (tb, tb2)")
        d, c = self.build("top_typo", files, self.first_engine(), "-top", "tbx", rc=1)
        self.assertEqual(self.errors(c.stdout)[0], "-top: -top tbx: no module tbx in the Verilog sources")

    def test_left_out_subckt(self):
        files = {"tb.sv": TB_ONE, "cells.sp": CELLS_LEFT, "vcsAD.init": INIT_CORE}
        d, c = self.build("left", files, self.first_engine(), rc=1)
        errs = self.errors(c.stdout)
        self.assertEqual(len(errs), 2, c.stdout)
        self.assertRegex(errs[0], r"^tb\.sv:5: cell inv: subckt inv cannot be simulated: s1: an S-parameter "
                                  r"element \(HSPICE S\) is not supported \(.*cells\.sp:8\)$")

    def test_parameter_named_inside_a_range_parameter(self):
        init = "choose xa cells.sp;\nuse_spice -cell dac;\n"
        d, c = self.build("ovr_g", {"tb.sv": TB_DAC % ".G(3)", "cells.sp": CELLS_DAC, "vcsAD.init": init},
                          self.first_engine(), rc=1)
        self.assertIn("tb.u1: parameter override G=3 on SPICE instance tb.u1 is not passed to subckt dac",
                      self.errors(c.stdout))
        d, c = self.build("ovr_ng", {"tb.sv": TB_DAC % ".NG(2)", "cells.sp": CELLS_DAC, "vcsAD.init": init},
                          self.first_engine(), "--vamos-no-deck-check")
        self.assertNotIn("parameter override", c.stdout)

    def test_variable_warning_and_option_notes(self):
        files = {"tb.sv": TB_VAR, "cells.sp": CELLS_CORE, "vcsAD.init": INIT_CORE}
        d, c = self.build("var", files, self.first_engine(), "--vamos-analog-maxstep=1n")
        warns = [ln for ln in c.stdout.splitlines() if "joins SPICE ports" in ln]
        self.assertEqual(len(warns), 1, warns)                      # tb.seen (one port) is not warned
        self.assertIn("variable tb.link joins SPICE ports in analog; VCS digitises it: tb.u2.y, tb.u3.a "
                      "share one analog node here", warns[0])
        self.assertIn(".tran: maximum time step 1e-09 s, from --vamos-analog-maxstep (in place of", c.stdout)
        self.assertNotIn("HSPICE's bound without .option delmax", c.stdout)
        notran = {"tb.sv": TB_ONE, "cells.sp": CELLS_CORE.replace(".tran 0.1n 50n\n", ""),
                  "vcsAD.init": INIT_CORE}
        d, c = self.build("stop", notran, self.first_engine(), "--vamos-analog-stop=10000",
                          "--vamos-no-deck-check")
        self.assertEqual(c.stdout.count("10000 s clamped to 9000 s"), 1, c.stdout)


# =============================================================================================
# Ideck-07 / Ideck-08: ie_reference_voltage and unevaluable supplies
# =============================================================================================

CELLS_REG = "* a regulator output from a VCVS: not an ideal source\n" + INV + """\
v5 v5 0 5
e1 vreg 0 v5 0 0.3
vio vdd_io 0 3.3
.tran 0.1n 50n
"""
INIT_REG = "choose xa cells.sp;\nport_connect -cell inv (vdd => vreg, vss => 0);\n"


class _Reference:
    """Engine-parametrized tests (a mixin: the concrete classes below add _Case)."""

    ENGINE = ""

    def setUp(self):
        super().setUp()
        if self.ENGINE not in engines_available():
            self.skipTest("analog engine %s is not available" % self.ENGINE)

    def test_reference_on_a_regulator_output(self):
        files = {"tb.sv": TB_ONE, "cells.sp": CELLS_REG,
                 "vcsAD.init": INIT_REG + "ie_reference_voltage node=vreg voltage=1.5;\n"}
        d, c = self.build("ref_reg", files, self.ENGINE)
        lv = ie_levels(self.report(d))
        self.assertEqual((lv["tb.u1.a"]["hiv"], lv["tb.u1.y"]["loth"], lv["tb.u1.y"]["hith"]),
                         ("1.5", "0.75", "0.75"))                     # was 5.0 / 2.5 / 2.5
        r = self.simv(d)
        self.assertIn("20000 a=0 y=1", r.stdout)                     # 1.5 V output over a 0.75 V threshold
        self.assertIn("40000 a=1 y=0", r.stdout)
        self.assertAlmostEqual(column_max(self.raw(d), "n_u1_y"), 1.5, delta=0.02)


@needs_ams
class TestE2EReferenceVacask(_Reference, _Case):
    ENGINE = "vacask"


@needs_ams
class TestE2EReferenceXyce(_Reference, _Case):
    ENGINE = "xyce"


@needs_ams
class TestE2EReferenceRules(_Case):
    def test_last_wins_and_unreached_entries(self):
        cells = CELLS_TWO_RAILS
        init = ("choose xa cells.sp;\nport_connect -cell inv (vdd => vdd_core, vss => 0);\n"
                "ie_reference_voltage node=vdd_core voltage=1.0;\n"
                "ie_reference_voltage node=tb.vdd_core voltage=0.9;\n"
                "ie_reference_voltage node=vdd_io voltage=2.5;\n")
        d, c = self.build("ref_rules", {"tb.sv": TB_ONE, "cells.sp": cells, "vcsAD.init": init},
                          self.first_engine(), "--vamos-no-deck-check")
        self.assertNotIn("MSV-IE-OPT-TNF", c.stdout)
        self.assertIn("vamos: note: vcsAD.init:3: ie_reference_voltage node=vdd_core is replaced by "
                      "node=tb.vdd_core at vcsAD.init:4", c.stdout)
        self.assertIn("vamos: warning: vcsAD.init:5: ie_reference_voltage node=vdd_io: no interface "
                      "element's supply trace reaches vdd_io", c.stdout)
        lv = ie_levels(self.report(d))
        self.assertEqual(lv["tb.u1.a"]["hiv"], "0.9")

    def test_unevaluable_supply(self):
        cells = "* a temper-dependent supply\n" + INV + "vt vdd_t 0 '1.2*temper/25'\n.tran 0.1n 50n\n"
        init = "choose xa cells.sp;\nport_connect -cell inv (vdd => vdd_t, vss => 0);\n"
        d, c = self.build("temper", {"tb.sv": TB_ONE, "cells.sp": cells, "vcsAD.init": init},
                          self.first_engine(), rc=1)
        errs = self.errors(c.stdout)
        self.assertRegex(errs[0], r"cells\.sp:\d+: V source vt cannot be evaluated \(.*temper.*\): the supply "
                                  r"trace of tb\.u1\.a, tb\.u1\.y reaches it")
        self.assertNotIn("3.3 V fallback", c.stdout)
        init += "d2a hiv=1.2 lov=0 node=tb.u1.a;\na2d loth=0.6 hith=0.6 node=tb.u1.y;\n"
        d, c = self.build("temper_rules", {"tb.sv": TB_ONE, "cells.sp": cells, "vcsAD.init": init},
                          self.first_engine(), "--vamos-no-deck-check")


# =============================================================================================
# Ideck-09: saves
# =============================================================================================

CELLS_SAVE = "* a divider and an ammeter instantiated in the netlist\n" + INV + """\
.subckt div p q
r1 p m 1k
r2 m q 1k
.ends
.subckt meter p q
vm p q 0
.ends
vcore vdd_core 0 1.2
xd vdd_core dout div
xm dout dmeas meter
rl dmeas 0 1k
.tran 0.1n 50n
"""


class _Saves:
    """Engine-parametrized tests (a mixin: the concrete classes below add _Case)."""

    ENGINE = ""

    def setUp(self):
        super().setUp()
        if self.ENGINE not in engines_available():
            self.skipTest("analog engine %s is not available" % self.ENGINE)

    def files(self, prints: str, init_extra: str = "", xa: Optional[str] = None) -> Dict[str, str]:
        f = {"tb.sv": TB_ONE, "cells.sp": CELLS_SAVE + prints,
             "vcsAD.init": ("choose xa cells.sp%s;\n" % (" -c xa.cfg" if xa else "")) +
             "port_connect -cell inv (vdd => vdd_core, vss => 0);\n" + init_extra}
        if xa:
            f["xa.cfg"] = xa
        return f

    def test_unknown_node_is_a_compile_error(self):
        d, c = self.build("save_bad", self.files(".print tran v(nosuch)\n"), self.ENGINE, rc=1)
        self.assertIn(".print/.probe: v(nosuch): the deck has no node nosuch (a top-level or .global net, "
                      "or <X instance>.<node>)", self.errors(c.stdout))

    def test_port_alias_and_internal_node(self):
        d, c = self.build("save_alias", self.files(".print tran v(xd.q) v(xd.m)\n"), self.ENGINE)
        r = self.simv(d)
        self.assertIn("40000 a=1 y=0", r.stdout)
        names = plain(self.raw(d))
        self.assertIn("dout", names)                      # v(xd.q) is saved as the node it names
        self.assertIn("xd.m", names)

    def test_xa_patterns(self):
        xa = ("probe_waveform_voltage tb.u1.*\nprobe_waveform_voltage tb.nosuch*\n"
              "probe_waveform_current xm.*\n")
        d, c = self.build("save_xa", self.files("", xa=xa), self.ENGINE)
        self.assertIn("vamos: warning: xa.cfg:2: probe_waveform_voltage tb.nosuch* matches no node of the "
                      "deck", c.stdout)
        self.assertNotIn("probe_waveform patterns are not resolved", c.stdout)
        self.simv(d)
        names = plain(self.raw(d))
        self.assertTrue({"n_u1_a", "n_u1_y", "vdd_core"} <= set(names), names)
        self.assertNotIn("xd.m", names)                   # every node used to be saved
        self.assertTrue([n for n in names if "vm" in n], names)    # i(xm.vm)


@needs_ams
class TestE2ESavesVacask(_Saves, _Case):
    ENGINE = "vacask"


@needs_ams
class TestE2ESavesXyce(_Saves, _Case):
    ENGINE = "xyce"


# =============================================================================================
# Ideck-10: engine overrides
# =============================================================================================

@needs_ams
class TestE2EEngineOverrides(_Case):
    def test_bad_overrides(self):
        files = {"tb.sv": TB_ONE, "cells.sp": CELLS_CORE, "vcsAD.init": INIT_CORE}
        for engine, var in (("vacask", "VAMOS_OPENVAF"), ("xyce", "VAMOS_XYCE")):
            if engine not in self.engines():
                continue
            with self.subTest(engine=engine):
                bad = os.path.join(self.tmp, "nonexistent", "tool")
                d, c = self.build("ovr_" + var, files, engine, rc=1, env={var: bad})
                self.assertEqual(self.errors(c.stdout), ["%s=%s is not an executable" % (var, bad),
                                                         "AMS compile failed at the analog engine"])


if __name__ == "__main__":
    unittest.main()
