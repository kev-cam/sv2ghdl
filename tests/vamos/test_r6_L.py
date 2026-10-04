"""Round-6 group L (nvc lib/sv2vhdl + tgt-vhdl), on the real stack:

- R6L-01  $random(seed) draws vvp's sequence, rtl_dist_uniform(&seed, INT32_MIN, INT32_MAX):
          the value and the seed left behind differ (sv_math_pkg sv_random_value /
          sv_random_next; it was a glibc-constant LCG whose value was the new seed).  1000-draw
          sequences for six seeds as a right-hand side and inside an expression, two calls in
          one statement, called as a task, in an if and a while condition (drawn before every
          test), in a for loop's step, a non-blocking assignment, an always block, 64-bit and
          time seeds, a function's input; a seed narrower than 32 bits is a located error, as
          in vvp.
- R6L-02  $urandom has a seed of its own, apart from $random's (vvp's second generator);
          $urandom(seed) draws from the caller's seed, advances it, and leaves the advanced seed
          in the $urandom generator (it was replaced by 0); $urandom_range draws vvp's numbers
          from that generator (sv_urandom_range, vvp's urandom(max, min); it was
          lo + draw mod span); thousands of draws in one process activation run (the vector
          xor that offset a $urandom draw crashed nvc).
- R6L-03  a vector variable copied from a weak net stores no strength, and a vector continuous
          assignment of a net drives its own: l3d_strengthen on vectors (the weak codes lost to
          a strong driver of the net the variable drove); one of a variable is left alone.
- R6L-04  a tran on a bit-select joins the vector element both ways (the core temporary is an
          alias of the element, the element is the switch's actual: no "connected one way
          only"); two tri-state nets joined by a tran resolve (the kernel net solver deadlocked
          on the nets' initial X: a member's own drive moved without an event -- this needs
          nvc's src/rt/model.c stitch hook); a tranif's x or z control gives
          x; a tran on a select of a module port is still one way, warned, and --vamos-strict
          makes that an error.
- R6L-05  the resolver plugin leaves SIGINT to nvc (Py_InitializeEx(0)) and writes its errors
          on stderr.
- R6L-06  sv2vhdl.logic3d_types_pkg's l3d_mod_s is Verilog's % (the dividend's sign; all x for
          a zero divisor), as tgt-vhdl's Verilog_Rem_S.

    python3 -m unittest discover -s tests/vamos -p 'test_r6_L.py' -v

The Verilog of each class is translated once with bin/iverilog-sv2ghdl (VAMOS_IVERILOG / IVERILOG
select the iverilog, VAMOS_NVC / NVC the nvc), run under nvc with bin/vvp-sv2ghdl and, for
reference, under vvp; lines starting with "@ " are compared.  Linux (WSL) only: the stack is
Linux ELF.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from typing import List

from vamos_testlib import ROOT, TempDir, needs_stack, run

from vamos import tools  # noqa: E402

BIN = os.path.join(ROOT, "bin")
SHIMS = os.path.join(ROOT, "shims")


def _resolver_pydir(nvc: str, libdir: str) -> str:
    prefix = os.path.dirname(os.path.dirname(os.path.realpath(nvc)))
    for pydir in (os.path.join(libdir, "sv2vhdl"),
                  os.path.join(os.path.dirname(prefix), "nvc", "lib", "sv2vhdl")):
        if os.path.isfile(os.path.join(pydir, "sv2vhdl_resolver.py")):
            return pydir
    return ""


def _stack_env() -> dict:
    """The environment NvcBackend gives the translator and the run."""
    env = dict(os.environ)
    nvc = tools.find_real("nvc")
    iverilog = tools.find_real("iverilog")
    if nvc:
        env["NVC"] = nvc
        libdir = tools.nvc_libdir(nvc)
        env["NVC_LIBDIR"] = libdir
        pydir = _resolver_pydir(nvc, libdir)
        if pydir:
            env["PYTHONPATH"] = pydir + (os.pathsep + env["PYTHONPATH"]
                                         if env.get("PYTHONPATH") else "")
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
                          universal_newlines=True, errors="replace", timeout=900)


def tagged(text: str) -> List[str]:
    """The `@ ...' lines of a run, in order."""
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("@ ")]


class Translated(unittest.TestCase):
    """Translate the inline SOURCE once per class; run under nvc and vvp on demand."""

    SOURCE = ""
    TOP = "tb"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vamos-r6l-")
        cls.env = _stack_env()
        cls.src = os.path.join(cls.tmp, "src.v")
        with open(cls.src, "w") as fh:
            fh.write(cls.SOURCE)
        cls.xlat = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "-g2012",
                         "-s", cls.TOP, cls.src], cls.tmp, cls.env)
        cls.outdir = os.path.join(cls.tmp, "vsim")
        cls.vhdl = cls._read(os.path.join(cls.outdir, "design.vhd"))
        cls.ivlog = cls._read(os.path.join(cls.outdir, "iverilog.log"))
        cls._nvc = None
        cls._vvp = None

    @staticmethod
    def _read(path: str) -> str:
        try:
            with open(path, errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("VAMOS_TEST_KEEP"):
            print("kept %s" % cls.tmp)
        else:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.assertEqual(self.xlat.returncode, 0, self.xlat.stdout + self.xlat.stderr)
        self.assertNotIn("sv2vhdl:deferred", self.vhdl, self.ivlog)

    def nvc(self) -> subprocess.CompletedProcess:
        if self.__class__._nvc is None:
            self.__class__._nvc = _run([os.path.join(BIN, "vvp-sv2ghdl"), "vsim"], self.tmp,
                                       self.env)
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

    def assert_like_vvp(self, prefix: str = "@ "):
        n, v = self.nvc(), self.vvp()
        self.assertEqual(n.returncode, 0, n.stdout[-3000:] + n.stderr[-3000:])
        self.assertEqual(v.returncode, 0, v.stdout[-3000:] + v.stderr[-3000:])
        want = [w for w in tagged(v.stdout) if w.startswith(prefix)]
        got = [g for g in tagged(n.stdout) if g.startswith(prefix)]
        self.assertTrue(want, v.stdout[-3000:])
        if got != want:
            first = next((k for k in range(min(len(got), len(want))) if got[k] != want[k]),
                         min(len(got), len(want)))
            self.fail("%s: %d lines from nvc, %d from vvp; first difference at line %d:\n"
                      "  nvc: %s\n  vvp: %s\n%s" % (prefix, len(got), len(want), first,
                                                    got[first] if first < len(got) else "<none>",
                                                    want[first] if first < len(want) else "<none>",
                                                    n.stderr[-2000:]))

    def lines(self, pattern: str) -> List[str]:
        return [ln.strip() for ln in self.vhdl.splitlines() if re.search(pattern, ln)]


# ---------------------------------------------------------------------- R6L-01 / R6L-02

@needs_stack
class TestSeededRandom(Translated):
    """$random(seed) in every place a call can stand, against vvp, draw by draw.  (One draw
    per time step: the translation spends a delta on a read after a blocking assignment.)"""

    SOURCE = """\
module tb;
  integer seed, k, j, r, ones, acc, n;
  integer seeds [0:5];
  reg [63:0] w64;
  reg [63:0] s64;
  time st;
  reg clk = 0;
  integer aseed = 99, ar;
  function integer first_draw(input integer s);
    first_draw = $random(s) ^ s;
  endfunction
  always @(posedge clk) begin
    ar = $random(aseed);
    $display("@ I %0d %0d", ar, aseed);
  end
  initial begin
    seeds[0] = 0; seeds[1] = 1; seeds[2] = -1;
    seeds[3] = 32'h7fffffff; seeds[4] = 32'h80000000; seeds[5] = 12345;
    seed = 5;
    r = $random(seed);
    $display("@ S %0d %0d", r, seed);
    for (j = 0; j < 6; j = j + 1) begin
      seed = seeds[j];
      for (k = 0; k < 1000; k = k + 1) begin
        #1 r = $random(seed);
        $display("@ A %0d %0d %0d %0d", j, k, r, seed);
      end
      seed = seeds[j];
      for (k = 0; k < 1000; k = k + 1) begin
        #1 r = $random(seed) % 1000 + seed;
        $display("@ B %0d %0d %0d %0d", j, k, r, seed);
      end
      seed = seeds[j];
      for (k = 0; k < 200; k = k + 1) begin
        #1 w64 = {$random(seed), $random(seed)};
        $display("@ C %0d %0d %h %0d", j, k, w64, seed);
      end
      seed = seeds[j];
      for (k = 0; k < 200; k = k + 1) begin
        #1 $random(seed);
        $display("@ D %0d %0d %0d", j, k, seed);
      end
      seed = seeds[j]; ones = 0;
      for (k = 0; k < 200; k = k + 1)
        #1 if ($random(seed) & 1) ones = ones + 1;
      $display("@ E %0d %0d %0d", j, ones, seed);
      seed = seeds[j]; acc = 0;
      while (($random(seed) & 7) != 0) #1 acc = acc + 1;
      $display("@ W %0d %0d %0d", j, acc, seed);
      seed = seeds[j]; n = 0;
      for (k = 0; k < 40; k = k + 1 + ($random(seed) & 3))
        #1 n = n + 1;
      $display("@ P %0d %0d %0d %0d", j, n, k, seed);
    end
    s64 = 64'hFFFF_FFFF_8000_0003; st = 64'd77;
    for (k = 0; k < 50; k = k + 1) begin
      #1 r = $random(s64); j = $random(st);
      $display("@ J %0d %0d %h %0d %h", k, r, s64, j, st);
    end
    for (k = 0; k < 100; k = k + 1) begin
      seed = 1000 + k;
      r <= $random(seed);
      #1 $display("@ F %0d %0d %0d", k, r, seed);
    end
    for (k = 0; k < 20; k = k + 1) begin
      #1 r = first_draw(k * 7919 - 3);
      $display("@ U %0d %0d", k, r);
    end
    repeat (120) #5 clk = ~clk;
    $finish;
  end
endmodule
"""

    def test_first_draw(self):
        # vvp: $random(seed) from seed = 5 gives -2147138048 and leaves 69069 * 5 + 1
        self.assertIn("@ S -2147138048 345346", tagged(self.nvc().stdout))

    def test_as_right_hand_side(self):
        self.assert_like_vvp("@ A ")

    def test_inside_an_expression(self):
        self.assert_like_vvp("@ B ")

    def test_two_calls_in_one_statement(self):
        self.assert_like_vvp("@ C ")

    def test_called_as_a_task(self):
        self.assert_like_vvp("@ D ")

    def test_in_if_and_while_conditions(self):
        self.assert_like_vvp("@ E ")
        self.assert_like_vvp("@ W ")

    def test_in_a_for_step(self):
        # the step's draw is made on every pass (it went ahead of the loop, once)
        self.assert_like_vvp("@ P ")

    def test_wide_and_time_seeds(self):
        self.assert_like_vvp("@ J ")

    def test_nonblocking_assignment(self):
        self.assert_like_vvp("@ F ")

    def test_function_input_seed(self):
        # a function's input is a constant in VHDL: the draw advances a shadow of it
        self.assert_like_vvp("@ U ")
        self.assertTrue(self.lines(r"variable s_Shadow : logic3d_vector\(31 downto 0\) := s;"),
                        self.vhdl)

    def test_always_block(self):
        self.assert_like_vvp("@ I ")

    def test_vhdl(self):
        self.assertTrue(self.lines(r":= sv_random_value\(seed\);"), self.vhdl)
        self.assertTrue(self.lines(r"seed := sv_random_next\(seed\);"), self.vhdl)
        self.assertFalse(self.lines(r"\bsv_random\("), self.vhdl)
        self.assertNotIn("Unsupported system task $random", self.vhdl)


@needs_stack
class TestURandomSeeds(Translated):
    """$urandom keeps a seed of its own; $urandom(seed) draws from the caller's seed and leaves
    the advanced seed in the $urandom generator, as vvp's urandom() does."""

    SOURCE = """\
module tb;
  integer k, j, r, u, useed;
  integer seeds [0:2];
  initial begin
    for (k = 0; k < 200; k = k + 1) begin
      #1 r = $random; u = $urandom;
      $display("@ H %0d %0d %0d", k, r, u);
    end
    seeds[0] = 0; seeds[1] = -1; seeds[2] = 77;
    for (j = 0; j < 3; j = j + 1) begin
      useed = seeds[j];
      for (k = 0; k < 300; k = k + 1) begin
        #1 u = $urandom(useed);
        $display("@ G %0d %0d %0d %0d", j, k, u, useed);
      end
      u = $urandom;
      $display("@ N %0d %0d", j, u);
      $urandom(useed);
      $display("@ T %0d %0d", j, useed);
    end
    for (k = 0; k < 50; k = k + 1) begin
      #1 u = $urandom_range(7, 1); r = $random;
      $display("@ R %0d %0d %0d", k, u, r);
    end
    $finish;
  end
endmodule
"""

    def test_own_seed(self):
        self.assert_like_vvp("@ H ")

    def test_seeded_sequence(self):
        self.assert_like_vvp("@ G ")

    def test_generator_keeps_the_advanced_seed(self):
        self.assert_like_vvp("@ N ")

    def test_called_as_a_task(self):
        self.assert_like_vvp("@ T ")

    def test_urandom_range_leaves_random_alone(self):
        self.assert_like_vvp("@ R ")

    def test_no_constant_stand_in(self):
        self.assertNotIn("Unsupported system function $urandom", self.vhdl)
        self.assertTrue(self.lines(r":= sv_urandom_value\(useed\);"), self.vhdl)
        self.assertTrue(self.lines(r"sv_urandom_seed\(useed\);"), self.vhdl)


@needs_stack
class TestURandomRange(Translated):
    """$urandom_range(max, min) gives vvp's numbers for bounds at every edge -- in order,
    swapped, equal (no draw), the full 32-bit range, near it, across 2**31 -- by every call
    form, on the $urandom generator ($urandom and $random interleaved stay vvp's too)."""

    SOURCE = """\
module tb;
  function integer burst(input integer n, input [31:0] mx, input [31:0] mn, input integer which);
    integer i, acc;
    reg [31:0] v;
    begin
      acc = 0;
      for (i = 0; i < n; i = i + 1) begin
        case (which)
          0: v = $urandom_range(mx, mn);
          1: v = $urandom_range(mx);
          default: v = $urandom_range(mn, mx);
        endcase
        acc = acc * 31 + v;
      end
      burst = acc;
    end
  endfunction
  reg [31:0] mx [0:13];
  reg [31:0] mn [0:13];
  integer k, w, r, u;
  reg [31:0] v;
  reg [7:0] narrow;
  initial begin
    mx[0] = 7;            mn[0] = 1;
    mx[1] = 1;            mn[1] = 7;
    mx[2] = 5;            mn[2] = 5;
    mx[3] = 0;            mn[3] = 0;
    mx[4] = 32'hFFFFFFFF; mn[4] = 0;
    mx[5] = 32'hFFFFFFFF; mn[5] = 1;
    mx[6] = 32'hFFFFFFFE; mn[6] = 0;
    mx[7] = 32'h7FFFFFFF; mn[7] = 32'h80000000;
    mx[8] = 32'h80000000; mn[8] = 32'h7FFFFFFF;
    mx[9] = 1000000;      mn[9] = 3;
    mx[10] = 32'hFFFFFFFF; mn[10] = 32'hFFFFFFF0;
    mx[11] = 32'h0000FFFF; mn[11] = 32'hFFFF0000;
    mx[12] = 1;           mn[12] = 0;
    mx[13] = 32'hC0000000; mn[13] = 32'h40000000;
    for (k = 0; k < 14; k = k + 1)
      for (w = 0; w < 3; w = w + 1) begin
        #1 v = burst(40, mx[k], mn[k], w);
        u = $urandom;
        r = $random;
        $display("@ R %0d %0d %h %0d %0d", k, w, v, u, r);
      end
    for (k = 0; k < 30; k = k + 1) begin
      #1 v = $urandom_range(k * 1000 + 17, k);
      narrow = $urandom_range(300, 2);
      $display("@ S %0d %0d %0d", k, v, narrow);
    end
    $finish;
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp("@ R ")
        self.assert_like_vvp("@ S ")

    def test_vhdl(self):
        self.assertTrue(self.lines(r"\bsv_urandom_range\("), self.vhdl)
        self.assertFalse(self.lines(r'"xor"'), self.vhdl)


@needs_stack
class TestRandomLongLoops(Translated):
    """Thousands of draws in one process activation (loops inside functions): $urandom,
    $urandom(seed), $random(seed) and $urandom_range.  The translation offset a $urandom draw
    with numeric_std's "xor", and nvc crashed in it (SIGSEGV in ieee_xor_vector_sse41) after
    some thousands of calls; the library now offsets in integer arithmetic."""

    SOURCE = """\
module tb;
  function integer fu(input integer n);
    integer i, acc;
    begin
      acc = 0;
      for (i = 0; i < n; i = i + 1)
        acc = acc ^ $urandom;
      fu = acc;
    end
  endfunction
  function integer fs(input integer n, input integer s0);
    integer i, acc, s;
    begin
      acc = 0; s = s0;
      for (i = 0; i < n; i = i + 1)
        acc = acc ^ $urandom(s);
      fs = acc ^ s;
    end
  endfunction
  function integer fr(input integer n, input integer s0);
    integer i, acc, s;
    begin
      acc = 0; s = s0;
      for (i = 0; i < n; i = i + 1)
        acc = acc + $random(s);
      fr = acc ^ s;
    end
  endfunction
  function integer fg(input integer n);
    integer i, acc;
    begin
      acc = 0;
      for (i = 0; i < n; i = i + 1)
        acc = acc + $urandom_range(1000, 10);
      fg = acc;
    end
  endfunction
  integer a;
  initial begin
    a = fu(5000);
    $display("@ fu %0d", a);
    a = fs(5000, 9);
    $display("@ fs %0d", a);
    a = fr(5000, 11);
    $display("@ fr %0d", a);
    a = fg(5000);
    $display("@ fg %0d", a);
    a = $urandom;
    $display("@ after %0d", a);
    $finish;
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestSeededRandomCaveats(Translated):
    """A seeded call Verilog may skip (a ?: branch, && / ||) and a read of the seed ahead of the
    call in the same statement are translated approximately: each says so, located, once."""

    SOURCE = """\
module tb;
  integer s, r, c, k;
  initial begin
    s = 5; c = 0;
    r = c ? $random(s) : 7;
    s = 5;
    r = s + $random(s);
    s = 5;
    r = $random(s) + s;
    for (k = 0; k < 3; k = k + 1)
      r = (c > 0) && ($random(s) > 0);
    $display("@ done %0d", r);
  end
endmodule
"""

    def test_located_warnings(self):
        self.assertRegex(self.ivlog, r"Warning: \$random\(s\) at \S+:5 is not translated "
                                     r"faithfully: Verilog evaluates it only when its \?:, && or "
                                     r"\|\| operand is taken")
        self.assertRegex(self.ivlog, r"Warning: \$random\(s\) at \S+:7 is not translated "
                                     r"faithfully: the statement reads s ahead of the call")
        self.assertNotRegex(self.ivlog, r"\$random\(s\) at \S+:9 ")
        # once, though draw_while draws the loop body twice
        self.assertEqual(len(re.findall(r"Warning: \$random\(s\) at \S+:11 ", self.ivlog)), 1,
                         self.ivlog)

    def test_runs(self):
        self.assert_like_vvp("@ done")


@needs_stack
class TestSeedErrors(TempDir):
    """A seed vvp refuses is a located translation error (it was translated silently): one
    narrower than 32 bits, and a net."""

    def xlat(self, body: str) -> str:
        src = self.write("src.v", "module tb;\n%s\nendmodule\n" % body)
        c = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "-g2012", "-s", "tb",
                  src], self.tmp, _stack_env())
        with open(os.path.join(self.tmp, "vsim", "iverilog.log"), errors="replace") as fh:
            return c.stdout + c.stderr + fh.read()

    def test_narrow_seed(self):
        log = self.xlat("  reg [15:0] s16;\n  integer r;\n  initial begin\n    s16 = 3;\n"
                        "    r = $random(s16);\n  end")
        self.assertRegex(log, r"src\.v:6: \$random's seed variable is less than 32 bits \(16\)"
                         .replace("src\\.v", r"\S+"))

    def test_net_seed(self):
        log = self.xlat("  wire [31:0] w = 7;\n  integer r;\n  initial r = $urandom(w);")
        self.assertRegex(log, r"\S+:4: \$urandom's seed must be an integer/time variable or a "
                              r"register")


# ---------------------------------------------------------------------- R6L-03

@needs_stack
class TestVectorStrengthen(Translated):
    """A vector variable copied from a weak net, and a vector continuous assignment of one,
    drive strong values (a strong driver on the same net makes x, as in vvp)."""

    SOURCE = """\
module tb;
  wire [3:0] pads;
  pullup p0(pads[0]); pullup p1(pads[1]); pulldown p2(pads[2]); pulldown p3(pads[3]);
  reg [3:0] data;
  wire [3:0] bus;
  assign bus = data;
  assign bus = 4'b1100;
  wire [3:0] cpy;
  assign cpy = pads;
  wire [3:0] cbus;
  assign cbus = cpy;
  assign cbus = 4'b1010;
  reg [7:0] mem [0:1];
  wire [7:0] mbus;
  assign mbus = mem[0];
  assign mbus = 8'h0f;
  tri1 [3:0] tb;
  reg [3:0] q;
  wire [3:0] y;
  assign y = q;
  assign y = 4'b0000;
  initial begin
    #1 data = pads;
    mem[0] = {pads, pads};
    q = tb;
    #1 $display("@ data=%b bus=%b cpy=%b cbus=%b mbus=%b q=%b y=%b",
                data, bus, cpy, cbus, mbus, q, y);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()
        self.assertIn("@ data=0011 bus=xxxx cpy=0011 cbus=x01x mbus=00xxxx11 q=1111 y=xxxx",
                      tagged(self.nvc().stdout))

    def test_vhdl(self):
        self.assertTrue(self.lines(r"\bdata := l3d_strengthen\(pads\);"), self.vhdl)
        # (the continuous assignment may sit in a fused comb process: `:=')
        self.assertTrue(self.lines(r"\bcpy (<=|:=) l3d_strengthen\(pads\);"), self.vhdl)
        # a variable stores no strength: its continuous assignment is left alone
        self.assertTrue(self.lines(r"\by (<=|:=) q;"), self.vhdl)


