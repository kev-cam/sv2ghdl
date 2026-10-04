"""tgt-vhdl semantics fixes of the vcs-ams final fix round (group TC), on the real stack:

- TC-01  case equality ignores strength: a pull/weak 1 (pullup, tri1, the weak drive of an
         AMS BIDIR A2D) matches 1'b1 in case/casez/casex, a variable stores no strength, a
         continuous assignment drives its own; X and Z still match only x and z items.
- TC-02  an asynchronous reset (or clock edge) that lands while an async-reset flop sits at its
         NBA `wait for 0 ns' is no longer lost (template path), and a loop-back keeps the
         first pass's NBA values.
- TC-03  the time-zero initializer hoist resolves the declaration by the signal's own VHDL
         name (d next to D, output reg ports, generate-block regs), keeps a later time-zero
         assignment, and a named-block local is its own variable.
- TC-04  an unseeded $random draws vvp's sequence (IEEE 1364 internal seed); a seeded call
         inside an expression advances its seed; a system function still replaced by a
         constant ($fopen) says so: "Unsupported system function $fopen replaced by 0 here
         (<file>:<line>)".
- TC-05  $urandom / $urandom_range run (no "foreign function sv_random not found") and draw
         vvp's $urandom sequence; $urandom_range stays in range, bounds swapped as SV says.
- TC-06  every file-I/O and dump task keeps the located "Unsupported system task" comment, and
         an always block left with only such a task still analyses.
- TC-07  a SystemVerilog class is a clean, located "unsupported construct (class)" error (the
         module is deferred), never an assertion abort of the VHDL back end.
- (TC-08) `@(a) stmt' inside a block waits for the change before running stmt; the
         top-level `always @(...)' still runs once at time 0 (open item).

    python3 -m unittest discover -s tests/vamos -p 'test_translator_semantics.py' -v

The source of each class is translated once with bin/iverilog-sv2ghdl (the vcs personality's
translator; VAMOS_IVERILOG / IVERILOG select the iverilog), run under nvc with
bin/vvp-sv2ghdl and, for reference, under vvp; lines starting with "@ " are compared.
Linux (WSL) only: the stack is Linux ELF.
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
    """The environment NvcBackend gives the translator and the run."""
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
    """The `@ ...' lines of a run, in order."""
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("@ ")]


class Translated(unittest.TestCase):
    """Translate the inline SOURCE once per class; run under nvc and vvp on demand."""

    SOURCE = ""
    TOP = "tb"
    EXPECT_TRANSLATION = True

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vamos-sem-")
        cls.env = _stack_env()
        cls.src = os.path.join(cls.tmp, "src.v")
        with open(cls.src, "w") as fh:
            fh.write(cls.SOURCE)
        cls.xlat = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "-g2012",
                         "-s", cls.TOP, cls.src], cls.tmp, cls.env)
        cls.outdir = os.path.join(cls.tmp, "vsim")
        try:
            with open(os.path.join(cls.outdir, "design.vhd"), errors="replace") as fh:
                cls.vhdl = fh.read()
        except OSError:
            cls.vhdl = ""
        try:
            with open(os.path.join(cls.outdir, "iverilog.log"), errors="replace") as fh:
                cls.ivlog = fh.read()
        except OSError:
            cls.ivlog = ""
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

    def assert_like_vvp(self, ordered: bool = True):
        """The tagged lines equal vvp's; `ordered=False' for lines of different processes
        in one time step, whose order Verilog leaves open."""
        n, v = self.nvc(), self.vvp()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        want, got = tagged(v.stdout), tagged(n.stdout)
        self.assertTrue(want, v.stdout)
        if not ordered:
            want, got = sorted(want), sorted(got)
        self.assertEqual(got, want,
                         "\n--- nvc:\n%s%s\n--- vvp:\n%s" % (n.stdout, n.stderr, v.stdout))

    def lines(self, pattern: str) -> List[str]:
        return [ln.strip() for ln in self.vhdl.splitlines() if re.search(pattern, ln)]


# ---------------------------------------------------------------------- TC-01

@needs_stack
class TestCaseIgnoresStrength(Translated):
    """case/casez/casex, case(1'b1) items, a reg copy and an assign copy of pulled nets."""

    SOURCE = """\
`timescale 1ns/1ps
module tb;
  wire n, m;
  pullup (n);
  pulldown (m);
  tri1 t1;
  tri0 t0;
  wire [3:0] bus;
  pullup (bus[2]);
  assign bus[1:0] = 2'b01;
  reg r, q;
  wire o, o2, o3;
  assign o = n;              // a continuous assignment drives its own (strong) strength
  assign o2 = q;             // a reg holds no strength: strong 1 against strong 0 is x
  assign o2 = 1'b0;
  assign o3 = n;
  assign o3 = 1'b0;          // pull 1 copied strong, against strong 0: x
  initial begin
    #1;
    case (n) 1'b1: $display("@ case n 1"); 1'b0: $display("@ case n 0"); default: $display("@ case n default"); endcase
    case (m) 1'b1: $display("@ case m 1"); 1'b0: $display("@ case m 0"); default: $display("@ case m default"); endcase
    case (t1) 1'b1: $display("@ case t1 1"); 1'b0: $display("@ case t1 0"); default: $display("@ case t1 default"); endcase
    case (t0) 1'b1: $display("@ case t0 1"); 1'b0: $display("@ case t0 0"); default: $display("@ case t0 default"); endcase
    case (bus[2]) 1'b1: $display("@ case bus2 1"); default: $display("@ case bus2 default"); endcase
    case (bus[2:0]) 3'b101: $display("@ case bus 101"); default: $display("@ case bus default"); endcase
    casez (n) 1'b1: $display("@ casez n 1"); default: $display("@ casez n default"); endcase
    casex (m) 1'b0: $display("@ casex m 0"); default: $display("@ casex m default"); endcase
    case (1'b1) n: $display("@ case(1) n hit"); default: $display("@ case(1) n default"); endcase
    case (1'b0) m: $display("@ case(0) m hit"); default: $display("@ case(0) m default"); endcase
    r = n;
    case (r) 1'b1: $display("@ case r 1"); 1'b0: $display("@ case r 0"); default: $display("@ case r default"); endcase
    q = t1;
    #1;
    $display("@ o=%b o2=%b o3=%b", o, o2, o3);
    case (o) 1'b1: $display("@ case o 1"); default: $display("@ case o default"); endcase
    $finish;
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_selector_drops_strength(self):
        # every scalar case selector is compared strength-free
        self.assertTrue(self.lines(r"Verilog_Case_Ex\w* := l3d_strengthen\(l3d_weaken\(n\)\);"),
                        self.vhdl)
        self.assertFalse(self.lines(r"^\s*case n is"), self.vhdl)

    def test_reg_copy_and_assign_copy_are_strong(self):
        self.assertTrue(self.lines(r"\br <= l3d_strengthen\(n\);|\br := l3d_strengthen\(n\);"),
                        self.vhdl)
        self.assertTrue(self.lines(r"\bo <= l3d_strengthen\(n\)|:= l3d_strengthen\(n\)"), self.vhdl)


@needs_stack
class TestCaseXZStillExact(Translated):
    """X and Z still match only x and z items; casez/casex don't-cares, ~x (U) is x."""

    SOURCE = """\
`timescale 1ns/1ps
module tb;
  reg s;
  wire w = s;
  wire p;
  pullup (p);
  task show;
    begin
      case (s) 1'b0: $display("@ %0t case %b 0", $time, s); 1'b1: $display("@ %0t case %b 1", $time, s);
               1'bx: $display("@ %0t case %b x", $time, s); 1'bz: $display("@ %0t case %b z", $time, s);
               default: $display("@ %0t case %b default", $time, s); endcase
      casez (s) 1'b1: $display("@ %0t casez %b 1", $time, s); 1'b0: $display("@ %0t casez %b 0", $time, s);
                1'bx: $display("@ %0t casez %b x", $time, s);
                default: $display("@ %0t casez %b default", $time, s); endcase
      casez (s) 1'bx: $display("@ %0t casez2 %b x", $time, s); 1'b?: $display("@ %0t casez2 %b ?", $time, s);
                default: $display("@ %0t casez2 %b default", $time, s); endcase
      casex (s) 1'b1: $display("@ %0t casex %b 1", $time, s); 1'b0: $display("@ %0t casex %b 0", $time, s);
                default: $display("@ %0t casex %b default", $time, s); endcase
      casex (w) 1'b0: $display("@ %0t casexw %b 0", $time, w); 1'bx: $display("@ %0t casexw %b x", $time, w);
                default: $display("@ %0t casexw %b default", $time, w); endcase
      case (~s) 1'b0: $display("@ %0t casenot 0", $time); 1'b1: $display("@ %0t casenot 1", $time);
                1'bx: $display("@ %0t casenot x", $time);
                default: $display("@ %0t casenot default", $time); endcase
      casez (p) s: $display("@ %0t casezp %b s", $time, s);
                default: $display("@ %0t casezp %b default", $time, s); endcase
    end
  endtask
  initial begin
    s = 1'b0; #1 show;
    s = 1'b1; #1 show;
    s = 1'bx; #1 show;
    s = 1'bz; #1 show;
    $finish;
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()


# ---------------------------------------------------------------------- TC-02

_C46 = """\
`timescale 1ns/1ns
module top;
  wire rst_n;
  reg drive, clk;
  reg [3:0] cnt;
  assign rst_n = ~drive;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) cnt <= 0; else cnt <= cnt + 1;
  initial begin
    clk = 0; drive = 1;
    #5 drive = 0;
    repeat (4) begin #5 clk = 1; #5 clk = 0; end
    $display("@ %0t cnt=%0d", $time, cnt);
    drive = 1; #1 $display("@ %0t cnt=%0d", $time, cnt);
    drive = 0; #1;
    repeat (2) begin #5 clk = 1; #5 clk = 0; end
    $display("@ %0t cnt=%0d", $time, cnt);
    $finish;
  end
endmodule
"""


@needs_stack
class TestAsyncResetInShadowDerived(Translated):
    """c46/a1: a derived reset falling one delta after a clock edge woke the flop."""

    SOURCE = _C46
    TOP = "top"

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_template_is_shadow_closed(self):
        self.assertTrue(self.lines(r"variable v_icg2en_snap_rst_n : logic3d"), self.vhdl)
        self.assertTrue(self.lines(r"if not v_nba_loopback then"), self.vhdl)
        self.assertTrue(self.lines(r"elsif rising_edge\(clk\) or \(is_one\(clk\) and "
                                   r"\(not is_one\(v_icg2en_snap_clk\)\)\) then"), self.vhdl)
        self.assertTrue(self.lines(r"if \(rst_n = v_icg2en_snap_rst_n\) and "
                                   r"\(clk = v_icg2en_snap_clk\) then"), self.vhdl)


@needs_stack
class TestAsyncResetAtClockEdges(Translated):
    """a2/a4/a9/a10: testbench resets asserted at clock edges (blocking, nonblocking, a
    2 ns glitch); a12/a13: a loop-back keeps the same-edge capture of an unreset register."""

    SOURCE = """\
`timescale 1ns/1ns
module neg_blocking;            // a2: reset at a negedge (blocking)
  reg rst_n, clk; reg [3:0] cnt;
  always @(posedge clk or negedge rst_n) if (!rst_n) cnt <= 0; else cnt <= cnt + 1;
  initial begin clk = 0; forever #5 clk = ~clk; end
  initial begin
    rst_n = 0; #12 rst_n = 1;
    repeat (4) @(posedge clk);
    @(negedge clk) rst_n = 0;
    #1 $display("@ a2 %0t cnt=%0d", $time, cnt);
    @(negedge clk) rst_n = 1;
    repeat (2) @(posedge clk);
    #1 $display("@ a2 %0t cnt=%0d", $time, cnt);
  end
endmodule
module pos_blocking;            // a4: reset at a posedge (blocking)
  reg rst_n, clk; reg [3:0] cnt;
  always @(posedge clk or negedge rst_n) if (!rst_n) cnt <= 0; else cnt <= cnt + 1;
  initial begin clk = 0; forever #5 clk = ~clk; end
  initial begin
    rst_n = 0; #12 rst_n = 1;
    repeat (4) @(posedge clk);
    @(posedge clk) rst_n = 0;
    #1 $display("@ a4 %0t cnt=%0d", $time, cnt);
  end
endmodule
module pos_nba;                 // a9: active-high reset by a nonblocking assignment
  reg rst, clk; reg [3:0] cnt;
  always @(posedge clk or posedge rst) if (rst) cnt <= 0; else cnt <= cnt + 1;
  initial begin clk = 0; forever #5 clk = ~clk; end
  initial begin
    rst = 1; #12 rst = 0;
    repeat (4) @(posedge clk);
    @(posedge clk) rst <= 1;
    #1 $display("@ a9 %0t cnt=%0d", $time, cnt);
  end
endmodule
module glitch;                  // a10: a 2 ns reset pulse at a negedge (always_ff)
  reg rst_n, clk; reg [3:0] cnt;
  always_ff @(posedge clk or negedge rst_n) if (rst_n == 1'b0) cnt <= '0; else cnt <= cnt + 4'd1;
  initial begin clk = 0; forever #5 clk = ~clk; end
  initial begin
    rst_n = 0; #12 rst_n = 1;
    repeat (4) @(posedge clk);
    @(negedge clk) rst_n = 0;
    #2 rst_n = 1;
    repeat (2) @(posedge clk);
    #1 $display("@ a10 %0t cnt=%0d", $time, cnt);
  end
endmodule
module subset;                  // a12: the reset branch writes a subset of the targets
  reg rst_n, clk; reg [3:0] cnt, data, d;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) cnt <= 0;
    else begin cnt <= cnt + 1; data <= d; end
  initial begin clk = 0; forever #5 clk = ~clk; end
  initial begin
    rst_n = 0; d = 4'd3; #12 rst_n = 1;
    repeat (4) @(posedge clk);
    d = 4'd9;
    @(posedge clk) rst_n = 0;
    #1 $display("@ a12 %0t cnt=%0d data=%0d", $time, cnt, data);
  end
endmodule
module generic;                 // a13: a12 on the generic draw_wait path
  reg rst_n, clk; reg [3:0] cnt, data, d; reg dummy;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) cnt <= 0;
    else begin cnt <= cnt + 1; data <= d; end
    dummy <= 1'b1;
  end
  initial begin clk = 0; forever #5 clk = ~clk; end
  initial begin
    rst_n = 0; d = 4'd3; #12 rst_n = 1;
    repeat (4) @(posedge clk);
    d = 4'd9;
    @(posedge clk) rst_n = 0;
    #1 $display("@ a13 %0t cnt=%0d data=%0d", $time, cnt, data);
  end
