"""Round-6 "C" regressions: the digital cut (docs/VAMOS_AMS_DESIGN.md §5.4, §10: vamos/ams/
cut.py, vhdl.py, shells.py), the translation scripts (bin/sv2vhdl-modules,
bin/iverilog-sv2ghdl), the regress harness (regress/) and stale names.

  * a tran or an sv2vhdl alias joins its two nets: a pull behind a tran left an inout
    SPICE pad's node at 0 V (the pull, taken for a strong driver, was not moved into the
    deck), a SPICE output through a tran was an error; a supply behind a tran stays a
    strong driver (a tran reduces supply to strong);
  * a SPICE output read only through a process variable (`q <= x` in a clocked always
    block: tgt-vhdl's `v_nba_q := l3d_strengthen(x)`) is read: it gets its A2D (it had
    none, and the register read z for the whole run);
  * the quantised round-trip warning follows continuous assignments and gate primitives
    beyond one hop (not a register);
  * a reg/logic or a tri0/tri1 declared in a begin block (a generate loop or block) counts
    for Net.variable and the tri0/tri1 rule, under its Verilog name (tb.g[0].r), from
    tgt-vhdl's generate-path names (r_g_0);
  * defence in depth: a real cut port's net must be real throughout;
  * SPICE port spellings: the folded Netlist.spelling key (set_sim_case upper and
    sensitive), and a subckt's own spelling when the IR keeps one;
  * the IE report gives each instance its own step-2b reason (shells.Direction);
  * an "Error:" from the VHDL back end with exit 0 is a failed translation, never a module
    that simulates X (sv2vhdl-modules defers it; vamos then stops with the message);
  * nvc's errors in a module the design does not use (the module-by-module translation
    analyses every module) are a note naming the module, not "** Error" lines on the
    console of a compile that worked; when nothing else translates they still show;
  * regress: a --filter that selects nothing is an error (never a whole-suite run); a
    filtered ivtest run reads the runner's own lists (regress-vhdl.list, regress-synth.list
    with -S, the runner's order); copies of one results DB get work dirs of their own; an
    ivtest run works in a private work area; a filter with '$' survives the dispatch
    makefile; a failed smak dispatch keeps the finished blocks and re-runs only the others
    under GNU make, saying why smak failed; the dispatcher's leftovers are ended; every
    block runs without the dispatcher's MAKE/MAKEFLAGS/SMAK_* variables;
  * neutral names in the Xyce-side C runner, and no stale load-order comments.

The pure-Python tests run everywhere (Cygwin Python 3.9 too); the harness tests need perl
with DBI and DBD::SQLite, bash, GNU make and an ivtest runner (WSL); the translation and
end-to-end tests need the stack (and an analog engine for AMS) (WSL).

    python3 -m unittest discover -s tests/vamos -p 'test_r6_C.py' -v

R6C_REGRESS_DIR=<dir> runs the harness tests on another copy of regress/ (default: this
tree's); R6C_IVTEST=<dir> takes the ivtest runners and perl-lib from another ivtest tree.
"""

from __future__ import annotations

import ast
import json
import os
import pickle
import re
import shutil
import sqlite3
import subprocess
import time
import unittest
from typing import Dict, List, Optional, Sequence, Tuple

from vamos_testlib import ROOT, TempDir, fixture, have_stack, run
from ams_e2e_lib import AmsCase, needs_ams

from vamos import tools  # noqa: E402
from vamos.ams import cut, names, shells, vhdl  # noqa: E402
from vamos.ams.config import AmsConfig  # noqa: E402
from vamos.ams.model import (A2D, AUTO, BIDIR, D2A, INOUT, INPUT, LOGIC, OUTPUT, POWERNET,  # noqa: E402
                             REAL, CutAnalysis, CutCell, CutPort, RuleHits)
from vamos.ams.verilog_ports import Decl  # noqa: E402
from vamos.netlist.ir import Netlist, Subckt  # noqa: E402
from vamos.notes import NoteError  # noqa: E402

needs_stack = unittest.skipUnless(have_stack(), "needs nvc + iverilog (Linux/WSL)")


# =============================================================================
# designs as tgt-vhdl draws them (iverilog-sv2ghdl -g2012, T3 comments)
# =============================================================================

NORM = "/r6c/nvc/_norm.sv"
DESIGN = "/r6c/nvc/design.vhd"
_HEAD = ("library ieee;\nuse ieee.std_logic_1164.all;\nlibrary sv2vhdl;\n"
         "use sv2vhdl.logic3d_types_pkg.all;\n\n")


def _shell(cell: str, variant: str, ports: Sequence[Tuple[str, str]], line: int = 20) -> str:
    """A SPICE cell's shell variant: a bufif1 vamos_ams_hiz marker on each output or inout
    port, fed by two constant temporaries.  ports: [(name, 'in' | 'out' | 'inout')]."""
    decls = ";\n".join("    %s : %s %s" % (p, m, "resolved_logic3d" if m == "inout" else "logic3d")
                       for p, m in ports)
    sigs: List[str] = []
    body: List[str] = []
    k = 0
    for p, m in ports:
        if m == "in":
            continue
        t0, t1 = "tmp_ivl_%d" % (4 * k), "tmp_ivl_%d" % (4 * k + 2)
        sigs += ["  signal %s : logic3d := L3D_X;  -- Temporary created at %s:%d" % (t, NORM, line + 2)
                 for t in (t0, t1)]
        body += ["  sv_bufif1_vamos_ams_hiz_%d_0_0_inst: entity sv2vhdl.sv_bufif1(behavioral)" % k,
                 "    port map (", "      y => %s," % p, "      data => %s," % t0,
                 "      ctrl => %s" % t1, "    );"]
        for t in (t0, t1):
            body += ["  process (all) is", "  begin", "    %s <= L3D_0;" % t, "  end process;"]
        k += 1
    prov = "-- Generated from Verilog module %s (%s:%d)\n" % (cell, NORM, line)
    return (_HEAD + prov + "entity %s is\n  port (\n%s\n  );\nend entity;\n\n" % (variant, decls)
            + prov + "architecture from_verilog of %s is\n%s\nbegin\n%s\nend architecture;\n\n"
            % (variant, "\n".join(sigs), "\n".join(body)))


def _tb(signals: Sequence[Tuple], stmts: str) -> str:
    """The top tb: signals [(name, type, declared line[, initial value])], statements."""
    prov = "-- Generated from Verilog module tb (%s:2)\n" % NORM
    sigs = "\n".join("  signal %s : %s := %s;  -- Declared at %s:%d"
                     % (s[0], s[1], s[3] if len(s) > 3 else "L3D_X", NORM, s[2]) for s in signals)
    return (_HEAD + prov + "entity tb is\nend entity;\n\n" + prov
            + "architecture from_verilog of tb is\n%s\nbegin\n%s\nend architecture;\n" % (sigs, stmts))


def _inst(label: str, variant: str, assoc: Sequence[Tuple[str, str]], line: int,
          vpath: Optional[str] = None) -> str:
    return ("  -- Generated from instantiation at %s:%d\n  -- Verilog instance: %s\n"
            "  %s: entity work.%s\n    port map (\n%s\n    );\n"
            % (NORM, line, vpath or label, label, variant,
               ",\n".join("      %s => %s" % a for a in assoc)))


def _lib(label: str, entity: str, assoc: Sequence[Tuple[str, str]], arch: str = "behavioral",
         strength: Optional[str] = None) -> str:
    return (("  -- sv_strength: %s\n" % strength if strength else "")
            + "  %s: entity sv2vhdl.%s(%s)\n    port map (\n%s\n    );\n"
            % (label, entity, arch, ",\n".join("      %s => %s" % a for a in assoc)))


def _proc(body: str, sens: Optional[str] = "all", decls: str = "", label: str = "") -> str:
    """A process; sens None: no sensitivity list (one that waits)."""
    return "  %sprocess%s is\n%s  begin\n%s  end process;\n" % (
        label + ": " if label else "", " (%s)" % sens if sens is not None else "", decls, body)


# `always @(posedge clk) q <= x;` as tgt-vhdl draws it (the NBA through a process variable)
_FLOP_PROC = _proc("    v_nba_q := q;\n    if rising_edge(clk) then\n"
                   "      v_nba_q := l3d_strengthen(x);\n    end if;\n    wait for 0 ns;\n"
                   "    q <= v_nba_q;\n    wait on clk;\n",
                   sens=None, decls="    variable v_nba_q : logic3d;\n")


def _reader(sig: str) -> str:
    return _proc("    report \"%s\" & to_string(%s);\n" % (sig, sig), sens=sig)


def _cell(name: str, ports: Sequence[Tuple[str, str, str]], view: str = "spice") -> CutCell:
    return CutCell(name, view, name.lower(),
                   [CutPort(i, nm, kind, d, d) for i, (nm, kind, d) in enumerate(ports)])


