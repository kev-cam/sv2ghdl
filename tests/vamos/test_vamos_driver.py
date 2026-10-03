"""vamos driver tests: the vcs/vcs-ams/simv front end around the compile.

    python3 -m unittest test_vamos_driver          (from tests/vamos)

The unit classes run anywhere (Cygwin Python 3.9 too); the end-to-end classes need
nvc + iverilog, and the AMS ones an analog engine (Linux/WSL), and are skipped otherwise.
Covered: a failed compile never leaves a runnable half-rebuilt daidir; --vamos-* option
checks; several top-level modules; a deferred top is an error; untranslated system
tasks and functions are reported; VCS's -v library rule; -top checks; checked
VAMOS_<TOOL> overrides and version parsing; compile messages; the help texts; ./simv
finds the daidir next to itself; the licence table and banner comments.
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

from vamos_testlib import LAUNCHER, ROOT, TempDir, have_stack, run  # noqa: F401
from ams_e2e_lib import AmsCase, needs_ams

from vamos import banner, cli, optable, tools  # noqa: E402
from vamos.ams import verilog_ports as vp  # noqa: E402
from vamos.backends.nvc import BackendError  # noqa: E402
from vamos.job import INAPPLICABLE, NOTED, UNSUPPORTED, Job  # noqa: E402
from vamos.personalities import vcs  # noqa: E402

SHIMS = os.path.join(ROOT, "shims")
POSIX_SH = os.name == "posix" and os.path.exists("/bin/sh")
needs_stack = unittest.skipUnless(have_stack(), "needs nvc + iverilog (Linux/WSL)")


def call_cli(argv):
    """cli.main(argv) -> (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


# =============================================================================
# --vamos-* options (Idriver-02) and the help texts (Idriver-10)
# =============================================================================

class TestVamosOptions(TempDir):
    def test_unknown_key_is_an_error_with_the_nearest_key(self):
        self.assertEqual(optable.check_vamos_opts({"analgo": "xyce"}),
                         ["unknown vamos option --vamos-analgo=xyce (did you mean --vamos-analog?)"])
        self.assertEqual(optable.check_vamos_opts({"analog_stp": "5u", "keeep": True}),
                         ["unknown vamos option --vamos-analog-stp=5u (did you mean --vamos-analog-stop?)",
                          "unknown vamos option --vamos-keeep (did you mean --vamos-keep?)"])
        self.assertEqual(optable.check_vamos_opts({"zzz": True}), ["unknown vamos option --vamos-zzz"])
        self.assertIn("planned", optable.check_vamos_opts({"mc": "10"})[0])

    def test_values(self):
        chk = optable.check_vamos_opts
        self.assertEqual(chk({"analog": True}), ["--vamos-analog needs a value: --vamos-analog=vacask|xyce"])
        self.assertEqual(chk({"analog": ""}), ["--vamos-analog needs a value: --vamos-analog=vacask|xyce"])
        self.assertEqual(chk({"analog": "spectre"}),
                         ["--vamos-analog=spectre: the analog engine must be vacask or xyce"])
        self.assertEqual(chk({"analog": "Xyce", "parhier": "LOCAL"}), [])
        self.assertEqual(chk({"analog_maxstep": True}),
                         ["--vamos-analog-maxstep needs a value: --vamos-analog-maxstep=<time>"])
        self.assertEqual(chk({"parhier": "lcoal"}), ["--vamos-parhier=lcoal: the value must be local or global"])
        self.assertEqual(chk({"strict": "0"}), ["--vamos-strict takes no value (--vamos-strict=0)"])
        self.assertEqual(chk({"banner": True}), ["--vamos-banner needs a value: --vamos-banner=<name|path|none>"])
        self.assertEqual(chk({"keep": True, "no_deck_check": True, "analog_stop": "5u", "daidir": "d"}), [])

    def test_every_key_the_code_reads_is_in_the_table(self):
        pat = re.compile(r"""\bopts(?:\.get\(|\[)\s*["']([a-z_]+)["']""")
        seen = set()
        for d, _, files in os.walk(os.path.join(ROOT, "vamos")):
            for f in files:
                if f.endswith(".py"):
                    with open(os.path.join(d, f)) as fh:
                        seen |= set(pat.findall(fh.read()))
        seen.discard("gmin")            # netlist/tables.py: a .option dict, not vamos options
        seen.discard("method")
        self.assertTrue({"analog", "keep", "strict", "parhier"} <= seen, seen)
        self.assertEqual(sorted(seen - set(optable.VAMOS_KEYS)), [])

    def test_effects(self):
        eff = optable.vamos_option_effects
        self.assertEqual([o for o, _ in eff({"analog": "xyce", "keep": True}, "vcs", ams=False, run=False)],
                         ["--vamos-analog=xyce", "--vamos-keep"])
        self.assertEqual(eff({"analog": "xyce", "keep": True}, "vcs", ams=True, run=True), [])
        self.assertIn("digital run", eff({"keep": True}, "vcs", ams=False, run=True)[0][1])
        self.assertEqual([o for o, _ in eff({"analog_stop": "1u", "keep": True, "strict": True}, "simv")],
                         ["--vamos-analog-stop=1u"])
        self.assertEqual(eff({"daidir": "x"}, "vcs")[0][0], "--vamos-daidir=x")
        self.assertIn("real nvc", eff({"verbose": True}, "nvc")[0][1])

    def test_cli_rejects_bad_options_before_anything_runs(self):
        rc, out, err = call_cli(["-vcs", "--vamos-analgo=xyce", "--vamos-strict", "nofile.v"])
        self.assertEqual(rc, 2)
        self.assertIn("vamos: error: unknown vamos option --vamos-analgo=xyce (did you mean "
                      "--vamos-analog?)", err)
        self.assertNotIn("cannot be opened", err)
        rc, _, err = call_cli(["-vcs-ams", "--vamos-analog", "x.v"])
        self.assertEqual(rc, 2)
        self.assertIn("--vamos-analog needs a value", err)
        rc, _, err = call_cli(["--vamos-version=2"])
        self.assertEqual(rc, 2)

    def test_simv_warns_about_compile_time_options(self):
        ran = r"vamos: error: .*%s" % re.escape(self.tmp)     # simv's "no job record here" error
        rc, _, err = call_cli(["-simv", "--vamos-analog=xyce", "--vamos-daidir=" + self.tmp])
        self.assertEqual(rc, 1)                         # no job record there
        self.assertIn("vamos: warning: --vamos-analog=xyce has no effect: a compile-time option", err)
        self.assertRegex(err, ran)
        rc, _, err = call_cli(["-simv", "--vamos-analog=xyce", "--vamos-strict", "--vamos-daidir=" + self.tmp])
        self.assertEqual(rc, 1)
        self.assertIn("vamos: error: --vamos-strict: 1 unsupported/unknown option(s): --vamos-analog=xyce", err)
        self.assertNotRegex(err, ran)                   # stopped before simv looked at the daidir

    def test_simv_append_log_reaches_the_runtime(self):
        got = {}
        saved = cli.PERSONALITIES["simv"]
        cli.PERSONALITIES["simv"] = lambda args, opts: got.setdefault("args", args) and 0
        try:
            rc, _, _ = call_cli(["-simv", "--vamos-daidir=d", "-l", "x.log", "--vamos-append-log"])
        finally:
            cli.PERSONALITIES["simv"] = saved
        self.assertEqual(rc, 0)
        self.assertEqual(got["args"], ["-l", "x.log", "--vamos-append-log"])

    def test_vcs_records_ineffective_options(self):
        self.write("x.v", "module x; endmodule\n")
        j = vcs.build_job(["x.v"], self.tmp)
        vcs._note_vamos_options(j, {"analog": "xyce", "keep": True, "strict": True}, ams=False)
        self.assertEqual([(u.option, u.disposition) for u in j.unmapped],
                         [("--vamos-analog=xyce", INAPPLICABLE), ("--vamos-keep", INAPPLICABLE)])
        self.assertEqual(optable.strict_failures(j), ["--vamos-analog=xyce", "--vamos-keep"])
        lines = []
        optable.report_unmapped(j, lines.append)
        self.assertEqual(lines[0], "vamos: warning: --vamos-analog=xyce has no effect: not an AMS compile "
                                   "(no -ad, +ad or vcs-ams)")

    def test_vamos_options_in_option_files_are_reported(self):
        self.write("run.f", "--vamos-analog=xyce x.v\n")
        j = vcs.build_job(["-f", "run.f"], self.tmp)
        self.assertEqual([(u.option, u.disposition) for u in j.unmapped],
                         [("--vamos-analog=xyce", UNSUPPORTED)])
        self.assertIn("command line only", j.unmapped[0].note)

    def test_help_texts_list_every_option_and_point_at_the_guide(self):
        usage = cli.usage()
        for o in optable.VAMOS_OPTIONS:
            self.assertIn(optable.vamos_option_text(o.key, True), usage)
        for p in ("vcs", "vcs-ams"):
            h = vcs.help_text(p)
            self.assertTrue(h.startswith("usage: %s " % p), h[:40])
            for o in optable.VAMOS_OPTIONS:
                if o.where != "simv":
                    self.assertIn(optable.vamos_option_text(o.key, True), h)
            for opt in ("-ad[=<file>]", "+ad[=<file>]", "-top <mod>[+<mod>...]", "-override_timescale"):
                self.assertIn(opt, h)
            self.assertIn("VAMOS_GUIDE.md", h)
            self.assertNotIn("VAMOS_PLAN", h)
            self.assertTrue(all(len(ln) <= 100 for ln in h.splitlines()), h)
        self.assertIn("VAMOS_GUIDE.md", usage)
        self.assertIn("vcs-ams", vcs.help_text("vcs-ams").splitlines()[0])
        rc, out, _ = call_cli(["-vcs-ams", "-h"])
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("usage: vcs-ams"))
        rc, out, _ = call_cli(["-h"])
        self.assertEqual(rc, 0)
        self.assertIn("--vamos-analog-maxstep", out)


