"""vamos.ams.vhdl: reading design.vhd as tgt-vhdl writes it (docs/VAMOS_AMS_DESIGN.md §5.3).

The fixtures under tests/vamos/fixtures/vhdl/cut_*/ were translated in WSL by
iverilog-sv2ghdl (fixtures/vhdl/cut_regen.sh); design_t2.vhd / design_t3.vhd
are hand-edited copies in the form translator patches T2/T3 produce.

    python3 -m unittest discover -s tests/vamos -p 'test_ams_vhdl.py' -v
"""

import os
import shutil
import tempfile
import unittest

from vamos_testlib import ROOT, fixture  # noqa: F401  (also puts ROOT on sys.path)

from vamos.ams import sv2vhdl_modes, vhdl  # noqa: E402
from vamos.notes import NoteError  # noqa: E402

NVC_SV2VHDL = os.environ.get("VAMOS_NVC_SRC", "/usr/local/src/nvc") + "/lib/sv2vhdl"


def design(case, name="design.vhd"):
    return vhdl.parse(fixture("vhdl", "cut_" + case, name))


def stmt(arch, label):
    for s in arch.stmts:
        if s.label == label:
            return s
    raise AssertionError("no statement %s in %s" % (label, arch.entity))


class TestNaming(unittest.TestCase):
    def test_make_safe_name(self):
        cases = {"in": "in_sig", "out": "out_sig", "OUT": "OUT_sig", "signal": "signal_sig",
                 "bus": "bus_sig", "open": "open_sig", "_a": "sig_a", "b_": "b_sig",
                 "c__d": "c_d", "inv": "inv", "In": "In_sig"}
        for v, h in cases.items():
            self.assertEqual(vhdl.make_safe_name(v), h, v)
        self.assertEqual(vhdl.make_safe_name("inv", entity_collision=True), "inv_sig")

    def test_safe_name_matches(self):
        self.assertTrue(vhdl.safe_name_matches("inv_sig", "inv"))       # entity collision
        self.assertTrue(vhdl.safe_name_matches("INV", "inv"))
        self.assertTrue(vhdl.safe_name_matches("w_out_1", "w_out"))     # case-only clash
        self.assertTrue(vhdl.safe_name_matches("sig_a", "_a"))
        self.assertFalse(vhdl.safe_name_matches("y", "a"))
        self.assertFalse(vhdl.safe_name_matches("in", "in"))           # reserved: in_sig

    def test_valid_entity_name(self):
        for m, e in (("buffer", "buffer_module"), ("my__cell", "my_cell"), ("cell_", "cell_module"),
                     ("_cell", "module_cell"), ("register", "register_module"), ("inv", "inv")):
            self.assertEqual(vhdl.valid_entity_name(m), e)


class TestEntities(unittest.TestCase):
    def test_provenance_and_ports(self):
        d = design("ports")
        e = d.entity("rw__c86a")
        self.assertEqual(e.module, "rw")
        self.assertTrue(e.module_file.endswith("_norm.sv"))
        self.assertEqual([p.name for p in e.ports],
                         ["in_sig", "out_sig", "signal_sig", "bus_sig", "open_sig", "sig_a", "b_sig",
                          "c_d", "inv_sig"])
        self.assertEqual([p.mode for p in e.ports],
                         ["in", "out", "in", "in", "out", "in", "inout", "out", "out"])
        self.assertEqual(e.port("b_sig").type.mark, "resolved_logic3d")
        self.assertTrue(e.port_clause.startswith("port (") and e.port_clause.endswith(");"))
        # the port named after module inv becomes inv_sig only in the variant
        self.assertEqual(d.entity("rw").ports[-1].name, "inv")

    def test_renamed_cells_keep_their_module(self):
        d = design("ports")
        for ent, mod in (("buffer_module__f8f0", "buffer"), ("block_module__f8f0", "block"),
                         ("register_module__f8f0", "register"), ("my_cell__f8f0", "my__cell"),
                         ("cell_module__f8f0", "cell_"), ("module_cell__f8f0", "_cell")):
            self.assertEqual(d.entity(ent).module, mod)

    def test_params_and_ranges(self):
        d = design("dac")
        e = d.entity("flash1__8c78")
        self.assertEqual(e.module, "flash")
        self.assertEqual(e.params, {"N": "8"})
        self.assertEqual(e.param_comments, {"N": "8"})
        q = e.port("q")
        self.assertTrue(q.type.vector)
        self.assertEqual((q.type.rng.left, q.type.rng.right, q.type.width), (7, 0, 8))
        self.assertEqual([x.name for x in d.variants("pio_sp")],
                         ["pio_sp", "pio_sp1__01a1", "pio_sp__efe1"])

    def test_deferred_stub(self):
        text = ("library ieee;\nuse ieee.std_logic_1164.all;\n\n"
                "-- Generated from Verilog module tb (x/_norm.sv:3)\n"
                "entity tb is\n    -- sv2vhdl:deferred source=x/_norm.sv module=tb\n"
                "  end entity;\n  architecture stub of tb is begin end architecture;\n")
        d = vhdl.parse_text(text, "stub.vhd")
        self.assertTrue(d.entity("tb").deferred)
        self.assertEqual(d.entity("tb").ports, [])

    def test_param_lines_of_every_type(self):
        text = ("-- Generated from Verilog module m (x/_norm.sv:3)\n--   GAIN = 0.9\n"
                '--   MODE = "slow"\n--   N = 8\n--   A$B = 1e-09\n'
                "entity m__f8f0 is\n  attribute nvc_verilog_params : string;\n"
                '  attribute nvc_verilog_params of m__f8f0 : entity is "N=8";\nend entity;\n')
        e = vhdl.parse_text(text, "x.vhd").entity("m__f8f0")
        self.assertEqual(e.param_comments, {"GAIN": "0.9", "MODE": '"slow"', "N": "8", "A$B": "1e-09"})
        self.assertEqual(e.params, {"N": "8"})


