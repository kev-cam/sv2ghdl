"""Unit tests for the vamos fixes of the repair round (no simulator: Cygwin Python 3.9 too).

- vamos_testlib.TempDir.tearDown kills a run a failed test left in its scratch directory
  (simv, and nvc in its own process group, ran on for the deck's whole stop time)
- the compile banner comes before a bad VAMOS_NVC / VAMOS_IVERILOG error, alike
- NvcBackend loads libresolver.so then libsv_math.so (one comma list); OutputFilter drops
  nvc's "   Function F [...] at design.vhd:N" trace, never a testbench line
- verilog_ports.translator_warnings: iverilog-sv2ghdl's surfaced warnings as vamos warnings
  at the user's file:line (errors under --vamos-strict); NvcBackend._call keeps those lines
  for the caller instead of printing them
- verilog_ports.apply_library_rule: AMS mode blanks a -v copy a source defines and rebuilds
  the index; precheck(pp=) then checks the masked stream (user file:line)
- the VACASK and Xyce deck writers write UTF-8 whatever the locale (a non-ASCII title)
- cut._Analyser._passive matches a port_connect -inst statement's -cell against the subckt
  too, as shells.py and deck.py do
- a vdd=/vss= rule on a net held by an unevaluable V source names the source, not "dynamic"
- flow: the IE report is written through the stage runner ("AMS compile failed at the IE
  report"); simv's message for a daidir holding no finished compile

    python3 -m unittest discover -s tests/vamos -p 'test_repair_units.py' -v
"""

from __future__ import annotations

import contextlib
import copy
import io
import os
import signal
import subprocess
import sys
import time
import unittest

import vamos_testlib
from vamos_testlib import TempDir

from vamos import cli, tools  # noqa: E402
from vamos.ams import cut, deck, flow, report, verilog_ports as vp  # noqa: E402
from vamos.ams.config import AmsConfig, PortConnect  # noqa: E402
from vamos.ams.model import AmsPlan, CutAnalysis, RuleHits  # noqa: E402
from vamos.backends import nvc as nvcmod  # noqa: E402
from vamos.console import Console  # noqa: E402
from vamos.job import Job  # noqa: E402
from vamos.netlist import ir  # noqa: E402
from vamos.netlist.expr_ast import Binary, Name, Num  # noqa: E402
from vamos.notes import NoteError, strict  # noqa: E402
from vamos.personalities import simv  # noqa: E402

HAVE_PROC_CWD = os.path.islink("/proc/self/cwd")


# ------------------------------------------------------------------ the harness

def _scratch() -> TempDir:
    """A TempDir case whose setUp/tearDown the tests drive by hand (the class is local, so
    the test loader does not collect it as a test of its own)."""
    class _Scratch(TempDir):
        def test_nothing(self):
            pass
    return _Scratch("test_nothing")


@unittest.skipUnless(HAVE_PROC_CWD and hasattr(os, "setsid"), "needs /proc/<pid>/cwd")
class TestReap(unittest.TestCase):
    def test_teardown_kills_a_run_left_in_the_scratch_directory(self):
        case = _scratch()
        case.setUp()
        try:
            sub = os.path.join(case.tmp, "simv.daidir", "nvc")
            os.makedirs(sub)
            # a "run" in a session of its own, as e2e tests start ./simv and as nvc runs
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"], cwd=sub,
                                 start_new_session=True)
            deadline = time.time() + 10
            while time.time() < deadline and p.pid not in vamos_testlib.procs_under(case.tmp):
                time.sleep(0.05)
            self.assertIn(p.pid, vamos_testlib.procs_under(case.tmp))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                case.tearDown()
            self.assertEqual(p.wait(timeout=10), -signal.SIGKILL)
            self.assertIn("killed process %d left running in %s" % (p.pid, case.tmp), err.getvalue())
        finally:
            if os.path.isdir(case.tmp):
                case.tearDown()

    def test_other_processes_are_left_alone(self):
        case = _scratch()
        case.setUp()
        try:
            self.assertNotIn(os.getpid(), vamos_testlib.procs_under(case.tmp))
            self.assertEqual(vamos_testlib.procs_under(os.path.join(case.tmp, "none")), [])
        finally:
            case.tearDown()


# ------------------------------------------------------------- banner order

