"""Round 6, fixer F: Verilog file I/O, and where ./simv's relative file names resolve.

R6F-01  $fopen (and $fopenr/$fopenw/$fopena), $fclose, $fdisplay, $fwrite, $fstrobe,
        $fmonitor (and their b/h/o forms), $fflush, $writememh and $writememb run on the
        sv2vhdl runtime (nvc lib/sv2vhdl: sv_display_pkg for the descriptors,
        logic3d_types_pkg for $readmem/$writemem; tgt-vhdl fileio.cc and stmt.cc's
        draw_stask_readmem) with vvp's semantics: descriptor numbering (MCD bit 0 stdout,
        files from bit 1; FD bit 31, files from 0x80000003), vvp's messages, $writemem's
        format, $fstrobe/$fmonitor at the end of the time step (every call on its own
        descriptor, a monitor watching just the memory word it shows) and $fclose ending
        an $fmonitor.  They were dropped (plain vcs: a warning; vcs-ams: an error); now
        neither, and vcs-ams accepts them.  A form with no translation (a memory in another
        module) still gets the located warning.
R6F-02  Relative file names resolve where ./simv was started, as under VCS: a plain run's
        nvc works there (it worked in simv.daidir/nvc); a co-simulation's nvc keeps its run
        directory for the analog engines' files, and the runtime gets the start directory
        as SV2VHDL_FILE_DIR.  The resolver plugin's cache and the work library it compiles
        into stay in the daidir (NVC_RESOLVER_DIR, NVC_WORK).
R6F-03  $readmemh/$readmemb (upstream's translation, iverilog b918ff864 + nvc 2e7d16d6a)
        follow vvp: a memory of 1-bit words (ivtest pr690: nvc analysis failed), a memory
        whose lowest address is not 0 (the run stopped: "index 16 outside of INTEGER range
        3 downto 0"), vvp's address rules and messages, a blocking write the process's next
        read sees.

    python3 -m unittest discover -s tests/vamos -p 'test_r6_F.py' -v

The NvcBackend tests run anywhere (Cygwin too); the end-to-end tests need the stack
(WSL/Linux), the AMS one an analog engine.  The expected output of every case is vvp's
(EXPECTED; test_expected_is_vvps checks it against iverilog's vvp when one is installed).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import unittest

from vamos_testlib import ROOT, TempDir, have_stack, run

from vamos.backends import nvc

SHIMS = os.path.join(ROOT, "shims")
needs_stack = unittest.skipUnless(have_stack(), "needs nvc + iverilog (Linux/WSL)")


# -- R6F-02: NvcBackend runs nvc where ./simv was started -----------------------------------

# A stand-in for nvc: reports its working directory and the variables vamos sets
REPORT_ENV = r'''
import os
print("** Note: 0ms+0: cwd=" + os.getcwd(), flush=True)
for k in ("SV2VHDL_FILE_DIR", "NVC_RESOLVER_DIR", "NVC_WORK"):
    print("** Note: 0ms+0: %s=%s" % (k, os.environ.get(k, "<unset>")), flush=True)
'''


def fake_backend(workdir):
    be = object.__new__(nvc.NvcBackend)
    be.job, be.emit, be.nvc = None, (lambda s: None), sys.executable
    be.libdir, be.workdir = workdir, workdir
    be.interrupted, be.killed = None, False
    be.translator_lines = []
    return be


def reported(lines):
    out = {}
    for ln in lines:
        k, _, v = ln.partition("=")
        out[k] = v
    return out


class TestRunDirectory(TempDir):
    def setUp(self):
        super().setUp()
        self.start = os.path.realpath(os.path.join(self.tmp, "start"))
        self.daidir_nvc = os.path.realpath(os.path.join(self.tmp, "simv.daidir", "nvc"))
        self.rundir = os.path.realpath(os.path.join(self.tmp, "run"))
        for d in (self.start, self.daidir_nvc, self.rundir):
            os.makedirs(d)
        self._cwd = os.getcwd()
        os.chdir(self.start)            # where the user started ./simv

    def tearDown(self):
        os.chdir(self._cwd)
        super().tearDown()

    def test_stream_hands_the_start_directory_to_the_runtime(self):
        # a co-simulation runs nvc in a directory of the run's own (the engines' files)
        be = fake_backend(self.daidir_nvc)
        out = []
        rc, _ = be.stream([sys.executable, "-c", REPORT_ENV], dict(os.environ), self.rundir,
                          out.append, out.append)
        self.assertEqual(rc, 0, out)
        got = reported(out)
        self.assertEqual(os.path.realpath(got["cwd"]), self.rundir)
        self.assertEqual(os.path.realpath(got["SV2VHDL_FILE_DIR"]), self.start)
        # the resolver plugin's cache and work library: the daidir's, never nvc's cwd
        self.assertEqual(got["NVC_RESOLVER_DIR"], os.path.join(self.daidir_nvc, "_sv2vhdl_cache"))
        self.assertEqual(got["NVC_WORK"], be.work_spec())
        self.assertEqual(be.work_spec(), "work:" + os.path.join(self.daidir_nvc, "work"))

    def test_resolver_variables_set_by_the_user_are_kept(self):
        be = fake_backend(self.daidir_nvc)
        out = []
        env = dict(os.environ, NVC_RESOLVER_DIR="/elsewhere/cache", NVC_WORK="/elsewhere/work")
        be.stream([sys.executable, "-c", REPORT_ENV], env, self.rundir, out.append, out.append)
        got = reported(out)
        self.assertEqual(got["NVC_RESOLVER_DIR"], "/elsewhere/cache")
        self.assertEqual(got["NVC_WORK"], "/elsewhere/work")

    def test_a_plain_run_works_where_simv_was_started(self):
        be = fake_backend(self.daidir_nvc)
        be.run_command = lambda top, plusargs, run_args=(): (
            [sys.executable, "-c", REPORT_ENV], dict(os.environ))
        out = []
        rc, _ = be.run("tb", [], out.append, out.append)
        self.assertEqual(rc, 0, out)
        got = reported(out)
        self.assertEqual(os.path.realpath(got["cwd"]), self.start)       # not simv.daidir/nvc
        self.assertEqual(os.path.realpath(got["SV2VHDL_FILE_DIR"]), self.start)

    @unittest.skipIf(sys.platform.startswith("win"), "POSIX tool paths")
    def test_tool_paths_are_absolute(self):
        # nvc runs in another directory: a relative VAMOS_NVC or NVC_LIBDIR must not break it
        bindir = os.path.join(self.start, "tools", "bin")
        os.makedirs(bindir)
        fake = os.path.join(bindir, "nvc")
        with open(fake, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(fake, 0o755)
        # nvc found through a relative PATH entry, its library directory a relative NVC_LIBDIR
        os.environ.pop("VAMOS_NVC", None)
        os.environ["PATH"] = os.path.join("tools", "bin") + os.pathsep + os.environ.get("PATH", "")
        os.environ["NVC_LIBDIR"] = os.path.join("tools", "lib")
        from vamos import tools
        tools._real_cache.clear()
        tools._scrub_cache.clear()
        from vamos.job import Job
        be = nvc.NvcBackend(Job(personality="simv", argv=[], cwd=self.start,
                                daidir=os.path.join(self.tmp, "simv.daidir")), lambda s: None)
        self.assertTrue(os.path.isabs(be.nvc), be.nvc)
        self.assertEqual(os.path.realpath(be.nvc), os.path.realpath(fake))
        self.assertTrue(os.path.isabs(be.libdir), be.libdir)
        self.assertEqual(be.libdir, os.path.join(self.start, "tools", "lib"))


# -- R6F-01 / R6F-03: the battery (the expected values are vvp's) ---------------------------

FIO_BASIC = r"""
module fio_basic;
  integer m1, m2, m3, m4, f1, f2, fr, fx;
  reg [7:0] a;
  reg [8*16:1] fname;
  initial begin
    a = 8'h5a;
    m1 = $fopen("out_m1.txt");
    m2 = $fopen("out_m2.txt");
    m3 = $fopen("out_m3.txt");
    $display("m1=%h m2=%h m3=%h", m1, m2, m3);
    $fdisplay(m1, "to m1 a=%h %b %d", a, a, a);
    $fwrite(m2, "partial ");
    $fwrite(m2, "line %0d\n", 2);
    $fdisplay(m1 | m2 | 1, "to m1, m2 and stdout");
    $fwrite(1, "stdout partial, ");
    $display("then display");
    $fclose(m2);
    m4 = $fopen("out_m4.txt");
    $display("m4=%h (reuses m2's channel)", m4);
    $fdisplay(m4, "m4 line");
    f1 = $fopen("out_f1.txt", "w");
    f2 = $fopen("out_f2.txt", "w");
    $display("f1=%h f2=%h", f1, f2);
    $fdisplay(f1, "f1 first");
    $fwrite(f1, "f1 no newline");
    $fclose(f1);
    f1 = $fopen("out_f1.txt", "a");
    $display("f1 reopened=%h", f1);
    $fdisplay(f1, " + appended");
    $fflush(f1);
    $fflush;
    $fclose(f1);
    fr = $fopen("out_f1.txt", "r");
    $display("fr=%h", fr);
    $fdisplay(fr, "write to a read-only fd is dropped");
    $fclose(fr);
    fx = $fopen("no_such_file_here.txt", "r");
    $display("missing r=%h", fx);
    fx = $fopen("no_such_dir/x.txt", "w");
    $display("bad dir w=%h", fx);
    fx = $fopen("no_such_dir/x.txt");
    $display("bad dir mcd=%h", fx);
    fx = $fopen("out_b.txt", "wb");
    $display("wb=%h", fx);
    $fdisplay(fx, "binary mode is text");
    $fclose(fx);
    fx = 32'h5555;
    fx = $fopen("out_d.txt", "rw");
    $display("rw=%h", fx);
    fx = 32'h5555;
    fx = $fopen("out_d.txt", "wxyz");
    $display("wxyz=%h", fx);
    fx = 32'h5555;
    fx = $fopen("", "w");
    $display("empty name=%h", fx);
    fname = "out_e.txt";
    fx = $fopen(fname, "w");
    $display("reg name=%h", fx);
    $fdisplay(fx, "via a reg name");
    $fclose(fx);
    fx = $fopenw("out_g.txt");
    $fdisplayh(fx, "h ", a);
    $fwriteb(fx, "b ", a, "\n");
    $fdisplayo(fx, "o ", a);
    $fclose(fx);
    fx = $fopen("out_wp.txt", "w+");
    $fdisplay(fx, "w+ writes");
    $fclose(fx);
    fx = $fopen("out_wp.txt", "a+");
    $fdisplay(fx, "a+ appends");
    $fclose(fx);
    $fdisplay(0, "fd 0 is silent");
    $fdisplay(32'h8000_0001, "fd stdout");
    $fdisplay(32'h8000_000f, "invalid fd");
    $fdisplay(32'h4000_0000, "invalid mcd");
    $fwrite(m1 | 32'h0000_0100, "partly invalid mcd");
    $fclose(32'h8000_0009);
    $fclose(1);
    $fclose(32'h8000_0001);
    $fclose(0);
    $fflush(32'h8000_0011);
    $fclose(m1);
    $fclose(m3);
    $fclose(m4);
    $fdisplay(m1, "m1 after close");
    $display("done");
  end
endmodule
"""

FIO_STROBE = r"""
`timescale 1ns/1ns
module fio_strobe;
  integer f, g, m;
  reg [3:0] c;
  initial begin
    c = 0;
    f = $fopen("out_strobe.txt", "w");
    m = $fopen("out_mon.txt");
    $fmonitor(m, "mon c=%0d t=%0t", c, $time);
    $fmonitor(1, "stdout mon c=%0d", c);
    $fstrobe(f, "strobe c=%0d at %0t", c, $time);
    c = 1;
    #1 c = 2;
    $fstrobe(f, "strobe c=%0d at %0t", c, $time);
    $fstrobe(1, "stdout strobe c=%0d", c);
    c = 3;
    #1 c = 4;
    #1 c = 5;
    $fclose(m);
    #1 c = 6;
    g = $fopen("out_strobe2.txt", "w");
    $fstrobe(g, "strobe to g c=%0d", c);
    $fclose(g);
    #1 c = 7;
    $fstrobe(32'h8000_0013, "bad strobe");
    $fmonitor(32'h8000_0014, "bad monitor");
    #1 $fclose(f);
  end
endmodule
"""

RM_DATA = {
    "rm_h1.dat": "// a comment line\n00 01 02 03\n04_ 05\n/* block\n   comment */ 06 07\n"
                 "08 09 0a 0B 0c 0d 0e\n0f\nx z 1x 2z\n",
    "rm_wide.dat": "123456789a\nx_zzzz_0000\nf\n",
    "rm_b1.dat": "1 0 x z 1_ 0\n",
    "rm_addr.dat": "@2 aa bb @a cc\n@0 dd\n",
    "rm_badaddr.dat": "@20 11\n",
    "rm_badchar.dat": "11 22 g3\n",
    "rm_excess.dat": "123 3f 7 xz\n",
    "rm_bexcess.dat": "1010101 11 x1z0 0\n",
    "rm_bbad.dat": "01 2\n",
    "rm_at.dat": "11 @ 22\n",
    "rm_under.dat": "_ 1_2 __3\n",
    "rm_crlf.dat": "a1\r\nb2\r\n\tc3\x0cd4\n",
    "rm_empty.dat": "",
    "rm_unterminated.dat": "01 /* never closed\n02 03\n",
}

# pr: ivtest pr690's memory of 1-bit words declared [1:0], read with a start address;
# b4 and neg: memories whose lowest address is not 0
RM_BASIC = r"""
module rm_basic;
  reg [7:0]  a [0:15];
  reg [7:0]  d [15:0];
  reg [7:0]  b4 [4:7];
  reg [39:0] w [0:3];
  reg        bit1 [0:7];
  reg [5:0]  s6 [0:3];
  reg [0:0]  pr [1:0];
  reg [7:0]  neg [-2:1];
  integer i;
  initial begin
    for (i = 0; i < 16; i = i + 1) begin a[i] = 8'hee; d[i] = 8'hee; end
    for (i = 0; i < 8; i = i + 1) bit1[i] = 1'b1;
    $readmemh("rm_h1.dat", a);
    for (i = 0; i < 16; i = i + 1) $display("a[%0d]=%h", i, a[i]);
    $readmemh("rm_h1.dat", d);
    for (i = 0; i < 16; i = i + 1) $display("d[%0d]=%h", i, d[i]);
    $readmemh("rm_h1.dat", a, 2, 5);
    for (i = 0; i < 8; i = i + 1) $display("a2[%0d]=%h", i, a[i]);
    $readmemh("rm_h1.dat", a, 9, 6);
    for (i = 0; i < 12; i = i + 1) $display("a3[%0d]=%h", i, a[i]);
    $readmemh("rm_h1.dat", b4);
    for (i = 4; i < 8; i = i + 1) $display("b4[%0d]=%h", i, b4[i]);
    $readmemh("rm_wide.dat", w);
    for (i = 0; i < 4; i = i + 1) $display("w[%0d]=%h", i, w[i]);
    $readmemb("rm_b1.dat", bit1);
    for (i = 0; i < 8; i = i + 1) $display("bit1[%0d]=%b", i, bit1[i]);
    $readmemb("rm_b1.dat", pr, 0);
    $display("pr[0]=%b pr[1]=%b", pr[0], pr[1]);
    $readmemh("rm_addr.dat", a);
    for (i = 0; i < 16; i = i + 1) $display("a4[%0d]=%h", i, a[i]);
    $readmemh("rm_badaddr.dat", a);
    $readmemh("rm_badchar.dat", a);
    $readmemh("rm_excess.dat", s6);
    for (i = 0; i < 4; i = i + 1) $display("s6[%0d]=%b", i, s6[i]);
    $readmemb("rm_bexcess.dat", s6);
    for (i = 0; i < 4; i = i + 1) $display("s6b[%0d]=%b", i, s6[i]);
    $readmemb("rm_bbad.dat", s6);
    $readmemh("rm_at.dat", s6);
    $readmemh("rm_under.dat", s6);
    for (i = 0; i < 4; i = i + 1) $display("s6u[%0d]=%b", i, s6[i]);
    $readmemh("rm_crlf.dat", neg);
    for (i = -2; i < 2; i = i + 1) $display("neg[%0d]=%h", i, neg[i]);
    $readmemh("rm_empty.dat", neg);
    $readmemh("rm_unterminated.dat", neg, -1);
    $display("neg[-1]=%h neg[0]=%h", neg[-1], neg[0]);
    $readmemh("rm_missing.dat", a);
    $readmemh("rm_h1.dat", a, 16);
    $readmemh("rm_h1.dat", a, 0, 16);
    $readmemh("rm_h1.dat", b4, 3);
    $readmemh("rm_h1.dat", a, 4);
    $display("a5[15]=%h a5[3]=%h", a[15], a[3]);
  end
endmodule
"""

WM_BASIC = r"""
module wm_basic;
  reg [7:0]  a [0:19];
  reg [5:0]  s [3:0];
  reg        b1 [0:2];
  reg [9:0]  b4 [4:7];
  reg [7:0]  ng [-1:1];
  integer i;
  initial begin
    for (i = 0; i < 20; i = i + 1) a[i] = i * 3;
    a[1] = 8'bxxxx_0000;
    a[2] = 8'bzzzz_1x10;
    a[3] = 8'bzz00_xxxx;
    a[4] = 8'bxxxxxxxx;
    a[5] = 8'bzzzzzzzz;
    for (i = 0; i < 4; i = i + 1) s[i] = 6'b10_0001 + i;
    s[2] = 6'bxx_0101;
    b1[0] = 1; b1[1] = 1'bx; b1[2] = 1'bz;
    for (i = 4; i < 8; i = i + 1) b4[i] = 10'h3f0 + i;
    for (i = -1; i < 2; i = i + 1) ng[i] = 8'h70 + i;
    $writememh("out_wm_a.hex", a);
    $writememb("out_wm_a.bin", a, 3, 6);
    $writememh("out_wm_ad.hex", a, 6, 3);
    $writememh("out_wm_s.hex", s);
    $writememb("out_wm_s.bin", s);
    $writememb("out_wm_b1.bin", b1);
    $writememh("out_wm_b4.hex", b4);
    $writememh("out_wm_b4s.hex", b4, 5);
    $writememh("out_wm_ng.hex", ng);
    $writememh("no_such_dir/x.hex", a);
    $writememh("out_wm_bad.hex", a, 0, 20);
    $display("done");
  end
endmodule
"""

# a $readmemh after a wait (a signal memory a later read must see), file tasks in a task,
# $fstrobe and $fdisplay to one file from an always block
FIO_PROC = r"""
`timescale 1ns/1ns
module fio_proc;
  reg clk = 0;
  integer f, n;
  reg [7:0] m [0:3];
  reg [7:0] q;
  always #5 clk = ~clk;
  initial f = $fopen("out_proc.txt", "w");
  always @(posedge clk) begin
    q = q + 1;
    $fstrobe(f, "strobe q=%0d t=%0t", q, $time);
    $fdisplay(f, "display q=%0d", q);
  end
  task show;
    input [7:0] v;
    begin
      $fdisplay(f, "task v=%h m1=%h", v, m[1]);
      $fwriteh(f, "h:", v, " ", 8'd10, "\n");
      $fwriteb(f, "b:", v, "\n");
      $fwriteo(f, "o:", v, "\n");
      $fdisplayh(f, "dh:", v);
    end
  endtask
  initial begin
    q = 0;
    #12 $readmemh("rm_h1.dat", m);
    $display("m[1]=%h m[3]=%h", m[1], m[3]);
    show(m[2]);
    m[0] = 8'h5a;
    $writememh("out_proc.hex", m);
    #20 $fclose(f);
    n = $fopen("out_proc.txt", "a");
    $fdisplay(n, "appended at %0t", $time);
    $fclose(n);
    $finish;
  end
endmodule
"""

FIO_LIMITS = r"""
`timescale 1ns/1ps
module fio_limits;
  integer i, f, g, mcds, fds [0:39];
  reg [8*12:1] name;
  reg [31:0] m [0:31];
  function integer open_log(input integer k);
    begin
      open_log = $fopen("out_func.txt", "w");
      $fdisplay(open_log, "opened in a function k=%0d", k);
    end
  endfunction
  initial begin
    mcds = 0;
    for (i = 1; i <= 31; i = i + 1) begin
      $swrite(name, "out_mc%0d.txt", i);
      m[i] = $fopen(name);
      mcds = mcds | m[i];
    end
    $display("mcds=%h last=%h", mcds, m[31]);
    $fdisplay(mcds, "to all thirty");
    $fclose(mcds);
    for (i = 0; i < 40; i = i + 1) begin
      $swrite(name, "out_fd%0d.txt", i);
      fds[i] = $fopen(name, "w");
    end
    $display("fd0=%h fd39=%h", fds[0], fds[39]);
    for (i = 0; i < 40; i = i + 1) begin
      $fwrite(fds[i], "fd %0d", i);
      $fclose(fds[i]);
    end
    f = $fopen("out_fmt.txt", "w");
    $fdisplay(f, "c=%c s=%s m=%m t=%t h=%h o=%o b=%b", 8'd65, "str", $time, 12'habc, 6'o17,
              3'b101);
    $fdisplay(f);
    $fwrite(f);
    $fwrite(f, "%0d%%\n", 50);
    $fclose(f);
    g = open_log(3);
    $fclose(g);
    $display("done");
  end
endmodule
"""

# $readmem in a task; a memory an always block writes after a time-0 $readmemh; $monitor
# and $fmonitor of one memory word side by side while another word is written (vvp
# watches the word: no line for the write to ram[2])
FIO_EDGE = r"""
`timescale 1ns/1ns
module fio_edge;
  reg clk = 0;
  reg [7:0] rom [0:3];
  reg [7:0] ram [0:3];
  reg [1:0] wa;
  reg [7:0] wd;
  reg we;
  integer f, i;
  always #5 clk = ~clk;
  always @(posedge clk) if (we) ram[wa] <= wd;
  task load;
    begin
      $readmemh("rm_h1.dat", rom);
    end
  endtask
  initial begin
    we = 0;
    $readmemh("rm_h1.dat", ram);
    load;
    f = $fopen("out_edge.txt", "w");
    $monitor("mon ram0=%h", ram[0]);
    $fmonitor(f, "fmon ram0=%h rom3=%h", ram[0], rom[3]);
    #7 wa = 0; wd = 8'h99; we = 1;
    #10 wa = 2; wd = 8'h77;
    #10 we = 0;
    for (i = 0; i < 4; i = i + 1) $display("ram[%0d]=%h", i, ram[i]);
    #10 $fclose(f);
    $finish;
  end
endmodule
"""

# The same $fstrobe / $fmonitor statement called more than once, on several descriptors
# and in one time step; two $fstrobe statements to one file in one time step
FIO_MULTI = r"""
`timescale 1ns/1ns
module fio_multi;
  integer f [0:2];
  integer k;
  reg [3:0] c;
  reg [7:0] mem [0:3];
  task tell(input integer fd);
    $fstrobe(fd, "strobe c=%0d fd=%h", c, fd);
  endtask
  task watch(input integer fd);
    $fmonitor(fd, "mon c=%0d mem1=%h", c, mem[1]);
  endtask
  initial begin
    c = 0;
    mem[0] = 0; mem[1] = 1; mem[2] = 2; mem[3] = 3;
    f[0] = $fopen("out_mul0.txt", "w");
    f[1] = $fopen("out_mul1.txt", "w");
    f[2] = $fopen("out_mul2.txt", "w");
    for (k = 0; k < 3; k = k + 1) tell(f[k]);
    watch(f[0]);
    #1 watch(f[1]);
    c = 1;
    #1 c = 2;
    mem[2] = 8'haa;
    #1 mem[1] = 8'h55;
    #1 $fclose(f[0]);
    c = 3;
    #1 c = 4;
    $fstrobe(f[2], "b first?");
    $fstrobe(f[2], "c second?");
    $fdisplay(f[2], "a now");
    #1 $fclose(f[1]);
    $fclose(f[2]);
    $display("done");
  end
endmodule
"""

# vvp evaluates a $fdisplay's arguments whatever the descriptor: a $random is drawn
FIO_RANDOM = r"""
module fio_random;
  integer f, v;
  initial begin
    f = 0;
    $fdisplay(f, "%0d", $random);
    $fdisplay(32'h8000_0021, "%0d", $random);
    v = $random;
    $display("first draw that counted: %0d", v);
    f = $fopen("out_rand.txt", "w");
    $fdisplay(f, "%0d", $random);
    $fclose(f);
    v = $random;
    $display("next: %0d", v);
  end
endmodule
"""

# $readmem with names from a reg and a parameter, real start/finish, a name that is not a
# valid string, a 1-bit memory in a running process
FIO_RMVAR = r"""
`timescale 1ns/1ns
module fio_rmvar;
  parameter P = "rm_h1.dat";
  parameter real RS = 1.6;
  reg [8*12:1] fn;
  reg [7:0] m [0:7];
  reg bits [0:3];
  real rr;
  integer i;
  reg go = 0;
  always @(posedge go) begin
    $readmemb("rm_b1.dat", bits);
    $display("bits=%b%b%b%b", bits[0], bits[1], bits[2], bits[3]);
  end
  initial begin
    fn = "rm_addr.dat";
    $readmemh(fn, m);
    $display("m=%h %h %h %h", m[0], m[1], m[2], m[3]);
    $readmemh(P, m, RS);
    $display("m=%h %h %h", m[1], m[2], m[3]);
    rr = 2.4;
    $readmemh(P, m, 0, rr);
    $display("m=%h %h %h", m[0], m[1], m[2]);
    fn = 0;
    $readmemh(fn, m);
    #1 go = 1;
    #1 $display("end");
  end
endmodule
"""

# files still open when the run ends ($finish, or no more events): their text is all there
FIO_NOCLOSE = r"""
`timescale 1ns/1ns
module fio_noclose;
  integer f, m;
  initial begin
    f = $fopen("out_nc_fd.txt", "w");
    m = $fopen("out_nc_mcd.txt");
    $fdisplay(f, "fd line");
    $fwrite(f, "fd partial");
    $fdisplay(m, "mcd line");
    $fwrite(m, "mcd partial");
    #5 $finish;
  end
endmodule
"""

FIO_NOCLOSE2 = r"""
`timescale 1ns/1ns
module fio_noclose2;
  integer f;
  initial begin
    f = $fopen("out_nc2.txt", "w");
    $fdisplay(f, "fd line");
    $fwrite(f, "fd partial");
    #5 $display("end of events");
  end
endmodule
"""

# the b/h/o forms of $fstrobe, $fmonitor, $fdisplay and $fwrite: bare arguments in that
# radix, every bit (x, z and a signed value too)
FIO_RADIX = r"""
`timescale 1ns/1ns
module fio_radix;
  integer f, i;
  reg [11:0] v;
  reg s;
  reg signed [7:0] n;
  initial begin
    f = $fopen("out_radix.txt", "w");
    v = 12'h1x5; s = 1'bz; n = -3;
    $fstrobeh(f, "sh ", v, " ", s, " ", n);
    $fstrobeb(f, "sb ", v);
    $fstrobeo(f, "so ", v, " ", n);
    $fmonitorh(f, "mh ", v);
    $fmonitoro(f, "mo ", n);
    $fdisplayh(f, "dh ", v, " ", n, " ", 5);
    $fwriteo(f, "wo ", n, "\n");
    #1 v = 12'habc; n = 8'sd100;
    #1 $fdisplayb(f, "db ", s, " ", n);
    #1 $fclose(f);
  end
endmodule
"""

BATTERY = {
    "fio_radix": (FIO_RADIX, {}),
    "fio_noclose": (FIO_NOCLOSE, {}),
    "fio_noclose2": (FIO_NOCLOSE2, {}),
    "fio_basic": (FIO_BASIC, {}),
    "fio_strobe": (FIO_STROBE, {}),
    "rm_basic": (RM_BASIC, RM_DATA),
    "wm_basic": (WM_BASIC, {}),
    "fio_proc": (FIO_PROC, RM_DATA),
    "fio_limits": (FIO_LIMITS, {}),
    "fio_edge": (FIO_EDGE, RM_DATA),
    "fio_multi": (FIO_MULTI, {}),
    "fio_random": (FIO_RANDOM, {}),
    "fio_rmvar": (FIO_RMVAR, RM_DATA),
}


def _behaviour(task):
    return ("WARNING: <L>: %s: The behaviour for reg[...] mem[N:0]; %s(\"...\", mem); changed in "
            "the 1364-2005 standard. To avoid ambiguity, use mem[0:N] or explicit range "
            "parameters %s(\"...\", mem, start, stop);. Defaulting to 1364-2005 behavior."
            % (task, task, task))


def _too_many(task, f, r):
    return ("WARNING: <L>: %s(%s): Too many words in the file for the requested range %s."
            % (task, f, r))


def _not_enough(task, f, r):
    return ("WARNING: <L>: %s(%s): Not enough words in the file for the requested range %s."
            % (task, f, r))


# vvp's stdout (each message's location shown as <L>) and the files the case writes
EXPECTED = {
    "fio_basic": ([
        "m1=00000002 m2=00000004 m3=00000008", "to m1, m2 and stdout",
        "stdout partial, then display", "m4=00000004 (reuses m2's channel)",
        "f1=80000003 f2=80000004", "f1 reopened=80000003", "fr=80000003", "missing r=00000000",
        "bad dir w=00000000", "bad dir mcd=00000000", "wb=80000003",
        "WARNING: <L>: $fopen's mode argument (rw) is invalid.", "rw=00000000",
        "WARNING: <L>: $fopen's mode argument (wxyz) is too long.", "wxyz=00000000",
        "WARNING: <L>: $fopen's file name argument (vpiConstant) is not a valid string.",
        "empty name=00000000", "reg name=80000003", "fd stdout",
        "WARNING: <L>: invalid file descriptor (0x8000000f) given to $fdisplay().",
        "WARNING: <L>: invalid MCD (0x40000000) given to $fdisplay().",
        "WARNING: <L>: invalid MCD (0x102) given to $fwrite().",
        "WARNING: <L>: invalid file descriptor (0x80000009) given to $fclose().",
        "WARNING: <L>: could not close MCD STDOUT (0x1) in $fclose().",
        "WARNING: <L>: could not close file descriptor STDOUT (0x80000001) in $fclose().",
        "WARNING: <L>: invalid file descriptor (0x80000011) given to $fflush().",
        "WARNING: <L>: invalid MCD (0x2) given to $fdisplay().", "done"], {
        "out_b.txt": "binary mode is text\n",
        "out_e.txt": "via a reg name\n",
        "out_f1.txt": "f1 first\nf1 no newline + appended\n",
        "out_f2.txt": "",
        "out_g.txt": "h 5a\nb 01011010\no 132\n",
        "out_m1.txt": "to m1 a=5a 01011010  90\nto m1, m2 and stdout\n",
        "out_m2.txt": "partial line 2\nto m1, m2 and stdout\n",
        "out_m3.txt": "",
        "out_m4.txt": "m4 line\n",
        "out_wp.txt": "w+ writes\na+ appends\n"}),
    "fio_edge": ([
        _too_many("$readmemh", "rm_h1.dat", "[0:3]"), _too_many("$readmemh", "rm_h1.dat", "[0:3]"),
        "mon ram0=00", "mon ram0=99", "ram[0]=99", "ram[1]=01", "ram[2]=77", "ram[3]=03"], {
        "out_edge.txt": "fmon ram0=00 rom3=03\nfmon ram0=99 rom3=03\n"}),
    "fio_limits": (["mcds=7ffffffe last=00000000", "fd0=80000003 fd39=8000002a", "done"], dict(
        [("out_fd%d.txt" % i, "fd %d" % i) for i in range(40)]
        + [("out_mc%d.txt" % i, "to all thirty\n") for i in range(1, 31)]
        + [("out_fmt.txt", "c=A s=str m=fio_limits t=                   0 h=abc o=17 b=101\n"
                           "\n50%\n"),
           ("out_func.txt", "opened in a function k=3\n")])),
    "fio_radix": ([], {"out_radix.txt": "dh 1x5 fd 00000005\nwo 375\nsh 1x5 z fd\n"
                                        "sb 0001xxxx0101\nso 0XX5 375\nmh 1x5\nmo 375\nmh abc\n"
                                        "mo 144\ndb z 01100100\n"}),
    "fio_noclose": ([], {"out_nc_fd.txt": "fd line\nfd partial",
                         "out_nc_mcd.txt": "mcd line\nmcd partial"}),
    "fio_noclose2": (["end of events"], {"out_nc2.txt": "fd line\nfd partial"}),
    "fio_multi": (["done"], {
        "out_mul0.txt": "strobe c=0 fd=80000005\nmon c=0 mem1=01\nmon c=1 mem1=01\n"
                        "mon c=2 mem1=01\nmon c=2 mem1=55\n",
        "out_mul1.txt": "strobe c=0 fd=80000005\nmon c=1 mem1=01\nmon c=2 mem1=01\n"
                        "mon c=2 mem1=55\nmon c=3 mem1=55\nmon c=4 mem1=55\n",
        "out_mul2.txt": "strobe c=0 fd=80000005\na now\nb first?\nc second?\n"}),
    "fio_proc": ([_too_many("$readmemh", "rm_h1.dat", "[0:3]"), "m[1]=01 m[3]=03"], {
        "out_proc.hex": "// 0x00000000\n5a\n01\n02\n03\n",
        "out_proc.txt": "display q=1\nstrobe q=1 t=5\ntask v=02 m1=01\nh:02 0a\nb:00000010\n"
                        "o:002\ndh:02\ndisplay q=2\nstrobe q=2 t=15\ndisplay q=3\n"
                        "strobe q=3 t=25\nappended at 32\n"}),
    "fio_random": ([
        "WARNING: <L>: invalid file descriptor (0x80000021) given to $fdisplay().",
        "first draw that counted: -2071669239", "next: 112818957"], {
        "out_rand.txt": "-1309649309\n"}),
    "fio_rmvar": ([
        "ERROR: <L>: $readmemh(rm_addr.dat): address (0xa) is out of range [0x0:0x7]",
        "m=xx xx aa bb",
        "WARNING: <L>: $readmemh's third argument (start address) is a real value.",
        _too_many("$readmemh", "rm_h1.dat", "[2:7]"), "m=xx 00 01",
        "WARNING: <L>: $readmemh's fourth argument (finish address) is a real value.",
        _too_many("$readmemh", "rm_h1.dat", "[0:2]"), "m=00 01 02",
        "WARNING: <L>: $readmemh's file name argument (vpiReg) is not a valid string.",
        _too_many("$readmemb", "rm_b1.dat", "[0:3]"), "bits=10xz", "end"], {}),
    "fio_strobe": ([
        "stdout mon c=1", "stdout mon c=3", "stdout strobe c=3", "stdout mon c=4",
        "stdout mon c=5", "stdout mon c=6",
        "WARNING: <L>: invalid file descriptor (0x80000013) given to $fstrobe().",
        "WARNING: <L>: invalid file descriptor (0x80000014) given to $fmonitor().",
        "stdout mon c=7"], {
        "out_mon.txt": "mon c=1 t=0\nmon c=3 t=1\nmon c=4 t=2\n",
        "out_strobe.txt": "strobe c=1 at 0\nstrobe c=3 at 1\n",
        "out_strobe2.txt": ""}),
    "rm_basic": (
        [_too_many("$readmemh", "rm_h1.dat", "[0:15]")]
        + ["a[%d]=%02x" % (i, i) for i in range(16)]
        + [_behaviour("$readmemh"), _too_many("$readmemh", "rm_h1.dat", "[0:15]")]
        + ["d[%d]=%02x" % (i, i) for i in range(16)]
        + [_too_many("$readmemh", "rm_h1.dat", "[2:5]")]
        + ["a2[%d]=%s" % (i, v) for i, v in enumerate("00 01 00 01 02 03 06 07".split())]
        + [_too_many("$readmemh", "rm_h1.dat", "[9:6]")]
        + ["a3[%d]=%s" % (i, v)
           for i, v in enumerate("00 01 00 01 02 03 03 02 01 00 0a 0b".split())]
        + [_too_many("$readmemh", "rm_h1.dat", "[4:7]")]
        + ["b4[%d]=%02x" % (i, i - 4) for i in range(4, 8)]
        + [_not_enough("$readmemh", "rm_wide.dat", "[0:3]"),
           "w[0]=123456789a", "w[1]=0xzzzz0000", "w[2]=000000000f", "w[3]=xxxxxxxxxx",
           _not_enough("$readmemb", "rm_b1.dat", "[0:7]")]
        + ["bit1[%d]=%s" % (i, v) for i, v in enumerate("10xz1011")]
        + [_behaviour("$readmemb"), _too_many("$readmemb", "rm_b1.dat", "[0:1]"),
           "pr[0]=1 pr[1]=0"]
        + ["a4[%d]=%s" % (i, v) for i, v in enumerate(
            "dd 01 aa bb 02 03 03 02 01 00 cc 0b 0c 0d 0e 0f".split())]
        + ["ERROR: <L>: $readmemh(rm_badaddr.dat): address (0x20) is out of range [0x0:0xf]",
           "ERROR: <L>: $readmemh(rm_badchar.dat): Invalid input character: g",
           "WARNING: <L>: Excess hex digits (1 of '123') while reading 6-bit words.",
           "s6[0]=100011", "s6[1]=111111", "s6[2]=000111", "s6[3]=xxzzzz",
           "WARNING: <L>: Excess binary digits (1 of '1010101') while reading 6-bit words.",
           "s6b[0]=010101", "s6b[1]=000011", "s6b[2]=00x1z0", "s6b[3]=000000",
           "ERROR: <L>: $readmemb(rm_bbad.dat): Invalid input character: 2",
           "ERROR: <L>: $readmemh(rm_at.dat): Invalid input character: @",
           _not_enough("$readmemh", "rm_under.dat", "[0:3]"),
           "s6u[0]=000000", "s6u[1]=010010", "s6u[2]=000011", "s6u[3]=000000",
           "neg[-2]=a1", "neg[-1]=b2", "neg[0]=c3", "neg[1]=d4",
           _not_enough("$readmemh", "rm_empty.dat", "[-2:1]"),
           _not_enough("$readmemh", "rm_unterminated.dat", "[-1:1]"),
           "neg[-1]=01 neg[0]=c3",
           "ERROR: <L>: $readmemh: Unable to open rm_missing.dat for reading.",
           "ERROR: <L>: $readmemh: Start address 16 is out of bounds for memory "
           "'rm_basic.a[0:15]'!",
           "ERROR: <L>: $readmemh: Finish address 16 is out of bounds for memory "
           "'rm_basic.a[0:15]'!",
           "ERROR: <L>: $readmemh: Start address 3 is out of bounds for memory "
           "'rm_basic.b4[4:7]'!",
           _too_many("$readmemh", "rm_h1.dat", "[4:15]"),
           "a5[15]=0b a5[3]=bb"], {}),
    "wm_basic": ([
        _behaviour("$writememh"), _behaviour("$writememb"),
        "ERROR: <L>: $writememh: Unable to open no_such_dir/x.hex for writing.",
        "ERROR: <L>: $writememh: Finish address 20 is out of bounds for memory "
        "'wm_basic.a[0:19]'!", "done"], {
        "out_wm_a.bin": "// 0x00000000\nzz00xxxx\nxxxxxxxx\nzzzzzzzz\n00010010\n",
        "out_wm_a.hex": "// 0x00000000\n00\nx0\nzX\nZx\nxx\nzz\n12\n15\n18\n1b\n1e\n21\n24\n"
                        "27\n2a\n2d\n// 0x00000010\n30\n33\n36\n39\n",
        "out_wm_ad.hex": "// 0x00000000\n12\nzz\nxx\nZx\n",
        "out_wm_b1.bin": "// 0x00000000\n1\nx\nz\n",
        "out_wm_b4.hex": "// 0x00000000\n3f4\n3f5\n3f6\n3f7\n",
        "out_wm_b4s.hex": "// 0x00000000\n3f5\n3f6\n3f7\n",
        "out_wm_ng.hex": "// 0x00000000\n6f\n70\n71\n",
        "out_wm_s.bin": "// 0x00000000\n100001\n100010\nxx0101\n100100\n",
        "out_wm_s.hex": "// 0x00000000\n21\n22\nx5\n24\n"}),
}

# "WARNING: <file>:<line>: ..." / "ERROR: ..." -> "WARNING: <L>: ..."
_LOC = re.compile(r"^(WARNING|ERROR): \S+:\d+:")
# vamos's own lines around the testbench's (the provenance table, nvc's end note, the footer)
_CHROME = re.compile(r"^(vamos [\d.]+ \(|  \S+ +\S+ +\S+ +/|(FINISH|STOP) called$|\s+V A M O S|"
                     r"Time: |CPU Time: )")


def testbench_lines(text):
    out = []
    for ln in text.splitlines():
        if _CHROME.match(ln):
            continue
        out.append(_LOC.sub(r"\1: <L>:", ln))
    return out


def vvp_tools():
    """(iverilog, vvp) of an Icarus install, for the reference runs; None when there is none."""
    dirs = [os.environ.get("VAMOS_TEST_VVP_DIR", ""), "/usr/local/src/iverilog/_install/bin"]
    dirs += os.environ.get("PATH", "").split(os.pathsep)
    for d in dirs:
        iv, vvp = os.path.join(d, "iverilog"), os.path.join(d, "vvp")
        if d and os.access(iv, os.X_OK) and os.access(vvp, os.X_OK) and SHIMS not in d:
            return iv, vvp
    return None


def write_case(d, name):
    src, data = BATTERY[name]
    os.makedirs(d)
    with open(os.path.join(d, name + ".v"), "w") as fh:
        fh.write(src)
    for rel, text in data.items():
        with open(os.path.join(d, rel), "w", newline="") as fh:
            fh.write(text)


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read().decode("latin-1")


@needs_stack
class TestFileIO(TempDir):
    """Each case is compiled with vcs and run with ./simv in its own directory: the output
    and every file it writes are vvp's, byte for byte."""

    def vcs(self, d, *args):
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        env.pop("PYTHONPATH", None)
        return run(["vcs"] + list(args), cwd=d, env=env, timeout=900)

    def simv(self, d, exe="./simv", cwd=None):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        return run([exe], cwd=cwd or d, env=env, timeout=900)

    def compile_case(self, name):
        d = os.path.join(self.tmp, name)
        write_case(d, name)
        c = self.vcs(d, "-sverilog", name + ".v")
        self.assertEqual(c.returncode, 0, c.stdout)
        # implemented: no "is not translated" warning for any of them
        self.assertNotRegex(c.stdout, r"system (task|function) \$(f|readmem|writemem)\w* is not "
                                      r"translated", c.stdout)
        return d

    def check_case(self, name):
        want_out, want_files = EXPECTED[name]
        d = self.compile_case(name)
        before = set(os.listdir(d))
        r = self.simv(d)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(testbench_lines(r.stdout), want_out, r.stdout)
        written = sorted(f for f in os.listdir(d) if f.startswith("out_"))
        self.assertEqual(written, sorted(want_files), r.stdout)
        for rel, text in sorted(want_files.items()):
            # in the directory ./simv ran in (R6F-02), byte for byte
            self.assertEqual(read_bytes(os.path.join(d, rel)), text, rel)
            self.assertFalse(os.path.exists(os.path.join(d, "simv.daidir", "nvc", rel)), rel)
        # nothing else of nvc's or the resolver plugin's lands where ./simv runs
        self.assertEqual(sorted(set(os.listdir(d)) - before - set(want_files)), [])
        return d

    def test_fopen_fdisplay_fwrite_fclose_fflush(self):
        self.check_case("fio_basic")

    def test_fstrobe_fmonitor(self):
        self.check_case("fio_strobe")

    def test_fstrobe_fmonitor_each_call_and_word(self):
        self.check_case("fio_multi")

    def test_readmem(self):
        self.check_case("rm_basic")

    def test_readmem_names_and_reals(self):
        self.check_case("fio_rmvar")

    def test_writemem(self):
        self.check_case("wm_basic")

    def test_always_task_and_after_a_wait(self):
        self.check_case("fio_proc")

    def test_descriptor_tables_formats_and_functions(self):
        self.check_case("fio_limits")

    def test_task_shared_memory_and_monitors(self):
        self.check_case("fio_edge")

    def test_arguments_are_evaluated_for_any_descriptor(self):
        self.check_case("fio_random")

    def test_radix_forms(self):
        self.check_case("fio_radix")

    def test_files_left_open_are_written_out(self):
        self.check_case("fio_noclose")
        self.check_case("fio_noclose2")

    def test_translation_shape(self):
        # R6F-03: a memory of 1-bit words takes sv_readmem_bit (sv_readmem_word, a vector,
        # failed nvc analysis), and the VHDL index is the address less the lowest one
        d = self.compile_case("rm_basic")
        with open(os.path.join(d, "simv.daidir", "nvc", "design.vhd"), errors="replace") as fh:
            vhd = fh.read()
        self.assertRegex(vhd, r"\bpr\(sv_readmem_addr\(sv_rm_i\)\) := sv_readmem_bit\(sv_rm_i\);")
        self.assertRegex(vhd, r"\bb4\(sv_readmem_addr\(sv_rm_i\) - 4\) := "
                              r"sv_readmem_word\(sv_rm_i, 8\);")
        self.assertRegex(vhd, r"\bneg\(sv_readmem_addr\(sv_rm_i\) \+ 2\) := ")
        self.assertNotIn("Unsupported system task", vhd)

    def test_relative_names_follow_the_directory_simv_starts_in(self):
        # R6F-02: ./simv run from another directory reads and writes there
        d = self.compile_case("fio_proc")
        other = os.path.join(self.tmp, "elsewhere")
        os.makedirs(other)
        with open(os.path.join(other, "rm_h1.dat"), "w") as fh:
            fh.write("aa bb cc dd\n")
        r = self.simv(d, exe=os.path.join(d, "simv"), cwd=other)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("m[1]=bb m[3]=dd", r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(other, "out_proc.txt")))
        self.assertEqual(read_bytes(os.path.join(other, "out_proc.hex")),
                         "// 0x00000000\n5a\nbb\ncc\ndd\n")
        self.assertEqual(sorted(os.listdir(other)), ["out_proc.hex", "out_proc.txt",
                                                     "rm_h1.dat"])
        self.assertFalse(os.path.exists(os.path.join(d, "out_proc.txt")))
        self.assertFalse(os.path.exists(os.path.join(d, "simv.daidir", "nvc", "out_proc.txt")))

    def test_memory_in_another_module_is_still_reported(self):
        # no translation: the located warning stays, never a silent drop
        d = os.path.join(self.tmp, "hier")
        os.makedirs(d)
        with open(os.path.join(d, "tb.v"), "w") as fh:
            fh.write("module mem; reg [7:0] m [0:3]; endmodule\n"
                     "module tb;\n  mem u ();\n  initial $readmemh(\"x.hex\", u.m);\nendmodule\n")
        c = self.vcs(d, "tb.v")
        self.assertEqual(c.returncode, 0, c.stdout)
        self.assertRegex(c.stdout, r"vamos: warning: tb\.v:4: system task \$readmemh is not "
                                   r"translated: the simulation drops it")

    def test_expected_is_vvps(self):
        """EXPECTED is what iverilog's vvp prints and writes for every case."""
        tools = vvp_tools()
        if tools is None:
            self.skipTest("no iverilog/vvp install for the reference runs")
        iv, vvp = tools
        for name in sorted(BATTERY):
            with self.subTest(case=name):
                ref = os.path.join(self.tmp, "vvp_" + name)
                write_case(ref, name)
                c = run([iv, "-g2012", "-o", name + ".vvp", name + ".v"], cwd=ref, timeout=300)
                self.assertEqual(c.returncode, 0, c.stdout)
                v = subprocess.run([vvp, "-n", name + ".vvp"], cwd=ref, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, universal_newlines=True,
                                   errors="replace", timeout=300)
                got = [ln for ln in testbench_lines(v.stdout)
                       if not re.search(r"\$finish called at", ln)]
                self.assertEqual(got, EXPECTED[name][0])
                files = {f: read_bytes(os.path.join(ref, f)) for f in os.listdir(ref)
                         if f.startswith("out_")}
                self.assertEqual(files, EXPECTED[name][1])


# ivtest pr690, as it is: a parameter file name, a [1:0] memory of 1-bit words
PR690 = r"""module test(CLK, OE, A, OUT);
parameter numAddr	= 1;
parameter numOut	= 1;
parameter wordDepth	= 2;
parameter MemFile       = "pr690.dat";

input CLK, OE;
input [numAddr-1:0] A;
output [numOut-1:0] OUT;

reg [numOut-1:0] memory[wordDepth-1:0];
reg [numAddr-1:0] addr;

initial begin
   $readmemb(MemFile,memory, 0);
   if (memory[0] !== 0) begin
      $display("FAILED -- memory[0] == %b", memory[0]);
      $finish;
   end
   if (memory[1] !== 1) begin
      $display("FAILED -- memory[1] == %b", memory[1]);
      $finish;
   end

   $display("PASSED");
end
endmodule
"""


@needs_stack
class TestPr690(TempDir):
    """R6F-03: upstream's $readmemb translation analysed a memory of 1-bit words as a
    vector (nvc: "type of value LOGIC3D_VECTOR does not match type of target LOGIC3D")."""

    def test_runs_like_vvp(self):
        d = os.path.join(self.tmp, "pr690")
        os.makedirs(d)
        with open(os.path.join(d, "pr690.v"), "w") as fh:
            fh.write(PR690)
        with open(os.path.join(d, "pr690.dat"), "w") as fh:
            fh.write("0\n1\n")
        env = dict(os.environ)
        env["PATH"] = SHIMS + os.pathsep + env.get("PATH", "")
        env.pop("PYTHONPATH", None)
        c = run(["vcs", "pr690.v"], cwd=d, env=env, timeout=900)
        self.assertEqual(c.returncode, 0, c.stdout)
        r = run(["./simv"], cwd=d, env=env, timeout=900)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(testbench_lines(r.stdout), [_behaviour("$readmemb"), "PASSED"])


AMS_TB = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  logic seen;
  integer f;
  reg [7:0] pat [0:3];
  always #5 clk = ~clk;
  rc_cell u1 (.in(clk), .out(seen));
  initial begin
    $readmemh("pat.hex", pat);
    f = $fopen("ams_out.txt", "w");
    $fdisplay(f, "pat0=%h pat3=%h", pat[0], pat[3]);
    $fstrobe(f, "strobe clk=%b", clk);
    $fmonitor(f, "mon clk=%b", clk);
  end
  initial #28 begin
    $fdisplay(f, "end");
    $fclose(f);
    $writememh("ams_pat.hex", pat);
    $finish;
  end
endmodule
"""
AMS_SP = """\
* RC cell; the capacitor node is buffered to the output
.subckt rc_cell in out
r1 in mid 500
c1 mid 0 0.5p
e1 out 0 mid 0 1
.ends
vsup sup 0 1.8
rsup sup 0 1meg
.tran 1p 100n
.end
"""

try:
    from ams_e2e_lib import AmsCase, needs_ams
except Exception:                       # pragma: no cover - the helper module is optional
    AmsCase, needs_ams = TempDir, unittest.skip("no AMS test helpers")


@needs_ams
class TestFileIOAms(AmsCase):
    def test_file_tasks_in_a_cosimulation(self):
        """vcs-ams accepts the file tasks (they were errors: "not translated"), and a
        co-simulation reads and writes where ./simv was started, not in its run directory."""
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.case("fio_" + engine, {"tb.sv": AMS_TB, "rc.sp": AMS_SP,
                                                "vcsAD.init": "choose xa rc.sp;\n",
                                                "pat.hex": "11 22 33 44\n"})
                c = self.compile(d, "-sverilog", "tb.sv", engine=engine)
                self.assertNotIn("not translated", c.stdout)
                self.simv(d)
                with open(os.path.join(d, "ams_out.txt")) as fh:
                    text = fh.read()
                self.assertEqual(text, "pat0=11 pat3=44\nstrobe clk=0\nmon clk=0\nmon clk=1\n"
                                       "mon clk=0\nmon clk=1\nmon clk=0\nmon clk=1\nend\n")
                with open(os.path.join(d, "ams_pat.hex")) as fh:
                    self.assertEqual(fh.read(), "// 0x00000000\n11\n22\n33\n44\n")
                # the run directory was removed: nothing of the testbench's was left in it
                self.assertEqual([n for n in os.listdir(d) if ".run." in n], [])


if __name__ == "__main__":
    unittest.main()
