"""e2e 29 (docs/VAMOS_SPECTRE_DESIGN.md §11.2 item 29): a production Verilog-A model on the default
engine.  The deck is a Spectre transcription of VACASK's ring benchmark
(benchmark/ring/vacask/runme.sim: a 9-stage PSP103 ring oscillator), generated at test time from the
VACASK source tree that VAMOS_VACASK_SRC names (needs_vacask_src); nothing of the tree is vendored:

  * `ahdl_include "<tree>/devices/psp103v4/psp103.va"`, read in place: four `include`s of macro files,
    `module PSP103VA(D, G, S, B);`, the 413 parameters declared through macros (§3.1, E98);
  * the tree's models.inc transcribed: its wrapper subckts are Spectre already, and each
    `model psp103n psp103va ( <one parameter per line> )` becomes a Spectre model statement continued
    with backslashes, the lower-case card names matched against the module's upper-case declarations
    (§3.9) and printed lower-cased on VACASK (§4.5 item 12);
  * runme.sim's inverter, ring, kick and supply, with `tran1 tran step=0.05n stop=1u maxstep=0.05n`
    (runme.sim's tran with its `tran_method="trap"`, Spectre's moderate traponly).

On VACASK the run is compared with VACASK's native run of runme.sim (a copy of the benchmark
directory, `--skip-embed --skip-postprocess`, the same shipped model: psp103v4.osdi is the
729,272-byte compile of the same tree, E98) at a tight tolerance on what both runs must share: the
oscillation (289 rising crossings of v(1) through 0.6 V; the period within 5e-5, measured 5.0e-6:
3.452497e-9 s native, 3.452479e-9 s vamos; the crossing times within 1e-10 s, measured 3.3e-11) and
the mean supply current over 0.5-1 us (within 2e-4, measured 4e-5).  The waveforms are not compared
point by point: the runs take different time steps (vamos's tran settings, §5.4: tran_lteimplicit=0,
relref sigglobal, lteratio 3.5), and a 33 ps shift of an edge is 0.19 V there.  On Xyce the run is the
v1 refusal of a Verilog-A module's card parameters (§3.9, §4.5 item 13): exit status 2.

The expectation in expect.json's keys is EXPECT below, checked by spectre_testlib's check_case on a
temporary deck directory holding the generated deck (there is no fixture directory: the deck exists
only with the tree).  Measured 2026-10-16 (VACASK 0.3.4-91, openvaf-r 20260616) with the two phase-2
seams of e2e 29 applied by hand (a card-based Verilog-A instance names its module; VACASK's module
names are lower case): every check above holds.

    cd tests/vamos && VAMOS_VACASK_SRC=/usr/local/src/VACASK PYTHONDONTWRITEBYTECODE=1 \\
        python3 -m unittest test_spectre_e2e_29
"""

import os
import re
import shutil
import subprocess
import unittest
from typing import List, Tuple

from spectre_testlib import SpectreCase, have_vacask, needs_flow, needs_vacask_src, vacask_src

from vamos.netlist import ir, rawfile  # noqa: E402
from vamos.notes import NoteError  # noqa: E402

RING = ("benchmark", "ring", "vacask")
PSP = ("devices", "psp103v4", "psp103.va")
LEVEL, STOP = 0.6, 1e-6                                   # vdd/2 and the tran's stop

EXPECT = {
    "argv": ["deck.scs"],
    "engines": ["vacask"],
    "status": 0,
    "files": ["tran1.tran.tran", "logFile"],
    "values": [
        {"file": "tran1.tran.tran", "signal": "vdd", "at": 0.0, "value": 1.2, "rel": 1e-9},
        {"file": "tran1.tran.tran", "signal": "vdd", "at": STOP, "value": 1.2, "rel": 1e-9},
        {"file": "tran1.tran.tran", "signal": "time", "at": None, "row": -1, "value": STOP, "rel": 1e-9},
    ],
    "cross_engine": {"rel": 1e-3},
    "messages": [],
    "xyce": {"status": 2, "files": None, "values": [],
             "messages": [["error", "Verilog-A parameters"], ["error", "on Xyce"]]},
    "gates": ["needs_vacask_src"],
}


# -- the deck -------------------------------------------------------------------------------------------

