"""Round-6 T items: tgt-vhdl (the iverilog VHDL back end) process semantics.

R6T-01  A blocking assignment to a signal-class target no longer yields in the middle of
        the process: a process that suspends inside its body (an initial or final block,
        or an always block with a delay or event control inside) deposits (:=) its
        blocking assignments, read back at once, so a process waiting on the target
        (ivtest vhdl_test2: a VHDL dut's process(input)) runs after this one suspends, and
        a net fed by it changes then too (ivtest sched2), as in Verilog. An initial block's
        final wait holds (ivtest pr710 ran again and again). An always block with two
        delays (`clk = 0; #5 clk = 1; #5;') commits each write before it suspends, and a
        shadow variable re-reads its signal after every suspension (another process may
        have written it). Kept from before: until an initial block first suspends, a read
        of what it deposited waits a delta (ivtest pr307a, x compared by its value bits);
        $readmemh deposits like any blocking write. Guard: a loop with no delay is one
        activation now, and $urandom no longer calls nvc's vector xor, which crashed there.
R6T-02  Verilog's time-zero order: an `always @(a)' waits for its first event; an initial
        block's time-zero assignments are events (SV variable initializers are not): when
        one initial block assigns a signal at time zero, every one starts a delta late (all
        or none; ivtest vhdl_loop reads at time zero, before a VHDL dut's update). A net a
        force or release names keeps its own driver (no fused comb cone: ivtest pr2849783,
        pr3368642).
R6T-03  repeat (n) reads its count after a blocking assignment to n (or a task argument).
R6T-04  SV break / continue / do-while; $dist_uniform .. $dist_erlang (sv_math_pkg);
        an automatic task called from several processes (each works on its own copy);
        recursion, disable fork and a $dist_* call that Verilog may not evaluate keep a
        located error.
R6T-05  A named-block local another process names (`blk.t') is an architecture signal.
R6T-06  A memory word read at a run-time index outside the array is x; such a store is
        dropped (vvp).
R6T-08  `mem[i][j] = <1-bit expr>' (an assignment sv-normalize leaves to iverilog: on two
        lines, a part-select, a nonblocking one): the right-hand side takes the select's
        width (it took the whole word's, and nvc rejected the VHDL: Hazard3's
        hazard3_onehot_priority_dynamic.v could not compile under vamos).
R6T-09  `$set_val(mem, i, j, v)' (sv-normalize's rewrite of a one-line `mem[i][j] = v;')
        on a memory: translated (the always block lost its body, silently); every index
        counts from its declared bounds and a store outside them is dropped, for a vector
        too.
R6T-10  Strength-specified constant drivers on several words of a memory of nets (ivtest
        pr1703346): one strength buffer per word and bit, with its own label (the labels
        repeated: nvc analysis failed), on the word's bit; a memory of nets with several
        drivers has resolved elements (the first driver won).

    cd tests/vamos && python3 -m unittest test_r6_T -v

Each class translates its sources once with bin/iverilog-sv2ghdl (VAMOS_IVERILOG / IVERILOG
select the iverilog, so a private translator build is tested by pointing them at its
wrapper; bin/sv2vhdl-modules takes the translator plugins from <its prefix>/lib/ivl), runs
them under nvc with bin/vvp-sv2ghdl and, for reference, under vvp; lines starting with "@ "
are compared. Linux (WSL) only: the stack is Linux ELF; elsewhere the classes skip.
"""

import os
import re
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
    """Translate SOURCES once per class; run under nvc and vvp on demand."""

    SOURCES = {}          # file name -> text; the Verilog top file first
    DATA = {}             # file name -> text: data files the run reads (not sources)
    TOP = "tb"
    EXPECT_TRANSLATION = True

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vamos-r6t-")
        cls.env = _stack_env()
        cls.files = []
        for name, text in cls.DATA.items():
            with open(os.path.join(cls.tmp, name), "w") as fh:
                fh.write(text)
        for name, text in cls.SOURCES.items():
            path = os.path.join(cls.tmp, name)
            with open(path, "w") as fh:
                fh.write(text.replace("@TMP@", cls.tmp))   # a data file's directory
            cls.files.append(path)
        cls.xlat = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "-g2012",
                         "-s", cls.TOP] + cls.files, cls.tmp, cls.env)
        out = os.path.join(cls.tmp, "vsim")
        cls.vhdl = cls.ivlog = ""
        for attr, fname in (("vhdl", "design.vhd"), ("ivlog", "iverilog.log")):
            try:
                with open(os.path.join(out, fname), errors="replace") as fh:
                    setattr(cls, attr, fh.read())
            except OSError:
                pass
        cls._nvc = cls._vvp = None

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("VAMOS_TEST_KEEP"):
            print("kept %s" % cls.tmp)
        else:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        if self.EXPECT_TRANSLATION:
            self.assertEqual(self.xlat.returncode, 0,
                             "translation failed (iverilog %s):\n%s%s" % (
                                 self.env.get("IVERILOG"), self.xlat.stdout,
                                 self.xlat.stderr))
            self.assertNotIn("sv2vhdl:deferred", self.vhdl, self.ivlog)

    def nvc(self):
        if self.__class__._nvc is None:
            self.__class__._nvc = _run([os.path.join(BIN, "vvp-sv2ghdl"), "vsim"],
                                       self.tmp, self.env)
        return self.__class__._nvc

    def vvp(self):
        if self.__class__._vvp is None:
            vvp = _vvp()
            self.assertTrue(vvp, "no vvp")
            c = _run([self.env["IVERILOG"], "-g2012", "-s", self.TOP, "-o", "ref.vvp"]
                     + self.files, self.tmp, self.env)
            self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
            self.__class__._vvp = _run([vvp, "-n", "ref.vvp"], self.tmp, self.env)
        return self.__class__._vvp

    def assert_like_vvp(self, ordered=True):
        n, v = self.nvc(), self.vvp()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        want, got = tagged(v.stdout), tagged(n.stdout)
        self.assertTrue(want, v.stdout)
        if not ordered:
            want, got = sorted(want), sorted(got)
        self.assertEqual(got, want, "\n--- nvc:\n%s%s\n--- vvp:\n%s"
                         % (n.stdout, n.stderr, v.stdout))

    def lines(self, pattern):
        return [ln.strip() for ln in self.vhdl.splitlines() if re.search(pattern, ln)]


