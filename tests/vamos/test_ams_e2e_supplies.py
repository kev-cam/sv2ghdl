"""vcs-ams end to end: supplies, x-heep, gate-only nodes and .option scale
(docs/VAMOS_AMS_DESIGN.md §9 e2e 7, 20, 24, 25 and 27).

Every test compiles with vcs-ams (x-heep: vcs -ad=...) and runs ./simv on each available
engine (VACASK and Xyce), then checks the rawfile, the IE report, the plan (ams.json) and
the values the digital side prints.  The testbenches print with
$display("%f <tag>=%b", $realtime, x): times in ns with their fractional part.  Only the
value an output has settled to at t=0 is checked, not the delta-cycle sequence that leads
there.

Linux/WSL only (nvc, iverilog, VACASK and/or Xyce).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import unittest
from typing import Dict, List, Optional, Tuple

from ams_e2e_lib import AmsCase, needs_ams
from vamos_testlib import FIXTURES

XHEEP = os.path.join(FIXTURES, "ams", "e2e_supplies", "xheep")
XHEEP_RUN_DIR = ("build", "openhwgroup.org_systems_core-v-mini-mcu_0", "sim-vcs")

INV_MODELS = """\
.model nch nmos level=1 vto=0.4 kp=200u
.model pch pmos level=1 vto=-0.4 kp=100u
"""


# -- output helpers -------------------------------------------------------------------------

def fevents(out: str, tag: str) -> List[Tuple[float, str]]:
    """(time in ns, value) from $display("%f <tag>=%b", $realtime, x) lines, in order."""
    rx = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s+%s=([01xzXZ]+)\s*$" % re.escape(tag))
    ev = []
    for ln in out.splitlines():
        m = rx.match(ln)
        if m:
            ev.append((float(m.group(1)), m.group(2).lower()))
    return ev


def settled(ev: List[Tuple[float, str]], t_ns: float) -> Optional[str]:
    """The value after every event at or before t_ns."""
    val = None
    for t, v in ev:
        if t <= t_ns:
            val = v
    return val


def after_zero(ev: List[Tuple[float, str]]) -> List[Tuple[float, str]]:
    return [(t, v) for t, v in ev if t > 0.0]


def ie_report(text: str) -> Dict[str, dict]:
    """The IE report as {canonical (lower case): {'d2a': {key: value}, 'a2d': {...},
    'comments': [comment lines]}}; a flag such as powernet maps to True."""
    out: Dict[str, dict] = {}
    cur: Optional[dict] = None
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            cur = None
            continue
        m = re.match(r"^(d2a|a2d)\s+(.*?);\s*$", s)
        if m:
            keys: Dict[str, object] = {}
            for tok in m.group(2).split():
                k, eq, v = tok.partition("=")
                keys[k.lower()] = v if eq else True
            node = str(keys.pop("node", "")).lower()
            cur = out.setdefault(node, {"comments": []})
            cur[m.group(1)] = keys
            continue
        m = re.match(r"^//\s*node=(\S+?):\s*(.*)$", s)
        if m:
            cur = out.setdefault(m.group(1).lower(), {"comments": []})
            cur["comments"].append(m.group(2))
            continue
        if s.startswith("//") and cur is not None:
            cur["comments"].append(s[2:].strip())
    return out


def _plain(name: str) -> str:
    """A rawfile variable name without v(...), lower case (VACASK x:n, Xyce V(X:N))."""
    n = name.strip().lower()
    m = re.match(r"^v\((.*)\)$", n)
    return m.group(1) if m else n


def column_ending(raw, suffix: str) -> str:
    """The one rawfile variable whose name ends in suffix (e.g. ':vmux_out')."""
    hits = [n for n in raw.names() if _plain(n).endswith(suffix.lower())]
    if len(hits) != 1:
        raise AssertionError("%s: %d variables end in %r: %s" % (raw.path, len(hits), suffix, raw.names()))
    return hits[0]


# -- designs --------------------------------------------------------------------------------

# e2e 20: a parameterised 1.2 V core supply (.param vsup=1.2, v_vdd vdd 0 'vsup') and a
# 3.3 V I/O rail.  The level shifter's input inverters run on the core supply (outc is the
# first inverter's output), its cross-coupled output stage on the I/O rail: the D2A into the
# 1.2 V inverter input must get hiv 1.2 V, outc thresholds 0.6 V, out thresholds 1.65 V.
SP_TWO_RAILS = """\
* vamos e2e 20: a parameterised core supply and a 3.3 V I/O rail, one level shifter
.param vsup=1.2
v_vdd vdd 0 'vsup'
v_io vio 0 3.3
.global vdd vio
""" + INV_MODELS + """\
.subckt ls in out outc
mp1 outc in vdd vdd pch w=2u l=0.2u
mn1 outc in 0 0 nch w=1u l=0.2u
mp4 inbb outc vdd vdd pch w=2u l=0.2u
mn4 inbb outc 0 0 nch w=1u l=0.2u
mn2 outb inbb 0 0 nch w=4u l=0.2u
mn3 out outc 0 0 nch w=4u l=0.2u
mp2 outb out vio vio pch w=1u l=0.2u
mp3 out outb vio vio pch w=1u l=0.2u
cl out 0 10f
c1 outc 0 5f
c2 inbb 0 5f
c3 outb 0 5f
.ends
.tran 0.1n 200n
"""

TB_TWO_RAILS = """\
`timescale 1ns/1ps
module tb;
  reg a = 1'b0;
  wire y, yc;
  ls u1 (.in(a), .out(y), .outc(yc));
  always @(y)  $display("%f y=%b", $realtime, y);
  always @(yc) $display("%f yc=%b", $realtime, yc);
  initial begin
    #50 a = 1'b1;
    #50 a = 1'b0;
    #50 $finish;
  end
endmodule
"""

# e2e 20, PAMS Method #2a (VCS AMS user guide, "Connecting Power Supplies to SPICE
# Subcircuits With Verilog Top"): supply pins in the subckt and on the Verilog instance;
# the Verilog nets drive them through `d2a powernet hiv=1.2 lov=0`.
SP_METHOD_2A = """\
* PAMS Method #2a: supply pins driven from Verilog through d2a powernet
""" + INV_MODELS + """\
.subckt inv in out vdd vss
mp out in vdd vdd pch w=2u l=0.2u
mn out in vss vss nch w=1u l=0.2u
cl out vss 10f
.ends
.tran 0.1n 200n
"""

TB_METHOD_2A = """\
`timescale 1ns/1ps
module top;
  wire vdd, vss;
  assign vdd = 1'b1;
  assign vss = 1'b0;
  reg a = 1'b0;
  wire y;
  inv i1 (.in(a), .out(y), .vdd(vdd), .vss(vss));
  always @(y) $display("%f y=%b", $realtime, y);
  initial begin
    #50 a = 1'b1;
    #50 a = 1'b0;
    #50 $finish;
  end
