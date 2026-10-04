"""Translator fixes of the vcs-ams repair round (tgt-vhdl, bin/vvp-sv2ghdl), on the real stack.

- memories whose lowest index is not 0 ([4:7], [-2:1], [7:4]): every word address the core
  hands tgt-vhdl is canonical (0 = the lowest word), so the VHDL array is (count-1 downto 0);
  it kept the Verilog range, so a write vanished or hit the wrong word and a read gave x or
  stopped the run ("index 0 outside of INTEGER range 7 downto 4")
- signed / and %: a continuous signed divide or modulus (an LPM) was the unsigned logic3d
  operator (0xFFE5 / 0x0077 = 550, where Verilog gives 0: ivtest pr2722339a/b), and every
  signed % took the divisor's sign (VHDL mod) where Verilog takes the dividend's (rem);
  /= and %= on a signed target were unsigned too
- a comparison shown by $display (%d, %0d, %b, %h, a bare argument) printed "true"/"false"
  (Boolean'image) where Verilog prints 1/0
- disable and SV return: a disable was drawn as `null', so `return x' in a function, `disable
  <task>' and `disable <block>' were ignored and the statements after them ran; a disable of a
  scope that does not enclose it in its process is now a located error
- task automatic: IVL_ST_ALLOC/IVL_ST_FREE had no translation, so a module calling one was a
  deferred stub; an automatic task now works when one process calls it (each call starts its
  variables afresh, as vvp does); recursion is a located error (a call from a second process
  works on that process's own copy since round 6)
- bin/vvp-sv2ghdl (the ivtest gate's runner): libsv_math.so is loaded ($sqrt, $ln, $pow, $rtoi
  ... stopped the run: "foreign function sv_sqrt not found"); the resolver's Python half is
  found without PYTHONPATH (every run printed "resolver: ERROR - cannot import
  sv2vhdl_resolver" and ModuleNotFoundError, which broke every gold-file comparison); nvc's
  "   Function F [...] at design.vhd:N" trace after output printed in a function is dropped

    python3 -m unittest discover -s tests/vamos -p 'test_repair_translator.py' -v

Each SOURCE is translated once with bin/iverilog-sv2ghdl, run under nvc with bin/vvp-sv2ghdl
(PYTHONPATH removed, as the ivtest gate runs it) and, for reference, under vvp; lines starting
with "@ " are compared.  Linux (WSL) only: the stack is Linux ELF.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from typing import List

from vamos_testlib import ROOT, needs_stack

from vamos import tools  # noqa: E402

BIN = os.path.join(ROOT, "bin")


def _stack_env() -> dict:
    """NVC / NVC_LIBDIR / IVERILOG as NvcBackend gives them, and no PYTHONPATH: vvp-sv2ghdl
    must find the resolver's Python half itself (the ivtest gate sets none)."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("VAMOS_STACK", None)
    nvc = tools.find_real("nvc")
    iverilog = tools.find_real("iverilog")
    if nvc:
        env["NVC"] = nvc
        env["NVC_LIBDIR"] = tools.nvc_libdir(nvc)
    if iverilog:
        env["IVERILOG"] = iverilog
    return env


def _vvp() -> str:
    iverilog = tools.find_real("iverilog") or ""
    cand = os.path.join(os.path.dirname(os.path.realpath(iverilog)), "vvp")
    if os.access(cand, os.X_OK):
        return cand
    for c in (tools.find_real("vvp") or "", "/usr/local/src/iverilog/_install/bin/vvp"):
        if c and os.access(c, os.X_OK):
            return c
    return ""


def _run(cmd: List[str], cwd: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, errors="replace", timeout=600)


def tagged(text: str) -> List[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("@ ")]


