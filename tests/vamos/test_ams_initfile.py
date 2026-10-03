"""The control-file parser: vamos/ams/initfile.py (docs/VAMOS_AMS_DESIGN.md §2).

    python3 -m unittest discover -s tests/vamos -p 'test_ams_initfile.py' -v
"""

import os
import unittest

from vamos_testlib import TempDir, fixture  # noqa: F401  (also puts ROOT on sys.path)

from vamos.ams import initfile, rules  # noqa: E402
from vamos.ams.config import TNF, PortConnect, PortDir  # noqa: E402
from vamos.notes import ERROR, NOTE, WARNING, has_errors  # noqa: E402

AMS = fixture("ams")


class Base(TempDir):
    def parse(self, text, name="vcsAD.init", env=None, before=()):
        self.write(name, text)
        return initfile.parse_control(list(before) + [name], self.tmp, env if env is not None else {})

    def texts(self, cfg, severity=None):
        return [n.text() for n in cfg.notes if severity is None or n.severity == severity]

    def only(self, cfg, severity, *needles):
        """Exactly one note of this severity contains every needle; return it."""
        hits = [n for n in cfg.notes if n.severity == severity and all(s in n.text() for s in needles)]
        self.assertEqual(len(hits), 1, "%s %s in %s" % (severity, needles, self.texts(cfg)))
        return hits[0]


class TestXheep(Base):
    """x-heep's hw/ip_examples/ams/analog/control.init, byte for byte."""

    def test_verbatim(self):
        cfg = initfile.parse_control(["init_xheep_control.init"], AMS, {})
        self.assertEqual(cfg.notes, [])
        ch = cfg.choose
        self.assertEqual((ch.engine, ch.netlists, ch.cfgs, ch.out_prefix, ch.uic, ch.dialect),
                         ("xa", ["../../../hw/ip_examples/ams/analog/adc.sp"], [], None, False, "hspice"))
        self.assertEqual(ch.origin, "init_xheep_control.init:5")
        self.assertEqual(cfg.port_connects, [PortConnect("ams_adc_1b", None, [("vdd", "vdd", False),
                                                                              ("gnd", "gnd", False)],
                                                         "init_xheep_control.init:6")])
        self.assertEqual(cfg.port_dirs, {"ams_adc_1b": PortDir("ams_adc_1b", {"sel": "input", "out": "output"},
                                                               "init_xheep_control.init:7")})
        self.assertEqual(cfg.bus_formats, ["<%d>"])
        self.assertEqual(cfg.formats(), ["<%d>"])
        self.assertEqual(cfg.files, [os.path.join(AMS, "init_xheep_control.init")])
        self.assertEqual((cfg.rules, cfg.use_spice, cfg.netlist_lines), ([], [], []))


