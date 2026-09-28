"""vamos phase-0 tests.  Standard library only:

    python3 -m unittest discover -s tests/vamos -v      (from the sv2ghdl root)

The end-to-end cases need nvc + iverilog (Linux/WSL) and are skipped otherwise.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from vamos import argscan, banner, tools  # noqa: E402
from vamos.job import IGNORED, NOTED, UNKNOWN, UNSUPPORTED, Job  # noqa: E402
from vamos.optable import strict_failures  # noqa: E402
from vamos.personalities import vcs  # noqa: E402

LAUNCHER = os.path.join(ROOT, "bin", "vamos")


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vamos-test-")
        self._env = dict(os.environ)
        tools._scrub_cache.clear()
        tools._real_cache.clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, text):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
        return p


class TestOptionFiles(TempDir):
    def test_comments_env_quotes(self):
        os.environ["VAMOS_T_DIR"] = "inc"
        self.write("a.f", '// line comment\n# hash line\n+incdir+$VAMOS_T_DIR /* block\n'
                          'comment */ top.v "sp ace.v" +define+X=${VAMOS_T_DIR}\n')
        got = argscan.expand_option_files(["-f", "a.f"], self.tmp)
        self.assertEqual(got, ["+incdir+inc", "top.v", "sp ace.v", "+define+X=inc"])

    def test_F_rebases_relative_paths(self):
        self.write("sub/b.f", "rtl/x.v -v lib/l.v +incdir+inc+/abs/inc\n")
        got = argscan.expand_option_files(["-F", "sub/b.f"], self.tmp)
        sub = os.path.join(self.tmp, "sub")
        self.assertEqual(got, [os.path.join(sub, "rtl/x.v"), "-v", os.path.join(sub, "lib/l.v"),
                               "+incdir+%s+/abs/inc" % os.path.join(sub, "inc")])

    def test_f_keeps_paths_relative_to_cwd_and_nests(self):
        self.write("sub/inner.f", "b.v\n")
        self.write("sub/outer.f", "a.v -f sub/inner.f\n")
        got = argscan.expand_option_files(["-f", "sub/outer.f", "c.v"], self.tmp)
        self.assertEqual(got, ["a.v", "b.v", "c.v"])

    def test_self_inclusion_is_an_error(self):
        self.write("loop.f", "-f loop.f\n")
        with self.assertRaises(argscan.ArgError):
            argscan.expand_option_files(["-f", "loop.f"], self.tmp)

    def test_missing_file(self):
        with self.assertRaises(argscan.ArgError):
            argscan.expand_option_files(["-f", "nope.f"], self.tmp)

    def test_hash_inside_line_is_not_a_comment(self):
        self.write("h.f", "a.v +define+N=#3\n")
        self.assertEqual(argscan.expand_option_files(["-f", "h.f"], self.tmp),
                         ["a.v", "+define+N=#3"])


class TestVcsOptions(TempDir):
    def job(self, *args):
        return vcs.build_job(list(args), self.tmp)

    def test_mapped(self):
        j = self.job("-sverilog", "-o", "out/sim", "-R", "-l", "c.log", "-top", "tb",
                     "+incdir+i1+i2", "+define+A=1+B", "-v", "lib.v", "-timescale=1ns/1ps",
                     "-Mdir=build", "t.sv", "d.v")
        self.assertEqual(j.exe, os.path.join(self.tmp, "out/sim"))
        self.assertEqual(j.daidir, j.exe + ".daidir")
        self.assertTrue(j.run)
        self.assertEqual(j.tops, ["tb"])
        self.assertEqual(j.incdirs, [os.path.join(self.tmp, "i1"), os.path.join(self.tmp, "i2")])
        self.assertEqual(j.defines, {"A": "1", "B": None})
        self.assertEqual(j.lib_files, [os.path.join(self.tmp, "lib.v")])
        self.assertEqual(j.timescale, "1ns/1ps")
        self.assertEqual(j.mdir, "build")
        self.assertEqual([(os.path.basename(s.path), s.lang) for s in j.sources],
                         [("t.sv", "sv"), ("d.v", "sv")])
        self.assertEqual(j.unmapped, [])

    def test_dispositions(self):
        j = self.job("-full64", "-kdb", "-xprop=tmerge", "-bogus", "+myplus", "x.v")
        disp = {u.option: u.disposition for u in j.unmapped}
        self.assertEqual(disp["-full64"], IGNORED)
        self.assertEqual(disp["-kdb"], NOTED)
        self.assertEqual(disp["-xprop=tmerge"], UNSUPPORTED)
        self.assertEqual(disp["-bogus"], UNKNOWN)
        self.assertEqual(j.plusargs, ["+myplus"])
        self.assertEqual(sorted(strict_failures(j)), ["-bogus", "-xprop=tmerge"])

    def test_uvm_is_flagged(self):
        j = self.job("-ntb_opts", "uvm-1.2", "x.sv")
        self.assertEqual(j.unmapped[0].disposition, UNSUPPORTED)

    def test_vhdl_and_c_sources_are_reported(self):
        j = self.job("a.vhd", "dpi.c", "x.v")
        self.assertEqual(len(j.sources), 1)
        self.assertEqual({u.disposition for u in j.unmapped}, {UNSUPPORTED})

    def test_job_json_roundtrip(self):
        j = self.job("-kdb", "x.v")
        j2 = Job.from_json(j.to_json())
        self.assertEqual(j2, j)


class TestToolsLockout(TempDir):
    def setUp(self):
        super().setUp()
        self.shims = os.path.join(self.tmp, "shims")
        self.real = os.path.join(self.tmp, "real")
        os.makedirs(self.shims)
        os.makedirs(self.real)
        os.symlink(LAUNCHER, os.path.join(self.shims, "nvc"))
        with open(os.path.join(self.real, "nvc"), "w") as fh:
            fh.write("#!/bin/sh\necho real\n")
        os.chmod(os.path.join(self.real, "nvc"), 0o755)
        os.environ["VAMOS_LAUNCHER"] = os.path.realpath(LAUNCHER)
        os.environ["PATH"] = os.pathsep.join([self.shims, self.real, "/usr/bin", "/bin"])
        os.environ.pop("VAMOS_NVC", None)
        os.environ.pop("VAMOS_STACK", None)

    def test_scrub_drops_shim_dir_only(self):
        parts = tools.scrubbed_path().split(os.pathsep)
        self.assertNotIn(self.shims, parts)
        self.assertIn(self.real, parts)

    def test_launcher_dir_is_kept(self):
        # a mixed dir holding vamos itself (PREFIX/bin) must survive scrubbing
        mixed = os.path.join(self.tmp, "prefix_bin")
        os.makedirs(mixed)
        shutil.copy(LAUNCHER, os.path.join(mixed, "vamos"))
        os.symlink(os.path.join(mixed, "vamos"), os.path.join(mixed, "vcs"))
        self.assertIn(mixed, tools.scrubbed_path(mixed).split(os.pathsep))

    def test_redirect_dirs_are_dropped(self):
        os.environ["VAMOS_REDIRECT"] = self.real
        self.assertNotIn(self.real, tools.scrubbed_path().split(os.pathsep))

    def test_find_real_skips_shims(self):
        self.assertEqual(tools.find_real("nvc"), os.path.join(self.real, "nvc"))

    def test_lockout_on_reentry(self):
        self.assertIsNone(tools.check_lockout("nvc"))
        os.environ["VAMOS_STACK"] = "nvc"
        self.assertEqual(tools.check_lockout("nvc"), os.path.join(self.real, "nvc"))

    def test_reentry_without_real_tool_errors(self):
        os.environ["VAMOS_STACK"] = "vcs"
        with self.assertRaises(tools.LockoutError):
            tools.check_lockout("vcs")

    def test_depth_limit(self):
        os.environ["VAMOS_STACK"] = "a,b,c,d"
        with self.assertRaises(tools.LockoutError):
            tools.check_lockout("vcs")

    def test_child_env(self):
        tools.current = "vcs"
        env = tools.child_env({"X": "1"})
        self.assertEqual(env["VAMOS_STACK"], "vcs")
        self.assertEqual(env["X"], "1")
        self.assertNotIn(self.shims, env["PATH"].split(os.pathsep))

    @unittest.skipUnless(os.name == "posix", "needs a POSIX shell")
    def test_nvc_shim_does_not_recurse(self):
        r = subprocess.run([os.path.join(self.shims, "nvc"), "--version"],
                           stdout=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "real")


class TestBanner(TempDir):
    def test_default_brand_and_placeholders(self):
        b = banner.Banner(banner.load_profile("vcs"), "vcs")
        self.assertIn("V A M O S   S i m u l a t i o n", b.text("run_footer", simtime="1ns", cpu=0.5))
        self.assertIsNone(b.text("no_such_key"))

    def test_none_profile(self):
        self.assertIsNone(banner.load_profile("vcs", "none"))

    def test_profile_by_path_and_brand(self):
        p = self.write("acme.json", json.dumps({"brand": "acme", "run_footer": "{BRAND} {Brand} {brand}"}))
        b = banner.Banner(banner.load_profile("vcs", p), "vcs")
        self.assertEqual(b.text("run_footer"), "ACME Acme acme")

    def test_project_layer_overrides(self):
        self.write(".vamos/banners/vcs.json", json.dumps({"brand": "zed", "compile_failed": "{BRAND}"}))
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            self.assertEqual(banner.Banner(banner.load_profile("vcs"), "vcs").text("compile_failed"), "ZED")
        finally:
            os.chdir(cwd)

    def test_unknown_profile_name(self):
        with self.assertRaises(ValueError):
            banner.load_profile("vcs", "no-such-profile")

    def test_provenance_lists_licences(self):
        text = banner.provenance([("nvc", "1.19", "/x/nvc"), ("bfit", "git-1", "/x/bfit")], "vcs")
        self.assertIn("GPL-3.0-or-later", text)
        self.assertIn("PolyForm-Noncommercial-1.0.0", text)


def _have_stack():
    if not sys.platform.startswith("linux"):     # the stack is Linux ELF
        return False
    os.environ.setdefault("VAMOS_LAUNCHER", os.path.realpath(LAUNCHER))
    return bool(tools.find_real("nvc")) and bool(tools.find_real("iverilog"))


@unittest.skipUnless(_have_stack(), "needs nvc + iverilog (Linux/WSL)")
class TestEndToEnd(TempDir):
    TB = ("module tb;\n  reg clk = 0; integer n = 0;\n  always #5 clk = ~clk;\n"
          "  always @(posedge clk) begin n = n + 1;\n"
          "    if (n == 3) begin $display(\"n=%0d V=%0d\", n, `V); $finish; end end\nendmodule\n")

    def vcs(self, *args):
        env = dict(os.environ)
        env["PATH"] = os.path.join(ROOT, "shims") + os.pathsep + env.get("PATH", "")
        return subprocess.run(["vcs"] + list(args), cwd=self.tmp, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=300)

    def test_vcs_R(self):
        self.write("tb.v", self.TB)
        r = self.vcs("-full64", "+define+V=7", "-timescale=1ns/1ps", "tb.v", "-R")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("n=3 V=7", r.stdout)
        self.assertIn("tools used:", r.stdout)
        self.assertIn("V A M O S", r.stdout)
        self.assertIn("Time: 25ns", r.stdout)
        self.assertTrue(os.access(os.path.join(self.tmp, "simv"), os.X_OK))
        s = subprocess.run(["./simv"], cwd=self.tmp, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=300)
        self.assertEqual(s.returncode, 0, s.stdout)
        self.assertIn("n=3 V=7", s.stdout)


if __name__ == "__main__":
    unittest.main()