def _netlist(*subs: Tuple[str, Sequence[str]]) -> Netlist:
    nl = Netlist(body=[Subckt(n.lower(), [p.lower() for p in ps], orig_ports=[p.lower() for p in ps])
                       for n, ps in subs])
    for _, ps in subs:
        nl.spelling.update({p.lower(): p for p in ps})
    return nl


def _analyse(text: str, cells: Sequence[CutCell], nl: Netlist, pp: object = None):
    return cut.analyse(vhdl.parse_text(text, DESIGN), "tb", list(cells), nl, AmsConfig(),
                       RuleHits(), pp=pp)


def _roles(ana) -> List:
    return cut.assign_roles(ana, names.NameAllocator(), lambda n: False, lambda n: (False, None))


def _warnings(ana) -> List[str]:
    return [n.message for n in ana.notes if n.severity == "warning"]


PAD = _shell("pad_sp", "pad_sp__c3c7", [("pad", "inout")])
IN1 = _shell("in_sp", "in_sp__4e0c", [("a", "in")])
IN2 = _shell("in2_sp", "in2_sp__4e0c", [("a", "in")])
DRV = _shell("drv_sp", "drv_sp__1111", [("y", "out")])
SRC = _shell("src_sp", "src_sp__7741", [("o", "out")])
SINK = _shell("sink_sp", "sink_sp__ef39", [("i", "in")])

PAD_CELL = _cell("pad_sp", [("pad", LOGIC, INOUT)])
IN_CELLS = [_cell("in_sp", [("a", LOGIC, INPUT)]), _cell("in2_sp", [("a", LOGIC, INPUT)])]
DRV_CELL = _cell("drv_sp", [("y", LOGIC, OUTPUT)])
SRC_SINK = [_cell("src_sp", [("o", LOGIC, OUTPUT)]), _cell("sink_sp", [("i", LOGIC, INPUT)])]
SRC_SINK_NL = _netlist(("src_sp", ["o"]), ("sink_sp", ["i"]))


# =============================================================================
# R6C-01 (a): trans and aliases join their nets
# =============================================================================

def _pull_behind(switch: str, strength: str = "pull1 pull0") -> str:
    """tb: a pull on b, a switch between a and b, an inout SPICE pad on a, b read."""
    return PAD + _tb([("a", "resolved_logic3d", 3), ("b", "resolved_logic3d", 3)],
                     _lib("sv_pullup_ivl_0_0_0_inst", "sv_pullup", [("y", "b")], strength=strength)
                     + _lib("%s_t1_0_0_inst" % switch, switch, [("a", "a"), ("b", "b")], arch="strength")
                     + _inst("u", "pad_sp__c3c7", [("pad", "a")], 6) + _reader("b"))


class TestSwitchJoins(unittest.TestCase):
    """A tran (sv_tran) or an alias (sv_alias) is a wire between its two nets (§5.4 step 3)."""

    def node(self, text: str, cells: Sequence[CutCell], nl: Netlist):
        nodes = _roles(_analyse(text, cells, nl))
        self.assertEqual(len(nodes), 1, [(n.canonical, n.role) for n in nodes])
        return nodes[0]

    def test_pull_behind_a_tran_moves_into_the_deck(self):
        # before: the tran was the net's only (strong) driver, the pull stayed digital and
        # the pad's node sat at 0 V while b read 1 (TestE2ER6C has the voltages)
        n = self.node(_pull_behind("sv_tran"), [PAD_CELL], _netlist(("pad_sp", ["pad"])))
        self.assertEqual((n.canonical, n.role, n.pull), ("tb.u.pad", BIDIR, "up"))
        self.assertTrue(any(r.startswith("pull-up moved into the analog deck") for r in n.report),
                        n.report)
        self.assertIn("tb.b", n.aliases)

    def test_an_alias_joins_too(self):
        n = self.node(_pull_behind("sv_alias"), [PAD_CELL], _netlist(("pad_sp", ["pad"])))
        self.assertEqual((n.role, n.pull), (BIDIR, "up"))

    def test_a_supply_behind_a_tran_stays_a_strong_driver(self):
        # a tran reduces supply to strong: the far side is no supply net
        n = self.node(_pull_behind("sv_tran", "supply1 supply0"), [PAD_CELL],
                      _netlist(("pad_sp", ["pad"])))
        self.assertNotEqual(n.role, POWERNET)
        self.assertIsNone(n.pull)
        self.assertNotIn("tb.b", n.aliases)

    def test_two_spice_inputs_across_a_tran_are_one_node(self):
        text = (IN1 + IN2 + _tb([("a", "resolved_logic3d", 3), ("b", "resolved_logic3d", 3)],
                                _proc("    a <= L3D_1;\n")
                                + _lib("sv_tran_t1_0_0_inst", "sv_tran", [("a", "a"), ("b", "b")],
                                       arch="strength")
                                + _inst("u1", "in_sp__4e0c", [("a", "a")], 6)
                                + _inst("u2", "in2_sp__4e0c", [("a", "b")], 7)))
        n = self.node(text, IN_CELLS, _netlist(("in_sp", ["a"]), ("in2_sp", ["a"])))
        self.assertEqual(n.role, D2A)
        self.assertEqual(len(n.ports), 2)

    def test_a_spice_output_through_a_tran_is_an_a2d(self):
        # before: "SPICE output tb.u.y and a digital driver (... sv_tran ...)" (an error)
        text = DRV + _tb([("a", "resolved_logic3d", 3), ("b", "resolved_logic3d", 3)],
                         _lib("sv_tran_t1_0_0_inst", "sv_tran", [("a", "a"), ("b", "b")],
                              arch="strength")
                         + _inst("u", "drv_sp__1111", [("y", "a")], 6) + _reader("b"))
        n = self.node(text, [DRV_CELL], _netlist(("drv_sp", ["y"])))
        self.assertEqual((n.canonical, n.role), ("tb.u.y", A2D))

    def test_a_controlled_switch_stays_a_driver_and_reader(self):
        # tranif/rtran are not joins: the pull stays on its own net
        text = (PAD + _tb([("a", "resolved_logic3d", 3), ("b", "resolved_logic3d", 3),
                           ("en", "logic3d", 3, "L3D_1")],
                          _lib("sv_pullup_ivl_0_0_0_inst", "sv_pullup", [("y", "b")],
                               strength="pull1 pull0")
                          + _lib("sv_tranif1_t1_0_0_inst", "sv_tranif1",
                                 [("a", "a"), ("b", "b"), ("ctrl", "en")], arch="strength")
                          + _inst("u", "pad_sp__c3c7", [("pad", "a")], 6) + _reader("b")))
        n = self.node(text, [PAD_CELL], _netlist(("pad_sp", ["pad"])))
        self.assertIsNone(n.pull)
        self.assertNotIn("tb.b", n.aliases)


# =============================================================================
# reads through process variables (found while reproducing R6C-01 (b))
# =============================================================================

FLOP = SRC + _tb([("clk", "logic3d", 3, "L3D_0"), ("q", "logic3d", 6), ("x", "resolved_logic3d", 5)],
                 _inst("u1", "src_sp__7741", [("o", "x")], 7) + _FLOP_PROC)


class TestVariableReads(unittest.TestCase):
    """`always @(posedge clk) q <= x;` reads x through tgt-vhdl's NBA variable."""

    def test_vhdl_keeps_the_reads_of_variable_assignments(self):
        d = vhdl.parse_text(FLOP, DESIGN)
        st = [s for s in d.arch("tb").stmts if s.kind == "process"][0]
        self.assertEqual([r.lname for r in st.var_reads], ["q", "x"])

    def test_a_flip_flop_reading_a_spice_output_gets_its_a2d(self):
        # before: THROUGH (no A2D): the register read z for the whole run
        nodes = _roles(_analyse(FLOP, [SRC_SINK[0]], _netlist(("src_sp", ["o"]))))
        self.assertEqual([(n.canonical, n.role) for n in nodes], [("tb.u1.o", A2D)])


# =============================================================================
# R6C-01 (b): the quantised round trip beyond one hop
# =============================================================================

_QUANT_SIGS = [("x", "resolved_logic3d", 3), ("w1", "logic3d", 3), ("w2", "logic3d", 3),
               ("y", "logic3d", 3), ("q", "logic3d", 4), ("clk", "logic3d", 4, "L3D_0")]
_QUANT_CELLS = (_inst("u1", "src_sp__7741", [("o", "x")], 5)
                + _inst("u2", "sink_sp__ef39", [("i", "y")], 9))
QUANTISED = "analog connection quantised: tb.u1.o -> digital -> tb.u2.i"


