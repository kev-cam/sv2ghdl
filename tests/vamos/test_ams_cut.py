"""vamos.ams.cut: the digital cut (docs/VAMOS_AMS_DESIGN.md §5.4, §5.5).

Unit tests run on the captured design.vhd fixtures (tests/vamos/fixtures/vhdl/
cut_*/); the CutCells and subckts are built here the way shells.py and
spice.py would.  The parameter tests (§4.7) add real and string "--   P = v"
lines to those fixtures and check cut.param_overrides.  The engine tests (WSL)
analyse and elaborate the emitted cut.vhd with nvc, and drive the A2D and D2A
templates with no engine (the A2D window; the D2A's x2v=3/4 rules); the
co-simulation tests run two hand-written VACASK decks (fixtures/vhdl/cut_cosim/)
against it: the shared-parent case with different levels and roles per path,
and a bidirectional pin.

    python3 -m unittest discover -s tests/vamos -p 'test_ams_cut.py' -v
"""

import os
import re
import shutil
import struct
import time
import unittest

from vamos_testlib import TempDir, fixture, needs_stack, needs_vacask, openvaf_bin, run

from vamos import tools  # noqa: E402
from vamos.ams import cut, engines, names, vhdl  # noqa: E402
from vamos.ams.config import AmsConfig, PortConnect, UseSpice  # noqa: E402
from vamos.ams.model import (A2D, AUTO, BIDIR, D2A, DISABLED, INOUT, INPUT, LOGIC, NONE,  # noqa: E402
                             OUTPUT, POWERNET, PULL_UP, RA2D, RD2A, REAL, REMOVED, STRONG,
                             SUPPLY0, SUPPLY1, THROUGH, WEAK, A2D_IE, AmsPlan, CutCell, CutInstance,
                             CutPort, D2A_IE, RuleHits)
from vamos.netlist.ir import Netlist, Subckt  # noqa: E402
from vamos.notes import NoteError  # noqa: E402


# -- helpers ------------------------------------------------------------------------

def cell(name, ports, view="spice", sub=None, **kw):
    """ports: (verilog, kind, declared[, range[, shell_dir]]); range (msb, lsb) or '[W-1:0]'."""
    cps = []
    for i, p in enumerate(ports):
        nm, kind, declared = p[:3]
        rng = p[3] if len(p) > 3 else None
        shell = p[4] if len(p) > 4 else (declared if declared != AUTO else INOUT)
        cp = CutPort(i, nm, kind, declared, shell)
        if isinstance(rng, str):
            cp.range_text = rng
        elif rng is not None:
            cp.msb, cp.lsb = rng
            cp.range_text = "[%d:%d]" % rng
        cps.append(cp)
    return CutCell(name, view, (sub or name).lower(), cps, **kw)


def subckt(name, ports, gnd=()):
    s = Subckt(name.lower(), [p.lower() for i, p in enumerate(ports) if i not in gnd],
               orig_ports=[p.lower() for p in ports], gnd_ports=list(gnd))
    s.spell = {p.lower(): p for p in ports}           # what spice.py keeps in Netlist.spelling
    return s


def netlist(subs):
    nl = Netlist(body=subs)
    for s in subs:
        nl.spelling.update(getattr(s, "spell", {}))
    return nl


CELLS = {
    "shared": ([cell("rc_sp", [("a", LOGIC, INPUT), ("y", LOGIC, OUTPUT)])],
               [subckt("rc_sp", ["a", "y"])]),
    "bidir": ([cell("pad_sp", [("pad", LOGIC, INOUT)])], [subckt("pad_sp", ["pad"])]),
    "readable": ([cell("src", [("a", LOGIC, INPUT), ("vo", LOGIC, OUTPUT)]),
                  cell("src2", [("a", LOGIC, INPUT), ("q", LOGIC, OUTPUT)]),
                  cell("sink", [("a", LOGIC, INPUT), ("q", LOGIC, OUTPUT)]),
                  cell("pad_sp", [("pad", LOGIC, INOUT)]),
                  cell("bg", [("vref", LOGIC, OUTPUT)]),
                  cell("adc", [("vin", LOGIC, INPUT), ("clk", LOGIC, INPUT), ("q", LOGIC, OUTPUT)])],
                 [subckt("src", ["a", "vo"]), subckt("src2", ["a", "q"]), subckt("sink", ["a", "q"]),
                  subckt("pad_sp", ["pad"]), subckt("bg", ["vref"]),
                  subckt("adc", ["vin", "clk", "q"])]),
    "prims": ([cell("cin", [("a", LOGIC, INPUT)]),
               cell("sup", [("vdd", LOGIC, INPUT), ("vss", LOGIC, INPUT), ("vb", LOGIC, INPUT, (1, 0))])],
              [subckt("cin", ["a"]), subckt("sup", ["vdd", "vss", "vb[1]", "vb[0]"])]),
    "vec": ([cell("cout", [("y", LOGIC, OUTPUT)]), cell("cin2", [("d", LOGIC, INPUT, (1, 0))])],
            [subckt("cout", ["y"]), subckt("cin2", ["d[1]", "d[0]"])]),
    "temps": ([cell("cin", [("a", LOGIC, INPUT)]), cell("cin2", [("d", LOGIC, INPUT, (1, 0))]),
               cell("cout", [("y", LOGIC, OUTPUT)]), cell("cout2", [("y", LOGIC, OUTPUT, (1, 0))])],
              [subckt("cin", ["a"]), subckt("cin2", ["d[1]", "d[0]"]), subckt("cout", ["y"]),
               subckt("cout2", ["y[1]", "y[0]"])]),
    "dac": ([cell("flash", [("clk", LOGIC, INPUT), ("q", LOGIC, OUTPUT, "[N-1:0]")], view="multi",
                  params={"N": "4"}),
             cell("pio_sp", [("pad", LOGIC, AUTO, None, INOUT), ("y", LOGIC, OUTPUT)])],
            [subckt("flash", ["clk"] + ["q[%d]" % i for i in range(7, -1, -1)]),
             subckt("pio_sp", ["pad", "y"])]),
    "force": ([cell("cin", [("a", LOGIC, INPUT)])], [subckt("cin", ["a"])]),
    "real": ([cell("vamp", [("vin", REAL, INPUT), ("vout", REAL, OUTPUT)], view="multi")],
             [subckt("vamp", ["vin", "vout"])]),
    "ports": ([cell("rw", [("in", LOGIC, INPUT), ("out", LOGIC, OUTPUT), ("signal", LOGIC, INPUT),
                           ("bus", LOGIC, INPUT), ("open", LOGIC, OUTPUT), ("_a", LOGIC, INPUT),
                           ("b_", LOGIC, INOUT), ("c__d", LOGIC, OUTPUT), ("inv", LOGIC, OUTPUT)]),
               cell("rw2", [("OUT", LOGIC, OUTPUT), ("In", LOGIC, INPUT)])] +
              [cell(n, [("a", LOGIC, INPUT), ("y", LOGIC, OUTPUT)])
               for n in ("buffer", "block", "register", "my__cell", "cell_", "_cell")],
              [subckt("rw", ["in", "out", "signal", "bus", "open", "_a", "b_", "c__d", "inv"]),
               subckt("rw2", ["OUT", "In"])] +
              [subckt(n, ["a", "y"]) for n in ("buffer", "block", "register", "my__cell", "cell_",
                                                "_cell")]),
    "swvp": ([cell("pad_sp", [("pad", LOGIC, INOUT)])], [subckt("pad_sp", ["pad"])]),
    "tri": ([cell("cin", [("a", LOGIC, INPUT)])], [subckt("cin", ["a"])]),
    "pullshare": ([cell("pio", [("pad", LOGIC, AUTO, None, INOUT)])], [subckt("pio", ["pad"])]),
    "portbuf": ([cell("cin", [("a", LOGIC, INPUT)]),
                 cell("srci", [("a", LOGIC, INOUT), ("vo", LOGIC, OUTPUT)]),
                 cell("srcv", [("a", LOGIC, INOUT, (1, 0)), ("vo", LOGIC, OUTPUT, (1, 0))]),
                 cell("srco", [("a", LOGIC, OUTPUT), ("vo", LOGIC, OUTPUT)])],
                [subckt("cin", ["a"]), subckt("srci", ["a", "vo"]),
                 subckt("srcv", ["a[1]", "a[0]", "vo[1]", "vo[0]"]), subckt("srco", ["a", "vo"])]),
}


def parse(case, name="design.vhd"):
    return vhdl.parse(fixture("vhdl", "cut_" + case, name))


def analyse(case, cells=None, subs=None, cfg=None, name="design.vhd", hits=None, design=None):
    c, s = CELLS[case]
    d = design if design is not None else parse(case, name)
    return cut.analyse(d, "tb", cells if cells is not None else c,
                       netlist(subs if subs is not None else s), cfg or AmsConfig(),
                       hits if hits is not None else RuleHits())


def roles(ana, disabled=None, removal=None, alloc=None):
    return cut.assign_roles(ana, alloc or names.NameAllocator(), disabled or (lambda n: False),
                            removal or (lambda n: (False, None)))


def by_canonical(nodes):
    return {n.canonical: n for n in nodes}


def messages(notes, severity=None):
    return [n.message for n in notes if severity is None or n.severity == severity]


def fill_levels(nodes, levels=None):
    """Give every bridged node the levels deck.py would (1.8 V, 0.6/1.2 V by default)."""
    levels = levels or {}
    for n in nodes:
        hiv, lov, loth, hith = levels.get(n.canonical, (1.8, 0.0, 0.6, 1.2))
        if n.role in (D2A, BIDIR, RD2A):
            n.d2a = D2A_IE(hiv, lov)
        if n.role in (A2D, BIDIR, RA2D):
            n.a2d = A2D_IE(loth, hith)


def plan_for(ana, nodes, levels=None):
    fill_levels(nodes, levels)
    plan = AmsPlan(ana, nodes)
    plan.bridges = names.build_bridges(plan)
    return plan


# -- variants and port binding ---------------------------------------------------------