class Translated(unittest.TestCase):
    """Translate SOURCE once per class; run it under nvc and vvp on demand."""

    SOURCE = ""
    TOP = "tb"
    EXPECT_TRANSLATION = True

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vamos-repair-")
        cls.env = _stack_env()
        cls.src = os.path.join(cls.tmp, "src.v")
        with open(cls.src, "w") as fh:
            fh.write(cls.SOURCE)
        cls.xlat = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "-g2012",
                         "-s", cls.TOP, cls.src], cls.tmp, cls.env)
        cls.outdir = os.path.join(cls.tmp, "vsim")
        for attr, name in (("vhdl", "design.vhd"), ("ivlog", "iverilog.log")):
            try:
                with open(os.path.join(cls.outdir, name), errors="replace") as fh:
                    setattr(cls, attr, fh.read())
            except OSError:
                setattr(cls, attr, "")
        cls._nvc = None
        cls._vvp = None

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("VAMOS_TEST_KEEP"):
            print("kept %s" % cls.tmp)
        else:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        if self.EXPECT_TRANSLATION:
            self.assertEqual(self.xlat.returncode, 0, self.xlat.stdout + self.xlat.stderr)
            self.assertNotIn("sv2vhdl:deferred", self.vhdl, self.ivlog)

    def nvc(self) -> subprocess.CompletedProcess:
        if self.__class__._nvc is None:
            self.__class__._nvc = _run([os.path.join(BIN, "vvp-sv2ghdl"), "vsim"], self.tmp, self.env)
        return self.__class__._nvc

    def vvp(self) -> subprocess.CompletedProcess:
        if self.__class__._vvp is None:
            vvp = _vvp()
            self.assertTrue(vvp, "no vvp")
            c = _run([self.env["IVERILOG"], "-g2012", "-s", self.TOP, "-o", "ref.vvp", self.src],
                     self.tmp, self.env)
            self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
            self.__class__._vvp = _run([vvp, "-n", "ref.vvp"], self.tmp, self.env)
        return self.__class__._vvp

    def assert_like_vvp(self):
        n, v = self.nvc(), self.vvp()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        want, got = tagged(v.stdout), tagged(n.stdout)
        self.assertTrue(want, v.stdout)
        self.assertEqual(got, want, "\n--- nvc:\n%s%s\n--- vvp:\n%s" % (n.stdout, n.stderr, v.stdout))


# ------------------------------------------------------------------- memories

@needs_stack
class TestNonzeroBaseMemories(Translated):
    SOURCE = """\
`timescale 1ns/1ps
module tb;
  reg [7:0] mc [4:7];
  reg [3:0] me [-2:1];
  reg [7:0] md [7:4];
  integer i;
  initial begin
    mc[4] = 8'h11; mc[5] = 8'h33; mc[6] = 8'h44; mc[7] = 8'h66;
    for (i = 4; i <= 7; i = i + 1) $display("@ mc[%0d]=%h", i, mc[i]);
    $display("@ mc5 %h", mc[5]);
    me[-2] = 1; me[-1] = 5; me[0] = 7; me[1] = 9;
    for (i = -2; i <= 1; i = i + 1) $display("@ me[%0d]=%0d", i, me[i]);
    md[7] = 8'hA7; md[4] = 8'hA4; md[5] = 8'hA5;
    $display("@ md %h %h %h %h", md[7], md[4], md[5], md[6]);
    $display("@ out of range %h %h", mc[3], me[2]);
    i = 6;
    mc[i] = mc[i] + 1;
    me[i - 7] = me[i - 7] + 1;
    $display("@ dynamic %h %0d", mc[i], me[-1]);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_declared_from_zero(self):
        self.assertRegex(self.vhdl, r"(?i)type\s+mc_Type\s+is\s+array\s*\(3 downto 0\)")
        self.assertRegex(self.vhdl, r"(?i)type\s+me_Type\s+is\s+array\s*\(3 downto 0\)")


# ------------------------------------------------------------- signed / and %

@needs_stack
class TestSignedDivMod(Translated):
    SOURCE = """\