endmodule
"""

INIT_METHOD_2A = """\
choose xa inv.sp;
d2a powernet hiv=1.2 lov=0 node=top.vdd;
d2a powernet hiv=1.2 lov=0 node=top.vss;
"""

# e2e 20, PAMS Method #2b: a two-port spice_pwr_supply cell instantiated from Verilog
# supplies the inverter through the through-nets vdd_wire / vss_wire (PAMS Examples 3, 4).
SP_METHOD_2B = """\
* two_port.spi
* Power pins specified in subckt port list
""" + INV_MODELS + """\
.subckt inv in out vdd vss
mp out in vdd vdd pch w=2u l=0.2u
mn out in vss vss nch w=1u l=0.2u
cl out vss 10f
.ends
* The spice_pwr_supply subcircuit supplies
* the vdd and vss power supply signals
.subckt spice_pwr_supply vdd vss
v_vdd vdd 0 1.8
v_vss vss 0 0
.ends
.tran 0.1n 200n
"""

TB_METHOD_2B = """\
`timescale 1ns/1ps
module verilog_top;
  wire vdd_wire, vss_wire;
  reg d_in = 1'b0;
  wire d_out;
  // Power pins included in the inverter instance
  inv i1 (.in(d_in), .out(d_out), .vdd(vdd_wire), .vss(vss_wire));
  // spice_pwr_supply supplies VDD and VSS for the design
  spice_pwr_supply s1 (.vdd(vdd_wire), .vss(vss_wire));
  always @(d_out) $display("%f y=%b", $realtime, d_out);
  initial begin
    #50 d_in = 1'b1;
    #50 d_in = 1'b0;
    #50 $finish;
  end
endmodule
"""

# e2e 24: three cut ports whose analog side reaches only MOS gates: a D2A input (a is X
# until 20 ns), an inout pin whose digital side is Z at t=0 (and again after 150 ns), and a
# port port_connect'ed to snps_open.  Without the AMS layer's shunts none of these nodes has
# a DC path; the smoke check and the operating point must still pass on both engines.
# The inverter outputs carry a 1 fF load: the level-1 cards have no capacitance (no TOX,
# no CJ), and an output with no capacitance at all is an algebraic node that Xyce's error
# control cannot step through a 10 ps D2A edge ("Time step too small", also standalone).
SP_GATES = """\
* vamos e2e 24: cut ports whose analog side reaches only MOS gates
""" + INV_MODELS + """\
v_vdd vdd 0 1.8
.global vdd
.subckt gates a io nc ya yio
* a: a D2A input that reaches only the gates of an inverter
mp1 ya a vdd vdd pch w=2u l=0.2u
mn1 ya a 0 0 nch w=1u l=0.2u
cya ya 0 1f
* io: an inout pin whose analog side is gates only
mp2 yio io vdd vdd pch w=2u l=0.2u
mn2 yio io 0 0 nch w=1u l=0.2u
cyio yio 0 1f
* nc: port_connect'ed to snps_open; it reaches one gate (dnc stays high while it is off)
mn3 dnc nc 0 0 nch w=1u l=0.2u
rnc dnc vdd 10k
.ends
.tran 0.1n 200n
"""

TB_GATES = """\
`timescale 1ns/1ps
module tb;
  reg a;                         // X until 20 ns
  reg en = 1'b0, d = 1'b0;
  wire io;
  wire ya, yio;
  assign io = en ? d : 1'bz;     // Z at t=0: nothing drives the pin
  gates u1 (.a(a), .io(io), .ya(ya), .yio(yio));
  always @(io)  $display("%f io=%b", $realtime, io);
  always @(ya)  $display("%f ya=%b", $realtime, ya);
  always @(yio) $display("%f yio=%b", $realtime, yio);
  initial begin
    #20 a = 1'b1;
    #30 en = 1'b1; d = 1'b1;
    #50 d = 1'b0;
    #50 en = 1'b0;
    #50 $finish;
  end
