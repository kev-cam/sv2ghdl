"""vamos Vivado-simulator personalities (xvlog / xvhdl / xelab / xsim).  Standard library only:

    python3 -m unittest discover -s tests/vamos -v      (from the sv2ghdl root)

The end-to-end cases need nvc + iverilog (Linux/WSL) and are skipped otherwise.
"""

import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from vamos import tools  # noqa: E402
from vamos.job import IGNORED, NOTED, UNKNOWN, UNSUPPORTED  # noqa: E402
from vamos.optable import scan  # noqa: E402
from vamos.personalities import xsim  # noqa: E402

LAUNCHER = os.path.join(ROOT, "bin", "vamos")

from vamos_testlib import TempDir  # noqa: E402


class TestAnalysisOptions(TempDir):
    def job(self, *args):
        job = xsim._AnJob("xvlog", list(args), self.tmp)
        scan(xsim.XVLOG_TABLE, xsim._expand(list(args), self.tmp), job, xsim._positional, xsim._unknown)
        return job

    def test_both_spellings_and_defines(self):
        j = self.job("--sv", "-d", "A=1", "--define", "B", "-i", "inc", "--include", "inc2", "x.sv")
        self.assertTrue(j.sv)
        self.assertEqual(j.defines, {"A": "1", "B": None})
        self.assertEqual([os.path.basename(d) for d in j.incdirs], ["inc", "inc2"])
        self.assertEqual(j.files, ["x.sv"])

    def test_work_and_libs(self):
        j = self.job("-work", "mylib=./mylib", "-L", "unisims_ver", "--lib", "other", "a.v")
        self.assertEqual(j.library, "mylib")
        self.assertEqual(j.libs, ["unisims_ver", "other"])
        self.assertTrue(any(u.disposition == NOTED and u.option.startswith("--work") for u in j.unmapped))

    def test_unknown_option_is_noted(self):
        j = self.job("--no-such-thing", "a.v")
        self.assertEqual([u.disposition for u in j.unmapped], [UNKNOWN])

    def test_option_file(self):
        self.write("opts.f", "-d V=3\n# comment\nsrc/a.v\n")
        j = self.job("-f", "opts.f")
        self.assertEqual(j.defines, {"V": "3"})
        self.assertEqual(j.files, ["src/a.v"])

    def test_lang_of(self):
        self.assertEqual(xsim._lang_of("a.v", False), "verilog")
        self.assertEqual(xsim._lang_of("a.v", True), "sv")
        self.assertEqual(xsim._lang_of("a.sv", False), "sv")
        self.assertEqual(xsim._lang_of("a.vhd", False), "vhdl")
        self.assertIsNone(xsim._lang_of("a.txt", False))

    def test_library_record_replaces_reanalysed_file(self):
        xd = os.path.join(self.tmp, "xsim.dir")
        one = {"path": "/a.v", "lang": "verilog", "defines": {}, "incdirs": [], "std": "verilog"}
        two = {"path": "/b.v", "lang": "verilog", "defines": {}, "incdirs": [], "std": "verilog"}
        self.assertEqual(xsim._record(xd, "work", [one, two]), 2)
        one2 = dict(one, defines={"X": "1"})
        self.assertEqual(xsim._record(xd, "work", [one2]), 2)
        srcs = xsim._read_lib(xd, "work")
        self.assertEqual([s["path"] for s in srcs], ["/a.v", "/b.v"])
        self.assertEqual(srcs[0]["defines"], {"X": "1"})
        with open(os.path.join(xd, "work", xsim.LIB_FILE)) as fh:
            self.assertEqual(json.load(fh)["library"], "work")


