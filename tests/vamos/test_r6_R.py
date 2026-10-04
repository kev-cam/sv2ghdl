"""Round-6 repair: the gate's failures and the open product bugs (after the T, F, L, W, N and C
merge).  Each class names the item it pins; every one failed before its fix.

Translator (iverilog tgt-vhdl):
  R6R-01  A constant word or bit index ivl ignores -- out of the array, or with x/z bits
          ("ignoring out of bounds l-value array access"): the store is dropped, as vvp drops
          it (array1[0] = 1 on reg array1[2:1] wrote array1[1]; ivtest array_lval_select1/2).
  R6R-02  A bit-select store at a run-time index outside a vector, or outside a memory word,
          is dropped (it stopped the run: "index 6 outside of INTEGER range 3 downto 0"); a
          part-select store at a run-time base keeps its intra-assignment delay
          (`v[k +: 2] <= #3 x' landed 3 units early, deposited at once).
  R6R-03  An intra-assignment event control on a nonblocking assignment (`x <= @(e) v',
          `x <= repeat (n) @(e) v') is a located error: the event control was dropped and the
          value stored at once (ivtest nb_ec_*: wrong values, or a run that never ended).
  R6R-04  $sformat(dest, fmt, ...) is translated (it was dropped: dest kept its old value).
  R6R-05  Bare arguments print as vvp prints them: $time and $simtime in 20 columns, $stime in
          10, $realtime with the scope's precision digits, a real as %#g (2.50000).
  R6R-06  $monitor watches a constant bit or part of a memory word by itself (`array[0][1]'
          printed again on every write to array[0]; ivtest pr2785294).
  R6R-07  %m (and $error's Scope:) in a module instantiated more than once names each
          instance (it named the first one everywhere).
  R6R-08  The undriven bits of a vector net driven only through parts read z (vvp zz0z, the
          translation xx0x), and so does a net with no driver at all.
  R6R-09  A final block is a located error (it ran at time 0).
  R6R-10  $finish(0) / $stop(0) print no end message (nvc's "FINISH called" was left;
          ivtest nested_impl_event1).
  R6R-26  A final block that only closes or flushes files is left out (the end of the run
          does both; vhdlpp's `final $fclose(f)', ivtest vhdl_textio_write).
  R6R-27  A void function is a located error (it crashed the translation inside ivl:
          "Assertion `net' failed"; ivtest function10).

Scripts (bin/):
  R6R-11  sv-normalize leaves a one-line `m[i][j] = v;' to an iverilog that takes the chained
          select (bin/iverilog-sv2ghdl probes it: SV_NORMALIZE_NO_SET_VAL): Rule 6's $set_val
          evaluated v at its own width (`m[i][j] = a + b' lost the carry).  Forced on, the
          $set_val path still works.
  R6R-12  bin/iverilog-sv2ghdl exits 1 when the top module is a deferred stub (outside vamos,
          which reports it itself); it exited 0 and the run ended at once with no output.
  R6R-13  A top module whose name is a VHDL reserved word (pipe, loop, view) elaborates:
          tgt-vhdl renames its entity (pipe_module__50dc); TOP_ENTITY and vcs's elaboration
          used the Verilog name ("cannot find unit WORK.PIPE").
  R6R-14  bin/vvp-sv2ghdl: the testbench's relative data files resolve against the directory
          it was started from (SV2VHDL_FILE_DIR; ivtest pr690, readmemh1, ...).
  R6R-15  bin/sv2vhdl-modules passes no -B of its own for an IVERILOG with no lib/ivl beside
          its bin/ (a wrapper with its own -B): it named the shared build's plugins and
          silently translated with the shared vhdl.tgt.
  R6R-25  regress: the `iverilog' engine puts the build-area iverilog's bin first on PATH
          (vvp_reg.pl calls a bare iverilog; the ivtest/iverilog block failed whole without
          one on PATH).
  R6R-28  sv-normalize: a min:typ:max specparam, hoisted as a localparam, keeps its typ value
          (a syntax error before; ivtest pr1587634).
  R6R-29  A module commented out with /* ... */ over several lines is no module to the top
          guess (iverilog-sv2ghdl) and the module list (sv2vhdl-modules) (pr1587634).

vamos:
  R6R-16  vcs-ams: $dumpfile/$dumpvars are vamos's, as in a plain compile (they were "not
          translated" errors that refused the design); an analog output that no digital code
          reads keeps an A2D when the waves record it (vcs.dump_covers; it dumped z).
  R6R-17  .option delmax sets the maximum time step of the analysis vamos synthesises for a
          netlist with no .tran (it was ignored with a warning).
  R6R-18  Subckt.spelling: each subckt keeps its own header spelling of a port.
  R6R-19  The +vcs+dumpvars part of a compile note that a design's $dumpvars carries is
          labelled as the option's.
  R6R-20  Run-time messages with a location ($warning/$error, the file tasks' vvp messages)
          name the user's file:line, not simv.daidir/nvc/_norm.sv:<n> (OutputFilter, through
          the compile's line map simv.daidir/vamos.srclines.json).
  R6R-30  The design's timing that is not simulated is said: specify path delays, timing
          checks, $sdf_annotate (verilog_ports.timing_omissions; it was silent).
  R6R-31  A single top-level module's input ports read 0 under nvc, where VCS leaves them
          undriven (z): said (vcs.undriven_top_inputs; it was silent).

Translator + nvc:
  R6R-21  A nonblocking assignment in a process that deposits (an initial block, an always
          block that waits in its body) to a variable that process alone writes is a `<=':
          drawn as a deposit it landed at once (`w <= 7; $display(w)' printed 7).  nvc
          rt/model.c sched_driver no longer elides a same-value driver update after a
          deposit changed the signal (`w = 3; ... w <= 7;' left w at 3).

nvc:
  R6R-22  Ctrl-C of a co-simulation while the digital side runs a process: no "** Fatal:
          <t>: interrupted [in process ...]" line ahead of the co-simulation's own end line
          (rt/model.c model_interrupt_quiet, cosim.c).
  R6R-23  Strength drivers on the words of a memory of multi-bit nets (ivtest pr1703346)
          read xx with exit 0: the kernel net solver declined the "(W)(B)" element nets
          (vhpi-model.c nvc_vhpi_stitch_net).  It takes them now; the run stops in
          l3d_resolve as for 1-bit words -- loud, still open.
  R6R-24  A long run of std_logic_1164/numeric_std vector "xor" in one evaluation crashed
          (SIGSEGV in ieee_xor_vector_sse41): the eval arena is 16-byte aligned
          (rt/mspace.c, jit-intrin.c __tlab_overflow).

    cd tests/vamos && python3 -m unittest test_r6_R -v

The translator classes translate their sources with bin/iverilog-sv2ghdl (VAMOS_IVERILOG /
IVERILOG select the iverilog; a private plugin is tested through a wrapper with its own -B),
run them under nvc with bin/vvp-sv2ghdl and, for reference, under vvp, and compare the lines
that start with "@ ".  Those and the end-to-end classes run on Linux (WSL) only: the stack is
Linux ELF.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

from vamos_testlib import ROOT, have_vacask, needs_stack

from vamos import tools

BIN = os.path.join(ROOT, "bin")
SHIMS = os.path.join(ROOT, "shims")


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
    env.pop("VAMOS_STACK", None)
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
    DATA = {}             # file name -> text: data files the run reads
    TOP = "tb"
    ENV = {}              # extra environment for the translation
    EXPECT_TRANSLATION = True

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vamos-r6r-")
        cls.env = _stack_env()
        cls.env.update(cls.ENV)
        cls.files = []
        for name, text in cls.DATA.items():
            with open(os.path.join(cls.tmp, name), "w") as fh:
                fh.write(text)
        for name, text in cls.SOURCES.items():
            path = os.path.join(cls.tmp, name)
            with open(path, "w") as fh:
                fh.write(text)
            cls.files.append(path)
        cls.xlat = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "-g2012",
                         "-s", cls.TOP] + cls.files, cls.tmp, cls.env)
        out = os.path.join(cls.tmp, "vsim")
        cls.vhdl = cls.ivlog = cls.norm = ""
        for attr, fname in (("vhdl", "design.vhd"), ("ivlog", "iverilog.log"),
                            ("norm", "_norm.sv")):
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

    def assert_like_vvp(self, ordered=True, lines=tagged):
        n, v = self.nvc(), self.vvp()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        want, got = lines(v.stdout), lines(n.stdout)
        self.assertTrue(want, v.stdout)
        if not ordered:
            want, got = sorted(want), sorted(got)
        self.assertEqual(got, want, "\n--- nvc:\n%s%s\n--- vvp:\n%s"
                         % (n.stdout, n.stderr, v.stdout))


class LoudError(Probe):
    """A construct with no translation: a located error, the module deferred."""

    EXPECT_TRANSLATION = False

    def assert_error(self, text):
        said = self.xlat.stdout + self.xlat.stderr + self.ivlog
        self.assertIn(text, said)
        self.assertRegex(said, r"\.s?v:\d+: [^\n]*" + re.escape(text))
        self.assertIn("sv2vhdl:deferred", self.vhdl)


# ---------------------------------------------------------------------- R6R-01

@needs_stack
class TestConstantIndexIgnored(Probe):
    """Constant word indices ivl ignores (outside the array, x/z) on the left of blocking,
    nonblocking and concatenated assignments, for a reg and a real memory: vvp drops each
    store (array1[0] = 1 on reg array1[2:1] wrote array1[1])."""

    SOURCES = {"src.v": """\