class TestVariants(unittest.TestCase):
    def test_variants_by_provenance(self):
        ana = analyse("ports")
        self.assertEqual(sorted(ana.variants), sorted([
            "rw__c86a", "rw2__7ed3", "buffer_module__f8f0", "block_module__f8f0",
            "register_module__f8f0", "my_cell__f8f0", "cell_module__f8f0", "module_cell__f8f0"]))
        self.assertEqual([ci.cell for ci in ana.instances],
                         ["rw", "rw2", "buffer", "block", "cell_", "_cell", "my__cell", "register"])
        self.assertEqual(ana.variants["rw__c86a"].vhdl_ports,
                         ["in_sig", "out_sig", "signal_sig", "bus_sig", "open_sig", "sig_a", "b_sig",
                          "c_d", "inv_sig"])
        self.assertEqual(ana.variants["rw2__7ed3"].vhdl_ports, ["OUT_sig", "In_sig"])

    def test_paths_and_walk_order(self):
        ana = analyse("shared")
        self.assertEqual([(ci.vpath, ci.path_name, ci.labels) for ci in ana.instances],
                         [("tb.u1", ":tb:u1:", ["u1"]), ("tb.w1.u3", ":tb:w1:u3:", ["w1", "u3"]),
                          ("tb.w2.u3", ":tb:w2:u3:", ["w2", "u3"])])
        self.assertEqual({ci.variant for ci in ana.instances}, {"rc_sp__f8f0"})
        self.assertIn("translator patch T3", " ".join(messages(ana.notes, "warning")))

    def test_t3_verilog_paths(self):
        ana = analyse("vec", name="design_t3.vhd")
        self.assertEqual([ci.vpath for ci in ana.instances][:4],
                         ["tb.ca[0]", "tb.ca[1]", "tb.g[0].u", "tb.g[1].u"])
        self.assertNotIn("translator patch T3", " ".join(messages(ana.notes)))

    def test_parameterised_variants(self):
        ana = analyse("dac")
        self.assertEqual(ana.variants["flash__24a6"].ranges, [None, (3, 0)])
        self.assertEqual(ana.variants["flash1__8c78"].ranges, [None, (7, 0)])
        self.assertEqual(ana.variants["flash1__8c78"].vhdl_vector, [False, True])
        self.assertEqual(ana.instances[1].params, {"N": "8"})
        self.assertEqual(cut.param_overrides(ana.cells["flash"], ana.instances[1]), {"N": ("4", "8")})
        self.assertEqual(cut.param_overrides(ana.cells["flash"], ana.instances[0]), {})
        # the tied net split pio_sp into two variants; the tie is a kept statement
        self.assertEqual({ci.variant for ci in ana.instances[2:]}, {"pio_sp__efe1", "pio_sp1__01a1"})
        self.assertTrue(any("pio_sp__efe1" in m and "kept as a digital driver" in m
                            for m in messages(ana.notes, "note")))

    def test_spice_map(self):
        ana = analyse("dac")
        self.assertEqual(ana.instances[0].spice, {(0, 0): "clk", (1, 3): "q[3]", (1, 2): "q[2]",
                                                  (1, 1): "q[1]", (1, 0): "q[0]"})

    def test_port_binding_checks(self):
        def errs(cells):
            with self.assertRaises(NoteError) as cm:
                analyse("shared", cells=cells)
            return " ".join(n.message for n in cm.exception.notes)
        self.assertIn("port order", errs([cell("rc_sp", [("y", LOGIC, OUTPUT), ("a", LOGIC, INPUT)])]))
        self.assertIn("bits wide", errs([cell("rc_sp", [("a", LOGIC, INPUT, (1, 0)),
                                                        ("y", LOGIC, OUTPUT)])]))
        self.assertIn("has mode in", errs([cell("rc_sp", [("a", LOGIC, INOUT),
                                                          ("y", LOGIC, OUTPUT)])]))
        self.assertIn("has type logic3d", errs([cell("rc_sp", [("a", REAL, INPUT),
                                                               ("y", LOGIC, OUTPUT)])]))
        self.assertIn("ports, the cut cell has", errs([cell("rc_sp", [("a", LOGIC, INPUT)])]))

    def test_missing_marker(self):
        text = parse("shared").text
        text = re.sub(r"(?ms)^  sv_bufif1_vamos_ams_hiz_0_0_0_inst:.*?\);\n", "", text)
        d = vhdl.parse_text(text, fixture("vhdl", "cut_shared", "design.vhd"))
        with self.assertRaises(NoteError) as cm:
            analyse("shared", design=d)
        self.assertIn("no marker on bit(s) 0", cm.exception.notes[0].message)

    def test_cell_without_instance(self):
        cells = CELLS["shared"][0] + [cell("nowhere", [("a", LOGIC, INPUT)]),
                                      cell("multi_unused", [("a", LOGIC, INPUT)], view="multi")]
        with self.assertRaises(NoteError) as cm:
            analyse("shared", cells=cells)
        self.assertEqual([n.message for n in cm.exception.notes],
                         ["cell nowhere has no instance under top tb"])


# -- parameter values and overrides (§4.7: cut.param_overrides) ---------------------------

def _with_params(text, lines_by_entity):
    """design.vhd text with more "--   P = v" lines above the named entities (and their
    architectures), the way tgt-vhdl prints a real or string parameter."""
    def add(m):
        ent = m.group(3) or m.group(4)
        return m.group(1) + "".join("--   %s\n" % x for x in lines_by_entity.get(ent, ())) + m.group(2)
    return re.sub(r"(-- Generated from Verilog module \w+ \([^)]*\)\n(?:--   .*\n)*)"
                  r"(entity (\w+) is|architecture \w+ of (\w+) is)", add, text)


def _flash_cells(params):
    return [cell("flash", [("clk", LOGIC, INPUT), ("q", LOGIC, OUTPUT, "[N-1:0]")], view="multi",
                 params=params),
            cell("pio_sp", [("pad", LOGIC, AUTO, None, INOUT), ("y", LOGIC, OUTPUT)])]


def _inst(params, **private):
    """A CutInstance with the given printed values (and private attributes, by name)."""
    ci = CutInstance(labels=["u"], vpath="tb.u", path_name=":tb:u:", cell="c", variant="c__0",
                     subckt="c", params=dict(params))
    for k, v in private.items():
        setattr(ci, "_param_" + k, v)
    return ci


MODS_RUNS = """\
-- This VHDL was converted from Verilog using the
-- Icarus Verilog VHDL Code Generator 13.0 (devel) (6029f3dfb)
library ieee;
-- Generated from Verilog module flash (x/_norm.sv:22)
--   N = 8
--   GAIN = 0.25
entity flash1__8c78 is
  port (clk : in logic3d);
end entity;
-- Generated from Verilog module flash (x/_norm.sv:22)
--   N = 8
--   GAIN = 0.25
architecture from_verilog of flash1__8c78 is
begin
end architecture;
-- Generated from Verilog module wrap (x/_norm.sv:30)
entity wrap is
end entity;
-- This VHDL was converted from Verilog using the
-- Generated from Verilog module flash (x/_norm.sv:22)
--   N = 4
--   GAIN = 0.25
entity flash is
end entity;
-- This VHDL was converted from Verilog using the
-- Generated from Verilog module flash (x/_norm.sv:22)
--   N = 4
--   GAIN = 0.25
entity flash__24a6 is
end entity;
-- Generated from Verilog module flash (x/_norm.sv:22)
--   N = 8
--   GAIN = 0.9
entity flash1__8c78 is
end entity;
-- Generated from Verilog module tb (x/_norm.sv:5)
entity tb is
end entity;
"""


class TestConstants(unittest.TestCase):
    def test_veval_stays_integer(self):
        self.assertEqual(cut.veval("8'hff"), 255)
        self.assertEqual(cut.veval("$clog2(9)"), 4)
        self.assertEqual(cut.veval("N-1", {"N": 8}), 7)
        self.assertEqual(cut.veval("7/2"), 3)
        for text in ("1.5", "1e3", '"a"', "$ln(1)", "N"):
            self.assertIsNone(cut.veval(text, {"N": 2.5}), text)

    def test_vvalue(self):
        cases = {"1.0/4": 0.25, "1/4": 0, "2.0**-1": 0.5, "$ln(1)": 0.0, "1e-9": 1e-9,
                 "1_000.5": 1000.5, "$rtoi(2.7)": 2, "$rtoi(-2.7)": -2, "$itor(3)": 3.0,
                 "-0.5": -0.5, "$pow(2, 0.5)": 2 ** 0.5, "$floor(2.5)": 2.0, '"fast"': "fast",
                 '"fast" == "fast"': 1, '"a" != "b"': 1, '"a\\"b"': 'a"b', "$clog2(8)": 3,
                 "N > 2 ? 1.5 : 2.5": 1.5, "VREF / (1 << N)": 0.0625, "8'd5 + 0.5": 5.5}
        for text, want in cases.items():
            got = cut.vvalue(text, {"N": 4, "VREF": 1.0})
            self.assertEqual((got, type(got)), (want, type(want)), text)
        for text in ("2**-1", "$sqrt(-1)", "$ln(0)", '"a" + 1', "~1.5", "3 % 2.0", "1.0 / 0",
                     "1 << 0.5", "$nosuch(1)", '"a" ? 1 : 2', "1.5 === 1.5"):
            self.assertIsNone(cut.vvalue(text), text)

    def test_printed_value(self):
        self.assertEqual(cut.printed_value("8"), 8)
        self.assertEqual(cut.printed_value("8", real=True), 8.0)
        self.assertEqual(cut.printed_value("1e-09"), 1e-09)
        self.assertEqual(cut.printed_value('"slow"'), "slow")
        self.assertIsNone(cut.printed_value('"cut'))


class TestParamOverrides(TempDir):
    def test_real_and_string_overrides(self):
        """A real or string override shows only in the "--   P = v" lines (§4.7)."""
        text = _with_params(parse("dac").text, {
            "flash": ['GAIN = 0.25', 'MODE = "fast"'],
            "flash__24a6": ['GAIN = 0.25', 'MODE = "fast"'],
            "flash1__8c78": ['GAIN = 0.9', 'MODE = "slow"']})
        d = vhdl.parse_text(text, fixture("vhdl", "cut_dac", "design.vhd"))
        cells = _flash_cells({"N": "4", "GAIN": "0.25", "MODE": '"fast"'})
        ana = analyse("dac", cells=cells, design=d)
        self.assertEqual(ana.instances[1].params, {"N": "8", "GAIN": "0.9", "MODE": '"slow"'})
        self.assertEqual(cut.param_overrides(cells[0], ana.instances[1]),
                         {"N": ("4", "8"), "GAIN": ("0.25", "0.9"), "MODE": ('"fast"', '"slow"')})
        self.assertEqual(cut.param_overrides(cells[0], ana.instances[0]), {})

    def test_default_from_the_cells_own_entity(self):
        """The cell's own entity (its module at the defaults) gives a default the header's
        text cannot (an unknown function); without it that default counts as differing."""
        text = _with_params(parse("dac").text, {"flash": ['GAIN = 0.25'],
                                                "flash__24a6": ['GAIN = 0.25'],
                                                "flash1__8c78": ['GAIN = 0.25']})
        d = vhdl.parse_text(text, fixture("vhdl", "cut_dac", "design.vhd"))
        cells = _flash_cells({"N": "4", "GAIN": "$myfunc(1)"})
        ana = analyse("dac", cells=cells, design=d)
        self.assertEqual(ana.instances[0]._param_defaults, {"N": "4", "GAIN": "0.25"})
        self.assertEqual(cut.param_overrides(cells[0], ana.instances[0]), {})
        self.assertEqual(cut.param_overrides(cells[0], ana.instances[1]), {"N": ("4", "8")})
        ana.instances[0]._param_defaults = None
        self.assertEqual(cut.param_overrides(cells[0], ana.instances[0]),
                         {"GAIN": ("$myfunc(1)", "0.25")})

    def test_values_from_the_tops_run(self):
        """design.vhd keeps one of two same-named variants (here another run's, GAIN = 0.25);
        the top's run in _mods.vhd gives the elaborated GAIN = 0.9."""
        text = _with_params(parse("dac").text, {"flash": ['GAIN = 0.25'],
                                                "flash__24a6": ['GAIN = 0.25'],
                                                "flash1__8c78": ['GAIN = 0.25']})
        self.write("design.vhd", text)
        with open(fixture("vhdl", "cut_dac", "_norm.sv")) as fh:
            self.write("_norm.sv", fh.read())
        cells = _flash_cells({"N": "4", "GAIN": "0.25"})

        def overrides():
            d = vhdl.parse(os.path.join(self.tmp, "design.vhd"))
            ana = analyse("dac", cells=cells, design=d)
            return [cut.param_overrides(cells[0], ci) for ci in ana.instances[:2]]

        self.assertEqual(overrides(), [{}, {"N": ("4", "8")}])        # no _mods.vhd
        self.write("_mods.vhd", MODS_RUNS)
        self.write("_metadata.tmp", "SV2VHDL_MODULES=1\n")
        self.assertEqual(overrides(), [{}, {"N": ("4", "8"), "GAIN": ("0.25", "0.9")}])
        # design.vhd from iverilog-sv2ghdl's whole-design run: _mods.vhd is not its source
        self.write("_metadata.tmp", "IVERILOG_BACKEND=1\n")
        self.assertEqual(overrides(), [{}, {"N": ("4", "8")}])

    def test_by_value(self):
        po = cut.param_overrides
        c = cell("c", [("a", LOGIC, INPUT)], view="multi",
                 params={"G": "0.25", "TD": "1e-9", "MODE": '"fast"', "OFF": "-1", "P": '"ab"',
                         "Q": "1.0/4"})
        same = {"G": "0.25", "TD": "1e-09", "MODE": '"fast"', "OFF": "4294967295",
                "P": str(0x6162), "Q": "0.25"}
        self.assertEqual(po(c, _inst(same)), {})
        for p, v in (("G", "0.9"), ("TD", "2e-09"), ("MODE", '"slow"'), ("OFF", "255"),
                     ("P", "1"), ("Q", "0.3")):
            self.assertEqual(po(c, _inst(dict(same, **{p: v}))), {p: (c.params[p], v)}, p)
        # 6 significant digits: an override that changes only later digits is not seen
        self.assertEqual(po(c, _inst(dict(same, G="0.25"))), {})
        # a default that does not evaluate counts as differing, unless a printed one is given
        u = cell("u", [("a", LOGIC, INPUT)], view="multi", params={"G": "$myfunc(1)"})
        self.assertEqual(po(u, _inst({"G": "0.25"})), {"G": ("$myfunc(1)", "0.25")})
        self.assertEqual(po(u, _inst({"G": "0.25"}, defaults={"G": "0.25"})), {})

    def test_derived_values(self):
        """A value that differs only through the parameters it is computed from is no
        override (a localparam, or a parameter left at its default expression)."""
        po = cut.param_overrides
        c = cell("c", [("a", LOGIC, INPUT, "[N-1:0]")], view="multi",
                 params={"N": "2", "VREF": "1.0", "LSB": "VREF / (1 << N)", "STEP": "1.0 / N",
                         "B": "STEP * 3"})
        vals = {"N": "4", "VREF": "1", "LSB": "0.0625", "STEP": "0.25", "B": "0.75"}
        typed = {"N": 4, "VREF": 1.0, "LSB": 0.0625, "STEP": 0.25, "B": 0.75}
        self.assertEqual(po(c, _inst(vals, values=typed)), {"N": ("2", "4")})
        self.assertEqual(po(c, _inst(vals)), {"N": ("2", "4")})      # typed from the text alone
        self.assertEqual(po(c, _inst(dict(vals, LSB="0.5"))),
                         {"N": ("2", "4"), "LSB": ("VREF / (1 << N)", "0.5")})
        self.assertEqual(po(c, _inst(dict(vals, LSB="0.5"), locals={"LSB"})), {"N": ("2", "4")})
        # computed from printed (rounded) values: within 2e-5
        vals6 = {"N": "6", "VREF": "1", "LSB": "0.015625", "STEP": "0.166667", "B": "0.5"}
        self.assertEqual(po(c, _inst(vals6)), {"N": ("2", "6")})
        # an override of the parameter it is computed from is reported, not the result
        self.assertEqual(po(c, _inst(dict(vals, VREF="2", LSB="0.125"))),
                         {"N": ("2", "4"), "VREF": ("1.0", "2")})


