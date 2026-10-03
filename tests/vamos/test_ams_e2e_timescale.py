"""vcs-ams end-to-end: the digital and the analog share one absolute time axis for every
`timescale` (docs/VAMOS_AMS_DESIGN.md §1.2).

tgt-vhdl used to translate one tick of a precision of 10 or 100 of a VHDL unit as ONE
unit (a 10 ps tick as `1 ps`, a 10 ns tick as `1 ns`).  In AMS mode the digital time is
the analog engine's absolute time, so under `timescale 1ns/10ps every Verilog delay
reached the analog 10x too short (100x at 1ns/100ps) while $realtime still looked right,
and +vcs+finish+N stopped at the wrong time; compile and run exited 0 with no message.

Each case drives an RC cell (r 10k, c 1p behind the D2A's 500.7 ohm, buffered to `out`)
with a 50 ns half-period clock written in the bench's own unit, on each engine:

- the precisions 1ns/10ps, 1ns/100ps and 10ns/10ns, and 1ns/1ps as the control: the
  translated clock wait is 50 ns in SI; the digital prints its clock edges at 50, 100, ...
  ns and the analog input ramps at exactly those times in the rawfile; the RC output
  crosses half supply one closed-form delay later and the digital sees each crossing
  (to within its precision); the run ends at the digital stop, 300 ns;
- the same with no `timescale and -override_timescale=1ns/10ps;
- +vcs+finish+20000 at 1ns/10ps stops both sides at 200 ns;
- `timescale 1ns/1fs: %t of $realtime prints the A2D event times past 2^31 ticks
  (2.147 us), where the translated integer() cast stopped the run.

    cd tests/vamos && python3 -m unittest test_ams_e2e_timescale -v

Needs the whole stack (nvc, iverilog, VACASK and/or Xyce): WSL/Linux.  VAMOS_TEST_KEEP=1
keeps the case directories.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import sys
import tempfile
import unittest
from typing import Dict, List, Optional, Tuple

from ams_e2e_lib import AmsCase, engines_available, needs_ams

ENGINES = ("vacask", "xyce")
NS = 1e-9
PS = 1e-12
FS = 1e-15

VSUP = 1.8
RSER = 500.7          # D2A series resistance (VCS rmap strength 6, §3.4)
TRAMP = 1e-11         # default D2A rise/fall time
TAU = (RSER + 10e3) * 1e-12


def rc_crossings(edges: List[float], half: float = 50 * NS) -> List[float]:
    """When the buffered RC node crosses half supply after each clock edge (closed form).

    The node charges towards VSUP after a rising edge and towards 0 after a falling one,
    from where the previous half period left it (a 50 ns half period is 4.8 time constants:
    not quite settled); the 10 ps D2A ramp delays a first-order response by TRAMP/2."""
    v, out = 0.0, []
    for k, e in enumerate(edges):
        target = VSUP if k % 2 == 0 else 0.0
        out.append(e + TRAMP / 2 + TAU * math.log((v - target) / (VSUP / 2 - target)))
        v = target + (v - target) * math.exp(-half / TAU)
    return out

RC_SP = """\
* RC cell with a buffer
.subckt rc_cell in out
r1 in mid 10k
c1 mid 0 1p
e1 out 0 mid 0 1
.ends
vsup vdd 0 1.8
.tran %s %s
.end
"""

# the bench in its own time unit: `half` units = 50 ns, `stop` units = 300 ns; times are
# printed in ns ($realtime times the unit in ns)
TB = """\
`timescale %(ts)s
module tb;
  reg clk = 0;
  wire out;
  always #%(half)s clk = ~clk;
  rc_cell u1 (.in(clk), .out(out));
  always @(clk) $display("T %%0.6f clk=%%b", $realtime * %(ns)s, clk);
  always @(out) $display("T %%0.6f out=%%b", $realtime * %(ns)s, out);
  initial #%(stop)s $finish;
endmodule
"""

# (`timescale, units per 50 ns, units per 300 ns, ns per unit, precision in s)
CASES = {
    "1ps": ("1ns/1ps", "50", "300", "1.0", 1 * PS),
    "10ps": ("1ns/10ps", "50", "300", "1.0", 10 * PS),
    "100ps": ("1ns/100ps", "50", "300", "1.0", 100 * PS),
    "10ns": ("10ns/10ns", "5", "30", "10.0", 10 * NS),
}