module tb;
  reg  array1 [2:1];
  reg  [3:0] m [1:0];
  real r [2:1];
  reg  [3:0] lo;
  initial begin
    array1[1] = 1'b0; array1[2] = 1'b0;
    array1[0] = 1'b1;
    array1[3] = 1'b1;
    array1['bx] = 1'b1;
    $display("@ a %b %b", array1[2], array1[1]);
    m[0] = 4'h0; m[1] = 4'h0;
    m[2] <= 4'h5;
    m['bz] <= 4'h6;
    #1 $display("@ m %h %h", m[1], m[0]);
    {m[3], lo} = 8'hA7;
    $display("@ c %h %h lo=%h", m[1], m[0], lo);
    r[1] = 0.0; r[2] = 0.0;
    r[0] = 1.5;
    r[3] = 2.5;
    $display("@ r %0g %0g", r[2], r[1]);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


# ---------------------------------------------------------------------- R6R-02

@needs_stack
class TestRuntimeBitStoreOutside(Probe):
    """Run-time bit and part offsets outside a vector (descending, ascending, offset ranges)
    or outside a memory word, blocking and nonblocking, with and without a delay."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ns
module tb;
  reg [3:0] v;
  reg [0:3] a;
  reg [7:4] b;
  reg [7:0] m [0:1];
  integer k;
  initial begin
    v = 4'b0000; a = 4'b0000; b = 4'b0000;
    m[0] = 0; m[1] = 0;
    k = 6;  v[k] = 1'b1;
    k = -1; v[k] = 1'b1;
    k = 2;  v[k] = 1'b1;
    $display("@ %0t v=%b", $time, v);
    k = 4;  a[k] = 1'b1;
    k = 1;  a[k] = 1'b1;
    $display("@ %0t a=%b", $time, a);
    k = 3;  b[k] = 1'b1;
    k = 8;  b[k] = 1'b1;
    k = 5;  b[k] = 1'b1;
    $display("@ %0t b=%b", $time, b);
    k = 1;  v[k] <= 1'b1;
    k = 9;  v[k] <= 1'b1;
    #1 $display("@ %0t v=%b", $time, v);
    k = 3;  v[k] <= #5 1'b0;
    k = 0;  v[k+:2] <= #3 2'b11;
    #1 $display("@ %0t v=%b", $time, v);
    #3 $display("@ %0t v=%b", $time, v);
    #3 $display("@ %0t v=%b", $time, v);
    k = 1; m[k][k] = 1'b1;
    k = 9; m[0][k] = 1'b1;
    k = 9; m[k][2] = 1'b1;
    k = 6; m[1][k +: 4] = 4'hF;
    k = -2; m[0][k +: 4] = 4'hF;
    $display("@ %0t m=%b %b", $time, m[0], m[1]);
    m[0][1] <= 1'b1;
    k = 12; m[1][k] <= 1'b1;
    #1 $display("@ %0t m=%b %b", $time, m[0], m[1]);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_bit_store_guarded(self):
        self.assertTrue([ln for ln in self.vhdl.splitlines() if "OOB_BIdx_" in ln], self.vhdl)
        self.assertTrue([ln for ln in self.vhdl.splitlines() if "OOB_EIdx_" in ln], self.vhdl)


# ---------------------------------------------------------------------- R6R-03

@needs_stack
class TestNbaEventControlIsLocated(LoudError):
    """`x <= repeat (n) @(posedge clk) v' (ivtest nb_ec_array_pv2): a located error, and
    bin/iverilog-sv2ghdl exits 1 for the deferred top (R6R-12)."""

    SOURCES = {"src.v": """\
module tb;
  reg clk = 0;
  reg [7:0] r;
  integer n;
  always #10 clk = ~clk;
  initial begin
    n = 2;
    r <= repeat (n) @(posedge clk) 8'h5a;
    #100 $display("@ r=%h", r);
    $finish(0);
  end
endmodule
"""}

    def test_located_error(self):
        self.assert_error("no VHDL translation for an intra-assignment event control on a "
                          "nonblocking assignment")

    def test_compile_fails(self):
        self.assertEqual(self.xlat.returncode, 1, self.xlat.stdout + self.xlat.stderr)
        self.assertIn("the top module tb was not translated", self.xlat.stderr)


# ---------------------------------------------------------------------- R6R-04

@needs_stack
class TestSformat(Probe):
    """$sformat into a reg, a real and vector word at run-time indices (ivtest array_select_a)."""

    SOURCES = {"src.v": """\
module tb;
  real rarr [1:0];
  reg [2:0] arr [1:0];
  reg [8*12:1] res;
  integer index;
  initial begin
    rarr[0] = 1.0; rarr[1] = 2.25; arr[0] = 1; arr[1] = 6;
    index = 1;
    $sformat(res, "%3.1f", rarr[index]);
    $display("@ [%0s]", res);
    $sformat(res, "%3b|%0d|%h", arr[index], index, 8'hA5);
    $display("@ [%0s]", res);
    $sformat(res, "no args");
    $display("@ [%0s]", res);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


# ---------------------------------------------------------------------- R6R-05

@needs_stack
class TestBareArguments(Probe):
    """Bare arguments of $display/$write/$strobe: time functions and reals as vvp prints them."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ps
module tb;
  integer i;
  real x, z;
  initial begin
    i = -7; x = 2.5; z = 0.0;
    #3.25;
    $display("@ [", $time, "][", $stime, "][", $simtime, "][", $realtime, "]");
    $display("@ [", x, "][", z, "][", -x, "][", x * 2.0, "][", 1.0e20, "][", 1.5, "]");
    $display("@ [", i, "][", $time + 1, "]");
    $strobe("@ s[", x, "|", $time, "]");
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


# ---------------------------------------------------------------------- R6R-06

@needs_stack
class TestMonitorWordBit(Probe):
    """$monitor of bits and parts of a vector and of a memory word (ivtest pr2785294): a line
    only when a watched bit changes."""

    SOURCES = {"src.v": """\
module tb;
  reg [7:0] array [1:0];
  reg [7:0] bs;
  integer idx;
  initial begin
    bs = 8'b0;
    array[0] = 8'b0;
    $monitor("@ ", $time, " BS = ", bs[1], ", AR = ", array[0][1], ", AP = ", array[0][3:2]);
    for (idx = 0; idx < 8; idx = idx + 1)
      #1 bs[idx] = 1'b1;
    for (idx = 0; idx < 8; idx = idx + 1)
      #1 array[0][idx] = 1'b1;
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


# ---------------------------------------------------------------------- R6R-07

@needs_stack
class TestHierNameInstances(Probe):
    """%m in modules instantiated several times: directly, under another module instantiated
    twice, in a generate loop, in a named block and a task, and $error's Scope: line.
    Processes of different instances at one time are unordered in Verilog: compared sorted."""

    SOURCES = {"src.v": """\
module leaf;
  task show;
    $display("@ task %m");
  endtask
  initial begin : blk
    #1 $display("@ leaf %m");
    show;
  end
endmodule
module mid;
  leaf l();
  initial #2 $display("@ mid %m");
endmodule
module pleaf #(parameter P = 1) ();
  initial #4 $display("@ pleaf%0d %m", P);
endmodule
module tb;
  mid a(), b();
  leaf u1(), u2();
  genvar g;
  generate for (g = 0; g < 2; g = g + 1) begin : gen
    leaf x();
  end endgenerate
  pleaf #(1) p1(), p2();
  pleaf #(2) p3(), p4();
  initial #3 $display("@ tb %m");
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp(ordered=False)

    def test_scope_line(self):
        # $error's "Scope:" line names the instance too (vvp's second line of $error)
        src = os.path.join(self.tmp, "err.v")
        with open(src, "w") as fh:
            fh.write("module sub; initial #1 $error(\"e\"); endmodule\n"
                     "module tb; sub s1(), s2(); endmodule\n")
        x = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "esim", "-g2012", "-s", "tb", src],
                 self.tmp, self.env)
        self.assertEqual(x.returncode, 0, x.stdout + x.stderr)
        r = _run([os.path.join(BIN, "vvp-sv2ghdl"), "esim"], self.tmp, self.env)
        scopes = sorted(re.findall(r"Scope: (\S+)", r.stdout))
        self.assertEqual(scopes, ["tb.s1", "tb.s2"], r.stdout + r.stderr)


# ---------------------------------------------------------------------- R6R-08

@needs_stack
class TestUndrivenBitsReadZ(Probe):
    """Vector nets driven through parts only, a pull on one bit, a tran on one bit, and a net
    with no driver: the undriven bits read z (L's repro partial_vector_undriven_bits.v)."""

    SOURCES = {"src.v": """\
module tb;
  wire [3:0] w1;
  pullup p1 (w1[0]);
  wire [3:0] w2;
  assign w2[1] = 1'b0;
  assign w2[3] = 1'b1;
  wire [3:0] w3; wire n3; reg v3;
  assign n3 = v3;
  tran t3 (w3[1], n3);
  wire [3:0] w5;
  wire [7:0] w6;
  assign w6[5:2] = 4'hA;
  initial begin
    v3 = 1;
    #1 $display("@ w1=%b w2=%b w3=%b n3=%b w5=%b w6=%b", w1, w2, w3, n3, w5, w6);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


# ---------------------------------------------------------------------- R6R-09

@needs_stack
class TestFinalBlockIsLocated(LoudError):
    """`final $display(n)' printed the value n had at time 0: a located error now."""

    SOURCES = {"src.v": """\
module tb;
  integer n = 0;
  initial begin
    #5 n = 3;
  end
  final $display("@ final n=%0d", n);
endmodule
"""}

    def test_located_error(self):
        self.assert_error("no VHDL translation for a final block")


@needs_stack
class TestFinalThatClosesFiles(Probe):
    """R6R-26: a final block that only closes or flushes files is left out: the end of the
    run does both (vhdlpp writes `final $fclose(f)' for a VHDL file object, so ivtest
    vhdl_textio_write was a located error after R6R-09; before it, the block ran at time 0
    and closed the file before anything was written)."""

    SOURCES = {"src.v": """\
module tb;
  integer fd, i;
  initial begin
    fd = $fopen("r6r_final.txt", "w");
    for (i = 0; i < 3; i = i + 1)
      #1 $fdisplay(fd, "line %0d", i);
    $display("@ wrote");
  end
  final begin
    $fflush(fd);
    $fclose(fd);
  end
endmodule
"""}

    def test_file_written_like_vvp(self):
        path = os.path.join(self.tmp, "r6r_final.txt")
        n = self.nvc()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertEqual(tagged(n.stdout), ["@ wrote"], n.stdout + n.stderr)
        with open(path) as fh:
            got = fh.read()
        os.remove(path)
        v = self.vvp()
        with open(path) as fh:
            want = fh.read()
        self.assertEqual(want, "line 0\nline 1\nline 2\n")
        self.assertEqual(got, want)


@needs_stack
class TestVoidFunctionIsLocated(LoudError):
    """R6R-27: a SystemVerilog void function crashed the translation inside ivl
    (ivl_signal_data_type: Assertion `net' failed; ivtest function10, which passed only as
    long as bin/iverilog-sv2ghdl exited 0 for a module it could not translate): a located
    error now."""

    SOURCES = {"src.v": """\
module main;
  integer n = 0;
  function void bump(input integer k);
    n = n + k;
  endfunction
  initial begin
    bump(3);
    $display("@ n=%0d", n);
  end
endmodule
"""}
    TOP = "main"

    def test_located_error(self):
        self.assert_error("no VHDL translation for the void function bump: write it as a task")
        self.assertNotIn("Assertion", self.xlat.stdout + self.xlat.stderr + self.ivlog)


# ---------------------------------------------------------------------- R6R-10

@needs_stack
class TestQuietFinish(Probe):
    """$finish(0) prints no end message (ivtest nested_impl_event1); $finish(1) still does,
    as FINISH called."""

    SOURCES = {"src.v": """\
module tb;
  initial begin
    #1 $display("@ before");
    $write("@ pending");
    #1 $finish(0);
  end
endmodule
""", "one.v": """\
module one;
  initial #1 $finish(1);
endmodule
"""}

    def test_like_vvp(self):
        n, v = self.nvc(), self.vvp()
        self.assertEqual(n.returncode, 0, n.stdout + n.stderr)
        self.assertEqual(n.stdout.strip().splitlines(), v.stdout.strip().splitlines(),
                         n.stdout + n.stderr)
        self.assertNotIn("FINISH called", n.stdout)
        self.assertNotIn("quiet end", n.stdout + n.stderr)

    def test_level_one_still_noted(self):
        x = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "osim", "-g2012", "-s", "one",
                  os.path.join(self.tmp, "one.v")], self.tmp, self.env)
        self.assertEqual(x.returncode, 0, x.stdout + x.stderr)
        r = _run([os.path.join(BIN, "vvp-sv2ghdl"), "osim"], self.tmp, self.env)
        self.assertIn("FINISH called", r.stdout)

    def test_vcs(self):
        # vamos: $finish(0) prints no end message either (VCS prints none); the run still
        # ends there (the footer's time is 2ms: no timescale)
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        d = os.path.join(self.tmp, "vcs")
        os.makedirs(d)
        shutil.copy(os.path.join(self.tmp, "src.v"), d)
        c = _run(["vcs", "-sverilog", "src.v"], d, env)
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        r = _run(["./simv"], d, env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("@ pending", r.stdout)
        self.assertNotIn("FINISH called", r.stdout + r.stderr)
        self.assertNotIn("quiet end", r.stdout + r.stderr)
        self.assertIn("Time: 2ms", r.stdout)


# ---------------------------------------------------------------------- R6R-21

@needs_stack
class TestNbaAfterDeposit(Probe):
    """A nonblocking assignment to a variable its process (the variable's only writer) had
    deposited (written with a blocking assignment, or at time 0): drawn as a deposit, it
    landed at once -- `w <= 7; $display(w)' printed 7 where vvp prints the old value.  Now a
    `<=', which nvc applies even when the driver already held the value and a deposit had
    changed the signal since (`w = 3; ... w <= 7;' left w at 3: nvc rt/model.c sched_driver's
    same-value elision)."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ns
module tb;
  reg [3:0] w, u;
  reg [7:0] m [0:1];
  integer k;
  initial begin
    w = 0; u = 0; m[0] = 0;
    #1 w <= 7;
    $display("@ %0t a w=%0d", $time, w);
    #1 w = 3;
    #1 w <= 7;
    $display("@ %0t b w=%0d", $time, w);
    #1 $display("@ %0t c w=%0d", $time, w);
    k = 1;
    u[k] <= 1'b1;
    m[0][k] <= 1'b1;
    $display("@ %0t d u=%0d m0=%0d", $time, u, m[0]);
    #1 $display("@ %0t e u=%0d m0=%0d", $time, u, m[0]);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()


# ---------------------------------------------------------------------- R6R-11

SET_VAL_SOURCE = """\
module tb;
  reg [7:0] m [0:1];
  reg [3:0] a, b;
  reg [3:0][7:0] p [0:1];
  reg [8:1] v1;
  reg [3:0][1:0] v3;
  integer i, j, k;
  initial begin
    m[0] = 0; m[1] = 0; p[0] = 0; p[1] = 0; v1 = 0; v3 = 0;
    a = 4'hF; b = 4'h3;
    i = 1; j = 4;
    m[i][j +: 8] = a + b;
    m[i][2] = a + b;
    $display("@ m1=%b", m[1]);
    i = 0; j = 2; k = 5;
    p[i][j][k] = 1'b1;
    j = 3;
    p[i][j] = a + b;
    $display("@ p0=%h", p[0]);
    j = 8; v1[j] = 1'b1;
    i = 2; j = 1; v3[i][j] = 1'b1;
    $display("@ v1=%b v3=%b", v1, v3);
  end
endmodule
"""


@needs_stack
class TestChainedSelectWidth(Probe):
    """`p[i][j] = a + b' with 4-bit a, b and an 8-bit select keeps the carry (vvp p0=12...):
    sv-normalize's Rule 6 made it $set_val(p, i, j, a + b), evaluated at 4 bits (p0=02...)."""

    SOURCES = {"src.v": SET_VAL_SOURCE}

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_no_set_val(self):
        self.assertNotIn("$set_val", self.norm)


@needs_stack
class TestSetValRewriteForced(Probe):
    """With Rule 6 forced on (SV_NORMALIZE_NO_SET_VAL=0, as for an iverilog that refuses the
    chained select), the one-line stores become $set_val and translate (R6T-09) -- the
    stores whose value is no wider than its select."""

    ENV = {"SV_NORMALIZE_NO_SET_VAL": "0"}
    SOURCES = {"src.v": """\
module tb;
  reg [7:0] m [4:7];
  reg [3:0][1:0] v3;
  integer i, j;
  initial begin
    for (i = 4; i < 8; i = i + 1) m[i] = 0;
    v3 = 0;
    i = 5; j = 3;
    m[i][j] = 1'b1;
    i = 3; j = 1;
    m[i][j] = 1'b1;
    i = 2; j = 1;
    v3[i][j] = 1'b1;
    #1 $display("@ m %b %b %b %b v3 %b", m[4], m[5], m[6], m[7], v3);
  end
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_set_val_used(self):
        self.assertIn("$set_val", self.norm)
        self.assertRegex(self.vhdl, r"SetVal_Idx_\d+ :=")


@needs_stack
class TestSpecparamMinTypMax(Probe):
    """R6R-28: sv-normalize keeps a specify block's specparams as localparams; a min:typ:max
    one (`specparam t = 1:2:3;') was a syntax error there ("Invalid module item"; ivtest
    pr1587634): it keeps the typ value, the one iverilog's default -Ttyp gives."""

    SOURCES = {"src.v": """\
`timescale 1ns/1ns
module tb;
  reg a = 0;
  wire z;
  mycell u (z, a);
  initial #2 $display("@ z=%b", z);
endmodule
module mycell (output z, input a);
  buf (z, a);
  specify
    specparam t = 1:2:3, s = (4:5:6);
    (a => z) = (t, t);
  endspecify
  initial #1 $display("@ t=%0d s=%0d", t, s);
endmodule
"""}

    def test_like_vvp(self):
        self.assert_like_vvp()

    def test_typ_kept(self):
        self.assertRegex(self.norm, r"localparam t = 2, s = \(5\);")