`timescale 1ns/1ps
module tb;
  reg signed [15:0] a, b;
  wire signed [15:0] q = a / b;
  wire signed [15:0] r = a % b;
  wire [15:0] uq = a / b;
  reg signed [15:0] pq, pr;
  initial begin
    a = -27; b = 119; #1;
    $display("@ cont %0d %0d %0d", q, r, uq);
    pq = a / b; pr = a % b;
    $display("@ proc %0d %0d", pq, pr);
    a = -7; b = 3; #1;
    $display("@ cont2 %0d %0d", q, r);
    pq = a / b; pr = a % b;
    $display("@ proc2 %0d %0d", pq, pr);
    a = 7; b = -3; #1;
    $display("@ cont3 %0d %0d", q, r);
    pr = a; pr %= b; pq = a; pq /= b;
    $display("@ compressed %0d %0d", pq, pr);
    a = -7; b = -3; #1;
    $display("@ cont4 %0d %0d", q, r);
    b = 0; #1;
    $display("@ by zero %0d %0d", q, r);
    pq = a / b; pr = a % b;
    $display("@ by zero proc %0d %0d", pq, pr);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()


# ------------------------------------------------------------ comparisons shown

@needs_stack
class TestComparisonDisplay(Translated):
    SOURCE = """\
`timescale 1ns/1ps
module tb;
  reg [3:0] x, y;
  initial begin
    x = 3; y = 5;
    $display("@ cmp %0d %b %h %0d %d %0d", x != y, x == y, x < y, x === y, x > y, x !== y);
    $display("@ bare", x != y, " ", x >= y);
    $write("@ write %0d", x <= y);
    $display("");
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_never_boolean_image(self):
        out = "\n".join(tagged(self.nvc().stdout))
        self.assertNotRegex(out, r"(?i)\b(true|false)\b")


# ------------------------------------------------------------- disable / return

@needs_stack
class TestDisable(Translated):
    SOURCE = """\
`timescale 1ns/1ps
module tb;
  integer i, j, k, n;
  function integer f(input integer m);
    integer t;
    begin
      f = 0;
      for (t = 0; t < 10; t = t + 1) begin
        if (t == m) return t * 10;
        f = f + 1;
      end
    end
  endfunction
  function integer first_one(input [7:0] v);
    integer p;
    begin : search
      first_one = -1;
      p = 0;
      while (p < 8) begin
        if (v[p]) begin
          first_one = p;
          disable search;
        end
        p = p + 1;
      end
    end
  endfunction
  task tk(input integer m);
    begin : tbody
      if (m > 2) disable tk;
      $display("@ tk %0d", m);
    end
  endtask
  task stopper;
    disable main.outer_blk;
  endtask
  initial begin : main
    for (i = 0; i < 5; i = i + 1) begin : body
      if (i == 2) disable body;
      $display("@ loop %0d", i);
    end
    $display("@ f %0d %0d", f(3), f(20));
    $display("@ first_one %0d %0d %0d", first_one(8'b0010_0100), first_one(8'b0), first_one(8'h80));
    tk(1); tk(5); tk(2);
    j = 0;
    begin : blk
      for (k = 0; k < 10; k = k + 1) begin
        j = j + 1;
        if (k == 2) disable blk;
      end
      $display("@ not reached");
    end
    $display("@ j %0d", j);
    n = 0;
    begin : brk
      forever begin
        n = n + 1;
        #1;
        if (n == 3) disable brk;
      end
    end
    $display("@ n %0d at %0t", n, $time);
    begin : outer_blk
      $display("@ in outer");
      stopper;
      $display("@ not printed");
    end
    disable main;
    $display("@ after disable main");
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestDisableOfAnotherProcess(Translated):
    """A disable of a block of another process has no translation: a located error, never
    the silent `null' it was."""

    EXPECT_TRANSLATION = False
    SOURCE = """\
`timescale 1ns/1ps
module tb;
  initial begin : worker
    #10 $display("@ worker done");
  end
  initial begin
    #5 disable worker;
    $display("@ killer");
  end