@needs_stack
class TestVectorStrengthenRegAndPull(Translated):
    """A net that a module's output reg and pulls drive together (the reg's z bits take the
    pulls' weak 1s): a vector continuous assignment of it drives strong values, so the strong
    driver on its target makes x where they differ (it lost to them: z=01xx)."""

    SOURCE = """\
module child(output reg [3:0] q);
  initial q = 4'bzz10;
endmodule
module tb;
  wire [3:0] y;
  pullup p3 (y[3]); pullup p2 (y[2]);
  child c (.q(y));
  wire [3:0] z;
  assign z = y;
  assign z = 4'b0101;
  initial #1 $display("@ y=%b z=%b", y, z);
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()
        self.assertIn("@ y=1110 z=x1xx", tagged(self.nvc().stdout))


# ---------------------------------------------------------------------- R6L-04

@needs_stack
class TestTranTristateNets(Translated):
    """Two tri-state nets joined by a tran, and a chain of two trans: each net gets the other's
    value (both stayed x: the kernel solver never saw a member's own drive move; this needs
    the src/rt/model.c stitch hook)."""

    SOURCE = """\
module tb;
  wire b2, w;
  reg e1, v1, wen, wv;
  assign b2 = e1 ? v1 : 1'bz;
  assign w = wen ? wv : 1'bz;
  tran t1(b2, w);
  wire a, b, c;
  reg ea, va, ec, vc;
  assign a = ea ? va : 1'bz;
  assign c = ec ? vc : 1'bz;
  tran t2(a, b);
  tran t3(b, c);
  initial begin
    e1 = 1; v1 = 0; wen = 0; wv = 0;
    ea = 1; va = 1; ec = 0; vc = 0;
    #1 $display("@ 1 b2=%b w=%b a=%b b=%b c=%b", b2, w, a, b, c);
    e1 = 0; wen = 1; wv = 1;
    ec = 1; vc = 0;
    #1 $display("@ 2 b2=%b w=%b a=%b b=%b c=%b", b2, w, a, b, c);
    wv = 0; ea = 0;
    #1 $display("@ 3 b2=%b w=%b a=%b b=%b c=%b", b2, w, a, b, c);
    wen = 0; ec = 0;
    #1 $display("@ 4 b2=%b w=%b a=%b b=%b c=%b", b2, w, a, b, c);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestTranOnBitSelect(Translated):
    """A tran on a bit-select: the element and the other net are joined both ways (the
    vector bit stayed z; only a one-way copy of it reached the switch)."""

    SOURCE = """\
module tb;
  wire [3:0] bus; wire w;
  reg ext_en, wen, wv; reg [3:0] ext;
  assign bus = ext_en ? ext : 4'bzzzz;
  assign w = wen ? wv : 1'bz;
  tran t1(bus[2], w);
  wire [3:0] lo; wire w2;
  reg wv2;
  assign lo[1:0] = 2'b01;
  assign lo[3] = 1'b1;
  assign w2 = wv2;
  tran t2(lo[2], w2);
  wire [3:0] v4;
  reg e0, x0, e3, x3;
  assign v4[0] = e0 ? x0 : 1'bz;
  assign v4[3] = e3 ? x3 : 1'bz;
  tran t3(v4[0], v4[1]);
  tran t4(v4[3], v4[2]);
  wire [1:0] pb; wire p, q;
  reg ep, vp, eq, vq;
  assign p = ep ? vp : 1'bz;
  assign q = eq ? vq : 1'bz;
  tran t5(pb[1], p);
  tran t6(pb[1], q);
  reg en; wire [3:0] g; wire h; reg he, hv;
  assign g = 4'bzzzz;
  assign h = he ? hv : 1'bz;
  tranif1 t7(g[1], h, en);
  initial begin
    ext_en = 1; ext = 4'b1010; wen = 0; wv = 0; wv2 = 0;
    e0 = 1; x0 = 1; e3 = 0; x3 = 0; ep = 1; vp = 1; eq = 0; vq = 0;
    en = 1; he = 1; hv = 0;
    #1 $display("@ 1 bus=%b w=%b lo=%b w2=%b v4=%b pb=%b p=%b q=%b g=%b", bus, w, lo, w2, v4, pb, p, q, g);
    ext_en = 0; wen = 1; wv = 1; wv2 = 1;
    e0 = 0; e3 = 1; x3 = 0; ep = 0; eq = 1;
    en = 1'bx;
    #1 $display("@ 2 bus=%b w=%b lo=%b w2=%b v4=%b pb=%b p=%b q=%b g=%b", bus, w, lo, w2, v4, pb, p, q, g);
    wv = 0; e0 = 1; x0 = 0; x3 = 1; ep = 1; vp = 1; vq = 0;
    en = 0;
    #1 $display("@ 3 bus=%b w=%b lo=%b w2=%b v4=%b pb=%b p=%b q=%b g=%b", bus, w, lo, w2, v4, pb, p, q, g);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_joined_not_copied(self):
        self.assertNotIn("connected one way only", self.ivlog)
        self.assertTrue(self.lines(r"^\s*alias tmp_\w+ is bus_sig\(2\);"), self.vhdl)
        self.assertTrue(self.lines(r"^\s*a => bus_sig\(2\),"), self.vhdl)
        self.assertFalse(self.lines(r"<= bus_sig\(2\);"), self.vhdl)


@needs_stack
class TestSwitchArrays(Translated):
    """Arrays of switches -- on whole vectors, with a scalar enable, on part-selects, with a
    vector enable: iverilog hands the target one switch as wide as the array, which was drawn
    as one instance whose scalar ports took the vectors (no switch at all, or nvc stopped at
    elaboration: "type kind T_ENUM does not have item I_ELEM"); now one instance per bit."""

    SOURCE = """\