endmodule
module tb;
  neg_blocking u2();
  pos_blocking u4();
  pos_nba u9();
  glitch u10();
  subset u12();
  generic u13();
  initial #100 $finish;
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp(ordered=False)

    def test_values(self):
        got = tagged(self.nvc().stdout)
        for want in ("@ a2 51 cnt=0", "@ a4 56 cnt=0", "@ a9 56 cnt=0", "@ a10 66 cnt=2",
                     "@ a12 56 cnt=0 data=9", "@ a13 56 cnt=0 data=9"):
            self.assertIn(want, got)


# ---------------------------------------------------------------------- TC-03

@needs_stack
class TestInitialiserHoist(Translated):
    """Time-zero initializers land on their own declarations."""

    SOURCE = """\
`timescale 1ns/1ns
package p;
  int x;
endpackage

module stim(output reg rst_n = 0, output reg en = 1);
  initial #3 rst_n = 1;
endmodule

module ctr(input clk, output reg [3:0] cnt = 3);
  always @(posedge clk) cnt <= cnt + 1;
endmodule

module tb;
  import p::*;
  reg d = 0, D = 1;
  reg [3:0] a = 4'h5, A = 4'hA;
  integer n = 5, N = 7;
  reg e = 0;
  initial e = 1;                 // a later time-zero assignment still happens
  reg g;
  genvar i;
  for (i = 0; i < 2; i = i + 1) begin : gen
    reg g = 1;
  end
  reg t;
  wire rst_n, en;
  stim s(.rst_n(rst_n), .en(en));
  reg clk = 0;
  always #5 clk = ~clk;
  wire [3:0] c;
  ctr u(.clk(clk), .cnt(c));
  initial x = 5;
  initial begin : blk
    reg t;
    t = 1;
    #1 $display("@ blk.t=%b tb.t=%b", t, tb.t);
  end
  initial begin
    #1 $display("@ d=%b D=%b a=%h A=%h n=%0d N=%0d e=%b", d, D, a, A, n, N, e);
    $display("@ g=%b gen0.g=%b gen1.g=%b x=%0d", g, gen[0].g, gen[1].g, x);
    $display("@ rst_n=%b en=%b c=%h", rst_n, en, c);
    #51 $display("@ c=%h", c);
    $finish;
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_case_colliding_declarations(self):
        self.assertTrue(self.lines(r"^\s*signal d\w* : logic3d := L3D_0;"), self.vhdl)
        self.assertTrue(self.lines(r"^\s*signal D\w* : logic3d := L3D_1;"), self.vhdl)

    def test_block_local_is_its_own_variable(self):
        self.assertTrue(self.lines(r"^\s*variable t_blk : logic3d;"), self.vhdl)


# ---------------------------------------------------------------------- TC-04 / TC-05

@needs_stack
class TestRandom(Translated):
    """An unseeded $random draws vvp's sequence (one internal seed, IEEE 1364 17.9.1 --
    303379748 first) in every context; a seeded call inside an expression advances its
    seed."""

    SOURCE = """module tb;
  integer r1, r2, r3, s, a, b, c, k, ones;
  integer r0 = $random;          // a declaration initial: drawn at time 0
  reg bit1;
  reg [7:0] byte8;
  reg [63:0] wide;
  function integer rnd_mod(input integer m);
    rnd_mod = $random % m;
  endfunction
  initial begin
    $display("@ r0=%0d", r0);
    r1 = $random; r2 = $random; r3 = $random;
    $display("@ r1=%0d r2=%0d r3=%0d", r1, r2, r3);
    bit1 = $random; byte8 = $random; wide = $random;
    $display("@ bit1=%b byte8=%0d wide=%h", bit1, byte8, wide);
    $display("@ direct=%0d", $random);
    ones = 0;
    for (k = 0; k < 100; k = k + 1)
      if ($random & 1) ones = ones + 1;
    $display("@ ones=%0d", ones);
    a = rnd_mod(1000);
    if (a > -1000 && a < 1000) $display("@ fn in range"); else $display("@ fn OUT of range");
    s = 11;
    a = $random(s) % 1000;
    b = $random(s) % 1000;
    c = {$random(s)} % 1000;
    if (a != b && b != c) $display("@ seeded calls advance"); else $display("@ seeded calls repeat");
    $finish;
  end