class TestQuantisedChains(unittest.TestCase):
    """§5.4 step 5: an A2D -> digital -> D2A round trip, through continuous assignments and
    gate primitives on nets with no SPICE port, any number of them."""

    def warnings(self, stmts: str) -> List[str]:
        ana = _analyse(SRC + SINK + _tb(_QUANT_SIGS, _QUANT_CELLS + stmts), SRC_SINK, SRC_SINK_NL)
        _roles(ana)
        return _warnings(ana)

    def test_one_hop(self):
        self.assertIn(QUANTISED, self.warnings(_proc("    y := l3d_strengthen(x);\n", sens="x",
                                                      label="comb_fused_0")))

    def test_a_fused_chain(self):
        # assign w1 = x; assign w2 = w1; assign y = w2;  (one comb_fused process)
        self.assertIn(QUANTISED, self.warnings(_proc(
            "    w1 := l3d_strengthen(x);\n    w2 := l3d_strengthen(w1);\n"
            "    y := l3d_strengthen(w2);\n", sens="x", label="comb_fused_0")))

    def test_concurrent_assignments(self):
        self.assertIn(QUANTISED, self.warnings(
            _proc("    w1 <= l3d_strengthen(x);\n") + _proc("    w2 <= l3d_strengthen(w1);\n")
            + _proc("    y <= l3d_strengthen(w2);\n")))

    def test_gate_primitives(self):
        self.assertIn(QUANTISED, self.warnings(
            _lib("sv_buf_b1_inst", "sv_buf", [("y", "w1"), ("a", "x")])
            + _lib("sv_not_n1_inst", "sv_not", [("y", "y"), ("a", "w1")])))

    def test_not_through_a_register(self):
        self.assertNotIn(QUANTISED, self.warnings(_FLOP_PROC + _proc("    y <= l3d_strengthen(q);\n")))


# =============================================================================
# R6C-01 (e): declarations inside begin blocks
# =============================================================================

class FakePP:
    """The PP declaration scan as cut.py reads it (verilog_ports.PP's rules)."""

    def __init__(self, variables: Sequence[Decl] = (), tri_nets: Sequence[Decl] = ()):
        self.variables = list(variables)
        self.tri_nets = list(tri_nets)

    def is_variable(self, module: str, name: str) -> bool:
        return any(d.module == module and d.name == name and not d.block for d in self.variables)

    def tri_kind(self, module: str, name: str) -> Optional[str]:
        for d in self.tri_nets:
            if d.module == module and d.name == name and not d.block:
                return d.kind
        return None


def _gen_var(sig: str = "r_g_0", prefix: str = "g[0].", suffix: str = "_g_0") -> str:
    """reg r = 0 in generate loop g (tgt-vhdl: r_g_0, the instances u1_g_0, u2_g_0),
    joining two SPICE inputs."""
    return IN1 + IN2 + _tb([(sig, "logic3d", 5, "L3D_0")],
                           _inst("u1" + suffix, "in_sp__4e0c", [("a", sig)], 6, prefix + "u1")
                           + _inst("u2" + suffix, "in2_sp__4e0c", [("a", sig)], 7, prefix + "u2")
                           + _proc("    wait for 10000 ps;\n    %s := L3D_1;\n    wait;\n" % sig,
                                   sens=None))


class TestBeginBlockDeclarations(unittest.TestCase):
    """§5.4 step 4: Net.variable and the tri0/tri1 rule count block-local declarations."""

    NL = _netlist(("in_sp", ["a"]), ("in2_sp", ["a"]))

    def warned(self, text: str, pp: FakePP, start: str) -> List:
        ana = _analyse(text, IN_CELLS, self.NL, pp)
        nodes = _roles(ana)
        self.assertTrue(any(w.startswith(start) for w in _warnings(ana)), _warnings(ana))
        return nodes

    def test_a_generate_local_variable_between_spice_ports_is_warned(self):
        # before: no warning (only module-level declarations counted)
        pp = FakePP(variables=[Decl("tb", "r", "reg", 5, "tb.sv:5", block="g")])
        nodes = self.warned(_gen_var(), pp, "variable tb.g[0].r joins SPICE ports in analog")
        self.assertIn("tb.g[0].r", nodes[0].aliases)

    def test_a_named_block_variable(self):
        pp = FakePP(variables=[Decl("tb", "r", "reg", 5, "tb.sv:5", block="b")])
        self.warned(_gen_var("r_b", "b.", "_b"), pp, "variable tb.b.r joins")

    def test_an_unnamed_block_variable(self):
        pp = FakePP(variables=[Decl("tb", "r", "reg", 5, "tb.sv:5", block="-")])
        self.warned(_gen_var("r_genblk1", "genblk1.", "_genblk1"), pp, "variable tb.genblk1.r joins")

    def test_nested_generate_loops(self):
        pp = FakePP(variables=[Decl("tb", "r", "reg", 5, "tb.sv:5", block="g.h")])
        self.warned(_gen_var("r_g_1_h_2", "g[1].h[2].", "_g_1_h_2"), pp, "variable tb.g[1].h[2].r joins")

    def test_a_module_level_variable_still_counts(self):
        pp = FakePP(variables=[Decl("tb", "r", "reg", 5, "tb.sv:5")])
        self.warned(_gen_var("r", "", ""), pp, "variable tb.r joins")

    def test_a_block_declaration_of_another_name_on_the_line_is_not_it(self):
        pp = FakePP(variables=[Decl("tb", "other", "reg", 5, "tb.sv:5", block="g")])
        ana = _analyse(_gen_var(), IN_CELLS, self.NL, pp)
        _roles(ana)
        self.assertFalse(any("joins SPICE ports" in w for w in _warnings(ana)))

    def test_a_generate_local_tri1_without_its_pull_is_an_error(self):
        text = IN1 + _tb([("t_g_0", "logic3d", 5)],
                         _inst("u1_g_0", "in_sp__4e0c", [("a", "t_g_0")], 6, "g[0].u1"))
        pp = FakePP(tri_nets=[Decl("tb", "t", "tri1", 5, "tb.sv:5", block="g")])
        with self.assertRaises(NoteError) as cm:
            _analyse(text, IN_CELLS[:1], _netlist(("in_sp", ["a"])), pp)
        self.assertIn("tri1 net", str(cm.exception))
        self.assertIn("its pull needs translator patch T5", str(cm.exception))
        # with the pull T5 draws, it passes
        _analyse(text.replace("begin\n  -- Generated from instantiation",
                              "begin\n" + _lib("sv_tri1_t_g_0", "sv_pullup", [("y", "t_g_0")],
                                               strength="pull1 pull0")
                              + "  -- Generated from instantiation", 1),
                 IN_CELLS[:1], _netlist(("in_sp", ["a"])), pp)

    def test_block_paths(self):
        bp = cut._block_path
        self.assertEqual(bp("r_g_0", "g"), ("r", "g[0]"))
        self.assertEqual(bp("r_g_1_h_2", "g.h"), ("r", "g[1].h[2]"))
        self.assertEqual(bp("r_g_1_b", "g.b"), ("r", "g[1].b"))
        self.assertEqual(bp("r_b", "b"), ("r", "b"))
        self.assertEqual(bp("r_genblk2_3", "-"), ("r", "genblk2[3]"))
        self.assertEqual(bp("x_g_g_0", "g"), ("x_g", "g[0]"))
        self.assertEqual(bp("R_G_0", "g"), ("R", "g[0]"))
        self.assertIsNone(bp("r", "g"))
        self.assertIsNone(bp("r_i0", "g"))           # not the translator's naming
        self.assertIsNone(bp("r_g_0", ""))
        self.assertIsNone(bp("_g_0", "g"))


# =============================================================================
# R6C-02: a real cut port's net is real throughout
# =============================================================================

class TestRealPortBackstop(unittest.TestCase):
    CELLS = [_cell("vamp", [("vin", REAL, INPUT), ("vout", REAL, OUTPUT)], view="multi")]
    NL = _netlist(("vamp", ["vin", "vout"]))

    def design(self) -> Tuple[str, str]:
        path = fixture("vhdl", "cut_real", "design.vhd")
        with open(path) as fh:
            return fh.read(), path

    def test_a_logic_signal_on_a_real_port_is_an_error(self):
        text, path = self.design()
        bad = text.replace("signal vin_r : real := 0.0;", "signal vin_r : logic3d := L3D_X;")
        self.assertNotEqual(bad, text)
        with self.assertRaises(NoteError) as cm:
            cut.analyse(vhdl.parse_text(bad, path), "tb", self.CELLS, self.NL, AmsConfig(), RuleHits())
        self.assertIn("real port vin of tb.a1 is on a net that is not real throughout: tb.vin_r "
                      "(logic3d)", str(cm.exception))

    def test_real_signals_pass(self):
        text, path = self.design()
        ana = cut.analyse(vhdl.parse_text(text, path), "tb", self.CELLS, self.NL, AmsConfig(),
                          RuleHits())
        self.assertEqual(len(ana.nets), 2)