# -- nets, drivers and roles ------------------------------------------------------------

class TestNets(unittest.TestCase):
    def test_shared_parent(self):
        ana = analyse("shared")
        nodes = by_canonical(roles(ana))
        self.assertEqual({k: (n.role, n.host.inst if n.host else None) for k, n in nodes.items()},
                         {"tb.u1.a": (D2A, 0), "tb.u1.y": (A2D, 0),
                          "tb.w1.u3.a": (D2A, 1), "tb.w1.u3.y": (A2D, 1)})
        self.assertEqual(nodes["tb.u1.a"].aliases, ["tb.w2.u3.a", "tb.clk", "tb.w2.a"])
        self.assertEqual(nodes["tb.u1.y"].aliases, ["tb.w2.u3.y", "tb.yb", "tb.w2.y"])
        self.assertEqual(len(nodes["tb.u1.y"].ports), 2)
        self.assertIn("variable tb.clk joins SPICE ports in analog; VCS digitises it: tb.u1.a, "
                      "tb.w2.u3.a share one analog node here, where VCS gives each SPICE port an "
                      "interface element of its own; declare tb.clk a wire if the analog connection is "
                      "intended, or connect each SPICE port to a net of its own (wire w = clk;) to get "
                      "VCS's digital connection", messages(ana.notes, "warning"))

    def test_declarations_from_pp(self):
        class FakePP:
            """The two verilog_ports.PP queries the cut uses."""
            def __init__(self, variables, tri):
                self.variables, self.tri = variables, tri

            def is_variable(self, module, name):
                return (module, name) in self.variables

            def tri_kind(self, module, name):
                return self.tri.get((module, name))

        c, s = CELLS["shared"]
        d = parse("shared")
        ana = cut.analyse(d, "tb", c, netlist(s), AmsConfig(), RuleHits(), pp=FakePP(set(), {}))
        roles(ana)
        self.assertFalse(any(n.variable for n in ana.nets))
        self.assertNotIn("joins SPICE ports", " ".join(messages(ana.notes)))
        with self.assertRaises(NoteError) as cm:
            cut.analyse(d, "tb", c, netlist(s), AmsConfig(), RuleHits(),
                        pp=FakePP({("tb", "clk")}, {("tb", "yb"): "tri1"}))
        self.assertEqual([n.message for n in cm.exception.notes],
                         ["tri1 net tb.yb reaches a SPICE port; its pull needs translator patch T5"])

    def test_readable_shapes(self):
        nodes = by_canonical(roles(analyse("readable")))
        # bandgap -> ADC inside one module, exported: one node, no IE
        self.assertEqual(nodes["tb.bg_inst.u1.vref"].role, THROUGH)
        self.assertEqual(len(nodes["tb.bg_inst.u1.vref"].ports), 2)
        # (e) cell -> wrapper output -> second cell: one node, no IE
        self.assertEqual(nodes["tb.ue.a"].role, THROUGH)
        self.assertIn("tb.we.u.vo", nodes["tb.ue.a"].aliases)
        # (a) different formal names, (b) equal formal names: one A2D each
        self.assertEqual((nodes["tb.wa.u1.vo"].role, len(nodes["tb.wa.u1.vo"].ports)), (A2D, 2))
        self.assertEqual((nodes["tb.wb.u1.vo"].role, len(nodes["tb.wb.u1.vo"].ports)), (A2D, 2))
        # (c) wrapper inout port
        self.assertEqual(nodes["tb.wc.u.pad"].role, A2D)
        self.assertEqual(nodes["tb.wc.u.pad"].aliases, ["tb.wc.p", "tb.vc"])

    def test_port_temporaries(self):
        ana = analyse("temps")
        nodes = by_canonical(roles(ana))
        self.assertEqual(cut.port5(ana, nodes["tb.c5.y[1]"].ports[0]),
                         ("cout2", "tb.c5", "y[1]", "y[1]", "y"))
        self.assertEqual(nodes["tb.c1.d[0]"].role, POWERNET)       # .d({x, gnd}), supply0 gnd
        self.assertEqual(nodes["tb.c1.d[1]"].role, D2A)
        self.assertEqual(nodes["tb.c2.a"].role, POWERNET)          # .a(vdd), supply1
        self.assertEqual(nodes["tb.c3.y"].role, THROUGH)           # cell output -> v[0] -> cell
        self.assertEqual(nodes["tb.c3.y"].aliases, ["tb.c4.a", "tb.v", "tb.v[0]"])
        self.assertEqual(nodes["tb.c5.y[0]"].aliases, ["tb.c5.y", "tb.c6.a", "tb.w", "tb.w[4]"])
        self.assertEqual(nodes["tb.c5.y[0]"].role, THROUGH)
        supply = [n for n in ana.nets if n.key == nodes["tb.c1.d[0]"].net][0]
        self.assertEqual([d.strength for d in supply.drivers], [SUPPLY0])

    def test_vector_outputs(self):
        nodes = roles(analyse("vec", name="design_t3.vhd"))
        got = {n.canonical: n.role for n in nodes}
        for c in ("tb.ca[0].y", "tb.ca[1].y", "tb.g[0].u.y", "tb.g[1].u.y", "tb.t0.y", "tb.t1.y",
                  "tb.m0.y"):
            self.assertEqual(got[c], A2D, c)
        bc = by_canonical(nodes)
        self.assertIn("tb.gy[1]", bc["tb.g[1].u.y"].aliases)
        self.assertIn("tb.mix[0]", bc["tb.m0.y"].aliases)
        # {code[0], code[1]} through the port-expression temporaries: one D2A per bit
        self.assertEqual(bc["tb.ub.d[1]"].role, D2A)
        self.assertIn("tb.code[0]", bc["tb.ub.d[1]"].aliases)
        self.assertIn("tb.code[3]", bc["tb.up.d[1]"].aliases)     # code[3:2] part-select

    def test_primitive_drivers(self):
        ana = analyse("prims")
        nets = {n.aliases[0]: n for n in ana.nets}
        strength = {k: [d.strength for d in n.drivers] for k, n in nets.items()}
        self.assertEqual(strength["tb.c1.a"], [STRONG])            # nand
        self.assertEqual(strength["tb.c2.a"], [PULL_UP])           # pullup
        self.assertEqual(strength["tb.c4.a"], [WEAK])              # assign (weak1, weak0)
        self.assertEqual(strength["tb.c6.a"], [PULL_UP])           # pullup (supply1): a pull
        self.assertEqual(strength["tb.c8.a"], [])                  # undriven wire: Z only
        self.assertEqual(strength["tb.s1.vdd"], [SUPPLY1])
        self.assertEqual(strength["tb.s1.vss"], [SUPPLY0])
        self.assertTrue(nets["tb.c2.a"].drivers[0].removable)
        nodes = by_canonical(roles(ana))
        self.assertEqual(nodes["tb.c2.a"].role, D2A)               # pulls stay digital on a D2A
        self.assertEqual(nodes["tb.c7.a"].role, D2A)               # initial-block deposits
        self.assertEqual(nodes["tb.c8.a"].role, NONE)
        self.assertEqual(nodes["tb.s1.vb[1]"].role, POWERNET)       # supply1 [1:0]
        self.assertIn("tb.vbus[1]", nodes["tb.s1.vb[1]"].aliases)

    def test_initial_values_drive(self):
        nodes = by_canonical(roles(analyse("ports")))
        self.assertEqual(nodes["tb.u1.signal"].role, D2A)          # reg a_signal = 1'b1, no assignment
        self.assertEqual([n.role for n in nodes.values() if "tb.w_out" in n.aliases], [A2D])
        self.assertIn("tb.w_OUT", nodes["tb.u2.OUT"].aliases)      # case-only clash undone

    def test_real_ports(self):
        nodes = by_canonical(roles(analyse("real")))
        self.assertEqual(nodes["tb.a1.vin"].role, RD2A)
        self.assertEqual(nodes["tb.a1.vout"].role, RA2D)

    def test_bidir_pull_moves(self):
        nodes = roles(analyse("bidir"))
        self.assertEqual([(n.role, n.pull) for n in nodes], [(BIDIR, "up")])

    def test_slice_alias(self):
        # T2-style alias of a part-select: c5.y[1:0] on w[5:4] without the LPM copy
        text = parse("temps").text
        text = text.replace("  signal LPM_d0_ivl_9 : logic3d_vector(1 downto 0) := (others => L3D_X);\n",
                            "  alias LPM_d0_ivl_9 is w(5 downto 4);\n")
        text = re.sub(r"(?ms)^  process \(all\) is\s*begin\s*w\(4 \+ 1 downto 4\) <= LPM_d0_ivl_9;"
                      r"\s*end process;\n", "", text)
        d = vhdl.parse_text(text, fixture("vhdl", "cut_temps", "design.vhd"))
        self.assertEqual(d.arch("tb").aliases["lpm_d0_ivl_9"].target.indices, (5, 4))
        nodes = by_canonical(roles(analyse("temps", design=d)))
        self.assertIn("tb.w[4]", nodes["tb.c5.y[0]"].aliases)
        self.assertIn("tb.c6.a", nodes["tb.c5.y[0]"].aliases)
        self.assertIn("tb.w[5]", nodes["tb.c5.y[1]"].aliases)

    def test_undriven_net_warning(self):
        # a digitally read net whose only driver is the undriven-net constant, in an
        # architecture with cut instances: what a dropped cut output looks like
        text = parse("vec").text.replace(
            "  signal arr : logic3d_vector(1 downto 0)",
            "  signal lost : logic3d_vector(1 downto 0) := (others => L3D_X);  "
            "-- Declared at /tmp/vamos_fx/vec/nvc/_norm.sv:8\n"
            "  signal arr : logic3d_vector(1 downto 0)", 1)
        text = text.replace("begin\n  process (all) is",
                            "begin\n  process (all) is begin lost <= (others => L3D_Z); end process;\n"
                            "  process (lost) is begin null; end process;\n  process (all) is", 1)
        self.assertIn("lost <= (others => L3D_Z)", text)
        ana = analyse("vec", design=vhdl.parse_text(text, fixture("vhdl", "cut_vec", "design.vhd")))
        self.assertIn("undriven-net constant", " ".join(messages(ana.notes, "warning")))

    def test_constant_and_open_actuals(self):
        # cin c1 (.a(1'b1)) comes out as 'a => L3D_1'; an output left open has no actual
        text = parse("force").text.replace("    port map (\n      a => n\n    );",
                                           "    port map (\n      a => L3D_1\n    );", 1)
        self.assertIn("a => L3D_1", text)
        ana = analyse("force", design=vhdl.parse_text(text, fixture("vhdl", "cut_force",
                                                                    "design.vhd")))
        nodes = roles(ana)
        self.assertEqual([(n.canonical, n.role) for n in nodes], [("tb.c1.a", D2A)])
        self.assertIn("(a => L3D_1)", ana.nets[0].drivers[0].origin)
        text = parse("shared").text.replace(
            "      a => clk2,\n      y => y3\n", "      a => clk2,\n      y => open\n", 1)
        ana = analyse("shared", design=vhdl.parse_text(text, fixture("vhdl", "cut_shared",
                                                                     "design.vhd")))
        nodes = by_canonical(roles(ana))
        self.assertEqual(nodes["tb.w1.u3.y"].role, THROUGH)        # a lone drivable bit
        self.assertEqual(nodes["tb.w1.u3.y"].aliases, ["tb.w1.y"])

    def test_auto_port_directions(self):
        nodes = by_canonical(roles(analyse("dac")))
        self.assertEqual(nodes["tb.u1.pad"].role, D2A)             # tie folded into the variant
        self.assertTrue(any("auto→inout" in r for r in nodes["tb.u1.pad"].report))