def merged_cli(argv):
    """cli.main(argv) with stdout and stderr in one stream, in the order printed."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


@unittest.skipUnless(os.name == "posix", "needs an executable fake tool")
class TestBannerBeforeToolErrors(TempDir):
    def setUp(self):
        super().setUp()
        self.write("x.v", "module x; endmodule\n")
        self.fake = self.write("bin/fakenvc", "#!/bin/sh\necho fake 1.0\n")
        os.chmod(self.fake, 0o755)
        self.cwd = os.getcwd()
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.cwd)
        super().tearDown()

    def check(self, var):
        rc, out = merged_cli(["-vcs", "x.v"])
        self.assertEqual(rc, 1, out)
        lines = out.splitlines()
        banner = [k for k, ln in enumerate(lines) if "compile (vcs personality)" in ln]
        err = [k for k, ln in enumerate(lines) if ln.startswith("vamos: error: %s=" % var)]
        self.assertTrue(banner and err, out)
        self.assertLess(banner[0], err[0], out)

    def test_bad_nvc(self):
        os.environ["VAMOS_NVC"] = "/nonexistent/nvc"
        self.check("VAMOS_NVC")

    def test_bad_iverilog(self):
        os.environ["VAMOS_NVC"] = self.fake
        os.environ["NVC_LIBDIR"] = self.tmp
        os.environ["VAMOS_IVERILOG"] = "/nonexistent/iverilog"
        self.check("VAMOS_IVERILOG")


# ------------------------------------------------------ nvc plugins and filter

@unittest.skipUnless(os.name == "posix", "needs an executable fake tool")
class TestNvcPlugins(TempDir):
    def backend(self, libs):
        fake = self.write("bin/fakenvc", "#!/bin/sh\nexit 0\n")
        os.chmod(fake, 0o755)
        os.environ["VAMOS_NVC"] = fake
        libdir = os.path.join(self.tmp, "lib")
        os.makedirs(os.path.join(libdir, "sv2vhdl"))
        for name in libs:
            self.write(os.path.join("lib", "sv2vhdl", name), "")
        os.environ["NVC_LIBDIR"] = libdir
        job = Job("vcs", daidir=os.path.join(self.tmp, "simv.daidir"))
        return nvcmod.NvcBackend(job, lambda s: None), libdir

    def test_both_libraries_resolver_first(self):
        be, libdir = self.backend(["libsv_math.so", "libresolver.so"])
        cmd, env = be.run_command("tb", [])
        loads = [a for a in cmd if a.startswith("--load=")]
        lib = os.path.join(libdir, "sv2vhdl")
        self.assertEqual(loads, ["--load=%s,%s" % (os.path.join(lib, "libresolver.so"),
                                                    os.path.join(lib, "libsv_math.so"))])
        self.assertLess(cmd.index(loads[0]), cmd.index("-r"))
        self.assertEqual(env.get("SV2VHDL_QUIET"), "1")

    def test_math_alone(self):
        be, libdir = self.backend(["libsv_math.so"])
        cmd, env = be.run_command("tb", [])
        self.assertIn("--load=" + os.path.join(libdir, "sv2vhdl", "libsv_math.so"), cmd)
        self.assertNotIn("SV2VHDL_QUIET", env)

    def test_none(self):
        be, _ = self.backend([])
        cmd, _ = be.run_command("tb", [])
        self.assertFalse([a for a in cmd if a.startswith("--load")])


class TestFunctionTraceFilter(unittest.TestCase):
    def run_filter(self, lines):
        out, err = [], []
        f = nvcmod.OutputFilter(out.append, err.append)
        for ln in lines:
            f.feed(ln)
        return out, err

    def test_trace_dropped_testbench_kept(self):
        out, err = self.run_filter([
            "** Note: 0ms+0: in chk a=5",
            "   Procedure SV_DISPLAY_LINE [STRING] at lib/sv2vhdl/sv_display_pkg.vhd:527",
            "   Function CHK [LOGIC3D_VECTOR return LOGIC3D_VECTOR] at /x/simv.daidir/nvc/design.vhd:33",
            "   Process :tb:_p0 at /x/simv.daidir/nvc/design.vhd:44",
            "** Note: 0ms+0:    Function lines",
            "   Function lines that are not a trace"])
        self.assertEqual(out, ["in chk a=5", "   Function lines", "   Function lines that are not a trace"])
        self.assertEqual(err, [])


# --------------------------------------------------------- translator warnings

PP_TEXT = "`timescale 1ns/1ps\nmodule tb;\n  wire [3:0] bus;\n  wire w;\n  tran t1 (bus[2], w);\nendmodule\n"


class TestTranslatorWarnings(unittest.TestCase):
    def test_parse_and_locate(self):
        pp = vp.from_text(PP_TEXT, path="/d/simv.daidir/pp/pp.v")
        lines = [
            "iverilog-sv2ghdl: Warning: bus_sig(2) at /d/simv.daidir/nvc/_norm.sv:5 is connected one way "
            "only: its part-select tran joins a translator temporary",
            "iverilog-sv2ghdl: Warning: bus_sig(2) at /d/simv.daidir/nvc/_norm.sv:5 is connected one way "
            "only: its part-select tran joins a translator temporary",
            "iverilog-sv2ghdl: Warning: tri1 net tb.p: its pull is not translated (no internal signal to "
            "attach it to)",
            "some other line"]
        notes = vp.translator_warnings(lines, pp)
        self.assertEqual([(n.severity, n.origin, n.message) for n in notes], [
            ("warning", pp.origin(5), "bus_sig(2) is connected one way only: its part-select tran joins a "
                                      "translator temporary"),
            ("warning", "", "tri1 net tb.p: its pull is not translated (no internal signal to attach it to)")])
        self.assertEqual([n.severity for n in strict(notes, True)], ["error", "error"])

    def test_unmapped_file_keeps_its_location(self):
        notes = vp.translator_warnings(["iverilog-sv2ghdl: Warning: inout port on x(3) at /a b/f.v:12 is "
                                        "connected one way only: the vector is an input or output port of "
                                        "the enclosing module"])
        self.assertEqual((notes[0].origin, notes[0].message),
                         ("/a b/f.v:12", "inout port on x(3) is connected one way only: the vector is an "
                                         "input or output port of the enclosing module"))

    @unittest.skipUnless(os.name == "posix", "needs /bin/sh")
    def test_call_keeps_them_from_the_output(self):
        printed = []
        be = nvcmod.NvcBackend.__new__(nvcmod.NvcBackend)
        be.emit = printed.append
        be.translator_lines = []
        os.environ.pop("VAMOS_VERBOSE", None)
        be._env = lambda: dict(os.environ)
        rc = be._call(["/bin/sh", "-c", "echo one; echo 'iverilog-sv2ghdl: Warning: w at f:1 is x' >&2; "
                                         "echo two"], cwd=os.getcwd(), keep=nvcmod._TRANSLATOR_WARNING,
                      kept=be.translator_lines)
        self.assertEqual(rc, 0)
        self.assertEqual(printed, ["one", "two"])
        self.assertEqual(be.translator_lines, ["iverilog-sv2ghdl: Warning: w at f:1 is x"])


# -------------------------------------------------------------- the -v rule

LIB_SRC = """module leaf(output [3:0] y); assign y = 4'd5; endmodule
module tb; wire [3:0] y; leaf u(.y(y)); endmodule
module leaf(output [3:0] y); assign y = 4'd9; endmodule
module helper; endmodule
"""


class TestAmsLibraryRule(TempDir):
    def test_masks_and_reindexes(self):
        path = os.path.join(self.tmp, "pp.orig.v")
        pp = vp.from_text(LIB_SRC, path=path, lib_lines=[(3, 4)])
        self.assertEqual(len(pp.modules["leaf"]), 2)
        names = vp.apply_library_rule(pp)
        self.assertEqual(names, ["leaf"])
        self.assertTrue(pp.library_masked)
        self.assertEqual(len(pp.modules["leaf"]), 1)
        self.assertIn("4'd5", pp.text)
        self.assertNotIn("4'd9", pp.text)
        self.assertEqual(pp.text.count("\n"), LIB_SRC.count("\n"))      # line numbers kept
        with open(path) as fh:
            self.assertEqual(fh.read(), pp.text)
        self.assertEqual(vp.find_roots(pp), ["tb"])

    def test_nothing_to_do(self):
        pp = vp.from_text("module a; endmodule\n", path=os.path.join(self.tmp, "pp.orig.v"))
        self.assertEqual(vp.apply_library_rule(pp), [])
        self.assertFalse(pp.library_masked)
        self.assertFalse(os.path.exists(pp.path))


# ------------------------------------------- a function the header's parameters use

HEADER_FN = """`timescale 1ns/1ps
module mvcell #(parameter real VREF = 1.0, parameter N = 4, localparam real LSB = scale(VREF, N))
  (input [N-1:0] d, output real a);
  function real scale(input real v, input integer n);
    scale = v / (1 << n);
  endfunction
  function integer unused_fn(input integer x);
    unused_fn = x;
  endfunction
  assign a = d * LSB;
endmodule
module tb;
  wire real a;
  mvcell #(.N(3)) u (.d(3'd5), .a(a));
endmodule
"""


class TestHeaderFunction(TempDir):
    """A function used only by the header's parameter port list is copied into the shell (the
    shell failed: "No function named `scale' found"); one nothing uses is not."""

    def test_copied(self):
        from vamos.ams import shells
        pp = vp.from_text(HEADER_FN)
        h = vp.module_header(pp, "mvcell")
        self.assertTrue(any("function real scale" in t for t in h.body_text), h.body_text)
        self.assertFalse(any("unused_fn" in t for t in h.body_text), h.body_text)
        text = shells.header_shell("mvcell", h)
        self.assertIn("function real scale", text)

    @unittest.skipUnless(vamos_testlib.have_stack(), "needs iverilog (Linux/WSL)")
    def test_shell_elaborates(self):
        from vamos.ams import shells
        pp = vp.from_text(HEADER_FN)
        shell = shells.header_shell("mvcell", vp.module_header(pp, "mvcell"))
        tb = HEADER_FN[HEADER_FN.index("module tb"):]
        path = self.write("shell.v", "`timescale 1ns/1ps\n" + shell + tb)
        r = subprocess.run([tools.find_real("iverilog"), "-g2012", "-tnull", "-s", "tb", path],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stdout + "\n" + shell)