endmodule
"""

INIT_GATES = """\
choose xa gates.sp;
port_connect -cell gates (nc => snps_open);
port_dir -cell gates (inout io);
"""

# e2e 25: .option scale applied once to devices inside a cut cell, and the default
# temperature (25 C) against cards with tnom=25.  Level 1 with LD=0.2u: Leff = 1u - 0.4u,
# Id = kp/2 * W/Leff * (vgs-vto)^2 = 0.5e-4 * 2/0.6 * 0.25 = 4.1667e-5 A (unscaled geometry
# would give 2.5e-5 A; 27 C would move kp and vto).  The H elements turn -1e4 * i(vd*) into
# the port voltages y1 / y54; the D2A drives the gates to 1 V.
SP_SCALE = """\
* vamos e2e 25: .option scale inside a cut cell, default temperature against tnom=25
.option scale=1e-6
.model n1 nmos level=1 vto=0.5 kp=1e-4 ld=0.2u tnom=25
.model n54 nmos level=54 version=4.8 toxe=2n vth0=0.4 u0=0.04 tnom=25
.subckt mcell g y1 y54
vd1 d1 0 1
m1 d1 g 0 0 n1 w=2 l=1
h1 y1 0 vd1 -1e4
vd54 d54 0 1
m54 d54 g 0 0 n54 w=2 l=1 ad=2 as=2 pd=6 ps=6
h54 y54 0 vd54 -1e4
.ends
.tran 0.1n 100n
"""

# the reference: the same cell written in metres, without .option scale
SP_UNSCALED = SP_SCALE.replace(".option scale=1e-6\n", "").replace(
    "w=2 l=1 ad=2 as=2 pd=6 ps=6", "w=2u l=1u ad=2p as=2p pd=6u ps=6u").replace(
    "n1 w=2 l=1", "n1 w=2u l=1u")

TB_SCALE = """\
`timescale 1ns/1ps
module tb;
  reg g = 1'b0;
  wire y1, y54;
  mcell u1 (.g(g), .y1(y1), .y54(y54));
  always @(y1) $display("%f y1=%b", $realtime, y1);
  initial begin
    #20 g = 1'b1;
    #60 $finish;
  end
endmodule
"""

INIT_SCALE = """\
choose xa mos.sp;
d2a hiv=1 lov=0 node=tb.u1.g;
a2d loth=0.30 hith=0.35 node=tb.u1.y1;
"""

# e2e 27: supply1/supply0 nets on SPICE supply pins.  Three identical cells: pwr_auto has
# auto supply ports, pwr_in declares them input, pwr_io inout.  Each loads its pins with
# 1 kohm (1.2-1.8 mA): an ideal POWERNET source keeps vdd at hiv, a 500.7 ohm series D2A
# would sag it by a third.  Each cell's inverter takes its IE levels from its own supply.
SP_SUPPLY_PINS = """\
* vamos e2e 27: SPICE supply pins on Verilog supply1/supply0 nets, 1 kohm loads
""" + INV_MODELS + "".join("""\
.subckt %s vdd vss in out
rl vdd vss 1k
mp out in vdd vdd pch w=2u l=0.2u
mn out in vss vss nch w=1u l=0.2u
cl out vss 10f
.ends
""" % c for c in ("pwr_auto", "pwr_in", "pwr_io")) + """\
.tran 0.1n 200n
"""

TB_SUPPLY_PINS = """\
`timescale 1ns/1ps
module tb;
  supply1 vdd_a, vdd_i, vdd_o;
  supply0 vss_a, vss_i, vss_o;
  reg a_a = 1'b0, a_i = 1'b0, a_o = 1'b0;
  wire ya, yi, yo;
  pwr_auto ua (.vdd(vdd_a), .vss(vss_a), .in(a_a), .out(ya));
  pwr_in   ui (.vdd(vdd_i), .vss(vss_i), .in(a_i), .out(yi));
  pwr_io   uo (.vdd(vdd_o), .vss(vss_o), .in(a_o), .out(yo));
  always @(ya) $display("%f ya=%b", $realtime, ya);
  always @(yi) $display("%f yi=%b", $realtime, yi);
  always @(yo) $display("%f yo=%b", $realtime, yo);
  initial begin
    #50 begin a_a = 1'b1; a_i = 1'b1; a_o = 1'b1; end
    #50 begin a_a = 1'b0; a_i = 1'b0; a_o = 1'b0; end
    #50 $finish;
  end