module tb;
  wire [3:0] a, b, c, d, e, f;
  wire [5:0] g, h;
  reg [3:0] av, bv, ae, be, cv, dv, ce, de, ev, fv, ee, fe, env;
  reg [5:0] gv, hv, ge, he;
  reg en;
  genvar i;
  generate for (i = 0; i < 4; i = i + 1) begin : g4
    assign a[i] = ae[i] ? av[i] : 1'bz;
    assign b[i] = be[i] ? bv[i] : 1'bz;
    assign c[i] = ce[i] ? cv[i] : 1'bz;
    assign d[i] = de[i] ? dv[i] : 1'bz;
    assign e[i] = ee[i] ? ev[i] : 1'bz;
    assign f[i] = fe[i] ? fv[i] : 1'bz;
  end endgenerate
  generate for (i = 0; i < 6; i = i + 1) begin : g6
    assign g[i] = ge[i] ? gv[i] : 1'bz;
    assign h[i] = he[i] ? hv[i] : 1'bz;
  end endgenerate
  tran ta[3:0] (a, b);
  tranif1 tb[3:0] (c, d, en);
  tranif0 tc[3:0] (e, f, env);
  rtran td[1:0] (g[2:1], h[4:3]);
  initial begin
    ae = 4'b0101; av = 4'b0001; be = 4'b1010; bv = 4'b1000;
    ce = 4'b0011; cv = 4'b0010; de = 4'b1100; dv = 4'b0100; en = 1;
    ee = 4'b1001; ev = 4'b1000; fe = 4'b0110; fv = 4'b0010; env = 4'b0101;
    ge = 6'b000110; gv = 6'b000100; he = 6'b100001; hv = 6'b000001;
    #1 $display("@ v1 a=%b b=%b c=%b d=%b g=%b h=%b", a, b, c, d, g, h);
    $display("@ e1 e=%b f=%b", e, f);
    ae = 0; be = 4'b1111; bv = 4'b0110; en = 0; env = 4'b1010;
    ge = 0; he = 6'b011000; hv = 6'b010000;
    #1 $display("@ v2 a=%b b=%b c=%b d=%b g=%b h=%b", a, b, c, d, g, h);
    $display("@ e2 e=%b f=%b", e, f);
    en = 1'bx; env = 4'b0x1z;
    #1 $display("@ v3 a=%b b=%b c=%b d=%b g=%b h=%b", a, b, c, d, g, h);
    $display("@ e3 e=%b f=%b", e, f);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp("@ v")

    def test_vector_enable_bit_by_bit(self):
        # IEEE 1364 7.1.6: instance k of tc[3:0] gets env[k] (tranif0: on where it is 0; x or
        # z gives x).  vvp switches every bit on env[0] (one wide switch, one enable bit), so
        # these are the standard's values, worked by hand.
        n = self.nvc()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        got = [g for g in tagged(n.stdout) if g.startswith("@ e")]
        self.assertEqual(got, ["@ e1 e=1z10 f=101z", "@ e2 e=10z0 f=z010",
                               "@ e3 e=1xz0 f=101x"])

    def test_one_instance_per_bit(self):
        self.assertEqual(len(self.lines(r"^\s*sv_tran_ta\w*_bit[0-3]: entity sv2vhdl\.sv_tran\(")),
                         4, self.vhdl)
        self.assertTrue(self.lines(r"^\s*a => g\(2\),"), self.vhdl)