def transcribe_models(text: str) -> str:
    """models.inc in Spectre: a `model <name> <master> (` block with one parameter per line becomes a
    model statement continued with backslashes; every other line (the wrapper subckts, comments) is
    Spectre already."""
    out, block = [], None
    for line in text.splitlines():
        m = re.match(r"\s*model\s+(\S+)\s+(\S+)\s*\(\s*$", line)
        if block is None and m:
            block = ["model %s %s" % (m.group(1), m.group(2))]
            continue
        if block is not None:
            if line.strip() == ")":
                out.append(" \\\n".join(block))
                block = None
            elif line.strip():
                block.append("    " + line.strip())
            continue
        out.append(line)
    if block is not None:
        raise ValueError("models.inc: a model block is not closed")
    return "\n".join(out) + "\n"


def deck_text(src: str) -> str:
    """The Spectre transcription of <src>/benchmark/ring/vacask (runme.sim and models.inc)."""
    with open(os.path.join(src, *RING, "models.inc"), encoding="utf-8") as fh:
        models = transcribe_models(fh.read())
    ring = "\n".join("u%d (%d %d vdd 0) inverter w=10u l=1u" % (k, k, k % 9 + 1) for k in range(1, 10))
    return ("// e2e 29: VACASK's 9-stage PSP103 ring oscillator (benchmark/ring/vacask) in Spectre\n"
            "simulator lang=spectre\n"
            "ahdl_include \"%s\"\n"
            "%s"
            "subckt inverter (in out vdd vss)\n"
            "parameters w=1u l=0.2u pfact=2\n"
            "  mp (out in vdd vdd) pmos w=w*pfact l=l\n"
            "  mn (out in vss vss) nmos w=w l=l\n"
            "ends\n"
            "i0 (0 1) isource type=pulse val0=0 val1=10u delay=1n rise=1n fall=1n width=1n\n"
            "%s\n"
            "vdd (vdd 0) vsource dc=1.2\n"
            "tran1 tran step=0.05n stop=1u maxstep=0.05n\n") % (os.path.join(src, *PSP), models, ring)


def write_deck(d: str) -> str:
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "deck.scs")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(deck_text(vacask_src()))
    return path


# -- the comparison with the native run -----------------------------------------------------------------

def native_run(tmp: str) -> rawfile.Raw:
    """runme.sim as VACASK runs it, in a copy of the benchmark directory (the tree stays untouched)."""
    from vamos.ams import engines
    d = os.path.join(tmp, "native")
    os.makedirs(d)
    for f in ("runme.sim", "models.inc"):
        shutil.copy(os.path.join(vacask_src(), *RING, f), d)
    env = engines.env_for("vacask", None, dict(os.environ))
    p = subprocess.run([engines.vacask_bin(), "--skip-embed", "--skip-postprocess", "runme.sim"], cwd=d, env=env,
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, errors="replace", timeout=1200)
    if p.returncode != 0:
        raise AssertionError("the native run failed (rc %d):\n%s" % (p.returncode, p.stdout[-2000:]))
    return rawfile.read_all(os.path.join(d, "tran1.raw"))[0]


def crossings(t: List[float], v: List[float], level: float = LEVEL) -> List[float]:
    """The rising crossings of `level`, linearly interpolated."""
    out = []
    for i in range(1, len(t)):
        if v[i - 1] < level <= v[i]:
            out.append(t[i - 1] + (level - v[i - 1]) * (t[i] - t[i - 1]) / (v[i] - v[i - 1]))
    return out


def mean(t: List[float], y: List[float], t0: float, t1: float) -> float:
    s = 0.0
    for j in range(1, len(t)):
        if t[j - 1] >= t0 and t[j] <= t1:
            s += 0.5 * (y[j] + y[j - 1]) * (t[j] - t[j - 1])
    return s / (t1 - t0)


def oscillation(t: List[float], v: List[float]) -> Tuple[List[float], float]:
    cs = crossings(t, v)
    periods = [b - a for a, b in zip(cs, cs[1:])]
    return cs, sum(periods[-10:]) / 10.0


