"""$random's generator in plain VHDL: no VHPI library, and bit-exact with vvp.

The translator maps an unseeded $random -- and $urandom / $urandom_range -- onto
sv2vhdl.sv_math_pkg's `random'.  That function was VHPIDIRECT sv_random, which only a
--load'ed library provides (libsv_math.so).  nvc's own Verilog route
(`nvc --std=2040 -a x.v -e top -r', which src/nvc.c sends through iverilog-sv2ghdl; nvc's
regression test binary3 runs that way) loads none, and cannot (with a VHPI plugin loaded, a
unit that fell back to the native Verilog parser stops the run), so such a design died at
its first draw: "** Fatal: foreign function sv_random not found".  `random' and `srandom'
are now VHDL: vvp's rtl_dist_uniform(&seed, INT32_MIN, INT32_MAX) (vpi/sys_random.c) on one
design-wide seed starting at 0, the uint32 arithmetic in 16-bit halves, the doubles in C's
operation order, C's truncating casts by hand.

- plain nvc, no --load: an unseeded $random prints vvp's first 1000 numbers; $urandom prints
  vvp's $urandom sequence; $urandom_range prints vvp's numbers (round 6: sv_math_pkg's
  sv_urandom_range on $urandom's own generator; it was lo + ($urandom mod span))
- srandom(s) then random (a VHDL testbench: the translator never seeds it) is vvp's
  $random(seed) sequence from seed = s, for s = 0, 1, -1, 2**31-1, -2**31, 12345, and for the
  seeds whose next draw hits the edges of the double -> int32 casts: just over 2**31 (vvp,
  x86-64: INT32_MIN), just under 2**31, and an exact negative integer (cast after r - 1)
- with a library exporting sv_random --load'ed, `random' still draws vvp's numbers: the VHDL
  body is what runs
- bin/vvp-sv2ghdl, vamos vcs, and vcs-ams on each analog engine (all --load libresolver.so
  and libsv_math.so) print the same numbers
- a million draws print vvp's millionth number and run in about 0.2 s here, as the C did
  (the test's 10 s bound only catches a pathological slowdown)

    python3 -m unittest discover -s tests/vamos -p 'test_random_vhdl.py' -v

NVC_LIBDIR selects the nvc library tree (default: the one next to the nvc vamos finds).
Linux (WSL) only: the stack is Linux ELF.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from typing import Dict, List, Optional, Sequence, Tuple

from ams_e2e_lib import AmsCase, needs_ams
from vamos_testlib import ROOT, TempDir, have_stack

from vamos import tools  # noqa: E402

BIN = os.path.join(ROOT, "bin")
SHIMS = os.path.join(ROOT, "shims")
M32 = 1 << 32

needs_stack = unittest.skipUnless(have_stack(), "needs nvc + iverilog (Linux/WSL)")

# Seeds whose next draw lands on an edge of rtl_dist_uniform's casts (newseed = 69069 * seed
# + 1; k = newseed >> 9 alone decides the draw): k = 2**23 - 1 is the one draw whose double
# is past int32 (2147484159.99..., C's cast is undefined; x86-64 -- and so vvp -- gives
# INT32_MIN), k = 2**23 - 2 just under 2**31, k = 0 an exact negative integer (r = -2147483136,
# cast after r - 1), k = 1 the next one up; two seeds per k (newseed's low 9 bits 0 and 511).
EDGE_SEEDS = {-1798353157: -2147483648, -1271221770: -2147483648,
              -813611781: 2147483647, -286480394: 2147483647,
              1511872763: -2147483137, 2039004150: -2147483137,
              527131387: -2147482624, 1054262774: -2147482624,
              259341593: 303379748}         # a 0 seed stands for 259341593
MAIN_SEEDS = (0, 1, -1, 2 ** 31 - 1, -2 ** 31, 12345)


# -- tools -------------------------------------------------------------------------------

def _nvc() -> str:
    return tools.find_real("nvc") or ""


def _libdir() -> str:
    return tools.nvc_libdir(_nvc())


def _iverilog() -> str:
    return tools.find_real("iverilog") or ""


def _vvp() -> str:
    iverilog = _iverilog()
    cand = os.path.join(os.path.dirname(os.path.realpath(iverilog)), "vvp") if iverilog else ""
    if cand and os.access(cand, os.X_OK):
        return cand
    for c in (tools.find_real("vvp") or "", "/usr/local/src/iverilog/_install/bin/vvp"):
        if c and os.access(c, os.X_OK):
            return c
    return ""


def _env(**extra: str) -> Dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("VAMOS_ANALOG", None)
    env.update(extra)
    return env


def _run(cmd: Sequence[str], cwd: str, env: Optional[Dict[str, str]] = None,
         timeout: int = 600, nvc_verilog: bool = False) -> subprocess.CompletedProcess:
    """Run cmd, stdout and stderr merged.  nvc_verilog: cmd is nvc's own Verilog route,
    whose translation directory (/tmp/nvc_sv2ghdl_<pid>) is removed afterwards."""
    p = subprocess.Popen(list(cmd), cwd=cwd, env=env if env is not None else _env(),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True, errors="replace")
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
    if nvc_verilog:
        shutil.rmtree("/tmp/nvc_sv2ghdl_%d" % p.pid, ignore_errors=True)
    return subprocess.CompletedProcess(list(cmd), p.returncode, out, "")


_NOTE = re.compile(r"^\*\* Note: [^:]*: ")


def tagged(text: str) -> List[str]:
    """The `@ ...' lines of a run, in order (nvc's "** Note: <time>: " prefix dropped)."""
    out = []
    for ln in text.splitlines():
        ln = _NOTE.sub("", ln.strip())
        if ln.startswith("@ "):
            out.append(ln)
    return out


def values(lines: List[str], key: str) -> List[int]:
    """The integers after `@ <key> ' in tagged lines."""
    pre = "@ %s " % key
    return [int(ln[len(pre):].split()[0]) for ln in lines if ln.startswith(pre)]


