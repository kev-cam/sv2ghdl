"""T4 (VAMOS_AMS_DESIGN.md §7): the translator scripts keep iverilog's messages.

    python3 -m unittest discover -s tests/vamos -p 'test_translator_scripts.py' -v

bin/sv2vhdl-modules and bin/iverilog-sv2ghdl write iverilog's output to
<outdir>/iverilog.log, next to design.vhd, as "=== <tool>: <run>" sections,
and report on stderr every module that ends as a deferred stub, with its
section.  These tests drive the two scripts with fake iverilog and nvc
executables (small bash scripts), so they need only bash, coreutils, perl and
python3, and run under Cygwin as well as Linux.
"""

from __future__ import annotations

import os
import subprocess
import unittest

from vamos_testlib import ROOT, TempDir

BIN = os.path.join(ROOT, "bin")

# -E copies the sources; -tvhdl -s <m> writes a minimal entity, except for a
# module named bad* (fails like tgt-vhdl does: a message on stdout, one on
# stderr, exit 1) or slow* (outlives any timeout); a whole-design -tvhdl run
# (no -s) fails; -tnull succeeds.
FAKE_IVERILOG = r"""#!/bin/bash
out=""; top=""; tgt=""; pre=0; srcs=()
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -s) top="$2"; shift 2 ;;
    -t) tgt="$2"; shift 2 ;;
    -t*) tgt="${1#-t}"; shift ;;
    -E) pre=1; shift ;;
    -*) shift ;;
    *) srcs+=("$1"); shift ;;
  esac
done
if [ $pre = 1 ]; then cat "${srcs[@]}" > "$out"; exit 0; fi
[ "$tgt" = null ] && exit 0
case "$top" in
  bad*) echo "VHDL conversion error: fake failure in $top"
        echo "error: Code generation had 1 error(s)." >&2
        exit 1 ;;
  slow*) exec sleep 60 ;;
  "") echo "fake: no whole-design translation" >&2; exit 1 ;;
esac
printf 'entity %s is\nend entity;\narchitecture from_verilog of %s is\nbegin\nend architecture;\n' \
  "$top" "$top" > "$out"
"""

FAKE_NVC = "#!/bin/bash\nexit 0\n"


class ScriptCase(TempDir):
    def setUp(self):
        super().setUp()
        self.fake_iverilog = self.write("fake/bin/iverilog", FAKE_IVERILOG)
        self.fake_nvc = self.write("fake/bin/nvc", FAKE_NVC)
        os.chmod(self.fake_iverilog, 0o755)
        os.chmod(self.fake_nvc, 0o755)
        os.makedirs(os.path.join(self.tmp, "fake", "lib", "ivl"))
        os.makedirs(os.path.join(self.tmp, "fake", "lib", "sv2vhdl"))

    def env(self, **extra):
        env = dict(os.environ)
        env.update({"IVERILOG": self.fake_iverilog, "NVC": self.fake_nvc,
                    "NVC_LIBDIR": os.path.join(self.tmp, "fake", "lib"),
                    "IVL_BUILD_LIB": os.path.join(self.tmp, "fake", "lib", "ivl")})
        env.update(extra)
        return env

    def run_script(self, name, args, **extra):
        return subprocess.run(["bash", os.path.join(BIN, name)] + list(args), cwd=self.tmp,
                              env=self.env(**extra), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, timeout=300)

    def sections(self, text):
        """{header: [lines]} of an iverilog.log."""
        out, cur = {}, None
        for line in text.splitlines():
            if line.startswith("=== "):
                cur = line
                out[cur] = []
            elif cur is not None:
                out[cur].append(line)
        return out