class TestLexer(Base):
    def test_port_dir_without_spaces_or_final_semicolon(self):
        cfg = self.parse("choose xa a.sp;\nport_dir -cell inv(input a; output y)")
        self.assertFalse(cfg.notes, self.texts(cfg))
        self.assertEqual(cfg.port_dirs["inv"].dirs, {"a": "input", "y": "output"})
        # ... and ended by the next keyword line when the ';' is missing mid-file
        cfg = self.parse("port_dir -cell inv(input a, b; output y)\nbus_format <%d>;\nchoose xa a.sp")
        self.assertFalse(cfg.notes, self.texts(cfg))
        self.assertEqual(cfg.port_dirs["inv"].dirs, {"a": "input", "b": "input", "y": "output"})
        self.assertEqual(cfg.bus_formats, ["<%d>"])
        self.assertEqual(cfg.choose.netlists, ["a.sp"])

    def test_pams_p180_multiline_port_dir(self):
        cfg = initfile.parse_control(["init_pams_p180_portdir.init"], AMS, {})
        self.assertFalse(cfg.notes, self.texts(cfg))
        self.assertEqual(cfg.port_dirs["addr4b"].dirs,
                         {"a": "input", "b": "input", "cin": "input", "s": "output", "cout": "output"})
        self.assertEqual(cfg.port_dirs["addr4b"].origin, "init_pams_p180_portdir.init:3")
        self.assertEqual(cfg.port_dirs["invs1"].dirs, {"a": "input", "y": "output"})
        self.assertEqual(cfg.choose.netlists, ["addr4.spi"])

    def test_unterminated_paren_recovers_at_the_next_keyword(self):
        cfg = self.parse("choose xa a.sp;\n"
                         "port_connect -cell c ( vdd => vdd\n"
                         "bus_format <%d>;\n"
                         "port_dir -cell d (input a\n")
        errs = self.texts(cfg, ERROR)
        self.assertEqual(errs, ["error: vcsAD.init:2: unterminated '(' in port_connect at vcsAD.init:2",
                                "error: vcsAD.init:4: unterminated '(' in port_dir at vcsAD.init:4"])
        self.assertEqual(cfg.bus_formats, ["<%d>"])          # the rest of the file is not swallowed
        self.assertEqual((cfg.port_connects, cfg.port_dirs), ([], {}))

    def test_keyword_named_ports_in_lists(self):
        # a flip-flop's set port starts continuation lines; it is no command
        cfg = self.parse("choose xa a.sp;\n"
                         "use_spice -cell dff port_map (q => q,\n"
                         "  set => SET,\n"
                         "  d2a => dac_in, * => snps_by_name);\n"
                         "port_dir -cell dff (input d,\n"
                         "set, rst; output q)\n"
                         "port_connect -cell dff (\n"
                         "  choose => vdd)\n"
                         "set bus_format <%d>;\n")
        self.assertFalse(cfg.notes, self.texts(cfg))
        self.assertEqual(cfg.use_spice[0].port_map, [("q", "q"), ("set", "SET"), ("d2a", "dac_in"),
                                                     ("*", "snps_by_name")])
        self.assertEqual(cfg.port_dirs["dff"].dirs, {"d": "input", "set": "input", "rst": "input",
                                                     "q": "output"})
        self.assertEqual(cfg.port_connects[0].conns, [("choose", "vdd", False)])
        self.assertEqual(cfg.bus_formats, ["<%d>"])          # 'set bus_format' still ends port_connect

    def test_missing_semicolons(self):
        # PAMS p241 and p297 write these without ';'
        cfg = self.parse("ie_tracing_rpt enable\n"
                         "d2a node=net1 Vdd=vdd20 Vss=vss00 hiv=90% lov=10%\n"
                         "choose xa x.sp")
        self.assertFalse(self.texts(cfg, ERROR))
        self.only(cfg, NOTE, "vcsAD.init:1", "ie_tracing_rpt has no effect")
        (r,) = cfg.rules
        self.assertEqual((r.kind, r.node, r.params, r.origin),
                         ("d2a", "net1", {"vdd": "vdd20", "vss": "vss00", "hiv": "90%", "lov": "10%"},
                          "vcsAD.init:2"))
        self.assertEqual(cfg.choose.netlists, ["x.sp"])

    def test_statements_span_lines_and_comments_are_stripped(self):
        cfg = self.parse("// d2a hiv=9 node=top.c1;\n"
                         "/* a2d loth=9 node=top.c2;\n"
                         "   choose xa never.sp; */ choose xa \"my//net.sp\"\n"
                         "   -o out ; // trailing\n"
                         "d2a hiv=1.2 /* inline */ lov=0\n"
                         "    node=top.a;\n")
        self.assertFalse(cfg.notes, self.texts(cfg))
        self.assertEqual(cfg.choose.netlists, ["my//net.sp"])
        self.assertEqual(cfg.choose.out_prefix, "out")
        self.assertEqual(cfg.choose.origin, "vcsAD.init:3")
        (r,) = cfg.rules
        self.assertEqual((r.node, r.params, r.origin), ("top.a", {"hiv": "1.2", "lov": "0"}, "vcsAD.init:5"))

    def test_netlist_commands_block(self):
        cfg = initfile.parse_control(["init_netlist_cmds.init"], AMS, {})
        self.assertFalse(self.texts(cfg, ERROR) + self.texts(cfg, WARNING))
        lab = "init_netlist_cmds.init:%d"
        self.assertEqual(cfg.netlist_lines, [
            ("* a SPICE comment line ; with a semicolon", lab % 3),
            (".temp 45 $ an inline dollar comment", lab % 4),
            (".param vsup=1.2 ; an inline semicolon comment", lab % 5),
            ("// a double-slash line, kept as written", lab % 6),
            ("   .option post=1", lab % 7)])
        self.assertEqual(cfg.bus_formats, ["<%d>"])
        # the xa_commands block is mined like an XA cfg, after the -c files
        self.assertEqual(cfg.xa["probe_v"], [("*", "init_xa.cfg:4"), ("tb.dut.*", lab % 11)])
        self.only(cfg, NOTE, lab % 10, "set_sim_level")

    def test_raw_block_edges(self):
        cfg = self.parse("choose xa a.sp;\nnetlist_commands_begin; .temp 27\n.option post\n"
                         "NETLIST_COMMANDS_END ; bus_format _%d;\n")
        self.assertFalse(cfg.notes, self.texts(cfg))
        self.assertEqual(cfg.netlist_lines, [(" .temp 27", "vcsAD.init:2"), (".option post", "vcsAD.init:3")])
        self.assertEqual(cfg.bus_formats, ["_%d"])           # the end line goes on as control text
        cfg = self.parse("choose xa a.sp;\nnetlist_commands_begin\n.temp 27\nd2a hiv=1 node=x;\n")
        self.only(cfg, ERROR, "vcsAD.init:2", "has no netlist_commands_end")
        self.assertEqual((cfg.netlist_lines, cfg.rules), ([], []))   # keywords inside a block are raw
        cfg = self.parse("choose xa a.sp;\nxa_commands_end;\nnetlist_commands_begin x;\n.temp 1\n"
                         "netlist_commands_end\n")
        self.only(cfg, ERROR, "vcsAD.init:2", "xa_commands_end without xa_commands_begin")
        self.only(cfg, ERROR, "vcsAD.init:3", "takes no arguments")

    def test_unknown_command_and_case(self):
        cfg = self.parse("CHOOSE XA a.sp;\nset_sim_case -case sensitive;\nset foo bar;\n")
        self.assertEqual(cfg.choose.engine, "xa")
        self.only(cfg, ERROR, "vcsAD.init:2", "unknown command 'set_sim_case'")
        self.only(cfg, ERROR, "vcsAD.init:3", "unknown command 'set foo'")

    def test_unbalanced_and_unterminated(self):
        cfg = self.parse("choose xa a.sp);\nchoose xa \"b.sp;\n/* open")
        self.only(cfg, ERROR, "vcsAD.init:1", "unbalanced ')'")
        self.only(cfg, ERROR, "vcsAD.init:2", "unterminated string")
        self.only(cfg, ERROR, "vcsAD.init:3", "unterminated /* comment")

    def test_escaped_instance_names(self):
        cfg = self.parse("choose xa a.sp;\nuse_spice -cell monitor -inst \\i1<1> .i2 top.\\g[0] .u;\n")
        self.assertFalse(cfg.notes, self.texts(cfg))
        self.assertEqual(cfg.use_spice[0].insts, ["i1<1>.i2", "top.g[0].u"])


