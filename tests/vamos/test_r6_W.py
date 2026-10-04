"""Round-6 W items: waves, the run footer, nvc's interrupt, VHPI and analyser fixes.

R6W-01  $dumpfile/$dumpvars write a four-state VCD: vcs.dump_request records the calls (with
        the $test$plusargs tests they run under) in job.dump, simv.wave_request turns them
        into nvc --wave/--dump-scope options (backends/nvc.Waves), and nvc's wave writer
        dumps the sv2vhdl LOGIC3D type and its vectors as 0/1/x/z values, Verilog scope kinds,
        no scope left with nothing to dump, and an empty VCD instead of a fatal error
R6W-02  the footer's time is the time nvc reports the run ended at (NVC_REPORT_END_TIME)
R6W-03  a second SIGINT in a co-simulation ends it with the interrupted line, exit 130
R6W-04  a VHPI plugin with a unit from nvc's native Verilog parser: no crash
R6W-05  --std=2040 analyses a NUMERIC_STD bit-string literal with no STD_LOGIC_1164 use
R6W-06  nvc's Verilog route removes its temporary translation directories
R6W-07  nvc prints its fused-block accel note only under NVC_ACCEL_JIT_DEBUG

    cd tests/vamos && python3 -m unittest test_r6_W -v

The unit tests run anywhere (Cygwin python3.9 too); the end-to-end ones need nvc and
iverilog (WSL/Linux), the VCD comparison vvp, the co-simulation one VACASK and gdb.
VAMOS_NVC selects the nvc.  Names new in this round are looked up when a test runs, so
against an older vamos each of its tests fails on its own.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import unittest

from vamos_testlib import ROOT, TempDir, have_vacask, needs_stack, run

from vamos import tools
from vamos.ams import verilog_ports as vp
from vamos.backends import nvc as nvcb
from vamos.job import Job, JobVersionError
from vamos.optable import scan
from vamos.personalities import simv, vcs

SHIMS = os.path.join(ROOT, "shims")


# =============================================================================
# unit tests (no tools needed)
# =============================================================================

class TestEndTimeLine(unittest.TestCase):
    """R6W-02: nvc's end line is taken for the footer and never printed."""

    def filt(self):
        out, err = [], []
        return nvcb.OutputFilter(out.append, err.append), out, err

    def test_end_line_is_taken_not_printed(self):
        for why in ("stop time", "no more events", "stopped", "interrupted"):
            f, out, err = self.filt()
            f.feed("** Note: simulation ended at 10ns (%s)" % why)
            self.assertEqual((f.end_time, f.end_why, out, err), ("10ns", why, [], []))

    def test_a_report_is_never_the_end_line(self):
        # a testbench $display of the same words is a report: time-stamped, printed
        f, out, _ = self.filt()
        f.feed("** Note: 5ns+0: simulation ended at 5ns (stopped)")
        self.assertIsNone(f.end_time)
        self.assertEqual(out, ["simulation ended at 5ns (stopped)"])

    def test_footer_time(self):
        f, _, _ = self.filt()
        f.feed("** Note: 7ns+0: hello")
        # no end line (an nvc killed, or one without NVC_REPORT_END_TIME): as before
        self.assertEqual(nvcb.end_time(f, 100 * 10 ** 6, 0, None), "100ns")
        self.assertEqual(nvcb.end_time(f, None, 0, None), "7ns")
        # the end line wins: a run that ran out of events before +vcs+finish+N
        f.feed("** Note: simulation ended at 10ns (no more events)")
        self.assertEqual(nvcb.end_time(f, 100 * 10 ** 6, 0, None), "10ns")
        f2, _, _ = self.filt()
        f2.feed("** Note: simulation ended at 0ms (no more events)")
        self.assertEqual(nvcb.end_time(f2, None, 0, None), "0")

    def test_memories_note_in_simv_terms(self):
        f, out, err = self.filt()
        f.feed("** Note: arrays of composite types such as LOGIC3D_VECTOR are not not dumped by "
               "default, pass --dump-arrays to include these in the waveform dump")
        self.assertEqual(out, [])
        self.assertEqual(err, ["vamos: note: memories (unpacked arrays) are not in the VCD, as "
                               "under VCS; ./simv +vcs+dumparrays adds them"])


def fake_nvc(tmp):
    fake = os.path.join(tmp, "bin", "nvc")
    os.makedirs(os.path.dirname(fake))
    with open(fake, "w") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(fake, stat.S_IRWXU)
    return fake


class TestWavesOptions(TempDir):
    """R6W-01: the nvc -r options for a dump, after -r and before the top."""

    def test_args(self):
        w = nvcb.Waves("/r/w.vcd", [(1, "tb.u1"), (0, "tb.x")], arrays=True)
        self.assertEqual(w.args(), ["--wave=/r/w.vcd", "--format=vcd", "--dump-scope=tb.u1,1",
                                    "--dump-scope=tb.x,0", "--exclude=*_ivl_*", "--dump-arrays"])
        self.assertEqual(nvcb.Waves("/w.vcd").args(), ["--wave=/w.vcd", "--format=vcd",
                                                       "--exclude=*_ivl_*"])

    @unittest.skipUnless(os.name == "posix", "a shell-script stand-in for nvc")
    def test_run_command(self):
        os.environ["VAMOS_NVC"] = fake_nvc(self.tmp)
        job = Job(personality="vcs", daidir=os.path.join(self.tmp, "simv.daidir"))
        be = nvcb.NvcBackend(job, lambda s: None)
        cmd, env = be.run_command("tb", ["+a"], ["--stop-time=5fs"])
        self.assertNotIn("--format=vcd", cmd)
        self.assertEqual(env.get("NVC_REPORT_END_TIME"), "1")
        be.waves = nvcb.Waves("/r/w.vcd", [(2, "tb.u1")])
        cmd, env = be.run_command("tb", ["+a"], ["--stop-time=5fs"])
        r = cmd.index("-r")
        self.assertEqual(cmd[r:], ["-r", "--stop-time=5fs", "--wave=/r/w.vcd", "--format=vcd",
                                   "--dump-scope=tb.u1,2", "--exclude=*_ivl_*", "tb", "+a"])