# =============================================================================
# vcs command line: -top, plusargs, -gui, the executable name (Idriver-07, 09)
# =============================================================================

class TestVcsCommandLine(TempDir):
    def job(self, *args):
        return vcs.build_job(list(args), self.tmp)

    def test_top_plus_form(self):
        self.assertEqual(self.job("-top", "a+b+", "-top", "c", "x.v").tops, ["a", "b", "c"])

    def test_plusargs_noted_only_without_R(self):
        j = self.job("+CYCLES=2", "+verbose", "x.v")
        self.assertEqual(j.plusargs, ["+CYCLES=2", "+verbose"])
        self.assertEqual([(u.option, u.disposition) for u in j.unmapped],
                         [("+CYCLES=2", NOTED), ("+verbose", NOTED)])
        self.assertIn("reaches only a -R run", j.unmapped[0].note)
        j = self.job("-R", "+CYCLES=2", "+verbose", "x.v")
        self.assertEqual(j.plusargs, ["+CYCLES=2", "+verbose"])
        self.assertEqual(j.unmapped, [])

    def test_gui_note_promises_no_waves(self):
        j = self.job("-gui", "x.v")
        self.assertEqual(j.unmapped[0].disposition, NOTED)
        self.assertNotIn("will be written", j.unmapped[0].note)
        self.assertIn("no waves", j.unmapped[0].note)

    def test_plusarg_save_is_unsupported(self):
        j = self.job("+plusarg_save", "+foo", "x.v")
        self.assertEqual(j.unmapped[0].option, "+plusarg_save")
        self.assertEqual(j.unmapped[0].disposition, UNSUPPORTED)

    def test_display_exe(self):
        self.assertEqual(vcs.display_exe(self.job("-o", "sim/tb_simv", "x.v")), os.path.join("sim", "tb_simv"))
        self.assertEqual(vcs.display_exe(self.job("x.v")), "simv")

    def test_ams_refuses_several_tops_before_compiling(self):
        self.write("x.v", "module a; endmodule\nmodule b; endmodule\n")
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                rc = vcs.main(["-ad", "-top", "a+b+", "x.v"], {})
        finally:
            os.chdir(cwd)
        self.assertEqual(rc, 1)
        self.assertIn("vamos: error: -top: an AMS design has one top module; -top was given 2 times (a, b)",
                      err.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "simv")))