class TestInclude(Base):
    def test_include_forms(self):
        self.write("sub/a.init", "d2a hiv=1 node=top.a;\n`include sub/b.init\n")   # nested: cwd-relative
        self.write("sub/b.init", "d2a hiv=2 node=top.b;\n")
        self.write("c.init", "d2a hiv=3 node=top.c;")
        cfg = self.parse("choose xa a.sp;\n"
                         "d2a hiv=0 node=top.first;\n"
                         "`include \"$SUBDIR/a.init\" ;\n"
                         "`include ${TOP}c.init\n"
                         "d2a hiv=4 node=top.last;\n",
                         env={"SUBDIR": "sub", "TOP": ""})
        self.assertFalse(cfg.notes, self.texts(cfg))
        self.assertEqual([r.node for r in cfg.rules], ["top.first", "top.a", "top.b", "top.c", "top.last"])
        self.assertEqual([r.origin for r in cfg.rules],
                         ["vcsAD.init:2", os.path.join("sub", "a.init") + ":1",
                          os.path.join("sub", "b.init") + ":1", "c.init:1", "vcsAD.init:5"])
        self.assertEqual([os.path.relpath(f, self.tmp) for f in cfg.files],
                         ["vcsAD.init", os.path.join("sub", "a.init"), os.path.join("sub", "b.init"),
                          "c.init"])

    def test_include_errors(self):
        self.write("loop.init", "`include loop.init\n")
        cfg = self.parse("choose xa a.sp;\n`include \"$NOPE/x.init\";\n`include missing.init\n"
                         "`include loop.init\n`include a b\n")
        self.only(cfg, ERROR, "vcsAD.init:2", "NOPE is not set")
        self.only(cfg, ERROR, "vcsAD.init:3", "missing.init not found")
        self.only(cfg, ERROR, "loop.init:1", "`include cycle")
        self.only(cfg, ERROR, "vcsAD.init:5", "one file name")