# ---------------------------------------------------------------------- R6R-12..15

class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vamos-r6r-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, text):
        path = os.path.join(self.tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def read(self, name):
        with open(os.path.join(self.tmp, name), errors="replace") as fh:
            return fh.read()


@needs_stack
class TestDeferredTopExitStatus(TempDir):
    """A top module that does not translate: iverilog-sv2ghdl exits 1 (it exited 0, and the
    run of the empty stub printed nothing); under vamos (VAMOS_STACK) it stays 0 and vamos
    reports the stub, quoting iverilog's reason."""

    SRC = "module tb;\n  initial #1 $display(\"x\");\n  final $display(\"y\");\nendmodule\n"

    def test_exit_status(self):
        self.write("t.v", self.SRC)
        env = _stack_env()
        r = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "-g2012", "t.v"],
                 self.tmp, env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("the top module tb was not translated", r.stderr)
        env["VAMOS_STACK"] = "vcs"
        r = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim2", "-g2012", "t.v"],
                 self.tmp, env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("sv2vhdl:deferred", self.read("vsim2/design.vhd"))

    def test_vcs_reports_the_reason(self):
        self.write("t.v", self.SRC)
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        c = _run(["vcs", "-sverilog", "t.v"], self.tmp, env)
        said = c.stdout + c.stderr
        self.assertEqual(c.returncode, 1, said)
        self.assertIn("vamos: error: sv2ghdl could not translate top module 'tb'", said)
        self.assertIn("t.v:3: no VHDL translation for a final block", said)