class TestSimvWaveRequest(unittest.TestCase):
    """R6W-01: ./simv's dump: the compiled calls, their plusarg tests, the file options."""

    def rt(self, *args):
        rt = Job(personality="simv", cwd="/run")
        scan(simv.TABLE, list(args), rt, simv._positional, simv._unknown)
        return rt

    def compiled(self, calls=None, files=()):
        c = Job(personality="vcs")
        c.dump = None if calls is None else {"calls": list(calls), "files": list(files)}
        return c

    def call(self, scopes, cond=()):
        return {"scopes": scopes, "origin": "tb.v:3", "if": [list(x) for x in cond]}

    def test_compiled_dump(self):
        w = simv.wave_request(self.compiled([self.call([[1, "tb.u1"]])],
                                            [{"name": "w.vcd", "origin": "tb.v:2", "if": []}]),
                              self.rt())
        self.assertEqual((w.path, w.scopes, w.arrays), ("/run/w.vcd", [(1, "tb.u1")], False))
        w = simv.wave_request(self.compiled([self.call([[1, "tb.u1"]]), self.call([])],
                                            [{"name": "/abs/w.vcd", "origin": "", "if": []}]),
                              self.rt("+vcs+dumparrays"))
        self.assertEqual((w.path, w.scopes, w.arrays), ("/abs/w.vcd", [], True))
        w = simv.wave_request(self.compiled([self.call([[1, "a"], [0, "b"]]),
                                             self.call([[0, "b"]])]), self.rt())
        self.assertEqual(w.scopes, [(1, "a"), (0, "b")])

    def test_no_dump(self):
        self.assertIsNone(simv.wave_request(self.compiled(), self.rt()))
        self.assertIsNone(simv.wave_request(self.compiled([]), self.rt()))

    def test_plusarg_tests(self):
        c = self.compiled([self.call([[0, "tb"]], [("vcd", True)]),
                           self.call([[1, "tb.u"]], [("vcd", False)])],
                          [{"name": "on.vcd", "origin": "", "if": [["vcd", True]]},
                           {"plusarg": "file=", "origin": "", "if": [["file=", True]]}])
        w = simv.wave_request(c, self.rt("+vcd"))
        self.assertEqual((w.path, w.scopes), ("/run/on.vcd", [(0, "tb")]))
        # $test$plusargs is a prefix test: +vcdfull passes $test$plusargs("vcd")
        self.assertEqual(simv.wave_request(c, self.rt("+vcdfull")).scopes, [(0, "tb")])
        w = simv.wave_request(c, self.rt())
        self.assertEqual((w.path, w.scopes), ("/run/verilog.dump", [(1, "tb.u")]))
        w = simv.wave_request(c, self.rt("+file=sub/x.vcd"))
        self.assertEqual(w.path, "/run/sub/x.vcd")
        self.assertIsNone(simv.wave_request(self.compiled([self.call([], [("x", True)])]),
                                            self.rt("+y")))

    def test_file_options(self):
        c = self.compiled([self.call([])])
        self.assertEqual(simv.wave_request(c, self.rt("+vcs+dumpfile+a.vcd")).path, "/run/a.vcd")
        self.assertEqual(simv.wave_request(c, self.rt("-vcd", "b.vcd")).path, "/run/b.vcd")
        self.assertEqual(simv.wave_request(c, self.rt()).path, "/run/" + nvcb.DEFAULT_DUMPFILE)
        self.assertEqual(nvcb.DEFAULT_DUMPFILE, "verilog.dump")
        # the design's $dumpfile overrides them (VCS)
        c2 = self.compiled([self.call([])], [{"name": "d.vcd", "origin": "", "if": []}])
        self.assertEqual(simv.wave_request(c2, self.rt("+vcs+dumpfile+a.vcd")).path, "/run/d.vcd")

    def test_noted_options(self):
        rt = self.rt("+vcs+dumpoff+10+0", "+vcs+dumpon+5+0", "+vcs+flush+dump", "+vcs+dumpfile+")
        self.assertEqual([(u.option, u.disposition) for u in rt.unmapped],
                         [("+vcs+dumpoff+10+0", "noted"), ("+vcs+dumpon+5+0", "noted"),
                          ("+vcs+flush+dump", "noted"), ("+vcs+dumpfile+", "unknown")])
        self.assertEqual(rt.plusargs, [])
        self.assertIn("+vcs+dumpfile+<file>", simv.usage())
        self.assertIn("+vcs+dumparrays", simv.usage())

    def test_waves_problem(self):
        self.assertEqual(simv.waves_problem("/nonexistent-dir-r6w/w.vcd"), "no such directory")
        self.assertEqual(simv.waves_problem(ROOT), "it is a directory")


class TestJobDump(unittest.TestCase):
    def test_round_trip(self):
        j = Job(personality="vcs")
        j.dump = {"calls": [{"scopes": [[0, "tb"]], "origin": "tb.v:5", "if": [["vcd", True]]}],
                  "files": [{"name": "w.vcd", "origin": "tb.v:4", "if": []}]}
        self.assertEqual(Job.from_json(j.to_json()).dump, j.dump)
        self.assertIsNone(Job.from_json(Job(personality="vcs").to_json()).dump)

    def test_an_older_vamos_refuses_it(self):
        d = json.loads(Job(personality="vcs").to_json())
        d["dump2"] = None
        with self.assertRaises(JobVersionError):
            Job.from_json(json.dumps(d))


# A design with every VCD task; the comments are what tgt-vhdl leaves (_norm.sv lines are
# the pp lines here).  mid is instantiated twice, junk never.
DUMP_SRC = """\
module leaf; reg r; endmodule
module mid;
  leaf u_leaf();
  initial $dumpvars(0, u_leaf);
endmodule
module junk;
  initial $dumpvars;
endmodule
module tb;
  reg [7:0] name;
  mid u1();
  mid u2();
  initial begin
    $dumpfile("w.vcd");
    $dumpfile(name);
    $dumpvars(1, tb);
    $dumpvars(2, tb.u1, tb.u1.u_leaf.r);
    $dumpoff;
    $dumpon;
    $dumpall;
    $dumpflush;
    $dumplimit(1000);
    $dumpports(tb, "x.evcd");
  end
endmodule
"""
DUMP_LINES = {"$dumpvars": (4, 7, 16, 17), "$dumpfile": (14, 15), "$dumpoff": (18,),
              "$dumpon": (19,), "$dumpall": (20,), "$dumpflush": (21,), "$dumplimit": (22,),
              "$dumpports": (23,)}


def dump_vhd(lines=DUMP_LINES):
    out = []
    for task, lns in lines.items():
        for ln in lns:
            for _copy in range(2):              # a module translated twice repeats its comments
                out.append("    null;  -- Unsupported system task %s omitted here "
                           "(/d/simv.daidir/nvc/_norm.sv:%d)" % (task, ln))
    return "\n".join(out) + "\n"


def scopes_of(dump):
    return [s for c in dump["calls"] for s in c["scopes"]]


