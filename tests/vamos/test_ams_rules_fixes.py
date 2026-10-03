"""Regression tests for the rules fixes (docs/VAMOS_AMS_DESIGN.md §2.2, §3.4-§3.6).

    python3 -m unittest discover -s tests/vamos -p 'test_ams_rules_fixes.py' -v

  Irules-01  the IE report is UTF-8 under any locale (a latin-1 locale used to crash
             step 13 on '→'); a report that cannot be written is a clean error
  Irules-02  supply nets (POWERNET) get a pasteable `d2a powernet` line
  Irules-03  one writer (rules.paste_line via rules.ie_lines): every kind of report
             line pasted back as the control file gives the same levels
  Irules-04  midv_logic=Z is applied (the cut's MIDV_L 3), not run as X
  Irules-05  map_by_node r= sets the D2A series resistance; XA cfg paths are
             environment-expanded; an absolute level beside vdd=/vss= is an error;
             a wildcard vdd_port=/vss_port= stays a clear error

The end-to-end side (both engines) is tests/vamos/test_ams_e2e_rulesfix.py.
"""

import codecs
import os
import shutil
import subprocess
import sys
import unittest
from dataclasses import replace
from typing import Dict, List, Optional

from vamos_testlib import ROOT, TempDir  # noqa: F401  (also puts ROOT on sys.path)

from vamos.ams import cut, initfile, report, rules  # noqa: E402
from vamos.ams.config import TNF  # noqa: E402
from vamos.ams.model import (A2D, BIDIR, D2A, DISABLED, POWERNET, REMOVED, A2D_IE,  # noqa: E402
                             AmsPlan, AnalogNode, CutAnalysis, D2A_IE, RuleHits)
from vamos.backends.nvc import BackendError  # noqa: E402
from vamos.notes import ERROR, WARNING, NoteError, has_errors, warning  # noqa: E402

VREF = (0.0, 1.8, None)
FALLBACK = warning("x", "no supply reached: using the VCS 3.3 V fallback")
VREF33 = (0.0, 3.3, FALLBACK)
LATIN1_LOCALES = ("en_US.ISO-8859-1", "en_US.iso88591", "de_DE.ISO-8859-1", "C.ISO-8859-1")


def missing(_name):
    return ("missing", None)