# sv2vhdl-modules' output for: a_wrap #(real G = 0.25) holding mvcell #(.GAIN(G)), and tb
# instantiating a_wrap #(.G(0.9)) (three runs: a_wrap, mvcell, tb; deferred stubs first)
MODS = """\
-- Deferred compilation stub for module dead
entity dead is
  -- sv2vhdl:deferred source=x/_norm.sv module=dead
end entity;

-- This VHDL was converted from Verilog using the
-- Icarus Verilog VHDL Code Generator 13.0 (devel) (6029f3dfb)

library ieee;
-- Generated from Verilog module mvcell (x/_norm.sv:21)
--   GAIN = 0.25
entity mvcell__f8f0 is
  port (
    a : in logic3d
  );
end entity;

-- Generated from Verilog module mvcell (x/_norm.sv:21)
--   GAIN = 0.25
architecture from_verilog of mvcell__f8f0 is
begin
end architecture;

library ieee;
-- Generated from Verilog module a_wrap (x/_norm.sv:5)
--   G = 0.25
entity a_wrap is
end entity;

-- This VHDL was converted from Verilog using the
-- Icarus Verilog VHDL Code Generator 13.0 (devel) (6029f3dfb)

-- Generated from Verilog module mvcell (x/_norm.sv:21)
--   GAIN = 0.25
entity mvcell is
end entity;

-- This VHDL was converted from Verilog using the
-- Icarus Verilog VHDL Code Generator 13.0 (devel) (6029f3dfb)

-- Generated from Verilog module mvcell (x/_norm.sv:21)
--   GAIN = 0.9
entity mvcell__f8f0 is
end entity;

-- Generated from Verilog module a_wrap (x/_norm.sv:5)
--   G = 0.9
entity a_wrap__e744 is
end entity;

-- Generated from Verilog module a_wrap (x/_norm.sv:5)
--   G = 0.9
architecture from_verilog of a_wrap__e744 is
begin
  u: entity work.mvcell__f8f0
    port map (
      a => a
    );
end architecture;

-- Generated from Verilog module tb (x/_norm.sv:8)
entity tb is
end entity;
"""