# 1 fs precision: A2D event times printed with %t past 2^31 ticks
TB_FS = """\
`timescale 1ns/1fs
module tb;
  reg clk = 0;
  wire out;
  always #500 clk = ~clk;
  rc_cell u1 (.in(clk), .out(out));
  always @(out) $display("T %0.6f out=%b", $realtime, out);
  always @(out) $display("TT %t out=%b", $realtime, out);
  initial #3100 $finish;
endmodule
"""

_EVENT = re.compile(r"^T\s+([0-9]+\.[0-9]+)\s+(\w+)=(\S+)\s*$")
_END = re.compile(r"co-simulation finished: (digital stop at|analog end at) ([-+0-9.eE]+) s")
_WAIT = re.compile(r"\bwait for (\d+) (fs|ps|ns|us|ms|sec);")
_UNIT_S = {"fs": FS, "ps": PS, "ns": NS, "us": 1e-6, "ms": 1e-3, "sec": 1.0}


def events(out: str, tag: str) -> List[Tuple[float, str]]:
    """(time in s, value) of every `T <ns> <tag>=<v>` line after t=0, in order."""
    ev = []
    for line in out.splitlines():
        m = _EVENT.match(line.strip())
        if m and m.group(2) == tag and float(m.group(1)) > 0.0:
            ev.append((float(m.group(1)) * NS, m.group(3)))
    return ev


def _per_engine(cls):
    """Every `check_<name>(self, engine)` becomes test_<name>_vacask and test_<name>_xyce."""
    for name in sorted(vars(cls)):
        if not name.startswith("check_"):
            continue
        fn = getattr(cls, name)
        for eng in ENGINES:
            def test(self, fn=fn, eng=eng):
                if eng not in engines_available():
                    self.skipTest("%s is not installed" % eng)
                fn(self, eng)
            test.__name__ = "test_%s_%s" % (name[len("check_"):], eng)
            test.__doc__ = "%s [%s]" % ((fn.__doc__ or name).strip().splitlines()[0], eng)
            setattr(cls, test.__name__, test)
    return cls


class _Result:
    def __init__(self, d: str, comp, sim):
        self.d, self.comp, self.sim = d, comp, sim

    @property
    def sout(self) -> str:
        return self.sim.stdout if self.sim is not None else ""