endmodule
"""

INIT_SUPPLY_PINS = """\
choose xa pwr.sp;
port_dir -cell pwr_in (input vdd, vss);
port_dir -cell pwr_io (inout vdd, vss);
d2a hiv=1.2 lov=0 node=tb.vdd_a;
d2a hiv=1.5 lov=0 node=tb.vdd_i;
d2a hiv=1.8 lov=0 node=tb.vdd_o;
"""

# e2e 27 through a wrapper: the supply1/supply0 nets and the signal reach pwr_auto through
# a Verilog wrapper's input ports (the supply drivers are one scope above the cell).
TB_SUPPLY_WRAPPER = """\
`timescale 1ns/1ps
module wrap (input vdd, input vss, input in, output out);
  pwr_auto u (.vdd(vdd), .vss(vss), .in(in), .out(out));
endmodule
module tb;
  supply1 vdd_a;
  supply0 vss_a;
  reg a = 1'b0;
  wire ya;
  wrap w (.vdd(vdd_a), .vss(vss_a), .in(a), .out(ya));
  always @(ya) $display("%f ya=%b", $realtime, ya);
  initial begin
    #50 a = 1'b1;
    #50 a = 1'b0;
    #50 $finish;
  end
endmodule
"""

INIT_SUPPLY_WRAPPER = """\
choose xa pwr.sp;
d2a hiv=1.2 lov=0 node=tb.vdd_a;
"""


@needs_ams
class TestE2ESupplies(AmsCase):
    """§9 e2e 7, 20, 24, 25 and 27 on every available engine."""

    # -- helpers ----------------------------------------------------------------------------

    def build(self, name: str, engine: str, files: Dict[str, str], *args: str) -> Tuple[str, str]:
        """Write the case, compile tb.sv with vcs-ams on engine; returns (dir, output)."""
        d = self.case("%s_%s" % (name, engine), files)
        r = self.compile(d, "-sverilog", "tb.sv", *args, engine=engine)
        return d, r.stdout

    def run_simv(self, d: str, *args: str, cwd: Optional[str] = None) -> str:
        return self.simv(d, *args, cwd=cwd).stdout

    def plan_nodes(self, d: str) -> Dict[str, dict]:
        """ams.json nodes by canonical name (lower case)."""
        with open(os.path.join(d, "simv.daidir", "ams", "ams.json")) as fh:
            plan = json.load(fh)
        return {n["canonical"].lower(): n for n in plan["nodes"]}

    def plan_cells(self, d: str) -> Dict[str, dict]:
        with open(os.path.join(d, "simv.daidir", "ams", "ams.json")) as fh:
            return json.load(fh)["cells"]

    def deck_text(self, d: str, engine: str, name: str = "vamos") -> str:
        ext = ".sim" if engine == "vacask" else ".cir"
        with open(os.path.join(d, "simv.daidir", "ams", "deck", name + ext)) as fh:
            return fh.read()

    def boundary_text(self, d: str) -> str:
        with open(os.path.join(d, "simv.daidir", "ams", "vamos.boundary")) as fh:
            return fh.read()

    def node(self, nodes: Dict[str, dict], canonical: str) -> str:
        n = nodes.get(canonical.lower())
        self.assertIsNotNone(n, "no analog node %s in the plan (%s)" % (canonical, sorted(nodes)))
        return n["name"]

    def assertV(self, raw, col: str, t: float, want: float, tol: float, what: str = "") -> None:
        v = raw.at(col, t)
        self.assertAlmostEqual(v, want, delta=tol, msg="%s%s: v(%s) at %.4g s is %.6g V, expected %.6g V"
                               % (what + ": " if what else "", raw.path, col, t, v, want))

    def assertIE(self, ies: Dict[str, dict], canonical: str, kind: str, **want: float) -> None:
        """The report's <kind> entry for canonical carries these levels (volts)."""
        e = ies.get(canonical.lower(), {}).get(kind)
        self.assertIsNotNone(e, "IE report has no %s entry for %s (entries: %s)" % (kind, canonical, sorted(ies)))
        for k, v in want.items():
            self.assertIn(k, e, "%s %s: no %s= in %s" % (kind, canonical, k, e))
            self.assertAlmostEqual(float(e[k]), v, delta=1e-9, msg="%s %s: %s=%s, expected %g"
                                   % (kind, canonical, k, e[k], v))

    def assertToggles(self, out: str, tag: str, at0: str, edges: List[Tuple[float, str]],
                      within: float = 1.5) -> None:
        """The digital value printed as <tag> settles to at0 at t=0 and then changes exactly at
        the given (time ns, value) edges, each event no more than `within` ns after it."""
        ev = fevents(out, tag)
        self.assertEqual(settled(ev, 0.0), at0, "%s at t=0 (events %s)\n%s" % (tag, ev, out))
        later = after_zero(ev)
        self.assertEqual([v for _, v in later], [v for _, v in edges],
                         "%s events after t=0: %s, expected values %s" % (tag, later, edges))
        for (t, v), (te, ve) in zip(later, edges):
            self.assertGreaterEqual(t, te, "%s=%s at %g ns, before the cause at %g ns" % (tag, v, t, te))
            self.assertLessEqual(t, te + within, "%s=%s at %g ns, more than %g ns after %g ns"
                                 % (tag, v, t, within, te))

    # -- e2e 7: x-heep ----------------------------------------------------------------------

    def test_07_xheep_adc(self):
        """x-heep adc.sp and control.init verbatim (GND/OUT/SEL<1>/SEL<0>/VDD, `v_gnd GND 0 0`,
        `.global VDD GND`, port_connect, port_dir, bus_format <%d>), compiled the way x-heep's
        vcs-ams target does it, with a 1 ns maximum step.  The comparator's 0.6 V crossings
        agree between VACASK and Xyce within 1 % of the 1 us input sine period, and the
        digital `out` changes at each crossing."""
        crossings: Dict[str, List[float]] = {}
        for engine in self.engines():
            with self.subTest(engine=engine):
                root = os.path.join(self.tmp, "xheep_" + engine)
                shutil.copytree(XHEEP, root)
                cwd = os.path.join(root, *XHEEP_RUN_DIR)
                r = self.compile(cwd, "-sverilog", "../../../hw/ip_examples/ams/rtl/tb_ams_adc.sv",
                                 "-ad=../../../hw/ip_examples/ams/analog/control.init",
                                 "--vamos-analog-maxstep=1n", engine=engine, tool="vcs")
                self.assertIn("v_gnd: both terminals are ground; the source is dropped", r.stdout)
                self.assertIn("no .tran: the run ends at $finish/$stop or at 3600 s", r.stdout)

                # the IE report: two D2A sel bits at the 1.2 V VDD (traced through the DEMUX
                # gates), one A2D at 0.6 V; nothing on the port_connect'ed VDD/GND
                ies = ie_report(self.report(cwd))
                top = "tb_ams_adc.ams_adc_1b_i."
                for bit in ("sel<0>", "sel<1>"):
                    self.assertIE(ies, top + bit, "d2a", hiv=1.2, lov=0.0)
                self.assertIE(ies, top + "out", "a2d", loth=0.6, hith=0.6)
                self.assertEqual(sorted(ies), sorted(top + p for p in ("out", "sel<0>", "sel<1>")),
                                 "IE report entries")
                nodes = self.plan_nodes(cwd)
                self.assertEqual({k: v["role"] for k, v in nodes.items()},
                                 {top + "out": "A2D", top + "sel<0>": "D2A", top + "sel<1>": "D2A"})
                n_out, n_s0, n_s1 = (self.node(nodes, top + p) for p in ("out", "sel<0>", "sel<1>"))

                # a run bounded with +vcs+finish+N (the design's way to bound a synthesized
                # stop); the testbench's $finish at 3.9 us ends it
                out = self.run_simv(cwd, "+vcs+finish+4000000")
                self.assertIn("co-simulation finished: digital stop at 3.9e-06 s", out)
                raw = self.raw(cwd)
                self.assertAlmostEqual(raw.last_time(), 3.9e-6, delta=1e-15)
                self.assertV(raw, "vdd", 1e-6, 1.2, 1e-9, "VDD (.global, port_connect vdd => vdd)")
                vmux = column_ending(raw, ":vmux_out")
                # sel 00 / 01 / 10: SEL<0>, SEL<1> at 0 or 1.2 V; the mux passes tap A/B/C
                for t, s0, s1, tap in ((1.0e-6, 0.0, 0.0, 0.24), (2.4e-6, 1.2, 0.0, 0.48),
                                       (3.7e-6, 0.0, 1.2, 0.72)):
                    self.assertV(raw, n_s0, t, s0, 1e-3, "SEL<0>")
                    self.assertV(raw, n_s1, t, s1, 1e-3, "SEL<1>")
                    self.assertV(raw, vmux, t, tap, 1e-2, "mux output")      # taps are 0.24 V apart
                rising = raw.crossings(n_out, 0.6, +1)
                falling = raw.crossings(n_out, 0.6, -1)
                both = sorted([(t, "1") for t in rising] + [(t, "0") for t in falling])
                self.assertGreaterEqual(len(both), 6, "comparator crossings: %s" % both)
                crossings[engine] = [t for t, _ in both]
                # the A2D: one digital event per analog crossing, at most one 1 ns step later
                ev = after_zero(fevents(out, "out"))
                self.assertEqual([v for _, v in ev], [v for _, v in both],
                                 "digital out events %s against analog crossings %s" % (ev, both))
                for (td, v), (ta, _) in zip(ev, both):
                    self.assertGreaterEqual(td * 1e-9, ta - 5e-11, "out=%s at %g ns, crossing at %g s" % (v, td, ta))
                    self.assertLessEqual(td * 1e-9, ta + 1.05e-9, "out=%s at %g ns, crossing at %g s" % (v, td, ta))

                # x-heep's own flow: plain ./simv under the synthesized 3600 s stop
                # (--stop-time=3600000000000000001fs, far beyond 2^32 fs: a count that wrapped
                # to 32 bits would end the analog at 6.61e-07 s); $finish at 3.9 us ends it
                with self.subTest(engine=engine, run="plain ./simv"):
                    r = self.simv(cwd, expect_rc=None)
                    self.assertEqual(r.returncode, 0, r.stdout)
                    self.assertIn("co-simulation finished: digital stop at 3.9e-06 s", r.stdout)
        if len(crossings) == 2:
            cv, cx = crossings["vacask"], crossings["xyce"]
            self.assertEqual(len(cv), len(cx), "VACASK %s\nXyce %s" % (cv, cx))
            for a, b in zip(cv, cx):
                self.assertAlmostEqual(a, b, delta=0.01 * 1e-6,
                                       msg="comparator crossing VACASK %.6g s, Xyce %.6g s" % (a, b))

    # -- e2e 20: supplies -------------------------------------------------------------------

    def test_20_param_supply_and_two_rails(self):
        """`.param vsup=1.2`, `v_vdd vdd 0 'vsup'`: the IE reference is 1.2 V, not the 3.3 V
        fallback; in the same deck a 3.3 V rail: the D2A into the 1.2 V inverter input gets
        hiv 1.2 V, the core output thresholds 0.6 V, the 3.3 V output 1.65 V."""
        files = {"ls.sp": SP_TWO_RAILS, "tb.sv": TB_TWO_RAILS, "vcsAD.init": "choose xa ls.sp;\n"}
        for engine in self.engines():
            with self.subTest(engine=engine):
                d, cout = self.build("rails", engine, files)
                self.assertNotIn("3.3 V fallback", cout)
                ies = ie_report(self.report(d))
                self.assertIE(ies, "tb.u1.in", "d2a", hiv=1.2, lov=0.0)
                self.assertIE(ies, "tb.u1.outc", "a2d", loth=0.6, hith=0.6)
                self.assertIE(ies, "tb.u1.out", "a2d", loth=1.65, hith=1.65)
                self.assertTrue(any(c.startswith("levels: reference trace v_vdd")
                                    for c in ies["tb.u1.in"]["comments"]), ies["tb.u1.in"])
                self.assertTrue(any(c.startswith("levels: reference trace v_io")
                                    for c in ies["tb.u1.out"]["comments"]), ies["tb.u1.out"])
                nodes = self.plan_nodes(d)
                n_in, n_out, n_outc = (self.node(nodes, "tb.u1." + p) for p in ("in", "out", "outc"))
                out = self.run_simv(d)
                raw = self.raw(d)
                self.assertV(raw, "vdd", 1e-8, 1.2, 1e-9, "v_vdd = 'vsup'")
                self.assertV(raw, "vio", 1e-8, 3.3, 1e-9)
                for t, a in ((25e-9, 0), (75e-9, 1), (125e-9, 0)):
                    self.assertV(raw, n_in, t, 1.2 * a, 1e-4, "D2A in")
                    self.assertV(raw, n_outc, t, 1.2 * (1 - a), 5e-3, "core output")
                    self.assertV(raw, n_out, t, 3.3 * a, 5e-3, "I/O output")
                self.assertToggles(out, "y", "0", [(50.0, "1"), (100.0, "0")])
                self.assertToggles(out, "yc", "1", [(50.0, "0"), (100.0, "1")])

    def test_20_pams_method_2a_d2a_powernet(self):
        """PAMS Method #2a: the subckt's vdd/vss pins are driven from Verilog nets with
        `d2a powernet hiv=1.2 lov=0 node=top.vdd` (and top.vss): ideal sources at 1.2 V and
        0 V, and the inverter's signal IEs get hiv 1.2 V and thresholds 0.6 V."""
        files = {"inv.sp": SP_METHOD_2A, "tb.sv": TB_METHOD_2A, "vcsAD.init": INIT_METHOD_2A}
        for engine in self.engines():
            with self.subTest(engine=engine):
                d, cout = self.build("m2a", engine, files)
                ies = ie_report(self.report(d))
                for sup, lvl in (("vdd", 1.2), ("vss", 0.0)):
                    e = ies.get("top.i1." + sup, {}).get("d2a", {})
                    self.assertIs(e.get("powernet"), True, "d2a powernet on top.i1.%s: %s" % (sup, e))
                    self.assertIE(ies, "top.i1." + sup, "d2a", hiv=1.2, lov=0.0)
                    self.assertIn("top.%s" % sup, " ".join(ies["top.i1." + sup]["comments"]))
                nodes = self.plan_nodes(d)
                n_in, n_vdd, n_vss = (self.node(nodes, "top.i1." + p) for p in ("in", "vdd", "vss"))
                deck = self.deck_text(d, engine)
                for n in (n_vdd, n_vss):                     # powernet: no gated element, no enable
                    self.assertNotIn(n + "_e", deck)
                out = self.run_simv(d)
                raw = self.raw(d)
                for t in (25e-9, 75e-9, 125e-9):
                    self.assertV(raw, n_vdd, t, 1.2, 1e-9, "top.vdd through d2a powernet")
                    self.assertV(raw, n_vss, t, 0.0, 1e-9, "top.vss through d2a powernet")
                # the reference trace from in/out reaches the d2a powernet vdd/vss nodes: they
                # count as supplies (no 3.3 V fallback, which would put 3.3 V on the 1.2 V
                # inverter's input and a 1.65 V threshold on its output that is never reached)
                with self.subTest(engine=engine, check="signal IE levels from the powernet"):
                    self.assertIE(ies, "top.i1.in", "d2a", hiv=1.2, lov=0.0)
                    self.assertIE(ies, "top.i1.out", "a2d", loth=0.6, hith=0.6)
                    self.assertNotIn("3.3 V fallback", cout)
                with self.subTest(engine=engine, check="D2A into the 1.2 V inverter"):
                    self.assertV(raw, n_in, 75e-9, 1.2, 1e-4, "D2A in")
                with self.subTest(engine=engine, check="digital reads the 1.2 V output"):
                    self.assertToggles(out, "y", "1", [(50.0, "0"), (100.0, "1")])

    def test_20_pams_method_2b_spice_supply_cell(self):
        """PAMS Method #2b: a two-port spice_pwr_supply cell (`v_vdd vdd 0 1.8`) instantiated
        from Verilog supplies the inverter through the through-nets vdd_wire/vss_wire: the
        signal IEs get 1.8 V / 0.9 V."""
        files = {"two_port.spi": SP_METHOD_2B, "tb.sv": TB_METHOD_2B,
                 "vcsAD.init": "choose xa two_port.spi;\n"}
        for engine in self.engines():
            with self.subTest(engine=engine):
                d, cout = self.build("m2b", engine, files)
                self.assertNotIn("3.3 V fallback", cout)
                ies = ie_report(self.report(d))
                self.assertIE(ies, "verilog_top.i1.in", "d2a", hiv=1.8, lov=0.0)
                self.assertIE(ies, "verilog_top.i1.out", "a2d", loth=0.9, hith=0.9)
                nodes = self.plan_nodes(d)
                for sup in ("vdd", "vss"):
                    n = nodes.get("verilog_top.i1." + sup)
                    self.assertIsNotNone(n, sorted(nodes))
                    self.assertEqual(n["role"], "THROUGH", n)
                    entry = ies.get("verilog_top.i1." + sup, {})
                    self.assertNotIn("d2a", entry)
                    self.assertNotIn("a2d", entry)
                    self.assertTrue(any("through-net" in c for c in entry.get("comments", [])), entry)
                self.assertFalse([k for k in ies if k.startswith("verilog_top.s1.")], sorted(ies))
                n_in, n_out, n_vdd, n_vss = (self.node(nodes, "verilog_top.i1." + p)
                                             for p in ("in", "out", "vdd", "vss"))
                out = self.run_simv(d)
                raw = self.raw(d)
                for t, a in ((25e-9, 0), (75e-9, 1), (125e-9, 0)):
                    self.assertV(raw, n_vdd, t, 1.8, 1e-6, "vdd_wire from spice_pwr_supply")
                    self.assertV(raw, n_vss, t, 0.0, 1e-6, "vss_wire from spice_pwr_supply")
                    self.assertV(raw, n_in, t, 1.8 * a, 1e-4, "D2A in")
                    self.assertV(raw, n_out, t, 1.8 * (1 - a), 5e-3, "inverter output")
                self.assertToggles(out, "y", "1", [(50.0, "0"), (100.0, "1")])

    # -- e2e 24: gate-only nodes ------------------------------------------------------------

    def test_24_gate_only_nodes(self):
        """A port_connect `nc => snps_open` port and a D2A input, each reaching only MOS
        gates, and an inout pin whose digital side is Z at t=0 with a gate-only analog side:
        the smoke check passes and the operating point converges on both engines."""
        files = {"gates.sp": SP_GATES, "tb.sv": TB_GATES, "vcsAD.init": INIT_GATES}
        for engine in self.engines():
            with self.subTest(engine=engine):
                d, cout = self.build("gates", engine, files)
                nodes = self.plan_nodes(d)
                self.assertEqual(nodes["tb.u1.a"]["role"], "D2A")
                self.assertEqual(nodes["tb.u1.io"]["role"], "BIDIR")
                n_a, n_io, n_ya, n_yio = (self.node(nodes, "tb.u1." + p) for p in ("a", "io", "ya", "yio"))
                # the smoke check ran (§4.7): code sources at DC 0, enables at DC 1.0, an OP
                smoke = self.deck_text(d, engine, "smoke")
                self.assertNotIn("code:", smoke)
                for n in (n_a, n_io):
                    self.assertTrue(re.search(r"(?im)^\S+\s+\(?%s_e\s+0\)?\s+.*\b(dc=|dc\s+)1(\.0*)?\s*$"
                                              % re.escape(n), smoke), "enable of %s in\n%s" % (n, smoke))
                self.assertTrue(os.path.isfile(os.path.join(d, "simv.daidir", "ams", "deck", "smoke.log")))
                ies = ie_report(self.report(d))
                self.assertIE(ies, "tb.u1.a", "d2a", hiv=1.8, lov=0.0)
                self.assertIE(ies, "tb.u1.io", "d2a", hiv=1.8, lov=0.0)
                self.assertIE(ies, "tb.u1.io", "a2d", loth=0.9, hith=0.9)
                for p in ("a", "io"):
                    self.assertTrue(any(c.startswith("shunt ") for c in ies["tb.u1." + p]["comments"]),
                                    ies["tb.u1." + p])
                deck = self.deck_text(d, engine)
                nc = re.findall(r"\b(nc_\w+)\b", deck)
                self.assertTrue(nc, "no nc_ node for the snps_open port in the deck")
                n_nc = nc[0]
                self.assertTrue(re.search(r"(?m)^rsh_\S+\s+\(?%s\s+0\b" % re.escape(n_nc), deck),
                                "no shunt on the snps_open node %s" % n_nc)

                out = self.run_simv(d)
                self.assertIn("co-simulation finished: digital stop at 2e-07 s", out)
                raw = self.raw(d)
                dnc = column_ending(raw, ":dnc")
                # the operating point: the open pin and the released inout pin sit at 0 V on
                # their shunts, a (X, x2v=0) is driven to lov, every gate it reaches is off/on
                self.assertV(raw, n_io, 0.0, 0.0, 1e-6, "inout pin, Z at t=0")
                self.assertV(raw, n_nc, 0.0, 0.0, 1e-6, "snps_open pin")
                self.assertV(raw, dnc, 0.0, 1.8, 1e-3, "drain of the snps_open-gated MOS")
                self.assertV(raw, n_a, 0.0, 0.0, 1e-4, "D2A of an X (x2v=0)")
                self.assertV(raw, n_ya, 0.0, 1.8, 1e-3)
                self.assertV(raw, n_yio, 0.0, 1.8, 1e-3)
                # then a=1 at 20 ns; io driven 1 at 50 ns, 0 at 100 ns, released at 150 ns
                for t, va, vio, vyio in ((40e-9, 1.8, 0.0, 1.8), (75e-9, 1.8, 1.8, 0.0),
                                         (125e-9, 1.8, 0.0, 1.8), (175e-9, 1.8, 0.0, 1.8)):
                    self.assertV(raw, n_a, t, va, 1e-4, "D2A a")
                    self.assertV(raw, n_io, t, vio, 1e-3, "inout pin")
                    self.assertV(raw, n_yio, t, vyio, 5e-3)
                    self.assertV(raw, n_nc, t, 0.0, 1e-6, "snps_open pin")
                    self.assertV(raw, dnc, t, 1.8, 1e-3)
                self.assertToggles(out, "ya", "1", [(20.0, "0")])
                self.assertToggles(out, "yio", "1", [(50.0, "0"), (100.0, "1")])
                ev = fevents(out, "io")
                self.assertEqual(settled(ev, 0.0), "0", "io reads the analog 0 V at t=0: %s" % ev)
                self.assertEqual(settled(ev, 75.0), "1", ev)
                self.assertEqual(settled(ev, 199.0), "0", "io after release: %s" % ev)

                with self.subTest(engine=engine, check="IE report lists the snps_open shunt"):
                    # §3.6, §4.7: "every unconnected cut-port bit and every shunt is listed",
                    # including the nc_<k> node the deck makes for a port_connect snps_open port
                    self.assertIn("rsh_" + n_nc, self.report(d))

    # -- e2e 25: .option scale and the default temperature -----------------------------------

    def test_25_scale_inside_a_cell_and_default_temperature(self):
        """`.option scale=1e-6` with `w=2 l=1` inside a cut cell gives the current of
        `w=2u l=1u` (level 1 with LD=0.2u: 4.1667e-5 A; unscaled 2.5e-5 A) and the same BSIM4
        current; the default temperature (25 C) against cards with tnom=25 shifts nothing."""
        y54: Dict[Tuple[str, str], float] = {}
        for engine in self.engines():
            for kind, sp in (("scaled", SP_SCALE), ("metres", SP_UNSCALED)):
                with self.subTest(engine=engine, deck=kind):
                    files = {"mos.sp": sp, "tb.sv": TB_SCALE, "vcsAD.init": INIT_SCALE}
                    d, cout = self.build("scale_" + kind, engine, files)
                    ies = ie_report(self.report(d))
                    self.assertIE(ies, "tb.u1.g", "d2a", hiv=1.0, lov=0.0)
                    self.assertIE(ies, "tb.u1.y1", "a2d", loth=0.30, hith=0.35)
                    nodes = self.plan_nodes(d)
                    n_g, n_y1, n_y54 = (self.node(nodes, "tb.u1." + p) for p in ("g", "y1", "y54"))
                    out = self.run_simv(d)
                    raw = self.raw(d)
                    self.assertV(raw, n_g, 10e-9, 0.0, 1e-6)
                    for t in (50e-9, 79e-9):
                        self.assertV(raw, n_g, t, 1.0, 1e-6, "D2A gate")
                        # y1 = 1e4 * Id(level 1): 0.41667 V; 25 C, not 27 C (> 4e-4 away)
                        self.assertV(raw, n_y1, t, 0.416667, 1e-5, "level-1 Id * 1e4")
                    y54[(engine, kind)] = raw.at(n_y54, 79e-9)
                    self.assertGreater(y54[(engine, kind)], 0.5, "BSIM4 Id * 1e4")
                    self.assertToggles(out, "y1", "0", [(20.0, "1")])
        for engine in self.engines():
            a, b = y54.get((engine, "scaled")), y54.get((engine, "metres"))
            if a is not None and b is not None:
                self.assertAlmostEqual(a, b, delta=abs(b) * 1e-6,
                                       msg="%s: BSIM4 scaled %.8g, in metres %.8g" % (engine, a, b))
        if ("vacask", "scaled") in y54 and ("xyce", "scaled") in y54:
            a, b = y54[("vacask", "scaled")], y54[("xyce", "scaled")]
            self.assertAlmostEqual(a, b, delta=abs(b) * 1e-3, msg="BSIM4 VACASK %.8g, Xyce %.8g" % (a, b))

    # -- e2e 27: supply1/supply0 ------------------------------------------------------------

    def test_27_supply1_supply0_pins(self):
        """`supply1`/`supply0` nets on SPICE supply pins, as auto, input and inout ports:
        ideal sources (no series R), vdd at hiv and vss at 0 V under a 1 kohm load, and each
        cell's signal IEs at its own supply."""
        files = {"pwr.sp": SP_SUPPLY_PINS, "tb.sv": TB_SUPPLY_PINS, "vcsAD.init": INIT_SUPPLY_PINS}
        cells = (("ua", "pwr_auto", 1.2), ("ui", "pwr_in", 1.5), ("uo", "pwr_io", 1.8))
        for engine in self.engines():
            with self.subTest(engine=engine):
                d, cout = self.build("supplies", engine, files)
                pcells = self.plan_cells(d)
                dirs = {c: {p["verilog"]: (p["declared"], p["shell_dir"]) for p in pcells[c]["ports"]}
                        for _, c, _ in cells}
                self.assertEqual(dirs["pwr_auto"]["vdd"][0], "auto", dirs)
                self.assertEqual(dirs["pwr_in"]["vdd"], ("input", "input"), dirs)
                self.assertEqual(dirs["pwr_io"]["vdd"], ("inout", "inout"), dirs)
                nodes = self.plan_nodes(d)
                ies = ie_report(self.report(d))
                deck = self.deck_text(d, engine)
                boundary = self.boundary_text(d)
                sup_nodes = {}
                for inst, _, hiv in cells:
                    for sup, lvl in (("vdd", hiv), ("vss", 0.0)):
                        canon = "tb.%s.%s" % (inst, sup)
                        self.assertEqual(nodes[canon]["role"], "POWERNET", nodes[canon])
                        n = nodes[canon]["name"]
                        sup_nodes[(inst, sup)] = (n, lvl)
                        self.assertTrue(any(re.search(r"supply net, ideal %s V source \(powernet\)"
                                                      % re.escape(repr(lvl)), c)
                                            for c in ies[canon]["comments"]), ies[canon])
                        # no bridge, no gated element: only an ideal DC source on the node
                        self.assertNotIn(n + "_d", deck)
                        self.assertNotIn(n + "_e", deck)
                        self.assertNotIn(canon, boundary.lower())
                        self.assertTrue(re.search(r"(?im)^\S+\s+\(?%s\s+0\)?\s+.*\bdc\b\s*=?\s*%s\b"
                                                  % (re.escape(n), re.escape(repr(lvl))), deck),
                                        "no ideal %g V DC source on %s in the deck" % (lvl, n))
                    self.assertIE(ies, "tb.%s.in" % inst, "d2a", hiv=hiv, lov=0.0)
                    self.assertIE(ies, "tb.%s.out" % inst, "a2d", loth=hiv / 2, hith=hiv / 2)
                out = self.run_simv(d)
                raw = self.raw(d)
                for (inst, sup), (n, lvl) in sorted(sup_nodes.items()):
                    for t in (0.0, 25e-9, 75e-9, 125e-9):
                        # 1.2-1.8 mA through 1 kohm: a 500.7 ohm series D2A would leave 2/3 of hiv
                        self.assertV(raw, n, t, lvl, 1e-6, "%s %s under the 1 kohm load" % (inst, sup))
                for inst, _, hiv in cells:
                    n_out = self.node(nodes, "tb.%s.out" % inst)
                    self.assertV(raw, n_out, 25e-9, hiv, 5e-3, inst + " output")
                    self.assertV(raw, n_out, 75e-9, 0.0, 5e-3, inst + " output")
                    self.assertToggles(out, "y" + inst[1], "1", [(50.0, "0"), (100.0, "1")])
                with self.subTest(engine=engine, check="no 3.3 V fallback warning"):
                    # a supply0 net's level (0 V) never depends on the fallback, so it must not
                    # warn (under --vamos-strict the warning would fail a well-defined design)
                    self.assertNotIn("3.3 V fallback", cout)

    def test_27_supply_pins_through_a_wrapper(self):
        """supply1/supply0 nets reaching a SPICE cell's supply pins through a Verilog
        wrapper's input ports: the drivers sit one scope above the cell; the nodes are
        still ideal POWERNET sources and the cell's IEs use them.  Once with the cell's
        ports auto (the VCS default), once with `port_dir ... input`."""
        variants = (("auto", INIT_SUPPLY_WRAPPER),
                    ("port_dir", INIT_SUPPLY_WRAPPER + "port_dir -cell pwr_auto (input vdd, vss, in);\n"))
        for engine in self.engines():
            for kind, init in variants:
                with self.subTest(engine=engine, ports=kind):
                    d = self.case("wrapper_%s_%s" % (kind, engine),
                                  {"pwr.sp": SP_SUPPLY_PINS, "tb.sv": TB_SUPPLY_WRAPPER, "vcsAD.init": init})
                    self.compile(d, "-sverilog", "tb.sv", engine=engine)
                    nodes = self.plan_nodes(d)
                    ies = ie_report(self.report(d))
                    for sup, lvl in (("vdd", 1.2), ("vss", 0.0)):
                        canon = "tb.w.u." + sup
                        self.assertEqual(nodes[canon]["role"], "POWERNET", nodes[canon])
                        self.assertTrue(any("supply net, ideal %r V source (powernet)" % lvl in c
                                            for c in ies[canon]["comments"]), ies[canon])
                    self.assertIE(ies, "tb.w.u.in", "d2a", hiv=1.2, lov=0.0)
                    self.assertIE(ies, "tb.w.u.out", "a2d", loth=0.6, hith=0.6)
                    n_vdd, n_vss, n_out = (self.node(nodes, "tb.w.u." + p) for p in ("vdd", "vss", "out"))
                    out = self.run_simv(d)
                    raw = self.raw(d)
                    for t in (0.0, 25e-9, 75e-9, 125e-9):
                        self.assertV(raw, n_vdd, t, 1.2, 1e-6, "vdd under the 1 kohm load")
                        self.assertV(raw, n_vss, t, 0.0, 1e-6, "vss")
                    self.assertV(raw, n_out, 25e-9, 1.2, 5e-3)
                    self.assertV(raw, n_out, 75e-9, 0.0, 5e-3)
                    self.assertToggles(out, "ya", "1", [(50.0, "0"), (100.0, "1")])


if __name__ == "__main__":
    unittest.main()