# =============================================================================
# R6C-01 (c): SPICE port spellings
# =============================================================================

def _with_netlist(nl: Netlist) -> CutAnalysis:
    a = CutAnalysis(top="tb", cells={})
    st = cut._State()
    st.nl = nl
    a._cut = st          # type: ignore[attr-defined]
    return a


class TestPortSpelling(unittest.TestCase):
    """spice.py keys Netlist.spelling by the folded name (set_sim_case)."""

    def test_upper(self):
        # before: the lookup by the lowercased name missed: the folded VIN was shown
        nl = Netlist()
        nl.spelling = {"VIN": "Vin"}
        self.assertEqual(cut._spelled(_with_netlist(nl), "VIN"), "Vin")

    def test_sensitive(self):
        # before: Vin was shown as vin's spelling
        nl = Netlist()
        nl.spelling = {"Vin": "Vin", "vin": "vin"}
        self.assertEqual(cut._spelled(_with_netlist(nl), "Vin"), "Vin")
        self.assertEqual(cut._spelled(_with_netlist(nl), "vin"), "vin")

    def test_lower(self):
        nl = Netlist()
        nl.spelling = {"vin": "Vin"}
        self.assertEqual(cut._spelled(_with_netlist(nl), "vin"), "Vin")
        self.assertEqual(cut._spelled(_with_netlist(nl), "other"), "other")

    def test_a_subckts_own_spelling(self):
        # used once the IR keeps each subckt's header spelling (Subckt.spelling)
        a = Subckt("a", ["vin"], orig_ports=["vin"])
        b = Subckt("b", ["vin"], orig_ports=["vin"])
        b.spelling = {"vin": "VIN"}             # type: ignore[attr-defined]
        nl = Netlist(body=[a, b])
        nl.spelling = {"vin": "Vin"}
        ana = _with_netlist(nl)
        self.assertEqual(cut._spelled(ana, "vin", "b"), "VIN")
        self.assertEqual(cut._spelled(ana, "vin", "a"), "Vin")
        self.assertEqual(cut._spelled(ana, "vin", "B"), "VIN")


# =============================================================================
# R6C-01 (d): each instance's own step-2b reason
# =============================================================================

WHY_WE = ("wrap.u (tb.sv:15), connected to input port wrap.a, which tb.we (tb.sv:8) connects "
          "to variable clk")
WHY_W2 = ("wrap.u (tb.sv:15), connected to input port wrap.a, which tb.w2 (tb.sv:9) connects "
          "to variable clk2")
CHAIN_W2 = (("tb", "w2"), ("wrap", "u"))
CHAIN_WE = (("tb", "we"), ("wrap", "u"))


class TestProbeReasons(unittest.TestCase):

    def entry(self):
        return shells.Direction("auto->input (%s)" % WHY_WE, {CHAIN_WE: WHY_WE, CHAIN_W2: WHY_W2})

    def test_direction_is_the_old_string(self):
        d = self.entry()
        self.assertEqual(d, "auto->input (%s)" % WHY_WE)
        self.assertEqual(json.loads(json.dumps({"a": d})), {"a": str(d)})
        e = pickle.loads(pickle.dumps(d))
        self.assertEqual((e, e.by_chain), (d, d.by_chain))

    def test_each_instance_has_its_own_reason(self):
        cp = CutPort(0, "a", LOGIC, AUTO, INPUT)
        dirs = {"auto_sp": {"a": self.entry()}}
        self.assertEqual(cut._probe_reason(dirs, "auto_sp", cp, CHAIN_W2), WHY_W2)
        self.assertEqual(cut._probe_reason(dirs, "auto_sp", cp, CHAIN_WE), WHY_WE)
        # an instance with no reason of its own, and a plain string entry: the first reason
        self.assertEqual(cut._probe_reason(dirs, "auto_sp", cp, (("tb", "x"), ("wrap", "u"))), WHY_WE)
        self.assertEqual(cut._probe_reason({"auto_sp": {"a": str(self.entry())}}, "auto_sp", cp,
                                           CHAIN_W2), WHY_WE)


# =============================================================================
# end to end (vcs-ams, each available engine)
# =============================================================================

E2E_SP = """\
* r6c cells
.subckt pad_sp pad
r1 pad 0 100k
.ends
.subckt drv_sp y
v1 x 0 1.8
r1 x y 1k
.ends
.subckt src_sp out
v1 out 0 pulse(0 1.8 10n 1n 1n 20n 40n)
.ends
.subckt auto_sp a vo
e1 vo 0 a 0 1
.ends
.subckt in_sp a
r1 a 0 1meg
.ends
.subckt in2_sp a
r1 a 0 1meg
.ends
.tran 1n 100n
"""
E2E_INIT = ("choose xa cells.sp;\nport_dir -cell pad_sp (inout pad);\nport_dir -cell drv_sp (output y);\n"
            "port_dir -cell src_sp (output out);\nport_dir -cell in_sp (input a);\n"
            "port_dir -cell in2_sp (input a);\n")

TB_TRAN_PULL = """\
`timescale 1ns/1ps
module tb;
  wire a, b;
  pullup (b);
  tran t1 (a, b);
  pad_sp u (.pad(a));
  always @(b) $display("%0t b=%b", $time, b);
  initial #100 $finish;
endmodule
"""
TB_DIRECT_PULL = """\
`timescale 1ns/1ps
module tb;
  wire a;
  pullup (a);
  pad_sp u (.pad(a));
  always @(a) $display("%0t b=%b", $time, a);
  initial #100 $finish;
endmodule
"""
TB_TRAN_OUT = """\
`timescale 1ns/1ps
module tb;
  wire a, b;
  tran t1 (a, b);
  drv_sp u (.y(a));
  always @(b) $display("%0t b=%b", $time, b);
  initial #100 $finish;
endmodule
"""
TB_FLOP = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  always #5 clk = ~clk;
  wire x;
  reg q;
  src_sp u1 (.out(x));
  always @(posedge clk) q <= x;
  always @(q) $display("%0t q=%b", $time, q);
  initial #100 $finish;
endmodule
"""
TB_PROBE2 = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  reg clk2 = 0;
  always #5 clk = ~clk;
  always #4 clk2 = ~clk2;
  wire y1, y2;
  wrap we (.a(clk), .y(y1));
  wrap w2 (.a(clk2), .y(y2));
  always @(y1) $display("%0t y1=%b", $time, y1);
  always @(y2) $display("%0t y2=%b", $time, y2);
  initial #50 $finish;
endmodule
module wrap (input a, output y);
  auto_sp u (.a(a), .vo(y));
endmodule
"""
TB_VAR_GEN = """\
`timescale 1ns/1ps
module tb;
  genvar i;
  generate for (i = 0; i < 1; i = i + 1) begin : g
    reg r = 0;
    in_sp u1 (.a(r));
    in2_sp u2 (.a(r));
    initial begin #10 r = 1; end
  end endgenerate
  initial #60 $finish;
endmodule
"""