class TestDeck(unittest.TestCase):
    """The transcription itself, on either leg (no engine): it parses, and the cards bind the module."""

    @needs_vacask_src
    def test_the_transcription_parses_and_binds_the_module(self):
        import tempfile
        from vamos.netlist import spectre
        d = tempfile.mkdtemp(prefix="vamos-e2e29-")
        try:
            path = write_deck(d)
            nl = spectre.parse(path, d, spectre.SpectreParseOpts())
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(list(nl.va_modules), ["psp103va"])
        self.assertEqual(nl.va_modules["psp103va"].name, "PSP103VA")
        self.assertGreater(len(nl.va_modules["psp103va"].params), 400)          # the macro-declared list
        cards = {name: m for name, m in nl.models().items()}
        self.assertEqual(sorted(cards), ["psp103n", "psp103p"])
        for name, m in cards.items():
            self.assertEqual(m.prim.lower(), "psp103va", name)
            self.assertEqual(len(m.params), 269, name)                          # models.inc's lines
        insts = [it for s in nl.body if isinstance(s, ir.Subckt) for it in ir.flat_items(s.body)
                 if isinstance(it, ir.Instance) and it.kind == "y"]
        self.assertEqual(len(insts), 2)                                          # m in nmos and in pmos

    @needs_vacask_src
    def test_xyce_refuses_the_card_parameters(self):
        """§3.9: on Xyce a module's card parameters are refused (PyMS ignores them, §4.5 item 13)."""
        import tempfile
        from vamos.netlist import plan as PL, signals as SG, spectre, xyce
        from vamos.spectre.job import Settings
        d = tempfile.mkdtemp(prefix="vamos-e2e29-")
        try:
            path = write_deck(d)
            with self.assertRaises(NoteError) as cm:
                nl = spectre.parse(path, d, spectre.SpectreParseOpts())
                pl = PL.build(nl, "xyce", Settings())
                nl2, sm, _notes = SG.resolve(nl, pl)
                for a in pl.actions:
                    if a.op == "analysis" and a.step is not None:
                        xyce.render(nl2, step=a.step, sigmap=sm, names={})
        finally:
            shutil.rmtree(d, ignore_errors=True)
        msgs = [n.message for n in cm.exception.notes if n.severity == "error"]
        self.assertTrue(any("Verilog-A parameters" in m and "on Xyce" in m and "type" in m for m in msgs), msgs)


class TestE2e29(SpectreCase):
    """WSL: the generated deck through vamos -spectre (check_case with EXPECT), then the VACASK run
    against the native one."""

    @needs_flow
    @needs_vacask_src
    def test_ring_oscillator_against_the_native_run(self):
        case = os.path.join(self.tmp, "29_psp103_ring")
        write_deck(case)
        runs = self.check_case(case, EXPECT)["main"]
        if "vacask" not in runs:
            if not have_vacask():
                self.skipTest("needs VACASK (Linux/WSL)")
            return                       # check_case's VACASK subtest has failed: nothing to compare
        import psfcheck
        pf = psfcheck.check(os.path.join(runs["vacask"].loc.results, "tran1.tran.tran"))
        self.assertEqual(pf.names(), ["1", "2", "3", "4", "5", "6", "7", "8", "9", "vdd", "vdd:p"])
        nat = native_run(self.tmp)
        tn = [p[0] for p in nat.points]
        tv = pf.sweep_values()
        for node in ("1", "5"):
            k = nat.exact(node)
            cn, pn = oscillation(tn, [p[k] for p in nat.points])
            cv, pv = oscillation(tv, pf.column(node))
            with self.subTest(node=node):
                self.assertEqual(len(cv), len(cn), "rising crossings of v(%s)" % node)
                self.assertLess(abs(pv / pn - 1.0), 5e-5, "period %r, native %r" % (pv, pn))
                self.assertLess(max(abs(a - b) for a, b in zip(cv, cn)), 1e-10)
        kn = nat.exact("vdd:flow(br)")
        i_nat = mean(tn, [p[kn] for p in nat.points], 0.5e-6, 1e-6)
        i_vam = mean(tv, pf.column("vdd:p"), 0.5e-6, 1e-6)
        self.assertLess(abs(i_vam / i_nat - 1.0), 2e-4, "mean supply current %r, native %r" % (i_vam, i_nat))


if __name__ == "__main__":
    unittest.main()