class TestDumpRequest(unittest.TestCase):
    """R6W-01: the design's VCD tasks as the dump record and its notes."""

    def test_record(self):
        pp = vp.from_text(DUMP_SRC)
        dump, notes = vcs.dump_request(dump_vhd(), pp, ["tb"], None)
        self.assertEqual(dump["files"], [{"name": "w.vcd", "origin": "pp.orig.v:14", "if": []}])
        # mid's $dumpvars(0, u_leaf) once per instance of mid; junk is never instantiated
        self.assertEqual(dump["calls"], [
            {"scopes": [[0, "tb.u1.u_leaf"], [0, "tb.u2.u_leaf"]], "origin": "pp.orig.v:4",
             "if": []},
            {"scopes": [[1, "tb"]], "origin": "pp.orig.v:16", "if": []},
            {"scopes": [[2, "tb.u1"], [2, "tb.u1.u_leaf.r"]], "origin": "pp.orig.v:17",
             "if": []}])
        text = [n.text() for n in notes]
        self.assertIn("note: pp.orig.v:15: $dumpfile: the file name is not a string literal, "
                      "which vamos cannot evaluate; the call is ignored", text)
        for ln, task, what in ((18, "$dumpoff", "is not honoured: the VCD records the whole run"),
                               (19, "$dumpon", "is not honoured: the VCD records the whole run"),
                               (20, "$dumpall", "has no effect: the VCD records every change"),
                               (21, "$dumpflush", "has no effect: the VCD is written when the run "
                                                  "ends"),
                               (22, "$dumplimit", "is not honoured: the VCD has no size limit")):
            self.assertIn("note: pp.orig.v:%d: %s %s" % (ln, task, what), text)
        self.assertIn("note: pp.orig.v:4: $dumpvars: ./simv writes a VCD of tb.u1.u_leaf (every "
                      "level), tb.u2.u_leaf (every level); tb (1 level); tb.u1 (2 levels), "
                      "tb.u1.u_leaf.r (2 levels) to w.vcd, from time 0 for the whole run", text)
        self.assertEqual({n.severity for n in notes}, {"note"})
        self.assertFalse([t for t in text if "$dumpports" in t])     # not vamos's: a warning stays

    def test_the_translator_warnings_go(self):
        pp = vp.from_text(DUMP_SRC)
        kept = [n.message for n in vp.unsupported_tasks(dump_vhd(), pp, ams=False)
                if not vcs._is_dump_task_note(n)]
        self.assertEqual(kept, ["system task $dumpports is not translated: the simulation "
                                "drops it"])

    def test_whole_design_and_defaults(self):
        pp = vp.from_text("module tb;\n  initial $dumpvars;\n  initial $dumpvars(1, tb);\nendmodule\n")
        dump, notes = vcs.dump_request(dump_vhd({"$dumpvars": (2, 3)}), pp, ["tb"], None)
        self.assertEqual(dump, {"files": [], "calls": [
            {"scopes": [], "origin": "pp.orig.v:2", "if": []},
            {"scopes": [[1, "tb"]], "origin": "pp.orig.v:3", "if": []}]})
        self.assertEqual([n.text() for n in notes],
                         ["note: pp.orig.v:2: $dumpvars: ./simv writes a VCD of the whole design; "
                          "tb (1 level) to verilog.dump (./simv +vcs+dumpfile+<file> names "
                          "another), from time 0 for the whole run"])

    def test_plus_dumpvars(self):
        # vcs +vcs+dumpvars: a $dumpvars with no arguments, with or without one in the design
        pp = vp.from_text("module tb;\nendmodule\n")
        dump, notes = vcs.dump_request("", pp, ["tb"], None, every=True)
        self.assertEqual(dump, {"files": [], "calls": [
            {"scopes": [], "origin": "+vcs+dumpvars", "if": []}]})
        self.assertEqual([n.text() for n in notes],
                         ["note: +vcs+dumpvars: ./simv writes a VCD of the whole design to "
                          "verilog.dump (./simv +vcs+dumpfile+<file> names another), from time 0 "
                          "for the whole run"])
        job = vcs.build_job(["+vcs+dumpvars", "tb.v"], "/w")
        self.assertTrue(getattr(job, "dumpvars_all", False))
        self.assertEqual(job.plusargs, [])

    def test_dumpfile_alone_writes_nothing(self):
        pp = vp.from_text('module tb;\n  initial $dumpfile("w.vcd");\nendmodule\n')
        dump, notes = vcs.dump_request(dump_vhd({"$dumpfile": (2,)}), pp, ["tb"], None)
        self.assertIsNone(dump)
        self.assertEqual([n.text() for n in notes],
                         ["note: pp.orig.v:2: $dumpfile without $dumpvars: no waves are written "
                          "(dumping starts with $dumpvars)"])

    def test_several_tops_are_under_the_wrapper(self):
        src = ("module sub; endmodule\nmodule alpha; endmodule\nmodule beta;\n  sub s1();\n"
               "  initial $dumpvars(0, s1);\n  initial $dumpvars(1, alpha);\nendmodule\n")
        pp = vp.from_text(src)
        dump, _ = vcs.dump_request(dump_vhd({"$dumpvars": (5, 6)}), pp, ["alpha", "beta"],
                                   "vamos_tops")
        self.assertEqual(scopes_of(dump), [[0, "vamos_tops.top2.s1"], [1, "vamos_tops.top1"]])
        dump, _ = vcs.dump_request(dump_vhd({"$dumpvars": (5,)}), pp, ["alpha", "beta"], None)
        self.assertEqual(scopes_of(dump), [[0, "beta.s1"]])

    def test_instance_array(self):
        # mid's instances are in an instance array: no plain name for its relative scope
        src = ("module mid;\n  reg r;\n  initial $dumpvars(1, r);\nendmodule\n"
               "module tb;\n  mid u[1:0]();\nendmodule\n")
        pp = vp.from_text(src)
        dump, notes = vcs.dump_request(dump_vhd({"$dumpvars": (3,)}), pp, ["tb"], None)
        self.assertEqual(scopes_of(dump), [])
        self.assertIn("note: pp.orig.v:3: $dumpvars: r is relative to mid, whose instances are in "
                      "an instance array, which vamos cannot name; every level of the design is "
                      "dumped", [n.text() for n in notes])

    def test_unreadable_levels(self):
        pp = vp.from_text("module tb;\n  integer n;\n  initial $dumpvars(n, tb);\nendmodule\n")
        dump, notes = vcs.dump_request(dump_vhd({"$dumpvars": (3,)}), pp, ["tb"], None)
        self.assertEqual(scopes_of(dump), [[0, "tb"]])
        self.assertIn("note: pp.orig.v:3: $dumpvars: the level count is not a number vamos can "
                      "read; every level is dumped", [n.text() for n in notes])

    def test_plusarg_guards(self):
        src = """\
module tb;
  reg [8*32:1] fn;
  initial begin
    if ($test$plusargs("vcd")) begin
      $dumpfile("waves.vcd");
      $dumpvars(0, tb);
    end
  end
  initial if (!$test$plusargs("quiet")) $dumpvars(1, tb); else $dumpvars(2, tb);
  initial if ($value$plusargs("wave=%s", fn)) begin $dumpfile(fn); $dumpvars; end
endmodule
"""
        pp = vp.from_text(src)
        dump, notes = vcs.dump_request(
            dump_vhd({"$dumpfile": (5, 10), "$dumpvars": (6, 9, 10)}), pp, ["tb"], None)
        self.assertEqual(dump["files"], [
            {"name": "waves.vcd", "origin": "pp.orig.v:5", "if": [["vcd", True]]},
            {"plusarg": "wave=", "origin": "pp.orig.v:10", "if": [["wave=", True]]}])
        self.assertEqual([(c["scopes"], c["if"]) for c in dump["calls"]], [
            ([[0, "tb"]], [["vcd", True]]),
            ([[1, "tb"]], [["quiet", False]]),
            ([], [["wave=", True]])])
        self.assertEqual([n.text() for n in notes], [
            "note: pp.orig.v:6: $dumpvars: ./simv writes a VCD of tb (every level) when "
            "./simv gets +vcd; tb (1 level) when ./simv gets no +quiet; the whole design when "
            "./simv gets +wave= to the $dumpfile name ./simv's plusargs select, else "
            "verilog.dump, from time 0 for the whole run"])
        # (the else branch's $dumpvars(2, tb) is on line 9 too: one comment per task and line)

    def test_conditions_vamos_cannot_evaluate(self):
        src = ("module tb;\n  reg en;\n  integer i;\n"
               "  initial if (en) $dumpvars;\n"
               "  initial for (i = 0; i < 1; i = i + 1) $dumpvars(1, tb);\n"
               "  task t; $dumpvars(2, tb); endtask\n"
               "  initial if (!en) repeat (2) if ($test$plusargs(\"x\")) $dumpvars(3, tb);\n"
               "endmodule\n")
        pp = vp.from_text(src)
        dump, notes = vcs.dump_request(dump_vhd({"$dumpvars": (4, 5, 6, 7)}), pp, ["tb"], None)
        self.assertEqual([c["if"] for c in dump["calls"]], [[], [], [], [["x", True]]])
        text = [n.text() for n in notes]
        for ln, what in ((4, "if (en)"), (5, "a for loop"), (6, "task t"),
                         (7, "a repeat loop (and 1 more condition)")):
            self.assertIn("note: pp.orig.v:%d: $dumpvars: the call is inside %s, which vamos "
                          "cannot evaluate before the run: ./simv acts as if it runs"
                          % (ln, what), text)