class TestSv2vhdlModules(ScriptCase):
    SRC = ("module good1;\nendmodule\n"
           "module bad1;\nendmodule\n")

    def test_log_next_to_output(self):
        self.write("src.sv", self.SRC)
        os.makedirs(os.path.join(self.tmp, "out"))
        r = self.run_script("sv2vhdl-modules", ["src.sv", "-o", "out/design.vhd"])
        self.assertEqual(r.returncode, 0, r.stderr)
        secs = self.sections(self.read("out/iverilog.log"))
        self.assertEqual(secs["=== sv2vhdl-modules: iverilog -tvhdl -s good1: translated"], [])
        self.assertEqual(secs["=== sv2vhdl-modules: iverilog -tvhdl -s bad1: deferred (iverilog exit 1)"],
                         ["VHDL conversion error: fake failure in bad1",
                          "error: Code generation had 1 error(s)."])
        # the deferred module is echoed, with what iverilog said
        self.assertIn("sv2vhdl-modules: module bad1: deferred (iverilog exit 1); iverilog said:\n"
                      "    VHDL conversion error: fake failure in bad1\n", r.stderr)
        self.assertNotIn("module good1:", r.stderr)
        vhd = self.read("out/design.vhd")
        self.assertIn("sv2vhdl:deferred source=src.sv module=bad1", vhd)
        self.assertIn("entity good1 is", vhd)

    def test_default_log_starts_afresh(self):
        self.write("src.sv", self.SRC)
        self.write("out/iverilog.log", "left over\n")
        r = self.run_script("sv2vhdl-modules", ["src.sv", "-o", "out/design.vhd"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("left over", self.read("out/iverilog.log"))

    def test_explicit_log_is_appended(self):
        self.write("src.sv", self.SRC)
        self.write("keep.log", "earlier section\n")
        r = self.run_script("sv2vhdl-modules", ["src.sv", "-o", "design.vhd", "--log", "keep.log"])
        self.assertEqual(r.returncode, 0, r.stderr)
        log = self.read("keep.log")
        self.assertTrue(log.startswith("earlier section\n"), log)
        self.assertIn("=== sv2vhdl-modules: iverilog -tvhdl -s bad1: deferred (iverilog exit 1)\n", log)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "iverilog.log")))

    def test_timeout_is_named(self):
        self.write("src.sv", "module slow1;\nendmodule\n")
        r = self.run_script("sv2vhdl-modules", ["src.sv", "-o", "design.vhd"], SV2VHDL_MOD_TIMEOUT="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("=== sv2vhdl-modules: iverilog -tvhdl -s slow1: deferred (timed out after 1 s)\n",
                      self.read("iverilog.log"))
        self.assertIn("module slow1: deferred (timed out after 1 s); iverilog said:\n    (nothing)\n",
                      r.stderr)


class TestIverilogSv2ghdl(ScriptCase):
    def test_deferred_module_is_reported(self):
        self.write("top.v", "module tb;\n  good1 g ();\n  bad1 b ();\nendmodule\n"
                            "module good1;\nendmodule\nmodule bad1;\nendmodule\n")
        r = self.run_script("iverilog-sv2ghdl", ["-o", "vsim", "-g2012", "top.v"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        log = self.read("vsim/iverilog.log")
        self.assertTrue(log.startswith("=== iverilog-sv2ghdl: iverilog -E (preprocess)\n"), log)
        secs = self.sections(log)
        self.assertIn("=== sv2vhdl-modules: iverilog -tvhdl -s good1: translated", secs)
        self.assertEqual(secs["=== sv2vhdl-modules: iverilog -tvhdl -s bad1: deferred (iverilog exit 1)"],
                         ["VHDL conversion error: fake failure in bad1",
                          "error: Code generation had 1 error(s)."])
        self.assertIn("sv2vhdl:deferred", self.read("vsim/design.vhd"))
        self.assertIn("iverilog-sv2ghdl: module bad1 was not translated (a deferred stub in design.vhd);"
                      " iverilog said:\n"
                      "    VHDL conversion error: fake failure in bad1\n"
                      "    error: Code generation had 1 error(s).\n", r.stderr)
        self.assertNotIn("module good1 was not translated", r.stderr)
        self.assertNotIn("module tb was not translated", r.stderr)

    def test_clean_design_reports_nothing(self):
        self.write("top.v", "module tb;\n  good1 g ();\nendmodule\nmodule good1;\nendmodule\n")
        r = self.run_script("iverilog-sv2ghdl", ["-o", "vsim", "-g2012", "top.v"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("was not translated", r.stderr)
        self.assertNotIn("deferred", self.read("vsim/iverilog.log"))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "vsim", "design.vhd")))

    def test_log_is_fresh_per_translation(self):
        self.write("top.v", "module tb;\nendmodule\n")
        self.run_script("iverilog-sv2ghdl", ["-o", "vsim", "top.v"])
        r = self.run_script("iverilog-sv2ghdl", ["-o", "vsim", "top.v"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.read("vsim/iverilog.log").count("iverilog -E (preprocess)"), 1)


if __name__ == "__main__":
    unittest.main()