class TestPerBitDrivers(unittest.TestCase):
    """§5.4 step 4 bit by bit: a driver that can only produce Z is not a driver, per bit
    (Z elements of a constant, Z-only translator temporaries in a concatenation, Z bits of a
    constant port actual), and an initial value drives each bit nothing else drives."""

    # cut_swvp/design_t2.vhd: pads on pbus(0) ring[0].u, pbus(1) ring[1].u, pbus(2) u2 (T2
    # aliases, declared inout), qbus(1) u9; pbus and qbus are read by a $display process
    TRI = ("  process (all) is\n\n  begin\n    if is_one(oe) then\n      pbus <= dout;\n"
           "    else\n      pbus <= tmp_ivl_2;\n    end if;\n  end process;\n")
    TEMP = "  signal %s : logic3d := L3D_X;  -- Temporary created at /tmp/vamos_fx/swvp/nvc/_norm.sv:10\n"

    def swvp(self, procs, temps=()):
        text = parse("swvp", "design_t2.vhd").text
        self.assertIn(self.TRI, text)
        text = text.replace(self.TRI, "".join("  process (all) is\n  begin\n%s  end process;\n" % p
                                              for p in procs), 1)
        anchor = "  signal oe : logic3d := L3D_0;"
        text = text.replace(anchor, "".join(self.TEMP % t for t in temps) + anchor, 1)
        d = vhdl.parse_text(text, fixture("vhdl", "cut_swvp", "design_t2.vhd"))
        ana = analyse("swvp", design=d)
        return ana, by_canonical(roles(ana))

    def pads(self, nodes):
        return {k: nodes[k].role for k in ("tb.ring[0].u.pad", "tb.ring[1].u.pad", "tb.u2.pad",
                                           "tb.u9.pad")}

    def test_z_only_temporary_in_a_concatenation(self):
        # BUG-28-CONCAT: tgt-vhdl merges `assign pbus[3] = ..; assign pbus[2] = ..;` into one
        # process whose undriven bits copy temporaries that are only ever assigned L3D_Z
        ana, nodes = self.swvp(["    pbus <= tmp_ivl_90 & dout(2) & tmp_ivl_91 & tmp_ivl_92;\n",
                                "    tmp_ivl_90 <= dout(3);\n",
                                "    tmp_ivl_91 <= L3D_Z;\n",
                                "    tmp_ivl_92 := tmp_ivl_91;\n"],      # a copy of a Z-only bit
                               ["tmp_ivl_90", "tmp_ivl_91", "tmp_ivl_92"])
        self.assertEqual(self.pads(nodes), {"tb.ring[0].u.pad": A2D, "tb.ring[1].u.pad": A2D,
                                            "tb.u2.pad": BIDIR, "tb.u9.pad": A2D})
        for k in ("tb.ring[0].u.pad", "tb.ring[1].u.pad"):
            self.assertEqual([n for n in ana.nets if n.key == nodes[k].net][0].drivers, [], k)

    def test_maybe_z_temporaries_still_drive(self):
        # not provably Z from t=0: a conditional Z (X until it runs), a delayed Z, a copy cycle
        _, nodes = self.swvp(["    pbus <= tmp_ivl_90 & tmp_ivl_93 & tmp_ivl_91 & tmp_ivl_92;\n",
                              "    tmp_ivl_90 <= dout(3);\n",
                              "    if is_one(oe) then\n      tmp_ivl_91 <= L3D_Z;\n    end if;\n",
                              "    tmp_ivl_92 <= L3D_Z after 1 ns;\n",
                              "    tmp_ivl_93 <= tmp_ivl_94;\n", "    tmp_ivl_94 <= tmp_ivl_93;\n"],
                             ["tmp_ivl_%d" % k for k in range(90, 95)])
        self.assertEqual(self.pads(nodes), {"tb.ring[0].u.pad": BIDIR, "tb.ring[1].u.pad": BIDIR,
                                            "tb.u2.pad": BIDIR, "tb.u9.pad": A2D})

    def test_z_elements_of_a_constant(self):
        # BUG-Z-PERBIT / item 13 auto ports: pbus <= logic3d_vector'(L3D_H, L3D_1, L3D_Z, L3D_0ZX)
        ana, nodes = self.swvp(["    pbus <= logic3d_vector'(L3D_H, L3D_1, L3D_Z, L3D_0ZX);\n"])
        self.assertEqual(self.pads(nodes), {"tb.ring[0].u.pad": A2D, "tb.ring[1].u.pad": A2D,
                                            "tb.u2.pad": BIDIR, "tb.u9.pad": A2D})
        u2 = [n for n in ana.nets if n.key == nodes["tb.u2.pad"].net][0]
        self.assertEqual([d.strength for d in u2.drivers], [STRONG])
        # the weak element lands on its own bit: a weak driver on a BIDIR net (not in v1)
        with self.assertRaises(NoteError) as cm:
            self.swvp(["    pbus <= logic3d_vector'(L3D_1, L3D_H, L3D_Z, L3D_Z);\n"])
        self.assertIn("weak driver(s)", cm.exception.notes[0].message)
        self.assertIn("tb.u2.pad", cm.exception.notes[0].message)

    def vec(self, *edits):
        text = parse("vec").text
        for old, new in edits:
            self.assertIn(old, text)
            text = text.replace(old, new, 1)
        ana = analyse("vec", design=vhdl.parse_text(text, fixture("vhdl", "cut_vec", "design.vhd")))
        return ana, by_canonical(roles(ana))

    def test_z_bit_of_a_constant_actual(self):
        # cin2 up (.d(2'bz1)): d[1] is tied to Z (no IE), d[0] to 1 (a D2A)
        ana, nodes = self.vec(("      d => LPM_q_ivl_21\n", "      d => logic3d_vector'(L3D_Z, L3D_1)\n"))
        self.assertEqual((nodes["tb.up.d[1]"].role, nodes["tb.up.d[0]"].role), (NONE, D2A))
        self.assertIn("unconnected bit tb.up.d[1] (private node)", nodes["tb.up.d[1]"].report)
        d0 = [n for n in ana.nets if n.key == nodes["tb.up.d[0]"].net][0]
        self.assertEqual([d.strength for d in d0.drivers], [STRONG])

    def test_initial_value_per_bit(self):
        # BUG-28-INIT: reg [3:0] code = 4'b0x(H)1 with only code[3] and code[1] assigned later
        # (code[1] only to Z): each bit's own literal drives it unless something else does
        decl = "logic3d_vector'(L3D_0, L3D_1, L3D_0, L3D_1)"
        ana, nodes = self.vec(
            (decl, "logic3d_vector'(L3D_0, L3D_X, L3D_H, L3D_1)"),
            ("  comb_fused_1: process (code) is",
             "  process is\n  begin\n    wait for 5000 ps;\n    code(3) <= L3D_1;\n"
             "    code(1) <= L3D_Z;\n    wait;\n  end process;\n\n  comb_fused_1: process (code) is"))
        # up.d = code[3:2]; ub.d = {code[0], code[1]}
        self.assertEqual({k: nodes[k].role for k in ("tb.up.d[1]", "tb.up.d[0]", "tb.ub.d[1]",
                                                     "tb.ub.d[0]")},
                         {"tb.up.d[1]": D2A, "tb.up.d[0]": NONE, "tb.ub.d[1]": D2A,
                          "tb.ub.d[0]": D2A})
        drv = {k: [(d.strength, "initial value" in d.origin) for d in
                   [n for n in ana.nets if n.key == nodes[k].net][0].drivers]
               for k in ("tb.up.d[1]", "tb.up.d[0]", "tb.ub.d[1]", "tb.ub.d[0]")}
        self.assertEqual(drv, {"tb.up.d[1]": [(STRONG, False)],    # the process (holds the init)
                               "tb.up.d[0]": [],                   # code[2] = x: no driver
                               "tb.ub.d[1]": [(STRONG, True)],     # code[0] = 1, never assigned
                               "tb.ub.d[0]": [(WEAK, True)]})      # H until code[1] = z

    def test_initial_value_with_no_other_driver_unchanged(self):
        # the plain fixture: code is never assigned, every bit is driven by its initial value
        ana, nodes = self.vec()
        for k in ("tb.ub.d[1]", "tb.ub.d[0]", "tb.up.d[1]", "tb.up.d[0]"):
            self.assertEqual(nodes[k].role, D2A, k)