# ------------------------------------------------------------- deck encoding

ENC_SNIPPET = r"""
import sys
sys.path.insert(0, sys.argv[1])
from vamos.netlist import vacask, xyce
from vamos.netlist.ir import Analysis, Instance, Netlist, Source
from vamos.netlist.expr_ast import Num
nl = Netlist(title="* RC filter " + chr(0x2014) + " 5 " + chr(0xb5) + "s", body=[
    Instance("r1", "r", ["a", "0"], value=Num(1000.0)),
    Instance("v1", "v", ["a", "0"], source=Source(dc=Num(1.0)))],
    analyses=[Analysis("tran", {"step": 1e-9, "stop": 1e-6})])
vacask.emit(nl, sys.argv[2])
xyce.emit(nl, sys.argv[3])
print("written")
"""


class TestDeckEncoding(TempDir):
    def test_non_ascii_title_under_an_ascii_locale(self):
        env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
        probe = subprocess.run([sys.executable, "-c", "import locale; print(locale.getpreferredencoding(False))"],
                               env=env, stdout=subprocess.PIPE, universal_newlines=True)
        if "utf" in probe.stdout.lower().replace("-", ""):
            self.skipTest("Python uses UTF-8 here even with LC_ALL=C")
        a, b = os.path.join(self.tmp, "d.sim"), os.path.join(self.tmp, "d.cir")
        r = subprocess.run([sys.executable, "-c", ENC_SNIPPET, vamos_testlib.ROOT, a, b], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                           errors="replace")
        self.assertEqual(r.returncode, 0, r.stdout)
        for p in (a, b):
            with open(p, "rb") as fh:
                self.assertIn((chr(0x2014) + " 5 " + chr(0xb5) + "s").encode("utf-8"), fh.read())