# =============================================================================
# VAMOS_<TOOL> overrides, versions, the provenance header (Idriver-08)
# =============================================================================

class TestToolOverrides(TempDir):
    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.tool = os.path.join(self.bin, "mynvc")
        with open(self.tool, "w") as fh:
            fh.write("#!/bin/sh\necho mynvc 9.9\n")
        os.chmod(self.tool, 0o755)

    def test_unset_is_none(self):
        os.environ.pop("VAMOS_ZZTOOL", None)
        self.assertIsNone(tools.checked_override("VAMOS_ZZTOOL", "zztool"))

    def test_good_paths(self):
        os.environ["VAMOS_NVC"] = self.tool
        self.assertEqual(tools.find_real("nvc"), self.tool)
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            os.environ["VAMOS_NVC"] = "bin/mynvc"            # relative: made absolute
            self.assertEqual(os.path.normcase(tools.find_real("nvc")), os.path.normcase(self.tool))
        finally:
            os.chdir(cwd)
        os.environ["PATH"] = self.bin + os.pathsep + os.environ.get("PATH", "")
        os.environ["VAMOS_NVC"] = "mynvc"                   # a bare name: looked up on PATH
        self.assertEqual(tools.find_real("nvc"), self.tool)

    def test_bad_paths_are_errors(self):
        cases = [("/nonexistent/nvc", "no such file"), (self.bin, "is a directory"),
                 ("no-such-tool-xyz", "no executable 'no-such-tool-xyz' on PATH")]
        if os.path.exists(LAUNCHER):
            cases.append((LAUNCHER, "is vamos itself"))
        plain = os.path.join(self.bin, "plain")
        with open(plain, "w") as fh:
            fh.write("x\n")
        os.chmod(plain, 0o644)
        if not os.access(plain, os.X_OK):                 # the filesystem keeps modes
            cases.append((plain, "is not executable"))
        for val, want in cases:
            with self.subTest(val=val):
                os.environ["VAMOS_NVC"] = val
                with self.assertRaises(tools.ToolError) as cm:
                    tools.find_real("nvc")
                self.assertIn("VAMOS_NVC=%s" % val, str(cm.exception))
                self.assertIn(want, str(cm.exception))
                self.assertIn("the real nvc", str(cm.exception))

    def test_vcs_reports_a_bad_override_without_a_traceback(self):
        self.write("x.v", "module x; endmodule\n")
        os.environ["VAMOS_NVC"] = "/nonexistent/nvc"
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            rc, out, err = call_cli(["-vcs", "x.v"])
        finally:
            os.chdir(cwd)
        self.assertEqual(rc, 1)
        self.assertIn("vamos: error: VAMOS_NVC=/nonexistent/nvc: no such file (it must name the real nvc)",
                      err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "simv")))

    def test_version_text(self):
        vt = tools.version_text
        self.assertEqual(vt("nvc 1.19-devel (1.18.0.r611.ge685d1a75) (Using LLVM 19.1.7)\n"), "1.19-devel")
        self.assertEqual(vt("Icarus Verilog version 13.0 (devel) (6029f3dfb)\n\nCopyright"), "13.0")
        self.assertEqual(vt("This is vacask 0.3.4-91-g64489cf7.\n(c)2023"), "0.3.4-91-g64489cf7")
        self.assertEqual(vt("OpenVAF-reloaded 20260616-3-g0e83f1ed\n"), "20260616-3-g0e83f1ed")
        self.assertEqual(vt("Xyce DEVELOPMENT-202609292309-(Release-7.10.0-203-g1c36edca)-opensource"),
                         "7.10.0-203-g1c36edca")
        self.assertEqual(vt("usage: vamos -<personality> [tool arguments...]\n"), "?")
        self.assertEqual(vt("vamos: error: 'vcs' was called from inside vamos vcs and no real 'vcs'"), "?")
        self.assertEqual(vt("sh: 1: /x: not found"), "?")
        self.assertEqual(vt(""), "?")
        self.assertEqual(vt("sometool (no version)"), "?")

    def test_version_of_a_tool_that_cannot_run(self):
        self.assertEqual(tools.version_of("nvc", "/nonexistent/nvc"), "?")

    def test_provenance_columns_line_up(self):
        text = banner.provenance([("vamos", "git-abc", "/x/vamos"),
                                  ("OpenVAF-r", "20260616-3-g0e83f1ed-long-build", "/x/openvaf-r"),
                                  ("VACASK", "0.3.4-91-g64489cf7", "/x/vacask"),
                                  ("stat-sim", "1.0", "/x/stat-sim")], "vcs")
        rows = text.splitlines()[1:]
        spdx = [r.index(s) for r, s in zip(rows, ("GPL-3.0-or-later", "GPL-3.0-only", "AGPL-3.0-only",
                                                  "PolyForm"))]
        paths = [r.index("/x/") for r in rows]
        self.assertEqual(len(set(spdx)), 1, text)
        self.assertEqual(len(set(paths)), 1, text)


# =============================================================================
# licences and banner comments (Idriver-12)
# =============================================================================

class TestLicenceTable(unittest.TestCase):
    def load(self, *parts):
        with open(os.path.join(ROOT, "vamos", *parts), encoding="utf-8") as fh:
            return json.load(fh)

    def test_layers_in_the_comments(self):
        for parts in (("licenses.json",), ("banners", "vcs.json")):
            c = self.load(*parts)["_comment"]
            for layer in ("etc/vamos/", "~/.config/vamos/", "~/.vamos/", "./.vamos/"):
                self.assertIn(layer, c, parts)

    def test_forks_and_upstreams(self):
        lic = self.load("licenses.json")
        for tool, fork, up in (("nvc", "kev-cam/nvc", "nickg/nvc"),
                               ("iverilog", "kev-cam/iverilog", "steveicarus/iverilog"),
                               ("VACASK", "kev-cam/VACASK", "arpadbuermen/VACASK"),
                               ("Xyce", "kev-cam/xyce", "xyce.sandia.gov")):
            self.assertIn(fork, lic[tool]["url"])
            self.assertIn(up, lic[tool]["upstream"])
        self.assertEqual(lic["nvc"]["spdx"], "GPL-3.0-or-later")      # the upstream licence, kept
        self.assertEqual(lic["VACASK"]["spdx"], "AGPL-3.0-only")
        report = banner.license_report()
        self.assertIn("https://github.com/kev-cam/nvc (a fork of https://github.com/nickg/nvc)", report)
        rows = [ln for ln in report.splitlines() if ln.startswith("  ")]
        self.assertEqual(len({r.index("http") for r in rows}), 1, report)


