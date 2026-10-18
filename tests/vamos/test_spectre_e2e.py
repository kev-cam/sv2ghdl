"""T2, the end-to-end tier (docs/VAMOS_SPECTRE_DESIGN.md §11.2; phase 2): every deck directory
fixtures/spectre/e2e/<nn>_<slug>/ that holds an expect.json, run through `vamos -spectre` on the
installed engines and checked against it by spectre_testlib.SpectreCase.check_case (the keys and
their rules are spectre_testlib's module docstring).

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_e2e

TestE2E (WSL; needs_flow and an engine)   test_<nn>_<slug> per deck directory: per engine a copy of
    the directory, the run, its exit status, messages, results directory, PSF files (psfcheck; the
    vendored psf-parser in its own subtest), values, the cross-engine comparison and the also decks
The harness itself, on both legs and without an engine (the real runs are WSL's):
TestGates          every gate by name, with the decorators' reasons
TestExpectFiles    every e2e expect.json is well formed and names files the directory holds
TestLocate         where a run's results and log go (§2.5), the percent codes of the harness
TestMessages       the Spectre message blocks and the trailer of the screen (§8.7)
TestValues         reading and comparing values: at, row, points, columns, complex, STRUCT members
TestCheckExpect    check_expect over synthetic results directories (psf.write_analysis and
                   write_logfile from hand-made AnalysisResults, psf/real for `real`), including the
                   comparisons that must fail
"""

import json
import math
import os
import shutil
import types
import unittest

import spectre_testlib as T  # noqa: E402
from spectre_testlib import SpectreCase, spectre_fixture  # noqa: E402

import psfcheck  # noqa: E402
from vamos.output import psf  # noqa: E402
from vamos.spectre.results import AnalysisResult, Signal  # noqa: E402

E2E = spectre_fixture("e2e")
REAL_ASCII = spectre_fixture("psf", "real", "psf-parser", "tests", "data", "ascii")
REAL_LOG = spectre_fixture("psf", "real", "psf-parser", "tests", "data", "binary", "logFile")
GOLDEN_RC = spectre_fixture("psf", "golden", "rc")
BAD_PSF = spectre_fixture("psf", "bad")


def e2e_cases():
    """The e2e deck directories with an expect.json, in order."""
    return sorted(d for d in os.listdir(E2E) if os.path.isfile(os.path.join(E2E, d, "expect.json")))


# -- synthetic results directories ---------------------------------------------------------------------

HEAD = psf.PsfHead(version="vamos (T2 harness test)", date="5:46:18 PM, Tue Apr 15, 2025", design="e2e harness")
STAMP = "Tue Apr 15 17:51:25 2025"
DC_HEADER = {"tolerance.relative": 1e-3, "reltol": 1e-3, "abstol(V)": 1e-6, "abstol(I)": 1e-12, "temp": 27.0,
             "tnom": 27.0, "tempeffects": "all", "gmindc": 1e-12}
TRAN_HEADER = {"tolerance.relative": 1e-3, "start": 0.0, "outputstart": 0.0, "stop": 2e-3, "step": 2e-6,
               "istep": 1e-3, "maxstep": 4e-5, "ic": "all", "useprevic": "no", "skipdc": "no", "reltol": 1e-3,
               "abstol(V)": 1e-6, "abstol(I)": 1e-12, "temp": 27.0, "tnom": 27.0, "tempeffects": "all",
               "errpreset": "moderate", "method": "traponly", "lteratio": 3.5, "relref": "sigglobal",
               "cmin": 0.0, "gmin": 1e-12}
AC_HEADER = {"tolerance.relative": 1e-3, "start": 1.0, "stop": 100.0, "operating point producer": "myac"}
NOISE_HEADER = {"tolerance.relative": 1e-3, "operating point producer": "nz", "output": "pair of nodes",
                "ground": "0", "positive output signal": "out", "negative output signal": "0"}
FREQS = (1.0, 10.0, 100.0)
DCS = (0.0, 0.5, 1.0, 1.5, 2.0)
TIMES = (0.0, 1e-3, 2e-3)
FILES = ["logFile", "myac.ac", "myop.dc", "nz.noise", "swp-000_tr.tran.tran", "swp-001_tr.tran.tran",
         "swp_tr.sweep", "swsrc.dc"]


def ac_out(f, scale=1.0):
    return scale / complex(1, f / 100.0)


def synthetic_results(d, scale=1.0, escchars=False, extra=()):
    """d (a results directory): an op, a dc sweep, an ac, a noise analysis with a STRUCT, and a sweep
    parent with two tran leaves, all through psf.write_analysis, then the logFile; `scale`
    multiplies every value of `out` (a second engine)."""
    res = [
        AnalysisResult("myop-dc", "dc", "myop.dc", description="DC Analysis `myop'", data_type="scalar",
                       header=dict(DC_HEADER),
                       signals=[Signal("in", "V", "V", [1.0]), Signal("out", "V", "V", [0.5 * scale]),
                                Signal("V1:p", "I", "A", [-5e-4]), Signal("net<3>", "V", "V", [0.25])]),
        AnalysisResult("swsrc-dc", "dc", "swsrc.dc", description="DC Analysis `swsrc': V1:dc = (0 V -> 2 V)",
                       data_type="scalar", header=dict(DC_HEADER), sweep=("dc", "V", 1, []),
                       signals=[Signal("out", "V", "V")],
                       rows=lambda: iter([(x, [x / 2.0 * scale]) for x in DCS])),
        AnalysisResult("myac-ac", "ac", "myac.ac", description="AC Analysis `myac': freq = (1 Hz -> 100 Hz)",
                       data_type="swept_scalar", header=dict(AC_HEADER), sweep=("freq", "Hz", 3, []),
                       signals=[Signal("out", "V", "V")],
                       rows=lambda: iter([(f, [ac_out(f, scale)]) for f in FREQS])),
        AnalysisResult("nz-noise", "noise", "nz.noise", description="Noise Analysis `nz': freq = (1 kHz -> 10 kHz)",
                       data_type="swept_struct", header=dict(NOISE_HEADER), sweep=("freq", "Hz", 3, []),
                       signals=[Signal("R1", "resistor", "", members=[("rn", []), ("total", [])]),
                                Signal("out", "V/sqrt(Hz)", "V/sqrt(Hz)"), Signal("gain", "V/V", "V/V"),
                                Signal("in", "V/sqrt(Hz)", "V/sqrt(Hz)")],
                       rows=lambda: iter([(1e3, [[4e-18, 4e-18], 2e-9 * scale, 0.5, 4e-9 * scale]),
                                          (1e4, [[2e-18, 2e-18], 1.5e-9 * scale, 0.5, 3e-9 * scale])])),
        AnalysisResult("swp_tr-sweep", "sweep", "swp_tr.sweep", tree="sweepNode", description="Sweep parent",
                       sweep=("rval", "", 1, [1000.0, 2000.0]), header={"tolerance.relative": 1e-3}),
    ]
    for i, r in enumerate((1000.0, 2000.0)):
        res.append(AnalysisResult(
            "swp-%03d_tr-tran" % i, "tran", "swp-%03d_tr.tran.tran" % i, parent="swp_tr-sweep", tree="leafNode",
            description="Transient Analysis `swp-%03d_tr': time = (0 s -> 2 ms)" % i, data_type="swept_scalar",
            header=dict(TRAN_HEADER), sweep=("time", "s", 1, []), swept={"rval": r},
            signals=[Signal("out", "V", "V")],
            rows=(lambda k: (lambda: iter([(t, [scale * (1 + k) * t / 2e-3]) for t in TIMES])))(i)))
    for r in res:
        psf.write_analysis(d, r, HEAD, escchars=escchars)
    psf.write_logfile(d, res, HEAD, escchars=escchars, stamp=STAMP)
    for name, text in extra:
        with open(os.path.join(d, name), "w") as fh:
            fh.write(text)
    return d