class TestErrors(unittest.TestCase):
    def raises(self, *a, **kw):
        with self.assertRaises(NoteError) as cm:
            ana = analyse(*a, **kw)
            roles(ana)
        return [n.message for n in cm.exception.notes]

    def test_force(self):
        self.assertEqual(self.raises("force"),
                         ["force/release on mixed-signal net tb.n is not supported"])

    def test_tri_nets_need_t5(self):
        self.assertEqual(self.raises("tri"), [
            "tri1 net tb.t1 reaches a SPICE port; its pull needs translator patch T5",
            "tri0 net tb.t0 reaches a SPICE port; its pull needs translator patch T5",
            "tri1 net tb.t1d reaches a SPICE port; its pull needs translator patch T5"])

    def test_inout_through_temporary(self):
        msgs = self.raises("swvp")
        self.assertEqual(len(msgs), 4)
        self.assertIn("translator temporary SW_ivl_0_b", msgs[0])

    def test_t2_aliases_join(self):
        nodes = by_canonical(roles(analyse("swvp", name="design_t2.vhd")))
        self.assertEqual(nodes["tb.ring[0].u.pad"].role, BIDIR)
        self.assertIn("tb.pbus[0]", nodes["tb.ring[0].u.pad"].aliases)
        self.assertIn("tb.pbus[2]", nodes["tb.u2.pad"].aliases)
        self.assertEqual(nodes["tb.u9.pad"].role, A2D)

    def test_pull_needed_on_one_path_only(self):
        msgs = self.raises("pullshare")
        self.assertEqual(len(msgs), 1)
        self.assertIn("needed digitally on tb.w2", msgs[0])
        # declared inout: BIDIR on both paths, the pull moves on both
        nodes = roles(analyse("pullshare", cells=[cell("pio", [("pad", LOGIC, INOUT)])]))
        self.assertEqual([(n.role, n.pull) for n in nodes], [(BIDIR, "up"), (BIDIR, "up")])

    def test_output_and_strong_driver(self):
        text = parse("shared").text.replace(
            "  -- Generated from instantiation at /tmp/vamos_fx/shared/nvc/_norm.sv:11",
            "  process (all) is\n  begin\n    yb <= clk2;\n  end process;\n"
            "  -- Generated from instantiation at /tmp/vamos_fx/shared/nvc/_norm.sv:11", 1)
        d = vhdl.parse_text(text, fixture("vhdl", "cut_shared", "design.vhd"))
        msgs = self.raises("shared", design=d)
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].startswith("SPICE output tb.u1.y and a digital driver (tb:line"),
                        msgs[0])


# -- port buffers (one-way joins) -----------------------------------------------------------

def portbuf_text(drop=()):
    """cut_portbuf/design.vhd without the tb instances in `drop` and their port buffer copies."""
    text = parse("portbuf").text
    for lab in drop:
        text, n = re.subn(r"(?ms)^  process \(all\) is\s*begin\s*PB_%s_a <= [^;]*;\s*end process;\n"
                          % lab, "", text)
        assert n == 1, lab
        text, n = re.subn(r"(?ms)^  -- Generated from instantiation at [^\n]*\n  -- Verilog instance: "
                          r"%s\n  %s: entity [^\n]*\n    port map \(.*?\);\n" % (lab, lab), "", text)
        assert n == 1, lab
    return text


def analyse_portbuf(drop=(), cells=None, directions=None):
    """The portbuf fixture without `drop`; the cells whose only instance went go too."""
    gone = {"wo": "srco", "wv": "srcv"}
    c = [x for x in (cells or CELLS["portbuf"][0]) if x.name not in {gone.get(d) for d in drop}]
    d = vhdl.parse_text(portbuf_text(drop), fixture("vhdl", "cut_portbuf", "design.vhd"))
    return cut.analyse(d, "tb", c, netlist(CELLS["portbuf"][1]), AmsConfig(), RuleHits(),
                       directions=directions)


class TestPortBuffers(unittest.TestCase):
    """A cut port declared inout or output on a wrapper input port that its instance connects
    to a variable or an expression: the iverilog core buffers the port, and tgt-vhdl draws the
    buffer in the parent as `PB_<label>_<port> <= <actual>` (cut_portbuf: tb.v names the
    shapes).  In Verilog the buffer is one-way, so the cut port can drive only the wrapper's
    side W of the port: W joins the actual's net with its cut ports acting as inputs, exactly
    as port_dir input would make it, unless the inside of the wrapper also drives W (a net of
    its own) or something could see what the cell drives on W (an error naming port_dir input).
    """

    CLEAN = ("wo", "wr")

    def node_of(self, nodes, bit):
        got = [n for n in nodes if any(cut._bit_name(self.ana, p) == bit for p in n.ports)]
        self.assertEqual(len(got), 1, "%s: %s" % (bit, [n.canonical for n in nodes]))
        return got[0]

    def net_of(self, node):
        return [x for x in self.ana.nets if x.key == node.net][0]

    def joined(self, cells=None):
        self.ana = analyse_portbuf(self.CLEAN, cells)
        return roles(self.ana)

    def test_variable_actual_joins_one_way(self):
        nodes = self.joined()
        n = self.node_of(nodes, "tb.we.u.a")
        # one node with the variable's own SPICE input: the rule on tb.clk reaches the cell
        self.assertEqual((n.canonical, n.role), ("tb.c1.a", D2A))
        self.assertEqual(n.aliases, ["tb.we.u.a", "tb.clk", "tb.we.a"])
        self.assertEqual([cut._bit_name(self.ana, p) for p in n.ports], ["tb.c1.a", "tb.we.u.a"])
        # the copy is a wire now, neither a driver nor a reader; clk's process drives the node
        drv = self.net_of(n).drivers
        self.assertEqual(len(drv), 1, drv)
        self.assertIn("_norm.sv:13", drv[0].origin)                # always #10 clk = ~clk
        self.assertIn("direction: inout→input (one-way port buffer: input port tb.we.a fed from "
                      "variable tb.clk) tb.we.u.a", n.report)
        self.assertIn("variable tb.clk joins SPICE ports in analog; VCS digitises it: tb.c1.a, "
                      "tb.we.u.a share one analog node here, where VCS gives each SPICE port an "
                      "interface element of its own; declare tb.clk a wire if the analog connection is "
                      "intended, or connect each SPICE port to a net of its own (wire w = clk;) to get "
                      "VCS's digital connection", messages(self.ana.notes, "warning"))

    def test_selects_and_vectors(self):
        nodes = self.joined()
        # .a(rv[1]) through tgt-vhdl's tmp_ivl_1 <= rv(1), and bit 1 of .a(rv): one node
        n = self.node_of(nodes, "tb.wb.u.a")
        self.assertIs(n, self.node_of(nodes, "tb.wv.u.a[1]"))
        self.assertEqual(n.role, D2A)
        self.assertIn("tb.rv[1]", n.aliases)
        self.assertIn("direction: inout→input (one-way port buffer: input port tb.wb.a fed from "
                      "variable tb.rv[1]) tb.wb.u.a", n.report)
        self.assertIn("direction: inout→input (one-way port buffer: input port tb.wv.a[1] fed "
                      "from variable tb.rv[1]) tb.wv.u.a[1]", n.report)
        n0 = self.node_of(nodes, "tb.wv.u.a[0]")
        self.assertEqual(n0.role, D2A)
        self.assertEqual(cut._bit_name(self.ana, n0.host), "tb.wv.u.a[0]")   # the cell hosts it
        self.assertIn("tb.rv[0]", n0.aliases)

    def test_expression_actual(self):
        # .a(~clk): the buffer copies tgt-vhdl's tmp_ivl_2 <= l3d_not(clk); W joins it
        n = self.node_of(self.joined(), "tb.wx.u.a")
        self.assertEqual((n.canonical, n.role, n.aliases), ("tb.wx.u.a", D2A, ["tb.wx.a"]))
        self.assertIn("direction: inout→input (one-way port buffer: input port tb.wx.a fed from "
                      "an expression) tb.wx.u.a", n.report)
        drv = self.net_of(n).drivers
        self.assertEqual(len(drv), 1, drv)

    def test_driven_inside_is_its_own_net(self):
        # bufif1 b (a, 1'b0, en) inside wrap_d: W is a net of its own, driven by the copy too
        n = self.node_of(self.joined(), "tb.wd.u.a")
        self.assertEqual((n.canonical, n.role, n.aliases), ("tb.wd.u.a", BIDIR, ["tb.wd.a"]))
        self.assertEqual(len(self.net_of(n).drivers), 2)
        self.assertIn("port buffer: input port tb.wd.a, fed one way from variable tb.clk, is also "
                      "driven inside tb.wd (a net of its own)", n.report)
        self.assertNotIn(n.ports[0], self.ana._cut.one_way)

    def test_no_one_way_port_drives(self):
        nodes = self.joined()
        st = self.ana._cut
        self.assertEqual(sorted(cut._bit_name(self.ana, p) for p in st.one_way),
                         ["tb.wb.u.a", "tb.we.u.a", "tb.wv.u.a[0]", "tb.wv.u.a[1]", "tb.wx.u.a"])
        for n in nodes:
            if n.role in (A2D, BIDIR):
                self.assertNotIn(n.host, st.one_way, n.canonical)
        # every cell output still has its A2D
        self.assertEqual(self.node_of(nodes, "tb.we.u.vo").role, A2D)

    def test_auto_inout_port(self):
        # an auto port the probe left inout (step 2b is structural) is joined the same way
        cells = [c for c in CELLS["portbuf"][0] if c.name != "srci"] + [
            cell("srci", [("a", LOGIC, AUTO, None, INOUT), ("vo", LOGIC, OUTPUT)])]
        n = self.node_of(self.joined(cells), "tb.we.u.a")
        self.assertEqual((n.canonical, n.role), ("tb.c1.a", D2A))
        self.assertIn("direction: auto→inout→input (one-way port buffer: input port tb.we.a fed "
                      "from variable tb.clk) tb.we.u.a", n.report)

    def errors(self, drop=(), cells=None):
        with self.assertRaises(NoteError) as cm:
            roles(analyse_portbuf(drop, cells))
        return [(n.origin, n.message) for n in cm.exception.notes]

    def test_output_behind_a_buffer(self):
        self.assertEqual(self.errors(("wr",)), [(
            "tb.wo.u", "port a of tb.wo.u is an output behind input port tb.wo.a, fed one way from "
            "variable tb.clk (a port buffer): the cell could drive only the wrapper's side of that "
            "port, against the buffer; declare it input (port_dir -cell srco (input a;)), or connect "
            "a net to tb.wo.a")])
        # a multi-view cell has no port_dir: its Verilog view declares the direction
        cells = [c for c in CELLS["portbuf"][0] if c.name != "srco"] + [
            cell("srco", [("a", LOGIC, OUTPUT), ("vo", LOGIC, OUTPUT)], view="multi")]
        msg = self.errors(("wr",), cells)[0][1]
        self.assertTrue(msg.endswith("declare it input (in the Verilog view of srco), or connect a "
                                     "net to tb.wo.a"), msg)

    def test_inout_read_inside_the_wrapper(self):
        # assign q = ~a in wrap_r: the cell's drive would be seen there, on W only
        self.assertEqual(self.errors(("wo",)), [(
            "tb.wr.u", "port a of tb.wr.u is inout behind input port tb.wr.a, fed one way from "
            "variable tb.clk (a port buffer), and tb.wr reads that port: what the cell drives there "
            "would reach only the wrapper's side, which vamos does not model; declare it input "
            "(port_dir -cell srci (input a;)), or connect a net to tb.wr.a")])

    def test_both_errors_at_once(self):
        msgs = sorted(m for _, m in self.errors())
        self.assertEqual(len(msgs), 2, msgs)
        self.assertTrue(msgs[0].startswith("port a of tb.wo.u is an output"), msgs)
        self.assertTrue(msgs[1].startswith("port a of tb.wr.u is inout"), msgs)

    def test_constant_actual_drives_the_inside(self):
        # .a(1'b1): the buffer is a constant, not a copy: it drives W, which is not joined
        old = "    PB_wx_a <= tmp_ivl_2;\n"
        text = portbuf_text(self.CLEAN)
        self.assertIn(old, text)
        d = vhdl.parse_text(text.replace(old, "    PB_wx_a <= L3D_1;\n"),
                            fixture("vhdl", "cut_portbuf", "design.vhd"))
        cells = [c for c in CELLS["portbuf"][0] if c.name != "srco"]
        self.ana = cut.analyse(d, "tb", cells, netlist(CELLS["portbuf"][1]), AmsConfig(), RuleHits())
        n = self.node_of(roles(self.ana), "tb.wx.u.a")
        self.assertEqual((n.role, n.aliases), (BIDIR, ["tb.wx.a"]))   # declared inout, driven
        self.assertNotIn(n.ports[0], self.ana._cut.one_way)
        self.assertEqual([dv.strength for dv in self.net_of(n).drivers], [STRONG])