@needs_ams
class TestE2ER6C(AmsCase):

    def build(self, name: str, tb: str, engine: str, run: bool = True):
        d = self.case(name + "_" + engine, {"tb.sv": tb, "cells.sp": E2E_SP, "vcsAD.init": E2E_INIT})
        comp = self.compile(d, "-sverilog", "tb.sv", engine=engine)
        out = self.simv(d).stdout if run else ""
        return d, comp.stdout, out

    def test_a_pull_behind_a_tran_reaches_the_pad(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d1, _, _ = self.build("tran_pull", TB_TRAN_PULL, engine)
                d2, _, _ = self.build("direct_pull", TB_DIRECT_PULL, engine)
                self.assertIn("// pull-up moved into the analog deck", self.report(d1))
                for t in (5e-9, 50e-9, 99e-9):
                    v1, v2 = self.raw(d1).at("n_u_pad", t), self.raw(d2).at("n_u_pad", t)
                    self.assertGreater(v2, 1.0)          # the pull-up against 100k
                    self.assertAlmostEqual(v1, v2, delta=1e-6)

    def test_a_spice_output_through_a_tran(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d, _, out = self.build("tran_out", TB_TRAN_OUT, engine)
                self.assertIn("a2d loth=", self.report(d))
                self.assertEqual(self.display_values(out, "b")[-1], "1")

    def test_a_flip_flop_reads_a_spice_output(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d, _, out = self.build("flop", TB_FLOP, engine)
                ev = self.display_events(out, "q")
                # the pulse is high 11-31 ns and 51-71 ns: sampled at 15, 35, 55, 75 ns
                self.assertEqual([e for e in ev if e[0] > 0][:4],
                                 [(5000, "0"), (15000, "1"), (35000, "0"), (55000, "1")], out)

    def test_each_instance_reports_its_own_reason(self):
        # before: tb.w2.u.a showed tb.we's reason (tb.we, variable clk)
        engine = self.engines()[0]
        d, _, _ = self.build("probe2", TB_PROBE2, engine, run=False)
        rpt = self.report(d)
        self.assertIn("// direction: auto→input (%s) tb.we.u.a" % WHY_WE, rpt)
        self.assertIn("// direction: auto→input (%s) tb.w2.u.a" % WHY_W2, rpt)

    def test_a_generate_local_variable_is_warned(self):
        # (tgt-vhdl names g[0]'s r r_g_0)
        engine = self.engines()[0]
        _, comp, _ = self.build("var_gen", TB_VAR_GEN, engine, run=False)
        self.assertIn("variable tb.g[0].r joins SPICE ports in analog", comp)


# =============================================================================
# R6C-05, R6C-06: the translation scripts
# =============================================================================

BIN = os.path.join(ROOT, "bin")
SHIMS = os.path.join(ROOT, "shims")

# iverilog with a VHDL back end error injected: it runs the real one; for a -tvhdl run of a
# module named in $R6C_ERROR_FOR (-s <module>; "whole" for a run without -s) it then prints
# tgt-vhdl's "Error: ..." on stderr and exits as the real one did; for a -s run of a module
# named in $R6C_BREAK_FOR it appends an architecture of that module nvc cannot analyse.
FAKE_BACKEND = """\
#!/bin/bash
"%(real)s" "$@"; rc=$?
tvhdl=0; mod=""; out=""; prev=""
for a in "$@"; do
  [ "$a" = -tvhdl ] && tvhdl=1
  [ "$prev" = -s ] && mod="$a"
  [ "$prev" = -o ] && out="$a"
  prev="$a"
done
[ $tvhdl = 1 ] || exit $rc
case " $R6C_ERROR_FOR " in *" ${mod:-whole} "*) echo "Error: injected by the test for ${mod:-whole}" >&2;; esac
if [ -n "$mod" ] && [ -n "$out" ]; then
  case " $R6C_BREAK_FOR " in *" $mod "*)
    printf '\\narchitecture r6c_broken of %%s is\\nbegin\\n  r6c_no_such_signal <= L3D_1;\\nend architecture;\\n' "$mod" >> "$out";;
  esac
fi
exit $rc
"""

T_MOD = "module t(output reg y);\n  initial begin y = 1; #1 $display(\"y=%b\", y); end\nendmodule\n"
T_UNUSED = """\
`timescale 1ns/1ps
module unused_m(input a, output y);
  assign y = ~a;
endmodule
module t_top;
  parameter USE = 0;
  reg a = 0;
  wire y;
  generate if (USE) begin : g
    unused_m u (.a(a), .y(y));
  end else begin : n
    assign y = 1'b1;
  end endgenerate
  initial #1 $display("y=%b", y);
endmodule
"""
# Hazard3's hazard3_onehot_priority_dynamic.v shape: a bit of a memory word assigned across
# two lines, which the module-by-module translation sizes to the word (nvc rejects it) ...
ONEHOT_DYN = """\
module onehot_dyn(input [7:0] req, input [7:0] pri, output [1:0] has);
  reg [7:0] m [0:1];
  always @(*) begin : stratify
    reg signed [31:0] i, j;
    for (i = 0; i < 2; i = i + 1)
      for (j = 0; j < 8; j = j + 1)
        m[i][j] = req[j] &&
                  pri[j] == i[0];
  end
  assign has = {|m[1], |m[0]};
endmodule
"""
# ... instantiated only in a generate branch that is off
T_ONEHOT_UNUSED = "`timescale 1ns/1ps\n" + ONEHOT_DYN + """\
module t_unused;
  parameter USE = 0;
  reg [7:0] req = 8'b1111_0101, pri = 8'b1010_1100;
  wire [1:0] has;
  generate if (USE) begin : g
    onehot_dyn u (.req(req), .pri(pri), .has(has));
  end else begin : n
    assign has = 2'b01;
  end endgenerate
  initial #1 $display("has=%b", has);
endmodule
"""
# the same bit written on one line: sv-normalize makes it $set_val(m, i, j, ...), which
# tgt-vhdl refuses for a memory with "Error: first arg to $set_val must be a signal" on
# stderr, dropping the always block's body, and exits 0 (vamos then ran it: X)
T_MEMBIT = """\
`timescale 1ns/1ps
module t_membit;
  reg [7:0] m [0:1];
  reg [7:0] req;
  reg [7:0] pri;
  always @(*) begin : stratify
    reg signed [31:0] i, j;
    for (i = 0; i < 2; i = i + 1)
      for (j = 0; j < 8; j = j + 1)
        m[i][j] = req[j] && pri[j] == i[0];
  end
  initial begin
    req = 8'b1111_0101;
    pri = 8'b1010_1100;
    #1 $display("m0=%b m1=%b", m[0], m[1]);
    $finish;
  end
endmodule
"""


@needs_stack
class TestTranslationErrors(TempDir):
    """bin/sv2vhdl-modules and bin/iverilog-sv2ghdl, alone and under vcs."""

    def setUp(self):
        super().setUp()
        self.real = tools.find_real("iverilog")
        self.fake = self.write(os.path.join("fake", "iverilog"), FAKE_BACKEND % {"real": self.real})
        os.chmod(self.fake, 0o755)

    def env(self, **kw) -> Dict[str, str]:
        e = dict(os.environ)
        e.pop("PYTHONPATH", None)
        e.pop("VAMOS_ANALOG", None)
        e["PATH"] = SHIMS + os.pathsep + e.get("PATH", "")
        e.update(kw)
        return e

    def vcs(self, src: str, text: str, **env):
        self.write(src, text)
        return run(["vcs", "-sverilog", src, "-o", "simv"], cwd=self.tmp, env=self.env(**env),
                   timeout=600)

    def simv(self):
        return run(["./simv"], cwd=self.tmp, env=self.env(), timeout=600)

    # -- R6C-05 -------------------------------------------------------------------------------

    def test_sv2vhdl_modules_defers_a_module_with_a_backend_error(self):
        self.write("t.v", T_MOD)
        e = self.env(IVERILOG=self.fake)
        r = run([os.path.join(BIN, "sv2vhdl-modules"), "t.v", "-o", "out.vhd"], cwd=self.tmp, env=e)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("=== sv2vhdl-modules: iverilog -tvhdl -s t: translated", self.read("iverilog.log"))
        self.assertNotIn("sv2vhdl:deferred", self.read("out.vhd"))
        # before: the module was "translated" though the back end said Error: (exit 0)
        e["R6C_ERROR_FOR"] = "t"
        r = run([os.path.join(BIN, "sv2vhdl-modules"), "t.v", "-o", "out.vhd"], cwd=self.tmp, env=e)
        self.assertEqual(r.returncode, 0, r.stdout)
        log = self.read("iverilog.log")
        self.assertIn("=== sv2vhdl-modules: iverilog -tvhdl -s t: deferred (1 error(s) from the "
                      "VHDL back end, which exited 0)\n", log)
        self.assertIn("Error: injected by the test for t", log)
        self.assertIn("sv2vhdl:deferred source=", self.read("out.vhd"))
        self.assertIn("module t: deferred (1 error(s) from the VHDL back end", r.stdout)

    def test_vcs_stops_at_a_backend_error(self):
        c = self.vcs("t.v", T_MOD, VAMOS_IVERILOG=self.fake, R6C_ERROR_FOR="t")
        self.assertEqual(c.returncode, 1, c.stdout)
        self.assertIn("vamos: error: sv2ghdl could not translate top module 't': "
                      "Error: injected by the test for t", c.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "simv")), c.stdout)

    def test_the_whole_design_stage_fails_on_a_backend_error(self):
        # a copy of bin/ without sv-normalize: no module-by-module stage, the whole-design
        # stage runs first, and an Error: with exit 0 fails it (iverilog.log says so)
        shutil.copytree(BIN, os.path.join(self.tmp, "bin2"), symlinks=True)
        os.remove(os.path.join(self.tmp, "bin2", "sv-normalize"))
        self.write("t.v", T_MOD)
        r = run([os.path.join(self.tmp, "bin2", "iverilog-sv2ghdl"), "-o", "out", "t.v"],
                cwd=self.tmp, env=self.env(IVERILOG=self.fake, R6C_ERROR_FOR="whole"))
        self.assertIn("=== iverilog-sv2ghdl: iverilog -tvhdl (whole design)\n"
                      "Error: injected by the test for whole\n"
                      "iverilog-sv2ghdl: 1 error(s) from the VHDL back end, which exited 0: this "
                      "run failed\n", self.read(os.path.join("out", "iverilog.log")), r.stdout)
        md = os.path.join(self.tmp, "out", "_metadata")
        if os.path.exists(md):
            self.assertNotIn("IVERILOG_BACKEND=1", self.read(md))

    def test_a_set_val_on_a_memory_never_simulates_x(self):
        # before: vcs exited 0 and ./simv printed m0=xxxxxxxx m1=xxxxxxxx
        c = self.vcs("t_membit.v", T_MEMBIT)
        if c.returncode == 0:
            r = self.simv()
            self.assertIn("m0=01010001 m1=10100100", r.stdout)        # what vvp prints
        else:
            self.assertRegex(c.stdout, r"vamos: error: sv2ghdl could not translate top module "
                                       r"'t_membit': Error: first arg to \$set_val")

    # -- R6C-06 -------------------------------------------------------------------------------

    def test_an_unused_module_that_does_not_analyse_is_a_note(self):
        # before: "** Error: no visible declaration for R6C_NO_SUCH_SIGNAL" on the console
        # of a compile that worked
        c = self.vcs("t_top.v", T_UNUSED, VAMOS_IVERILOG=self.fake, R6C_BREAK_FOR="unused_m")
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertNotIn("** Error", c.stdout)
        self.assertRegex(c.stdout, r"iverilog-sv2ghdl: note: module unused_m, which the design does "
                                   r"not use, does not translate on its own \(nvc: no visible "
                                   r"declaration for R6C_NO_SUCH_SIGNAL, \S+/_mods_design\.vhd:\d+\); "
                                   r"it is left out")
        self.assertIn("y=1", self.simv().stdout)
        nvc = os.path.join("simv.daidir", "nvc")
        self.assertIn("_mods_design.vhd:", self.read(os.path.join(nvc, "_mods_analysis.log")))
        self.assertIn("r6c_broken", self.read(os.path.join(nvc, "_mods_design.vhd")))

    def test_a_used_module_names_the_whole_design_translation(self):
        c = self.vcs("t_top.v", T_UNUSED, VAMOS_IVERILOG=self.fake, R6C_BREAK_FOR="t_top")
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertNotIn("** Error", c.stdout)
        self.assertIn("iverilog-sv2ghdl: note: the module-by-module translation of module t_top does "
                      "not analyse (nvc: no visible declaration for R6C_NO_SUCH_SIGNAL", c.stdout)
        self.assertIn("the whole-design translation, which analyses, was used", c.stdout)

    def test_the_errors_still_show_when_nothing_else_translates(self):
        # stage 1 fails too: stage 1a's nvc errors are printed, as they always were
        c = self.vcs("t_top.v", T_UNUSED, VAMOS_IVERILOG=self.fake, R6C_BREAK_FOR="unused_m",
                     R6C_ERROR_FOR="whole")
        self.assertIn("** Error: no visible declaration for R6C_NO_SUCH_SIGNAL", c.stdout)
        self.assertNotIn("iverilog-sv2ghdl: note:", c.stdout)

    def test_hazard3s_unused_onehot_module(self):
        # the hazard3/vamos case (problems 1 and 3 of the test case): the compile works and
        # prints no "** Error"
        c = self.vcs("t_unused.v", T_ONEHOT_UNUSED)
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertNotIn("** Error", c.stdout)
        if os.path.exists(os.path.join(self.tmp, "simv.daidir", "nvc", "_mods_analysis.log")):
            self.assertIn("iverilog-sv2ghdl: note: module onehot_dyn, which the design does not use",
                          c.stdout)
        self.assertIn("has=01", self.simv().stdout)

    def test_ivtest_flow_stays_silent(self):
        # without vamos (the ivtest harness compares the output with gold files) the note goes
        # to iverilog.log only
        self.write("t_top.v", T_UNUSED)
        e = self.env(IVERILOG=self.fake, R6C_BREAK_FOR="unused_m")
        e.pop("VAMOS_STACK", None)
        r = run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "out", "t_top.v"], cwd=self.tmp, env=e)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("** Error", r.stdout)
        self.assertNotIn("note:", r.stdout)
        self.assertIn("iverilog-sv2ghdl: note: module unused_m, which the design does not use",
                      self.read(os.path.join("out", "iverilog.log")))