# =============================================================================
# the ./simv stub and a failed compile (Idriver-01, 11)
# =============================================================================

class TestStub(TempDir):
    def setUp(self):
        super().setUp()
        self.fake = os.path.join(self.tmp, "fake_vamos")
        with open(self.fake, "w") as fh:
            fh.write("#!/bin/sh\nfor a in \"$@\"; do echo \"arg:$a\"; done\n")
        os.chmod(self.fake, 0o755)
        os.environ["VAMOS_LAUNCHER"] = self.fake

    def job(self, exe="simv", cwd=None):
        j = Job("vcs", cwd=cwd or os.path.join(self.tmp, "a"))
        j.exe = os.path.join(j.cwd, exe)
        j.daidir = j.exe + ".daidir"
        os.makedirs(j.daidir, exist_ok=True)
        return j

    def run_stub(self, path, *args, cwd=None):
        r = subprocess.run(["/bin/sh", "-c", 'exec "$0" "$@"', path] + list(args),
                           cwd=cwd or self.tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=60)
        return r.returncode, r.stdout, r.stderr

    @staticmethod
    def daidir_arg(out):
        for ln in out.splitlines():
            if ln.startswith("arg:--vamos-daidir="):
                return os.path.realpath(ln[len("arg:--vamos-daidir="):])
        return None

    @unittest.skipUnless(POSIX_SH, "needs /bin/sh")
    def test_the_stub_runs_the_daidir_next_to_it(self):
        j = self.job()
        vcs.write_stub(j)
        a = os.path.join(self.tmp, "a")
        rc, out, _ = self.run_stub(j.exe, "+x")
        self.assertEqual(rc, 0)
        self.assertEqual(self.daidir_arg(out), os.path.realpath(j.daidir))
        self.assertIn("arg:+x", out)
        # a copy runs its own daidir, also once the original is gone
        b = os.path.join(self.tmp, "b")
        shutil.copytree(a, b, symlinks=True)
        rc, out, _ = self.run_stub(os.path.join(b, "simv"))
        self.assertEqual(self.daidir_arg(out), os.path.realpath(os.path.join(b, "simv.daidir")))
        shutil.rmtree(a)
        rc, out, _ = self.run_stub(os.path.join(b, "simv"))
        self.assertEqual(self.daidir_arg(out), os.path.realpath(os.path.join(b, "simv.daidir")))
        # invoked by a bare name from its own directory (sh simv)
        r = subprocess.run(["/bin/sh", "simv"], cwd=b, stdout=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(self.daidir_arg(r.stdout), os.path.realpath(os.path.join(b, "simv.daidir")))
        # a symlink to simv alone falls back to the daidir beside its target
        c = os.path.join(self.tmp, "c")
        os.makedirs(c)
        os.symlink(os.path.join(b, "simv"), os.path.join(c, "simv"))
        rc, out, _ = self.run_stub(os.path.join(c, "simv"))
        self.assertEqual(self.daidir_arg(out), os.path.realpath(os.path.join(b, "simv.daidir")))

    @unittest.skipUnless(POSIX_SH, "needs /bin/sh")
    def test_o_layout(self):
        j = self.job(exe=os.path.join("sim", "tb simv"))
        vcs.write_stub(j)
        rc, out, _ = self.run_stub(j.exe, cwd=os.path.join(self.tmp, "a"))
        self.assertEqual(self.daidir_arg(out), os.path.realpath(j.daidir))

    @unittest.skipUnless(POSIX_SH, "needs /bin/sh")
    def test_invalidate_disables_simv_and_removes_the_record(self):
        j = self.job()
        vcs.write_stub(j)
        for p in (os.path.join(j.daidir, "vamos.job.json"),
                  os.path.join(j.exe + ".msv", "interface_element.rpt")):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write("old\n")
        vcs.invalidate(j)
        self.assertFalse(os.path.exists(os.path.join(j.daidir, "vamos.job.json")))
        self.assertFalse(os.path.exists(os.path.join(j.exe + ".msv", "interface_element.rpt")))
        rc, out, err = self.run_stub(j.exe)
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("vamos: error: %s: the last compile into simv.daidir failed or was interrupted, "
                      "so there is nothing to run; compile again" % j.exe, err)
        vcs.write_stub(j)                                  # what a successful compile ends with
        rc, out, _ = self.run_stub(j.exe)
        self.assertEqual(rc, 0)

    def test_main_invalidates_before_the_compile_touches_the_daidir(self):
        """vcs.main with a compile that fails: ./simv refuses to run, the old record is gone."""
        d = os.path.join(self.tmp, "m")
        os.makedirs(os.path.join(d, "simv.daidir"))
        with open(os.path.join(d, "x.v"), "w") as fh:
            fh.write("module x; endmodule\n")
        with open(os.path.join(d, "simv"), "w") as fh:
            fh.write("#!/bin/sh\necho old build\n")
        with open(os.path.join(d, "simv.daidir", "vamos.job.json"), "w") as fh:
            fh.write(Job("vcs").to_json())
        rc, err = self.failing_main(d)
        self.assertEqual(rc, 1)
        self.assertIn("vamos: error: translation failed", err)
        self.assertFalse(os.path.exists(os.path.join(d, "simv.daidir", "vamos.job.json")))
        with open(os.path.join(d, "simv")) as fh:
            self.assertIn("failed or was interrupted", fh.read())

    def test_a_failed_first_compile_leaves_no_simv(self):
        d = os.path.join(self.tmp, "f")
        os.makedirs(d)
        with open(os.path.join(d, "x.v"), "w") as fh:
            fh.write("module x; endmodule\n")
        rc, err = self.failing_main(d)
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.lexists(os.path.join(d, "simv")))

    def failing_main(self, d):
        """vcs.main(["x.v"]) in d, with a backend whose compile fails: (rc, stderr)."""
        class FakeBackend:
            def __init__(self, job, emit):
                pass

            def compile_tools(self):
                return []

        def failing_compile(job, be, con, opts):
            raise BackendError("translation failed")

        saved = (vcs.NvcBackend, vcs.plain_compile)
        vcs.NvcBackend, vcs.plain_compile = FakeBackend, failing_compile
        cwd = os.getcwd()
        os.chdir(d)
        try:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = vcs.main(["x.v"], {})
        finally:
            os.chdir(cwd)
            vcs.NvcBackend, vcs.plain_compile = saved
        return rc, err.getvalue()