class TestProbeDirections(unittest.TestCase):
    """IE-report direction lines of auto ports: the direction probe's step-2b reason (an input
    port the wrapper's instance drives from a variable) comes from shells.ShellResult.directions."""

    WHY = ("wrap_e.u (tb.sv:13), connected to input port wrap_e.a, which tb.we (tb.sv:7) connects "
           "to variable clk")

    def lines(self, directions, via="analyse"):
        """The direction lines of tb.we.u.a (src.a auto -> input), with `directions` passed
        to cut.analyse or to cut.assign_roles."""
        cells = [c for c in CELLS["readable"][0] if c.name != "src"] + [
            cell("src", [("a", LOGIC, AUTO, None, INPUT), ("vo", LOGIC, OUTPUT)])]
        args = (parse("readable"), "tb", cells, netlist(CELLS["readable"][1]), AmsConfig(), RuleHits())
        if via == "analyse":
            nodes = roles(cut.analyse(*args, directions=directions))
        else:
            nodes = cut.assign_roles(cut.analyse(*args), names.NameAllocator(), lambda n: False,
                                     lambda n: (False, None), directions=directions)
        return [r for n in nodes for r in n.report if r.startswith("direction:")
                and r.endswith(" tb.we.u.a")]

    def test_step_2b_reason(self):
        want = ["direction: auto→input (%s) tb.we.u.a" % self.WHY]
        dirs = {"src": {"a": "auto->input (%s)" % self.WHY}}
        self.assertEqual(self.lines(dirs), want)
        self.assertEqual(self.lines(dirs, via="assign_roles"), want)

    def test_other_reasons_keep_variable_actual(self):
        want = ["direction: auto→input (variable actual) tb.we.u.a"]
        self.assertEqual(self.lines(None), want)
        self.assertEqual(self.lines({}), want)
        self.assertEqual(self.lines({"src": {"a": "auto->input (tb.u1 (tb.sv:10), connected to r; "
                                                  "r is assigned procedurally at tb.sv:3)"}}), want)
        # a reason for another mode than the port's is not used
        self.assertEqual(self.lines({"src": {"a": "auto->output (%s)" % self.WHY}}), want)


@needs_stack
class TestPortBufferElaborate(TempDir):
    """The joined port buffers' cut.vhd analyses and elaborates (a cell behind a buffer hosts
    a D2A: its clone reads the buffer)."""

    def test_elaborate(self):
        nvc, libdir = _nvc()
        src = self.write("design.vhd", portbuf_text(TestPortBuffers.CLEAN))
        base = [nvc, "--std=2040", "--work=work:" + os.path.join(self.tmp, "work"), "-L", libdir,
                "-M", "2g", "-H", "1g"]
        r = run(base + ["-a", src], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout)
        ana = analyse_portbuf(TestPortBuffers.CLEAN)
        nodes = roles(ana)
        res = cut.emit(plan_for(ana, nodes), os.path.join(self.tmp, "ams"))
        text = self.read(os.path.join("ams", "cut.vhd"))
        self.assertIn("PB_we_a <= clk;", text)                  # the copy stays, digitally
        for args in (["-a", res.vhdl_path], ["-e", "tb"]):
            r = run(base + args, cwd=self.tmp)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertNotIn("should be reanalysed", r.stdout)


class TestRules(unittest.TestCase):
    def test_remove_d2a(self):
        ana = analyse("shared")
        nodes = by_canonical(roles(ana, removal=lambda n: (n[0] in ("tb.u1.a", "tb.u1.y"), 0.9)))
        self.assertEqual((nodes["tb.u1.a"].role, nodes["tb.u1.a"].dc, nodes["tb.u1.a"].host),
                         (REMOVED, 0.9, None))
        self.assertEqual(nodes["tb.u1.y"].role, A2D)
        self.assertIn("remove_d2a: no D2A on tb.u1.y (A2D)", messages(ana.notes, "note"))
        with self.assertRaises(NoteError):
            roles(analyse("bidir"), removal=lambda n: (True, None))

    def test_disable_ie_and_names(self):
        seen = []

        def disabled(n):
            seen.append(list(n))
            return n[0] == "tb.w1.u3.y"
        ana = analyse("shared")
        nodes = by_canonical(roles(ana, disabled=disabled))
        self.assertEqual((nodes["tb.w1.u3.y"].role, nodes["tb.w1.u3.y"].host), (DISABLED, None))
        self.assertIn(["tb.w1.u3.y", "tb.w1.y", "tb.y3"], seen)   # canonical first, then aliases
        self.assertTrue(any("disable_ie" in m for m in messages(ana.notes, "warning")))

    def test_use_spice_inst(self):
        cfg = AmsConfig(use_spice=[
            UseSpice(cells=[("flash", "")], origin="vcsAD.init:1"),
            UseSpice(cells=[("flash", "flash8")], insts=["tb.f8"],
                     port_map=[("*", "snps_by_position")], origin="vcsAD.init:2")])
        subs = CELLS["dac"][1] + [subckt("flash8", ["c"] + ["d%d" % i for i in range(8)])]
        hits = RuleHits()
        ana = analyse("dac", subs=subs, cfg=cfg, hits=hits)
        self.assertTrue(hits.hit("use_spice_inst#1.0"))
        self.assertEqual(ana.instances[1].subckt, "flash8")
        self.assertEqual(ana.instances[1].spice[(1, 7)], "d0")      # by position: q[7] first
        self.assertEqual(ana.instances[0].subckt, "flash")
        # an -inst statement that leaves a multi-view instance uncovered
        cfg2 = AmsConfig(use_spice=[UseSpice(cells=[("flash", "")], insts=["tb.f8"])])
        with self.assertRaises(NoteError) as cm:
            analyse("dac", cfg=cfg2)
        self.assertIn("do not cover instance tb.f4", cm.exception.notes[0].message)

    def test_two_inst_statements(self):
        cfg = AmsConfig(use_spice=[
            UseSpice(cells=[("flash", "flash4")], insts=["tb.f4"],
                     port_map=[("*", "snps_by_position")], origin="vcsAD.init:1"),
            UseSpice(cells=[("flash", "")], insts=["tb.f8"],
                     port_map=[("clk", "ck"), ("*", "snps_by_name")], origin="vcsAD.init:2")])
        subs = [subckt("flash", ["ck"] + ["q[%d]" % i for i in range(7, -1, -1)]),
                subckt("flash4", ["c", "o3", "o2", "o1", "o0"]), subckt("pio_sp", ["pad", "y"])]
        hits = RuleHits()
        ana = analyse("dac", subs=subs, cfg=cfg, hits=hits)
        f4, f8 = ana.instances[0], ana.instances[1]
        self.assertEqual((f4.subckt, f4.spice[(0, 0)], f4.spice[(1, 3)]), ("flash4", "c", "o3"))
        self.assertEqual((f8.subckt, f8.spice[(0, 0)], f8.spice[(1, 7)]), ("flash", "ck", "q[7]"))
        self.assertTrue(hits.hit("use_spice_inst#0.0") and hits.hit("use_spice_inst#1.0"))

    def test_ground_port_by_position(self):
        # the Verilog view lists vdd first; by position it lands on the subckt's ground port
        cells = [CELLS["prims"][0][0],
                 cell("sup", [("vdd", LOGIC, INPUT), ("vss", LOGIC, INPUT), ("vb", LOGIC, INPUT, (1, 0))])]
        cells[1].portmap.default = "snps_by_position"
        subs = [subckt("cin", ["a"]), subckt("sup", ["gnd", "x", "p1", "p0"], gnd=(0,))]
        ana = analyse("prims", cells=cells, subs=subs)
        nodes = by_canonical(roles(ana))
        self.assertNotIn("tb.s1.gnd", nodes)
        self.assertEqual(nodes["tb.s1.x"].role, POWERNET)          # vss (supply0) on x
        vdd = [n for n in ana.nets if "tb.vdd" in n.aliases][0]
        self.assertEqual((vdd.ports, len(vdd.passive)), ([], 1))
        self.assertTrue(any("ground or port_connect'ed SPICE ports only" in m
                            for m in messages(ana.notes, "note")))

    def test_port_connect_passive(self):
        cfg = AmsConfig(port_connects=[PortConnect("sup", conns=[("vss", "gnd", False)])])
        nodes = by_canonical(roles(analyse("prims", cfg=cfg)))
        self.assertNotIn("tb.s1.vss", nodes)
        ana = analyse("prims", cfg=cfg)
        net = [n for n in ana.nets if any(a == "tb.vss" for a in n.aliases)][0]
        self.assertEqual((net.ports, len(net.passive)), ([], 1))


# -- emit ---------------------------------------------------------------------------------

