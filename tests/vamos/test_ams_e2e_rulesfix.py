"""End-to-end checks of the rules fixes on both engines (docs/VAMOS_AMS_DESIGN.md §2.2, §3.4-§3.6).

    python3 -m unittest discover -s tests/vamos -p 'test_ams_e2e_rulesfix.py' -v

One design carries every kind of interface-element report line (unit side:
tests/vamos/test_ams_rules_fixes.py):

  * half: a D2A with % levels, split ramps and delays and x2v, feeding three A2D
    outputs held mid-band; midv_logic Z, X and 1 (Irules-04: Z used to run as X)
  * load: a strong and a weak D2A into 1 kohm loads through `map_by_node r=1k`
    (Irules-05: map_by_node used to be ignored, leaving 500.7 / 3500.2 ohm)
  * pwr: supply1/supply0 rails set by `d2a powernet` rules, a d2a powernet pin
    that follows a reg (with ramps, a delay and x2v) and one held at 1
  * pad: a BIDIR pin with a pull-up in the cell and `map_by_node r=250`

  test_<check>_<engine>: the run's waveforms and digital events, and the
  paste-back round trip: the report's control lines (d2a, a2d, map_by_node, the
  supply nets' d2a powernet lines included; Irules-02/03) pasted back in place of
  the control file's rules give the same deck, cut VHDL and boundary file.
  TestE2ELatin1: the compile and ./simv under an ISO-8859-1 locale (Irules-01).
"""

import codecs
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Dict, List, Optional, Tuple

from ams_e2e_lib import AmsCase, engines_available, needs_ams

from vamos.ams import layout  # noqa: E402

ENGINES = ("vacask", "xyce")
ARROW = chr(0x2192)

SP = """\
* vamos rulesfix e2e: every kind of interface-element report line
.subckt half in oz ox o1
* three outputs at half the input: a 1.62 V input holds them mid-band (0.81 V)
e1 oz 0 in 0 0.5
e2 ox 0 in 0 0.5
e3 o1 0 in 0 0.5
rin in 0 1meg
.ends
.subckt load a b
* 1 kohm loads: the D2A series resistance shows in the node voltage
ra a 0 1k
rb b 0 1k
.ends
.subckt pwr vdd vee vp vk
* supply pins under 10 kohm loads
rdd vdd 0 10k
ree vee 0 10k
rvp vp 0 10k
rvk vk 0 10k
.ends
.subckt pad io
* a bidirectional pin pulled up to 1.8 V inside the cell
rpu io vt 10k
vt vt 0 1.8
.ends
.tran 0.1n 150n
"""

TB = """\
`timescale 1ns/1ps
module tb;
  reg a = 1'b0;
  wire oz, ox, o1;
  half uh (.in(a), .oz(oz), .ox(ox), .o1(o1));
  reg s = 1'b1;
  wire w;
  assign (weak1, weak0) w = s;
  load ul (.a(s), .b(w));
  supply1 vdd;
  supply0 vee;
  reg vp = 1'b0;
  wire vk;
  assign vk = 1'b1;
  pwr up (.vdd(vdd), .vee(vee), .vp(vp), .vk(vk));
  reg en = 1'b0, d = 1'b0;
  wire io;
  assign io = en ? d : 1'bz;
  pad uq (.io(io));
  always @(oz) $display("%f oz=%b", $realtime, oz);
  always @(ox) $display("%f ox=%b", $realtime, ox);
  always @(o1) $display("%f o1=%b", $realtime, o1);
  always @(io) $display("%f io=%b", $realtime, io);
  initial begin
    #20 begin a = 1'b1; vp = 1'b1; end
    #30 begin a = 1'b0; en = 1'b1; end
    #50 begin en = 1'b0; vp = 1'b0; end
    #40 $finish;
  end
endmodule
"""