# =============================================================================
# R6C-03, R6C-07, R6C-08: the regress harness
# =============================================================================

HARNESS = os.environ.get("R6C_REGRESS_DIR") or os.path.join(ROOT, "regress")
IVTEST = os.environ.get("R6C_IVTEST") or "/usr/local/src/iverilog/ivtest"

LISTS = {
    "regress-ivl1.list": "t_ivl1  normal  ivltests\n",
    "regress-vlg.list": "t_vlg  normal  ivltests\nt_both  normal  ivltests\n",
    "regress-sv.list": "t_sv  normal,-g2012  ivltests\nt_both  normal,-gBOTH_SV  ivltests\n",
    "regress-vhdl.list": "t_vhdl  normal,-g2005-sv,ivltests/t_vhdl.vhd  ivltests\n",
    "regress-synth.list": "t_synth  normal  ivltests\nv99:t_v99  normal  ivltests\n",
    "regress-fsv.list": "t_fsv  normal  ivltests\n",
    "regress-vvp.list": "t_json  vvp_tests/t_json.json\n",
}
ALL_TESTS = ["t_ivl1", "t_vlg", "t_both", "t_sv", "t_vhdl", "t_synth"]
MAKE_VARS = ("MAKE", "MAKEFLAGS", "MAKELEVEL", "SMAK_JOB_SERVER", "SMAK_INVOKED_AS")

# records each compile: cwd | arguments | the make/smak variables it sees
FAKE_IVERILOG = """\
#!/bin/bash
if [ "$1" = "-V" ]; then echo "Icarus Verilog version 13.0 (devel) (r6c fake)"; exit 0; fi
echo "$PWD|$*|MAKE=${MAKE-}|MAKEFLAGS=${MAKEFLAGS-}|MAKELEVEL=${MAKELEVEL-}|SMAK_JOB_SERVER=${SMAK_JOB_SERVER-}|SMAK_INVOKED_AS=${SMAK_INVOKED_AS-}" >> "$R6C_FAKE_LOG"
out=a.out; prev=""
for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
echo "entity t_top is" > "$out"
exit 0
"""
FAKE_VVP = "#!/bin/bash\necho PASSED\n"
FAKE_NVC = "#!/bin/bash\nfor a in \"$@\"; do [ \"$a\" = -r ] && echo PASSED; done\nexit 0\n"
# A smak that fails as a broken one does: it runs only the targets in $R6C_SMAK_TARGETS
# (with GNU make, exporting a smak's MAKE and job server variables to them), may leave a
# process behind ($R6C_SMAK_MARKER, its name), and exits $R6C_SMAK_RC (3)
FAKE_SMAK = """\
#!/bin/bash
mk=""; j=""
while [ $# -gt 0 ]; do case "$1" in -f) mk="$2"; shift 2;; -j*) j="$1"; shift;; *) shift;; esac; done
echo "fake smak: -f $mk"
if [ -n "$R6C_SMAK_MARKER" ]; then bash -c "exec -a $R6C_SMAK_MARKER sleep 300" > /dev/null 2>&1 & fi
env MAKE=/fake/smak SMAK_JOB_SERVER=127.0.0.1:1 SMAK_INVOKED_AS=/fake/smak make $j -f "$mk" $R6C_SMAK_TARGETS
echo "Task 1 FAILED: the fake smak's failure"
exit ${R6C_SMAK_RC:-3}
"""