class TestConditions(unittest.TestCase):
    """R6W-01: the conditions each system task call runs under (vcs._Conditions)."""

    def conds(self, body):
        src = "module tb;\n  reg [7:0] f;\n  reg en, c;\n%s\nendmodule\n" % body
        pp = vp.from_text(src)
        toks = pp.toks()
        m = pp.modules["tb"][0]
        at = vcs._Conditions(toks, m.tok_hdr + 1, m.tok_end).at
        return [(toks[k].text, pp.line_of(toks[k].start) - 3, v) for k, v in sorted(at.items())]

    def test_statements(self):
        body = """\
initial begin : blk
  #10 $a;
  #5ns @(posedge c) $b;
  fork $c; join
  if ($test$plusargs("x")) $d; else if (en) $e; else $f;
  case (f) 1, 2: $g; default: $h; endcase
  forever begin wait (en) $i; end
  repeat (3) $j;
  do $k; while (en);
end : blk
always @(posedge c) if (!($test$plusargs("y"))) $l;"""
        x, other = ("plusarg", "x", True, None), lambda w: ("other", w)
        nx = ("plusarg", "x", False, None)
        self.assertEqual(self.conds(body), [
            ("$a", 2, []), ("$b", 3, []), ("$c", 4, []),
            ("$d", 5, [x]), ("$e", 5, [nx, other("if (en)")]),
            ("$f", 5, [nx, other("the else branch of if (en)")]),
            ("$g", 6, [other("a case statement")]), ("$h", 6, [other("a case statement")]),
            ("$i", 7, []), ("$j", 8, [other("a repeat loop")]), ("$k", 9, []),
            ("$l", 11, [("plusarg", "y", False, None)])])

    def test_value_plusargs_and_subprograms(self):
        body = """\
initial if ($value$plusargs("vcd=%s", f)) $dumpfile(f);
function integer g(input x); begin $display("g"); g = 1; end endfunction
task automatic t(input x); $dumpvars; endtask"""
        self.assertEqual(self.conds(body), [
            ("$dumpfile", 1, [("plusarg", "vcd=", True, "f")]),
            ("$display", 2, [("other", "function g")]),
            ("$dumpvars", 3, [("other", "task t")])])

    def test_deep_nesting(self):
        # a long else-if chain is walked in one frame; nesting past Python's stack loses only
        # the conditions of the calls in that process
        chain = "always @(f) " + " else ".join("if (f == %d) c = 1;" % k for k in range(3000))
        nest = "initial " + "begin " * 1500 + "$z;" + " end" * 1500
        got = self.conds(chain + " else if ($test$plusargs(\"w\")) $e;\n" + nest +
                         "\ninitial if ($test$plusargs(\"v\")) $d;")
        self.assertEqual(got[0], ("$e", 1, [("other", "the else branch of if (f == %d)" % k)
                                             for k in range(3000)] +
                                  [("plusarg", "w", True, None)]))
        self.assertEqual(got[1:], [("$d", 3, [("plusarg", "v", True, None)])])

    def test_garbage_never_hangs(self):
        # unbalanced or unexpected text: the walk ends, whatever it finds
        for body in ("initial begin if (", "initial case (f) 1 $a; endcase", "initial @",
                     "initial begin end end end", "initial #", "always"):
            self.conds(body)


# =============================================================================
# end to end (nvc + iverilog: WSL/Linux)
# =============================================================================

def nvc_bin():
    return tools.find_real("nvc")


def nvc_lib():
    return tools.nvc_libdir(nvc_bin())


def vvp_bin():
    iv = tools.find_real("iverilog")
    for c in (tools.find_real("vvp") or "", os.path.join(os.path.dirname(iv or "/"), "vvp")):
        if c and os.access(c, os.X_OK):
            return c
    return ""


WAVE_TB = """\
`timescale 1ns/1ps
module leaf(input [1:0] a, output [1:0] y);
  wire [1:0] inner = ~a;
  assign y = inner & a;
endmodule
module mid(input clk, output reg [3:0] q);
  wire [1:0] lo;
  leaf u_leaf(.a(q[1:0]), .y(lo));
  initial q = 4'b1x0z;
  always @(posedge clk) q <= {q[2:0], q[3]};
endmodule
module tb;
  reg clk = 0;
  reg [3:0] cnt = 0;
  wire [3:0] q;
  reg xx;
  always #5 clk = ~clk;
  always @(posedge clk) cnt <= cnt + 1;
  mid u_mid(.clk(clk), .q(q));
  initial begin
    xx = 1'bx;
    $dumpfile("waves.vcd");
    $dumpvars(0, tb);
    #30 $display("cnt=%0d", cnt);
    $finish;
  end
endmodule
"""

VECTORS_ONLY = """\
`timescale 1ns/1ps
module tb2;
  reg [7:0] a = 0;
  initial begin
    $dumpvars;
    #10 a = 5;
    #10 $finish;
  end
endmodule
"""

EVENTS_END = """\
`timescale 1ns/1ps
module ev;
  reg a = 0;
  initial begin #7 a = 1; #3 a = 0; end
endmodule
"""

GUARDED = """\
`timescale 1ns/1ps
module gd;
  reg [3:0] a = 0;
  always #5 a = a + 1;
  initial begin
    if ($test$plusargs("vcd")) begin
      $dumpfile("gd.vcd");
      $dumpvars(0, gd);
    end
    #20 $finish;
  end
endmodule
"""

# Signals of every kind against vvp's own VCD: x and z bits in vectors, a tristate bus, a
# pull-up, an integer, a real, ports collapsed into nets, and more than 1000 ns with five
# processes on the clock (nvc's fast-clock table and fused block take them over then)
COMPARE_TB = """\
`timescale 1ns/1ps
module leaf(input [1:0] a, input en, output [1:0] y, inout [1:0] bus);
  wire [1:0] inner = ~a;
  assign y = inner;
  assign bus = en ? a : 2'bzz;
endmodule
module mid(input clk, output reg [3:0] q);
  wire [1:0] lo;
  wire [1:0] bus;
  reg en;
  leaf u_leaf(.a(q[1:0]), .en(en), .y(lo), .bus(bus));
  initial begin q = 4'b0110; en = 0; #23 en = 1; end
  always @(posedge clk) q <= q + 1;
endmodule
module tb;
  reg clk = 0;
  reg [3:0] cnt = 0;
  reg [7:0] r0 = 0, r1 = 1, r2 = 2, r3 = 3, r4 = 4;
  wire [3:0] q;
  reg xx;
  wire pz;
  wire a_and = clk & xx;
  wire [3:0] mixed = {xx, 1'bz, q[1:0]};
  integer k;
  real rv;
  pullup(pz);
  always #5 clk = ~clk;
  always @(posedge clk) cnt <= cnt + 1;
  always @(posedge clk) r0 <= r0 + 1;
  always @(posedge clk) r1 <= r1 + r0;
  always @(posedge clk) r2 <= r2 ^ r1;
  always @(posedge clk) r3 <= r3 + r2;
  always @(posedge clk) r4 <= r4 - r3;
  mid u_mid(.clk(clk), .q(q));
  initial begin
    xx = 1'bx;
    k = 0;
    rv = 0.5;
    $dumpfile("cmp.vcd");
    $dumpvars(0, tb);
    #17 xx = 1;
    #10 xx = 1'bz;
    #10 xx = 0;
    repeat (5) #7 k = k + 3;
    rv = 2.25;
    #2000 $display("cnt=%0d r4=%0d", cnt, r4);
    $finish;
  end
endmodule
"""