SCREEN = """vamos 0.1.0 (spectre personality) - tools used:
  vamos     git-0000000    GPL-3.0-or-later           /x/bin/vamos
Simulating `deck.scs' on host at 1:00:00 PM, Fri Oct 16, 2026.

Notice from spectre during circuit read-in.
    deck.scs:7: saves allpub
Warning from spectre during transient analysis `tr'.
    V1: rise is absent: the pulse
    gets the transres edge
Aggregate audit (1:00:01 PM, Fri Oct 16, 2026):
Time used: CPU = 0.10 s, elapsed = 1 s.
spectre completes with 0 errors, 1 warnings, and 1 notices
"""


def fake(returncode=0, stdout=SCREEN):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout)


def base_expect():
    """An expectation every synthetic directory meets (every value form of e2e 1-10)."""
    return {
        "argv": ["deck.scs"], "engines": ["vacask", "xyce"], "status": 0, "files": list(FILES),
        "values": [
            {"file": "myop.dc", "signal": "out", "at": None, "value": 0.5, "rel": 1e-12},
            {"file": "myop.dc", "signal": "V1:p", "at": None, "value": -5e-4, "rel": 1e-12},
            {"file": "myop.dc", "signal": "net<3>", "at": None, "value": 0.25, "abs": 1e-15},
            {"file": "myop.dc", "signal": "nosuch", "at": None, "value": None},
            {"file": "swsrc.dc", "signal": "out", "at": 1.5, "value": 0.75, "rel": 1e-12},
            {"file": "swsrc.dc", "signal": "out", "at": 1.25, "value": 0.625, "rel": 1e-12},
            {"file": "swsrc.dc", "signal": "dc", "at": None, "value": list(DCS), "rel": 1e-12},
            {"file": "myac.ac", "signal": "out", "at": 10.0, "value": [ac_out(10).real, ac_out(10).imag], "rel": 1e-12},
            {"file": "myac.ac", "signal": "freq", "at": None, "value": 100.0, "rel": 1e-12, "row": -1, "points": 3},
            {"file": "nz.noise", "signal": "R1", "at": 1000.0, "value": {"rn": 4e-18, "total": 4e-18}, "rel": 1e-9},
            {"file": "nz.noise", "signal": "R1", "at": 5500.0, "value": {"total": 3e-18}, "rel": 1e-9},
            {"file": "nz.noise", "signal": "out", "at": 10000.0, "value": 1.5e-9, "rel": 1e-9},
            {"file": "nz.noise", "signal": "gain", "at": None, "value": 0.5, "rel": 1e-12, "row": 0},
            {"file": "nz.noise", "signal": "nosuch", "at": None, "value": None},
            {"file": "swp_tr.sweep", "signal": "rval", "at": None, "value": [1000.0, 2000.0], "rel": 1e-12},
            {"file": "swp-001_tr.tran.tran", "signal": "out", "at": 1e-3, "value": 1.0, "rel": 1e-12},
            {"file": "swp-000_tr.tran.tran", "signal": "time", "at": None, "value": 2e-3, "rel": 1e-12, "row": -1},
        ],
        "cross_engine": {"rel": 1e-6},
        "messages": [["note", "saves allpub"], ["warning", "rise is absent: the pulse gets the transres edge"]],
    }


# -- the harness, without an engine ---------------------------------------------------------------------

class TestGates(unittest.TestCase):
    def test_every_gate_has_its_decorator_and_reason(self):
        for name, (pred, reason) in T.GATES.items():
            with self.subTest(gate=name):
                dec = getattr(T, name)
                f = dec(lambda: None)
                if pred():
                    self.assertFalse(getattr(f, "__unittest_skip__", False))
                else:
                    self.assertEqual(getattr(f, "__unittest_skip_why__", None), reason)
        for name in ("needs_vacask", "needs_xyce", "needs_engines", "needs_cpp", "needs_psf_parser", "needs_psf_utils",
                     "needs_ngspice", "needs_cadnip", "needs_pyms", "needs_cmc", "needs_vacask_src",
                     "needs_vacask_rawread"):
            self.assertIn(name, T.GATES)

    def test_every_gate_is_used(self):
        """No dead gate: every GATES name gates some test, as a decorator or a name (`needs_x`,
        `T.needs_x`, `[needs_x, ...]`), as `GATES["needs_x"]`, or in an expect.json's gates list (e2e
        and engine_facts; an import is no use).  R-tests-6: needs_psf_utils gated nothing, so setting
        VAMOS_PSF_UTILS ran no check."""
        import ast
        import glob
        here = os.path.dirname(os.path.abspath(__file__))
        used = {}
        for path in sorted(glob.glob(os.path.join(here, "test_*.py"))) + [os.path.join(here, "spectre_testlib.py")]:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for node in ast.walk(tree):
                name = None
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    name = node.id
                elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                    name = node.attr
                elif (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "GATES"
                      and isinstance(node.slice, ast.Constant)):              # Python >= 3.9: the key itself
                    name = node.slice.value
                if name in T.GATES:
                    used.setdefault(name, set()).add(os.path.basename(path))
        facts = spectre_fixture("engine_facts")
        for path in sorted(glob.glob(os.path.join(E2E, "*", "expect.json")) + glob.glob(os.path.join(facts, "*", "expect.json"))):
            with open(path, encoding="utf-8") as fh:
                exp = json.load(fh)
            items = [exp] + (exp.get("also") if isinstance(exp.get("also"), list) else [exp["also"]] if exp.get("also") else [])
            items += exp.get("runs", [])
            for it in items:
                for g in it.get("gates") or []:
                    used.setdefault(g, set()).add(os.path.relpath(path, T.SPECTRE_FIXTURES))
        unused = sorted(n for n in T.GATES if n not in used)
        self.assertEqual(unused, [], "gates no test uses: %s" % ", ".join(unused))

    def test_gate_problems(self):
        self.assertEqual(T.gate_problems([]), [])
        with self.assertRaises(ValueError):
            T.gate_problems(["needs_nothing"])
        old = os.environ.pop("VAMOS_CMC", None)
        try:
            self.assertEqual(T.gate_problems(["needs_cmc"]), ["needs VAMOS_CMC=1 and VAMOS_CMC_EXAMPLES"])
        finally:
            if old is not None:
                os.environ["VAMOS_CMC"] = old


def _environ(**values):
    """Set (a string) or unset (None) environment variables; returns the old values for _restore."""
    old = {k: os.environ.get(k) for k in values}
    for k, v in values.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return old


def _restore(old):
    _environ(**old)