# -- $urandom, from a $random draw ---------------------------------------------------------

def urandom_of(draw: int) -> int:
    """$urandom: the draw with bit 31 flipped (vvp: rtl_dist_uniform(...) - INT32_MIN)."""
    return (draw % M32) ^ 0x80000000


# -- vvp references ----------------------------------------------------------------------

_vvp_cache: Dict[Tuple[Tuple[Optional[int], int], ...], List[List[int]]] = {}


def vvp_sequences(cases: Sequence[Tuple[Optional[int], int]]) -> List[List[int]]:
    """vvp's $random draws, one list per (seed, n): n unseeded draws (seed None: the internal
    seed, which carries on from case to case), or n draws of $random(seed) from seed = s."""
    key = tuple(cases)
    if key in _vvp_cache:
        return _vvp_cache[key]
    lines = ["module vvpref;", "  integer seed, x, k;", "  initial begin"]
    for i, (seed, n) in enumerate(cases):
        if seed is None:
            draw = "x = $random;"
        else:
            lines.append("    seed = 32'sh%08X;" % (seed % M32))
            draw = "x = $random(seed);"
        lines.append("    for (k = 0; k < %d; k = k + 1) begin %s $display(\"@ c%d %%0d\", x); end"
                     % (n, draw, i))
    lines += ["    $finish;", "  end", "endmodule", ""]
    tmp = tempfile.mkdtemp(prefix="vamos-rnd-vvp-")
    try:
        with open(os.path.join(tmp, "ref.v"), "w") as fh:
            fh.write("\n".join(lines))
        c = _run([_iverilog(), "-g2012", "-o", "ref.vvp", "ref.v"], tmp)
        if c.returncode != 0:
            raise RuntimeError("iverilog: " + c.stdout)
        r = _run([_vvp(), "-n", "ref.vvp"], tmp)
        if r.returncode != 0:
            raise RuntimeError("vvp: " + r.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    got = tagged(r.stdout)
    seqs = [values(got, "c%d" % i) for i in range(len(cases))]
    for (seed, n), seq in zip(cases, seqs):
        if len(seq) != n:
            raise RuntimeError("vvp printed %d of %d draws (seed %r)" % (len(seq), n, seed))
    _vvp_cache[key] = seqs
    return seqs


def vvp_draws(n: int) -> List[int]:
    """vvp's first n unseeded $random draws."""
    return vvp_sequences(((None, 1000 if n <= 1000 else n),))[0][:n]


_vvp_tagged_cache: Dict[str, List[str]] = {}


def vvp_tagged(src: str) -> List[str]:
    """The `@ ...' lines vvp prints for Verilog source src."""
    if src not in _vvp_tagged_cache:
        tmp = tempfile.mkdtemp(prefix="vamos-rnd-vvp-")
        try:
            with open(os.path.join(tmp, "ref.v"), "w") as fh:
                fh.write(src)
            c = _run([_iverilog(), "-g2012", "-o", "ref.vvp", "ref.v"], tmp)
            if c.returncode != 0:
                raise RuntimeError("iverilog: " + c.stdout)
            r = _run([_vvp(), "-n", "ref.vvp"], tmp)
            if r.returncode != 0:
                raise RuntimeError("vvp: " + r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        _vvp_tagged_cache[src] = tagged(r.stdout)
    return _vvp_tagged_cache[src]


# -- designs ------------------------------------------------------------------------------

RAND1000 = """\
`timescale 1ns/1ps
module tb;
  integer k, r;
  initial begin
    for (k = 0; k < 1000; k = k + 1) begin
      r = $random;
      $display("@ r %0d", r);
    end
    $finish;
  end
endmodule
"""

# 200 $urandom draws, then 200 x 4 $urandom_range draws, all from vvp's $urandom generator
# (its own seed): the first 200 are vvp's $urandom sequence, the $urandom_range rows vvp's.
URAND = """\
`timescale 1ns/1ps
module tb;
  integer k;
  int unsigned u, v1, v2, v3, v4;
  initial begin
    for (k = 0; k < 200; k = k + 1) begin
      u = $urandom;
      $display("@ u %0d", u);
    end
    for (k = 0; k < 200; k = k + 1) begin
      v1 = $urandom_range(10, 3);
      v2 = $urandom_range(7);
      v3 = $urandom_range(3, 10);
      v4 = $urandom_range(32'hFFFFFFFF, 32'hFFFFFFF0);
      $display("@ v %0d %0d %0d %0d", v1, v2, v3, v4);
    end
    $finish;
  end
endmodule
"""


def urand_expected() -> Tuple[List[int], List[List[int]]]:
    """URAND's u values (vvp's first 200 $random draws, bit 31 flipped: $urandom's generator
    starts where $random's does) and v rows (vvp's)."""
    d = vvp_draws(1000)
    us = [urandom_of(x) for x in d[:200]]
    return us, v_rows(vvp_tagged(URAND))


def v_rows(lines: List[str]) -> List[List[int]]:
    return [[int(t) for t in ln.split()[2:]] for ln in lines if ln.startswith("@ v ")]


def seeded_tb(name: str, cases: Sequence[Tuple[int, int]]) -> str:
    """A VHDL testbench: for each (seed, n), srandom(seed) then n draws, printed as
    `@ s<i> <draw>'."""
    body = []
    for i, (seed, n) in enumerate(cases):
        lit = "-2147483647 - 1" if seed == -2 ** 31 else str(seed)
        body += ["        srandom(%s);" % lit,
                 "        for k in 1 to %d loop" % n,
                 "            x := random;",
                 "            write(l, string'(\"@ s%d \"));" % i,
                 "            write(l, x);",
                 "            writeline(output, l);",
                 "        end loop;"]
    return "\n".join([
        "library sv2vhdl;",
        "use sv2vhdl.sv_math_pkg.all;",
        "use std.textio.all;",
        "",
        "entity %s is" % name,
        "end entity;",
        "",
        "architecture a of %s is" % name,
        "begin",
        "    process",
        "        variable l : line;",
        "        variable x : integer;",
        "    begin"] + body + [
        "        wait;",
        "    end process;",
        "end architecture;",
        ""])


def run_plain_nvc_vhdl(tmp: str, src: str, top: str, *load: str) -> subprocess.CompletedProcess:
    """Analyse, elaborate and run a VHDL testbench with plain nvc (--load only what is given)."""
    with open(os.path.join(tmp, top + ".vhd"), "w") as fh:
        fh.write(src)
    lib = _libdir()
    opts = ["--std=2040", "-L", lib] + (["--load=" + ",".join(load)] if load else [])
    return _run([_nvc()] + opts + ["-a", top + ".vhd", "-e", top, "-r"], tmp,
                _env(NVC_LIBPATH=lib))


def run_plain_nvc_verilog(tmp: str, src: str, top: str = "tb") -> subprocess.CompletedProcess:
    """nvc's own Verilog route: `nvc --std=2040 -a tb.v -e tb -r', no --load (src/nvc.c sends
    the .v through iverilog-sv2ghdl -- this checkout's, via SV2GHDL)."""
    with open(os.path.join(tmp, "tb.v"), "w") as fh:
        fh.write(src)
    env = _env(NVC_LIBPATH=_libdir(), SV2GHDL=os.path.join(BIN, "iverilog-sv2ghdl"),
               IVERILOG=_iverilog())
    return _run([_nvc(), "--std=2040", "-a", "tb.v", "-e", top, "-r"], tmp, env,
                nvc_verilog=True)


class _Cached(unittest.TestCase):
    """One scratch directory per class; runs cached by name."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vamos-rnd-")
        cls.runs = {}

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("VAMOS_TEST_KEEP"):
            print("kept %s" % cls.tmp)
        else:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def cached(self, name: str, fn) -> subprocess.CompletedProcess:
        if name not in self.runs:
            d = os.path.join(self.tmp, name)
            os.makedirs(d, exist_ok=True)
            self.runs[name] = fn(d)
        return self.runs[name]

    def assertRan(self, r: subprocess.CompletedProcess):
        self.assertNotIn("not found", r.stdout)            # foreign function sv_random
        self.assertNotIn("** Fatal", r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])


# -- plain nvc (no --load): the Verilog route ----------------------------------------------

@needs_stack
class TestPlainNvcVerilog(_Cached):
    """`nvc --std=2040 -a tb.v -e tb -r' runs a $random / $urandom testbench with no VHPI
    library loaded and prints vvp's numbers."""

    def plain(self, name: str, src: str) -> List[str]:
        r = self.cached(name, lambda d: run_plain_nvc_verilog(d, src))
        self.assertRan(r)
        return tagged(r.stdout)

    def test_random_first_1000_like_vvp(self):
        got = values(self.plain("rand", RAND1000), "r")
        self.assertEqual(got, vvp_draws(1000))
        self.assertEqual(got[:3], [303379748, -1064739199, -2071669239])

    def test_urandom_like_vvp(self):
        """vvp's own $urandom sequence (its second seed, which only $urandom draws here)."""
        def ref(d):
            with open(os.path.join(d, "ref.v"), "w") as fh:
                fh.write(URAND)
            c = _run([_iverilog(), "-g2012", "-o", "ref.vvp", "ref.v"], d)
            return c if c.returncode else _run([_vvp(), "-n", "ref.vvp"], d)
        v = self.cached("urand_vvp", ref)
        self.assertEqual(v.returncode, 0, v.stdout)
        want = values(tagged(v.stdout), "u")
        self.assertEqual(want, urand_expected()[0])
        got = values(self.plain("urand", URAND), "u")
        self.assertEqual(got, want)
        self.assertEqual(got[:3], [2450863396, 1082744449, 75814409])

    def test_urandom_range_like_vvp(self):
        rows = v_rows(self.plain("urand", URAND))
        self.assertEqual(len(rows), 200)
        self.assertEqual(rows, urand_expected()[1])
        for v1, v2, v3, v4 in rows:
            self.assertTrue(3 <= v1 <= 10 and v2 <= 7 and 3 <= v3 <= 10 and v4 >= 0xFFFFFFF0)


# -- srandom(s) then random: a VHDL testbench, plain nvc ------------------------------------

@needs_stack
class TestSrandomLikeVvp(_Cached):
    """srandom(s) then random is vvp's $random(seed) sequence from seed = s."""

    CASES = [(s, 1000) for s in MAIN_SEEDS] + [(s, 3) for s in sorted(EDGE_SEEDS)]

    def setUp(self):
        r = self.cached("seeded", lambda d: run_plain_nvc_vhdl(
            d, seeded_tb("tbseed", self.CASES), "tbseed"))
        self.assertRan(r)
        lines = tagged(r.stdout)
        self.got = {seed: values(lines, "s%d" % i) for i, (seed, _) in enumerate(self.CASES)}

    def test_seeds_like_vvp(self):
        want = vvp_sequences(self.CASES)
        for (seed, _), w in zip(self.CASES, want):
            with self.subTest(seed=seed):
                self.assertEqual(self.got[seed], w)

    def test_cast_edges(self):
        """The first draw after each edge seed, as vvp prints it on this machine."""
        want = vvp_sequences([(s, 1) for s in sorted(EDGE_SEEDS)])
        for s, w in zip(sorted(EDGE_SEEDS), want):
            with self.subTest(seed=s):
                self.assertEqual(w[0], EDGE_SEEDS[s])           # the edge is what vvp does
                self.assertEqual(self.got[s][0], EDGE_SEEDS[s])

    def test_seed_zero_is_the_unseeded_sequence(self):
        self.assertEqual(self.got[0], vvp_draws(1000))
        self.assertEqual(self.got[0][0], 303379748)


# -- the VHDL body is what runs, whatever is --load'ed ---------------------------------------

STUB_C = """\
#include <stdint.h>
/* the old VHPIDIRECT entry points, answering what vvp never would */
int32_t sv_random(void) { return 42; }
void sv_srandom(int32_t seed) { (void)seed; }
"""

STUB_TB = """\
library sv2vhdl;
use sv2vhdl.sv_math_pkg.all;
use std.textio.all;

entity tbstub is
end entity;

architecture a of tbstub is
begin
    process
        variable l : line;
        variable x : integer;
    begin
        for k in 1 to 3 loop
            x := random;
            write(l, string'("@ u "));
            write(l, x);
            writeline(output, l);
        end loop;
        srandom(12345);
        x := random;
        write(l, string'("@ s "));
        write(l, x);
        writeline(output, l);
        wait;
    end process;
end architecture;
"""


def _cc() -> str:
    return shutil.which("gcc") or shutil.which("cc") or ""


@needs_stack
class TestLoadedLibraries(TempDir):
    """Where libraries are --load'ed (vvp-sv2ghdl and vamos load libresolver.so and
    libsv_math.so, which keeps a sv_random of its own), the numbers are the same: `random'
    is the VHDL body."""

    @unittest.skipUnless(_cc(), "needs a C compiler")
    def test_vhdl_body_wins_over_a_loaded_sv_random(self):
        """A library whose sv_random answers 42, --load'ed: random still draws vvp's numbers
        (through the old VHPIDIRECT declaration it drew 42s)."""
        with open(os.path.join(self.tmp, "stub.c"), "w") as fh:
            fh.write(STUB_C)
        stub = os.path.join(self.tmp, "libstub.so")
        c = _run([_cc(), "-shared", "-fPIC", "-o", stub, "stub.c"], self.tmp)
        self.assertEqual(c.returncode, 0, c.stdout)
        r = run_plain_nvc_vhdl(self.tmp, STUB_TB, "tbstub", stub)
        self.assertEqual(r.returncode, 0, r.stdout)
        got = tagged(r.stdout)
        self.assertEqual(values(got, "u"), vvp_draws(3))
        self.assertEqual(values(got, "s"), vvp_sequences([(12345, 1)])[0])

    def test_libsv_math_loaded(self):
        """libsv_math.so, which keeps a sv_random of its own, --load'ed by hand."""
        lib = os.path.join(_libdir(), "sv2vhdl", "libsv_math.so")
        if not os.path.isfile(lib):
            self.skipTest("no %s" % lib)
        r = run_plain_nvc_vhdl(self.tmp, STUB_TB, "tbstub", lib)
        self.assertEqual(r.returncode, 0, r.stdout)
        got = tagged(r.stdout)
        self.assertEqual(values(got, "u"), vvp_draws(3))
        self.assertEqual(values(got, "s"), vvp_sequences([(12345, 1)])[0])

    def test_vvp_sv2ghdl(self):
        """bin/iverilog-sv2ghdl + bin/vvp-sv2ghdl (--load libresolver.so,libsv_math.so)."""
        with open(os.path.join(self.tmp, "tb.v"), "w") as fh:
            fh.write(RAND1000)
        env = _env(IVERILOG=_iverilog(), NVC=_nvc(), NVC_LIBDIR=_libdir())
        x = _run([os.path.join(BIN, "iverilog-sv2ghdl"), "-o", "vsim", "-g2012", "-s", "tb",
                  "tb.v"], self.tmp, env)
        self.assertEqual(x.returncode, 0, x.stdout)
        r = _run([os.path.join(BIN, "vvp-sv2ghdl"), "vsim"], self.tmp, env)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("not found", r.stdout)
        self.assertEqual(values(tagged(r.stdout), "r"), vvp_draws(1000))

    def vcs(self, src: str) -> List[str]:
        with open(os.path.join(self.tmp, "tb.sv"), "w") as fh:
            fh.write(src)
        env = _env(PATH=SHIMS + os.pathsep + os.environ.get("PATH", ""))
        c = _run([os.path.join(SHIMS, "vcs"), "-sverilog", "tb.sv"], self.tmp, env)
        self.assertEqual(c.returncode, 0, c.stdout)
        r = _run(["./simv"], self.tmp, _env())
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("not found", r.stdout)
        return tagged(r.stdout)

    def test_vcs_random(self):
        self.assertEqual(values(self.vcs(RAND1000), "r"), vvp_draws(1000))

    def test_vcs_urandom(self):
        got = self.vcs(URAND)
        us, vs = urand_expected()
        self.assertEqual(values(got, "u"), us)
        self.assertEqual(v_rows(got), vs)


# -- vcs-ams, each analog engine ----------------------------------------------------------

AMS_TB = """\
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  logic seen;
  integer k, r;
  int unsigned u;
  always #5 clk = ~clk;
  rc_cell u1 (.in(clk), .out(seen));
  initial begin
    for (k = 0; k < 50; k = k + 1) begin
      r = $random;
      $display("@ r %0d", r);
    end
    for (k = 0; k < 10; k = k + 1) begin
      u = $urandom_range(1000, 10);
      $display("@ v %0d", u);
    end
    #20 $finish;
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


# AMS_TB's draws alone, for vvp: $urandom_range draws from $urandom's own generator (vvp's
# second seed), not on from the 50 $random draws
AMS_DRAWS_V = """\
module tb;
  integer k, r;
  int unsigned u;
  initial begin
    for (k = 0; k < 50; k = k + 1) r = $random;
    for (k = 0; k < 10; k = k + 1) begin
      u = $urandom_range(1000, 10);
      $display("@ v %0d", u);
    end
  end
endmodule
"""


@needs_ams
class TestVcsAms(AmsCase):
    """vcs-ams (nvc + an analog engine; the digital side --loads both libraries)."""

    def test_random_each_engine(self):
        d = vvp_draws(60)
        want_r = d[:50]
        want_v = values(vvp_tagged(AMS_DRAWS_V), "v")
        for engine in self.engines():
            with self.subTest(engine=engine):
                case = self.case("rnd_" + engine, {"tb.sv": AMS_TB, "rc.sp": AMS_SP,
                                                   "vcsAD.init": "choose xa rc.sp;\n"})
                self.compile(case, "-sverilog", "tb.sv", engine=engine)
                out = self.simv(case).stdout
                self.assertNotIn("not found", out)
                got = tagged(out)
                self.assertEqual(values(got, "r"), want_r)
                self.assertEqual(values(got, "v"), want_v)


# -- speed -----------------------------------------------------------------------------------

TIME_TB = """\
library sv2vhdl;
use sv2vhdl.sv_math_pkg.all;

entity tbtime is
end entity;

architecture a of tbtime is
begin
    process
        variable x, acc : integer := 0;
    begin
        for i in 1 to 1000000 loop
            x := random;
            acc := (acc + (x mod 1024)) mod 65536;
        end loop;
        report "@ acc " & integer'image(acc) & " last " & integer'image(x);
        wait;
    end process;
end architecture;
"""

TIME_V = """\
module vvpref;
  integer i, x, acc;
  initial begin
    acc = 0;
    for (i = 0; i < 1000000; i = i + 1) begin
      x = $random;
      acc = (acc + (x & 1023)) % 65536;
    end
    $display("@ acc %0d last %0d", acc, x);
    $finish;
  end
endmodule
"""


@needs_stack
class TestSpeed(TempDir):
    def test_million_draws(self):
        """1e6 draws: vvp's last number and checksum, in a sane time (about 0.2 s here, the
        VHPIDIRECT C took about 0.15 s; the bound only catches a pathological slowdown)."""
        lib = _libdir()
        with open(os.path.join(self.tmp, "tbtime.vhd"), "w") as fh:
            fh.write(TIME_TB)
        env = _env(NVC_LIBPATH=lib)
        e = _run([_nvc(), "--std=2040", "-L", lib, "-a", "tbtime.vhd", "-e", "tbtime"],
                 self.tmp, env)
        self.assertEqual(e.returncode, 0, e.stdout)
        t0 = time.time()
        r = _run([_nvc(), "--std=2040", "-L", lib, "-r", "tbtime"], self.tmp, env)
        dt = time.time() - t0
        self.assertEqual(r.returncode, 0, r.stdout)
        with open(os.path.join(self.tmp, "ref.v"), "w") as fh:
            fh.write(TIME_V)
        c = _run([_iverilog(), "-g2012", "-o", "ref.vvp", "ref.v"], self.tmp)
        self.assertEqual(c.returncode, 0, c.stdout)
        want = tagged(_run([_vvp(), "-n", "ref.vvp"], self.tmp).stdout)
        self.assertTrue(want)
        self.assertEqual(tagged(r.stdout), want)
        self.assertLess(dt, 10.0, "1e6 draws took %.1f s" % dt)


if __name__ == "__main__":
    unittest.main()