# =============================================================================
# the plain compile's helpers (Idriver-03..07)
# =============================================================================

LIB_SRC = """module leaf(output [3:0] y); assign y = 4'd5; endmodule
module tb; wire [3:0] y; leaf u(.y(y)); initial #1 $display("y=%0d", y); endmodule
module leaf(output [3:0] y); assign y = 4'd9; endmodule
module other; endmodule
module only_lib; endmodule
module only_lib; endmodule
"""


class TestPlainHelpers(unittest.TestCase):
    def test_library_rule(self):
        pp = vp.from_text(LIB_SRC, lib_lines=[(3, 6)])         # lines 3-6 came from -v files
        text, names = vcs.library_rule(pp)
        self.assertEqual(names, ["leaf", "only_lib"])
        lines = text.splitlines()
        self.assertEqual(len(lines), len(LIB_SRC.splitlines()))  # line numbers kept
        self.assertIn("4'd5", lines[0])
        self.assertEqual(lines[2].strip(), "")                  # the -v copy of leaf
        self.assertIn("module other", lines[3])
        self.assertIn("module only_lib", lines[4])              # the first -v copy wins
        self.assertEqual(lines[5].strip(), "")

    def test_two_source_definitions_are_left_to_iverilog(self):
        pp = vp.from_text("module a; endmodule\nmodule a; endmodule\n")
        self.assertEqual(vcs.library_rule(pp), (pp.text, []))

    def test_tops(self):
        pp = vp.from_text("module alpha; endmodule\nmodule beta; sub s(); endmodule\nmodule sub; endmodule\n")
        self.assertEqual(vcs.plain_tops(Job("vcs"), pp), ["alpha", "beta"])
        self.assertEqual(vcs.plain_tops(Job("vcs", tops=["beta", "sub", "beta"]), pp), ["beta", "sub"])
        with self.assertRaises(BackendError) as cm:
            vcs.plain_tops(Job("vcs", tops=["alhpa"]), pp)
        self.assertEqual(str(cm.exception), "-top alhpa: no module alhpa in the Verilog sources (did you mean "
                                            "alpha?); the top-level modules are: alpha, beta")
        loop = vp.from_text("module a; b u(); endmodule\nmodule b; a u(); endmodule\n")
        with self.assertRaises(BackendError) as cm:
            vcs.plain_tops(Job("vcs"), loop)
        self.assertIn("no top-level module", str(cm.exception))

    def test_lib_only_modules_are_not_tops(self):
        pp = vp.from_text(LIB_SRC, lib_lines=[(3, 6)])
        self.assertEqual(vcs.plain_tops(Job("vcs"), pp), ["tb"])

    def test_deferred_reason(self):
        import tempfile
        pp = vp.from_text("module tb;\n  string s;\n  initial s = $sformatf(\"x\");\nendmodule\n")
        log = ("=== iverilog-sv2ghdl: iverilog -E (preprocess)\n"
               "=== sv2vhdl-modules: iverilog -tvhdl -s sub: translated\n"
               "=== sv2vhdl-modules: iverilog -tvhdl -s tb: deferred (iverilog exit 1)\n"
               "/d/simv.daidir/nvc/_norm.sv:3: error: No function named `$sformatf' found\n"
               "error: Code generation had 1 error(s).\n"
               "=== sv2vhdl-modules: iverilog -tvhdl -s other: deferred (timed out after 600 s)\n")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "iverilog.log")
            with open(p, "w") as fh:
                fh.write(log)
            self.assertEqual(vcs.deferred_reason(p, "tb", pp),
                             "pp.orig.v:3: error: No function named `$sformatf' found")
            self.assertEqual(vcs.deferred_reason(p, "other"), "deferred (timed out after 600 s)")
            self.assertEqual(vcs.deferred_reason(os.path.join(d, "none"), "tb"), "no iverilog.log")

    VHD = """entity alpha is
  port (
    clk : in logic3d;   -- a comment ; with a semicolon
    d, e : in logic3d_vector(3 downto 0);
    r : in real;
    q : out logic3d_vector(3 downto 0);
    w : inout resolved_logic3d;
    k : in logic3d := L3D_1
  );
  attribute nvc_verilog_src : string;
end entity;
architecture from_verilog of alpha is begin end architecture;
entity beta is
end entity;
entity gamma is
  port ( t : in time );
end entity;
"""

    def test_entity_ports_and_wrapper(self):
        ports = vcs._entity_ports(self.VHD, "alpha")
        self.assertEqual(ports, [("clk", "in", "logic3d", False), ("d", "in", "logic3d_vector(3 downto 0)", False),
                                 ("e", "in", "logic3d_vector(3 downto 0)", False), ("r", "in", "real", False),
                                 ("q", "out", "logic3d_vector(3 downto 0)", False),
                                 ("w", "inout", "resolved_logic3d", False), ("k", "in", "logic3d", True)])
        self.assertEqual(vcs._entity_ports(self.VHD, "beta"), [])
        self.assertIsNone(vcs._entity_ports(self.VHD, "nosuch"))
        w = vcs.wrapper_vhdl("vamos_tops", ["alpha", "beta"], self.VHD)
        self.assertIn("entity vamos_tops is", w)
        self.assertIn("top1: entity work.alpha\n    port map (clk => L3D_Z, d => (others => L3D_Z), "
                      "e => (others => L3D_Z), r => 0.0);", w)
        self.assertIn("top2: entity work.beta;", w)
        with self.assertRaises(BackendError):
            vcs.wrapper_vhdl("vamos_tops", ["alpha", "gamma"], self.VHD)
        with self.assertRaises(BackendError):
            vcs.wrapper_vhdl("vamos_tops", ["alpha", "nosuch"], self.VHD)

    def test_wrapper_name_avoids_user_names(self):
        pp = vp.from_text("module vamos_tops; endmodule\nmodule b; endmodule\n")
        self.assertEqual(vcs._wrapper_name(pp, "entity VAMOS_TOPS_1 is\nend entity;\n"), "vamos_tops_2")