# ------------------------------------------------------- cut: -cell names the subckt

class TestCutPassiveSubckt(unittest.TestCase):
    """The "prims" cut fixture with cell sup bound to a subckt of another name (sup_sp): a
    port_connect -inst statement naming the subckt leaves its vss bit passive."""

    def analyse(self, cfg):
        import test_ams_cut as tc
        c, s = tc.CELLS["prims"]
        cells = [c[0], tc.cell("sup", [("vdd", tc.LOGIC, tc.INPUT), ("vss", tc.LOGIC, tc.INPUT),
                                       ("vb", tc.LOGIC, tc.INPUT, (1, 0))], sub="sup_sp")]
        subs = [s[0], tc.subckt("sup_sp", ["vdd", "vss", "vb[1]", "vb[0]"])]
        hits = RuleHits()
        ana = tc.analyse("prims", cells=cells, subs=subs, cfg=cfg, hits=hits)
        return ana, tc.by_canonical(tc.roles(ana)), hits

    def test_subckt_named_inst_statement(self):
        inst = None
        ana, _, _ = self.analyse(AmsConfig())
        for ci in ana.instances:
            if ci.cell == "sup":
                inst = ci.vpath
        self.assertIsNotNone(inst)
        for pattern in ("sup", "sup_sp", "SUP_SP", "sup_*"):
            with self.subTest(cell=pattern):
                cfg = AmsConfig(port_connects=[PortConnect(pattern, inst=inst, conns=[("vss", "gnd", False)])])
                ana, nodes, hits = self.analyse(cfg)
                self.assertNotIn(inst + ".vss", nodes)
                net = [n for n in ana.nets if any(a == "tb.vss" for a in n.aliases)][0]
                self.assertEqual((net.ports, len(net.passive)), ([], 1))
                self.assertTrue(hits.hit("port_connect_inst#0"))

    def test_guard_names_the_one_way_copy(self):
        """The temporaries guard names the shape still unsupported (a one-way copy: only a select
        of a port of the enclosing module is left one way), not "needs translator patch T2",
        which has landed."""
        import test_ams_cut as tc
        with self.assertRaises(NoteError) as cm:
            tc.roles(tc.analyse("swvp"))
        msgs = [n.message for n in cm.exception.notes]
        self.assertIn("is connected through translator temporary SW_ivl_0_b, a one-way copy (the "
                      "translator joins this bit- or part-select one way only, e.g. a select of an "
                      "input or output port of the enclosing module); use port_dir or connect a "
                      "plain net", msgs[0])
        self.assertFalse(any("patch T2" in m for m in msgs), msgs)

    def test_other_cell_is_not_matched(self):
        ana, _, _ = self.analyse(AmsConfig())
        inst = [ci.vpath for ci in ana.instances if ci.cell == "sup"][0]
        cfg = AmsConfig(port_connects=[PortConnect("cin", inst=inst, conns=[("vss", "gnd", False)])])
        _, nodes, _ = self.analyse(cfg)
        self.assertIn(inst + ".vss", nodes)