def _harness_problem() -> str:
    if not os.path.isfile(os.path.join(HARNESS, "regress")):
        return "no regress harness at %s" % HARNESS
    if not (os.path.isfile(os.path.join(IVTEST, "vvp_reg.pl"))
            and os.path.isdir(os.path.join(IVTEST, "perl-lib"))):
        return "no ivtest runner at %s" % IVTEST
    if not shutil.which("bash") or not shutil.which("perl") or not shutil.which("make"):
        return "needs bash, perl and make"
    try:
        r = subprocess.run(["perl", "-MDBI", "-MDBD::SQLite", "-MStorable", "-e", "1"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError:
        return "needs perl"
    return "" if r.returncode == 0 else "needs perl with DBI and DBD::SQLite"


_PROBLEM = _harness_problem()


@unittest.skipIf(_PROBLEM, _PROBLEM)
class TestRegressHarness(TempDir):
    """The harness on a fake ivtest tree: the real vvp_reg.pl and perl-lib, small lists,
    an iverilog that records its command lines and a vvp that prints PASSED."""

    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "root")
        self.iv = os.path.join(self.root, "iverilog", "ivtest")
        os.makedirs(os.path.join(self.iv, "ivltests"))
        shutil.copy(os.path.join(IVTEST, "vvp_reg.pl"), self.iv)
        shutil.copytree(os.path.join(IVTEST, "perl-lib"), os.path.join(self.iv, "perl-lib"))
        for name, text in LISTS.items():
            self.write(os.path.join("root", "iverilog", "ivtest", name), text)
        for t in ALL_TESTS + ["t_v99", "t_fsv", "t_json"]:
            self.write(os.path.join("root", "iverilog", "ivtest", "ivltests", t + ".v"), "module m; endmodule\n")
        self.bin = os.path.join(self.tmp, "bin")
        for name, text in (("iverilog", FAKE_IVERILOG), ("vvp", FAKE_VVP), ("nvc", FAKE_NVC)):
            p = self.write(os.path.join("bin", name), text)
            os.chmod(p, 0o755)
        self.smakbin = os.path.join(self.tmp, "smakbin")
        p = self.write(os.path.join("smakbin", "smak"), FAKE_SMAK)
        os.chmod(p, 0o755)
        # a copy of the harness, so its out/ lands here
        self.h = os.path.join(self.tmp, "harness")
        os.makedirs(self.h)
        shutil.copy(os.path.join(HARNESS, "regress"), self.h)
        shutil.copytree(os.path.join(HARNESS, "lib"), os.path.join(self.h, "lib"))
        self.fake_log = os.path.join(self.tmp, "fake.log")
        self.marker = "r6c_leftover_%d_%d" % (os.getpid(), int(time.time() * 1000) % 100000)

    def tearDown(self):
        subprocess.run(["pkill", "-f", self.marker], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        super().tearDown()

    def regress(self, *args: str, db: Optional[str] = "r.db", env: Optional[dict] = None,
                smak: bool = False) -> subprocess.CompletedProcess:
        e = dict(os.environ)
        for k in ("REGRESS_DB", "REGRESS_IVTEST_SHARED", "REGRESS_IVTEST_FILTER") + MAKE_VARS:
            e.pop(k, None)
        path = self.bin + os.pathsep + e.get("PATH", "")
        e.update(SV2GHDL_SRC_ROOT=self.root, IVERILOG=os.path.join(self.bin, "iverilog"),
                 VVP=os.path.join(self.bin, "vvp"), IVERILOG_STEVE=os.path.join(self.bin, "iverilog"),
                 VVP_STEVE=os.path.join(self.bin, "vvp"), R6C_FAKE_LOG=self.fake_log,
                 PATH=(self.smakbin + os.pathsep + path) if smak else path)
        e.update(env or {})
        cmd = ["perl", os.path.join(self.h, "regress")] + list(args)
        if db:
            cmd += ["--db", os.path.join(self.tmp, db)]
        return subprocess.run(cmd, cwd=self.tmp, env=e, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, universal_newlines=True, timeout=600)

    def compiles(self) -> List[Tuple[str, str, str, Dict[str, str]]]:
        """(test, cwd, iverilog arguments, the make variables it saw) per compile, in order."""
        if not os.path.exists(self.fake_log):
            return []
        out = []
        with open(self.fake_log) as fh:
            for line in fh:
                cwd, args, *env = line.rstrip("\n").split("|")
                m = re.search(r"\./ivltests/(\w+)\.v\b", args)
                out.append((m.group(1) if m else "?", cwd, args,
                            dict(kv.split("=", 1) for kv in env)))
        return out

    def db(self, sql: str, args: Sequence = (), db: str = "r.db") -> List[Tuple]:
        c = sqlite3.connect(os.path.join(self.tmp, db))
        try:
            return list(c.execute(sql, tuple(args)))
        finally:
            c.close()

    def results(self, db: str = "r.db") -> List[Tuple[str, str, str, str]]:
        rid = self.db("SELECT MAX(run_id) FROM run", db=db)[0][0]
        return self.db("SELECT r.test_name, r.status, r.message, r.log_path FROM result r JOIN "
                       "block_run b USING (block_run_id) WHERE b.run_id=? ORDER BY r.test_name",
                       (rid,), db=db)

    def workdir(self, out: str) -> str:
        m = re.search(r"^  workdir: (\S+)$", out, re.M)
        self.assertIsNotNone(m, out)
        return m.group(1)

    # -- R6C-03 ---------------------------------------------------------------------------

    def test_a_filter_that_selects_nothing_is_an_error(self):
        # before: _filtered_list returned no list and the runner ran its whole suite
        r = self.regress("run", "ivtest/iverilog", "--seq", "--filter", "zz_no_such_test")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("--filter 'zz_no_such_test' selects none of the", r.stdout)
        self.assertEqual(self.compiles(), [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "r.db")))   # no run recorded

    def test_a_filtered_run_reads_the_runners_own_lists(self):
        # before: regress-vhdl.list was missing (t_vhdl: whole suite), regress-synth.list
        # was read without -S, and t_both came from regress-sv.list (the lists' order)
        r = self.regress("run", "ivtest/iverilog", "--seq", "--filter", "^t_(vhdl|synth|both)$")
        self.assertEqual(r.returncode, 0, r.stdout)
        got = {t: args for t, _, args, _ in self.compiles()}
        self.assertEqual(sorted(got), ["t_both", "t_synth", "t_vhdl"], r.stdout)
        self.assertIn("ivltests/t_vhdl.vhd", got["t_vhdl"])
        self.assertIn("-S", got["t_synth"].split())
        self.assertNotIn("-gBOTH_SV", got["t_both"])       # regress-vlg.list comes first
        self.assertEqual([(t, s) for t, s, _, _ in self.results()],
                         [("t_both", "pass"), ("t_synth", "pass"), ("t_vhdl", "pass")])

    def test_a_filter_the_runner_finds_nothing_for_is_an_error_row(self):
        # v99:t_v99 is for another version: the runner skips it
        r = self.regress("run", "ivtest/iverilog", "--seq", "--filter", "^t_v99$")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.compiles(), [])
        rows = self.results()
        self.assertEqual([(t, s) for t, s, _, _ in rows], [("ivtest/iverilog", "error")], r.stdout)
        self.assertIn("selects none of the", rows[0][2])

    def test_copies_of_a_db_get_their_own_work_dirs(self):
        self.assertEqual(self.regress("run", "ivtest/iverilog", "--seq", "--filter", "^t_ivl1$")
                         .returncode, 0)
        for db in ("b.db", "c.db"):
            shutil.copy(os.path.join(self.tmp, "r.db"), os.path.join(self.tmp, db))
        for db, test in (("b.db", "t_vlg"), ("c.db", "t_sv")):
            r = self.regress("run", "ivtest/iverilog", "--seq", "--filter", "^%s$" % test, db=db)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("Run #2 ", r.stdout)                 # both copies hand out run 2
        logs = {}
        for db, test in (("b.db", "t_vlg"), ("c.db", "t_sv")):
            (name, _, _, log), = self.results(db)
            self.assertEqual(name, test)
            with open(log) as fh:
                self.assertIn(test + ":", fh.read())          # its own log, not the other's
            logs[db] = log
        self.assertNotEqual(os.path.dirname(logs["b.db"]), os.path.dirname(logs["c.db"]))

    def test_the_runner_works_in_a_private_area(self):
        r = self.regress("run", "ivtest/iverilog", "--seq", "--filter", "^t_ivl1$")
        self.assertEqual(r.returncode, 0, r.stdout)
        (_, cwd, _, _), = self.compiles()
        self.assertTrue(cwd.startswith(os.path.join(os.path.realpath(self.h), "out", "run-1-")), cwd)
        self.assertTrue(cwd.endswith("/ivtest-ivtest_iverilog"), cwd)
        for leftover in ("log", "work", "vsim", "regression_report.txt"):
            self.assertFalse(os.path.exists(os.path.join(self.iv, leftover)), leftover)
        self.assertTrue(os.path.isfile(os.path.join(cwd, "log", "t_ivl1.log")))
        # REGRESS_IVTEST_SHARED=1: in the ivtest tree itself, as before
        os.remove(self.fake_log)
        r = self.regress("run", "ivtest/iverilog", "--seq", "--filter", "^t_ivl1$",
                         env={"REGRESS_IVTEST_SHARED": "1"})
        self.assertEqual(r.returncode, 0, r.stdout)
        (_, cwd, _, _), = self.compiles()
        self.assertEqual(os.path.realpath(cwd), os.path.realpath(self.iv))

    def test_a_dollar_filter_through_the_dispatch_makefile(self):
        # before: make expanded $' in '^t_ivl1$': "Unterminated quoted string", no results
        r = self.regress("run", "ivtest/iverilog", "--make", "--filter", "^t_ivl1$")
        self.assertEqual([(t, s) for t, s, _, _ in self.results()], [("t_ivl1", "pass")], r.stdout)

    def test_the_vhdl_runner_finds_its_log_dir(self):
        # vhdl_nvc_reg.pl writes log/<test>.log but never makes log/ (vvp_reg.pl does): in a
        # fresh work area every test failed ("cannot create log/...") and nothing parsed
        shutil.copy(os.path.join(IVTEST, "vhdl_nvc_reg.pl"), self.iv)
        self.write(os.path.join("root", "iverilog", "ivtest", "vhdl_regress.list"),
                   "t_vh  normal  ivltests\n")
        self.write(os.path.join("root", "iverilog", "ivtest", "ivltests", "t_vh.v"), "module m; endmodule\n")
        t0 = time.time()
        r = self.regress("run", "ivtest/nvc-vhdl", "--seq",
                         env={"NVC": os.path.join(self.bin, "nvc"), "NVC_LIBDIR": self.tmp})
        self.assertEqual([(t, s) for t, s, _, _ in self.results()], [("t_vh", "pass")], r.stdout)
        # the block's 60 s timeout watchdogs end with their tools: the wrapper around
        # `iverilog -V`, which the runner reads through a pipe, held it for 60 s
        self.assertLess(time.time() - t0, 40)

    def test_run_one_finds_the_runs_work_dir(self):
        self.assertEqual(self.regress("run", "ivtest/iverilog", "--seq", "--filter", "^t_ivl1$")
                         .returncode, 0)
        (_, _, _, log), = self.results()
        r = self.regress("run-one", "ivtest/iverilog", "--run", "1", "--filter", "^t_sv$")
        self.assertEqual(r.returncode, 0, r.stdout)
        with open(log) as fh:
            self.assertIn("t_sv:", fh.read())                  # the run's own logs/ dir

    # -- R6C-07: a failed smak dispatch ----------------------------------------------------

    BLOCKS = ("ivtest/iverilog", "ivtest/iverilog-steve")

    def smak_run(self, targets: str, **env) -> subprocess.CompletedProcess:
        e = {"R6C_SMAK_TARGETS": targets}
        e.update(env)
        # (no '$' in the filter: the make quoting of one is test_a_dollar_filter_...'s)
        return self.regress("run", *self.BLOCKS, "--filter", "t_ivl1", "--notes", "r6c",
                            env=e, smak=True)

    def test_a_failed_smak_dispatch_keeps_the_finished_blocks(self):
        # before: "clearing partials and falling back to make": both blocks ran again
        r = self.smak_run("blk0")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual([t for t, _, _, _ in self.compiles()], ["t_ivl1", "t_ivl1"], r.stdout)
        rows = self.db("SELECT block, finished_at IS NOT NULL FROM block_run ORDER BY block_run_id")
        self.assertEqual(rows, [("ivtest/iverilog", 1), ("ivtest/iverilog-steve", 1)])
        (notes,), = self.db("SELECT notes FROM run")
        self.assertRegex(notes, r"^r6c \[smak dispatch failed \(exit 3; 1 of 2 block\(s\) without a "
                                r"finished result; Task 1 FAILED: the fake smak's failure; see \S+"
                                r"/logs/dispatch-smak\.log\); blocks re-run under GNU make: "
                                r"ivtest/iverilog-steve\]$")
        self.assertIn("regress run: smak dispatch failed (exit 3", r.stdout)
        self.assertIn("dispatch: make -j8 -f run-make.mk", r.stdout)
        wd = self.workdir(r.stdout)
        with open(os.path.join(wd, "logs", "dispatch-smak.log")) as fh:
            self.assertIn("Task 1 FAILED", fh.read())
        with open(os.path.join(wd, "run-make.mk")) as fh:
            mk = fh.read()
        self.assertIn("'ivtest/iverilog-steve'", mk)
        self.assertNotIn("'ivtest/iverilog'", mk)

    def test_a_failed_smak_dispatch_that_finished_everything_reruns_nothing(self):
        r = self.smak_run("all")
        self.assertEqual(len(self.compiles()), 2, r.stdout)
        (notes,), = self.db("SELECT notes FROM run")
        self.assertIn("blocks re-run under GNU make: none", notes)
        self.assertNotIn("dispatch: make", r.stdout)

    def test_a_smak_dispatch_that_skips_a_block_is_failed(self):
        # exit 0, but a block never ran: it runs under GNU make
        r = self.smak_run("blk0", R6C_SMAK_RC="0")
        self.assertEqual([t for t, _, _, _ in self.compiles()], ["t_ivl1", "t_ivl1"], r.stdout)
        (notes,), = self.db("SELECT notes FROM run")
        self.assertIn("smak dispatch failed (exit 0; 1 of 2 block(s) without a finished result", notes)

    def test_the_dispatchers_leftovers_are_ended(self):
        # before: a process the smak dispatch left (its job server) ran on after regress
        # (and one that held regress's output kept a caller reading it waiting)
        r = self.smak_run("all", R6C_SMAK_MARKER=self.marker)
        self.assertEqual(r.returncode, 0, r.stdout)
        left = subprocess.run(["pgrep", "-f", self.marker], stdout=subprocess.PIPE,
                              universal_newlines=True).stdout.split()
        self.assertEqual(left, [], "left running: %s" % left)

    # -- R6C-08: a clean make environment --------------------------------------------------

    def test_blocks_run_without_the_smak_dispatchers_make_variables(self):
        # before: MAKE=smak and SMAK_JOB_SERVER reached every build a block started (a
        # recursive smak there joined the dispatcher's job server and hung)
        r = self.smak_run("all")
        envs = [e for _, _, _, e in self.compiles()]
        self.assertEqual(len(envs), 2, r.stdout)
        for e in envs:
            self.assertEqual({k: e[k] for k in MAKE_VARS}, {k: "" for k in MAKE_VARS})

    def test_blocks_run_without_gnu_makes_job_server(self):
        r = self.regress("run", *self.BLOCKS, "--make", "--filter", "t_ivl1")
        envs = [e for _, _, _, e in self.compiles()]
        self.assertEqual(len(envs), 2, r.stdout)
        for e in envs:
            self.assertEqual({k: e[k] for k in MAKE_VARS}, {k: "" for k in MAKE_VARS})

    def test_a_sequential_run_drops_a_callers_make_variables(self):
        r = self.regress("run", "ivtest/iverilog", "--seq", "--filter", "t_ivl1",
                         env={"MAKEFLAGS": "-j3 --jobserver-auth=fifo:/nonexistent", "MAKELEVEL": "1",
                              "MAKE": "make", "SMAK_JOB_SERVER": "127.0.0.1:1"})
        (_, _, _, e), = self.compiles()
        self.assertEqual({k: e[k] for k in MAKE_VARS}, {k: "" for k in MAKE_VARS}, r.stdout)

    @unittest.skipUnless(shutil.which("smak"), "needs smak")
    def test_the_real_smak_dispatch(self):
        r = self.regress("run", *self.BLOCKS, "--filter", "^t_ivl1$")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("dispatch: smak", r.stdout)
        self.assertNotIn("smak dispatch failed", r.stdout)
        self.assertEqual([(t, s) for t, s, _, _ in self.results()],
                         [("t_ivl1", "pass"), ("t_ivl1", "pass")], r.stdout)