class TestElabOptions(TempDir):
    def job(self, *args):
        job = xsim._ElabJob(list(args), self.tmp)
        scan(xsim.XELAB_TABLE, xsim._expand(list(args), self.tmp), job, xsim._positional, xsim._unknown)
        return job

    def test_tops_and_snapshot(self):
        j = self.job("-s", "snap", "-L", "work", "-L", "unisims_ver", "--debug", "typical", "-R",
                     "work.tb", "glbl", "lib2.top(rtl)")
        self.assertEqual(j.snapshot, "snap")
        self.assertTrue(j.run)
        from vamos.console import Console
        tops = xsim._tops(j, Console())
        self.assertEqual(tops, ["tb", "top"])
        notes = {u.option: u.disposition for u in j.unmapped}
        self.assertEqual(notes.get("glbl"), NOTED)
        self.assertEqual(notes.get("--debug typical"), NOTED)

    def test_unsupported_dpi_and_generic(self):
        j = self.job("--sv_lib", "dpi", "-generic_top", "W=8", "--O3", "-mt", "4", "tb")
        d = {u.option: u.disposition for u in j.unmapped}
        self.assertEqual(d.get("--sv_lib dpi"), UNSUPPORTED)
        self.assertEqual(d.get("-generic_top W=8"), UNSUPPORTED)
        self.assertEqual(d.get("--O3"), IGNORED)       # recorded, reported silently
        self.assertEqual(d.get("-mt 4"), IGNORED)

    def test_direct_sources(self):
        j = self.job("--svlog", "a.sv", "-vlog", "b.v", "--vhdl2008", "c.vhd", "tb")
        self.assertEqual([(l, os.path.basename(p)) for l, p in j.direct],
                         [("sv", "a.sv"), ("verilog", "b.v"), ("vhdl", "c.vhd")])

    def test_gather_notes_xilinx_libraries(self):
        j = self.job("-L", "unisims_ver", "-L", "nolib", "tb")
        from vamos.console import Console
        srcs, libs = xsim._gather(j, Console())
        self.assertEqual(srcs, [])
        self.assertEqual(libs, ["work", "unisims_ver", "nolib"])
        d = {u.option: u.note for u in j.unmapped}
        self.assertIn("Xilinx simulation library", d["-L unisims_ver"])
        self.assertIn("no sources", d["-L nolib"])


class TestRunOptions(TempDir):
    def job(self, *args):
        job = xsim._RunJob(list(args), self.tmp)
        scan(xsim.XSIM_TABLE, xsim._expand(list(args), self.tmp), job, xsim._positional, xsim._unknown)
        return job

    def test_plusargs_and_runall(self):
        j = self.job("snap", "-R", "--testplusarg", "A=1", "-testplusarg", "B", "--sv_seed", "9",
                     "-wdb", "w.wdb", "-g")
        self.assertEqual(j.files, ["snap"])
        self.assertTrue(j.runall)
        self.assertEqual(j.plusargs, ["+A=1", "+B"])
        self.assertEqual(j.seed, "9")
        self.assertEqual({u.option for u in j.unmapped}, {"-wdb w.wdb", "-g"})

    def test_tcl_batch(self):
        j = self.job("snap")
        run_all, fs = xsim.tcl_batch(["# c", "log_wave -r *", "run 10 ns", "run 500ps", "puts hi",
                                      "frobnicate", "quit", "run all"], j)
        self.assertFalse(run_all)
        self.assertEqual(fs, 10 * 10 ** 6 + 500 * 10 ** 3)
        d = {u.option: u.disposition for u in j.unmapped}
        self.assertEqual(d["-t: log_wave"], NOTED)
        self.assertEqual(d["-t: puts"], NOTED)
        self.assertEqual(d["-t: frobnicate"], UNKNOWN)
        run_all, fs = xsim.tcl_batch(["run all"], j)
        self.assertTrue(run_all)
        self.assertIsNone(fs)
        run_all, fs = xsim.tcl_batch(["run -all;"], j)
        self.assertTrue(run_all)
        run_all, fs = xsim.tcl_batch(["run 100"], j)       # no unit: ns
        self.assertEqual(fs, 100 * 10 ** 6)


def _have_stack():
    if not sys.platform.startswith("linux"):
        return False
    os.environ.setdefault("VAMOS_LAUNCHER", os.path.realpath(LAUNCHER))
    return bool(tools.find_real("nvc")) and bool(tools.find_real("iverilog"))