@needs_stack
class TestReservedWordTop(TempDir):
    """Top modules named pipe and loop (VHDL reserved words): bin/iverilog-sv2ghdl's
    TOP_ENTITY and vcs's elaboration use the renamed entity; several tops too."""

    def test_ivtest_flow(self):
        self.write("t.v", "module pipe; initial $display(\"@ hi %m\"); endmodule\n")
        env = _stack_env()
        x = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "-g2012", "t.v"],
                 self.tmp, env)
        self.assertEqual(x.returncode, 0, x.stdout + x.stderr)
        md = self.read("vsim/_metadata")
        self.assertNotIn('TOP_ENTITY="pipe"', md)
        self.assertRegex(md, r'TOP_ENTITY="pipe_\w+"')
        r = _run([os.path.join(BIN, "vvp-sv2ghdl"), "vsim"], self.tmp, env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("@ hi pipe", r.stdout)

    def vcs(self, *args):
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        return _run(["vcs"] + list(args), self.tmp, env), env

    def test_vcs(self):
        self.write("t.v", "module loop; sub s(); initial #1 $display(\"@ loop %m\"); endmodule\n"
                          "module sub; initial $display(\"@ sub %m\"); endmodule\n")
        c, env = self.vcs("t.v")
        self.assertEqual(c.returncode, 0, c.stdout)
        r = _run(["./simv"], self.tmp, env)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(tagged(r.stdout), ["@ sub loop.s", "@ loop loop"], r.stdout)

    def test_vcs_two_tops(self):
        self.write("t.v", "module view; initial #1 $display(\"@ view %m\"); endmodule\n"
                          "module other; initial #2 $display(\"@ other %m\"); endmodule\n")
        c, env = self.vcs("t.v")
        self.assertEqual(c.returncode, 0, c.stdout)
        r = _run(["./simv"], self.tmp, env)
        self.assertEqual(tagged(r.stdout), ["@ view view", "@ other other"], r.stdout)


@needs_stack
class TestCommentedOutModule(TempDir):
    """R6R-29: a module commented out with /* ... */ over several lines is no module.  The
    top guess of bin/iverilog-sv2ghdl and the module list of bin/sv2vhdl-modules took it for
    one (ivtest pr1587634: `top', which iverilog cannot find, became the top, and the
    compile failed once a deferred top fails it, R6R-12)."""

    def test_top_found(self):
        self.write("t.v", "/*\nmodule top();\n  inv1 g(out, in);\nendmodule\n*/\n"
                          "module inv1 (z, a);\n  output z;\n  input a;\n  not g1(z, a);\n"
                          "  initial #1 $display(\"@ inv1 %m\");\nendmodule\n")
        env = _stack_env()
        r = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "t.v"], self.tmp, env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        log = self.read("vsim/iverilog.log")
        self.assertIn("-s inv1: translated", log)
        self.assertNotIn("-s top:", log)
        n = _run([os.path.join(BIN, "vvp-sv2ghdl"), "vsim"], self.tmp, env)
        self.assertEqual(tagged(n.stdout), ["@ inv1 inv1"], n.stdout + n.stderr)