# ------------------------------------------------------ unevaluable supply wording

class TestUnevaluableSupplyRule(unittest.TestCase):
    def test_names_the_source(self):
        import test_ams_deck_fixes as df
        temper = ir.Instance("vt", "v", ["vdd_t", "0"], origin="cells.sp:5",
                             source=ir.Source(dc=Binary("*", Num(1.2), Name("temper"))))
        nl = df.netlist(temper)
        case = df._Deck("errors")
        case.setUp()
        try:
            c, x = case.wire("port_connect -cell inv (vdd => vdd_core, vss => 0);\n"
                             "d2a vdd=vdd_t node=tb.u1.a;\n", nl=nl,
                             plan=df.two_inverters(vpaths=("tb.u1",)))
            deck._levels(c, copy.deepcopy(nl), [ir.Instance(k, "x", v, master="inv") for k, v in x.items()])
            errs = [n.message for n in c.notes if n.severity == "error"]
        finally:
            case.tearDown()
        self.assertIn("d2a: vdd=vdd_t is held by V source vt (cells.sp:5), ", " | ".join(errs))
        self.assertIn("which cannot be evaluated, so it gives no level", " | ".join(errs))
        self.assertNotIn("dynamic supplies", " | ".join(errs))


# ------------------------------------------------------- wildcard vdd_port= / vss_port=