endmodule
"""

    def test_runs(self):
        n = self.nvc()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertNotIn("not found", n.stdout + n.stderr)

    def test_sequence_like_vvp(self):
        got = tagged(self.nvc().stdout)
        want = tagged(self.vvp().stdout)
        for key in ("@ r0=", "@ r1=", "@ bit1=", "@ direct=", "@ ones="):
            self.assertEqual([g for g in got if g.startswith(key)],
                             [w for w in want if w.startswith(key)], key)
        self.assertIn("@ r0=303379748", got)
        self.assertIn("@ r1=-1064739199 r2=-2071669239 r3=-1309649309", got)

    def test_seeded_calls_advance(self):
        got = tagged(self.nvc().stdout)
        self.assertIn("@ fn in range", got)
        self.assertIn("@ seeded calls advance", got)

    def test_vhdl(self):
        self.assertTrue(self.lines(r"unsigned_to_l3d\(unsigned\(to_signed\(random, 32\)\)\)"),
                        self.vhdl)
        self.assertFalse(self.lines(r"Unsupported system function \$random"), self.vhdl)


@needs_stack
class TestURandom(Translated):
    """$urandom draws vvp's $urandom sequence, from its own design-wide seed (sv_math_pkg's
    sv_urandom, apart from $random's, as vvp keeps two: round 6, L); $urandom_range stays in
    [min, max], bounds swapped, full range too."""

    SOURCE = """module tb;
  int unsigned u1, u2, u3, v1, v2, v3, v4, k, bad;
  initial begin
    u1 = $urandom; u2 = $urandom; u3 = $urandom;
    $display("@ u1=%0d u2=%0d u3=%0d", u1, u2, u3);
    bad = 0;
    for (k = 0; k < 200; k = k + 1) begin
      v1 = $urandom_range(10, 3);
      v2 = $urandom_range(7);
      v3 = $urandom_range(3, 10);
      v4 = $urandom_range(32'hFFFFFFFF, 32'hFFFFFFF0);
      if (v1 < 3 || v1 > 10 || v2 > 7 || v3 < 3 || v3 > 10 || v4 < 32'hFFFFFFF0) bad = bad + 1;
    end
    $display("@ urandom_range out of range: %0d", bad);
    $finish;
  end
endmodule
"""

    def test_runs(self):
        n = self.nvc()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertNotIn("not found", n.stdout + n.stderr)

    def test_urandom_like_vvp(self):
        got = [g for g in tagged(self.nvc().stdout) if g.startswith("@ u1=")]
        want = [w for w in tagged(self.vvp().stdout) if w.startswith("@ u1=")]
        self.assertEqual(got, want)
        self.assertEqual(got, ["@ u1=2450863396 u2=1082744449 u3=75814409"])

    def test_urandom_range_in_range(self):
        self.assertIn("@ urandom_range out of range: 0", tagged(self.nvc().stdout))


@needs_stack
class TestFopenIsTranslated(Translated):
    """$fopen is translated (R6F-01; it was replaced by 0 with a located comment): the
    sv2vhdl runtime's sv_fopen, which returns vvp's descriptor."""

    SOURCE = """\
module tb;
  integer fd;
  initial begin
    fd = $fopen("out.txt", "w");
    $display("@ fd=%0d", fd);
    $finish;
  end
endmodule
"""

    def test_translated(self):
        self.assertFalse(self.lines(r"Unsupported system function \$fopen"), self.vhdl)
        self.assertTrue(self.lines(r'\bsv_fopen\("out\.txt", "vpiConstant", "w", "\$fopen", '
                                   r'"\S+:4"\)'), self.vhdl)

    def test_runs_like_vvp(self):
        n = self.nvc()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertIn("@ fd=-2147483645", tagged(n.stdout))     # 32'h8000_0003
        self.assert_like_vvp()


# ---------------------------------------------------------------------- TC-06

@needs_stack
class TestFileTasksLocated(Translated):
    """Every untranslated dump task keeps its located comment, in any context; the file
    tasks are translated, in any context too: $readmemh / $readmemb / $writememh /
    $writememb on nvc's logic3d_types_pkg (sv_readmem_load, sv_writemem_open), the
    descriptor tasks on its sv_display_pkg (R6F-01)."""

    TASKS = ("$dumpfile", "$dumpvars", "$dumpoff", "$dumpon", "$dumpall", "$dumpflush",
             "$dumplimit")
    READMEM = (r'\bsv_readmem_load\("m\.hex", true, "\$readmemh", "\S+:27", "tb\.mem", 0, 3, 8, '
               r'"vpiConstant", true, 0, false, true, 3, false\);$',
               r'\bsv_readmem_load\("m\.bin", false, "\$readmemb", "\S+:28", "tb\.mem", 0, 3, 8, '
               r'"vpiConstant", false, 0, false, false, 0, false\);$')
    FILE_TASKS = (r'\bsv_fdisplay\(', r'\bsv_fwrite\(', r'\bsv_fstrobe_arm\(',
                  r'\bsv_fmonitor_arm\(', r'\bsv_fclose\(', r'\bsv_fflush\(',
                  r'\bsv_writemem_open\("w\.hex", true, ', r'\bsv_writemem_open\("w\.bin", false, ')

    SOURCE = """\
module tb;
  integer fd, k;
  reg clk = 0;
  reg [7:0] mem [0:3];
  task logit(input integer v);
    $fdisplay(fd, "v=%0d", v);
    $fwriteh(fd, v);
  endtask
  function integer f(input integer a);
    $fwrite(32'h8000_0001, "in f\\n");
    f = a + 1;
  endfunction
  always @(posedge clk) begin
    $fstrobe(fd, "clk");
    $fdisplayb(fd, k);
  end
  always @(k) $fmonitor(fd, "k=%0d", k);
  initial begin
    $dumpfile("x.vcd");
    $dumpvars;
    $dumpoff;
    $dumpon;
    $dumpall;
    $dumpflush;
    $dumplimit(1000);
    fd = $fopen("o.txt");
    $readmemh("m.hex", mem, 0, 3);
    $readmemb("m.bin", mem);
    $writememh("w.hex", mem);
    $writememb("w.bin", mem);
    $fflush(fd);
    logit(3);
    k = f(4);
    #1 clk = 1;
    #1 $fclose(fd);
    $display("@ done k=%0d", k);
    $finish;
  end
endmodule
"""

    def test_every_task_located(self):
        for task in self.TASKS:
            hits = self.lines(r"null;\s+-- Unsupported system task %s omitted here \(\S+:\d+\)$"
                              % re.escape(task))
            self.assertTrue(hits, task)

    def test_readmem_translated(self):
        for pat in self.READMEM:
            self.assertTrue(self.lines(pat), pat)
        self.assertFalse(self.lines(r"Unsupported system task \$readmem"))

    def test_file_tasks_translated(self):
        for pat in self.FILE_TASKS:
            self.assertTrue(self.lines(pat), pat)
        self.assertFalse(self.lines(r"Unsupported system (task|function) \$(f|writemem)"),
                         self.vhdl)

    def test_runs(self):
        n = self.nvc()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertIn("@ done k=5", tagged(n.stdout))


# ---------------------------------------------------------------------- TC-07

class _ClassChecks(object):
    """Mixed into the class cases (not a TestCase itself)."""

    EXPECT_TRANSLATION = False

    def test_clean_located_error(self):
        self.assertNotIn("Assertion", self.ivlog)
        self.assertNotIn("Aborted", self.ivlog)
        self.assertRegex(self.ivlog, r"unsupported construct \(class\) at \S+:\d+")
        self.assertIn("sv2vhdl:deferred", self.vhdl)


@needs_stack
class TestClassIsUnsupported(_ClassChecks, Translated):
    SOURCE = """\
class C;
  int x;
  function new(); x = 5; endfunction
  task bump(); x = x + 1; endtask
endclass
module tb;
  C c;
  initial begin
    c = new;
    c.bump();
    $display("@ x=%0d", c.x);
    $finish;
  end
endmodule
"""

    def test_names_the_line(self):
        self.assertRegex(self.ivlog, r"unsupported construct \(class\) at \S+:9: new\(\) of "
                                     r"SystemVerilog class C")


@needs_stack
class TestClassInModuleIsUnsupported(_ClassChecks, Translated):
    SOURCE = """\
module tb;
  class K;
    int v;
    function int get(); return v; endfunction
  endclass
  K k;
  initial begin
    k = new;
    $display("@ v=%0d", k.get());
    $finish;
  end
endmodule
"""


@needs_stack
class TestClassUnusedTranslates(Translated):
    """A class nobody in the module uses does not stop the module's translation."""

    SOURCE = """\
module tb;
  class K;
    int v;
  endclass
  initial begin
    $display("@ hello");
    $finish;
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()


# ---------------------------------------------------------------------- TC-08 (related)

@needs_stack
class TestEventWaitInsideBlock(Translated):
    """`@(a) stmt' inside a block waits for the change first, then runs stmt (it ran stmt
    first and waited after).  The top-level `always @(a)' form is unchanged (it still runs
    once at time 0: VAMOS_AMS_DESIGN.md open item)."""

    SOURCE = """\
`timescale 1ns/1ns
module tb;
  reg a = 0, b = 0;
  integer n = 0;
  task waitb;
    @(b) n = n + 1;
  endtask
  initial begin
    #5;
    @(a) $display("@ %0t a changed", $time);
    @(b or a) $display("@ %0t b or a changed", $time);
    waitb;
    $display("@ %0t n=%0d", $time, n);
    #20 $finish;
  end
  initial begin
    #10 a = 1;
    #10 b = 1;
    #10 b = 0;
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_values(self):
        self.assertEqual(tagged(self.nvc().stdout),
                         ["@ 10 a changed", "@ 20 b or a changed", "@ 30 n=1"])


# ---------------------------------------------------------------- statement translation

@needs_stack
class TestCompressedShifts(Translated):
    """`>>>=' was silently translated as `+=', `<<=' / `>>=' with a vector count did not
    analyse; an operator with no translation is now an error, never a stand-in."""

    SOURCE = """\
module tb;
  reg signed [7:0] s;
  reg [7:0] u, n;
  initial begin
    s = -8'sd16; s >>>= 2;
    $display("@ s=%0d", s);
    u = 8'hF0; u >>>= 2;
    $display("@ u=%h", u);
    n = 3; u = 8'h01; u <<= n;
    $display("@ u=%h", u);
    u >>= 2;
    $display("@ u=%h", u);
    s = 8'sd5; s <<<= 1;
    $display("@ s=%0d", s);
    $finish;
  end
endmodule
"""

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_values(self):
        self.assertEqual(tagged(self.nvc().stdout),
                         ["@ s=-4", "@ u=3c", "@ u=08", "@ u=02", "@ s=10"])


class TestTagged(unittest.TestCase):
    """The comparison helper itself (no stack needed)."""

    def test_tagged(self):
        self.assertEqual(tagged("x\n@ a\n  @ b\nc @ d\n"), ["@ a", "@ b"])


if __name__ == "__main__":
    unittest.main()