class LoudError(Probe):
    """A construct with no translation: a located error, the module deferred."""

    EXPECT_TRANSLATION = False

    def assert_error(self, text):
        said = self.xlat.stdout + self.xlat.stderr + self.ivlog
        self.assertIn(text, said)
        # located: <file>:<line>: <text>
        self.assertRegex(said, r"\.s?v:\d+: [^\n]*" + re.escape(text))


# ---------------------------------------------------------------------- R6T-01

@needs_stack
class TestBlockingDoesNotYield(Probe):
    """An initial block's blocking writes, after a delay, land together: a process waiting
    on them sees all of them, never a half-updated state."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ps
module tb;
  reg [3:0] a, b;
  always @(a) if ($time > 0) $display("@ %0t obs a=%0d b=%0d", $time, a, b);
  initial begin
    #1 a = 1; b = a + 1;
    #1 a = 3; b = a; a = 4;
    #1 b = 7; a = b - 2; b = a;
    #1 $finish;
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_deposits_without_yield(self):
        self.assertTrue(self.lines(r"^\s*b := a \+"), self.vhdl)
        self.assertFalse(self.lines(r"Read target of blocking assignment"), self.vhdl)


VHDL_DUT = """\
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity mask is
    port (input : in std_logic_vector(15 downto 0);
          mask  : in std_logic_vector(15 downto 0);
          output : out std_logic_vector(15 downto 0)
    );
end;

architecture behaviour of mask is
begin
    L: process(input)
        variable tmp : std_logic_vector(15 downto 0);
    begin
        tmp := input;
        tmp := tmp and mask;
        output <= tmp;
    end process;
end;
"""


@needs_stack
class TestVhdlDutSeesWholeStep(Probe):
    """ivtest vhdl_test2 with a deterministic mask: the VHDL dut, sensitive to `input'
    only, runs after `in = in+1' AND `mask = ...', never between them."""

    TOP = "main"
    SOURCES = {"dut.vhd": VHDL_DUT, "src.v": """\