@needs_stack
class TestVvpSv2ghdlFileDir(TempDir):
    """bin/vvp-sv2ghdl runs nvc in the vsim directory: $readmemh's relative name resolved
    there ("Unable to open ivltests/pr690.dat"); it resolves against where it was started."""

    def test_relative_readmem(self):
        self.write("data/words.hex", "0a\n0b\n")
        self.write("t.v", "module tb; reg [7:0] m [0:1];\n"
                          "  initial begin $readmemh(\"data/words.hex\", m);\n"
                          "    $display(\"@ %h %h\", m[0], m[1]); end\nendmodule\n")
        env = _stack_env()
        x = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "-g2012", "t.v"],
                 self.tmp, env)
        self.assertEqual(x.returncode, 0, x.stdout + x.stderr)
        r = _run([os.path.join(BIN, "vvp-sv2ghdl"), "vsim"], self.tmp, env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(tagged(r.stdout), ["@ 0a 0b"], r.stdout + r.stderr)


@needs_stack
class TestModulesWrapperB(TempDir):
    """sv2vhdl-modules with IVERILOG a wrapper script that passes its own -B (no lib/ivl
    beside its bin/): the wrapper's plugins are used.  The wrapper here names a plugin
    directory whose vhdl.conf points at a vhdl.tgt that writes a marker; sv2vhdl-modules's
    own -B (the shared build's) overrode it."""

    def test_wrapper_plugins_used(self):
        real = tools.find_real("iverilog")
        if not real:
            self.skipTest("no iverilog")
        libdir = None
        for cand in (os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(real))),
                                  "lib", "ivl"),
                     "/usr/local/src/iverilog/_install/lib/ivl"):
            if os.path.isfile(os.path.join(cand, "vhdl.conf")):
                libdir = cand
                break
        if libdir is None:
            self.skipTest("no ivl plugin directory")
        # the plugin directory (links) with a vhdl target that is null.tgt: nothing is written
        mine = os.path.join(self.tmp, "myivl")
        os.makedirs(mine)
        for f in os.listdir(libdir):
            if f != "vhdl.conf":
                os.symlink(os.path.join(libdir, f), os.path.join(mine, f))
        with open(os.path.join(libdir, "vhdl.conf")) as fh:
            conf = fh.read()
        with open(os.path.join(mine, "vhdl.conf"), "w") as fh:
            fh.write(conf.replace("vhdl.tgt", "null.tgt"))
        wrapper = self.write("wbin/iverilog", "#!/bin/sh\nexec %s -B%s \"$@\"\n" % (real, mine))
        os.chmod(wrapper, 0o755)
        self.write("t.v", "module t; initial $display(\"x\"); endmodule\n")
        env = _stack_env()
        env["IVERILOG"] = wrapper
        env.pop("IVL_BUILD_LIB", None)
        r = _run([os.path.join(BIN, "sv2vhdl-modules"), "t.v", "-o", "out.vhd"], self.tmp, env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # null.tgt writes no VHDL: the module is a deferred stub (the shared vhdl.tgt, which
        # the old -B named, translated it)
        self.assertIn("sv2vhdl:deferred", self.read("out.vhd"))
        self.assertIn("deferred (no VHDL written)", self.read("iverilog.log"))


class TestIverilogBlockPath(TempDir):
    """R6R-25 (R6C-03, harness): regress's `iverilog' engine puts the build-area iverilog's
    bin first on PATH.  vvp_reg.pl calls a bare `iverilog'/`vvp', so on a box with none on
    PATH the ivtest/iverilog block, which `regress list' shows ready, failed whole ("Failed
    to get version from iverilog -V output"); elsewhere it ran whatever copy PATH held."""

    PERL = r'''
use strict;
use warnings;
use Regress::Block;
no warnings qw(redefine once);
*Regress::Adapter::Ivtest::run = sub {
    my ($class, $block, %opt) = @_;
    print "PATH_PREPEND=", ($block->{path_prepend} // ''), "\n";
    print "IVERILOG=", ($block->{env}{IVERILOG} // ''), "\n";
    return { exit_code => 0, results => [] };
};
Regress::Block::dispatch(Regress::Block::get('ivtest/iverilog'));
'''

    def test_build_area_bin_first(self):
        perl = shutil.which("perl")
        if not perl:
            self.skipTest("no perl")
        root = os.path.join(self.tmp, "root")
        bindir = os.path.join(root, "iverilog", "_install", "bin")
        for tool in ("iverilog", "vvp"):
            os.chmod(self.write(os.path.join("root", "iverilog", "_install", "bin", tool),
                                "#!/bin/sh\nexit 0\n"), 0o755)
        env = dict(os.environ)
        env["SV2GHDL_SRC_ROOT"] = root
        for var in ("IVERILOG", "VVP"):
            env.pop(var, None)
        r = _run([perl, "-I" + os.path.join(ROOT, "regress", "lib"), "-e", self.PERL],
                 self.tmp, env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = dict(ln.split("=", 1) for ln in r.stdout.splitlines() if "=" in ln)
        self.assertEqual(got.get("IVERILOG"), os.path.join(bindir, "iverilog"), r.stdout)
        self.assertEqual(got.get("PATH_PREPEND", "").split(":")[0], bindir, r.stdout)


# ---------------------------------------------------------------------- R6R-16..19 (vamos)

@needs_stack
@unittest.skipUnless(have_vacask(), "needs VACASK (Linux/WSL)")
class TestAmsDumpvars(TempDir):
    """vcs-ams with $dumpfile/$dumpvars in the design (they were "not translated" errors that
    refused it): compiled, the VCD written; tb.seen, an analog output nothing digital reads,
    gets its A2D because the waves record it (it stayed z: no A2D without a reader)."""

    def test_dumpvars(self):
        from test_r6_W import CELL_SP, CELL_TB, Vcd
        self.write("tb.sv", CELL_TB.replace(
            "initial #1000 $finish;",
            'initial begin $dumpfile("w.vcd"); $dumpvars(1, tb); end\n'
            "  initial #100 $finish;"))
        self.write("rc.sp", CELL_SP)
        self.write("vcsAD.init", "choose xa rc.sp;\n")
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        env.pop("VAMOS_ANALOG", None)
        c = _run(["vcs-ams", "-sverilog", "tb.sv", "--vamos-analog=vacask"], self.tmp, env,
                 timeout=1200)
        said = c.stdout + c.stderr
        self.assertEqual(c.returncode, 0, said)
        self.assertNotIn("is not translated", said)
        self.assertIn("$dumpvars: ./simv writes a VCD of tb (1 level) to w.vcd", said)
        r = _run([os.path.join(self.tmp, "simv")], self.tmp, env, timeout=1200)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        vcd = Vcd(self.read("w.vcd"))
        seen = [v for _t, v in vcd.history("tb.seen")]
        self.assertGreater(seen.count("1"), 3, seen)
        self.assertGreater(seen.count("0"), 3, seen)


@needs_stack
class TestRunMessagesNameTheSource(TempDir):
    """Run-time messages that carry a location ($warning, $error, the file tasks' vvp
    messages) name the user's file:line (tb.v:6), not vamos's preprocessed copy
    (/abs/simv.daidir/nvc/_norm.sv:9): the compile writes pp's line map beside the job
    (vamos.srclines.json), the run's OutputFilter rewrites through it."""

    def test_locations(self):
        self.write("inc/defs.vh", "`define WIDTH 8\n")
        self.write("tb.v", "`timescale 1ns/1ps\n`include \"inc/defs.vh\"\nmodule tb;\n"
                           "  reg [`WIDTH-1:0] r;\n  initial begin\n"
                           "    #1 $warning(\"careful %0d\", 5);\n    $error(\"boom\");\n"
                           "    $fclose(32'h7fff_0000);\n    #1 $finish;\n  end\nendmodule\n")
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        c = _run(["vcs", "-sverilog", "+incdir+inc", "tb.v"], self.tmp, env)
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        r = _run(["./simv"], self.tmp, env)
        said = r.stdout + r.stderr
        self.assertIn("WARNING: tb.v:6: careful 5", said)
        self.assertIn("ERROR: tb.v:7: boom", said)
        self.assertIn("WARNING: tb.v:8: invalid MCD (0x7fff0000) given to $fclose().", said)
        self.assertNotIn("_norm.sv", said)


@needs_stack
@unittest.skipUnless(have_vacask(), "needs VACASK (Linux/WSL)")
class TestCosimInterruptInProcess(TempDir):
    """Ctrl-C while the digital side of a co-simulation runs a process (a long zero-delay
    loop): the run ends with the co-simulation's interrupted line and exit 130, and no
    "** Fatal: <t>: interrupted in process ..." ahead of it (nvc's own interrupt message,
    which came out whenever the digital was running: nvc rt/model.c model_interrupt_quiet,
    cosim.c cosim_ctrl_c)."""

    def test_one_end_line(self):
        import signal
        import time
        from test_r6_W import CELL_SP
        self.write("tb.sv", """`timescale 1ns/1ps
module tb;
  reg clk = 0;
  logic seen;
  integer i, x;
  always #5 clk = ~clk;
  rc_cell u1 (.in(clk), .out(seen));
  initial begin
    #20;
    x = 0;
    for (i = 0; i < 2000000000; i = i + 1) x = x + 1;
    $display("loop done %0d", x);
  end
  initial #100000 $finish;
endmodule
""")
        self.write("rc.sp", CELL_SP)
        self.write("vcsAD.init", "choose xa rc.sp;\n")
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        env.pop("VAMOS_ANALOG", None)
        c = _run(["vcs-ams", "-sverilog", "tb.sv", "--vamos-analog=vacask"], self.tmp, env,
                 timeout=1200)
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)
        p = subprocess.Popen([os.path.join(self.tmp, "simv")], cwd=self.tmp, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, errors="replace", start_new_session=True)
        time.sleep(6)                      # the loop runs for well over a minute
        os.killpg(p.pid, signal.SIGINT)    # Ctrl-C: the whole process group
        out, _ = p.communicate(timeout=300)
        # ./simv ends as Ctrl-C ends a program: exit status 130, or death by SIGINT (vamos's
        # Python re-raises it), which a shell shows as 130 too
        self.assertIn(p.returncode, (130, -signal.SIGINT), out)
        self.assertIn("co-simulation interrupted at", out)
        self.assertNotIn("interrupted in process", out)
        self.assertNotRegex(out, r"\*\* Fatal: [^\n]*interrupted")


@needs_stack
class TestMemoryOfNetsStrengthsNeverSilent(Probe):
    """ivtest pr1703346: strength-specified drivers on the words of a memory of nets.  nvc's
    kernel net solver declined the element nets .main.foo(1)(0) (a "(W)(B)" path), and the
    VHDL resolution then read xx with exit 0 -- silently.  The solver takes them now (nvc
    vhpi-model.c nvc_vhpi_stitch_net); the run still stops at initialisation in nvc
    ("value -252 outside of LOGIC3D range", as for a memory of 1-bit nets before): loud,
    open.  Either it matches vvp or it fails -- never xx with exit 0."""

    SOURCES = {"src.v": """\
module main;
   wire [1:0] foo [0:1];
   assign     (highz0, strong1) foo[0] = 2'b01;
   assign     (strong0, highz1) foo[0] = 2'b01;
   assign     (highz0, strong1) foo[1] = 2'b10;
   assign     (strong0, highz1) foo[1] = 2'b10;
   initial #1 $display("@ foo[0] = %b, foo[1] = %b", foo[0], foo[1]);
endmodule
"""}
    TOP = "main"

    def test_never_silent(self):
        n, v = self.nvc(), self.vvp()
        if n.returncode == 0:
            self.assertEqual(tagged(n.stdout), tagged(v.stdout), n.stdout + n.stderr)


@needs_stack
class TestNvcVectorIntrinsicArena(TempDir):
    """nvc: many std_logic_1164 "xor" results in one process evaluation overflow the TLAB
    into the eval arena, whose results were 8-byte aligned; the SSE4.1 intrinsic stores
    with aligned 128-bit moves and crashed (SIGSEGV at address nil in ieee_xor_vector_sse41,
    L's repro nvc_xor_crash.vhd; nvc rt/mspace.c eval arena, jit-intrin.c __tlab_overflow)."""

    VHDL = """library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity xorcrash is end entity;
architecture a of xorcrash is
    impure function f(n : natural) return natural is
        variable acc : unsigned(31 downto 0) := (others => '0');
        variable one : unsigned(31 downto 0);
    begin
        for i in 1 to n loop
            one := to_unsigned(i, 32);
            acc := acc xor shift_left(one, 3);
        end loop;
        return to_integer(acc(15 downto 0));
    end function;
begin
    process
    begin
        report "result " & integer'image(f(200000));
        wait;
    end process;
end architecture;
"""

    def test_long_xor_run(self):
        nvc = tools.find_real("nvc")
        self.write("x.vhd", self.VHDL)
        r = _run([nvc, "--std=2008", "-L", tools.nvc_libdir(nvc), "-a", "x.vhd", "-e",
                  "xorcrash", "-r"], self.tmp, dict(os.environ), timeout=600)
        acc = 0
        for i in range(1, 200001):
            acc ^= (i << 3) & 0xFFFFFFFF
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("result %d" % (acc & 0xFFFF), r.stdout + r.stderr)


class TestDumpCovers(unittest.TestCase):
    """vcs.dump_covers: which nets a dump record records (the AMS cut keeps an A2D for an
    unread analog output it covers)."""

    def test_scopes(self):
        from vamos.personalities import vcs
        whole = {"calls": [{"scopes": [], "origin": "+vcs+dumpvars", "if": []}]}
        self.assertTrue(vcs.dump_covers(whole, ["tb.u1.out"]))
        self.assertFalse(vcs.dump_covers(None, ["tb.seen"]))
        one = {"calls": [{"scopes": [[1, "tb"]], "origin": "t.v:3", "if": []}]}
        self.assertTrue(vcs.dump_covers(one, ["tb.seen"]))
        self.assertTrue(vcs.dump_covers(one, ["tb.seen[2]"]))
        self.assertFalse(vcs.dump_covers(one, ["tb.u1.out"]))
        two = {"calls": [{"scopes": [[2, "tb"]], "origin": "t.v:3", "if": []}]}
        self.assertTrue(vcs.dump_covers(two, ["tb.u1.out"]))
        self.assertFalse(vcs.dump_covers(two, ["tb.u1.l.out"]))
        every = {"calls": [{"scopes": [[0, "tb.u1"]], "origin": "t.v:3", "if": []}]}
        self.assertTrue(vcs.dump_covers(every, ["tb.u1.l.k.out"]))
        self.assertFalse(vcs.dump_covers(every, ["tb.seen"]))
        sig = {"calls": [{"scopes": [[1, "tb.seen"]], "origin": "t.v:3", "if": []}]}
        self.assertTrue(vcs.dump_covers(sig, ["tb.seen"]))
        self.assertFalse(vcs.dump_covers(sig, ["tb.other"]))

    def test_mixed_note_labels_the_option(self):
        from vamos.ams import verilog_ports as vp
        from vamos.personalities import vcs
        pp = vp.from_text("module tb;\n  initial $dumpvars(1, tb);\nendmodule\n")
        vhd = ("    null;  -- Unsupported system task $dumpvars omitted here "
               "(/d/simv.daidir/nvc/_norm.sv:2)\n")
        _, notes = vcs.dump_request(vhd, pp, ["tb"], None, every=True)
        text = "\n".join(n.text() for n in notes)
        self.assertIn("the whole design (+vcs+dumpvars); tb (1 level)", text)


class TestSynthesisedDelmax(unittest.TestCase):
    """.option delmax in a netlist with no .tran: the maximum time step of the analysis vamos
    synthesises, unless --vamos-analog-maxstep names another (it was ignored, a warning)."""

    def parse(self, text):
        from vamos.netlist import spice, ir
        d = tempfile.mkdtemp(prefix="vamos-r6r-")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "n.sp")
        with open(p, "w") as fh:
            fh.write(text)
        return spice.parse([p], [], d, ir.ParseOpts())

    def test_delmax_kept_for_the_synthesised_tran(self):
        nl = self.parse("* t\n.option delmax=1n\nr1 a 0 1k\n.end\n")
        self.assertIn("delmax", nl.options)
        self.assertAlmostEqual(nl.options["delmax"].value, 1e-9)
        self.assertFalse([n for n in nl.notes if "ignored" in n.message], nl.notes)

    def test_deck_takes_it(self):
        from vamos.ams import deck
        from vamos.netlist import ir
        nl = self.parse("* t\n.option delmax=1n\nr1 a 0 1k\n.end\n")

        class Ctx:
            notes = []
            cfg = type("C", (), {"choose": None})()
        ctx = Ctx()
        dk = ir.Netlist(options=dict(nl.options))
        stop, synth = deck._analysis(ctx, dk, {})
        self.assertTrue(synth)
        self.assertAlmostEqual(dk.analyses[0].args["maxstep"], 1e-9)
        self.assertTrue([n for n in ctx.notes if ".option delmax" in n.message], ctx.notes)
        dk = ir.Netlist(options=dict(nl.options))
        deck._analysis(ctx, dk, {"analog_maxstep": "2n"})
        self.assertAlmostEqual(dk.analyses[0].args["maxstep"], 2e-9)