# =============================================================================
# untranslated system tasks and functions (Idriver-05)
# =============================================================================

class TestUnsupportedFunctions(unittest.TestCase):
    VHD = ("    fd := to_l3d(0, 32);  -- Unsupported system function $fopen replaced by 0 here (/d/nvc/_norm.sv:3)\n"
           "    null;  -- Unsupported system task $fdisplay omitted here (/d/nvc/_norm.sv:4)\n"
           "    fd := to_l3d(0, 32);  -- Unsupported system function $fopen replaced by 0 here (/d/nvc/_norm.sv:3)\n"
           "    x := 0.0;  -- Unsupported system function $bitstoreal replaced by 0.0 here (/x/y.v:9)\n")

    def test_plain_warns_ams_refuses(self):
        pp = vp.from_text("module t;\n  integer fd;\n  initial fd = $fopen(\"o\");\n  initial $fdisplay(fd);\nendmodule\n")
        plain = vp.unsupported_tasks(self.VHD, pp, ams=False)
        self.assertEqual([(n.severity, n.origin, n.message) for n in plain], [
            ("warning", "pp.orig.v:3", "system function $fopen is not translated: every call returns 0 in "
                                       "the simulation"),
            ("warning", "pp.orig.v:4", "system task $fdisplay is not translated: the simulation drops it"),
            ("warning", "/x/y.v:9", "system function $bitstoreal is not translated: every call returns 0.0 "
                                    "in the simulation")])
        ams = vp.unsupported_tasks(self.VHD, pp, ams=True)
        self.assertEqual({n.severity for n in ams}, {"error"})
        self.assertEqual(ams[0].message, "system function $fopen is not translated (it would return 0 in the "
                                         "simulation)")
        self.assertEqual(ams[1].message, "system task $fdisplay is not translated (it would be dropped from "
                                         "the simulation)")


# =============================================================================
# end to end: plain vcs
# =============================================================================

TWO_TOPS = """`timescale 1ns/1ps
module alpha(input clk, input [3:0] d, output reg [3:0] q);
  initial begin #10 $display("alpha %m at %0t d=%b clk=%b", $time, d, clk); end
endmodule
module beta;
  sub s1();
  initial begin #20 $display("beta %m at %0t", $time); #5 $finish; end
endmodule
module sub;
  initial begin #15 $display("sub %m at %0t", $time); end
endmodule
"""

DUMPS = """`timescale 1ns/1ps
module tb;
  reg [7:0] r = 8'h5a;
  initial begin
    $dumpfile("x.vcd");
    $dumpvars(0, tb);
    #1 $display("r=%h", r);
    $finish;
  end
endmodule
"""

# A top tgt-vhdl cannot translate (a class: an abort or an error, either way a deferred stub).
CLASS_TOP = """`timescale 1ns/1ps
class C; int x; function new(); x = 3; endfunction endclass
module tb;
  C c;
  initial begin c = new(); $display("x=%0d", c.x); $finish; end
endmodule
"""


