"""vcs / vcs-ams end to end: the vamos fixes of the repair round.

Plain vcs (nvc + iverilog):
  * $sqrt, $ln, $pow, $rtoi on a non-constant argument run: libsv_math.so is loaded next to
    libresolver.so (they stopped the run: "foreign function sv_sqrt not found"), and $random
    keeps the resolver's generator (vvp's first draw)
  * output printed inside a Verilog function has no nvc "   Function CHK [...] at
    design.vhd:N" trace line after it
  * the translator's "connected one way only" warning is a vamos warning at the user's
    file:line, and --vamos-strict makes it an error (it passed through as plain output)
  * a bad VAMOS_IVERILOG reports after the compile banner, as a bad VAMOS_NVC does
  * `vamos -simv --vamos-daidir=<dir>' on a daidir whose compile failed names the failed
    compile ("holds no finished compile")
AMS (vcs-ams, each available engine):
  * VCS's -v library rule: a module defined in a source and again in a -v file compiles and
    runs the source's copy (the precheck called it "already declared")
  * a non-ASCII SPICE title compiles under a non-UTF-8 locale (the deck writers used the
    locale's encoding: UnicodeEncodeError)
  * the job's ams record carries the .tran start (cosim no longer re-reads the deck for it)
  * VACASK: a copied daidir whose original is gone runs: the deck loads its compiled
    Verilog-A relative to itself (it loaded the original's .osdi by absolute path)

    python3 -m unittest discover -s tests/vamos -p 'test_repair_e2e.py' -v

Linux (WSL) only: the stack is Linux ELF.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest

from ams_e2e_lib import AmsCase, needs_ams
from vamos_testlib import ROOT, TempDir, have_stack, run

SHIMS = os.path.join(ROOT, "shims")
needs_stack = unittest.skipUnless(have_stack(), "needs nvc + iverilog (Linux/WSL)")


@needs_stack
class TestPlainRepairE2E(TempDir):
    def tool(self, *args, env=None, prog="vcs"):
        e = dict(os.environ)
        e["PATH"] = SHIMS + os.pathsep + e.get("PATH", "")
        e.pop("VAMOS_ANALOG", None)
        e.pop("PYTHONPATH", None)
        e.update(env or {})
        return run([prog] + list(args), cwd=self.tmp, env=e, timeout=600)

    def simv(self, *args):
        e = dict(os.environ)
        e.pop("PYTHONPATH", None)
        return run(["./simv"] + list(args), cwd=self.tmp, env=e, timeout=600)

    def test_math_functions_run(self):
        self.write("tb.v", "`timescale 1ns/1ps\nmodule tb;\n  real x, y;\n  integer i;\n  initial begin\n"
                           "    x = 2.0;\n    #1 y = $sqrt(x);\n    $display(\"sqrt %0.6f\", y);\n"
                           "    $display(\"ln %0.6f pow %0.3f rtoi %0d\", $ln(x), $pow(x, 3.0), $rtoi(x * 2.6));\n"
                           "    i = $random;\n    $display(\"random %0d\", i);\n  end\nendmodule\n")
        c = self.tool("tb.v")
        self.assertEqual(c.returncode, 0, c.stdout)
        r = self.simv()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("not found", r.stdout)
        self.assertIn("sqrt 1.414214", r.stdout)
        self.assertIn("ln 0.693147 pow 8.000 rtoi 5", r.stdout)
        self.assertIn("random 303379748", r.stdout)       # vvp's first $random

    def test_function_trace_is_dropped(self):
        self.write("tb.v", "`timescale 1ns/1ps\nmodule tb;\n  function [7:0] chk(input [7:0] a);\n"
                           "    begin\n      $display(\"in chk a=%0d\", a);\n"
                           "      $display(\"two\\n   Function lines\");\n      chk = a + 1;\n    end\n"
                           "  endfunction\n  initial $display(\"r=%0d\", chk(8'd5));\nendmodule\n")
        self.assertEqual(self.tool("tb.v").returncode, 0)
        r = self.simv()
        self.assertEqual(r.returncode, 0, r.stdout)
        lines = r.stdout.splitlines()
        self.assertNotRegex(r.stdout, r"(?m)^   Function CHK ")
        i = lines.index("in chk a=5")
        self.assertEqual(lines[i:i + 4], ["in chk a=5", "two", "   Function lines", "r=6"])

    TRAN_ON_SELECT = ("`timescale 1ns/1ps\nmodule tb;\n  wire [3:0] bus;\n  wire w;\n  reg en;\n"
                      "  assign bus = en ? 4'b1010 : 4'bzzzz;\n  tran t1 (bus[2], w);\n"
                      "  initial begin\n    en = 1;\n    #10 $display(\"%0t bus=%b\", $time, bus);\n"
                      "  end\nendmodule\n")

    def test_translator_warning_is_a_vamos_warning(self):
        self.write("tb.v", self.TRAN_ON_SELECT)
        c = self.tool("tb.v")
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertRegex(c.stdout, r"vamos: warning: tb\.v:7: bus_sig\(2\) is connected one way only: its "
                                   r"part-select tran joins a translator temporary")
        self.assertNotIn("iverilog-sv2ghdl: Warning:", c.stdout)
        c = self.tool("tb.v", "--vamos-strict")
        self.assertEqual(c.returncode, 1, c.stdout)
        self.assertRegex(c.stdout, r"vamos: error: tb\.v:7: bus_sig\(2\) is connected one way only")
        self.assertIn("compile failed", c.stdout)

    def test_bad_iverilog_override_after_the_banner(self):
        self.write("tb.v", "module tb; endmodule\n")
        for var in ("VAMOS_NVC", "VAMOS_IVERILOG"):
            with self.subTest(var=var):
                c = self.tool("tb.v", env={var: "/nonexistent/tool"})
                self.assertEqual(c.returncode, 1, c.stdout)
                lines = c.stdout.splitlines()
                banner = [k for k, ln in enumerate(lines) if "compile (vcs personality)" in ln]
                err = [k for k, ln in enumerate(lines) if ln.startswith("vamos: error: %s=" % var)]
                self.assertTrue(banner and err, c.stdout)
                self.assertLess(banner[0], err[0], c.stdout)

    def test_simv_on_a_failed_compile_names_it(self):
        self.write("tb.v", "`timescale 1ns/1ps\nmodule tb; initial #1 $display(\"ok\"); endmodule\n")
        self.assertEqual(self.tool("tb.v").returncode, 0)
        self.write("tb.v", "module tb; initial #1 $display(\"ok\") endmodule\n")      # a syntax error
        self.assertEqual(self.tool("tb.v").returncode, 1)
        daidir = os.path.join(self.tmp, "simv.daidir")
        launcher = os.path.join(ROOT, "bin", "vamos")
        r = run([launcher, "-simv", "--vamos-daidir=" + daidir], cwd=self.tmp, timeout=120)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("vamos: error: %s holds no finished compile (the last compile failed or was "
                      "interrupted, or the directory is incomplete); compile again" % daidir, r.stdout)
        r = run([launcher, "-simv", "--vamos-daidir=" + daidir + "_none"], cwd=self.tmp, timeout=120)
        self.assertIn("is not a vamos simulation directory", r.stdout)


TITLE = "* vcs-ams repair e2e " + chr(0x2014) + " RC cell, 5 " + chr(0xb5) + "s\n"
RC = (".subckt rc_cell in out\nr1 in mid 10k\nc1 mid 0 1p\ne1 out 0 mid 0 1\n.ends\n"
      "vsup vdd 0 1.8\nrload vdd 0 1meg\n.tran 1n 1u 100n\n")
INIT = "choose xa rc.sp;\n"
TB = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  wire out;
  wire [3:0] lv;
  always #50 clk = ~clk;
  rc_cell u1 (.in(clk), .out(out));
  leaf l (.y(lv));
  initial begin
    #420 $display("out=%b lv=%0d", out, lv);
    $finish;
  end
endmodule
module leaf(output [3:0] y);
  assign y = 4'd5;
endmodule
"""
LIB = """\
`timescale 1ns/1ps
module leaf(output [3:0] y);
  assign y = 4'd9;
endmodule
"""