class TestWildcardSupplyPort(unittest.TestCase):
    """VCS matches a wildcard vdd_port=/vss_port= against the matched instance's ports (PAMS
    p194: `a2d cell=* port=* vdd_port=vdd* vss_port=vss*'): it was a parse error."""

    def levels(self, text):
        import test_ams_deck_fixes as df
        nl = df.netlist()
        case = df._Deck("errors")
        case.setUp()
        try:
            c, x = case.wire("port_connect -cell inv (vdd => vdd_core, vss => 0);\n" + text, nl=nl,
                             plan=df.two_inverters(vpaths=("tb.u1",)))
            deck._levels(c, copy.deepcopy(nl), [ir.Instance(k, "x", v, master="inv") for k, v in x.items()])
            from vamos.ams import rules
            tnf = rules.unmatched(c.cfg, c.hits)
        finally:
            case.tearDown()
        return c, {n.canonical: n for n in c.plan.nodes}, tnf

    def test_one_port_matches(self):
        c, n, tnf = self.levels("d2a hiv=50% lov=0% vdd_port=vd* vss_port=vs* cell=inv port=a;\n")
        self.assertEqual([x.message for x in c.notes if x.severity == "error"], [])
        self.assertEqual(tnf, [])
        self.assertAlmostEqual(n["tb.u1.a"].d2a.hiv, 0.6)            # 50% of vdd_core's 1.2 V

    def test_several_ports_match(self):
        c, n, tnf = self.levels("d2a vdd_port=v* cell=inv port=a;\n")
        errs = [x.message for x in c.notes if x.severity == "error"]
        self.assertIn("d2a: vdd_port=v* matches 2 ports of tb.u1 (vdd, vss); name one", errs)
        self.assertEqual(tnf, [])

    def test_no_port_matches(self):
        c, n, tnf = self.levels("d2a vdd_port=pwr* cell=inv port=a;\n")
        self.assertTrue(any("vdd_port=pwr*" in t.message and "Option Target Not Found" in t.message
                            for t in tnf), tnf)

    def test_ground_is_a_constant_supply(self):
        """vss=0, vss=gnd and a vss_port= on a port connected to ground name the 0 V supply
        (supply.SupplyGraph.constant_value): they were "not a constant supply"."""
        for rule in ("d2a hiv=50% lov=0% vdd=vdd_core vss=0 node=tb.u1.a;\n",
                     "d2a hiv=50% lov=0% vdd=vdd_core vss=gnd node=tb.u1.a;\n",
                     "d2a hiv=50% lov=0% vdd_port=vdd vss_port=vss cell=inv port=a;\n"):
            with self.subTest(rule=rule):
                c, n, tnf = self.levels(rule)
                self.assertEqual([x.message for x in c.notes if x.severity == "error"], [])
                self.assertEqual(tnf, [])
                self.assertEqual((n["tb.u1.a"].d2a.hiv, n["tb.u1.a"].d2a.lov), (0.6, 0.0))


# -------------------------------------------------------- flow: the IE report stage

class TestReportStage(TempDir):
    def test_report_failure_is_a_stage_error(self):
        out = io.StringIO()
        con = Console()
        pr = flow._Printer(con, False)
        path = os.path.join(self.tmp, "simv.msv", "interface_element.rpt")
        os.makedirs(os.path.join(path, "sub"))                      # a directory in the way
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            with self.assertRaises(flow.AmsError) as cm:
                pr.run(report.write, "the IE report", path, AmsPlan(CutAnalysis("tb"), []), "vacask")
        self.assertEqual(str(cm.exception), "AMS compile failed at the IE report")
        self.assertIn("vamos: error: %s: cannot write the interface-element report: " % path, out.getvalue())


# ----------------------------------------------------- flow: a cut-table miss

@unittest.skipUnless(os.name == "posix", "needs an executable fake tool")
class TestCutTableMiss(TempDir):
    """An instance path the cut's tables lack makes nvc report it and crash while elaborating
    the cut design: the compile names it as an internal error of the cut."""

    def test_named(self):
        fake = self.write("nvc", "#!/bin/sh\necho '** Failure: (init): vamos: no cut table entry for instance "
                                 "path :tb:u9:'\necho 'nvc: crashed'\nexit 1\n")
        os.chmod(fake, 0o755)

        class FakeBe:
            nvc = fake
            libdir = self.tmp
            workdir = self.tmp

            def _metadata(self):
                return {}

            def work_spec(self):
                return "work:" + os.path.join(self_tmp, "work")

            def _env(self):
                return dict(os.environ)

        self_tmp = self.tmp
        out = io.StringIO()
        pr = flow._Printer(Console(), False)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            with self.assertRaises(flow.AmsError) as cm:
                flow._analyse_cut(FakeBe(), os.path.join(self.tmp, "cut.vhd"), "tb", pr)
        self.assertEqual(str(cm.exception), "AMS compile failed at the cut design")
        self.assertIn("the cut has no table entry for instance path :tb:u9:, which nvc elaborated: an "
                      "internal error of the cut", out.getvalue())


# --------------------------------------------------------- simv: no job record

class TestSimvNoJobRecord(TempDir):
    def test_failed_compile_is_named(self):
        daidir = os.path.join(self.tmp, "simv.daidir")
        os.makedirs(daidir)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = simv.run_daidir(daidir, [], {})
        self.assertEqual(rc, 1)
        self.assertIn("vamos: error: %s holds no finished compile (the last compile failed or was "
                      "interrupted, or the directory is incomplete); compile again" % daidir, out.getvalue())

    def test_missing_directory(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = simv.run_daidir(os.path.join(self.tmp, "none.daidir"), [], {})
        self.assertEqual(rc, 1)
        self.assertIn("is not a vamos simulation directory", out.getvalue())

    def test_usage_lists_append_log(self):
        self.assertIn("--vamos-append-log", simv.usage())


if __name__ == "__main__":
    unittest.main()