@needs_stack
class TestPlainE2E(TempDir):
    def tool(self, *args, cwd=None, env=None, prog="vcs"):
        e = dict(os.environ)
        e["PATH"] = SHIMS + os.pathsep + e.get("PATH", "")
        e.pop("VAMOS_ANALOG", None)
        e.update(env or {})
        return run([prog] + list(args), cwd=cwd or self.tmp, env=e, timeout=600)

    def simv(self, *args, cwd=None, exe="./simv"):
        return run([exe] + list(args), cwd=cwd or self.tmp, timeout=600)

    @staticmethod
    def tops_listed(out):
        lines = out.splitlines()
        i = lines.index("Top Level Modules:")
        tops = []
        for ln in lines[i + 1:]:
            if not ln.startswith(" ") or not ln.strip():
                break
            tops.append(ln.strip())
        return tops

    def test_every_uninstantiated_module_is_a_top(self):
        self.write("two.v", TWO_TOPS)
        c = self.tool("two.v")
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertEqual(self.tops_listed(c.stdout), ["alpha", "beta"])
        with open(os.path.join(self.tmp, "simv.daidir", "vamos.job.json")) as fh:
            self.assertEqual(json.load(fh)["tops"], ["vamos_tops", "alpha", "beta"])
        r = self.simv()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("alpha alpha at 10000 d=zzzz clk=z", r.stdout)      # an undriven top-level input
        self.assertIn("sub beta.s1 at 15000", r.stdout)
        self.assertIn("beta beta at 20000", r.stdout)
        self.assertIn("Time: 25ns", r.stdout)

    def test_top_options(self):
        self.write("two.v", TWO_TOPS)
        for args in (["-top", "alpha", "-top", "beta"], ["-top", "alpha+beta+"]):
            with self.subTest(args=args):
                c = self.tool("two.v", *args)
                self.assertEqual(c.returncode, 0, c.stdout)
                self.assertEqual(self.tops_listed(c.stdout), ["alpha", "beta"])
                r = self.simv()
                self.assertIn("alpha alpha at 10000", r.stdout)
                self.assertIn("beta beta at 20000", r.stdout)
        c = self.tool("two.v", "-top", "beta")
        self.assertEqual(self.tops_listed(c.stdout), ["beta"])
        r = self.simv()
        self.assertNotIn("alpha", r.stdout)
        self.assertIn("beta beta at 20000", r.stdout)
        c = self.tool("two.v", "-top", "alhpa")
        self.assertEqual(c.returncode, 1, c.stdout)
        self.assertIn("vamos: error: -top alhpa: no module alhpa in the Verilog sources (did you mean alpha?); "
                      "the top-level modules are: alpha, beta", c.stdout)

    def test_a_deferred_top_is_an_error_quoting_iverilog(self):
        self.write("tb.v", "module tb; initial #1 $display(\"ok\"); endmodule\n")
        self.assertEqual(self.tool("tb.v").returncode, 0)
        self.write("cls.sv", CLASS_TOP)
        c = self.tool("-sverilog", "cls.sv")
        self.assertEqual(c.returncode, 1, c.stdout)
        self.assertRegex(c.stdout, r"vamos: error: sv2ghdl could not translate top module 'tb': \S.* "
                                   r"\(see .*simv\.daidir/nvc/iverilog\.log\)")
        self.assertNotIn("_mods.vhd", c.stdout)
        r = self.simv()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("the last compile into simv.daidir failed or was interrupted", r.stdout)
        r = run(["sh", "simv"], cwd=self.tmp)
        self.assertEqual(r.returncode, 1)
        # an extra, uninstantiated module that cannot be translated: VCS would elaborate it
        self.write("two.sv", "`timescale 1ns/1ps\nmodule tb; initial #1 $display(\"tb ran\"); endmodule\n"
                             "module junk;\n  class K; int x; function new(); x = 3; endfunction endclass\n"
                             "  K k;\n  initial begin k = new(); $display(\"x=%0d\", k.x); end\nendmodule\n")
        c = self.tool("-sverilog", "two.sv")
        self.assertEqual(c.returncode, 1, c.stdout)
        self.assertIn("vamos: error: sv2ghdl could not translate top module 'junk'", c.stdout)
        self.assertIn("give -top to choose the tops", c.stdout)
        c = self.tool("-sverilog", "two.sv", "-top", "tb")
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertIn("tb ran", self.simv().stdout)

    def test_untranslated_tasks_are_warnings_at_the_users_line(self):
        self.write("tb.v", DUMPS)
        c = self.tool("tb.v")
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertIn("vamos: warning: tb.v:5: system task $dumpfile is not translated: the simulation drops it",
                      c.stdout)
        self.assertIn("vamos: warning: tb.v:6: system task $dumpvars is not translated", c.stdout)
        self.assertIn("r=5a", self.simv().stdout)
        c = self.tool("tb.v", "--vamos-strict")
        self.assertEqual(c.returncode, 1, c.stdout)
        self.assertIn("vamos: error: tb.v:5: system task $dumpfile is not translated", c.stdout)

    def test_replaced_system_functions_are_warnings(self):
        self.write("tb.v", "`timescale 1ns/1ps\nmodule tb;\n  integer fd;\n"
                           "  initial begin fd = $fopen(\"o.txt\", \"w\"); #1 $display(\"fd=%0d\", fd); end\n"
                           "endmodule\n")
        c = self.tool("tb.v")
        with open(os.path.join(self.tmp, "simv.daidir", "nvc", "design.vhd"), errors="replace") as fh:
            vhd = fh.read()
        if "Unsupported system function" not in vhd:
            self.skipTest("this tgt-vhdl leaves no located comment for a replaced system function")
        self.assertIn("vamos: warning: tb.v:4: system function $fopen is not translated: every call returns",
                      c.stdout)

    def test_library_rule(self):
        self.write("top.v", "`timescale 1ns/1ps\nmodule leaf(output [3:0] y); assign y = 4'd5; endmodule\n"
                            "module tb; wire [3:0] y; wire [3:0] z; leaf u(.y(y)); helper h(.z(z));\n"
                            "  initial #1 begin $display(\"y=%0d z=%0d\", y, z); $finish; end endmodule\n")
        self.write("lib.v", "`timescale 1ns/1ps\nmodule leaf(output [3:0] y); assign y = 4'd9; endmodule\n"
                            "module helper(output [3:0] z); assign z = 4'd7; endmodule\n"
                            "module unused; initial $display(\"unused ran\"); endmodule\n")
        c = self.tool("top.v", "-v", "lib.v")
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertEqual(self.tops_listed(c.stdout), ["tb"])
        r = self.simv()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("y=5 z=7", r.stdout)
        self.assertNotIn("unused ran", r.stdout)

    def test_failed_recompile_never_runs_a_half_rebuilt_daidir(self):
        self.write("tb.v", "`timescale 1ns/1ps\nmodule tb; initial begin #5 $display(\"first build\"); "
                           "$finish; end endmodule\n")
        self.assertEqual(self.tool("tb.v").returncode, 0)
        self.assertIn("first build", self.simv().stdout)
        self.write("tb.v", "`timescale 1ns/1ps\nmodule tb; initial begin #5 $display(\"second\") $finish; "
                           "end endmodule\n")           # a syntax error
        c = self.tool("tb.v")
        self.assertEqual(c.returncode, 1, c.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "simv.daidir", "vamos.job.json")))
        r = self.simv()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertNotIn("first build", r.stdout)
        self.assertIn("vamos: error: ./simv: the last compile into simv.daidir failed or was interrupted",
                      r.stdout)
        # a command-line error stops before the daidir is touched: the old build still runs
        self.write("tb.v", "`timescale 1ns/1ps\nmodule tb; initial begin #5 $display(\"third\"); $finish; "
                           "end endmodule\n")
        self.assertEqual(self.tool("tb.v").returncode, 0)
        self.assertEqual(self.tool("tb.v", "nofile.v").returncode, 1)
        self.assertIn("third", self.simv().stdout)

    def test_copied_and_moved_directories_run_their_own_daidir(self):
        a, b = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")
        os.makedirs(a)
        self.write("a/tb.v", "`timescale 1ns/1ps\nmodule tb; initial #1 $display(\"build A\"); endmodule\n")
        self.assertEqual(self.tool("tb.v", cwd=a).returncode, 0)
        shutil.copytree(a, b, symlinks=True)
        self.write("a/tb.v", "`timescale 1ns/1ps\nmodule tb; initial #1 $display(\"build A2\"); endmodule\n")
        self.assertEqual(self.tool("tb.v", cwd=a).returncode, 0)
        r = self.simv(cwd=b)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("build A\n", r.stdout + "\n")
        self.assertNotIn("build A2", r.stdout)
        moved = os.path.join(self.tmp, "a_moved")
        os.rename(a, moved)
        r = self.simv(cwd=moved)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("build A2", r.stdout)
        r = self.simv(cwd=self.tmp, exe=os.path.join(b, "simv"))        # run from elsewhere
        self.assertIn("build A\n", r.stdout + "\n")

    def test_compile_messages(self):
        self.write("tb.v", "`timescale 1ns/1ps\nmodule tb;\n  integer cycles = 1;\n  initial begin\n"
                           "    if ($value$plusargs(\"CYCLES=%d\", cycles)) $display(\"cycles=%0d\", cycles);\n"
                           "    #1 $finish;\n  end\nendmodule\n")
        c = self.tool("tb.v", "-o", "sim/tb_simv")
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertIn("Vamos: ./sim/tb_simv is up to date", c.stdout)
        r = self.simv("+CYCLES=3", exe="sim/tb_simv")
        self.assertIn("cycles=3", r.stdout)
        c = self.tool("tb.v", "-R", "+CYCLES=2")
        self.assertIn("cycles=2", c.stdout)
        with open(os.path.join(self.tmp, "simv.daidir", "vamos.job.json")) as fh:
            self.assertEqual(json.load(fh)["unmapped"], [])
        c = self.tool("tb.v", "+CYCLES=2")
        self.assertIn("vamos: note: +CYCLES=2: a plusarg given to vcs reaches only a -R run", c.stdout)

    def test_bad_overrides_and_options(self):
        self.write("tb.v", "module tb; endmodule\n")
        for var in ("VAMOS_NVC", "VAMOS_IVERILOG"):
            with self.subTest(var=var):
                c = self.tool("tb.v", env={var: "/nonexistent"})
                self.assertEqual(c.returncode, 1, c.stdout)
                self.assertIn("vamos: error: %s=/nonexistent: no such file (it must name the real %s)"
                              % (var, var[6:].lower()), c.stdout)
                self.assertNotIn("Traceback", c.stdout)
        c = self.tool("tb.v", "--vamos-baner=none")
        self.assertEqual(c.returncode, 2, c.stdout)
        self.assertIn("did you mean --vamos-banner?", c.stdout)