class TestEmit(TempDir):
    def test_shared(self):
        ana = analyse("shared")
        nodes = roles(ana)
        plan = plan_for(ana, nodes, {"tb.w1.u3.a": (3.3, 0.0, 0.6, 1.2),
                                     "tb.w1.u3.y": (1.8, 0.0, 1.1, 2.2)})
        res = cut.emit(plan, self.tmp)
        self.assertEqual(res.clones, {"rc_sp__f8f0": "rc_sp__f8f0__vams"})
        self.assertEqual(res.repointed, ["tb:u1", "wrap__7aff:u3"])
        text = self.read("cut.vhd")
        self.assertLess(text.index("package vams_cut_pkg"), text.index("entity rc_sp__f8f0__vams"))
        self.assertLess(text.index("entity rc_sp__f8f0__vams"), text.index("architecture from_verilog of tb"))
        self.assertIn('(":tb:u1:   ", ":tb:w1:u3:", ":tb:w2:u3:")', text)
        self.assertIn("VAMS_ROLES_0 : vams_roles_0_t := ((1, 2), (1, 2), (0, 0));", text)
        self.assertIn("3.3", text)
        self.assertIn("u1: entity work.rc_sp__f8f0__vams", text)
        self.assertIn("u3: entity work.rc_sp__f8f0__vams", text)
        self.assertIn("signal vb1_0_a : real := real'low;", text)
        self.assertIn("vams_z1_0: if vams_r1_0 /= ROLE_A2D and vams_r1_0 /= ROLE_BIDIR generate", text)
        self.assertNotIn("Generated from Verilog module rc_sp", text.split("package body")[1]
                         .split("entity rc_sp__f8f0__vams")[0])
        self.assertEqual(self.read("vamos.boundary").splitlines(), [
            "D2A .u1.vb0_0_d tb.u1.a__d rise=1e-11 fall=1e-11",
            "D2A .u1.vb0_0_e tb.u1.a__e rise=1e-11 fall=1e-11",
            "A2D .u1.vb1_0_a tb.u1.y__a",
            "D2A .w1.u3.vb0_0_d tb.w1.u3.a__d rise=1e-11 fall=1e-11",
            "D2A .w1.u3.vb0_0_e tb.w1.u3.a__e rise=1e-11 fall=1e-11",
            "A2D .w1.u3.vb1_0_a tb.w1.u3.y__a"])
        self.assertEqual(self.read("vamos.boundary").splitlines(),
                         [names.boundary_line(b) for b in plan.bridges])
        self.assertEqual(cut.port5(ana, nodes[2].host),
                         ("rc_sp", "tb.w1.u3", "a", "a", "a"))

    def test_bidir_pull_removed(self):
        ana = analyse("bidir")
        res = cut.emit(plan_for(ana, roles(ana)), self.tmp)
        text = self.read("cut.vhd")
        self.assertIn("-- vamos: pull moved into the analog deck (BIDIR):\n  -- sv_pullup_ivl_4", text)
        self.assertNotIn("\n  sv_pullup_ivl_4_0_0_inst:", text)
        self.assertEqual(res.repointed, ["tb:u1"])
        lv = re.search(r"VAMS_LVLS_0 : vams_lvls_0_t := \((.*?)\);$", text, re.S | re.M).group(1)
        self.assertIn("1.8, 0.0, 0.0, 1.2, 0.6, 0.0, -1.0, 2.0, 0.0, 0.0, 0.14304896863036398, "
                      "1.8, 0.14304896863036398", lv)

    def test_real_and_vector_slots(self):
        ana = analyse("real")
        cut.emit(plan_for(ana, roles(ana)), self.tmp)
        text = self.read("cut.vhd")
        self.assertIn("vb0_0_d <= vin;", text)
        self.assertIn("vout <= 0.0 when vb1_0_a < -1.0e300 else vb1_0_a;", text)
        ana = analyse("dac")
        cut.emit(plan_for(ana, roles(ana)), self.tmp)
        text = self.read("cut.vhd")
        self.assertIn("q(7) <= vams_drive(vams_st);", text)
        self.assertIn("  -- kept from pio_sp__efe1", text)
        self.assertIn("pad <= L3D_1;", text)

    def test_levels_required(self):
        ana = analyse("shared")
        nodes = roles(ana)
        plan = AmsPlan(ana, nodes)
        plan.bridges = names.build_bridges(plan)
        with self.assertRaises(NoteError):
            cut.emit(plan, self.tmp)

    def test_d2a_x2v4(self):
        """x2v=4 (PAMS p206): hiv after a 0, lov after a 1, otherwise the previous voltage."""
        ana = analyse("shared")
        cut.emit(plan_for(ana, roles(ana)), self.tmp)
        text = self.read("cut.vhd")
        self.assertIn("      variable vams_prev : natural := 2;\n", text)
        self.assertIn("        elsif VAMS_X2V = 4 then\n", text)
        self.assertIn("          if vams_prev = 0 then\n            vams_nv := VAMS_HIV;\n"
                      "          elsif vams_prev = 1 then\n            vams_nv := VAMS_LOV;\n"
                      "          else\n            vams_nv := vams_last;\n", text)
        self.assertIn("        vams_ne := VAMS_PULL_E;\n        vams_prev := 2;\n", text)
        self.assertNotIn("vams_last > (", text)

    def test_slot_names_hide_no_port(self):
        """A slot's own names carry the vams_ prefix and are reserved: a cell port named v
        is the port in the D2A process (it was read as the process's variable v, and the
        node stayed at lov); a port named vams_v is refused."""
        cells = [cell("rc_sp", [("v", LOGIC, INPUT), ("y", LOGIC, OUTPUT)])]
        subs = [subckt("rc_sp", ["v", "y"])]
        d = vhdl.parse_text(_rename_cell_port(parse("shared").text, "v"),
                            fixture("vhdl", "cut_shared", "design.vhd"))
        ana = analyse("shared", cells=cells, subs=subs, design=d)
        cut.emit(plan_for(ana, roles(ana)), self.tmp)
        text = self.read("cut.vhd")
        self.assertIn("    process (v)\n", text)
        self.assertIn("      vams_v := v;\n", text)
        cells = [cell("rc_sp", [("vams_v", LOGIC, INPUT), ("y", LOGIC, OUTPUT)])]
        subs = [subckt("rc_sp", ["vams_v", "y"])]
        d = vhdl.parse_text(_rename_cell_port(parse("shared").text, "vams_v"),
                            fixture("vhdl", "cut_shared", "design.vhd"))
        ana = analyse("shared", cells=cells, subs=subs, design=d)
        with self.assertRaises(NoteError) as cm:
            cut.emit(plan_for(ana, roles(ana)), self.tmp)
        self.assertIn("port or signal vams_v of rc_sp__f8f0 collides with a generated cut name",
                      cm.exception.notes[0].message)

    def test_many_instances(self):
        n = 600
        d = vhdl.parse_text(_big_design(n), "/nonexistent/design.vhd")
        t0 = time.time()
        ana = analyse("shared", design=d)
        nodes = roles(ana)
        res = cut.emit(plan_for(ana, nodes), self.tmp)
        self.assertLess(time.time() - t0, 30.0)
        self.assertEqual(len(ana.instances), n)
        self.assertEqual(sum(1 for x in nodes if x.role == A2D), n)
        text = self.read("cut.vhd")
        paths = re.search(r"VAMS_PATHS_0 : vams_paths_0_t := \((.*?)\);", text, re.S).group(1)
        lst = [p.strip().strip('"').rstrip() for p in paths.split(",")]
        self.assertEqual(lst, sorted(lst))
        self.assertEqual(len(res.repointed), n)


def _rename_cell_port(text, new):
    """The shared fixture with rc_sp's port a renamed (its entities and its instances)."""
    for e in ("rc_sp", "rc_sp__f8f0"):
        text = re.sub(r"(entity %s is\n  port \(\n    )a :" % e, r"\g<1>%s :" % new, text)
    return re.sub(r"(entity work\.rc_sp__f8f0\n    port map \(\n      )a =>", r"\g<1>%s =>" % new, text)


def _big_design(n):
    """tb with n rc_sp instances on one clock and n output bits (synthetic tgt-vhdl text)."""
    with open(fixture("vhdl", "cut_shared", "design.vhd")) as fh:
        head = fh.read()
    cellpart = head[:head.index("-- Generated from Verilog module wrap")]
    body = ["-- Generated from Verilog module tb (x/_norm.sv:5)", "entity tb is", "end entity;",
            "", "-- Generated from Verilog module tb (x/_norm.sv:5)", "architecture from_verilog of tb is",
            "  signal clk : logic3d := L3D_0;  -- Declared at x/_norm.sv:6",
            "  signal y : resolved_logic3d_vector(%d downto 0) := (others => L3D_X);  "
            "-- Declared at x/_norm.sv:8" % (n - 1), "begin"]
    for i in range(n):
        body += ["  u%d: entity work.rc_sp__f8f0" % i, "    port map (", "      a => clk,",
                 "      y => y(%d)" % i, "    );"]
    body += ["  process (y) is", "  begin", "    null;", "  end process;", "end architecture;", ""]
    return cellpart + "library ieee;\n" + "\n".join(body)


# -- nvc: analyse and elaborate the emitted VHDL ---------------------------------------------

def _nvc():
    nvc = tools.find_real("nvc")
    return nvc, tools.nvc_libdir(nvc)