TIMING_SRC = """\
`timescale 1ns/10ps
module top;
  reg a;
  wire z;
  initial $sdf_annotate("t.sdf", top);
  initial begin a = 0; #10 a = 1; #10 $display("@ z=%b", z); end
  mycell u (z, a);
endmodule
module mycell (output z, input a);
  buf (z, a);
  specify
    specparam tr = 3;
    (a => z) = (tr, 4);
    $setup(a, posedge z, 1);
    $width(posedge a, 2);
  endspecify
endmodule
module spare (output y, input b);
  assign y = b;
  specify (b *> y) = 2; endspecify
endmodule
"""


class TestTimingOmissions(unittest.TestCase):
    """R6R-30: what of the design's timing vamos does not simulate is said (it was silent:
    ivtest sdf_del_* ran with zero path delays and no SDF, and passed only while an nvc bug
    lost the test's own failure flag): the specify path delays and timing checks once each,
    with a count, and every $sdf_annotate; +nospecify / +notimingcheck silence what VCS
    then ignores too; a module SPICE replaces (AMS) does not count."""

    def scan(self, **kw):
        from vamos.ams import verilog_ports as vp
        return [n.text() for n in vp.timing_omissions(vp.from_text(TIMING_SRC), **kw)]

    def test_all(self):
        got = self.scan()
        self.assertEqual(len(got), 3, got)
        self.assertIn("pp.orig.v:11: specify path delays are not simulated: every module path "
                      "has zero delay (2 specify blocks with path delays", got[0])
        self.assertIn("pp.orig.v:14: timing checks are not run ($setup, $width; 2 in the "
                      "design", got[1])
        self.assertIn("pp.orig.v:5: $sdf_annotate is not simulated", got[2])
        self.assertTrue(all(t.startswith("warning: ") for t in got), got)

    def test_options_and_cells(self):
        self.assertEqual(len(self.scan(nospecify=True)), 1)              # the SDF call only
        got = self.scan(notimingcheck=True)
        self.assertEqual(len(got), 2, got)
        self.assertFalse([t for t in got if "timing checks" in t], got)
        got = self.scan(exclude=["mycell"])
        self.assertIn("(1 specify block with path delays", got[0])
        self.assertIn("pp.orig.v:20:", got[0])
        self.assertFalse([t for t in got if "timing checks" in t], got)