KEEP = """\
choose xa rf.sp;
port_dir -cell half (input in; output oz, ox, o1);
port_dir -cell load (input a, b);
port_dir -cell pwr (input vdd, vee, vp, vk);
port_dir -cell pad (inout io);
"""
RULES = """\
d2a hiv=90% lov=10% rise_time=2n fall_time=3n rise_delay=1n fall_delay=0.5n x2v=2 node=tb.uh.in;
a2d loth=0.6 hith=1.2 xband=4 midv_time=5n midv_logic=Z node=tb.uh.oz;
a2d loth=0.6 hith=1.2 midv_time=5n node=tb.uh.ox;
a2d loth=0.6 hith=1.2 midv_time=5n midv_logic=1 node=tb.uh.o1;
map_by_node r=1k node=tb.ul.*;
d2a powernet hiv=1.8 lov=0 node=tb.vdd;
d2a powernet lov=-1.5 node=tb.vee;
d2a powernet hiv=1.5 lov=0.2 rf_time=4n delay=2n x2v=3 node=tb.vp;
d2a powernet hiv=1.2 lov=0 node=tb.vk;
a2d loth=0.5 hith=1.3 node=tb.uq.io;
d2a hiv=1.8 lov=0 rf_time=1n node=tb.uq.io;
map_by_node r=250 node=tb.uq.io;
"""
FILES = {"rf.sp": SP, "tb.sv": TB, "vcsAD.init": KEEP + RULES}
CONTROL_LINE = re.compile(r"^(d2a|a2d|map_by_node)\s")


def fevents(out: str, tag: str) -> List[Tuple[float, str]]:
    """(time in ns, value) from $display("%f <tag>=%b", $realtime, x) lines, in order."""
    rx = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s+%s=([01xzXZ]+)\s*$" % re.escape(tag))
    return [(float(m.group(1)), m.group(2).lower()) for m in map(rx.match, out.splitlines()) if m]


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def report_of(d: str) -> str:
    return read(os.path.join(d, "simv.msv", "interface_element.rpt"))


def control_lines(rep: str) -> List[str]:
    return [ln for ln in rep.splitlines() if CONTROL_LINE.match(ln)]