endmodule
"""

    def test_located_error(self):
        self.assertIn("sv2vhdl:deferred", self.vhdl)
        self.assertRegex(self.ivlog, r"VHDL conversion error: \S+:7: disable tb\.worker has no VHDL "
                                     r"translation: only a block, task or function that encloses "
                                     r"the disable statement, in the same process, can be disabled")


# --------------------------------------------------------------- task automatic

@needs_stack
class TestAutomaticTask(Translated):
    SOURCE = """\
`timescale 1ns/1ps
module tb;
  integer r;
  bit [3:0] b;
  task automatic acc(input integer n, output integer s);
    integer k;
    integer z = 5;
    begin : body
      integer t;
      s = z;
      for (k = 0; k < n; k = k + 1) begin
        t = k * 2;
        s = s + t;
      end
    end
  endtask
  task automatic noout(input integer n, output integer o);
    if (n > 0) o = n;
  endtask
  task automatic twostate(input integer n, output bit [3:0] o);
    bit [3:0] l;
    if (n > 0) l = n;
    o = l;
  endtask
  initial begin
    acc(3, r);
    $display("@ acc3 %0d", r);
    acc(4, r);
    $display("@ acc4 %0d", r);
    noout(7, r);
    $display("@ noout7 %0d", r);
    noout(0, r);
    $display("@ noout0 %0d", r);
    twostate(9, b);
    $display("@ twostate9 %0d", b);
    twostate(0, b);
    $display("@ twostate0 %0d", b);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestAutomaticTaskTwoProcesses(Translated):
    """Round 6 (R6T-04): a call from a second process works on that process's own copy of
    the task's variables (it was a located error); see test_r6_T for overlapping calls."""
    SOURCE = """\
`timescale 1ns/1ps
module tb;
  integer r1, r2;
  task automatic dbl(input integer n, output integer o);
    #1 o = 2 * n;
  endtask
  initial begin
    dbl(3, r1);
    $display("@ p1 %0d", r1);
  end
  initial begin
    dbl(5, r2);
    $display("@ p2 %0d", r2);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestRecursiveTask(Translated):
    EXPECT_TRANSLATION = False
    SOURCE = """\
`timescale 1ns/1ps
module tb;
  integer r;
  task automatic fact(input integer n, output integer o);
    integer t;
    if (n <= 1) o = 1;
    else begin
      fact(n - 1, t);
      o = n * t;
    end
  endtask
  initial begin
    fact(5, r);
    $display("@ fact %0d", r);
  end
endmodule
"""

    def test_located_error(self):
        self.assertIn("sv2vhdl:deferred", self.vhdl)
        self.assertRegex(self.ivlog, r"VHDL conversion error: \S+:8: task tb\.fact calls itself "
                                     r"\(recursion\)")


# ---------------------------------------------------------------- vvp-sv2ghdl

@needs_stack
class TestVvpWrapper(Translated):
    """bin/vvp-sv2ghdl as the ivtest gate runs it (no PYTHONPATH)."""

    SOURCE = """\
`timescale 1ns/1ps
module tb;
  real x, y;
  integer i;
  function [7:0] chk(input [7:0] a);
    begin
      $display("@ in chk a=%0d", a);
      chk = a + 1;
    end
  endfunction
  initial begin
    $display("@ chk %0d", chk(8'd5));
    x = 2.0;
    #1 y = $sqrt(x);
    $display("@ sqrt %0.6f", y);
    $display("@ ln %0.6f pow %0.3f", $ln(x), $pow(x, 3.0));
    $display("@ rtoi %0d", $rtoi(x * 2.6));
    i = $random;
    $display("@ random %0d", i);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_no_resolver_import_error(self):
        out = self.nvc().stdout + self.nvc().stderr
        self.assertNotIn("cannot import sv2vhdl_resolver", out)
        self.assertNotIn("ModuleNotFoundError", out)

    def test_no_function_trace_lines(self):
        out = self.nvc().stdout
        self.assertNotRegex(out, r"(?m)^   Function ")


if __name__ == "__main__":
    unittest.main()