@needs_stack
class TestTimingWarningsInVcs(TempDir):
    """R6R-30, end to end: vcs says what it leaves out of the design's timing."""

    def test_vcs(self):
        self.write("t.v", TIMING_SRC)
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        c = _run(["vcs", "t.v"], self.tmp, env)
        said = c.stdout + c.stderr
        self.assertEqual(c.returncode, 0, said)
        self.assertIn("vamos: warning: t.v:11: specify path delays are not simulated", said)
        self.assertIn("vamos: warning: t.v:14: timing checks are not run", said)
        self.assertIn("vamos: warning: t.v:5: $sdf_annotate is not simulated", said)
        c = _run(["vcs", "+nospecify", "t.v"], self.tmp, env)
        said = c.stdout + c.stderr
        self.assertEqual(c.returncode, 0, said)
        self.assertNotIn("specify path delays", said)
        self.assertNotIn("timing checks are not run", said)


class TestUndrivenTopInputs(unittest.TestCase):
    """R6R-31: one top-level module with input ports: nvc elaborates it alone and each
    unassociated input takes its type's first value (0, `a=0 z=1' for a top-level inverter),
    where VCS leaves a top-level input undriven (z): said now (it was silent).  A real input
    reads 0.0 in both and is not named."""

    VHD = ("entity inv1 is\n  port (\n    z : out logic3d;\n    a : in logic3d;\n"
           "    b : in logic3d_vector(3 downto 0);\n    r : in real\n  );\nend entity;\n")
    SRC = ("module inv1 (z, a, b, r);\n  output z;\n  input a;\n  input [3:0] b;\n"
           "  input real r;\n  not g1(z, a);\nendmodule\n")

    def test_warning(self):
        from vamos.ams import verilog_ports as vp
        from vamos.personalities import vcs
        pp = vp.from_text(self.SRC)
        got = [n.text() for n in vcs.undriven_top_inputs(pp, self.VHD, "inv1")]
        self.assertEqual(got, ["warning: pp.orig.v:1: top-level module inv1 has input ports a, b "
                               "that nothing drives: they read 0 in this simulation, where VCS "
                               "leaves them undriven (z)"])
        self.assertEqual(vcs.undriven_top_inputs(pp, "entity inv1 is\nend entity;\n", "inv1"), [])

    @needs_stack
    def test_vcs(self):
        d = tempfile.mkdtemp(prefix="vamos-r6r-")
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "t.v"), "w") as fh:
            fh.write(self.SRC.replace("  input real r;\n", "").replace(", r);", ");"))
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        c = _run(["vcs", "t.v"], d, env)
        said = c.stdout + c.stderr
        self.assertEqual(c.returncode, 0, said)
        self.assertIn("vamos: warning: t.v:1: top-level module inv1 has input ports a, b that "
                      "nothing drives", said)


class TestSubcktSpelling(unittest.TestCase):
    """Two subckts whose ports differ only in case: each keeps its own header spelling."""

    def test_per_subckt(self):
        from vamos.netlist import spice, ir
        from vamos.ams import cut
        d = tempfile.mkdtemp(prefix="vamos-r6r-")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "n.sp")
        with open(p, "w") as fh:
            fh.write("* t\n.subckt one VOUT gnd\nr1 VOUT gnd 1k\n.ends\n"
                     ".subckt two Vout gnd\nr1 Vout gnd 1k\n.ends\n.end\n")
        nl = spice.parse([p], [], d, ir.ParseOpts())
        subs = nl.subckts()
        self.assertEqual(subs["one"].spelling.get("vout"), "VOUT")
        self.assertEqual(subs["two"].spelling.get("vout"), "Vout")
        self.assertEqual(cut._port_spelling(nl, "vout", "two"), "Vout")
        self.assertEqual(cut._port_spelling(nl, "vout", "one"), "VOUT")


if __name__ == "__main__":
    unittest.main()