# =============================================================================
# R6C-04: neutral names, no stale load-order comments
# =============================================================================

class TestNamesAndComments(unittest.TestCase):

    def read(self, *parts: str) -> str:
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def assertNoMatch(self, text: str, rx: str, where: Sequence[str]) -> None:
        m = re.search(rx, text)
        if m:
            self.fail("%s: %r at line %d" % (os.path.join(*where), m.group(0),
                                             text.count("\n", 0, m.start()) + 1))

    def test_the_xyce_side_runner_has_no_work_item_names(self):
        parts = ("tests", "vamos", "fixtures", "ams", "cside_xyce", "run_cside_xyce.py")
        text = self.read(*parts)
        found = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Name):
                found.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                found.add(node.name)
            elif isinstance(node, ast.Attribute):
                found.add(node.attr)
        self.assertEqual(sorted(n for n in found if re.search(r"(?i)(^|_)e[12](_|$|ctx)", n)), [])
        self.assertTrue({"NVC_RUNNER", "NVC_SIDE", "nvc_env", "nvc_ctx", "_runner_fail"} <= found)
        self.assertNoMatch(text, r"\bE[12]\b", parts)
        self.assertNoMatch(self.read("tests", "vamos", "test_cside_xyce.py"), r"\bE[12]\b",
                           ("tests", "vamos", "test_cside_xyce.py"))

    def test_no_load_order_claim_about_random(self):
        for parts in (("vamos", "backends", "nvc.py"), ("bin", "vvp-sv2ghdl")):
            text = self.read(*parts)
            self.assertNoMatch(text, r"first (loaded library|library loaded) wins", parts)
            self.assertNoMatch(text, r"both export the \$random", parts)
            self.assertNoMatch(text, r"\$random generator \(sv_random\)", parts)
        parts = ("tests", "vamos", "test_repair_e2e.py")
        self.assertNoMatch(self.read(*parts), r"keeps the resolver's generator", parts)


if __name__ == "__main__":
    unittest.main()