class TestFiles(Base):
    def test_find_ini(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        self.assertIsNone(initfile.find_ini(self.tmp, {"HOME": home}))
        self.write("home/snps_vcsAD.ini", "")
        self.assertEqual(initfile.find_ini(self.tmp, {"HOME": home}), os.path.join(home, initfile.INI_NAME))
        self.write("snps_vcsAD.ini", "")
        self.assertEqual(initfile.find_ini(self.tmp, {"HOME": home}), os.path.join(self.tmp, initfile.INI_NAME))
        self.assertIsNone(initfile.find_ini(home + "x", {}))

    def test_ini_first_and_later_commands_win(self):
        # PAMS p124: snps_vcsAD.ini is read first; the later command wins
        self.write("snps_vcsAD.ini", "choose xa -c none.cfg;\nresolve_x_inst_prefix enable;\n"
                   "a2d loth=1.65v hith=1.65v node=top.s[0];\nbus_format [%d];\n")
        cfg = self.parse("choose xa -hspice netlist.sp -o xa/xa;\n"
                         "a2d loth=1.65v hith=1.65v node=top.s[1];\n"
                         "a2d loth=1.35v hith=1.35v node=top.s[0] ;\n"
                         "bus_format <%d>;\n",
                         before=[initfile.find_ini(self.tmp, {})])
        self.assertEqual([r.node for r in cfg.rules], ["top.s[0]", "top.s[1]", "top.s[0]"])
        self.assertEqual(cfg.choose.netlists, ["netlist.sp"])
        self.assertEqual(cfg.choose.cfgs, [])                # the ini's choose (and its -c) is replaced
        self.only(cfg, NOTE, "vcsAD.init:1", "replaces the choose at snps_vcsAD.ini:1")
        self.only(cfg, NOTE, "[MSV-MC-FRO]", "snps_vcsAD.ini, vcsAD.init")
        self.assertEqual(cfg.bus_formats, ["<%d>"])
        self.assertFalse(self.texts(cfg, ERROR))

    def test_missing_files_and_no_choose(self):
        cfg = initfile.parse_control(["nope.init"], self.tmp, {})
        self.only(cfg, ERROR, "nope.init not found")
        self.assertEqual(len(cfg.notes), 1)
        cfg = self.parse("bus_format <%d>;\n")
        self.only(cfg, ERROR, "must contain at least choose")
        cfg = initfile.parse_control([], self.tmp, {})
        self.assertTrue(has_errors(cfg.notes))
        self.write("x.init", "choose xa a.sp;")
        cfg = initfile.parse_control(["x.init", os.path.join(self.tmp, "x.init")], self.tmp, {})
        self.only(cfg, NOTE, "read once")
        self.assertEqual(len(cfg.files), 1)


class TestChoose(Base):
    def test_arity(self):
        # -spice is a mode flag and never takes the netlist (PAMS p204)
        cfg = self.parse("choose finesim -spice net.spi -o out;")
        ch = cfg.choose
        self.assertEqual((ch.engine, ch.netlists, ch.out_prefix, ch.options, ch.dialect),
                         ("finesim", ["net.spi"], "out", ["-spice"], "hspice"))
        self.only(cfg, NOTE, "-spice")
        # SimpRisc: -wavefmt takes a value that is not a netlist
        self.write("cfg.cfg", "")
        cfg = self.parse("choose xa -c cfg.cfg -wavefmt wdb -o out/ana;")
        ch = cfg.choose
        self.assertEqual((ch.netlists, ch.cfgs, ch.out_prefix, ch.options), ([], ["cfg.cfg"], "out/ana",
                                                                             ["-wavefmt wdb"]))
        self.only(cfg, NOTE, "-wavefmt wdb is ignored")
        self.assertFalse(has_errors(cfg.notes))

    def test_netlist_forms_and_flags(self):
        cfg = self.parse("choose primesim -n a.sp -hspice b.sp c.sp -nspice d.sp -skipdc -afile e.sp -mt f.sp;")
        ch = cfg.choose
        self.assertEqual(ch.netlists, ["a.sp", "b.sp", "c.sp", "d.sp", "f.sp"])
        self.assertTrue(ch.uic)
        self.assertEqual(ch.dialect, "spice")
        self.assertEqual(ch.options, ["-afile e.sp", "-mt"])
        self.only(cfg, WARNING, "-nspice netlists are parsed as HSPICE")
        self.only(cfg, WARNING, "-afile e.sp is ignored")
        self.only(cfg, WARNING, "option -mt is ignored")     # takes no value: f.sp stays a netlist
        self.assertFalse(has_errors(cfg.notes))

    def test_engines(self):
        for eng in ("xa", "finesim", "primesim", "hsim", "nanosim", "vacask", "xyce", "Xyce"):
            cfg = self.parse("choose %s a.sp;" % eng)
            self.assertEqual(cfg.choose.engine, eng.lower())
            self.assertFalse(cfg.notes, self.texts(cfg))
        cfg = self.parse("choose nanosim -n chargepump.spi -C config -o lcd;")   # NanoSim era (UTK)
        self.only(cfg, ERROR, "XA cfg config not found")
        cfg = self.parse("choose spectre a.scs;")
        self.only(cfg, ERROR, "unknown analog engine 'spectre'")
        cfg = self.parse("choose -n a.sp;\nchoose;")
        self.assertEqual(len(self.texts(cfg, ERROR)), 3)     # two bad chooses + no choose at all
        cfg = self.parse("choose xa a.sp -o;")
        self.only(cfg, ERROR, "-o needs a value")


class TestCommands(Base):
    def test_every_command_form(self):
        cfg = initfile.parse_control(["init_every_command.init"], AMS, {})
        lab = "init_every_command.init:%d"
        ch = cfg.choose
        self.assertEqual(ch.origin, lab % 5)                 # not the commented-out choose
        self.assertEqual((ch.netlists, ch.cfgs, ch.out_prefix, ch.uic, ch.dialect),
                         (["net.sp", "extra.sp", "third.sp", "fourth.sp"], ["init_xa.cfg"], "out/ana",
                          True, "spice"))
        us = cfg.use_spice
        self.assertEqual([(u.cells, u.insts) for u in us],
                         [([("inv1", "")], []), ([("CPU", "SPICE_CPU")], ["top.dut.blk1", "top.dut.blk2"]),
                          ([("chargepump_com", ""), ("chargepump_seg", "")], [])])
        self.assertEqual(us[0].port_map, [("z", "y"), ("n", "snps_open"), ("d[0]", "d_0"),
                                          ("*", "snps_by_name")])
        self.assertEqual(us[0].index_order, [("*", "same"), ("a", "dec"), ("s", "inc")])
        self.assertEqual(cfg.bus_formats, ["<%d>", "_%d"])
        self.assertEqual(cfg.port_dirs["invs1"].dirs, {"a": "input", "b": "input", "y": "output", "z": "inout"})
        self.assertEqual([(p.cell, p.inst, p.conns) for p in cfg.port_connects], [
            ("inv1", None, [("vdd", "vdd!", False), ("vss", "snps_open", False), ("vref", "top.my_vref", True)]),
            ("inv2", "top.i1", [("vdd", "top.pwr.vdd", False)]),
            ("inv2", "top.i2", [("vdd", "top.pwr.vdd", False)]),
            ("inv3", "top.i3", [("vdd", "vdd", False), ("vss", "gnd", False)])])
        self.assertEqual([(r.kind, r.origin) for r in cfg.rules],
                         [("a2d", lab % 15), ("d2a", lab % 16), ("d2a", lab % 17), ("a2d", lab % 18),
                          ("d2a", lab % 19), ("d2a", lab % 20), ("map_by_node", lab % 31)])
        self.assertEqual((cfg.rules[6].node, cfg.rules[6].params), ("top.n1", {"r": "5"}))
        self.assertEqual(cfg.rules[1].params, {"powernet": "", "hiv": "1.2", "lov": "0"})
        self.assertEqual((cfg.rules[3].cell, cfg.rules[3].port), ("adc", "d<0>"))
        self.assertEqual((cfg.rules[4].inst, cfg.rules[4].port), ("top.x2", "*"))
        self.assertEqual(cfg.rules[5].params, {"hiv": "1.0", "lov": "0.2"})     # "lov= 0.2"
        self.assertEqual(cfg.remove_d2a, [("top.V*", 2.5, lab % 21), ("top.u.y", None, lab % 22)])
        self.assertEqual(cfg.disable_ie, [("top.i1.outa", lab % 23)])
        refs = cfg.ref_voltages
        self.assertEqual(refs[:2], [("A1", 1.5, lab % 24), ("A2", None, lab % 25)])
        self.assertEqual((refs[2][0], rules.is_skip(refs[2][1])), ("vss", True))
        self.assertEqual(rules.ref_nodes(cfg), ([("A1", 1.5, 0), ("A2", None, 1)], ["vss"]))
        self.assertEqual(cfg.severity_overrides, {TNF: "warning"})
        self.assertEqual(cfg.netlist_lines, [(".temp 45", lab % 39)])
        self.assertEqual(cfg.xa["case"], "sensitive")
        # one disposition per command: errors, warnings and notes by line
        by = {}
        for n in cfg.notes:
            if n.origin.startswith("init_every_command.init:"):
                by.setdefault(int(n.origin.split(":")[1]), []).append(n.severity)
        self.assertEqual(by, {5: [NOTE, WARNING, NOTE, WARNING], 10: [NOTE], 28: [NOTE], 29: [NOTE],
                              30: [WARNING], 32: [WARNING], 33: [NOTE], 34: [NOTE],
                              35: [NOTE], 36: [NOTE], 37: [ERROR]})       # 31 map_by_node: applied

    def test_dispositions(self):
        lines = {"use_verilog -cell a;": ERROR, "use_vcs -module a;": ERROR, "use_veriloga -cell a;": ERROR,
                 "spice_top name=t;": ERROR, "dynamic_supply_filter -deltav=0.1;": ERROR,
                 "r2e node=top.v;": WARNING, "e2n node=top.v;": WARNING, "n2e node=top.v;": WARNING,
                 "insert_cell subckt=m node=top.r;": WARNING, "rmap_file r.map;": WARNING,
                 "rt_a2d node=top.y;": WARNING, "ams_set_discipline discipline=logic;": WARNING,
                 "ams_new_thing x;": WARNING, "udn_n2e type=t module=m node=x;": WARNING,
                 "ie_connect_rpt enable;": NOTE, "ie_new_rpt enable;": NOTE, "optimize_shadowfile;": NOTE,
                 "shadow_file type=compact;": NOTE, "shadow_file_dir /u/x;": NOTE,
                 "duplicate_net_inst_name enable;": NOTE, "skip_xmr_name_check enable;": NOTE,
                 "form_spice_bus disable;": NOTE, "print_ie_res node=top.x;": NOTE,
                 "gen_spice_wrapper -cell INV;": NOTE, "transient_analysis $stop;": NOTE,
                 "transient_analysis;": ERROR, "frobnicate;": ERROR}
        for line, sev in lines.items():
            cfg = self.parse("choose xa a.sp;\n" + line)
            got = [n.severity for n in cfg.notes]
            self.assertEqual(got, [sev], "%s -> %s" % (line, self.texts(cfg)))
            self.assertEqual(cfg.notes[0].origin, "vcsAD.init:2")

    def test_use_spice_forms(self):
        cfg = self.parse("choose xa a.sp;\n"
                         "use_spice -cell inv* adc:adc_sp port_map(a => in_a, b[1] => b_1, c => snps_open,"
                         " * => snps_by_position);\n"
                         "use_spice -cell dac -inst top.d1 port_map (* => snps_open);\n")
        self.assertFalse(cfg.notes, self.texts(cfg))
        self.assertEqual(cfg.use_spice[0].cells, [("inv*", ""), ("adc", "adc_sp")])
        self.assertEqual(cfg.use_spice[0].port_map, [("a", "in_a"), ("b[1]", "b_1"), ("c", "snps_open"),
                                                     ("*", "snps_by_position")])
        bad = {"use_spice -cell d port_map (data => d[2:0]);": "SPICE bus range",
               "use_spice -cell d port_map (data => {d_2, d_1, d_0});": "concatenation",
               "use_spice -cell d port_map (data[0:2] => d);": "part-select",
               "use_spice -cell d port_map (* => d);": "snps_by_name, snps_by_position or snps_open",
               "use_spice -cell d port_map (a => snps_by_name);": "only valid as",
               "use_spice -cell d port_map (a => x, a => y);": "mapped twice",
               "use_spice -cell d port_map (a x);": "is not 'verilog => spice'",
               "use_spice -cell d port_index_order (a => up);": "same|dec|inc",
               "use_spice -cell d port_map;": "needs a ( ... ) list",
               "use_spice -inst top.x;": "needs -cell",
               "use_spice -cell d: ;": "bad -cell name",
               "use_spice -cell d -bogus;": "unexpected '-bogus'"}
        for line, needle in bad.items():
            cfg = self.parse("choose xa a.sp;\n" + line)
            self.only(cfg, ERROR, "vcsAD.init:2", needle)
            self.assertEqual(cfg.use_spice, [], line)

    def test_bus_format_forms(self):
        for fmts in ("<%d>", "[%d]", "_%d", "<%d> _%d"):
            cfg = self.parse("choose xa a.sp;\nbus_format %s;" % fmts)
            self.assertEqual(cfg.bus_formats, fmts.split())
            self.assertFalse(cfg.notes, self.texts(cfg))
        cfg = self.parse("choose xa a.sp;\nbus_format %_d;")    # PAMS p354's typo
        self.only(cfg, ERROR, "bus_format %_d")
        self.assertEqual(cfg.formats(), ["[%d]"])
        cfg = self.parse("choose xa a.sp;\nbus_format;")
        self.only(cfg, ERROR, "needs a format")

    def test_port_dir_forms(self):
        cfg = self.parse("choose xa a.sp;\n"
                         "port_dir -cell inv* (input a b, c;; output Y);\n"
                         "port_dir -cell INV* (inout c);\n")
        self.assertFalse(cfg.notes, self.texts(cfg))
        pd = cfg.port_dirs["inv*"]
        self.assertEqual(pd.dirs, {"a": "input", "b": "input", "c": "inout", "y": "output"})
        self.assertEqual(pd.origin, "vcsAD.init:2, vcsAD.init:3")
        bad = {"port_dir -cell x (in a);": "should start with input",
               "port_dir -cell x (input);": "lists no ports",
               "port_dir -cell x (input a; output a);": "both input and output",
               "port_dir -cell x input a;": "unexpected 'input'",
               "port_dir (input a);": "needs -cell"}
        for line, needle in bad.items():
            cfg = self.parse("choose xa a.sp;\n" + line)
            self.assertTrue(any(needle in t for t in self.texts(cfg, ERROR)), (line, self.texts(cfg)))
            self.assertEqual(cfg.port_dirs, {}, line)

    def test_port_connect_forms(self):
        cfg = self.parse("choose xa a.sp;\n"
                         "port_connect -cell test ( real vdd => top.my_vdd , real vss => top.my_vss );\n"
                         "port_connect -cell inv1 (vdd => top.i_power_blk.vdd , vss =>\n top.i_power_blk.vss);\n"
                         "port_connect -cell c2 (real => x, nc => SNPS_OPEN);\n")
        self.assertFalse(cfg.notes, self.texts(cfg))
        self.assertEqual(cfg.port_connects[0].conns, [("vdd", "top.my_vdd", True), ("vss", "top.my_vss", True)])
        self.assertEqual(cfg.port_connects[1].conns, [("vdd", "top.i_power_blk.vdd", False),
                                                      ("vss", "top.i_power_blk.vss", False)])
        self.assertEqual(cfg.port_connects[2].conns, [("real", "x", False),     # a port named real
                                                      ("nc", "snps_open", False)])
        bad = {"port_connect -cell c (vdd);": "is not 'spice_port => net'",
               "port_connect -cell c (vdd => a, vdd => b);": "connected twice",
               "port_connect (vdd => a);": "needs -cell",
               "port_connect -cell c ();": "lists no connection"}
        for line, needle in bad.items():
            cfg = self.parse("choose xa a.sp;\n" + line)
            self.only(cfg, ERROR, needle)
            self.assertEqual(cfg.port_connects, [], line)

    def test_remove_disable_reference(self):
        cfg = self.parse("choose xa a.sp;\n"
                         "remove_d2a dc=1.8v node=top.VVDH;\nremove_d2a node = top.din* ;\n"
                         "disable_ie node=top.i1.outa;\n"
                         "ie_reference_voltage node=A1 voltage=1.5;\nie_reference_voltage skip_node=vss;\n")
        self.assertFalse(cfg.notes, self.texts(cfg))
        self.assertEqual([r[:2] for r in cfg.remove_d2a], [("top.VVDH", 1.8), ("top.din*", None)])
        bad = {"remove_d2a dc=1.8;": "needs node=", "remove_d2a node=*;": "is not allowed",
               "remove_d2a dc=50% node=top.v;": "not a voltage", "remove_d2a node=x foo=1;": "unknown key foo",
               "disable_ie node=*;": "is not allowed", "disable_ie;": "needs node=",
               "ie_reference_voltage skip_node=vss node=v;": "cannot be combined",
               "ie_reference_voltage voltage=1.2;": "needs node=",
               "ie_reference_voltage node=v voltage=90%;": "not a voltage",
               "ie_reference_voltage node=v*;": "wildcards", "disable_ie node;": "needs a value"}
        for line, needle in bad.items():
            cfg = self.parse("choose xa a.sp;\n" + line)
            self.only(cfg, ERROR, needle)
            self.assertEqual((cfg.remove_d2a, cfg.disable_ie, cfg.ref_voltages), ([], [], []), line)

    def test_severity_overrides(self):
        cfg = self.parse("choose xa a.sp;\ndowngrade_to_warn [MSV-IE-OPT-TNF]|[MSV-RTIE-OPT-TNF];\n")
        self.assertEqual(cfg.severity_overrides, {TNF: "warning"})
        self.only(cfg, NOTE, "MSV-RTIE-OPT-TNF has no effect")
        cfg = self.parse("choose xa a.sp;\ndowngrade_to_warn MSV-IE-OPT-TNF;\nupgrade_to_error msv-ie-opt-tnf\n")
        self.assertEqual(cfg.severity_overrides, {TNF: "error"})       # the later one wins
        cfg = self.parse("choose xa a.sp;\nupgrade_to_error;")
        self.only(cfg, ERROR, "needs a message id")


class TestIeRuleParsing(Base):
    def rule(self, text):
        cfg = self.parse("choose xa a.sp;\n" + text)
        return cfg, (cfg.rules[0] if cfg.rules else None)

    def test_key_value_spacing_and_case(self):
        cfg, r = self.rule("d2a HIV = 1.2 lov= 0 rf_time =1n Node=Top.A powernet;")
        self.assertFalse(cfg.notes, self.texts(cfg))
        self.assertEqual((r.node, r.params), ("Top.A", {"hiv": "1.2", "lov": "0", "rf_time": "1n",
                                                        "powernet": ""}))

    def test_selectors(self):
        ok = ["a2d node=top.x;", "a2d cell=adc port=d<0>;", "d2a inst=top.x2 port=*;",
              "d2a library=libCell1 cell=* port=*;"]
        for line in ok:
            cfg, r = self.rule(line)
            self.assertIsNotNone(r, (line, self.texts(cfg)))
            self.assertFalse(self.texts(cfg, ERROR))
        self.only(self.rule(ok[3])[0], NOTE, "library=libCell1 is ignored")
        bad = {"a2d loth=0.3;": "no selector", "a2d cell=adc;": "needs port=", "a2d port=a;": "needs cell=",
               "a2d node=top.x port=a;": "cannot be combined", "a2d cell=a inst=b port=c;": "exclusive"}
        for line, needle in bad.items():
            cfg, r = self.rule(line)
            self.assertIsNone(r, line)
            self.only(cfg, ERROR, needle)

    def test_several_libraries_warn(self):
        cfg = self.parse("choose xa a.sp;\nd2a hiv=1.2 lov=0.0 library=libCell1 cell=* port=*;\n"
                         "d2a hiv=1.8 lov=0.0 library=libCell2 cell=* port=*;\n")
        self.only(cfg, WARNING, "several libraries (libCell1, libCell2)")

    def test_key_dispositions(self):
        cases = {
            "a2d minv=0.5 node=x;": (ERROR, "minv= is not supported"),
            "a2d minv_logic=0 node=x;": (ERROR, "minv_logic= is not supported"),
            "d2a minv_analog=0 node=x;": (ERROR, "minv_analog= is not supported"),
            "d2a vdd_filter=0.1 node=x;": (ERROR, "vdd_filter= is not supported"),
            "a2d frob=1 node=x;": (ERROR, "unknown key frob="),
            "d2a loth=1 node=x;": (ERROR, "loth= is a a2d key"),
            "d2a hiv=abc node=x;": (ERROR, "bad hiv=abc"),
            "d2a x2v=5 node=x;": (ERROR, "x2v=5"),
            "d2a rf_time=-1n node=x;": (ERROR, "negative"),
            "d2a delay=-1n node=x;": (ERROR, "negative"),
            "d2a rf_time=50% node=x;": (ERROR, "percentage"),
            "d2a rf_time=1n rise_time=2n node=x;": (ERROR, "cannot be combined with rise_time="),
            "d2a delay=1n fall_delay=2n node=x;": (ERROR, "cannot be combined with fall_delay="),
            "d2a powernet=1 node=x;": (ERROR, "takes no value"),
            "d2a hiv node=x;": (ERROR, "hiv= needs a value"),
            "a2d xband=0 node=x;": (ERROR, "positive"),
            "a2d midv_logic=q node=x;": (ERROR, "expected 0, 1, X or Z"),
            "a2d strength=bogus node=x;": (ERROR, "expected one of"),
            "a2d queue=later node=x;": (ERROR, "blocking or nonblocking"),
            "d2a vdd=vdd* node=x;": (ERROR, "wildcard supply name"),
            "d2a vdd_port=vdd node=x;": (ERROR, "needs port="),
            "a2d vdd_port=../vdd* cell=c port=*;": (ERROR, "a wildcard after ../ is not supported"),
            "a2d except_port=v* node=x;": (ERROR, "needs port="),
            "a2d vdd=a vdd_port=b cell=c port=*;": (ERROR, "cannot be combined with vdd_port="),
            "a2d hiz_on node=x;": (WARNING, "hiz_on is ignored"),
            "a2d hiz_off node=x;": (WARNING, "hiz_off is ignored"),
            "a2d strength=weak node=x;": (WARNING, "strength=weak is ignored"),
            "a2d ceff=1p node=x;": (WARNING, "ceff=1p is ignored"),
            "a2d queue=nonblocking node=x;": (WARNING, "queue=nonblocking"),
            "d2a rf_time=0 node=x;": (NOTE, "clamped to 1 fs"),
            "a2d xband=4 node=x;": (NOTE, "no effect on a node without midv_time"),
            "d2a hiv=1 hiv=2 node=x;": (NOTE, "given twice"),
        }
        for line, (sev, needle) in cases.items():
            cfg, r = self.rule(line)
            self.only(cfg, sev, needle)
            self.assertEqual(r is None, sev == ERROR, line)
        for line in ("a2d strength=rmap queue=blocking loth=0.2 hith=0.8 node=x;", "a2d midv_logic=1 node=x;",
                     "a2d midv_time=1n midv_logic=Z node=x;"):        # Z is applied (MIDV_L 3)
            cfg, r = self.rule(line)
            self.assertFalse(cfg.notes, self.texts(cfg))


class TestXaCfg(Base):
    def test_mine(self):
        d = initfile.mine_xa_cfg(os.path.join(AMS, "init_xa.cfg"), "init_xa.cfg")
        self.assertEqual(d["probe_v"], [("*", "init_xa.cfg:4")])
        self.assertEqual(d["probe_i"], [("tb.dut.x1.m1", "init_xa.cfg:5"), ("tb.dut.x1.m2", "init_xa.cfg:5")])
        self.assertEqual(d["case"], "sensitive")
        self.assertEqual([(n.severity, n.origin) for n in d["notes"]],
                         [(NOTE, "init_xa.cfg:2"), (NOTE, "init_xa.cfg:3"), (NOTE, "init_xa.cfg:4")])
        bad = initfile.mine_xa_cfg(os.path.join(self.tmp, "none.cfg"))
        self.assertTrue(has_errors(bad["notes"]))

    def test_case_reaches_rules(self):
        self.write("cfg/xa.cfg", "set_sim_case lower\n")
        self.write("cfg/xa2.cfg", "set_sim_case -case sensitive\n")
        self.write("cfg/bad.cfg", "set_sim_case -case shouting\n")
        cfg = self.parse("choose xa a.sp -c cfg/xa.cfg;")
        self.assertEqual(cfg.xa, {"probe_v": [], "probe_i": [], "case": "lower"})
        self.assertFalse(rules.case_sensitive(cfg))
        cfg = self.parse("choose xa a.sp -c cfg/xa.cfg -C cfg/xa2.cfg;")   # the later file wins
        self.assertTrue(rules.case_sensitive(cfg))
        cfg = self.parse("choose xa a.sp -c cfg/bad.cfg;")
        self.only(cfg, WARNING, "set_sim_case -case shouting is not understood")
        self.assertIsNone(cfg.xa["case"])

    def test_cfg_found_beside_the_control_file(self):
        # §4.3.1: the vcs cwd first, then the directory of the file that names it
        self.write("ctl/xa.cfg", "probe_waveform_voltage tb.v\n")
        self.write("ctl/vcsAD.init", "choose xa a.sp -c xa.cfg;\n")
        cfg = initfile.parse_control(["ctl/vcsAD.init"], self.tmp, {})
        self.assertEqual(cfg.xa["probe_v"], [("tb.v", os.path.join("ctl", "xa.cfg") + ":1")])
        self.write("xa.cfg", "probe_waveform_voltage tb.w\n")
        cfg = initfile.parse_control(["ctl/vcsAD.init"], self.tmp, {})
        self.assertEqual(cfg.xa["probe_v"], [("tb.w", "xa.cfg:1")])
        n = self.only(cfg, NOTE, "xa.cfg is used", "also exists")
        self.assertEqual(n.origin, os.path.join("ctl", "vcsAD.init") + ":1")


if __name__ == "__main__":
    unittest.main()