@needs_ams
@_per_engine
class TestE2ETimescale(AmsCase):
    """Each (case, engine) is compiled and run once per class."""

    _root = None    # type: Optional[str]
    _cache = None   # type: Optional[Dict[Tuple[str, str], _Result]]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._root = tempfile.mkdtemp(prefix="vamos-e2ets-")
        cls._cache = {}

    @classmethod
    def tearDownClass(cls):
        if cls._root:
            if os.environ.get("VAMOS_TEST_KEEP"):
                sys.stderr.write("kept %s\n" % cls._root)
            else:
                shutil.rmtree(cls._root, ignore_errors=True)
        super().tearDownClass()

    def run_case(self, key: str, tb: str, tran: Tuple[str, str], engine: str,
                 simv_args: Tuple[str, ...] = (), args: Tuple[str, ...] = ()) -> _Result:
        ck = (key, engine)
        if ck not in self._cache:
            d = os.path.join(self._root, "%s_%s" % (key, engine))
            os.makedirs(d)
            for rel, text in (("tb.sv", tb), ("rc.sp", RC_SP % tran),
                              ("vcsAD.init", "choose xa rc.sp;\n")):
                with open(os.path.join(d, rel), "w") as fh:
                    fh.write(text)
            comp = self.compile(d, "-sverilog", "tb.sv", *args, engine=engine, expect_rc=None)
            sim = None
            if comp.returncode == 0 and os.path.isfile(os.path.join(d, "simv")):
                sim = self.simv(d, *simv_args, expect_rc=None)
            self._cache[ck] = _Result(d, comp, sim)
        return self._cache[ck]

    def precision_case(self, key: str, engine: str) -> _Result:
        ts, half, stop, ns, _ = CASES[key]
        return self.run_case(key, TB % {"ts": ts, "half": half, "stop": stop, "ns": ns},
                             ("10p", "400n"), engine)

    # -- assertions -----------------------------------------------------------------------

    def assertEnd(self, r: _Result, t_end: float) -> None:
        self.assertEqual(r.comp.returncode, 0, r.comp.stdout)
        self.assertIsNotNone(r.sim, r.comp.stdout)
        self.assertEqual(r.sim.returncode, 0, r.sout)
        ends = _END.findall(r.sout)
        self.assertEqual(len(ends), 1, "want exactly one end-of-run line:\n" + r.sout)
        self.assertAlmostEqual(float(ends[0][1]), t_end, delta=t_end * 1e-9, msg=r.sout)
        self.assertNotIn("** Fatal", r.sout)
        self.assertNotIn("vamos: error", r.sout)

    def assertWaitsSI(self, r: _Result, *want: float) -> None:
        """The translated design's `wait for` literals include each wanted SI time."""
        with open(os.path.join(r.d, "simv.daidir", "nvc", "design.vhd"), errors="replace") as fh:
            waits = [int(n) * _UNIT_S[u] for n, u in _WAIT.findall(fh.read())]
        for w in want:
            self.assertTrue(any(abs(x - w) <= w * 1e-9 for x in waits), (w, waits))

    def assertClockOnTime(self, r: _Result, raw, t_end: float) -> List[float]:
        """The digital prints its clock edges at 50, 100, ... ns (before t_end), and the
        D2A source ramps through half supply TRAMP/2 after each, in the rawfile."""
        clk = [e for e in events(r.sout, "clk") if e[0] < t_end - PS]
        edges = [50 * NS * k for k in range(1, len(clk) + 1)]
        self.assertEqual(len(clk), int(round(t_end / (50 * NS))) - 1, r.sout)
        self.assertEqual([v for _, v in clk], ["1" if k % 2 else "0" for k in range(1, len(clk) + 1)])
        for (t, _), e in zip(clk, edges):
            self.assertAlmostEqual(t, e, delta=1 * PS, msg="digital clock edge")
        xs = [x for x in raw.crossings("n_u1_in_d", VSUP / 2) if x < t_end - PS]
        self.assertEqual(len(xs), len(edges), xs)
        for x, e in zip(xs, edges):
            self.assertAlmostEqual(x, e + TRAMP / 2, delta=1 * PS, msg="analog input ramp")
        return edges

    def assertOutFollows(self, r: _Result, raw, edges: List[float], tick: float) -> None:
        """The RC output crosses half supply when the closed form says after each edge,
        and the digital prints one event per crossing, no earlier than the crossing
        truncated to its precision and no later than the next stored analog point."""
        xs = raw.crossings("n_u1_out", VSUP / 2)
        self.assertEqual(len(xs), len(edges), xs)
        for x, want in zip(xs, rc_crossings(edges)):
            self.assertAlmostEqual(x, want, delta=20 * PS, msg="RC output crossing")
        ev = events(r.sout, "out")
        self.assertEqual([v for _, v in ev], ["1" if k % 2 == 0 else "0" for k in range(len(xs))],
                         r.sout)
        times = raw.time()
        for (te, _), x in zip(ev, xs):
            nxt = min(t for t in times if t >= x)
            self.assertGreaterEqual(te, x - tick - 2 * PS, "event %.6g s, crossing %.6g s" % (te, x))
            self.assertLessEqual(te, nxt + 2 * PS, "event %.6g s, next point %.6g s" % (te, nxt))

    def _precision(self, engine: str, key: str, r: Optional[_Result] = None) -> None:
        r = r or self.precision_case(key, engine)
        self.assertEnd(r, 300 * NS)
        self.assertEqual(r.sout.count("digital stop at"), 1, r.sout)
        self.assertWaitsSI(r, 50 * NS, 300 * NS)
        raw = self.raw(r.d)
        self.assertAlmostEqual(raw.last_time(), 300 * NS, delta=1 * PS)
        edges = self.assertClockOnTime(r, raw, 300 * NS)
        self.assertOutFollows(r, raw, edges, CASES[key][4])

    # -- the items ----------------------------------------------------------------------

    def check_1ns_1ps(self, engine):
        """`timescale 1ns/1ps (control): one time axis, digital stop at 300 ns"""
        self._precision(engine, "1ps")

    def check_1ns_10ps(self, engine):
        """`timescale 1ns/10ps: #50 is 50 ns on both sides (it was 5 ns)"""
        self._precision(engine, "10ps")

    def check_1ns_100ps(self, engine):
        """`timescale 1ns/100ps: #50 is 50 ns on both sides (it was 0.5 ns)"""
        self._precision(engine, "100ps")

    def check_10ns_10ns(self, engine):
        """`timescale 10ns/10ns: #5 is 50 ns on both sides (it was 5 ns)"""
        self._precision(engine, "10ns")

    def check_override_1ns_10ps(self, engine):
        """no `timescale, -override_timescale=1ns/10ps: #50 is 50 ns on both sides"""
        _, half, stop, ns, _ = CASES["10ps"]
        tb = (TB % {"ts": "1ns/10ps", "half": half, "stop": stop, "ns": ns}).split("\n", 1)[1]
        r = self.run_case("override10ps", tb, ("10p", "400n"), engine,
                          args=("-override_timescale=1ns/10ps",))
        self.assertTrue(r.comp.returncode == 0 and "`timescale" not in tb, r.comp.stdout)
        self._precision(engine, "10ps", r)

    def check_finish_at_10ps(self, engine):
        """+vcs+finish+20000 at 10 ps precision stops both sides at 200 ns"""
        ts, half, stop, ns, _ = CASES["10ps"]
        r = self.run_case("finish10ps", TB % {"ts": ts, "half": half, "stop": stop, "ns": ns},
                          ("10p", "400n"), engine, simv_args=("+vcs+finish+20000",))
        self.assertEnd(r, 200 * NS)
        raw = self.raw(r.d)
        self.assertAlmostEqual(raw.last_time(), 200 * NS, delta=1 * PS)
        edges = self.assertClockOnTime(r, raw, 200 * NS)
        self.assertOutFollows(r, raw, edges, 10 * PS)

    def check_percent_t_past_2e31_fs(self, engine):
        """`timescale 1ns/1fs: %t of $realtime prints A2D times past 2.147 us"""
        r = self.run_case("fs3us", TB_FS, ("100p", "3.2u"), engine)
        self.assertEnd(r, 3100 * NS)
        exact = events(r.sout, "out")
        self.assertEqual(len(exact), 6, r.sout)                  # 0.5 ... 3 us
        printed = [ln.strip() for ln in r.sout.splitlines() if ln.startswith("TT ")]
        printed = [ln for ln in printed if not re.match(r"^TT\s+0 ", ln)]
        self.assertEqual(len(printed), len(exact), printed)
        for ln, (t, v) in zip(printed, exact):
            self.assertRegex(ln, r"^TT\s+%d out=%s$" % (int(round(t / FS)), v))
        self.assertGreater(exact[-1][0], 2 ** 31 * FS)


if __name__ == "__main__":
    unittest.main()