# =============================================================================
# end to end: vcs-ams (both engines)
# =============================================================================

RC_SP = """* RC cell with a buffer
.subckt rc_cell in out
r1 in mid 10k
c1 mid 0 1p
e1 out 0 mid 0 1
.ends
vsup sup 0 1.8
rsup sup 0 1meg
.tran 1n 1u
"""

RC_TB = """`timescale 1ns/1ps
module tb;
  reg clk = 0;
  wire out;
  always #50 clk = ~clk;
  rc_cell u1 (.in(clk), .out(out));
  always @(out) $display("%0t out=%b", $time, out);
  initial #300 $finish;
endmodule
"""


@needs_ams
class TestAmsDriverE2E(AmsCase):
    def files(self):
        return {"tb.sv": RC_TB, "rc.sp": RC_SP, "vcsAD.init": "choose xa rc.sp;\n"}

    def test_failed_recompile_leaves_nothing_to_run(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.case("fail_" + engine, self.files())
                self.compile(d, "-sverilog", "tb.sv", engine=engine)
                r = self.simv(d)
                self.assertIn("co-simulation finished", r.stdout)
                with open(os.path.join(d, "vamos_ams.raw"), "rb") as fh:
                    raw = fh.read()
                # a changed cell and a control-file rule that matches nothing: step 13 fails
                with open(os.path.join(d, "rc.sp"), "w") as fh:
                    fh.write(RC_SP.replace("10k", "100k"))
                with open(os.path.join(d, "vcsAD.init"), "a") as fh:
                    fh.write("a2d hith=2.0 node=tb.no_such_net;\n")
                c = self.compile(d, "-sverilog", "tb.sv", engine=engine, expect_rc=1)
                self.assertIn("[MSV-IE-OPT-TNF]", c.stdout)
                self.assertFalse(os.path.exists(os.path.join(d, "simv.daidir", "vamos.job.json")))
                self.assertFalse(os.path.exists(os.path.join(d, "simv.msv", "interface_element.rpt")))
                r = self.simv(d, expect_rc=1)
                self.assertIn("the last compile into simv.daidir failed or was interrupted", r.stdout)
                self.assertNotIn("co-simulation", r.stdout)
                with open(os.path.join(d, "vamos_ams.raw"), "rb") as fh:
                    self.assertEqual(fh.read(), raw)
                self.assertEqual([f for f in os.listdir(d) if ".run." in f], [])
                # an early failure (a bad control-file command) too
                with open(os.path.join(d, "vcsAD.init"), "w") as fh:
                    fh.write("choose xa rc.sp; bogus_command x;\n")
                self.compile(d, "-sverilog", "tb.sv", engine=engine, expect_rc=1)
                r = self.simv(d, expect_rc=1)
                self.assertIn("failed or was interrupted", r.stdout)
                self.assertEqual([f for f in os.listdir(d) if ".run." in f], [])

    def test_analog_stop_beside_a_tran(self):
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.case("stop_" + engine, self.files())
                c = self.compile(d, "-sverilog", "tb.sv", "--vamos-analog-stop=300n", engine=engine)
                self.assertIn("vamos: warning: --vamos-analog-stop=300n has no effect: the netlist's .tran "
                              "sets the analog stop time (1e-06 s)", c.stdout)
                c = self.compile(d, "-sverilog", "tb.sv", "--vamos-analog-stop=300n", "--vamos-strict",
                                 engine=engine, expect_rc=1)
                self.assertIn("vamos: error: --vamos-strict: 1 unsupported/unknown option(s): "
                              "--vamos-analog-stop=300n", c.stdout)
                self.simv(d, expect_rc=1)

    def test_simv_compile_time_options_and_typos(self):
        engine = self.engines()[0]
        d = self.case("simvopts", self.files())
        self.compile(d, "-sverilog", "tb.sv", engine=engine)
        r = self.simv(d, "--vamos-analog=xyce")
        self.assertIn("vamos: warning: --vamos-analog=xyce has no effect: a compile-time option", r.stdout)
        r = self.simv(d, "--vamos-analog=xyce", "--vamos-strict", expect_rc=1)
        self.assertNotIn("co-simulation", r.stdout)
        r = self.simv(d, "--vamos-kep", expect_rc=2)
        self.assertIn("did you mean --vamos-keep?", r.stdout)
        c = self.compile(d, "-sverilog", "tb.sv", "--vamos-keep", engine=engine)
        self.assertIn("vamos: warning: --vamos-keep has no effect: a ./simv option", c.stdout)


if __name__ == "__main__":
    unittest.main()