def latin1_env(tmp: str) -> Optional[Dict[str, str]]:
    """An environment whose locale encoding is ISO-8859-1 (RHEL's plain LANG=en_US), or
    None when this machine can make none.  Cygwin has the locale built in; on Linux it
    is compiled with localedef into <tmp>/locale (LOCPATH).  PYTHONUTF8=0, so a Python
    that defaults to UTF-8 mode (3.15+) still takes the locale's encoding."""
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

    for loc in LATIN1_LOCALES:
        env = dict(base, LANG=loc, LC_ALL=loc)
        if encoding(env) == "iso8859-1":
            return env
    if shutil.which("localedef"):
        locdir = os.path.join(tmp, "locale")
        os.makedirs(locdir, exist_ok=True)
        subprocess.run(["localedef", "-i", "en_US", "-f", "ISO-8859-1",
                        os.path.join(locdir, "en_US.ISO-8859-1")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        env = dict(base, LOCPATH=locdir, LANG="en_US.ISO-8859-1", LC_ALL="en_US.ISO-8859-1")
        if encoding(env) == "iso8859-1":
            return env
    return None


class Base(TempDir):
    def parse(self, text: str, env: Optional[Dict[str, str]] = None):
        self.write("vcsAD.init", text)
        return initfile.parse_control(["vcsAD.init"], self.tmp, env if env is not None else {})

    def cfg(self, text: str):
        cfg = self.parse("choose xa a.sp;\n" + text)
        self.assertFalse(has_errors(cfg.notes), [n.text() for n in cfg.notes])
        return cfg

    def errors(self, cfg) -> List[str]:
        return [n.text() for n in cfg.notes if n.severity == ERROR]


# -- Irules-01 ---------------------------------------------------------------------------

ARROW = chr(0x2192)          # cut.py's direction comments: "direction: auto<ARROW>input ..."

# Run from a file (Python reads source as UTF-8 whatever the locale), as cut.py's
# '→' reaches report.write in a real compile; never through argv, which a latin-1
# locale would decode differently.
_WRITE_CHILD = """\
import sys
sys.path.insert(0, sys.argv[1])
from vamos.ams import report
from vamos.ams.model import AmsPlan, AnalogNode, CutAnalysis, D2A, D2A_IE
n = AnalogNode("n_u1_in", "tb.u1.in", ["tb.clk"], "k0", [], D2A)
n.d2a = D2A_IE(1.8, 0.0)
n.report = ["direction: auto" + chr(0x2192) + "input (variable actual) tb.u1.in",
            "levels: reference 3.3 V fallback"]
report.write(sys.argv[2], AmsPlan(CutAnalysis("tb"), [n]), "vacask")
"""


class TestReportEncoding(TempDir):
    """Irules-01: report.write under a latin-1 locale, and a write that fails."""

    def test_utf8_under_a_latin1_locale(self):
        env = latin1_env(self.tmp)
        if env is None:
            self.skipTest("no ISO-8859-1 locale on this machine (no localedef)")
        path = os.path.join(self.tmp, "simv.msv", "interface_element.rpt")
        child = os.path.join(self.tmp, "write_report.py")
        with open(child, "w", encoding="utf-8") as fh:
            fh.write(_WRITE_CHILD)
        r = subprocess.run([sys.executable, child, ROOT, path], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                           errors="replace")
        self.assertEqual(r.returncode, 0, r.stdout)          # was UnicodeEncodeError on the arrow
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8")                 # valid UTF-8 whatever the locale
        self.assertIn("// direction: auto%sinput (variable actual) tb.u1.in" % ARROW, text)
        self.assertIn("d2a hiv=1.8 lov=0.0 rf_time=1e-11 x2v=0 node=tb.u1.in;", text)
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_unwritable_report_is_a_clean_error(self):
        plan = AmsPlan(CutAnalysis("tb"), [])
        blocker = os.path.join(self.tmp, "simv.msv")
        with open(blocker, "w") as fh:                      # a file where the directory goes
            fh.write("x")
        path = os.path.join(blocker, "interface_element.rpt")
        with self.assertRaises(report.ReportError) as cm:
            report.write(path, plan, "xyce")
        e = cm.exception
        self.assertIsInstance(e, NoteError)                  # a stage runner prints its note
        self.assertIsInstance(e, BackendError)               # vcs.main prints "vamos: error: ..."
        (n,) = e.notes
        self.assertEqual((n.severity, n.origin), (ERROR, path))
        self.assertTrue(str(e).startswith(path + ": cannot write the interface-element report: "), str(e))
        self.assertFalse(str(e).startswith("error:"))         # vcs.main adds "vamos: error: "

    def test_failed_replace_leaves_no_tmp(self):
        plan = AmsPlan(CutAnalysis("tb"), [])
        path = os.path.join(self.tmp, "simv.msv", "interface_element.rpt")
        os.makedirs(os.path.join(path, "sub"))               # a non-empty directory in the way
        with self.assertRaises(report.ReportError):
            report.write(path, plan, "vacask")
        self.assertFalse(os.path.exists(path + ".tmp"))


# -- Irules-02 / Irules-03: one writer, every kind of line pastes back ------------------------

RULES = """\
d2a hiv=90% lov=10% rise_time=2n fall_time=3n rise_delay=1n fall_delay=0.5n x2v=2 node=tb.uh.in;
a2d loth=0.6 hith=1.2 xband=4 midv_time=5n midv_logic=Z node=tb.uh.oz;
a2d loth=30% hith=70% node=tb.uh.ox;
a2d midv_logic=1 node=tb.uh.o1;
map_by_node r=1k node=tb.ul.*;
d2a powernet hiv=1.8 lov=0 node=tb.vdd;
d2a powernet lov=-1.5 node=tb.vee;
d2a powernet hiv=1.5 lov=0.2 rf_time=4n delay=2n x2v=3 node=tb.vp;
a2d loth=0.5 hith=1.3 node=tb.uq.io;
d2a hiv=1.8 lov=0 rf_time=1n node=tb.uq.io;
map_by_node r=250 node=tb.uq.io;
"""

# (canonical, aliases, role, supply polarity for POWERNET)
NODES = [("tb.uh.in", ["tb.a"], D2A, None),
         ("tb.uh.in2", ["tb.a2"], D2A, None),               # no rule: levels from the reference
         ("tb.uh.oz", ["tb.oz"], A2D, None),
         ("tb.uh.ox", ["tb.ox"], A2D, None),
         ("tb.uh.o1", ["tb.o1"], A2D, None),
         ("tb.ul.a", ["tb.s"], D2A, None),
         ("tb.ul.b", ["tb.w"], D2A, None),
         ("tb.up.vdd", ["tb.vdd"], POWERNET, "supply1"),
         ("tb.up.vee", ["tb.vee"], POWERNET, "supply0"),
         ("tb.up.vss", ["tb.vss"], POWERNET, "supply0"),   # no rule
         ("tb.up.vp", ["tb.vp"], D2A, None),                # a d2a powernet D2A with ramps
         ("tb.uq.io", ["tb.io"], BIDIR, None),
         ("tb.ur.x", ["tb.rx"], REMOVED, None),
         ("tb.ud.y", ["tb.dy"], DISABLED, None)]


def build_plan(cfg, vref=VREF) -> AmsPlan:
    """The nodes as deck._levels leaves them: IEs from resolve_detail with the node's role;
    a supply net's dc is the level it uses (supply1 hiv, supply0 lov)."""
    hits = RuleHits()
    nodes = []
    for k, (canon, aliases, role, pol) in enumerate(NODES):
        n = AnalogNode("n%d" % k, canon, list(aliases), "k%d" % k, [], role)
        res = rules.resolve_detail(cfg, [canon] + aliases, [], vref, missing, hits, role)
        n.d2a, n.a2d = res.d2a, res.a2d
        if role == POWERNET:
            n.dc = n.d2a.hiv if pol == "supply1" else n.d2a.lov
        elif role == REMOVED:
            n.dc = 2.5
        nodes.append(n)
    return AmsPlan(CutAnalysis("tb"), nodes)


def control_lines(text: str) -> List[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("//")]


class TestOneWriter(Base):
    """Irules-02 and Irules-03: report.py's control lines are rules.ie_lines' (paste_line's),
    supply nets included, and pasting all of them back reproduces every node."""

    def test_report_lines_come_from_paste_line(self):
        plan = build_plan(self.cfg(RULES))
        text = report.text(plan, "vacask")
        want = []
        for n in plan.nodes:
            want += rules.ie_lines(n.role, n.canonical, n.d2a, n.a2d)
        self.assertEqual(control_lines(text), want)
        by = {ln.split("node=")[1].rstrip(";") + " " + ln.split()[0]: ln for ln in want}
        # a d2a powernet D2A keeps its ramps, delay and x2v (they shape its source)
        self.assertEqual(by["tb.up.vp d2a"], "d2a powernet hiv=1.5 lov=0.2 rf_time=4e-09 delay=2e-09 x2v=3 "
                                             "node=tb.up.vp;")
        # supply nets: a pasteable d2a powernet line with the merged levels, then the comment
        self.assertEqual(by["tb.up.vdd d2a"], "d2a powernet hiv=1.8 lov=0.0 rf_time=1e-11 x2v=0 node=tb.up.vdd;")
        self.assertEqual(by["tb.up.vee d2a"], "d2a powernet hiv=1.8 lov=-1.5 rf_time=1e-11 x2v=0 node=tb.up.vee;")
        self.assertIn("d2a powernet hiv=1.8 lov=0.0 rf_time=1e-11 x2v=0 node=tb.up.vdd;\n"
                      "// node=tb.up.vdd: supply net, ideal 1.8 V source (powernet)", text)
        self.assertIn("// node=tb.up.vee: supply net, ideal -1.5 V source (powernet)", text)
        self.assertEqual(by["tb.uh.oz a2d"], "a2d loth=0.6 hith=1.2 xband=4.0 midv_time=5e-09 midv_logic=Z "
                                             "node=tb.uh.oz;")
        self.assertEqual(by["tb.uh.o1 a2d"], "a2d loth=0.9 hith=0.9 midv_logic=1 node=tb.uh.o1;")
        self.assertEqual(by["tb.ul.a map_by_node"], "map_by_node r=1000.0 node=tb.ul.a;")
        self.assertEqual(by["tb.uq.io map_by_node"], "map_by_node r=250.0 node=tb.uq.io;")
        self.assertNotIn("tb.up.vp map_by_node", by)
        # removed and disabled nodes keep their comment lines only
        self.assertIn("// node=tb.ur.x: remove_d2a dc=2.5", text)
        self.assertIn("// node=tb.ud.y: disable_ie (no interface element)", text)

    def test_every_line_kind_pastes_back(self):
        cfg = self.cfg(RULES)
        plan = build_plan(cfg)
        lines = control_lines(report.text(plan, "xyce"))
        self.assertEqual(sorted({ln.split()[0] for ln in lines}), ["a2d", "d2a", "map_by_node"])
        cfg2 = self.parse("choose xa a.sp;\n" + "\n".join(lines) + "\n")
        self.assertEqual(cfg2.notes, [])
        # resolved against a different reference (3.3 V, with the fallback warning): every
        # pasted level is absolute, so nothing may come from the reference
        hits = RuleHits()
        for n, (canon, aliases, role, pol) in zip(plan.nodes, NODES):
            res = rules.resolve_detail(cfg2, [canon] + aliases, [], VREF33, missing, hits, role)
            with self.subTest(node=canon, role=role):
                self.assertEqual(res.notes, [])
                if role in (D2A, BIDIR):
                    self.assertEqual(res.d2a, n.d2a)
                if role in (A2D, BIDIR):
                    self.assertEqual(res.a2d, n.a2d)
                if role == POWERNET:                         # the pasted line adds the flag
                    self.assertEqual(res.d2a, replace(n.d2a, powernet=True))
                    self.assertEqual(res.d2a.hiv if pol == "supply1" else res.d2a.lov, n.dc)
                    self.assertEqual(rules.ie_lines(role, canon, res.d2a, res.a2d),
                                     rules.ie_lines(role, canon, n.d2a, n.a2d))
        self.assertEqual(rules.unmatched(cfg2, hits), [])
        # and the second report reads exactly as the first
        self.assertEqual(control_lines(report.text(build_plan(cfg2, VREF33), "xyce")), lines)

    def test_supply_rail_survives_paste_back(self):
        """The finding: a supply1 net set by `d2a powernet hiv=1.8` became 3.3 V and a supply0
        rail at -1.5 V became 0 V once the report was pasted back; so did a supply1 net set by
        a plain `d2a hiv=2.5` rule (the skeptic's variant)."""
        cfg = self.cfg("d2a powernet hiv=1.8 lov=0 node=tb.vdd;\nd2a powernet lov=-1.5 node=tb.vee;\n"
                       "d2a hiv=2.5 node=tb.vdd2;\n")
        sup = [("tb.u1.vdd", ["tb.vdd"], "supply1"), ("tb.u1.vee", ["tb.vee"], "supply0"),
               ("tb.u1.vdd2", ["tb.vdd2"], "supply1")]
        nodes = []
        for canon, aliases, pol in sup:
            n = AnalogNode(canon.replace(".", "_"), canon, aliases, "k", [], POWERNET)
            n.d2a = rules.resolve(cfg, [canon] + aliases, [], VREF, missing, RuleHits(), POWERNET)[0]
            n.dc = n.d2a.hiv if pol == "supply1" else n.d2a.lov
            nodes.append(n)
        self.assertEqual([n.dc for n in nodes], [1.8, -1.5, 2.5])
        lines = control_lines(report.text(AmsPlan(CutAnalysis("tb"), nodes), "vacask"))
        cfg2 = self.cfg("\n".join(lines) + "\n")
        hits = RuleHits()
        got = []
        for (canon, aliases, pol), n in zip(sup, nodes):
            d, _a, notes = rules.resolve(cfg2, [canon], [], VREF33, missing, hits, POWERNET)
            self.assertEqual(notes, [])                      # no 3.3 V fallback
            got.append(d.hiv if pol == "supply1" else d.lov)
        self.assertEqual(got, [1.8, -1.5, 2.5])
        self.assertEqual(rules.unmatched(cfg2, hits), [])
        self.assertIn("d2a powernet hiv=2.5 lov=0.0 rf_time=1e-11 x2v=0 node=tb.u1.vdd2;", lines)

    def test_paste_line_forms(self):
        self.assertEqual(rules.paste_line("d2a", "tb.y", D2A_IE(1.0, 0.0)), "d2a hiv=1.0 lov=0.0 rf_time=1e-11 "
                                                                         "x2v=0 node=tb.y;")
        self.assertEqual(rules.paste_line("a2d", "tb.y", A2D_IE(0.9, 0.9, midv_time=1e-9)),
                         "a2d loth=0.9 hith=0.9 midv_time=1e-09 midv_logic=X node=tb.y;")
        self.assertEqual(rules.paste_line("map_by_node", "tb.y", D2A_IE(1.0, 0.0, r_series=5.0)),
                         "map_by_node r=5.0 node=tb.y;")
        with self.assertRaises(ValueError):
            rules.paste_line("e2r", "tb.y", D2A_IE(1.0, 0.0))
        # no map_by_node line for the default resistance, nor for an ideal (powernet) D2A
        self.assertEqual(len(rules.ie_lines(D2A, "tb.y", D2A_IE(1.0, 0.0), None)), 1)
        ideal = D2A_IE(1.0, 0.0, powernet=True, r_series=5.0, weak_frac=1.0)
        self.assertEqual(len(rules.ie_lines(D2A, "tb.y", ideal, None)), 1)
        self.assertEqual(len(rules.ie_lines(BIDIR, "tb.y", ideal, A2D_IE(0.9, 0.9))), 3)   # BIDIR is gated
        self.assertEqual(rules.ie_lines(REMOVED, "tb.y", D2A_IE(1.0, 0.0), None), [])


# -- Irules-04 ---------------------------------------------------------------------------

class TestMidvLogicZ(Base):
    def test_z_is_applied(self):
        cfg = self.cfg("a2d loth=0.6 hith=1.2 midv_time=5n midv_logic=Z node=tb.u.a;\n"
                       "a2d loth=0.6 hith=1.2 midv_time=5n midv_logic=z node=tb.u.b;\n"
                       "a2d loth=0.6 hith=1.2 midv_time=5n node=tb.u.c;\n")
        self.assertEqual(cfg.notes, [])                      # no "approximated as X" warning
        codes = []
        for name in ("tb.u.a", "tb.u.b", "tb.u.c"):
            a = rules.resolve(cfg, [name], [], VREF, missing, RuleHits(), A2D)[1]
            n = AnalogNode("n", name, [], "k", [], A2D)
            n.a2d = a
            codes.append((a.midv_logic, cut._levels(n)[cut.K_NAMES.index("MIDV_L")]))
        self.assertEqual(codes, [("Z", 3.0), ("Z", 3.0), ("X", 2.0)])


# -- Irules-05 ---------------------------------------------------------------------------

class TestMapByNode(Base):
    def resolve(self, cfg, name, role, hits=None):
        return rules.resolve_detail(cfg, [name], [], VREF, missing, hits or RuleHits(), role)

    def test_forms(self):
        cfg = self.cfg("map_by_node r=5 node=top.n1;\nmap_by_node r = 1.5k node = top.n* ;\n")
        self.assertEqual([(r.kind, r.node, r.params) for r in cfg.rules],
                         [("map_by_node", "top.n1", {"r": "5"}), ("map_by_node", "top.n*", {"r": "1.5k"})])
        bad = {"map_by_node node=x;": "needs r=", "map_by_node r=5;": "needs node=",
               "map_by_node r=0 node=x;": "positive resistance", "map_by_node r=-1 node=x;": "positive",
               "map_by_node r=abc node=x;": "bad r=abc", "map_by_node r=5 cell=c port=p;": "node= only",
               "map_by_node r=5 hiv=1 node=x;": "hiv= is a d2a key", "map_by_node r=5 q=1 node=x;": "unknown key"}
        for line, needle in bad.items():
            cfg = self.parse("choose xa a.sp;\n" + line)
            errs = self.errors(cfg)
            self.assertTrue(any(needle in e and "vcsAD.init:2" in e for e in errs), (line, errs))
            self.assertEqual(cfg.rules, [], line)

    def test_sets_the_series_resistance(self):
        cfg = self.cfg("map_by_node r=5 node=tb.u.a;\nd2a hiv=1.2 node=tb.u.a;\n"
                       "map_by_node r=2k node=tb.u.*;\n")    # the last match wins
        r = self.resolve(cfg, "tb.u.a", D2A)
        self.assertEqual((r.d2a.r_series, r.d2a.weak_frac, r.d2a.hiv), (2000.0, 1.0, 1.2))
        self.assertEqual(r.origins["map_by_node.r"], "vcsAD.init:4")
        self.assertEqual(r.notes, [])
        n = AnalogNode("n", "tb.u.a", [], "k", [], D2A)
        n.d2a = r.d2a
        self.assertEqual(cut._levels(n)[cut.K_NAMES.index("WF")], 1.0)    # a weak drive sees r too
        self.assertEqual(self.resolve(cfg, "tb.u.b", BIDIR).d2a.r_series, 2000.0)
        d = self.resolve(self.cfg(""), "tb.u.a", D2A).d2a
        self.assertEqual((d.r_series, d.weak_frac), (500.7, 500.7 / 3500.2))   # unchanged without it

    def test_role_filter_and_tnf(self):
        cfg = self.cfg("map_by_node r=5 node=tb.u.y;\n")
        hits = RuleHits()
        r = self.resolve(cfg, "tb.u.y", A2D, hits)
        self.assertEqual(r.d2a.r_series, 500.7)
        (n,) = rules.unmatched(cfg, hits)
        self.assertIn('[%s] Option Target Not Found: "map_by_node" command, option target "node=tb.u.y"' % TNF,
                      n.message)

    def test_no_effect_on_ideal_sources_warns_once(self):
        cfg = self.cfg("map_by_node r=5 node=tb.u.*;\nd2a powernet node=tb.u.p;\n")
        hits = RuleHits()
        notes = []
        for name, role in (("tb.u.vdd", POWERNET), ("tb.u.vss", POWERNET), ("tb.u.p", D2A), ("tb.u.a", D2A)):
            notes += self.resolve(cfg, name, role, hits).notes
        (w,) = notes
        self.assertEqual((w.severity, w.origin), (WARNING, "vcsAD.init:2"))
        self.assertIn("map_by_node r=5 has no effect on tb.u.vdd: a supply net is an ideal source", w.message)
        self.assertEqual(rules.unmatched(cfg, hits), [])
        cfg = self.cfg("map_by_node r=5 node=tb.u.p;\nd2a powernet node=tb.u.p;\n")
        (w,) = self.resolve(cfg, "tb.u.p", D2A).notes
        self.assertIn("a d2a powernet node is an ideal source", w.message)


class TestXaCfgPaths(Base):
    def test_environment_and_home_are_expanded(self):
        self.write("cfg/xa.cfg", "probe_waveform_voltage tb.v\n")
        self.write("cfg/xa2.cfg", "set_sim_case -case sensitive\n")
        self.write("home/h.cfg", "probe_waveform_current tb.x1.m1\n")
        env = {"CFGDIR": "cfg", "HOME": os.path.join(self.tmp, "home")}
        cfg = self.parse("choose xa a.sp -c $CFGDIR/xa.cfg -C ${CFGDIR}/xa2.cfg -c ~/h.cfg;\n", env)
        self.assertEqual(cfg.notes, [])
        self.assertEqual(cfg.choose.cfgs, ["$CFGDIR/xa.cfg", "${CFGDIR}/xa2.cfg", "~/h.cfg"])   # as written
        self.assertEqual(cfg.xa["probe_v"], [("tb.v", os.path.join("cfg", "xa.cfg") + ":1")])
        self.assertEqual(cfg.xa["probe_i"], [("tb.x1.m1", os.path.join("home", "h.cfg") + ":1")])
        self.assertEqual(cfg.xa["case"], "sensitive")

    def test_unset_variable_is_an_error(self):
        cfg = self.parse("choose xa a.sp -c $NOPE/xa.cfg;\n")
        (e,) = [n for n in cfg.notes if n.severity == ERROR]
        self.assertEqual(e.origin, "vcsAD.init:1")
        self.assertIn("XA cfg $NOPE/xa.cfg: environment variable NOPE is not set", e.message)


class TestAbsoluteLevelsBesideSupplies(Base):
    def test_rejected_where_vcs_rejects_them(self):
        bad = {"d2a hiv=1.2 vdd=tb.v node=x;": "hiv=1.2 cannot be an absolute level in a rule with vdd=",
               "d2a hiv=90% lov=0 vss=tb.g node=x;": "lov=0 cannot be an absolute level in a rule with vss=",
               "a2d loth=0.6 hith=70% vdd=tb.v vss=tb.g node=x;": "loth=0.6 cannot be an absolute level",
               "a2d hith=1.2 vdd_port=vdda cell=c port=*;": "in a rule with vdd_port="}
        for line, needle in bad.items():
            cfg = self.parse("choose xa a.sp;\n" + line)
            errs = self.errors(cfg)
            self.assertTrue(any(needle in e and "vcsAD.init:2" in e for e in errs), (line, errs))
            self.assertEqual(cfg.rules, [], line)

    def test_percentages_and_plain_levels_are_fine(self):
        cfg = self.cfg("d2a hiv=90% lov=10% vdd=tb.v vss=tb.g node=x;\nd2a hiv=1.2 lov=0 node=y;\n"
                       "a2d loth=40% hith=60% vdd_port=vdda cell=c port=*;\nd2a vdd=tb.v node=z;\n"
                       # VCS merges key by key: an absolute hiv from one rule beside another
                       # rule's vdd= is the merged IE, not a statement VCS rejects
                       "d2a hiv=1.2 node=w;\nd2a vdd=tb.v node=w;\n")
        self.assertEqual(len(cfg.rules), 6)
        d = rules.resolve(cfg, ["w"], [], VREF, lambda n: ("const", 2.0), RuleHits(), D2A)[0]
        self.assertEqual((d.hiv, d.lov), (1.2, 0.0))


class TestWildcardSupplyPort(Base):
    def test_after_dotdot_is_a_clear_error_naming_the_construct(self):
        # (a plain wildcard vdd_port= is matched against the instance's ports since the repair
        # round: test_repair_units.TestWildcardSupplyPort)
        for line, key in (("d2a vss_port=../vss* inst=tb.u port=a;", "vss_port=../vss*"),):
            cfg = self.parse("choose xa a.sp;\n" + line)
            (e,) = [n for n in cfg.notes if n.severity == ERROR]
            self.assertEqual(e.origin, "vcsAD.init:2")
            self.assertIn("%s: a wildcard after ../ is not supported" % key, e.message)
            self.assertEqual(cfg.rules, [])
        cfg = self.parse("choose xa a.sp;\na2d vdd_port=vdd* cell=c port=*;\n")
        self.assertEqual([n for n in cfg.notes if n.severity == ERROR], [])
        self.assertEqual(len(cfg.rules), 1)


if __name__ == "__main__":
    unittest.main()