module main;
   wire [15:0] out;
   reg [16:0]  in;
   reg [15:0]  mask;
   mask dut (.\\output (out), .\\input (in[15:0]), .mask(mask));
   wire [15:0] out_ref = in[15:0] & mask;
   initial begin
      for (in = 0 ; in[10] == 0 ; in = in+1) begin
         mask = 16'h5E81 ^ in[15:0];
         #1 if (out !== out_ref) begin
            $display("@ FAILED: in=%b, out=%b, mask=%b, out_ref=%b", in, out, mask, out_ref);
            $finish;
         end
      end
      $display("@ PASSED");
   end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestClockWithTwoDelays(Probe):
    """`always begin clk = 0; #5 clk = 1; #5; end': each write is visible when the process
    suspends (the shadow commit at the end of the body made clk 1 for ever)."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ps
module tb;
  reg clk; integer n = 0;
  always begin clk = 0; #5 clk = 1; #5; end
  always @(posedge clk) begin
    n = n + 1;
    $display("@ %0t posedge %0d", $time, n);
    if (n == 3) $finish;
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestShadowSeesOtherWriters(Probe):
    """An always block's shadow variable re-reads its signal after every suspension: a
    value another process wrote meanwhile is not lost."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  integer cnt;
  reg [7:0] a, b;
  always @(posedge clk) begin
    cnt = cnt + 1;
    $display("@ %0t cnt=%0d", $time, cnt);
  end
  always @(a) begin
    b = b + a;
    if ($time > 0) $display("@ %0t a=%0d b=%0d", $time, a, b);
  end
  initial begin
    cnt = 10; b = 0;
    #1 clk = 1; #1 clk = 0;
    #1 cnt = 100;
    #1 clk = 1; #1 clk = 0;
    #1 b = 50; a = 1;
    #1 b = 70; a = 2;
    #1 $display("@ final cnt=%0d b=%0d", cnt, b);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestNetNotUpdatedInMidProcess(Probe):
    """ivtest sched2: a net fed by a blocking assignment changes only after the process
    suspends (`b = 1; q' still reads the old `a & b'), as in Verilog."""

    TOP = "main"
    SOURCES = {"src.v": """\
module main;
   reg a;
   reg b;
   wire q = a & b;
   initial begin
      a = 1;
      b = 0;
      #1 $display("@ start q=%b", q);
      b = 1;
      $display("@ after b=1, before suspending: q=%b b=%b", q, b);
      #0 $display("@ after #0: q=%b", q);
   end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestReadmemAfterBlockingWrites(Probe):
    """$readmemh of a memory the initial block wrote before (a deposit): the load is
    deposited too, so a read right after it sees the data (its `<=' had no wait after
    it once a deposit stopped making one: the read saw the old words)."""

    DATA = {"mem.hex": "0a\n0b\n0c\n0d\n"}
    SOURCES = {"src.v": """\
`timescale 1ns/1ps
module tb;
  reg [7:0] mem [0:3];
  integer i;
  initial begin
    for (i = 0; i < 4; i = i + 1) mem[i] = 8'h11;
    #1 i = 0;
    $readmemh("@TMP@/mem.hex", mem);
    $display("@ now %h %h %h %h", mem[0], mem[1], mem[2], mem[3]);
    #1 $display("@ later %h %h %h %h", mem[0], mem[1], mem[2], mem[3]);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestInitialEndsOnce(Probe):
    """ivtest pr710: an initial block that waited on `foo' (a `wait (foo == idx)' loop)
    and ends while `foo' still changes holds at its end (nvc resumed it from `wait;' and
    it ran again and again). (Its first line shows foo as x where vvp shows 0: vvp's
    `x == 0' is not true, the value-plane comparison is -- by design.)"""

    TOP = "main"
    SOURCES = {"src.v": """\
module main;
   reg [5:0] idx, mask;
   wire [5:0] foo = idx & mask;
   initial begin
      mask = 5'h1f;
      for (idx = 0 ;  idx < 5 ;  idx = idx+1)
        wait (foo == idx) begin
           $display("@ foo=%d, idx=%d", foo, idx);
        end
      $display("@ PASSED");
   end
endmodule
"""}

    def test_runs_once(self):
        n = self.nvc()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertEqual(tagged(n.stdout).count("@ PASSED"), 1, n.stdout)
        self.assertEqual(tagged(n.stdout)[-4:],
                         ["@ foo= 1, idx= 1", "@ foo= 2, idx= 2", "@ foo= 3, idx= 3",
                          "@ foo= 4, idx= 4"][-3:] + ["@ PASSED"], n.stdout)

    def test_end_holds(self):
        self.assertTrue(self.lines(r"^\s*loop$") and self.lines(r"^\s*wait;$"), self.vhdl)


@needs_stack
class TestTimeZeroReadsSettle(Probe):
    """ivtest pr307a: at time zero, before an initial block first suspends, a read of what
    it deposited still waits a delta, so a continuous `in1 + in2' is read settled. (Verilog
    reads it not yet updated, x, and `x != out2' is unknown, so no MISMATCH; read by its
    value bits, as the translation reads an x, it would mismatch.) After the first delay
    nothing yields (TestBlockingDoesNotYield)."""

    SOURCES = {"src.v": """\
module tb;
  reg [127:0] in1, in2;
  wire [128:0] out1;
  reg [128:0] out2;
  assign out1 = in1 + in2;
  task r;
    begin
      out2 = in1 + in2;
      if (out1 != out2) $display("@ MISMATCH %h %h", out1, out2);
    end
  endtask
  initial begin
    in1 = 128'hffffffffffffffffffffffffffffffff;
    in2 = 128'hfffffffffffffffffffffffffffffff7;
    r;
    in1 = 128'h1;
    in2 = 128'hffffffffffffffffffffffffffffffff;
    r;
    $display("@ done");
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestLongActivation(Probe):
    """Loops with no delay in them run in one process activation now (no `wait for 0 ns'
    in each pass): 20000 passes of vector arithmetic and 3000 $urandom draws. ($urandom
    flips bit 31 by an add: nvc's std_logic vector xor stopped with a SIGSEGV after a few
    hundred calls in one activation.)"""

    SOURCES = {"src.v": """\
module tb;
  reg [31:0] x, y, s;
  reg [63:0] w;
  reg [7:0] mem [0:4095];
  integer i;
  reg [31:0] u;
  initial begin
    x = 32'h12345678; y = 32'h9abcdef0; s = 0; w = 64'h1;
    for (i = 0; i < 20000; i = i + 1) begin
      x = x ^ y;
      s = s + i;
      w = w * 3 + {x, y};
      mem[i % 4096] = i[7:0] ^ x[7:0];
      y = {y[30:0], y[31]} | (x & s);
    end
    $display("@ x=%h s=%h w=%h y=%h m=%h", x, s, w, y, mem[17]);
    for (i = 0; i < 3000; i = i + 1) u = $urandom;
    $display("@ u=%0d", u);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestForceReleaseThroughNetChain(Probe):
    """ivtest pr2849783 / pr3368642: force and release of a net another net follows
    (`assign a = i; assign b = a;'). A forced or released net keeps its own continuous
    assignment instead of joining a fused comb cone: under Verilog's time-zero order the
    cone's first run came before its input changed, and nvc then never re-ran it on the
    force (b kept 1) or the release (a stayed 0)."""

    SOURCES = {"src.v": """\
module tb;
  reg i;
  wire a, b;
  reg [3:0] r1;
  wire [3:0] w1, w2;
  assign a = i;
  assign b = a;
  assign w1 = r1;
  assign w2 = w1;
  initial begin
    i = 1; r1 = 0;
    #1 $display("@ %b %b %b %b", a, b, w1, w2);
    #1 force a = 0; force w1 = 4'bz;
    #1 $display("@ %b %b %b %b", a, b, w1, w2);
    r1 = 3;
    #1 release a; release w1;
    #1 $display("@ %b %b %b %b", a, b, w1, w2);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_forced_nets_keep_their_drivers(self):
        self.assertFalse(self.lines(r"^\s*a := "), self.vhdl)
        self.assertFalse(self.lines(r"^\s*w1 := "), self.vhdl)


# ---------------------------------------------------------------------- R6T-02

@needs_stack
class TestTimeZeroOrder(Probe):
    """Verilog's time-zero order: an `always @(a)' waits for its first event (it ran once at
    time 0, TC-08), an initial block's time-zero assignment is an event to it, an SV variable
    initializer is not, and a flop's time-zero reset prints its q once."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ps
module tb;
  reg a = 0;
  integer cnt = 0, bcnt = 0;
  reg b;
  initial b = 1;
  always @(a) cnt = cnt + 1;
  always @(b) bcnt = bcnt + 1;
  reg clk, rst;
  reg [3:0] q;
  initial clk = 0;
  always #5 clk = ~clk;
  always @(posedge clk or posedge rst) if (rst) q <= 0; else q <= q + 1;
  always @(q) $display("@ %0t q=%0d", $time, q);
  initial begin
    rst = 1;
    #1 a = 1;
    #1 $display("@ %0t cnt=%0d bcnt=%0d", $time, cnt, bcnt);
    #10 rst = 0;
    #20 $finish;
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_always_waits_first(self):
        self.assertTrue(self.lines(r"^\s*wait on a;"), self.vhdl)


VHDL_LOOP_DUT = """\
library ieee;
use ieee.std_logic_1164.all;

entity vhdl_loop is
    port(start : in std_logic;
         counter : out integer);
end vhdl_loop;

architecture test of vhdl_loop is
begin
    process(start)
        variable cnt : integer := 0;
    begin
        loop
            cnt := cnt + 1;
            counter <= cnt;
            wait for 10 s;
        end loop;
    end process;
end test;
"""


@needs_stack
class TestTimeZeroOrderOnlyWhenAssigned(Probe):
    """ivtest vhdl_loop: an initial block that only reads at time zero reads before a VHDL
    dut's time-zero `counter <= 1' lands, as vvp's initial does. The initial blocks start
    with `wait for 0 ns' only in a design where one of them assigns a signal at time
    zero (all or none, so they keep their order)."""

    TOP = "vhdl_loop_test"
    SOURCES = {"dut.vhd": VHDL_LOOP_DUT, "src.v": """\
module vhdl_loop_test;
logic start;
int counter;
vhdl_loop dut(start, counter);

initial begin
    for(int i = 0; i < 5; ++i) begin
        if(counter !== i) begin
            $display("FAILED");
            $finish();
        end

        #10;
    end

    $display("PASSED");
    $finish();
end
endmodule
"""}

    def test_counts_from_zero(self):
        n = self.nvc()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertIn("PASSED", n.stdout)
        self.assertNotIn("FAILED", n.stdout)

    def test_no_time_zero_wait(self):
        self.assertFalse(self.lines(r"Every process reaches its first wait first"), self.vhdl)


# ---------------------------------------------------------------------- R6T-03

@needs_stack
class TestRepeatCount(Probe):
    """repeat (n) right after `n = 3', and over a task's input argument."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ps
module tb;
  integer n, c, cnt;
  always @(n) if ($time > 0) $display("@ %0t n=%0d", $time, n);
  task rep(input integer k); begin repeat (k) cnt = cnt + 1; end endtask
  initial begin
    #1 n = 3; c = 0; repeat (n) c = c + 1;
    $display("@ c=%0d", c);
    cnt = 0; rep(n); rep(2);
    $display("@ cnt=%0d", cnt);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


# ---------------------------------------------------------------------- R6T-04

@needs_stack
class TestBreakContinue(Probe):
    """break / continue in for, while, forever, repeat and do-while loops, nested loops,
    a function's loop and a loop inside a disabled block (ivtest br_gh191_break and
    br_gh191_continue were translation errors)."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ps
module tb;
  integer i, j, s, n, idx;
  reg [7:0] r;
  function integer f(input integer k);
    integer m;
    begin
      f = 0;
      for (m = 0; m < 10; m = m + 1) begin
        if (m == k) break;
        if (m % 2) continue;
        f = f + m;
      end
    end
  endfunction
  initial begin
    s = 0;
    for (i = 0; i < 4; i = i + 1) begin
      for (j = 0; j < 4; j = j + 1) begin
        if (j == 2) continue;
        if (j == 3) break;
        s = s + 10*i + j;
      end
      if (i == 2) continue;
      s = s + 100;
    end
    $display("@ s=%0d i=%0d j=%0d", s, i, j);
    begin : blk
      n = 0;
      while (1) begin
        n = n + 1;
        if (n > 5) disable blk;
        if (n == 3) continue;
        s = s + n;
      end
    end
    $display("@ s=%0d n=%0d f=%0d f7=%0d", s, n, f(5), f(7));
    #1 r = 0;
    do begin r = r + 1; if (r == 2) continue; end while (r < 4);
    $display("@ r=%0d", r);
    idx = 0;
    forever begin idx += 1; if (idx < 2) continue; break; end
    $display("@ idx=%0d", idx);
    n = 0;
    repeat (3) begin #1 n = n + 1; if (n == 2) break; end
    $display("@ %0t n=%0d", $time, n);
    idx = 0;
    repeat (5) begin idx += 1; if (idx < 2) continue; idx += 1; end
    $display("@ idx=%0d", idx);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_labelled_exits(self):
        self.assertTrue(self.lines(r"exit sv_brk_\d+;"), self.vhdl)
        self.assertTrue(self.lines(r"exit sv_cont_\d+;"), self.vhdl)


@needs_stack
class TestDistFunctions(Probe):
    """$dist_* draw vvp's numbers and advance their seed, in an initial and an always
    block (a signal-class seed)."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ps
module tb;
  integer seed, i, v;
  reg [7:0] r;
  reg clk = 0;
  integer sseed, sv;
  always @(sseed) if ($time > 0) $display("@ %0t sseed=%0d", $time, sseed);
  always @(posedge clk) begin
    sv = $dist_uniform(sseed, 10, 20);
    $display("@ %0t clk sv=%0d", $time, sv);
  end
  initial begin
    seed = 1;
    for (i = 0; i < 5; i = i + 1) begin
      v = $dist_uniform(seed, 0, 99);
      $display("@ u %0d seed=%0d", v, seed);
    end
    seed = 7;
    $display("@ n %0d %0d", $dist_normal(seed, 50, 10), $dist_normal(seed, 50, 10));
    $display("@ e %0d p %0d c %0d t %0d er %0d", $dist_exponential(seed, 20),
             $dist_poisson(seed, 5), $dist_chi_square(seed, 4), $dist_t(seed, 3),
             $dist_erlang(seed, 2, 10));
    r = $dist_uniform(seed, -5, 300);
    $display("@ r=%0d seed=%0d", r, seed);
    sseed = 3;
    #1 clk = 1; #1 clk = 0; #1 clk = 1;
    #1 $display("@ sseed=%0d", sseed);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestAutomaticTaskFromProcesses(Probe):
    """An automatic task called from three processes, overlapping in time: each works on
    its own copy of the task's variables (one shared copy has no translation)."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ps
module tb;
  integer r1, r2, r3;
  task automatic add(input integer a, input integer b, output integer s);
    integer tmp;
    begin tmp = a + b; #1 s = tmp; end
  endtask
  initial begin
    add(1, 2, r1); $display("@ %0t p1 r1=%0d", $time, r1);
    add(10, 20, r1); $display("@ %0t p1 r1=%0d", $time, r1);
  end
  initial begin
    add(100, 200, r2); $display("@ %0t p2 r2=%0d", $time, r2);
  end
  always @(r2) if ($time > 0) begin
    add(r2, 5, r3); $display("@ %0t p3 r3=%0d", $time, r3);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp(ordered=False)


@needs_stack
class TestRecursiveTaskStaysLoud(LoudError):
    SOURCES = {"src.v": """\
module tb;
  integer r;
  task automatic fact(input integer n, output integer f);
    integer g;
    begin if (n <= 1) f = 1; else begin fact(n - 1, g); f = n * g; end end
  endtask
  initial begin fact(5, r); $display("r=%0d", r); end
endmodule
"""}

    def test_located_error(self):
        self.assert_error("calls itself (recursion)")


@needs_stack
class TestDisableForkStaysLoud(LoudError):
    SOURCES = {"src.v": """\
module tb;
  initial begin
    fork #1 $display("a"); join_none
    disable fork;
  end
endmodule
"""}

    def test_located_error(self):
        said = self.xlat.stdout + self.xlat.stderr + self.ivlog
        self.assertIn("unsupported construct (fork)", said)


@needs_stack
class TestConditionalDistStaysLoud(LoudError):
    """A $dist_* call in a ?: branch would draw (and advance its seed) even when Verilog
    takes the other branch: a located error."""

    SOURCES = {"src.v": """\
module tb;
  integer seed, v;
  reg c;
  initial begin
    seed = 1; c = 1;
    v = c ? $dist_uniform(seed, 0, 9) : 0;
    $display("v=%0d", v);
  end
endmodule
"""}

    def test_located_error(self):
        self.assert_error("$dist_uniform in a branch of ?:")


# ---------------------------------------------------------------------- R6T-05

@needs_stack
class TestSharedBlockLocal(Probe):
    """`blk.t', a local of another process's named block, read and written: an
    architecture signal (nvc analysis said "no visible declaration"; the write aborted
    the back end)."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  integer n = 0;
  always @(posedge clk) begin : blk
    reg [7:0] t;
    t = 8'd5 + n;
    n = n + 1;
    $display("@ %0t inside t=%0d", $time, t);
  end
  always begin : gen
    reg [3:0] c;
    c = 3;
    #2 c = c + 4;
    #10;
  end
  initial begin
    #1 clk = 1;
    #1 $display("@ %0t outside blk.t=%0d gen.c=%0d", $time, blk.t, gen.c);
    clk = 0;
    #1 clk = 1;
    #1 $display("@ %0t outside blk.t=%0d gen.c=%0d", $time, blk.t, gen.c);
    blk.t = 99;
    $display("@ %0t written blk.t=%0d", $time, blk.t);
    #1 $finish;
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


# ---------------------------------------------------------------------- R6T-06

@needs_stack
class TestMemoryIndexOutOfRange(Probe):
    """A word read at a run-time index outside the array is x (0.0 for a real array),
    and such a store is dropped; vvp's results (the run stopped)."""

    SOURCES = {"src.v": """\
module tb;
  reg [7:0] mem [0:3];
  reg [7:0] mc [4:7];
  reg       sb [0:1];
  real      rm [0:1];
  integer i, k;
  reg [7:0] v;
  initial begin
    for (i = 0; i < 4; i = i + 1) mem[i] = i * 3;
    for (i = 4; i < 8; i = i + 1) mc[i] = 8'h30 + i;
    sb[0] = 1; sb[1] = 0; rm[0] = 1.5; rm[1] = 2.5;
    for (i = -2; i < 7; i = i + 1) begin
      v = mem[i];
      $display("@ mem[%0d]=%h mc[%0d]=%h bit=%b", i, v, i + 2, mc[i + 2], mem[i][1]);
    end
    k = 9; mem[k] = 8'hAA; k = -1; mem[k] = 8'hBB; k = 5; mem[k][3:0] = 4'h7;
    $display("@ after oob writes: %h %h %h %h", mem[0], mem[1], mem[2], mem[3]);
    k = 5; $display("@ sb %b %b rm %f", sb[k], sb[1], rm[k]);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


# ---------------------------------------------------------------------- R6T-08

@needs_stack
class TestMemoryWordSelectTarget(Probe):
    """A bit or part of a memory word as the target of an assignment that iverilog
    elaborates itself (sv-normalize rewrites only a one-line `m[i][j] = v;'): Hazard3's
    stratify loop over two lines, a part-select, an indexed part-select of another word
    and a nonblocking bit store. The right-hand side takes the select's width; it took
    the word's (`v_m(i)(j) := unsigned_to_l3d(Resize(..., 8))'), which nvc rejects."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ps
module tb;
  reg [7:0] m [0:1];
  reg [1:0] has;
  reg [7:0] req, pri;
  reg [15:0] w [0:3];
  integer k;
  always @(*) begin : stratify
    reg signed [31:0] i, j;
    for (i = 0; i < 2; i = i + 1) begin
      for (j = 0; j < 8; j = j + 1)
        m[i][j] = req[j] &&
          pri[j] == i[0];
      has[i] = |m[i];
    end
  end
  initial begin
    req = 8'b1111_0101;
    pri = 8'b1010_1100;
    for (k = 0; k < 4; k = k + 1) w[k] = 16'h0;
    #1 $display("@ m0=%b m1=%b has=%b", m[0], m[1], has);
    k = 2;
    w[k][7:4] =
      4'hA;
    w[k][k] =
      1'b1;
    w[k+1][k*4 +: 4] =
      4'h9;
    w[k][15] <= 1'b1;
    w[k][0] =
      req[0] + req[2];
    #1 $display("@ w2=%h w3=%h", w[2], w[3]);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_bit_target_takes_a_bit(self):
        self.assertFalse(self.lines(r"\)\(l3d_index\(j, True\)\) :=.*Resize\(.*, 8\)\);"),
                         self.vhdl)


@needs_stack
class TestMemoryWordBitCompressed(Probe):
    """`w[k][1] ^= 1'b1' reads and writes one bit of the word (vvp itself stops on it:
    of_XOR's operand sizes differ), so the value is checked by hand."""

    SOURCES = {"src.v": """\
module tb;
  reg [15:0] w [0:3];
  integer k;
  initial begin
    for (k = 0; k < 4; k = k + 1) w[k] = 16'h00A4;
    k = 2;
    w[k][1] ^=
      1'b1;
    w[k][2] ^=
      1'b1;
    $display("@ w1=%h w2=%h", w[1], w[2]);
  end
endmodule
"""}

    def test_value(self):
        n = self.nvc()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertEqual(tagged(n.stdout), ["@ w1=00a4 w2=00a2"], n.stdout)


def _hazard3_dir():
    """The verilator-hazard3-mandelbrot-testbench checkout the hazard3 suite uses
    ($HAZARD3_MANDELBROT_DIR, else ~/verilator-hazard3-mandelbrot-testbench), or ""."""
    env = os.environ.get("HAZARD3_MANDELBROT_DIR")
    for d in ([env] if env else [os.path.expanduser("~/verilator-hazard3-mandelbrot-testbench")]):
        if os.path.isfile(os.path.join(d, "Hazard3", "hdl", "arith",
                                       "hazard3_onehot_priority_dynamic.v")):
            return d
    return ""


H3_ARITH = os.path.join(_hazard3_dir(), "Hazard3", "hdl", "arith")


@needs_stack
@unittest.skipUnless(_hazard3_dir(), "needs the verilator-hazard3-mandelbrot-testbench checkout")
class TestHazard3PriorityDynamic(Probe):
    """Hazard3's hazard3_onehot_priority_dynamic (the Xh3irq interrupt priorities) with
    W_REQ=8, N_PRIORITIES=2: it did not compile under vamos (iverilog and Verilator give
    gnt=00000100, 00000010)."""

    TOP = "t_opd"
    SOURCES = {"t_opd.v": """\
`timescale 1ns/1ps
module t_opd;
  reg  [7:0] req;
  reg  [7:0] pri;
  wire [7:0] gnt;
  hazard3_onehot_priority_dynamic #(.W_REQ(8), .N_PRIORITIES(2)) u (.pri(pri), .req(req), .gnt(gnt));
  initial begin
    req = 8'b1011_0110; pri = 8'b0010_0100;
    #1 $display("@ gnt=%b", gnt);
    req = 8'b1011_0110; pri = 8'b0000_0000;
    #1 $display("@ gnt=%b", gnt);
    req = 8'b0000_0001; pri = 8'b1111_1110;
    #1 $display("@ gnt=%b", gnt);
  end
endmodule
"""}

    @classmethod
    def setUpClass(cls):
        for f in ("hazard3_onehot_priority_dynamic.v", "hazard3_onehot_priority.v"):
            with open(os.path.join(H3_ARITH, f)) as fh:
                cls.SOURCES = dict(cls.SOURCES, **{f: fh.read()})
        super().setUpClass()

    def test_like_vvp(self):
        self.assert_like_vvp()


# ---------------------------------------------------------------------- R6T-09

@needs_stack
class TestSetValOnMemory(Probe):
    """One-line `m[i][j] = v;' assignments, which sv-normalize rewrites to
    `$set_val(m, i, j, v)': a memory's word and bit (the always block lost its body and
    the memory stayed x: "first arg to $set_val must be a signal"), memories and vectors
    whose ranges start elsewhere than 0 or ascend, 2-D packed words, a whole sub-word,
    a wider value, and indices outside the array or the word (the store is dropped)."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ps
module tb;
  reg [7:0] s [0:1];
  reg [7:0] req, pri;
  always @(*) begin : stratify
    reg signed [31:0] i, j;
    for (i = 0; i < 2; i = i + 1)
      for (j = 0; j < 8; j = j + 1)
        s[i][j] = req[j] && pri[j] == i[0];
  end
  reg [7:0] m [4:7];
  reg [8:1] d [3:0];
  reg [0:7] a [0:1];
  reg [3:0][7:0] p [0:1];
  reg [8:1] v1;
  reg [0:7] v2;
  reg [3:0][1:0] v3;
  integer i, j, k;
  reg [3:0] wide;
  initial begin
    req = 8'b1111_0101;
    pri = 8'b1010_1100;
    for (i = 4; i < 8; i = i + 1) m[i] = 0;
    for (i = 0; i < 4; i = i + 1) d[i] = 0;
    a[0] = 0; a[1] = 0; p[0] = 0; p[1] = 0;
    v1 = 0; v2 = 0; v3 = 0;
    i = 5; j = 3;
    m[i][j] = 1'b1;
    i = 7; j = 0;
    m[i][j] = 1'b1;
    i = 3; j = 1;
    m[i][j] = 1'b1;
    i = 4; j = 8;
    m[i][j] = 1'b1;
    i = 2; j = 8;
    d[i][j] = 1'b1;
    i = 0; j = 1;
    d[i][j] = 1'b1;
    i = 1; j = 0;
    d[i][j] = 1'b1;
    i = 1; j = 0;
    a[i][j] = 1'b1;
    j = 7;
    a[i][j] = 1'b1;
    i = 1; j = 2; k = 5;
    p[i][j][k] = 1'b1;
    i = 0; j = 3;
    p[i][j] = 8'hA5;
    j = 8;
    v1[j] = 1'b1;
    j = 0;
    v2[j] = 1'b1;
    i = 2; j = 1;
    v3[i][j] = 1'b1;
    i = 3; j = 0;
    v3[i][j] = 1'b1;
    wide = 4'b0110;
    i = 6; j = 2;
    m[i][j] = wide;
    i = 6; j = 1;
    m[i][j] = wide + 1;
    i = 6; j = 4;
    m[i][j] = 1'bx;
    #1;
    $display("@ s %b %b", s[0], s[1]);
    $display("@ m %b %b %b %b", m[4], m[5], m[6], m[7]);
    $display("@ d %b %b %b %b", d[0], d[1], d[2], d[3]);
    $display("@ a %b %b", a[0], a[1]);
    $display("@ p %h %h", p[0], p[1]);
    $display("@ v %b %b %b", v1, v2, v3);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_translated(self):
        # (round 6 repair: bin/iverilog-sv2ghdl sets SV_NORMALIZE_NO_SET_VAL for an iverilog
        # that takes the chained select, so these stores reach tgt-vhdl as assignments, at
        # the select's width; test_r6_R.TestSetValRewriteForced keeps the $set_val forms)
        self.assertNotIn("$set_val", self.ivlog)
        with open(os.path.join(self.tmp, "vsim", "_norm.sv"), errors="replace") as fh:
            self.assertNotIn("$set_val", fh.read())
        self.assertFalse(self.lines(r"SetVal_Idx_"), self.vhdl)


@needs_stack
class TestSetValNegativeRanges(Probe):
    """ivtest br_gh112e: `$set_val' on a vector whose packed ranges run below 0
    (reg [0:-1][14:-1][6:-1]): each index counts from its dimension's declared bound (the
    raw indices stopped the run: "index -128 outside of ..."), with no negative literal in
    the VHDL (`x - -1' failed nvc analysis)."""

    SOURCES = {"src.v": """\
module tb;
  reg [0:-1][14:-1][6:-1] array;
  integer i;
  reg signed [4:0] index;
  initial begin
    for (i = 0; i < 16; i++) begin
      index = i[3:0];
      array[-1][-5'sd1+index] = {4'd0, index[3:0]};
      array[ 0][-5'sd1+index] = {4'd1, index[3:0]};
    end
    $display("@ %h", array);
    index = 5;
    $display("@ %h %h", array[-1][-5'sd1+index], array[0][-5'sd1+index]);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


@needs_stack
class TestSetValBadTargetIsLocated(LoudError):
    """$set_val of something else than a vector or a memory: a located error (it was a
    message on stderr only, and the process silently lost the rest of its body)."""

    SOURCES = {"src.v": """\
module tb;
  reg [7:0] m [0:1];
  integer i;
  initial begin
    i = 0;
    $set_val(m[i], 1, 2, 1'b1);
    $display("%b", m[0]);
  end
endmodule
"""}

    def test_located_error(self):
        self.assert_error("the first argument of $set_val must be a vector or a memory")


# ---------------------------------------------------------------------- R6T-10

@needs_stack
class TestStrengthDriversOnMemoryWords(Probe):
    """ivtest pr1703346: constant drivers with strengths on two words of a memory of nets.
    Each word's bits get their own strength buffers, labelled with the word, on the word's
    bit (every word repeated word 0's labels -- "SV_STRENGTH_BUF_CD0B0_FOO already
    declared" -- and drove foo(b), a whole word). (The run itself still stops in nvc: its
    kernel net solver declines the nets foo(j)(b).)"""

    TOP = "main"
    SOURCES = {"src.v": """\
module main;
   wire [1:0] foo [0:1];
   assign     (highz0, strong1) foo[0] = 2'b01;
   assign     (strong0, highz1) foo[0] = 2'b01;
   assign     (highz0, strong1) foo[1] = 2'b10;
   assign     (strong0, highz1) foo[1] = 2'b10;
   initial #1 $display("foo[0] = %b, foo[1] = %b", foo[0], foo[1]);
endmodule
"""}

    def test_unique_labels_on_word_bits(self):
        labels = self.lines(r"^\s*sv_strength_buf_\w+: entity sv2vhdl\.sv_strength_buf")
        self.assertEqual(len(labels), 8, self.vhdl)
        self.assertEqual(len(set(labels)), 8, labels)
        for w in (0, 1):
            for b in (0, 1):
                self.assertEqual(len(self.lines(r"^\s*y => foo\(%d\)\(%d\),$" % (w, b))), 2,
                                 self.vhdl)

    def test_analysed(self):
        self.assertNotIn("already declared", self.xlat.stdout + self.xlat.stderr)

    def test_resolved_elements(self):
        self.assertTrue(self.lines(r"^\s*type foo_Type is array \(1 downto 0\) of "
                                   r"resolved_logic3d_vector\(1 downto 0\);"), self.vhdl)


@needs_stack
class TestMemoryOfNetsTwoDrivers(Probe):
    """A word of a memory of nets with two tristate drivers resolves them (the array's
    elements were unresolved and the first driver won: 11 where vvp gives x1)."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ps
module tb;
   wire [1:0] w [0:1];
   reg  a, b;
   assign     w[0] = a ? 2'b11 : 2'bzz;
   assign     w[0] = b ? 2'b01 : 2'bzz;
   assign     w[1] = 2'b10;
   initial begin
      a = 0; b = 0;
      #1 $display("@ w0 = %b w1 = %b", w[0], w[1]);
      a = 1;
      #1 $display("@ w0 = %b", w[0]);
      b = 1;
      #1 $display("@ w0 = %b", w[0]);
      a = 0;
      #1 $display("@ w0 = %b", w[0]);
   end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


if __name__ == "__main__":
    unittest.main()