@needs_stack
class TestTranifUnknownControl(Translated):
    """A tranif's x or z control passes an x (it blocked like a 0 control: z)."""

    SOURCE = """\
module tb;
  wire a, b, c, d;
  reg en, wv, ww;
  assign b = wv;
  assign d = ww;
  tranif1 t1(a, b, en);
  tranif0 t0(c, d, en);
  initial begin
    wv = 0; ww = 0; en = 1'bx;
    #1 $display("@ x0 a=%b c=%b", a, c);
    wv = 1; ww = 1;
    #1 $display("@ x1 a=%b c=%b", a, c);
    wv = 1'bz; ww = 1'bz;
    #1 $display("@ xz a=%b c=%b", a, c);
    en = 1'bz; wv = 1; ww = 1;
    #1 $display("@ z1 a=%b c=%b", a, c);
    en = 1;
    #1 $display("@ 11 a=%b c=%b", a, c);
    en = 0;
    #1 $display("@ 01 a=%b c=%b", a, c);
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestTranOnPortSelectWarns(Translated):
    """A tran on a select of a module port stays a one-way copy, and says so (the resolver joins
    a switch to a port only on the module's side)."""

    TOP = "tb"
    SOURCE = """\
module sub(input [3:0] bus, inout w);
  tran t1(bus[2], w);
endmodule
module tb;
  reg [3:0] b; wire w;
  sub u(.bus(b), .w(w));
  initial begin
    b = 4'b0100;
    #1 $display("@ w=%b", w);
  end
endmodule
"""

    def test_warns(self):
        self.assertRegex(self.ivlog, r"Warning: bus_sig\(2\) at \S+:2 is connected one way only")

    def test_value_reaches_the_switch(self):
        self.assert_like_vvp()


@needs_stack
class TestTranWarningUnderVamos(TempDir):
    """The one-way warning that stays is a vamos warning; --vamos-strict makes it an error."""

    SOURCE = ("`timescale 1ns/1ps\nmodule sub(input [3:0] bus, inout w);\n  tran t1(bus[2], w);\n"
              "endmodule\nmodule tb;\n  reg [3:0] b; wire w;\n  sub u(.bus(b), .w(w));\n"
              "  initial begin\n    b = 4'b0100;\n    #1 $display(\"w=%b\", w);\n  end\nendmodule\n")

    def vcs(self, *args):
        e = dict(os.environ)
        e["PATH"] = SHIMS + os.pathsep + e.get("PATH", "")
        e.pop("VAMOS_ANALOG", None)
        e.pop("PYTHONPATH", None)
        return run(["vcs"] + list(args), cwd=self.tmp, env=e, timeout=600)

    def test_warning_and_strict(self):
        self.write("tb.v", self.SOURCE)
        c = self.vcs("tb.v")
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertRegex(c.stdout, r"vamos: warning: tb\.v:3: bus_sig\(2\) is connected one way only")
        c = self.vcs("tb.v", "--vamos-strict")
        self.assertEqual(c.returncode, 1, c.stdout)
        self.assertRegex(c.stdout, r"vamos: error: tb\.v:3: bus_sig\(2\) is connected one way only")


# ---------------------------------------------------------------------- R6L-05 / R6L-06

def _nvc_and_libdir():
    nvc = tools.find_real("nvc")
    return nvc, (tools.nvc_libdir(nvc) if nvc else "")


@needs_stack
class TestResolverPlugin(TempDir):
    """libresolver.so: a SIGINT while the plugin's Python runs ends nvc as any SIGINT does,
    and an error line goes to stderr, with SV2VHDL_QUIET set or not."""

    VHDL = ("entity top is end entity;\narchitecture a of top is\n  signal s : bit;\nbegin\n"
            "  process begin s <= '1'; wait for 1 ns; report \"top done\"; wait; end process;\n"
            "end architecture;\n")

    def prepare(self, pythonpath: str, quiet: bool):
        """The design analysed and elaborated; (the -r command line, its environment)."""
        nvc, libdir = _nvc_and_libdir()
        plugin = os.path.join(libdir, "sv2vhdl", "libresolver.so")
        if not os.path.isfile(plugin):
            self.skipTest("no libresolver.so in %s" % libdir)
        if os.path.isfile(os.path.join(libdir, "sv2vhdl", "sv2vhdl_resolver.py")):
            self.skipTest("the plugin directory holds sv2vhdl_resolver.py (an installed nvc)")
        self.write("top.vhd", self.VHDL)
        env = dict(os.environ)
        env["PYTHONPATH"] = pythonpath
        env.pop("SV2VHDL_QUIET", None)
        if quiet:
            env["SV2VHDL_QUIET"] = "1"
        c = subprocess.run([nvc, "-L", libdir, "-a", "top.vhd", "-e", "top"], cwd=self.tmp,
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, errors="replace", timeout=300)
        self.assertEqual(c.returncode, 0, c.stdout)
        return [nvc, "-L", libdir, "--load=" + plugin, "-r", "top"], env

    def test_early_sigint_ends_nvc(self):
        """The resolver module, imported while nvc loads the plugin (before nvc has a SIGINT
        handler of its own), says it is running and sleeps: a SIGINT then ends nvc.  Python's
        handler (Py_Initialize) made it a KeyboardInterrupt inside the import, and the run went
        on to the end."""
        fake = os.path.join(self.tmp, "fakepy")
        os.makedirs(fake)
        marker = os.path.join(self.tmp, "in_import")
        with open(os.path.join(fake, "sv2vhdl_resolver.py"), "w") as fh:
            fh.write("import time\n"
                     "open(%r, 'w').close()\n"
                     "time.sleep(20)\n"
                     "def resolve_net(nets, design_name):\n"
                     "    return None\n" % marker)
        cmd, env = self.prepare(fake, quiet=True)
        p = subprocess.Popen(cmd, cwd=self.tmp, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True, errors="replace")
        try:
            deadline = time.time() + 60
            while not os.path.exists(marker) and p.poll() is None and time.time() < deadline:
                time.sleep(0.02)
            self.assertTrue(os.path.exists(marker), "the resolver module was never imported")
            os.kill(p.pid, signal.SIGINT)
            out, _ = p.communicate(timeout=120)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
        self.assertNotIn("top done", out)
        self.assertNotIn("KeyboardInterrupt", out)
        self.assertNotEqual(p.returncode, 0, out)

    def test_errors_on_stderr(self):
        """An error is an error line on stderr ("** Error: resolver: ERROR - ..."), which
        bin/vvp-sv2ghdl and vamos show as nvc's own errors.  It was an nvc note, which they
        print on stdout as if the design had said "ERROR - cannot import sv2vhdl_resolver"."""
        empty = os.path.join(self.tmp, "nopy")
        os.makedirs(empty)
        for quiet in (True, False):
            with self.subTest(quiet=quiet):
                cmd, env = self.prepare(empty, quiet=quiet)
                r = subprocess.run(cmd, cwd=self.tmp, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, universal_newlines=True,
                                   errors="replace", timeout=300)
                self.assertRegex(r.stderr, r"(?m)^\*\* Error: resolver: ERROR - cannot import "
                                           r"sv2vhdl_resolver$")
                self.assertNotIn("resolver: ERROR", r.stdout)
                self.assertNotRegex(r.stdout + r.stderr, r"Note: resolver: ERROR")
                self.assertIn("top done", r.stdout + r.stderr)


@needs_stack
class TestModS(TempDir):
    """l3d_mod_s: Verilog's signed %, the remainder with the dividend's sign."""

    VHDL = """\
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.logic3d_types_pkg.all;
entity modtb is end entity;
architecture t of modtb is
    function v(i : integer) return logic3d_vector is
    begin
        return unsigned_to_l3d(unsigned(std_logic_vector(to_signed(i, 8))));
    end function;
    function s(a : logic3d_vector) return string is
        variable r : string(1 to a'length);
        variable k : natural := 1;
    begin
        for i in a'range loop
            r(k) := to_char(a(i));
            k := k + 1;
        end loop;
        return r;
    end function;
begin
    process
        type pair is array (0 to 1) of integer;
        type pairs is array (natural range <>) of pair;
        constant cases : pairs := ((-7, 3), (7, -3), (-7, -3), (7, 3), (-8, 3), (-5, 7), (-128, 3));
    begin
        for k in cases'range loop
            report "MOD " & integer'image(cases(k)(0)) & " " & integer'image(cases(k)(1)) & " "
                & integer'image(to_integer(signed(std_logic_vector(
                      l3d_to_unsigned(l3d_mod_s(v(cases(k)(0)), v(cases(k)(1))))))));
        end loop;
        report "MOD0 " & s(l3d_mod_s(v(7), v(0)));
        wait;
    end process;
end architecture;
"""

    def test_dividend_sign(self):
        nvc, libdir = _nvc_and_libdir()
        self.write("modtb.vhd", self.VHDL)
        r = subprocess.run([nvc, "--std=2040", "-L", libdir, "-a", "modtb.vhd", "-e", "modtb",
                            "-r"], cwd=self.tmp, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                           timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout)
        got = re.findall(r"MOD0? [-\d X]+", r.stdout)
        # Verilog: -7 % 3 = -1, 7 % -3 = 1 (VHDL mod gave 2 and -2), x for a zero divisor
        self.assertEqual(got, ["MOD -7 3 -1", "MOD 7 -3 1", "MOD -7 -3 -1", "MOD 7 3 1",
                               "MOD -8 3 -2", "MOD -5 7 -5", "MOD -128 3 -2",
                               "MOD0 XXXXXXXX"], r.stdout)


if __name__ == "__main__":
    unittest.main()