@needs_ams
class TestAmsRepairE2E(AmsCase):
    def build_rc(self, name, engine, *extra, env=None, title=TITLE):
        d = self.case("%s_%s" % (name, engine), {"tb.sv": TB, "lib.v": LIB, "vcsAD.init": INIT})
        with open(os.path.join(d, "rc.sp"), "w", encoding="utf-8") as fh:
            fh.write(title + RC)
        r = self.compile(d, "-sverilog", "tb.sv", *extra, engine=engine, env=env, expect_rc=None)
        return d, r

    def test_library_rule(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d, r = self.build_rc("vlib", engine, "-v", "lib.v")
                self.assertEqual(r.returncode, 0, r.stdout)
                self.assertNotIn("already declared", r.stdout)
                s = self.simv(d)
                self.assertIn("lv=5", s.stdout)                 # the source's leaf, not lib.v's
                self.assertIn("out=", s.stdout)

    def test_non_ascii_title_under_a_non_utf8_locale(self):
        env = {"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
        probe = subprocess.run(["python3", "-c", "import locale; print(locale.getpreferredencoding(False))"],
                               env=dict(os.environ, **env), stdout=subprocess.PIPE,
                               universal_newlines=True)
        if "utf" in probe.stdout.lower().replace("-", ""):
            self.skipTest("this system gives Python a UTF-8 locale even with LC_ALL=C")
        for engine in self.engines():
            with self.subTest(engine=engine):
                d, r = self.build_rc("title", engine, env=env)
                self.assertEqual(r.returncode, 0, r.stdout)
                self.assertNotIn("UnicodeEncodeError", r.stdout)
                deck = os.path.join(d, "simv.daidir", "ams", "deck",
                                    "vamos.sim" if engine == "vacask" else "vamos.cir")
                with open(deck, "rb") as fh:
                    self.assertIn((chr(0xb5) + "s").encode("utf-8"), fh.read())

    def test_report_write_failure_is_a_stage_error(self):
        """The IE report is written through the stage runner: its error is a stage note and
        "AMS compile failed at the IE report", as every other stage reports."""
        if os.geteuid() == 0:
            self.skipTest("root writes into a read-only directory")
        engine = self.engines()[0]
        d = self.case("rpt_%s" % engine, {"tb.sv": TB, "lib.v": LIB, "vcsAD.init": INIT})
        with open(os.path.join(d, "rc.sp"), "w", encoding="utf-8") as fh:
            fh.write(TITLE + RC)
        msv = os.path.join(d, "simv.msv")
        os.makedirs(msv)
        os.chmod(msv, 0o555)
        try:
            r = self.compile(d, "-sverilog", "tb.sv", engine=engine, expect_rc=1)
        finally:
            os.chmod(msv, 0o755)
        self.assertRegex(r.stdout, r"vamos: error: \S*simv\.msv/interface_element\.rpt: cannot write the "
                                   r"interface-element report: ")
        self.assertIn("vamos: error: AMS compile failed at the IE report", r.stdout)

    def test_translator_warning_and_strict(self):
        """The translator's one-way-connection warning is a vamos warning in AMS mode too, and
        --vamos-strict makes it an error."""
        engine = self.engines()[0]
        tb = TB.replace("  leaf l (.y(lv));\n", "  leaf l (.y(lv));\n  wire [3:0] bus;\n  wire w;\n"
                                                  "  tran t1 (bus[2], w);\n")
        d = self.case("xw_%s" % engine, {"tb.sv": tb, "lib.v": LIB, "vcsAD.init": INIT})
        with open(os.path.join(d, "rc.sp"), "w", encoding="utf-8") as fh:
            fh.write(TITLE + RC)
        r = self.compile(d, "-sverilog", "tb.sv", engine=engine)
        self.assertRegex(r.stdout, r"vamos: warning: tb\.sv:\d+: bus_sig\(2\) is connected one way only")
        r = self.compile(d, "-sverilog", "tb.sv", "--vamos-strict", engine=engine, expect_rc=1)
        self.assertRegex(r.stdout, r"vamos: error: tb\.sv:\d+: bus_sig\(2\) is connected one way only")
        self.assertIn("AMS compile failed at translation", r.stdout)

    def test_record_carries_the_tran_start(self):
        engine = self.engines()[0]
        d, r = self.build_rc("start", engine)
        self.assertEqual(r.returncode, 0, r.stdout)
        with open(os.path.join(d, "simv.daidir", "vamos.job.json")) as fh:
            rec = json.load(fh)["ams"]
        self.assertAlmostEqual(rec["start"], 100e-9, delta=1e-18)

    def test_copied_vacask_daidir_runs_without_the_original(self):
        if "vacask" not in self.engines():
            self.skipTest("needs VACASK")
        d, r = self.build_rc("copy", "vacask")
        self.assertEqual(r.returncode, 0, r.stdout)
        with open(os.path.join(d, "simv.daidir", "ams", "deck", "vamos.sim"), encoding="utf-8") as fh:
            loads = re.findall(r'(?m)^load "([^"]+)"', fh.read())
        self.assertTrue(loads)
        for p in loads:
            if p.endswith(".osdi") and "vamos_ie" in p:
                self.assertFalse(os.path.isabs(p), p)
        moved = os.path.join(self.tmp, "moved")
        os.makedirs(moved)
        for f in ("simv", "simv.daidir", "rc.sp", "vcsAD.init"):
            src = os.path.join(d, f)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(moved, f), symlinks=True)
            else:
                shutil.copy2(src, os.path.join(moved, f))
        shutil.rmtree(d)
        s = self.simv(moved)
        self.assertNotIn("initialize failed", s.stdout)
        self.assertIn("out=", s.stdout)


if __name__ == "__main__":
    unittest.main()