@needs_stack
class TestElaborate(TempDir):
    """cut.vhd analyses after design.vhd into one work library and elaborates."""

    def elaborate(self, case, name="design.vhd", cells=None, nodes_fn=None):
        nvc, libdir = _nvc()
        work = os.path.join(self.tmp, "work")
        src = fixture("vhdl", "cut_" + case, name)
        base = [nvc, "--std=2040", "--work=work:" + work, "-L", libdir, "-M", "2g", "-H", "1g"]
        r = run(base + ["-a", src], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout)
        ana = analyse(case, name=name, cells=cells)
        nodes = roles(ana)
        cut.emit(plan_for(ana, nodes), os.path.join(self.tmp, "ams"))
        r = run(base + ["-a", os.path.join(self.tmp, "ams", "cut.vhd")], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("should be reanalysed", r.stdout)
        r = run(base + ["-e", "tb"], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("should be reanalysed", r.stdout)

    def test_shared(self):
        self.elaborate("shared")

    def test_bidir(self):
        self.elaborate("bidir")

    def test_readable(self):
        self.elaborate("readable")

    def test_dac_variants(self):
        self.elaborate("dac")

    def test_real(self):
        self.elaborate("real")

    def test_vectors_t3(self):
        self.elaborate("vec", "design_t3.vhd")

    def test_prims_temps(self):
        self.elaborate("prims")
        self.elaborate("temps")

    def test_ports(self):
        self.elaborate("ports")

    def test_t2_aliases(self):
        self.elaborate("swvp", "design_t2.vhd")

    def test_pull_moved_from_shared_wrapper(self):
        self.elaborate("pullshare", cells=[cell("pio", [("pad", LOGIC, INOUT)])])

    def test_a2d_window(self):
        """The A2D template on w1.u3.y, driven by forcing its bridge signal (no engine).

        HITH 1.2, LOTH 0.6, XBAND 4 (hith_hys 1.05, loth_hys 0.75), midv 5 ns -> X.
        """
        steps = [("1.5", 1, "1"), ("1.1", 8, "1"), ("0.9", 3, "1"), (None, 3, "X"),
                 ("0.5", 1, "0"), ("0.7", 8, "0"), ("0.8", 6, "X"), ("1.3", 1, "1")]
        body = ["  vams_test: process",
                "    alias va is <<signal .tb.w1.u3.vb1_0_a : real>>;",
                "  begin",
                "    wait for 1 ns;",
                "    report \"step 0 y3=\" & to_char(y3);"]
        for k, (v, ns, _) in enumerate(steps, 1):
            if v is not None:
                body.append("    va <= force %s;" % v)
            body += ["    wait for %d ns;" % ns, "    report \"step %d y3=\" & to_char(y3);" % k]
        body += ["    std.env.finish;", "  end process;", ""]
        text = parse("shared").text
        j = text.index("end architecture;", text.index("architecture from_verilog of tb is"))
        text = text[:j] + "\n".join(body) + text[j:]
        src = os.path.join(self.tmp, "design.vhd")
        with open(src, "w") as fh:
            fh.write(text)
        ana = analyse("shared", design=vhdl.parse_text(text, src))
        nodes = roles(ana)
        plan = plan_for(ana, nodes)
        for n in nodes:
            if n.canonical == "tb.w1.u3.y":
                n.a2d = A2D_IE(0.6, 1.2, xband=4.0, midv_time=5e-9, midv_logic="X")
        res = cut.emit(plan, os.path.join(self.tmp, "ams"))
        nvc, libdir = _nvc()
        base = [nvc, "--std=2040", "--work=work:" + os.path.join(self.tmp, "work"), "-L", libdir]
        for args in (["-a", src], ["-a", res.vhdl_path], ["-e", "tb"]):
            r = run(base + args, cwd=self.tmp)
            self.assertEqual(r.returncode, 0, r.stdout)
        r = run(base + ["-r", "--stop-time=60ns", "tb"], cwd=self.tmp)
        got = re.findall(r"step (\d+) y3=(\w)", r.stdout)
        self.assertEqual(got, [("0", "X")] + [(str(k), want) for k, (_, _, want)
                                              in enumerate(steps, 1)], r.stdout[-3000:])

    # (clk2 -> w1.u3.a with x2v=4, clk -> u1.a with x2v=3; then d, e of each D2A)
    X2V_STEPS = [
        ("X", "X", 0.0, 1.0, 0.0, 1.0),       # start-up X: the previous voltage, lov
        ("0", "1", 0.0, 1.0, 1.8, 1.0),
        ("X", "X", 1.8, 1.0, 1.8, 1.0),       # x2v=4: X after a 0 -> hiv
        ("1", "0", 1.8, 1.0, 0.0, 1.0),
        ("X", "X", 0.0, 1.0, 0.0, 1.0),       # x2v=4: X after a 1 -> lov
        ("1", "1", 1.8, 1.0, 1.8, 1.0),
        ("Z", "Z", 1.8, 0.0, 1.8, 0.0),       # Z: released, the voltage held
        ("X", "X", 1.8, 1.0, 1.8, 1.0),       # X after Z: the previous voltage
        ("0", "0", 0.0, 1.0, 0.0, 1.0),
        ("Z", "Z", 0.0, 0.0, 0.0, 0.0),
        ("X", "X", 0.0, 1.0, 0.0, 1.0),       # X after Z
        ("L", "H", 0.0, "WF", 1.8, "WF"),     # weak 0 / weak 1
        ("X", "X", 1.8, 1.0, 1.8, 1.0),       # x2v=4: X after L (a 0) -> hiv
        ("H", "W", 1.8, "WF", 1.8, "WF"),     # W (weak X) on x2v=3: held, weak
        ("W", "X", 0.0, "WF", 1.8, 1.0),      # x2v=4: W after H (a 1) -> lov, weak
        ("U", "U", 0.0, 1.0, 1.8, 1.0),       # U after W: the previous voltage
    ]

    def test_d2a_x2v(self):
        """The D2A template's X rules, driven from tb with no engine: x2v=4 (PAMS p206) is
        hiv after a 0, lov after a 1, otherwise (after Z or X, and at start-up) the
        previous voltage; x2v=3 is always the previous voltage.  Run again with the cell
        port named v, which the D2A process used to read as its own variable."""
        wf = 500.7 / 3500.2
        for port in ("a", "v"):
            with self.subTest(port=port):
                got = self._x2v_run(port)
                want = [tuple(wf if x == "WF" else x for x in row[2:]) for row in self.X2V_STEPS]
                self.assertEqual(len(got), len(want), got)
                for k, (g, w) in enumerate(zip(got, want)):
                    for gx, wx in zip(g, w):
                        self.assertAlmostEqual(gx, wx, delta=1e-9,
                                               msg="step %d (%s): got %s, want %s"
                                               % (k, self.X2V_STEPS[k][:2], g, w))

    def _x2v_run(self, port):
        work = os.path.join(self.tmp, "work_" + port)
        text = parse("shared").text
        if port != "a":
            text = _rename_cell_port(text, port)
        # tb's clocks become X at start-up, driven by the stimulus below instead
        text = re.sub(r"  -- Generated from always process in tb \([^)]*_norm\.sv:(?:9|10)\)\n"
                      r"  process is\n(?:.*\n)*?  end process;\n", "", text)
        for s in ("clk", "clk2"):
            text = text.replace("signal %s : logic3d := L3D_0;" % s, "signal %s : logic3d := L3D_X;" % s)
        stim = ["  vams_stim: process", "  begin"]
        for c2, c1 in [row[:2] for row in self.X2V_STEPS[1:]]:
            stim += ["    wait for 5 ns;", "    clk2 <= L3D_%s;" % c2, "    clk <= L3D_%s;" % c1]
        stim += ["    wait;", "  end process;",
                 "  vams_probe: process",
                 "    alias d4 is <<signal .tb.w1.u3.vb0_0_d : real>>;",
                 "    alias e4 is <<signal .tb.w1.u3.vb0_0_e : real>>;",
                 "    alias d3 is <<signal .tb.u1.vb0_0_d : real>>;",
                 "    alias e3 is <<signal .tb.u1.vb0_0_e : real>>;",
                 "  begin",
                 "    wait for 2 ns;",
                 "    for i in 0 to %d loop" % (len(self.X2V_STEPS) - 1),
                 "      report \"step \" & integer'image(i) & \" \" & real'image(d4) & \" \" & "
                 "real'image(e4) & \" \" & real'image(d3) & \" \" & real'image(e3);",
                 "      wait for 5 ns;",
                 "    end loop;",
                 "    std.env.finish;",
                 "  end process;", ""]
        j = text.index("end architecture;", text.index("architecture from_verilog of tb is"))
        text = text[:j] + "\n".join(stim) + text[j:]
        src = os.path.join(self.tmp, "design_%s.vhd" % port)
        with open(src, "w") as fh:
            fh.write(text)
        cells = [cell("rc_sp", [(port, LOGIC, INPUT), ("y", LOGIC, OUTPUT)])]
        subs = [subckt("rc_sp", [port, "y"])]
        ana = analyse("shared", cells=cells, subs=subs, design=vhdl.parse_text(text, src))
        nodes = roles(ana)
        plan = plan_for(ana, nodes)
        for n in nodes:
            if n.role == D2A:
                n.d2a.x2v = 4 if ana.instances[n.host.inst].vpath == "tb.w1.u3" else 3
        self.assertEqual(sorted(n.d2a.x2v for n in nodes if n.role == D2A), [3, 4])
        res = cut.emit(plan, os.path.join(self.tmp, "ams_" + port))
        nvc, libdir = _nvc()
        base = [nvc, "--std=2040", "--work=work:" + work, "-L", libdir]
        for args in (["-a", src], ["-a", res.vhdl_path], ["-e", "tb"]):
            r = run(base + args, cwd=self.tmp)
            self.assertEqual(r.returncode, 0, r.stdout)
        r = run(base + ["-r", "--stop-time=100ns", "tb"], cwd=self.tmp)
        rows = re.findall(r"step (\d+) (\S+) (\S+) (\S+) (\S+)", r.stdout)
        self.assertTrue(rows, r.stdout[-3000:])
        return [tuple(float(x) for x in row[1:]) for row in rows]


# -- co-simulation (nvc + VACASK) ---------------------------------------------------------------

def _raw(path):
    """(names, columns) of a binary or ASCII SPICE rawfile (real data)."""
    with open(path, "rb") as fh:
        data = fh.read()
    k = data.find(b"Binary:\n")
    binary = k >= 0
    if not binary:
        k = data.find(b"Values:\n")
    head = data[:k].decode("latin1").splitlines()
    nvars = int([x for x in head if x.startswith("No. Variables:")][0].split(":")[1])
    npts = int([x for x in head if x.startswith("No. Points:")][0].split(":")[1])
    vi = [i for i, x in enumerate(head) if x.startswith("Variables:")][0]
    names_ = [head[vi + 1 + j].split()[1] for j in range(nvars)]
    body = data[k + 8:]
    cols = {nm: [] for nm in names_}
    if binary:
        vals = struct.unpack("<%dd" % (npts * nvars), body[:npts * nvars * 8])
        for p in range(npts):
            for j, nm in enumerate(names_):
                cols[nm].append(vals[p * nvars + j])
    else:
        toks = body.decode().split()
        i = 0
        for p in range(npts):
            i += 1
            for nm in names_:
                cols[nm].append(float(toks[i]))
                i += 1
    return cols


def _at(cols, name, t):
    ts = cols["time"]
    for i in range(1, len(ts)):
        if ts[i] >= t:
            t0, t1 = ts[i - 1], ts[i]
            v0, v1 = cols[name][i - 1], cols[name][i]
            return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return cols[name][-1]


@needs_stack
@needs_vacask
class TestCosim(TempDir):
    """Real co-simulations: the emitted VHDL and boundary file against hand-written decks."""

    def cosim(self, case, levels=None, stop="95ns"):
        nvc, libdir = _nvc()
        if not os.path.isfile(engines.bridge_lib(libdir)):
            self.skipTest("no libcosim_bridge.so next to nvc")
        ov = openvaf_bin()
        if not ov:
            self.skipTest("needs openvaf-r")
        work = os.path.join(self.tmp, "work")
        base = [nvc, "--std=2040", "--work=work:" + work, "-L", libdir, "-M", "2g", "-H", "1g"]
        r = run(base + ["-a", fixture("vhdl", "cut_" + case, "design.vhd")], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout)
        ana = analyse(case)
        nodes = roles(ana)
        plan = plan_for(ana, nodes, levels)
        res = cut.emit(plan, os.path.join(self.tmp, "ams"))
        r = run(base + ["-a", res.vhdl_path], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout)
        r = run(base + ["-e", "tb"], cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout)
        rundir = os.path.join(self.tmp, "run")
        os.makedirs(rundir)
        shutil.copy(fixture("vhdl", "cut_cosim", case + ".sim"), os.path.join(rundir, "deck.sim"))
        r = run([ov, fixture("vhdl", "cut_cosim", "gcond.va"), "-o",
                 os.path.join(rundir, "gcond.osdi")], cwd=rundir)
        self.assertEqual(r.returncode, 0, r.stdout)
        env = dict(os.environ)
        env.update(engines.env_for("vacask", libdir))
        # engines.vacask_module_path() returns None when lib/vacask/mod exists (VACASK's
        # own default), but under nvc the C interface resolves that default against
        # nvc's location, so name it explicitly.
        mod = os.path.join(engines.vacask_home(), "lib", "vacask", "mod")
        if "SIM_MODULE_PATH" not in env and os.path.isdir(mod):
            env["SIM_MODULE_PATH"] = mod
        r = run([nvc, "--std=2040", "--work=work:" + work, "-L", libdir, "-r", "--stop-time=" + stop,
                 "--vacask-netlist=" + os.path.join(rundir, "deck.sim"),
                 "--cosim-config=" + res.boundary_path, "tb"], cwd=rundir, env=env, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("resolved %d/%d boundary signals" % (len(plan.bridges), len(plan.bridges)),
                      r.stdout)
        self.assertNotIn("FAILED", r.stdout)
        shown = re.findall(r"^\*\* Note: \S+:\s+(\d+) (\w+)=([01xz])$", r.stdout, re.M)
        return shown, _raw(os.path.join(rundir, "tran1.raw"))

    def test_shared_parent_paths_differ(self):
        # one architecture, three paths: u1 hosts clk's D2A and yb's A2D at 1.8 V;
        # w1.u3 hosts its own D2A at 3.3 V and an A2D at 1.1/2.2 V; w2.u3 is passive
        shown, raw = self.cosim("shared", {"tb.w1.u3.a": (3.3, 0.0, 0.6, 1.2),
                                           "tb.w1.u3.y": (1.8, 0.0, 1.1, 2.2)})
        yb = [(int(t), v) for t, n, v in shown if n == "yb" and int(t) > 0]
        y3 = [(int(t), v) for t, n, v in shown if n == "y3" and int(t) > 0]
        self.assertEqual([v for _, v in yb], ["1", "0", "1", "0", "1", "0", "1", "0", "1"])
        self.assertEqual([t // 10000 for t, _ in yb], [1, 2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual([v for _, v in y3], ["1", "0", "1", "0", "1", "0"])
        self.assertAlmostEqual(_at(raw, "n_u1_a", 19e-9), 1.8, delta=0.05)
        self.assertAlmostEqual(_at(raw, "n_w1_u3_a", 29e-9), 3.3, delta=0.05)
        self.assertAlmostEqual(_at(raw, "n_u1_y", 19e-9), 1.8, delta=0.05)
        self.assertAlmostEqual(_at(raw, "n_w1_u3_y", 29e-9), 3.3, delta=0.05)

    def test_bidirectional_pin(self):
        # released: the pull moved into the deck holds the pad at 1.8*20k/(20k+3500.2)
        shown, raw = self.cosim("bidir")
        pad = [(int(t), v) for t, n, v in shown if n == "pad" and int(t) > 0]
        self.assertEqual(pad, [(20000, "0"), (40000, "1"), (60000, "1")])
        released = 1.8 * 20e3 / (20e3 + 3500.2)
        self.assertAlmostEqual(_at(raw, "n_u1_pad", 10e-9), released, delta=0.005)
        self.assertAlmostEqual(_at(raw, "n_u1_pad", 30e-9), 0.0, delta=0.005)
        self.assertAlmostEqual(_at(raw, "n_u1_pad", 50e-9), 1.8 * 20e3 / (20e3 + 500.7), delta=0.005)
        self.assertAlmostEqual(_at(raw, "n_u1_pad", 70e-9), released, delta=0.005)
        self.assertAlmostEqual(_at(raw, "n_u1_pad_e", 10e-9), 500.7 / 3500.2, delta=1e-6)
        self.assertAlmostEqual(_at(raw, "n_u1_pad_e", 30e-9), 1.0, delta=1e-6)


if __name__ == "__main__":
    unittest.main()