@unittest.skipUnless(_have_stack(), "needs nvc + iverilog (Linux/WSL)")
class TestEndToEnd(TempDir):
    TB = ("module tb;\n  reg clk = 0; integer n = 0; integer lim;\n  always #5 clk = ~clk;\n"
          "  initial if (!$value$plusargs(\"lim=%d\", lim)) lim = 3;\n"
          "  always @(posedge clk) begin n = n + 1;\n"
          "    if (n == lim) begin $display(\"n=%0d V=%0d\", n, `V); $finish; end end\nendmodule\n")

    def run_tool(self, tool, *args):
        env = dict(os.environ)
        env["PATH"] = os.path.join(ROOT, "shims") + os.pathsep + env.get("PATH", "")
        return subprocess.run([tool] + list(args), cwd=self.tmp, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=300)

    def test_three_steps(self):
        self.write("tb.v", self.TB)
        r = self.run_tool("xvlog", "-d", "V=7", "tb.v")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "xsim.dir", "work", xsim.LIB_FILE)))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "xvlog.log")))
        r = self.run_tool("xelab", "--timescale", "1ns/1ps", "-debug", "typical", "-s", "snap", "work.tb")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("tools used:", r.stdout)
        self.assertIn("snapshot xsim.dir/snap is up to date", r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "xsim.dir", "snap", "vamos.job.json")))
        r = self.run_tool("xsim", "snap", "-R", "--testplusarg", "lim=5")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("n=5 V=7", r.stdout)
        self.assertIn("Time: 45ns", r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "xsim.log")))

    def test_elab_runall_and_tcl_batch(self):
        self.write("tb.v", self.TB)
        self.write("run.tcl", "log_wave -r *\nrun 22 ns\nquit\n")
        r = self.run_tool("xvlog", "--sv", "-d", "V=1", "tb.v")
        self.assertEqual(r.returncode, 0, r.stdout)
        r = self.run_tool("xelab", "tb", "-R")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("n=3 V=1", r.stdout)
        r = self.run_tool("xsim", "work.tb", "-t", "run.tcl")        # default snapshot name
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("log_wave", r.stdout)
        self.assertIn("Time: 22ns", r.stdout)
        self.assertNotIn("n=3", r.stdout)

    def test_errors(self):
        self.write("tb.v", self.TB)
        r = self.run_tool("xsim", "nosnap", "-R")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not built", r.stdout)
        r = self.run_tool("xelab", "tb")
        self.assertEqual(r.returncode, 1)
        self.assertIn("nothing to elaborate", r.stdout)
        r = self.run_tool("xvlog", "-d", "V=1", "tb.v")
        self.assertEqual(r.returncode, 0, r.stdout)
        r = self.run_tool("xelab", "tb")
        self.assertEqual(r.returncode, 0, r.stdout)
        r = self.run_tool("xsim", "work.tb")
        self.assertEqual(r.returncode, 1)
        self.assertIn("interactive Tcl shell", r.stdout)


def _have_verilator():
    return _have_stack() and bool(tools.find_real("verilator"))


@unittest.skipUnless(_have_verilator(), "needs verilator (and the stack)")
class TestVerilatorEngine(TempDir):
    TB = TestEndToEnd.TB

    def run_tool(self, tool, *args, env_extra=None):
        env = dict(os.environ)
        env["PATH"] = os.path.join(ROOT, "shims") + os.pathsep + env.get("PATH", "")
        env.update(env_extra or {})
        return subprocess.run([tool] + list(args), cwd=self.tmp, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=600)

    def test_xelab_xsim_on_verilator(self):
        self.write("tb.v", self.TB)
        self.write("run.tcl", "run 22 ns\nquit\n")
        r = self.run_tool("xvlog", "-d", "V=5", "tb.v")
        self.assertEqual(r.returncode, 0, r.stdout)
        r = self.run_tool("xelab", "--vamos-engine=verilator", "-s", "snap", "tb", "-R")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("verilator", r.stdout)
        self.assertIn("n=3 V=5", r.stdout)
        with open(os.path.join(self.tmp, "xsim.dir", "snap", "vamos.job.json")) as fh:
            self.assertEqual(json.load(fh)["engine"], "verilator")
        r = self.run_tool("xsim", "snap", "-R", "--testplusarg", "lim=4")   # no engine option needed
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("n=4 V=5", r.stdout)
        r = self.run_tool("xsim", "snap", "-t", "run.tcl")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("no stop time", r.stdout)
        self.assertIn("n=3 V=5", r.stdout)

    def test_vcs_on_verilator_via_environment(self):
        self.write("tb.v", self.TB)
        r = self.run_tool("vcs", "-full64", "+define+V=8", "-timescale=1ns/1ps", "tb.v", "-R",
                          env_extra={"VAMOS_ENGINE": "verilator"})
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("n=3 V=8", r.stdout)
        self.assertIn("verilator", r.stdout)
        s = subprocess.run(["./simv", "+lim=6", "+vcs+finish+10"], cwd=self.tmp, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=300)
        self.assertEqual(s.returncode, 0, s.stdout)
        self.assertIn("n=6 V=8", s.stdout)
        self.assertIn("no stop time", s.stdout)

    def test_bad_engine_value(self):
        self.write("tb.v", self.TB)
        r = self.run_tool("xelab", "--vamos-engine=icarus", "tb")
        self.assertEqual(r.returncode, 2)
        r = self.run_tool("vcs", "tb.v", env_extra={"VAMOS_ENGINE": "icarus"})
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("VAMOS_ENGINE=icarus", r.stdout)


if __name__ == "__main__":
    unittest.main()