class TestTranslationRuns(unittest.TestCase):
    def test_runs(self):
        runs = vhdl.translation_runs(MODS)
        self.assertEqual([[(e.name, e.module, e.params) for e in r] for r in runs], [
            [("dead", None, {})],
            [("mvcell__f8f0", "mvcell", {"GAIN": "0.25"}), ("a_wrap", "a_wrap", {"G": "0.25"})],
            [("mvcell", "mvcell", {"GAIN": "0.25"})],
            [("mvcell__f8f0", "mvcell", {"GAIN": "0.9"}), ("a_wrap__e744", "a_wrap", {"G": "0.9"}),
             ("tb", "tb", {})]])

    def test_read_skips_the_whole_design_run(self):
        """Not when design.vhd came from iverilog-sv2ghdl's whole-design run (its
        IVERILOG_BACKEND=1 in _metadata.tmp, where stage 1a writes SV2VHDL_MODULES=1)."""
        tmp = tempfile.mkdtemp(prefix="vamos-test-")
        try:
            self.assertIsNone(vhdl.read_translation_runs(tmp))
            with open(os.path.join(tmp, "_mods.vhd"), "w") as fh:
                fh.write(MODS)
            with open(os.path.join(tmp, "_metadata"), "w") as fh:
                fh.write('TOP_ENTITY="tb"\nNVC_STD="2040"\n')
            with open(os.path.join(tmp, "_metadata.tmp"), "w") as fh:
                fh.write("SV2VHDL_MODULES=1\n")
            runs = vhdl.read_translation_runs(tmp)
            self.assertEqual([r[-1].name for r in runs], ["dead", "a_wrap", "mvcell", "tb"])
            with open(os.path.join(tmp, "_metadata.tmp"), "w") as fh:
                fh.write("IVERILOG_BACKEND=1\n")
            self.assertIsNone(vhdl.read_translation_runs(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestArchitectures(unittest.TestCase):
    def test_signal_comments(self):
        d = design("readable")
        a = d.arch("wrap_a__a8b4")
        self.assertTrue(a.signals["vo_readable"].readable_shadow)
        self.assertTrue(a.signals["vo_readable"].temporary)
        tb = d.arch("tb")
        self.assertEqual(tb.signals["clk"].declared_at[1], 7)
        self.assertFalse(tb.signals["clk"].temporary)
        v = design("vec").arch("tb")
        self.assertTrue(v.signals["lpm_q_ivl_20"].temporary)
        self.assertTrue(v.signals["tmp_ivl_16"].created_at.endswith("_norm.sv:20"))

    def test_instances(self):
        d = design("shared")
        tb = d.arch("tb")
        u1 = stmt(tb, "u1")
        self.assertEqual((u1.kind, u1.lib, u1.entity), ("instance", "work", "rc_sp__f8f0"))
        self.assertEqual([(a.formal.name, a.actual[0].name) for a in u1.assocs],
                         [("a", "clk"), ("y", "yb")])
        self.assertTrue(u1.origin.endswith("_norm.sv:11"))
        self.assertEqual(d.text[u1.ent_span[0]:u1.ent_span[1]], "rc_sp__f8f0")
        self.assertTrue(d.text[u1.span[0]:u1.span[1]].startswith("u1: entity work.rc_sp__f8f0"))
        self.assertTrue(d.text[u1.span[0]:u1.span[1]].endswith(";"))
        self.assertIsNone(u1.vpath)

    def test_library_instances(self):
        tb = design("prims").arch("tb")
        self.assertEqual(stmt(tb, "sv_pullup_ivl_1").strength, ("supply", "supply"))
        self.assertEqual(stmt(tb, "sv_pullup_ivl_3_0_0_inst").strength, ("pull", "pull"))
        self.assertEqual(stmt(tb, "sv_pullup_p1_0_0_inst").strength, ("supply", "highz"))
        sb = stmt(tb, "sv_strength_buf_ivl_4")
        self.assertEqual((sb.lib, sb.entity, sb.generics), ("sv2vhdl", "sv_strength_buf",
                                                            {"str1": "2", "str0": "2"}))
        nand = stmt(tb, "sv_nand_g1_0_0_inst")
        self.assertEqual([r.name for r in nand.assoc("a").actual], ["a", "b"])

    def test_assignments(self):
        tb = design("vec").arch("tb")
        fused = stmt(tb, "comb_fused_1")
        self.assertTrue(fused.fused)
        got = [(x.target.name, x.op, [(r.name, r.indices) for r in x.copy]) for x in fused.assigns]
        self.assertEqual(got, [("tmp_ivl_16", ":=", [("code", (0,))]),
                               ("tmp_ivl_18", ":=", [("code", (1,))]),
                               ("LPM_q_ivl_20", ":=", [("tmp_ivl_16", None), ("tmp_ivl_18", None)])])
        slice_copy = [s for s in tb.stmts if s.kind == "process" and s.assigns
                      and s.assigns[0].target.name == "LPM_q_ivl_21"][0]
        self.assertTrue(slice_copy.simple)
        self.assertEqual(slice_copy.assigns[0].copy[0].indices, (3, 2))   # code(2 + 1 downto 2)
        z = [x for x in stmt(tb, "comb_fused_0").assigns if x.target.name == "tmp_ivl_32"][0]
        self.assertTrue(z.z_only and z.const)

    def test_conditions_force_and_sensitivity(self):
        tb = design("bidir").arch("tb")
        tri = [s for s in tb.stmts if s.kind == "process" and len(s.assigns) == 2
               and s.assigns[0].target.name == "pad"][0]
        self.assertFalse(tri.simple)
        self.assertEqual([r.name for r in tri.cond_reads], ["en"])
        self.assertEqual({r.name for r in tri.reads}, {"en", "d", "tmp_ivl_0"})
        f = design("force").arch("tb")
        ops = [x.op for s in f.stmts for x in s.assigns if x.target.name == "n"]
        self.assertEqual(ops, ["<=", "force", "release"])
        sh = design("shared").arch("tb")
        disp = [s for s in sh.stmts if s.kind == "process" and s.sens == ["yb"]][0]
        self.assertEqual([r.name for r in disp.sens_reads], ["yb"])

    def test_port_buffers(self):
        # cut_portbuf: tgt-vhdl's port buffers PB_<label>_<port> (input ports the core buffers)
        tb = design("portbuf").arch("tb")
        pbs = {n: sd.port_buffer for n, sd in tb.signals.items() if sd.port_buffer}
        self.assertEqual(pbs, {"pb_%s_a" % k: ("a", k)
                               for k in ("wb", "wd", "we", "wo", "wr", "wv", "wx")})
        we = tb.signals["pb_we_a"]
        self.assertTrue(we.temporary)
        self.assertFalse(we.readable_shadow)
        self.assertIsNone(we.created_at)
        self.assertEqual(we.type.mark, "resolved_logic3d")
        wv = tb.signals["pb_wv_a"]
        self.assertEqual((wv.type.mark, wv.type.width), ("resolved_logic3d_vector", 2))
        self.assertIsNone(tb.signals["clk"].port_buffer)
        self.assertIsNone(tb.signals["tmp_ivl_1"].port_buffer)
        # the copies (a process each) and the associations
        copies = {s.assigns[0].target.name: s for s in tb.stmts if s.kind == "process"
                  and len(s.assigns) == 1 and s.assigns[0].target.name.startswith("PB_")}
        self.assertEqual(sorted(copies), ["PB_wb_a", "PB_wd_a", "PB_we_a", "PB_wo_a", "PB_wr_a",
                                          "PB_wv_a", "PB_wx_a"])
        for name, src in (("PB_we_a", "clk"), ("PB_wb_a", "tmp_ivl_1"), ("PB_wv_a", "rv"),
                          ("PB_wx_a", "tmp_ivl_2")):
            s = copies[name]
            self.assertTrue(s.simple, name)
            self.assertEqual([(r.name, r.indices) for r in s.assigns[0].copy], [(src, None)], name)
        self.assertEqual([(a.formal.name, a.actual[0].name) for a in stmt(tb, "we").assocs],
                         [("a", "PB_we_a"), ("y", "ve")])
        # the wrapper's port is inout (the core's buffer), the cell's port as declared
        d = design("portbuf")
        self.assertEqual(d.entity(stmt(tb, "we").entity).port("a").mode, "inout")

    def test_t2_aliases_and_t3_comments(self):
        d = design("swvp", "design_t2.vhd")
        tb = d.arch("tb")
        al = tb.aliases["sw_ivl_0_b_g1"]
        self.assertEqual((al.target.name, al.target.indices), ("pbus", (1,)))
        self.assertEqual(stmt(tb, "u_g1").vpath, "ring[1].u")
        v = design("vec", "design_t3.vhd").arch("tb")
        self.assertEqual(stmt(v, "ca0").vpath, "ca[0]")
        self.assertEqual(stmt(v, "u_i1").vpath, "g[1].u")
        self.assertTrue(stmt(v, "u_i1").origin.endswith("_norm.sv:14"))


class TestElements(unittest.TestCase):
    """Constant values and right-hand sides element by element: what the per-bit driver
    rules of §5.4 step 4 read (a Z element drives nothing; an initial value drives the
    bits nothing else drives)."""

    ARCH = ("library ieee;\n-- Generated from Verilog module tb (x:1)\nentity tb is\nend entity;\n"
            "architecture from_verilog of tb is\n"
            "  signal t1 : logic3d := L3D_X;  -- Temporary created at x:5\n"
            "  signal v : logic3d_vector(2 downto 0) := logic3d_vector'(L3D_1, L3D_0ZX, L3D_H);"
            "  -- Declared at x:3\n"
            "  signal w : logic3d_vector(1 downto 0) := (others => L3D_X);  -- Declared at x:4\n"
            "  signal s : logic3d := L3D_0;  -- Declared at x:2\n"
            "begin\n%send architecture;\n")

    @staticmethod
    def elems(text, width=None):
        toks, _ = vhdl.lex(text)
        return vhdl.literal_elems(toks, width)

    def arch(self, body):
        return vhdl.parse_text(self.ARCH % body, "elems.vhd").arch("tb")

    def test_literal_elems(self):
        for text, width, want in (
                ("L3D_1", None, ["l3d_1"]),
                ("logic3d_vector'(L3D_Z, L3D_1)", None, ["l3d_z", "l3d_1"]),     # tgt-vhdl, MSB first
                ("logic3d_vector'(0 => L3D_H)", None, ["l3d_h"]),                 # one element
                ("logic3d_vector'(1 downto 0 => L3D_X)", None, ["l3d_x", "l3d_x"]),
                ("(others => L3D_Z)", 3, ["l3d_z"] * 3),
                ("(L3D_1, others => L3D_0)", 3, ["l3d_1", "l3d_0", "l3d_0"]),
                # a concatenation; logic3d_types_pkg aliases fold to tgt-vhdl's names
                ("L3D_1 & logic3d_vector'(L3D_0ZX, L3D_1Z)", None, ["l3d_1", "l3d_z", "l3d_h"])):
            self.assertEqual(self.elems(text, width), want, text)
        for text, width in (("(others => L3D_Z)", None),             # no width: unknown length
                            ("L3D_1 & (others => L3D_0)", 3),        # 'others' inside '&'
                            ("logic3d_vector'(1 => L3D_Z, 0 => L3D_1)", None),   # named: refused
                            ("l3d_not(a)", None), ("(a, L3D_1)", None),
                            ("L3D_1 after 1 ns", None), ("(L3D_1)(L3D_0)", None)):
            self.assertIsNone(self.elems(text, width), text)

    def test_assignment_parts(self):
        a = self.arch("  process (all) is\n  begin\n    v <= t1 & L3D_Z & s;\n  end process;\n"
                      "  process (all) is\n  begin\n    w <= logic3d_vector'(L3D_Z, L3D_1);\n"
                      "  end process;\n"
                      "  process (all) is\n  begin\n    w <= v(2 downto 1);\n  end process;\n")
        asg = [s.assigns[0] for s in a.stmts]
        self.assertEqual([(p.name, p.indices) if isinstance(p, vhdl.Ref) else p
                          for p in asg[0].parts], [("t1", None), "l3d_z", ("s", None)])
        self.assertIsNone(asg[0].copy)                         # a literal: not a plain copy
        self.assertEqual(asg[1].parts, ["l3d_z", "l3d_1"])
        self.assertFalse(asg[1].z_only)                        # only one element is Z
        self.assertEqual([(p.name, p.indices) for p in asg[2].parts], [("v", (2, 1))])
        self.assertEqual([(p.name, p.indices) for p in asg[2].copy], [("v", (2, 1))])
        self.assertTrue(all(s.straight for s in a.stmts))

    def test_conditional_and_delayed(self):
        a = self.arch("  process (all) is\n  begin\n    if is_one(s) then\n      t1 <= L3D_Z;\n"
                      "    end if;\n  end process;\n"
                      "  process (all) is\n  begin\n    t1 <= L3D_Z after 1 ns;\n  end process;\n"
                      "  w <= logic3d_vector'(L3D_Z, L3D_1) when is_one(s) else (others => L3D_Z);\n"
                      "  with s select t1 <= L3D_Z when L3D_1, L3D_Z when others;\n"
                      "  t1 <= L3D_Z;\n")
        st = a.stmts
        self.assertEqual([s.straight for s in st], [False, True, False, False, True])
        self.assertTrue(st[0].assigns[0].z_only)
        self.assertTrue(st[1].assigns[0].delayed and st[1].assigns[0].z_only)
        self.assertIsNone(st[1].assigns[0].parts)              # the value lands after 1 ns
        self.assertIsNone(st[2].assigns[0].parts)              # a conditional waveform
        self.assertIsNone(st[3].assigns[0].parts)
        self.assertEqual(st[4].assigns[0].parts, ["l3d_z"])
        self.assertFalse(st[4].assigns[0].delayed)

    def test_initial_values_and_constant_actuals(self):
        a = self.arch("  u: entity work.cin2\n    port map (\n"
                      "      d => logic3d_vector'(L3D_Z, L3D_1),\n      e => L3D_0ZX,\n"
                      "      f => (others => L3D_1)\n    );\n")
        self.assertEqual(a.signals["v"].init_elems, ["l3d_1", "l3d_z", "l3d_h"])
        self.assertEqual(a.signals["w"].init_elems, ["l3d_x", "l3d_x"])
        self.assertEqual(a.signals["s"].init_elems, ["l3d_0"])
        d, e, f = a.stmts[0].assocs
        self.assertEqual((d.elems, d.z_only, d.const), (["l3d_z", "l3d_1"], False, True))
        self.assertEqual((e.elems, e.z_only), (["l3d_z"], True))      # L3D_0ZX is L3D_Z
        self.assertIsNone(f.elems)                                    # width of the formal unknown

    def test_fixture_forms(self):
        # comb_fused_0 of cut_vec: tmp_ivl_32 := (others => L3D_Z); mix := tmp_ivl_12 & tmp_ivl_32 & ...
        tb = design("vec").arch("tb")
        fused = stmt(tb, "comb_fused_0")
        self.assertTrue(fused.straight)
        mix = [x for x in fused.assigns if x.target.name == "mix"][0]
        self.assertEqual([p.name for p in mix.parts], ["tmp_ivl_12", "tmp_ivl_32", "LPM_d0_ivl_30"])
        self.assertEqual(tb.signals["code"].init_elems, ["l3d_0", "l3d_1", "l3d_0", "l3d_1"])
        tri = [s for s in design("bidir").arch("tb").stmts
               if s.kind == "process" and len(s.assigns) == 2][0]
        self.assertFalse(tri.straight)                         # if en ... else ...


class TestRejects(unittest.TestCase):
    HEAD = ("library ieee;\n-- Generated from Verilog module tb (x:1)\nentity tb is\nend entity;\n"
            "architecture from_verilog of tb is\n  signal s : logic3d := L3D_X;\nbegin\n")

    def bad(self, body):
        with self.assertRaises(NoteError) as cm:
            vhdl.parse_text(self.HEAD + body + "end architecture;\n", "bad.vhd")
        return cm.exception.notes[0].text()

    def test_unsupported_statements(self):
        self.assertIn("block", self.bad("  b: block begin end block;\n"))
        self.assertIn("for", self.bad("  g: for i in 0 to 1 generate end generate;\n"))
        self.assertIn("extended identifier", self.bad("  \\u.x\\: entity work.x;\n"))
        self.assertIn("positional", self.bad("  u: entity work.x port map (s);\n"))

    def test_unexpected_unit(self):
        with self.assertRaises(NoteError):
            vhdl.parse_text("package p is end package;\n", "p.vhd")


class TestLibraryModes(unittest.TestCase):
    def test_table(self):
        m = sv2vhdl_modes.MODES
        self.assertEqual(m["sv_and"], {"y": "out", "a": "in"})
        self.assertEqual(m["sv_tran"], {"a": "inout", "b": "inout"})
        self.assertEqual(m["sv_alias"]["a"], "inout")
        self.assertEqual(m["sv_tranif1"]["ctrl"], "in")
        self.assertEqual(m["sv_strength_buf"]["y"], "inout")
        self.assertEqual(m["sv_pullup"], {"y": "out"})

    @unittest.skipUnless(os.path.isdir(NVC_SV2VHDL), "needs the nvc sources (VAMOS_NVC_SRC)")
    def test_regenerates_identically(self):
        path = os.path.join(ROOT, "vamos", "ams", "sv2vhdl_modes.py")
        with open(path) as fh:
            checked_in = fh.read()
        self.assertEqual(vhdl.render_modes_module(NVC_SV2VHDL), checked_in,
                         "regenerate vamos/ams/sv2vhdl_modes.py (see vamos/ams/vhdl.py)")


if __name__ == "__main__":
    unittest.main()