def latin1_env(tmp: str) -> Optional[Dict[str, str]]:
    """Locale variables whose encoding is ISO-8859-1 (RHEL's plain LANG=en_US), or None.
    Linux: compiled with localedef into <tmp>/locale (LOCPATH).  PYTHONUTF8=0, so a
    Python that defaults to UTF-8 mode (3.15+) still takes the locale's encoding.
    (The same helper as test_ams_rules_fixes.py; test modules import only vamos_testlib.)"""
    base = dict(os.environ, PYTHONUTF8="0")
    base.pop("PYTHONIOENCODING", None)

    def encoding(env):
        r = subprocess.run([sys.executable, "-c", "import locale; print(locale.getpreferredencoding(False))"],
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True)
        try:
            return codecs.lookup(r.stdout.strip()).name
        except LookupError:
            return ""

    for loc in ("en_US.ISO-8859-1", "en_US.iso88591", "de_DE.ISO-8859-1"):
        env = dict(base, LANG=loc, LC_ALL=loc)
        if encoding(env) == "iso8859-1":
            return {k: env[k] for k in ("LANG", "LC_ALL", "PYTHONUTF8")}
    if shutil.which("localedef"):
        locdir = os.path.join(tmp, "locale")
        os.makedirs(locdir, exist_ok=True)
        subprocess.run(["localedef", "-i", "en_US", "-f", "ISO-8859-1",
                        os.path.join(locdir, "en_US.ISO-8859-1")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        env = dict(base, LOCPATH=locdir, LANG="en_US.ISO-8859-1", LC_ALL="en_US.ISO-8859-1")
        if encoding(env) == "iso8859-1":
            return {k: env[k] for k in ("LOCPATH", "LANG", "LC_ALL", "PYTHONUTF8")}
    return None


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


@needs_ams
@_per_engine
class TestE2ERulesfix(AmsCase):
    """The rulesfix design, compiled and run once per engine (kept for every check)."""

    _root: Optional[str] = None
    _cache: Dict[Tuple[str, str], Tuple[str, subprocess.CompletedProcess, Optional[str]]] = {}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._root = tempfile.mkdtemp(prefix="vamos-e2e-rulesfix-")
        cls._cache = {}

    @classmethod
    def tearDownClass(cls):
        if cls._root:
            if os.environ.get("VAMOS_TEST_KEEP"):
                sys.stderr.write("kept %s\n" % cls._root)
            else:
                shutil.rmtree(cls._root, ignore_errors=True)
        super().tearDownClass()

    def build(self, key: str, engine: str, files: Dict[str, str], run: bool = True):
        """(dir, compile result, simv output or None), once per (key, engine)."""
        ck = (key, engine)
        if ck not in self._cache:
            d = os.path.join(self._root, "%s_%s" % (key, engine))
            for rel, text in files.items():
                os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
                with open(os.path.join(d, rel), "w") as fh:
                    fh.write(text)
            comp = self.compile(d, "-sverilog", "tb.sv", engine=engine, expect_rc=None)
            out = None
            if run and comp.returncode == 0:
                out = self.simv(d, expect_rc=None)
                out = "%s\n[rc %d]" % (out.stdout, out.returncode)
            self._cache[ck] = (d, comp, out)
        d, comp, out = self._cache[ck]
        self.assertEqual(comp.returncode, 0, comp.stdout)
        if run:
            self.assertTrue(out is not None and out.endswith("[rc 0]"), out)
        return d, comp.stdout, out

    def node(self, d: str, canonical: str) -> str:
        with open(layout.plan_json(os.path.join(d, "simv.daidir"))) as fh:
            nodes = {n["canonical"]: n for n in json.load(fh)["nodes"]}
        self.assertIn(canonical, nodes, sorted(nodes))
        return nodes[canonical]["name"]

    def assertV(self, raw, col: str, t: float, want: float, tol: float, what: str) -> None:
        v = raw.at(col, t)
        self.assertAlmostEqual(v, want, delta=tol, msg="%s: v(%s) at %.4g s is %.6g V, expected %.6g V"
                               % (what, col, t, v, want))

    # -- Irules-04 ------------------------------------------------------------------------

    def check_midv_logic(self, engine):
        """midv_logic=Z releases the A2D's net (z), X drives x, 1 drives 1, each midv_time
        after the output enters the window"""
        d, _cout, out = self.build("rf", engine, FILES)
        # entering the window: ox/o1 at 0.6 V (22.42 ns), oz at loth_hys 0.75 V (22.83 ns);
        # the entry is seen at the first analog point inside (one step late at most)
        for tag, mid, enter in (("oz", "z", 22.83), ("ox", "x", 22.42), ("o1", "1", 22.42)):
            ev = fevents(out, tag)
            at0 = [v for t, v in ev if t == 0.0]
            later = [(t, v) for t, v in ev if t > 0.0]
            self.assertEqual(at0[-1:], ["0"], (tag, ev))
            self.assertEqual([v for _, v in later], [mid, "0"], (tag, ev))
            self.assertGreaterEqual(later[0][0], enter + 5.0 - 0.3, (tag, ev))
            self.assertLessEqual(later[0][0], enter + 5.0 + 1.0, (tag, ev))
        rep = report_of(d)
        self.assertIn("a2d loth=0.6 hith=1.2 xband=4.0 midv_time=5e-09 midv_logic=Z node=tb.uh.oz;", rep)

    # -- Irules-05: map_by_node ---------------------------------------------------------

    def check_map_by_node(self, engine):
        """map_by_node r=1k: a strong and a weak 1 both drive the 1 kohm loads through 1 kohm
        (0.9 V, not 1.1994 V and 0.4 V); r=250 on a BIDIR pin driving 0 against its 10 kohm
        pull-up"""
        d, cout, _out = self.build("rf", engine, FILES)
        self.assertNotIn("map_by_node is ignored", cout)
        raw = self.raw(d)
        for canon, what in (("tb.ul.a", "strong 1 through r=1k"), ("tb.ul.b", "weak 1 through r=1k")):
            n = self.node(d, canon)
            for t in (10e-9, 90e-9):
                self.assertV(raw, n, t, 0.9, 2e-3, what)
        io = self.node(d, "tb.uq.io")
        self.assertV(raw, io, 75e-9, 1.8 * 250.0 / 10250.0, 2e-3, "BIDIR driven 0 through r=250")
        self.assertV(raw, io, 10e-9, 1.8, 2e-3, "BIDIR released: the cell's pull-up")
        lines = control_lines(report_of(d))
        for canon, r in (("tb.ul.a", "1000.0"), ("tb.ul.b", "1000.0"), ("tb.uq.io", "250.0")):
            self.assertIn("map_by_node r=%s node=%s;" % (r, canon), lines)

    # -- supplies (Irules-02) -------------------------------------------------------------

    def check_supplies(self, engine):
        """supply1 at its rule's 1.8 V, supply0 at -1.5 V, a d2a powernet pin following its
        reg with ramp and delay, one held at 1.2 V; each with a d2a powernet report line"""
        d, cout, _out = self.build("rf", engine, FILES)
        self.assertNotIn("3.3 V fallback", cout)
        raw = self.raw(d)
        for canon, level in (("tb.up.vdd", 1.8), ("tb.up.vee", -1.5), ("tb.up.vk", 1.2)):
            n = self.node(d, canon)
            for t in (5e-9, 60e-9, 130e-9):
                self.assertV(raw, n, t, level, 1e-6, canon)
        vp = self.node(d, "tb.up.vp")
        for t, level in ((10e-9, 0.2), (21.9e-9, 0.2), (24e-9, 0.85), (40e-9, 1.5), (120e-9, 0.2)):
            self.assertV(raw, vp, t, level, 2e-3, "d2a powernet vp (delay 2 ns, 4 ns ramp)")
        lines = control_lines(report_of(d))
        for line in ("d2a powernet hiv=1.8 lov=0.0 rf_time=1e-11 x2v=0 node=tb.up.vdd;",
                     "d2a powernet hiv=1.2 lov=0.0 rf_time=1e-11 x2v=0 node=tb.up.vk;",
                     "d2a powernet hiv=1.5 lov=0.2 rf_time=4e-09 delay=2e-09 x2v=3 node=tb.up.vp;"):
            self.assertIn(line, lines)
        self.assertTrue(any(ln.startswith("d2a powernet ") and ln.endswith(" node=tb.up.vee;")
                            and " lov=-1.5 " in ln for ln in lines), lines)

    # -- Irules-02/03: paste-back ----------------------------------------------------------

    def check_paste_back(self, engine):
        """the report's control lines pasted back in place of the rules give the same deck,
        cut VHDL, boundary and report lines, with no TNF and no 3.3 V fallback"""
        d1, _c1, _out = self.build("rf", engine, FILES)
        lines = control_lines(report_of(d1))
        kinds = sorted({ln.split()[0] for ln in lines})
        self.assertEqual(kinds, ["a2d", "d2a", "map_by_node"])
        self.assertEqual(len(lines), 15, lines)       # 8 d2a (2 supply nets), 4 a2d, 3 map_by_node
        files = dict(FILES, **{"vcsAD.init": KEEP + "\n".join(lines) + "\n"})
        d2, c2, _ = self.build("paste", engine, files, run=False)
        self.assertNotIn("MSV-IE-OPT-TNF", c2)
        self.assertNotIn("3.3 V fallback", c2)
        self.assertEqual(control_lines(report_of(d2)), lines)
        for what, path in (("deck", layout.deck("simv.daidir", engine)), ("cut", layout.cut_vhd("simv.daidir")),
                           ("boundary", layout.boundary("simv.daidir"))):
            a = read(os.path.join(d1, path)).replace(d1, "<dir>")
            b = read(os.path.join(d2, path)).replace(d2, "<dir>")
            self.assertEqual(a.splitlines(), b.splitlines(), "%s differs after the paste-back" % what)


# -- Irules-01 -----------------------------------------------------------------------------

@needs_ams
class TestE2ELatin1(AmsCase):
    """vcs-ams and ./simv under an ISO-8859-1 locale (RHEL's LANG=en_US): the IE report,
    whose direction comments carry '→', is written as UTF-8 and the run goes on."""

    def test_compile_and_run(self):
        env = latin1_env(self.tmp)
        if env is None:
            self.skipTest("no ISO-8859-1 locale on this machine (no localedef)")
        # half and load without port_dir: their auto ports get "direction: auto->..." comments
        files = {"rf.sp": SP, "tb.sv": TB,
                 "vcsAD.init": "choose xa rf.sp;\nport_dir -cell pwr (input vdd, vee, vp, vk);\n"
                               "port_dir -cell pad (inout io);\n" + RULES}
        for engine in self.engines():
            with self.subTest(engine=engine):
                d = self.case("latin1_" + engine, files)
                r = self.compile(d, "-sverilog", "tb.sv", engine=engine, env=env, expect_rc=None)
                self.assertEqual(r.returncode, 0, r.stdout)
                self.assertNotIn("Traceback", r.stdout)
                self.assertFalse(os.path.exists(os.path.join(d, "simv.msv", "interface_element.rpt.tmp")))
                with open(os.path.join(d, "simv.msv", "interface_element.rpt"), "rb") as fh:
                    rep = fh.read().decode("utf-8")           # UTF-8 whatever the locale
                self.assertIn("direction: auto%sinput" % ARROW, rep)
                s = self.run_cmd(["./simv"], d, env)
                self.assertEqual(s.returncode, 0, s.stdout)
                self.assertIn("co-simulation finished", s.stdout)


if __name__ == "__main__":
    unittest.main()