class Vcd:
    """A small VCD reader: scopes, vars (type, size, id by hierarchical name), the value of
    each id at the end of the time-0 $dumpvars block, and every variable's changes."""

    UNITS = {"s": 10 ** 15, "ms": 10 ** 12, "us": 10 ** 9, "ns": 10 ** 6, "ps": 10 ** 3, "fs": 1}

    def __init__(self, text):
        self.text = text
        self.vars = {}            # "tb.u_mid.q" -> (type, size, id)
        self.scopes = []          # (type, path)
        self.initial = {}         # id -> value
        self.changes = {}         # id -> [(fs, value)]
        toks = text.split()       # (an id may contain '$': read words, not up to a '$')
        path, scale, i, now, in_dumpvars = [], 1, 0, 0, False
        while i < len(toks):
            w = toks[i]
            if w == "$timescale":
                m = re.match(r"(\d+)(\w+)", toks[i + 1] + (toks[i + 2] if toks[i + 2] != "$end"
                                                           else ""))
                scale = int(m.group(1)) * self.UNITS[m.group(2)]
                i = toks.index("$end", i) + 1
            elif w == "$scope":
                path.append(toks[i + 2])
                self.scopes.append((toks[i + 1], ".".join(path)))
                i = toks.index("$end", i) + 1
            elif w == "$upscope":
                path.pop()
                i = toks.index("$end", i) + 1
            elif w == "$var":
                end = toks.index("$end", i)
                name = re.sub(r"\[.*$", "", toks[i + 4])
                self.vars[".".join(path + [name])] = (toks[i + 1], int(toks[i + 2]), toks[i + 3])
                i = end + 1
            elif w == "$dumpvars":
                in_dumpvars = True
                i += 1
            elif w == "$end":
                in_dumpvars = False
                i += 1
            elif w.startswith("$"):
                i = toks.index("$end", i) + 1
            elif w.startswith("#"):
                now = int(w[1:]) * scale
                i += 1
            elif w[0] in "bBrR":
                self._set(toks[i + 1], w[1:], now, in_dumpvars)
                i += 2
            else:
                self._set(w[1:], w[0], now, in_dumpvars)
                i += 1

    def _set(self, ident, val, now, initial):
        if initial:
            self.initial[ident] = val
        lst = self.changes.setdefault(ident, [])
        if lst and lst[-1][0] == now:
            lst[-1] = (now, val)
        else:
            lst.append((now, val))

    def init(self, path):
        return self.initial.get(self.vars[path][2])

    def history(self, path):
        """The variable's (fs, value) changes, values as full-width lower-case bit strings."""
        kind, width, ident = self.vars[path]
        out = []
        for t, v in self.changes.get(ident, []):
            v = v.lower()
            if kind != "real" and len(v) < width:
                v = (v[0] if v[0] in "xz" else "0") * (width - len(v)) + v
            if not out or out[-1][1] != v:
                out.append((t, v))
        return out


