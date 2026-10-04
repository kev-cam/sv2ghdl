"""Round-6 merge of the T, F and L translator patches (tgt-vhdl): what neither patch tested
alone.

Deposits  R6T-01 makes a process that suspends inside its body (an initial block, an always
          block with a wait in it) deposit (:=) its blocking writes and read them back at
          once, with no yield.  F's $readmemh/$readmemb store and L's $random(seed) /
          $urandom(seed) seed write-back came with their own test (initializing() ||
          was_deposited()) and registered a blocking target even when they deposited.  In
          the merge they use R6T-01's deposits_signal and register a blocking target only
          for a `<='.  With the patches' own test, a load or a draw after a delay in an
          initial block was read stale (m0=11 where vvp prints 55; the old seed), and in
          an always block the read yielded, so an observer ran in the middle of the block.
Loops     L draws a loop test that holds $random(seed) before every test
          (draw_while_drawn_test); T's break/continue are labelled loops opened by
          begin_loop_jumps.  In the merge the drawn-test loop carries T's labels (nvc
          analysis failed: "no visible declaration for SV_BRK_1"), and the loop test stays
          inside T's conditional-evaluation bracket.

    cd tests/vamos && python3 -m unittest test_r6_merge -v

Each class translates its source once with bin/iverilog-sv2ghdl (VAMOS_IVERILOG / IVERILOG
select the iverilog), runs it under nvc with bin/vvp-sv2ghdl and under vvp, and compares
the lines that start with "@ ".  Linux (WSL) only: the stack is Linux ELF; elsewhere the
classes skip.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from vamos_testlib import ROOT, needs_stack

from vamos import tools

BIN = os.path.join(ROOT, "bin")


def _stack_env():
    """The environment the vcs personality gives the translator and the run."""
    env = dict(os.environ)
    nvc = tools.find_real("nvc")
    iverilog = tools.find_real("iverilog")
    if nvc:
        env["NVC"] = nvc
        libdir = tools.nvc_libdir(nvc)
        env["NVC_LIBDIR"] = libdir
        prefix = os.path.dirname(os.path.dirname(os.path.realpath(nvc)))
        for pydir in (os.path.join(libdir, "sv2vhdl"),
                      os.path.join(os.path.dirname(prefix), "nvc", "lib", "sv2vhdl")):
            if os.path.isfile(os.path.join(pydir, "sv2vhdl_resolver.py")):
                env["PYTHONPATH"] = pydir + (os.pathsep + env["PYTHONPATH"]
                                             if env.get("PYTHONPATH") else "")
                break
    if iverilog:
        env["IVERILOG"] = iverilog
    return env


def _vvp():
    for c in (tools.find_real("vvp") or "", "/usr/local/src/iverilog/_install/bin/vvp"):
        if c and os.access(c, os.X_OK):
            return c
    return ""


def _run(cmd, cwd, env, timeout=600):
    return subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True,
                          errors="replace", timeout=timeout)


def tagged(text):
    """The `@ ...' lines of a run, in order."""
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("@ ")]


class Probe(unittest.TestCase):
    """Translate SOURCE once per class; run it under nvc and vvp."""

    SOURCE = ""           # the Verilog top file; @TMP@ is the run directory
    DATA = {}             # file name -> text: data files the run reads
    TOP = "tb"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vamos-r6merge-")
        cls.env = _stack_env()
        for name, text in cls.DATA.items():
            with open(os.path.join(cls.tmp, name), "w") as fh:
                fh.write(text)
        cls.src = os.path.join(cls.tmp, "src.v")
        with open(cls.src, "w") as fh:
            fh.write(cls.SOURCE.replace("@TMP@", cls.tmp))
        cls.xlat = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "-g2012",
                         "-s", cls.TOP, cls.src], cls.tmp, cls.env)
        cls.vhdl = ""
        try:
            with open(os.path.join(cls.tmp, "vsim", "design.vhd"), errors="replace") as fh:
                cls.vhdl = fh.read()
        except OSError:
            pass

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("VAMOS_TEST_KEEP"):
            print("kept %s" % cls.tmp)
        else:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def assert_like_vvp(self):
        self.assertEqual(self.xlat.returncode, 0,
                         "translation failed (iverilog %s):\n%s%s" % (
                             self.env.get("IVERILOG"), self.xlat.stdout, self.xlat.stderr))
        self.assertNotIn("sv2vhdl:deferred", self.vhdl)
        n = _run([os.path.join(BIN, "vvp-sv2ghdl"), "vsim"], self.tmp, self.env)
        vvp = _vvp()
        self.assertTrue(vvp, "no vvp")
        c = _run([self.env["IVERILOG"], "-g2012", "-s", self.TOP, "-o", "ref.vvp", self.src],
                 self.tmp, self.env)
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        v = _run([vvp, "-n", "ref.vvp"], self.tmp, self.env)
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        want, got = tagged(v.stdout), tagged(n.stdout)
        self.assertTrue(want, v.stdout)
        self.assertEqual(got, want, "\n--- nvc:\n%s%s\n--- vvp:\n%s"
                         % (n.stdout, n.stderr, v.stdout))


@needs_stack
class TestDepositedLoadAndDraw(Probe):
    """$readmemh and $random(seed) / $urandom(seed) in an always block that waits inside
    its body and in an initial block after a delay: each deposits, the next statement
    reads the new words and the advanced seed, and the observer runs once the writer
    suspends, as under vvp."""

    DATA = {"p5a.hex": "11 22 33 44\n", "p5b.hex": "55 66 77 88\n"}
    SOURCE = """\
`timescale 1ns/1ps
module tb;
  reg [7:0] mem [0:3];
  integer seed, v;
  reg go = 0;
  initial begin
    seed = 9;
    #1 go = 1;
    #20 $finish;
  end
  // an observer: Verilog runs it once the writer suspends
  always @(seed or mem[0]) $display("@ %0t obs seed=%0d m0=%h", $time, seed, mem[0]);
  // a process that waits inside its body
  always begin
    wait (go);
    $readmemh("@TMP@/p5a.hex", mem);
    $display("@ %0t w1 m0=%h m3=%h", $time, mem[0], mem[3]);
    v = $random(seed);
    $display("@ %0t w2 v=%0d seed=%0d", $time, v, seed);
    #5;
    go = 0;
    #1;
  end
  initial begin
    #10;
    $readmemh("@TMP@/p5b.hex", mem);
    $display("@ %0t i1 m0=%h", $time, mem[0]);
    v = $urandom(seed);
    $display("@ %0t i2 v=%0d seed=%0d", $time, v, seed);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestDrawnLoopTestWithJumps(Probe):
    """A for or while loop whose test draws $random(seed) / $urandom(seed) (drawn again
    before every test) with break and continue in its body, nested in a loop with jumps of
    its own."""

    SOURCE = """\
module tb;
  integer seed, a, b, i, n, k;
  initial begin
    seed = 5;
    a = $random(seed);
    b = seed;
    $display("@ a=%0d b=%0d", a, b);
    n = 0;
    for (i = 0; ($random(seed) % 4 != 0) && (i < 50); i = i + 1) begin
      if (i == 7) break;
      if (i % 2) continue;
      n = n + 1;
    end
    $display("@ for i=%0d n=%0d seed=%0d", i, n, seed);
    i = 0; n = 0;
    while ($random(seed) % 8 != 3) begin
      i = i + 1;
      if (i > 20) break;
      if (i % 3 == 0) continue;
      n = n + 1;
    end
    $display("@ while i=%0d n=%0d seed=%0d", i, n, seed);
    n = 0;
    for (k = 0; k < 4; k = k + 1) begin
      if (k == 1) continue;
      i = 0;
      while ($urandom(seed) % 5 != 0) begin
        i = i + 1;
        if (i > 9) break;
        if (i == 2) continue;
        n = n + i;
      end
      if (k == 3) break;
    end
    $display("@ nested k=%0d n=%0d seed=%0d", k, n, seed);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_labelled_exits(self):
        # the drawn-test loops are in their break wrappers
        self.assertRegex(self.vhdl, r"(?i)sv_brk_\d+\s*:\s*loop")
        self.assertRegex(self.vhdl, r"(?i)exit\s+sv_cont_\d+")


if __name__ == "__main__":
    unittest.main()