class TestVacaskSrcDefault(unittest.TestCase):
    """D6: e2e 29 is a T2 deck, so needs_vacask_src defaults, when VAMOS_VACASK_SRC is unset, to the
    source tree the VACASK in use was configured from (CMAKE_HOME_DIRECTORY in the CMakeCache.txt of
    the build two levels above engines.vacask_bin()), when that tree holds benchmark/ring and
    devices/psp103v4; otherwise the gate stays a reported skip."""

    def tree(self, with_psp=True, with_ring=True):
        """A fake build (<t>/build/simulator/vacask, <t>/build/CMakeCache.txt) and source tree <t>/src."""
        import tempfile
        t = tempfile.mkdtemp(prefix="vamos-d6-")
        self.addCleanup(shutil.rmtree, t, True)
        src, build = os.path.join(t, "src"), os.path.join(t, "build")
        os.makedirs(os.path.join(build, "simulator"))
        with open(os.path.join(build, "simulator", "vacask"), "w") as fh:
            fh.write("")
        with open(os.path.join(build, "CMakeCache.txt"), "w") as fh:
            fh.write("# This is the CMakeCache file.\nCMAKE_PROJECT_NAME:STATIC=sim\n"
                     "CMAKE_HOME_DIRECTORY:INTERNAL=%s\nsim_SOURCE_DIR:STATIC=%s\n" % (src, src))
        for sub, made in ((("benchmark", "ring", "vacask"), with_ring), (("devices", "psp103v4"), with_psp)):
            os.makedirs(os.path.join(src, *sub) if made else src, exist_ok=True)
        return src, os.path.join(build, "simulator", "vacask")

    def test_unset_defaults_to_the_build_tree(self):
        src, exe = self.tree()
        old = _environ(VAMOS_VACASK=exe, VAMOS_VACASK_SRC=None)
        try:
            self.assertEqual(T.vacask_src(), src)
            self.assertTrue(T.have_vacask_src())
            self.assertEqual(T.gate_problems(["needs_vacask_src"]), [])
        finally:
            _restore(old)

    def test_the_variable_wins_and_an_empty_one_turns_the_default_off(self):
        src, exe = self.tree()
        old = _environ(VAMOS_VACASK=exe, VAMOS_VACASK_SRC=os.path.dirname(src))
        try:
            self.assertEqual(T.vacask_src(), os.path.dirname(src))
            os.environ["VAMOS_VACASK_SRC"] = ""
            self.assertEqual(T.vacask_src(), "")
            self.assertFalse(T.have_vacask_src())
        finally:
            _restore(old)

    def test_a_tree_without_the_ring_or_psp103_is_no_default(self):
        for kw in ({"with_psp": False}, {"with_ring": False}):
            with self.subTest(**kw):
                src, exe = self.tree(**kw)
                old = _environ(VAMOS_VACASK=exe, VAMOS_VACASK_SRC=None)
                try:
                    self.assertEqual(T.vacask_src(), "")
                    self.assertEqual(T.gate_problems(["needs_vacask_src"]),
                                     ["needs VAMOS_VACASK_SRC (VACASK's source tree)"])
                finally:
                    _restore(old)

    def test_no_build_tree_is_no_default(self):
        import tempfile
        t = tempfile.mkdtemp(prefix="vamos-d6-")
        self.addCleanup(shutil.rmtree, t, True)
        old = _environ(VAMOS_VACASK=os.path.join(t, "simulator", "vacask"), VAMOS_VACASK_SRC=None)
        try:
            self.assertEqual(T.vacask_src(), "")
        finally:
            _restore(old)

    def test_a_linked_binary_finds_its_build(self):
        if not hasattr(os, "symlink"):
            self.skipTest("no symbolic links")
        src, exe = self.tree()
        link = os.path.join(os.path.dirname(src), "vacask-link")
        try:
            os.symlink(exe, link)
        except OSError as e:
            self.skipTest("cannot make a symbolic link: %s" % e)
        old = _environ(VAMOS_VACASK=link, VAMOS_VACASK_SRC=None)
        try:
            self.assertEqual(T.vacask_src(), src)
        finally:
            _restore(old)

    def test_the_readme_names_the_gate_environment(self):
        """D6: T3b and T3c stay opt-in, so the fixtures' README names all five variables a gate run sets."""
        with open(spectre_fixture("README"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue("Gate environment" in text, "fixtures/spectre/README has no Gate environment section")
        section = text[text.index("Gate environment"):]
        for var in ("VAMOS_CADNIP_VACASK", "SIM_MODULE_PATH", "VAMOS_CMC=1", "VAMOS_CMC_EXAMPLES", "VAMOS_VACASK_SRC"):
            self.assertTrue(var in section, "the README's gate environment does not name %s" % var)

    @T.needs_vacask
    def test_e2e_29s_gate_holds_here_without_the_variable(self):
        """WSL: the VACASK in use was built from a tree that holds the ring and PSP103 (§0's [E] row),
        so e2e 29 runs without VAMOS_VACASK_SRC instead of being a reported skip."""
        from vamos.ams import engines
        cache = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(engines.vacask_bin()))), "CMakeCache.txt")
        home = ""
        if os.path.isfile(cache):
            with open(cache, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("CMAKE_HOME_DIRECTORY:"):
                        home = line.split("=", 1)[1].strip()
        if not (home and os.path.isdir(os.path.join(home, "benchmark", "ring"))
                and os.path.isdir(os.path.join(home, "devices", "psp103v4"))):
            self.skipTest("the VACASK in use has no build tree with benchmark/ring and devices/psp103v4 (%s)" % cache)
        old = _environ(VAMOS_VACASK_SRC=None)
        try:
            self.assertEqual(T.vacask_src(), home)
            self.assertEqual(T.gate_problems(["needs_vacask_src"]), [])
        finally:
            _restore(old)


def rc_sine(t, tau, f):
    """out(t) of an RC low-pass (time constant tau) driven by sin(2 pi f t) from out(0) = 0."""
    w = 2.0 * math.pi * f
    x = w * tau
    return (math.sin(w * t) - x * math.cos(w * t) + x * math.exp(-t / tau)) / (1.0 + x * x)


def rc_ramp(t, tau, t0, tr):
    """out(t) of an RC low-pass after a 0 -> 1 ramp from t0 over tr, for t >= t0 + tr."""
    return 1.0 - (tau / tr) * (math.exp(tr / tau) - 1.0) * math.exp(-(t - t0) / tau)


# the transient values of the e2e decks whose circuit has an exact answer: (case, file, signal, at) ->
# that answer; e2e 1's pulse rises over 1 us at 1 ms into R C = 1 ms (rval 1k) and 2 ms (2k), e2e 7's
# sin(0 1 1k) drives R C = 1 ms
EXACT = {
    ("01_rc", "swp-000_tr.tran.tran", "out", 5e-3): rc_ramp(5e-3, 1e-3, 1e-3, 1e-6),
    ("01_rc", "swp-001_tr.tran.tran", "out", 5e-3): rc_ramp(5e-3, 2e-3, 1e-3, 1e-6),
    ("07_spice_mode", "timeSweep.tran.tran", "out", 1e-3): rc_sine(1e-3, 1e-3, 1e3),
}
# engine_expect.json's names for them: (case, step, signal, at, the sweep point) -> the key of EXACT
ENGINE_EXACT = {
    ("01_rc", "vamos_a3", "out", 5e-3, 0): ("01_rc", "swp-000_tr.tran.tran", "out", 5e-3),
    ("01_rc", "vamos_a3", "out", 5e-3, 1): ("01_rc", "swp-001_tr.tran.tran", "out", 5e-3),
    ("07_spice_mode", "vamos_a3", "out", 1e-3, 0): ("07_spice_mode", "timeSweep.tran.tran", "out", 1e-3),
}


class TestAnalyticBands(unittest.TestCase):
    """Both legs, no engine: every transient value e2e 1 and 7 expect, in expect.json (the main deck
    and the also decks, every engine) and in engine_expect.json, holds the circuit's exact answer
    within its own tolerance (§11 T2: analytic checks tighter than the engines' agreement, never
    the engines' integration error: a run closer to the circuit's answer must pass too)."""

    def test_the_exact_answers(self):
        self.assertAlmostEqual(EXACT[("07_spice_mode", "timeSweep.tran.tran", "out", 1e-3)], -0.0981197, delta=1e-7)
        self.assertAlmostEqual(EXACT[("01_rc", "swp-000_tr.tran.tran", "out", 5e-3)], 0.981675, delta=1e-6)
        self.assertAlmostEqual(EXACT[("01_rc", "swp-001_tr.tran.tran", "out", 5e-3)], 0.864631, delta=1e-6)

    def test_expect_json_bands_hold_the_exact_answer(self):
        seen = set()
        for case in sorted(set(k[0] for k in EXACT)):
            with open(os.path.join(E2E, case, "expect.json"), encoding="utf-8") as fh:
                exp = json.load(fh)
            for label, item in [("main", exp)] + [("also%d" % k, it) for k, it in enumerate(T.also_items(exp), 1)]:
                for engine in T.run_engines(item):
                    for v in T.expectation_for(item, engine).get("values") or []:
                        key = (case, v["file"], v["signal"], v.get("at"))
                        if key not in EXACT:
                            continue
                        seen.add(key)
                        with self.subTest(case=case, deck=label, engine=engine, file=v["file"]):
                            why = T.compare_value(EXACT[key], v["value"], float(v.get("rel", 0.0)),
                                                  float(v.get("abs", 0.0)))
                            self.assertIsNone(why, "%s %s on %s: the exact %s(%r) = %r is outside the band: %s"
                                              % (case, label, engine, v["signal"], v["at"], EXACT[key], why))
        self.assertEqual(seen, set(EXACT))

    def test_engine_expect_bands_hold_the_exact_answer(self):
        seen = set()
        for case in sorted(set(k[0] for k in EXACT)):
            with open(os.path.join(E2E, case, "engine_expect.json"), encoding="utf-8") as fh:
                exp = json.load(fh)
            for engine in T.ENGINE_NAMES:
                for c in (exp.get(engine) or {}).get("checks", []):
                    point = c.get("plot", 0)
                    if c.get("select"):
                        point = {1000.0: 0, 2000.0: 1}[list(c["select"].values())[0]]
                    key = ENGINE_EXACT.get((case, c.get("step"), c.get("signal"), c.get("at"), point))
                    if key is None:
                        continue
                    seen.add(key)
                    with self.subTest(case=case, engine=engine, step=c["step"], point=point):
                        why = T.compare_value(EXACT[key], c["value"], float(c.get("rel", 0.0)), float(c.get("abs", 0.0)))
                        self.assertIsNone(why, "%s engine_expect %s: the exact %s(%r) = %r is outside the band: %s"
                                          % (case, engine, c["signal"], c["at"], EXACT[key], why))
        self.assertEqual(seen, set(EXACT))


class TestChildEnv(SpectreCase):
    """The run's environment (§11 Layout; D7): TMPDIR and PYMS_CACHE under the test's scratch
    directory, PyMS's evaluator libraries in the shared PYMS_VAE_CACHE (E125), and the docstring
    saying which is which."""

    def test_pyms_vae_cache_stays_shared(self):
        saved = os.environ.pop("PYMS_VAE_CACHE", None)
        try:
            env = self.child_env()
            self.assertNotIn("PYMS_VAE_CACHE", env)                          # PyMS's default, /tmp/pyms_vae_cache
            os.environ["PYMS_VAE_CACHE"] = os.path.join(os.sep, "elsewhere", "vae")
            self.assertEqual(self.child_env()["PYMS_VAE_CACHE"], os.path.join(os.sep, "elsewhere", "vae"))
        finally:
            os.environ.pop("PYMS_VAE_CACHE", None)
            if saved is not None:
                os.environ["PYMS_VAE_CACHE"] = saved
        for var in ("TMPDIR", "PYMS_CACHE"):
            self.assertEqual(os.path.dirname(env[var]), self.tmp, var)
            self.assertTrue(os.path.isdir(env[var]), var)

    def test_the_docstring_names_what_is_isolated(self):
        doc = " ".join(SpectreCase.child_env.__doc__.split())
        self.assertNotIn("the PyMS cache isolated", doc)
        for word in ("TMPDIR", "PYMS_CACHE", "PYMS_VAE_CACHE", "/tmp/pyms_vae_cache", "shared"):
            self.assertIn(word, doc)


class TestExpectFiles(unittest.TestCase):
    """Every e2e expect.json (1-30 as they appear) is well formed for the harness."""

    def test_every_expect_json(self):
        cases = e2e_cases()
        self.assertTrue(cases)
        for case in cases:
            with self.subTest(case=case):
                d = os.path.join(E2E, case)
                with open(os.path.join(d, "expect.json"), encoding="utf-8") as fh:
                    exp = json.load(fh)
                self.assertEqual(T.expect_problems(exp, case + "/expect.json"), [])
                self.assertTrue(T.run_engines(exp), "runs on no engine")
                for item in [exp] + T.also_items(exp):
                    for engine in T.run_engines(item):
                        e = T.expectation_for(item, engine)
                        argv = e["argv"]
                        if argv and argv[0][:1] not in "-+=":
                            self.assertTrue(os.path.isfile(os.path.join(d, argv[0])), "%s: no %s" % (case, argv[0]))
                        else:
                            self.assertIn("stdin", e, "%s: no netlist in argv and no stdin" % case)
                        if e.get("stdin"):
                            self.assertTrue(os.path.isfile(os.path.join(d, e["stdin"])), e["stdin"])
                        loc = T.locate(argv, dict(e.get("env") or {}), d)
                        if e.get("files") and e.get("status") in (0, 1) and not loc.nutmeg:
                            self.assertIn("logFile", e["files"], "%s on %s" % (case, engine))
                        for v in e.get("values") or []:
                            if e.get("files") is not None:
                                self.assertIn(v["file"], e["files"], "%s on %s: values name a file not listed"
                                              % (case, engine))


class TestLocate(unittest.TestCase):
    def test_results_path(self):
        cwd = "/w"
        self.assertEqual(T.locate(["x.scs"], {}, cwd).results, "/w/x.raw")
        self.assertEqual(T.locate(["sub/x.scs"], {}, cwd).results, "/w/sub/x.raw")
        self.assertEqual(T.locate(["x.scs", "-raw", "./psf"], {}, cwd).results, "/w/psf")
        self.assertEqual(T.locate(["x.scs", "-r", "/abs/out"], {}, cwd).results, "/abs/out")
        self.assertEqual(T.locate(["x.scs", "-outdir", "o"], {}, cwd).results, "/w/o/x.raw")
        self.assertEqual(T.locate(["sub/x.scs", "-outdir", "o"], {}, cwd).results, "/w/sub/x.raw")   # §14 q.2
        self.assertEqual(T.locate(["x.scs", "-outdir", "o", "-raw", "r"], {}, cwd).results, "/w/r")
        self.assertEqual(T.locate([], {}, cwd).results, "/w/stdin.raw")
        self.assertEqual(T.locate(["+escchars"], {}, cwd).netlist, None)
        self.assertEqual(T.locate(["x.scs", "-raw", "%C:r.psf"], {}, cwd).results, "/w/x.psf")
        loc = T.locate(["x.scs", "-format", "nutbin"], {}, cwd)
        self.assertEqual((loc.fmt, loc.nutmeg), ("nutbin", True))
        self.assertEqual(T.locate(["x.scs", "-f", "psfxl"], {}, cwd).fmt, "psfascii")
        self.assertTrue(T.locate(["x.scs", "+escchars"], {}, cwd).escchars)

    def test_defaults_layer(self):
        cwd = "/w"
        env = {"SPECTRE_DEFAULTS": "+log %C:r.out -outdir o"}
        loc = T.locate(["x.scs"], env, cwd)
        self.assertEqual((loc.log, loc.results), ("/w/o/x.out", "/w/o/x.raw"))
        self.assertEqual(T.locate(["x.scs", "-log"], env, cwd).log, None)            # argv's -log cancels it
        self.assertEqual(T.locate(["x.scs"], {"SPECTRE_DEFAULTS": "+log %C:r.out"}, cwd).log, "/w/x.out")
        self.assertEqual(T.locate([], {"SPECTRE_DEFAULTS": "+log %C:r.out"}, cwd).log, "/w/stdin.out")
        self.assertEqual(T.locate(["x.scs"], {"spectre_DEFAULTS": "", "SPECTRE_DEFAULTS": "+log a"}, cwd).log, None)
        self.assertEqual(T.locate(["x.scs", "=log", "sub/r.log", "-outdir", "o"], {}, cwd).log, "/w/sub/r.log")
        self.assertEqual(T.locate(["x.scs", "+log", "./psf/spectre.out", "-raw", "./psf"], {}, cwd).log,
                         "/w/psf/spectre.out")

    def test_percent_codes(self):
        self.assertEqual(T.expand_percent("%C:r.raw", "/users/m/opamp.ckt"), "/users/m/opamp.raw")
        self.assertEqual(T.expand_percent("%C:t:r", "/users/m/opamp.ckt"), "opamp")
        self.assertEqual(T.expand_percent("%C:h", "opamp.ckt"), ".")
        self.assertEqual(T.expand_percent("%C:e", "/users/m/opamp.ckt"), "ckt")
        self.assertEqual(T.expand_percent("%C::x", "a.scs"), "a.scs:x")
        self.assertEqual(T.expand_percent("100%%", "a.scs"), "100%")
        self.assertEqual(T.expand_percent("%C.log", None), "stdin.log")
        with self.assertRaises(ValueError):
            T.expand_percent("%D.raw", "a.scs")

    def test_nutmeg_into_a_directory(self):
        d = os.path.join(self._tmp(), "res")
        os.makedirs(d)
        self.assertEqual(T.locate(["sub/x.scs", "-format", "nutascii", "-raw", d], {}, "/w").results,
                         os.path.join(d, "x.raw"))                                    # §8.5: <dir>/%C:r:t.raw

    def _tmp(self):
        import tempfile
        t = tempfile.mkdtemp(prefix="vamos-test-")
        self.addCleanup(shutil.rmtree, t, True)
        return t


class TestMessages(unittest.TestCase):
    def test_blocks(self):
        msgs = T.parse_messages(SCREEN)
        self.assertEqual(msgs, [("note", "deck.scs:7: saves allpub"),
                                ("warning", "V1: rise is absent: the pulse\ngets the transres edge")])
        self.assertTrue(T.find_message(msgs, "warning", "the pulse gets the transres"))
        self.assertTrue(T.find_message(msgs, "notice", "saves allpub"))
        self.assertFalse(T.find_message(msgs, "error", "saves allpub"))
        self.assertFalse(T.find_message(msgs, "note", "rise is absent"))
        self.assertEqual(T.trailer(SCREEN), "spectre completes with 0 errors, 1 warnings, and 1 notices")
        self.assertTrue(T.TRAILER_OK.match(T.trailer(SCREEN)))
        self.assertTrue(T.TRAILER_FATAL.match("spectre terminated prematurely due to fatal error."))
        self.assertEqual(T.parse_messages(""), [])
        self.assertEqual(T.parse_messages("Error from spectre-23.1 during setup.\n    a\n    b\nc\n    d\n"),
                         [("error", "a\nb")])


def _psf_file(rows, sweep="time", names=("out",), kind="tran"):
    """A psfcheck.PsfFile assembled by hand (no file): rows [(x, [values...])]."""
    p = psfcheck.PsfFile("mem")
    p.header["analysis type"] = kind
    p.sweep = psfcheck.Trace(sweep, "sweep", {})
    p.traces = [psfcheck.Trace(n, "V", {}) for n in names]
    p.columns = {sweep: [x for x, _ in rows]}
    for k, n in enumerate(names):
        p.columns[n] = [v[k] for _, v in rows]
    p.points = len(rows)
    return p


class TestValues(unittest.TestCase):
    def test_interpolation_and_grid_points(self):
        f = _psf_file([(0.0, [0.0]), (1.0, [1.0]), (1.0, [3.0]), (2.0, [5.0])])
        self.assertEqual(T.read_entry(f, {"signal": "out", "at": 0.5, "value": 0}), 0.5)
        self.assertEqual(T.read_entry(f, {"signal": "out", "at": 1.0, "value": 0}), 1.0)    # the first exact point
        self.assertEqual(T.read_entry(f, {"signal": "out", "at": 1.5, "value": 0}), 4.0)
        self.assertEqual(T.read_entry(f, {"signal": "out", "row": -1, "value": 0}), 5.0)
        self.assertEqual(T.read_entry(f, {"signal": "time", "row": 2, "value": 0}), 1.0)
        self.assertEqual(T.read_entry(f, {"signal": "time", "value": [0, 1, 1, 2]}), [0.0, 1.0, 1.0, 2.0])
        with self.assertRaises(ValueError):
            T.read_entry(f, {"signal": "out", "at": 2.5, "value": 0})
        with self.assertRaises(ValueError):
            T.read_entry(f, {"signal": "out", "value": 0})                             # 4 points, no at/row
        with self.assertRaises(ValueError):
            T.read_entry(f, {"signal": "nosuch", "at": 1.0, "value": 0})
        with self.assertRaises(ValueError):
            T.read_entry(f, {"signal": "out", "row": 9, "value": 0})
        unsorted = _psf_file([(1000.0, [1.0]), (1000.0, [2.0]), (500.0, [3.0])], sweep="rval", kind="dc")
        self.assertEqual(T.read_entry(unsorted, {"signal": "out", "at": 500.0, "value": 0}), 3.0)
        with self.assertRaises(ValueError):
            T.read_entry(unsorted, {"signal": "out", "at": 700.0, "value": 0})
        one = _psf_file([(1e3, [complex(0.5, -0.1)])], sweep="freq", kind="ac")
        self.assertEqual(T.read_entry(one, {"signal": "out", "value": [0.5, -0.1]}), complex(0.5, -0.1))
        structs = _psf_file([(1.0, [{"rn": 1.0, "total": 2.0}]), (3.0, [{"rn": 3.0, "total": 4.0}])], sweep="freq")
        self.assertEqual(T.read_entry(structs, {"signal": "out", "at": 2.0, "value": {}}), {"rn": 2.0, "total": 3.0})

    def test_compare(self):
        c = T.compare_value
        self.assertIsNone(c(1.0, 1.0))
        self.assertIsNotNone(c(1.0 + 1e-15, 1.0))                                       # neither rel nor abs: exact
        self.assertIsNone(c(1.0005, 1.0, rel=1e-3))
        self.assertIsNotNone(c(1.002, 1.0, rel=1e-3))
        self.assertIsNone(c(1e-13, 0.0, abs_=1e-12))
        self.assertIsNotNone(c(float("nan"), 0.0, abs_=1.0))
        self.assertIsNone(c(complex(0.5, 1e-12), [0.5, 0.0], rel=1e-9))                  # parts against |want|
        self.assertIsNotNone(c(complex(0.5, 1e-6), [0.5, 0.0], rel=1e-9))
        self.assertIsNotNone(c(complex(0.5, 0.0), 0.5, rel=1e-9))                        # complex needs [re, im]
        self.assertIsNone(c({"rn": 1.0, "total": 2.0}, {"total": 2.0}, rel=1e-9))
        self.assertIn("member total", c({"rn": 1.0, "total": 2.5}, {"total": 2.0}, rel=1e-9))
        self.assertIn("no STRUCT member", c({"rn": 1.0}, {"fn": 1.0}))
        self.assertIn("STRUCT", c(1.0, {"rn": 1.0}))
        self.assertIsNone(c([1.0, 2.0], [1.0, 2.0]))
        self.assertIn("3 values, want 2", c([1.0, 2.0, 3.0], [1.0, 2.0]))
        self.assertIn("[1]", c([1.0, 2.5], [1.0, 2.0], rel=1e-3))
        self.assertIsNone(c([complex(1, 2), complex(3, 4)], [[1, 2], [3, 4]]))

    def test_cross_compare(self):
        x = T.cross_compare
        self.assertIsNone(x(1.0, 1.0005, 1e-3))
        self.assertIsNotNone(x(1.0, 1.002, 1e-3))
        self.assertIsNone(x(0.0, 1e-13, 1e-3, 1e-12))
        self.assertIsNone(x(complex(1, 1), complex(1, 1.0001), 1e-3))
        self.assertIsNotNone(x({"total": 1.0}, {"total": 2.0}, 1e-3))
        self.assertIsNotNone(x([1.0, 2.0], [1.0], 1e-3))

    def test_expectation_merging(self):
        with open(os.path.join(E2E, "05_xf", "expect.json"), encoding="utf-8") as fh:
            e5 = json.load(fh)
        self.assertEqual(T.run_engines(e5), ["vacask", "xyce"])
        x = T.expectation_for(e5, "xyce")
        self.assertEqual((x["status"], x["argv"], "files" in x, "values" in x), (2, ["deck.scs"], False, False))
        v = T.expectation_for(e5, "vacask")
        self.assertEqual((v["status"], v["files"]), (0, ["xf1.xf", "logFile"]))
        self.assertNotIn("xyce", v)
        both = {"argv": ["d.scs"], "engines": ["vacask", "xyce"], "status": 0, "files": ["a"],
                "xyce": {"status": 2, "files": None}}
        self.assertEqual(T.expectation_for(both, "xyce")["files"], None)
        self.assertEqual(T.expectation_for(both, "vacask")["files"], ["a"])
        self.assertIsNone(T.expectation_for({"argv": ["d"], "engines": ["vacask"], "status": 0}, "xyce"))
        with open(os.path.join(E2E, "10_inline_bins", "expect.json"), encoding="utf-8") as fh:
            e10 = json.load(fh)
        also = T.also_items(e10)
        self.assertEqual(len(also), 1)
        self.assertEqual((also[0]["argv"], also[0]["engines"], also[0]["status"]),
                         (["deck_cross.scs"], ["vacask", "xyce"], 2))
        one = T.also_items({"engines": ["vacask"], "gates": ["needs_cpp"],
                            "also": {"deck": "b.scs", "status": 1, "gates": ["needs_ngspice"]}})
        self.assertEqual(one, [{"deck": "b.scs", "argv": ["b.scs"], "status": 1, "engines": ["vacask"],
                                "gates": ["needs_cpp", "needs_ngspice"]}])

    def test_expect_problems(self):
        good = base_expect()
        self.assertEqual(T.expect_problems(good), [])
        for bad, what in (({"engines": ["vacask"], "status": 0}, "no argv"),
                          (dict(good, status=5), "status must be"),
                          (dict(good, engines=["spectre"]), "engines must be"),
                          (dict(good, bogus=1), "unknown keys"),
                          (dict(good, values=[{"file": "a", "signal": "b"}]), "no value"),
                          (dict(good, values=[{"file": "a", "signal": "b", "value": 1, "tol": 1}]), "unknown keys"),
                          (dict(good, values=[{"file": "a", "signal": "b", "value": 1, "at": 1, "row": 0}]),
                           "not both"),
                          (dict(good, messages=[["fatal", "x"]]), "severity"),
                          (dict(good, gates=["needs_nothing"]), "unknown gate"),
                          (dict(good, env={"A": 1}), "env must"),
                          (dict(good, real={"logfile": "x"}), "real must"),
                          (dict(good, xyce={"engines": ["xyce"]}), "unknown keys"),
                          (dict(good, also=[{"status": 2}]), "deck or argv"),
                          (dict(good, also={"deck": "x.scs"}), "no status"),
                          (dict(good, xyce={"status": 2, "messages": [["error", "x"]]}), "merged under it")):
            with self.subTest(what=what):
                probs = T.expect_problems(bad)
                self.assertTrue(any(what in p for p in probs), probs)


class TestCheckExpect(SpectreCase):
    """check_expect over synthetic results directories; every failing comparison must fail."""

    def make(self, name="run", **kw):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        with open(os.path.join(d, "deck.scs"), "w") as fh:
            fh.write("// synthetic\n")
        synthetic_results(os.path.join(d, "deck.raw"), **kw)
        return d

    def must_fail(self, d, expect, what, engine="vacask", proc=None):
        with self.assertRaises(AssertionError) as cm:
            self.check_expect(d, expect, engine, proc=proc or fake())
        self.assertIn(what, str(cm.exception))
        return str(cm.exception)

    def test_a_matching_run_passes(self):
        d = self.make()
        r = self.check_expect(d, base_expect(), "vacask", proc=fake())
        self.assertEqual(sorted(r.files), sorted(f for f in FILES if f != "logFile"))
        self.assertEqual(r.loc.results, os.path.join(d, "deck.raw"))
        self.assertEqual(len(r.values), sum(1 for v in base_expect()["values"] if v["value"] is not None))
        self.assertEqual(r.values[T.value_key(base_expect()["values"][0])], 0.5)

    def test_every_mismatch_fails(self):
        d = self.make()
        e = base_expect()
        self.must_fail(d, e, "exit status 1, expected 0", proc=fake(returncode=1))
        self.must_fail(d, dict(e, files=FILES[1:]), "unexpected ['logFile']")
        self.must_fail(d, dict(e, files=FILES + ["mytran.tran.tran"]), "missing ['mytran.tran.tran']")
        for k, entry, what in ((0, dict(e["values"][0], value=0.6), "got 0.5, want 0.6"),
                               (3, {"file": "myop.dc", "signal": "out", "at": None, "value": None}, "expected absent"),
                               (4, dict(e["values"][4], at=3.0), "outside the sweep"),
                               (7, dict(e["values"][7], value=[0.9900990099, 0.1]), "got ("),
                               (8, dict(e["values"][8], points=4), "3 points, expected 4"),
                               (9, dict(e["values"][9], value={"rn": 4e-18, "fn": 1e-18}), "no STRUCT member 'fn'"),
                               (10, dict(e["values"][10], value={"total": 3.1e-18}), "member total"),
                               (14, dict(e["values"][14], value=[1000.0, 2000.0, 3000.0]), "2 values, want 3"),
                               (15, dict(e["values"][15], file="swp-002_tr.tran.tran"), "no PSF file"),
                               (16, dict(e["values"][16], row=3), "row 3 of 3 points"),
                               (0, dict(e["values"][0], at=1.0), "operating-point file"),
                               (0, dict(e["values"][0], signal="V1:n"), "no value 'V1:n'")):
            with self.subTest(entry=k, what=what):
                vals = list(e["values"])
                vals[k] = entry
                self.must_fail(d, dict(e, values=vals), what)
        self.must_fail(d, dict(e, messages=[["error", "saves allpub"]]), "no error message holding 'saves allpub'")
        self.must_fail(d, dict(e, messages=[["note", "nothing like it"]]), "no note message")

    def test_overrides_and_the_engine_only_an_override_names(self):
        d = self.make()
        e = dict(base_expect(), xyce={"status": 2, "messages": [["error", "xf is not available on Xyce"]],
                                     "files": None, "values": []})
        screen = "Error from spectre during setup.\n    xf1: xf is not available on Xyce\n" \
                 "spectre terminated prematurely due to fatal error.\n"
        self.check_expect(d, e, "xyce", proc=fake(2, screen))
        self.must_fail(d, e, "exit status 0, expected 2", engine="xyce")
        e5 = dict(e, engines=["vacask"])                        # e2e 5's shape: xyce named by its override only
        self.assertEqual(T.run_engines(e5), ["vacask", "xyce"])
        self.check_expect(d, e5, "xyce", proc=fake(2, screen))

    def test_the_log_file_and_escchars(self):
        d = self.make(escchars=True)
        with open(os.path.join(d, "deck.raw", "myop.dc"), encoding="latin-1") as fh:
            self.assertIn('"net\\<3\\>"', fh.read())
        with open(os.path.join(d, "run.log"), "w") as fh:
            fh.write(SCREEN.replace("saves allpub", "saves allpub (in the file only)"))
        e = dict(base_expect(), argv=["deck.scs", "+escchars", "=log", "run.log"])
        e["messages"] = [["note", "(in the file only)"]]
        r = self.check_expect(d, e, "vacask", proc=fake(0, ""))
        self.assertEqual(r.loc.log, os.path.join(d, "run.log"))
        self.assertTrue(r.loc.escchars)
        self.must_fail(d, dict(e, argv=["deck.scs", "+escchars"]), "no note message", proc=fake(0, ""))
        self.must_fail(d, dict(e, argv=["deck.scs", "=log", "run.log"]), "psfcheck rejects", proc=fake(0, ""))

    def test_psfcheck_and_the_logfile_listing(self):
        d = self.make()
        res = os.path.join(d, "deck.raw")
        shutil.copyfile(os.path.join(BAD_PSF, sorted(f for f in os.listdir(BAD_PSF) if f != "README")[0]),
                        os.path.join(res, "bad.dc"))
        e = dict(base_expect(), files=FILES + ["bad.dc"])
        self.must_fail(d, e, "psfcheck rejects bad.dc")
        os.remove(os.path.join(res, "bad.dc"))
        shutil.copyfile(os.path.join(res, "myop.dc"), os.path.join(res, "extra.dc"))
        self.must_fail(d, dict(base_expect(), files=FILES + ["extra.dc"]), "logFile lists")
        os.remove(os.path.join(res, "extra.dc"))
        os.remove(os.path.join(res, "logFile"))
        self.must_fail(d, dict(base_expect(), files=FILES[1:]), "but no logFile")

    def test_results_absent_and_status_2(self):
        d = os.path.join(self.tmp, "parse")
        os.makedirs(d)
        screen = "Error from spectre during circuit read-in.\n    deck.scs:4: unclosed '('\n" \
                 "spectre terminated prematurely due to fatal error.\n"
        e = {"argv": ["deck.scs"], "engines": ["vacask"], "status": 2, "messages": [["error", "deck.scs:4"]]}
        self.check_expect(d, e, "vacask", proc=fake(2, screen))
        self.check_expect(d, dict(e, files=[]), "vacask", proc=fake(2, screen))       # absent = empty
        self.must_fail(d, dict(e, files=["logFile"]), "missing ['logFile']", proc=fake(2, screen))

    def test_nutmeg_results(self):
        d = os.path.join(self.tmp, "nut")
        os.makedirs(d)
        with open(os.path.join(d, "deck.raw"), "w") as fh:
            fh.write("Title: x\n")
        e = {"argv": ["deck.scs", "-format", "nutascii"], "engines": ["vacask"], "status": 0, "files": ["deck.raw"]}
        self.check_expect(d, e, "vacask", proc=fake())
        with open(os.path.join(d, "logFile"), "w") as fh:
            fh.write("x")
        self.must_fail(d, e, "no logFile")
        os.remove(os.path.join(d, "logFile"))
        self.must_fail(d, dict(e, files=["other.raw"]), "results file")
        self.must_fail(d, dict(e, values=[{"file": "x.dc", "signal": "a", "at": None, "value": 1.0}]),
                       "values are read from PSF files")
        os.remove(os.path.join(d, "deck.raw"))
        os.makedirs(os.path.join(d, "deck.raw"))
        self.must_fail(d, e, "results file")                    # into the directory: deck.raw/deck.raw, absent
        with open(os.path.join(d, "deck.raw", "deck.raw"), "w") as fh:
            fh.write("Title: x\n")
        self.check_expect(d, e, "vacask", proc=fake())

    def test_cross_engine(self):
        da, db, dc = self.make("a"), self.make("b", scale=1.0 + 1e-8), self.make("c", scale=1.001)
        e = base_expect()
        loose = dict(e, values=[dict(v, rel=1e-2) if "rel" in v else v for v in e["values"]])
        ra = self.check_expect(da, e, "vacask", proc=fake())
        self.must_fail(db, e, "values[0] myop.dc out")                  # 1e-8 off: outside rel 1e-12
        rb = self.check_expect(db, loose, "xyce", proc=fake())
        rc = self.check_expect(dc, loose, "xyce", proc=fake())
        self.assertEqual(T.cross_engine_problems({"vacask": ra, "xyce": rb}, e), [])
        probs = T.cross_engine_problems({"vacask": ra, "xyce": rc}, e)
        self.assertTrue(probs)
        self.assertTrue(any("myop.dc out" in p and "vacask against xyce" in p for p in probs), probs)
        self.assertEqual(T.cross_engine_problems({"vacask": ra}, e), [])
        self.assertEqual(T.cross_engine_problems({"vacask": ra, "xyce": rc}, dict(e, cross_engine={"rel": 1e-2})), [])

    def test_real_spectre_run(self):
        """`real`: compare_results_dir against psf/real (e2e 28); the S4 rebuild passes with the
        omitted keys, the real run itself passes with none, and a missing file fails."""
        d = os.path.join(self.tmp, "rc")
        os.makedirs(d)
        shutil.copytree(GOLDEN_RC, os.path.join(d, "deck.raw"))
        real = {"dir": "psf/real/psf-parser/tests/data/ascii", "logfile": "psf/real/psf-parser/tests/data/binary/logFile",
                "omit": "RC_OMIT_KEYS"}
        e = {"argv": ["deck.scs"], "engines": ["vacask"], "status": 0, "real": real}
        self.check_expect(d, e, "vacask", proc=fake())
        self.must_fail(d, dict(e, real=dict(real, omit=[])), "real (psf/real/psf-parser/tests/data/ascii): files")
        beside = {"dir": "../../psf/real/psf-parser/tests/data/ascii", "omit": "RC_OMIT_KEYS",
                  "logfile": "../../psf/real/psf-parser/tests/data/binary/logFile"}      # e2e 28's spelling
        self.check_expect(d, dict(e, real=beside), "vacask", proc=fake(), case_dir=os.path.join(E2E, "01_rc"))
        self.must_fail(d, dict(e, real=beside), "real (../../psf/real")                 # not from the copy
        d2 = os.path.join(self.tmp, "real")
        os.makedirs(d2)
        shutil.copytree(REAL_ASCII, os.path.join(d2, "deck.raw"))
        shutil.copyfile(REAL_LOG, os.path.join(d2, "deck.raw", "logFile"))
        self.check_expect(d2, dict(e, real=dict(real, omit=[])), "vacask", proc=fake())
        os.remove(os.path.join(d2, "deck.raw", "mydc.dc"))
        self.must_fail(d2, dict(e, real=dict(real, omit=[])), "logFile")

    def test_check_case_skips_with_the_gate_reason(self):
        e = dict(base_expect(), gates=["needs_cmc"])
        old = os.environ.pop("VAMOS_CMC", None)
        try:
            with self.assertRaises(unittest.SkipTest) as cm:
                self.check_case(self.tmp, e)
        finally:
            if old is not None:
                os.environ["VAMOS_CMC"] = old
        self.assertEqual(str(cm.exception), "needs VAMOS_CMC=1 and VAMOS_CMC_EXAMPLES")
        with self.assertRaises(AssertionError):
            self.check_case(self.tmp, dict(base_expect(), bogus=1))                   # a malformed expect.json

    @T.needs_psf_parser
    def test_psf_parser_reads_a_synthetic_directory(self):
        d = self.make()
        self.check_psf_parser(os.path.join(d, "deck.raw"))
        pp = T.import_psf_parser()
        saved = pp.PsfFile

        def broken(path):
            raise ValueError("cannot read %s" % os.path.basename(path))
        pp.PsfFile = broken
        try:
            with self.assertRaises(AssertionError) as cm:
                self.check_psf_parser(os.path.join(d, "deck.raw"))
        finally:
            pp.PsfFile = saved
        self.assertIn("myop.dc: psf-parser: ValueError: cannot read myop.dc", str(cm.exception))
        empty = os.path.join(self.tmp, "empty")                       # a status-2 run's logFile lists nothing
        psf.write_logfile(empty, [], HEAD, stamp=STAMP)
        self.check_psf_parser(empty)

    @T.needs_psf_utils
    def test_psf_utils_reads_a_synthetic_directory(self):
        """§11 Third-party PSF consumers: psf_utils reads every data file but logFile and the sweep
        parent [E83], its values (assigned by position in TRACE order) equal psfcheck's, and it
        leaves no cache file beside them; a reader that puts values on the wrong traces fails."""
        d = self.make(escchars=True)
        res = os.path.join(d, "deck.raw")
        read = self.check_psf_utils(res, escchars=True)
        self.assertEqual(sorted(read), sorted(f for f in FILES if f not in ("logFile", "swp_tr.sweep")))
        self.assertEqual(sorted(os.listdir(res)), sorted(FILES))
        pu = T.import_psf_utils()
        saved = pu.PSF

        def swapped(path, **kw):
            p = saved(path, **kw)
            names = list(p.signals)
            if len(names) >= 2:                                        # the first and last values change places
                a, b = p.signals[names[0]], p.signals[names[-1]]
                a.ordinate, b.ordinate = b.ordinate, a.ordinate
            return p
        pu.PSF = swapped
        try:
            with self.assertRaises(AssertionError) as cm:
                self.check_psf_utils(res, escchars=True)
        finally:
            pu.PSF = saved
        self.assertIn("myop.dc: in: psf_utils reads 0.25, psfcheck 1.0", str(cm.exception))
        self.assertIn("nz.noise: R1:rn: point 0: psf_utils reads 4e-09, psfcheck 4e-18", str(cm.exception))

    @T.needs_psf_utils
    def test_psf_utils_reads_the_goldens(self):
        """The PSF writer's goldens (psf/golden/*/, read in place: nothing is written beside them)."""
        golden = spectre_fixture("psf", "golden")
        dirs = [n for n in sorted(os.listdir(golden)) if os.path.isfile(os.path.join(golden, n, "logFile"))]
        self.assertGreaterEqual(len(dirs), 7, dirs)
        for n in dirs:
            with self.subTest(golden=n):
                before = sorted(os.listdir(os.path.join(golden, n)))
                self.assertTrue(self.check_psf_utils(os.path.join(golden, n)))
                self.assertEqual(sorted(os.listdir(os.path.join(golden, n))), before)


# -- the decks (WSL: needs_flow and an engine) ----------------------------------------------------------

class TestE2E(SpectreCase):
    """Every e2e deck directory with an expect.json, through vamos -spectre on the installed
    engines (§11.2); a deck whose gates fail, or a tree without flow.py, is a reported skip."""


def _case_test(case):
    def test(self):
        self.check_case(os.path.join(E2E, case))
    test.__name__ = "test_" + case
    test.__doc__ = "e2e %s through vamos -spectre (§11.2 %d)" % (case, int(case[:2]))
    return test


for _case in e2e_cases():
    setattr(TestE2E, "test_" + _case, _case_test(_case))


if __name__ == "__main__":
    unittest.main()