@needs_stack
class TestWavesE2E(TempDir):
    """R6W-01 end to end: vcs records the dump, ./simv writes the VCD where it runs."""

    def tool(self, *args, cwd=None):
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        env.pop("VAMOS_ANALOG", None)
        return run(["vcs"] + list(args), cwd=cwd or self.tmp, env=env, timeout=600)

    def simv(self, *args, cwd=None):
        return run([os.path.join(self.tmp, "simv")] + list(args), cwd=cwd or self.tmp,
                   timeout=600)

    def test_hierarchy_four_state_vectors(self):
        self.write("tb.v", WAVE_TB)
        c = self.tool("tb.v")
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertIn("vamos: note: tb.v:23: $dumpvars: ./simv writes a VCD of tb (every level) to "
                      "waves.vcd, from time 0 for the whole run", c.stdout)
        self.assertNotIn("is not translated", c.stdout)
        with open(os.path.join(self.tmp, "simv.daidir", "vamos.job.json")) as fh:
            self.assertEqual(json.load(fh)["dump"], {
                "calls": [{"scopes": [[0, "tb"]], "origin": "tb.v:23", "if": []}],
                "files": [{"name": "waves.vcd", "origin": "tb.v:22", "if": []}]})
        os.makedirs(os.path.join(self.tmp, "run"))
        r = self.simv(cwd=os.path.join(self.tmp, "run"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("cnt=3", r.stdout)
        self.assertNotIn("Fatal", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "waves.vcd")))
        vcd = Vcd(self.read("run/waves.vcd"))
        # module scopes, as Verilog's own VCD has them; none for the translator's gates
        self.assertEqual(vcd.scopes, [("module", "tb"), ("module", "tb.u_mid"),
                                      ("module", "tb.u_mid.u_leaf")])
        self.assertEqual(vcd.vars["tb.cnt"][:2], ("wire", 4))
        self.assertEqual(vcd.vars["tb.clk"][:2], ("wire", 1))
        self.assertEqual(vcd.init("tb.u_mid.q"), "1x0z")          # four-state values
        self.assertEqual(vcd.init("tb.xx"), "x")
        self.assertEqual(vcd.init("tb.cnt"), "0000")
        self.assertEqual(vcd.init("tb.clk"), "0")
        self.assertEqual(vcd.history("tb.u_mid.q")[:3], [(0, "1x0z"), (5000000, "x0z1"),
                                                         (15000000, "0z1x")])
        self.assertIn("tb.u_mid.u_leaf.a", vcd.vars)                # a port collapsed into a net
        self.assertFalse([v for v in vcd.vars if "_ivl_" in v])     # no translator temporaries
        self.assertNotIn("$attrbegin", vcd.text)
        self.assertIn("#25000000\n", vcd.text)                      # 1fs timescale

    def test_levels(self):
        self.write("tb.v", WAVE_TB.replace("$dumpvars(0, tb);", "$dumpvars(1, tb);"))
        self.assertEqual(self.tool("tb.v").returncode, 0)
        r = self.simv()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("names no instance", r.stdout)
        vcd = Vcd(self.read("waves.vcd"))
        self.assertEqual(sorted(vcd.vars), ["tb.clk", "tb.cnt", "tb.q", "tb.xx"])
        self.assertEqual(vcd.scopes, [("module", "tb")])

    def test_vectors_only_and_default_file(self):
        # nvc: "fstReaderOpen failed for temporary FST file" when no variable was dumped,
        # which was every vector; the VCD stored LOGIC3D as 32-bit integer codes
        self.write("tb2.v", VECTORS_ONLY)
        c = self.tool("tb2.v")
        self.assertEqual(c.returncode, 0, c.stdout)
        r = self.simv()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("Fatal", r.stdout)
        vcd = Vcd(self.read(nvcb.DEFAULT_DUMPFILE))
        self.assertEqual(vcd.vars["tb2.a"][:2], ("wire", 8))
        self.assertEqual(vcd.init("tb2.a"), "00000000")
        self.assertIn("b00000101 " + vcd.vars["tb2.a"][2], vcd.text)

    def test_plusarg_guard_and_file_options(self):
        self.write("gd.v", GUARDED)
        c = self.tool("gd.v")
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertIn("vamos: note: gd.v:8: $dumpvars: ./simv writes a VCD of gd (every level) to "
                      "gd.vcd when ./simv gets +vcd, from time 0 for the whole run", c.stdout)
        r = self.simv()                                   # no +vcd: no waves, as under VCS
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual([f for f in os.listdir(self.tmp) if f.endswith((".vcd", ".dump"))], [])
        r = self.simv("+vcd", "+vcs+dumpfile+other.vcd")  # the design's $dumpfile wins
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(Vcd(self.read("gd.vcd")).history("gd.a")[:2],
                         [(0, "0000"), (5000000, "0001")])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "other.vcd")))

    def test_plus_dumpvars_compile_option(self):
        self.write("ev.v", EVENTS_END)
        c = self.tool("+vcs+dumpvars", "ev.v")
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertIn("vamos: note: +vcs+dumpvars: ./simv writes a VCD of the whole design",
                      c.stdout)
        r = self.simv("-vcd", "all.vcd")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(sorted(Vcd(self.read("all.vcd")).vars), ["ev.a"])

    def test_dump_directory_missing(self):
        # nvc stops before simulating when it cannot create the VCD: the run goes on without it
        self.write("tb.v", WAVE_TB.replace('"waves.vcd"', '"nodir/waves.vcd"'))
        self.assertEqual(self.tool("tb.v").returncode, 0)
        r = self.simv()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("cnt=3", r.stdout)
        self.assertIn("vamos: warning: the run writes no waves: %s cannot be written (no such "
                      "directory)" % os.path.join(self.tmp, "nodir", "waves.vcd"), r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "nodir")))

    def test_memories(self):
        self.write("m.v", "module m;\n  reg [7:0] mem [0:1];\n  initial begin mem[1] = 8'h5a; "
                          "$dumpvars; #1 $finish; end\nendmodule\n")
        self.assertEqual(self.tool("m.v").returncode, 0)
        r = self.simv()
        self.assertIn("vamos: note: memories (unpacked arrays) are not in the VCD, as under VCS; "
                      "./simv +vcs+dumparrays adds them", r.stdout)
        self.assertEqual(sorted(Vcd(self.read("verilog.dump")).vars), [])
        r = self.simv("+vcs+dumparrays")
        self.assertNotIn("memories", r.stdout)
        text = self.read("verilog.dump")
        words = dict((w, i) for i, w in re.findall(r"\$var wire 8 (\S+) (mem\[\d\])\[7:0\] \$end",
                                                    text))
        self.assertEqual(sorted(words), ["mem[0]", "mem[1]"])      # one variable per word
        self.assertRegex(text, r"\nb0*1011010 %s\n" % re.escape(words["mem[1]"]))

    def test_nvc_empty_vcd_is_no_error(self):
        # every signal left out by --dump-scope: a valid VCD with no variables and a warning,
        # not "fstReaderOpen failed" (a fatal error, exit status 1)
        self.write("ev.v", EVENTS_END)
        self.assertEqual(self.tool("ev.v").returncode, 0)
        be = nvcb.NvcBackend(Job.from_json(self.read("simv.daidir/vamos.job.json")),
                             lambda s: None)
        be.waves = nvcb.Waves(os.path.join(self.tmp, "none.vcd"), [(0, "ev.nosuch")])
        cmd, env = be.run_command("ev", [])
        p = run(cmd, cwd=self.tmp, env=env)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("no signals were written to the waveform dump", p.stdout)
        self.assertIn("waveform dump scope ev.nosuch (--dump-scope) names no instance or signal "
                      "of the design: nothing is dumped for it", p.stdout)
        text = self.read("none.vcd")
        self.assertIn("$enddefinitions $end\n#10000000\n", text)
        self.assertNotIn("$var", text)

    @unittest.skipUnless(vvp_bin(), "needs vvp")
    def test_same_changes_as_vvp(self):
        # every variable vvp dumps has the same value changes in nvc's VCD (names as the
        # translation has them: a VHDL reserved word gets _sig)
        self.write("cmp.v", COMPARE_TB)
        os.makedirs(os.path.join(self.tmp, "iv"))
        iv = run([tools.find_real("iverilog"), "-g2012", "-o", "iv/a.out", "cmp.v"], cwd=self.tmp)
        self.assertEqual(iv.returncode, 0, iv.stdout)
        p = run([vvp_bin(), "-n", "a.out"], cwd=os.path.join(self.tmp, "iv"))
        self.assertIn("cnt=15 r4=75", p.stdout)
        self.assertEqual(self.tool("cmp.v").returncode, 0)
        r = self.simv()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("cnt=15 r4=75", r.stdout)
        ref, ours = Vcd(self.read("iv/cmp.vcd")), Vcd(self.read("cmp.vcd"))
        names = {n.replace("bus_sig", "bus"): n for n in ours.vars}
        self.assertEqual(sorted(set(ref.vars) - set(names)), [])
        self.assertEqual(sorted(set(names) - set(ref.vars)), ["tb.u_mid.q_reg"])
        for n in sorted(ref.vars):
            self.assertEqual(ours.history(names[n]), ref.history(n), n)
        self.assertEqual(sorted(ours.scopes), sorted(ref.scopes))


@needs_stack
class TestFooterE2E(TempDir):
    """R6W-02: the footer shows where a run ended, even before +vcs+finish+N."""

    def setUp(self):
        super().setUp()
        self.write("ev.v", EVENTS_END)
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        c = run(["vcs", "ev.v"], cwd=self.tmp, env=env, timeout=600)
        self.assertEqual(c.returncode, 0, c.stdout)

    def footer(self, *args):
        r = run([os.path.join(self.tmp, "simv")] + list(args), cwd=self.tmp, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("simulation ended", r.stdout)
        m = re.search(r"^Time: (\S+)$", r.stdout, re.M)
        self.assertTrue(m, r.stdout)
        return m.group(1)

    def test_ran_out_of_events_first(self):
        self.assertEqual(self.footer("+vcs+finish+100000"), "10ns")   # 100ns at 1ps

    def test_bounded(self):
        self.assertEqual(self.footer("+vcs+finish+4000"), "4ns")

    def test_unbounded(self):
        self.assertEqual(self.footer(), "10ns")

    def test_nvc_end_line(self):
        be = nvcb.NvcBackend(Job.from_json(self.read("simv.daidir/vamos.job.json")),
                             lambda s: None)
        for args, line in (([], "simulation ended at 10ns (no more events)"),
                           (["--stop-time=4ns"], "simulation ended at 4ns (stop time)"),
                           (["--stop-time=10ns"], "simulation ended at 10ns (no more events)")):
            cmd, env = be.run_command("ev", [], args)
            p = run(cmd, cwd=self.tmp, env=env)
            self.assertEqual(p.returncode, 0, p.stdout)
            self.assertEqual(p.stdout.strip().splitlines()[-1], "** Note: " + line, p.stdout)
        cmd, env = be.run_command("ev", [], [])
        del env["NVC_REPORT_END_TIME"]
        self.assertNotIn("simulation ended", run(cmd, cwd=self.tmp, env=env).stdout)


CLOCKED = """\
`timescale 1ns/1ps
module clk6;
  reg clk = 0;
  reg [7:0] r0 = 0, r1 = 1, r2 = 2, r3 = 3, r4 = 4, r5 = 5;
  always #5 clk = ~clk;
  always @(posedge clk) r0 <= r0 + 1;
  always @(posedge clk) r1 <= r1 + r0;
  always @(posedge clk) r2 <= r2 ^ r1;
  always @(posedge clk) r3 <= r3 + r2;
  always @(posedge clk) r4 <= r4 - r3;
  always @(posedge clk) r5 <= r5 + r4;
  initial begin
    #3000 $display("r5=%0d", r5);
    $finish;
  end
endmodule
"""


@needs_stack
class TestAccelNote(TempDir):
    """R6W-07: nvc builds its fast-clock table and fused block by itself after 1000 ns; its
    note about the block ("accel-jit: NVC_FUSED_BLOCK ...") was printed on stdout in every
    such simulation, mixed into the design's output."""

    def nvc(self, extra_env=None):
        env = dict(os.environ)
        env.pop("NVC_ACCEL_JIT_DEBUG", None)
        env.update(extra_env or {})
        return run([nvc_bin(), "--std=2040", "-L", nvc_lib(), "-a", "clk6.v", "-e", "clk6",
                    "-r"], cwd=self.tmp, env=env)

    def test_quiet_by_default(self):
        self.write("clk6.v", CLOCKED)
        p = self.nvc()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("r5=252", p.stdout)
        self.assertNotIn("accel-jit", p.stdout)
        # the block is still built: its note is there under the debug variable
        p = self.nvc({"NVC_ACCEL_JIT_DEBUG": "1"})
        self.assertIn("r5=252", p.stdout)
        self.assertIn("accel-jit: NVC_FUSED_BLOCK", p.stdout)


MIXED_SENS = """\
module mixsens;
  reg a, b; reg [3:0] n;
  always @(a or posedge b) n <= n + 1;
  initial begin n = 0; a <= 0; b <= 0; #1 b <= 1; #1 $display("mixsens n=%0d", n); end
endmodule
"""

MIXED_TOP = """\
entity mixtop is end entity;
architecture a of mixtop is
  component mixsens is end component;
begin
  u_v: component mixsens;
  process begin wait for 5 ns; report "mixtop done"; wait; end process;
end architecture;
"""


@needs_stack
class TestVhpiNativeVerilog(TempDir):
    """R6W-04: VHPI plugins and units from nvc's native Verilog parser.  SV2GHDL=/bin/false
    makes nvc's translation fail, so it falls back to the native parser."""

    def nvc(self, *args, load=None):
        env = dict(os.environ)
        env["SV2GHDL"] = "/bin/false"
        cmd = [nvc_bin(), "--std=2040", "-L", nvc_lib()]
        if load:
            cmd.append("--load=" + os.path.join(nvc_lib(), "sv2vhdl", load))
        return run(cmd + list(args), cwd=self.tmp, env=env)

    def test_verilog_top_with_a_foreign_function_plugin(self):
        self.write("mixsens.v", MIXED_SENS)
        p = self.nvc("-a", "mixsens.v", "-e", "mixsens", "-r", load="libsv_math.so")
        self.assertIn("trying native parser", p.stdout)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertRegex(p.stdout, r"(?m)mixsens n=\s*2$")
        self.assertIn("the top-level unit WORK.MIXSENS is a Verilog module, which VHPI does not "
                      "model", p.stdout)
        self.assertNotIn("unsupported tree kind", p.stdout)

    def test_verilog_top_with_a_hierarchy_walking_plugin(self):
        self.write("mixsens.v", MIXED_SENS)
        p = self.nvc("-a", "mixsens.v", "-e", "mixsens", "-r", load="libresolver.so")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("resolver: ERROR - cannot get root instance", p.stdout)
        self.assertRegex(p.stdout, r"(?m)mixsens n=\s*2$")

    def test_verilog_instance_in_a_vhdl_hierarchy(self):
        self.write("mixsens.v", MIXED_SENS)
        self.write("mixtop.vhd", MIXED_TOP)
        p = self.nvc("-a", "mixsens.v", "mixtop.vhd", "-e", "mixtop")
        self.assertEqual(p.returncode, 0, p.stdout)
        p = self.nvc("-r", "mixtop", load="libresolver.so")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("instance U_V is a Verilog module, which VHPI does not model: it and any "
                      "other Verilog instance are left out of the VHPI hierarchy", p.stdout)
        self.assertRegex(p.stdout, r"(?m)mixsens n=\s*2$")
        self.assertIn("mixtop done", p.stdout)


BITSTRING = """\
library ieee;
use ieee.numeric_std.all;
entity bits is end entity;
architecture a of bits is
begin
  process
    variable s : unsigned(31 downto 0) := x"5A1D58FB";
    constant c : unsigned(7 downto 0) := b"0101_0101";
    constant d : unsigned(3 downto 0) := "1001";
  begin
    report "s=" & integer'image(to_integer(s)) & " c=" & integer'image(to_integer(c))
      & " d=" & integer'image(to_integer(d));
    wait;
  end process;
end architecture;
"""

AMBIGUOUS = """\
entity amb is end entity;
architecture a of amb is
  type t1 is ('0', '1', 'x');
  type t2 is ('0', '1', 'y');
  procedure p(v : t1) is begin end procedure;
  procedure p(v : t2) is begin end procedure;
begin
  process begin p('0'); wait; end process;
end architecture;
"""


@needs_stack
class TestAnalyser2040(TempDir):
    """R6W-05: under --std=2040 the analyser segfaulted (simp_ref, NULL decl) on a NUMERIC_STD
    string or bit-string literal when no use clause made STD_LOGIC_1164's literals visible."""

    def nvc(self, *args):
        return run([nvc_bin(), "--std=2040", "-L", nvc_lib()] + list(args), cwd=self.tmp)

    def test_literals_of_the_expected_type(self):
        self.write("bits.vhd", BITSTRING)
        p = self.nvc("-a", "bits.vhd", "-e", "bits", "-r")
        self.assertNotIn("Caught signal", p.stdout)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("s=1511872763 c=85 d=9", p.stdout)

    def test_ambiguous_literal_is_an_error(self):
        self.write("amb.vhd", AMBIGUOUS)
        p = self.nvc("-a", "amb.vhd")
        self.assertNotIn("Caught signal", p.stdout)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("ambiguous use of enumeration literal '0'", p.stdout)


@needs_stack
class TestVerilogRouteTempDir(TempDir):
    """R6W-06: nvc -a x.v translates through iverilog-sv2ghdl into a temporary directory, which
    stayed behind (/tmp/nvc_sv2ghdl_<pid>); it now goes under TMPDIR and is removed at exit."""

    def run_nvc(self, files, top, extra_env=None):
        tmpdir = os.path.join(self.tmp, "t")
        os.makedirs(tmpdir, exist_ok=True)
        env = dict(os.environ)
        env["TMPDIR"] = tmpdir
        env.pop("NVC_SV2GHDL_KEEP", None)
        env.update(extra_env or {})
        p = subprocess.Popen([nvc_bin(), "--std=2040", "-L", nvc_lib(), "-a"] + files +
                             ["-e", top, "-r"], cwd=self.tmp, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True, errors="replace")
        out = p.communicate(timeout=600)[0]
        return p.pid, p.returncode, out, tmpdir

    def assert_no_leftovers(self, pid, tmpdir):
        self.assertEqual([f for f in os.listdir(tmpdir) if f.startswith("nvc_sv2ghdl")], [])
        for name in ("nvc_sv2ghdl_%d" % pid, "nvc_sv2ghdl_batch_%d" % pid):
            self.assertFalse(os.path.exists(os.path.join("/tmp", name)), name)

    def test_translated(self):
        self.write("ok.v", "module ok; initial #1 $display(\"ok ran\"); endmodule\n")
        pid, rc, out, tmpdir = self.run_nvc(["ok.v"], "ok")
        self.assertEqual(rc, 0, out)
        self.assertIn("ok ran", out)
        self.assert_no_leftovers(pid, tmpdir)

    def test_native_fallback(self):
        self.write("mixsens.v", MIXED_SENS)
        pid, rc, out, tmpdir = self.run_nvc(["mixsens.v"], "mixsens", {"SV2GHDL": "/bin/false"})
        self.assertEqual(rc, 0, out)
        self.assertIn("trying native parser", out)
        self.assert_no_leftovers(pid, tmpdir)

    def test_batch(self):
        self.write("a.v", "module a; b u(); initial #1 $display(\"a ran\"); endmodule\n")
        self.write("b.v", "module b; initial #2 $display(\"b ran\"); endmodule\n")
        pid, rc, out, tmpdir = self.run_nvc(["a.v", "b.v"], "a")
        self.assertEqual(rc, 0, out)
        self.assertIn("translating 2 Verilog files together", out)
        self.assertIn("b ran", out)
        self.assert_no_leftovers(pid, tmpdir)

    def test_kept_for_debugging(self):
        self.write("ok.v", "module ok; initial #1 $display(\"ok ran\"); endmodule\n")
        pid, rc, out, tmpdir = self.run_nvc(["ok.v"], "ok", {"NVC_SV2GHDL_KEEP": "1"})
        self.assertEqual(rc, 0, out)
        kept = [f for f in os.listdir(tmpdir) if f.startswith("nvc_sv2ghdl")]
        self.assertEqual(len(kept), 1, kept)
        self.assertTrue(kept[0].startswith("nvc_sv2ghdl_"))
        self.assertIn("the translation is kept in %s (NVC_SV2GHDL_KEEP)"
                      % os.path.join(tmpdir, kept[0]), out)
        self.assertTrue(os.path.isfile(os.path.join(tmpdir, kept[0], "design.vhd")))


# R6W-03: a co-simulation stepped under gdb, so that each SIGINT arrives at a known point
CELL_TB = """`timescale 1ns/1ps
module tb;
  reg clk = 0;
  logic seen;
  always #5 clk = ~clk;
  rc_cell u1 (.in(clk), .out(seen));
  initial #1000 $finish;
endmodule
"""

CELL_SP = """* an RC cell
.subckt rc_cell in out
r1 in mid 500
c1 mid 0 0.5p
e1 out 0 mid 0 1
.ends
.tran 1p 1u
.end
"""

# The first SIGINT on the engine's 21st candidate step (cosim_advance, while the engine is in
# its transient); the second when the run is about to print its end line (errorf), the
# first SIGINT's stop being taken by then.  "signal" resumes nvc with that signal.
GDB_SCRIPT = """set pagination off
set confirm off
set breakpoint pending on
handle SIGINT nostop noprint pass
break cosim_advance
ignore 1 20
run
delete 1
break errorf
signal SIGINT
signal SIGINT
"""


@needs_stack
@unittest.skipUnless(have_vacask(), "needs VACASK (Linux/WSL)")
class TestAmsWaves(TempDir):
    """R6W-01: vcs-ams +vcs+dumpvars: the co-simulation's digital side writes the VCD (nvc
    closes the dump when the co-simulation ends)."""

    def test_plus_dumpvars(self):
        # (a digital reader of seen: an analog output nothing reads is not bridged, and stays z)
        self.write("tb.sv", CELL_TB.replace("initial #1000 $finish;",
                                            'initial #100 $display("seen=%b", seen);\n'
                                            "  initial #100 $finish;"))
        self.write("rc.sp", CELL_SP)
        self.write("vcsAD.init", "choose xa rc.sp;\n")
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        env.pop("VAMOS_ANALOG", None)
        c = run(["vcs-ams", "-sverilog", "+vcs+dumpvars", "tb.sv", "--vamos-analog=vacask"],
                cwd=self.tmp, env=env, timeout=1200)
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertIn("vamos: note: +vcs+dumpvars: ./simv writes a VCD of the whole design to "
                      "verilog.dump", c.stdout)
        r = run([os.path.join(self.tmp, "simv")], cwd=self.tmp, timeout=1200)
        self.assertEqual(r.returncode, 0, r.stdout)
        vcd = Vcd(self.read("verilog.dump"))
        self.assertEqual(vcd.history("tb.clk")[:3], [(0, "0"), (5000000, "1"), (10000000, "0")])
        seen = [v for _t, v in vcd.history("tb.seen")]
        self.assertGreater(seen.count("1"), 5, seen)               # the analog output's edges
        self.assertGreater(seen.count("0"), 5, seen)


@needs_stack
@unittest.skipUnless(have_vacask(), "needs VACASK (Linux/WSL)")
@unittest.skipUnless(shutil.which("gdb"), "needs gdb")
class TestCosimSecondInterrupt(TempDir):
    """R6W-03: a second SIGINT before a co-simulation ends made nvc exit 1 with no end line
    (jit_interrupt takes an interrupt that arrives while another is pending as "quit now",
    and the stopped digital never ran again to take the first).  It now ends the run at
    once with the interrupted line and exit status 130."""

    def test_two_interrupts(self):
        self.write("tb.sv", CELL_TB)
        self.write("rc.sp", CELL_SP)
        self.write("vcsAD.init", "choose xa rc.sp;\n")
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        env.pop("VAMOS_ANALOG", None)
        c = run(["vcs-ams", "-sverilog", "tb.sv", "--vamos-analog=vacask"], cwd=self.tmp,
                env=env, timeout=1200)
        self.assertEqual(c.returncode, 0, c.stdout)

        from vamos.ams import engines, layout
        job = Job.from_json(self.read("simv.daidir/vamos.job.json"))
        be = nvcb.NvcBackend(job, lambda s: None)
        args = ["--stop-time=1000000001fs",
                "--vacask-netlist=" + layout.resolve(job.daidir, job.ams["deck"]),
                "--cosim-config=" + layout.resolve(job.daidir, job.ams["boundary"])]
        cmd, cenv = be.run_command(job.tops[0], [], args)
        cenv.update(engines.env_for("vacask", be.libdir))
        rundir = os.path.join(self.tmp, "run")
        os.makedirs(rundir)
        script = self.write("gdb.cmds", GDB_SCRIPT)
        g = run(["gdb", "-q", "-batch", "-x", script, "--args"] + cmd, cwd=rundir, env=cenv,
                timeout=600)
        m = re.search(r"exited with code (\d+)\]", g.stdout)
        self.assertTrue(m, g.stdout)
        self.assertEqual(int(m.group(1), 8), 130, g.stdout)      # gdb prints it in octal
        self.assertEqual(len(re.findall(r"(?m)^\*\* Error: co-simulation interrupted at \S+ s$",
                                        g.stdout)), 1, g.stdout)


if __name__ == "__main__":
    unittest.main()
