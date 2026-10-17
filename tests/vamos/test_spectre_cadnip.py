"""T3b, the Cadnip engine oracle (docs/VAMOS_SPECTRE_DESIGN.md §11.3 (b), §13 E76; phase 2).

    cd tests/vamos && VAMOS_CADNIP_VACASK=<build>/simulator/vacask \\
        SIM_MODULE_PATH=/opt/build.VACASK/Release/devices PYTHONDONTWRITEBYTECODE=1 \\
        python3 -m unittest test_spectre_cadnip

The oracle is VACASK built with -DCADNIP_PARSERS=ON (fixtures/spectre/cadnip/BUILD): it reads a
Spectre-language file named by `include "<file>" lang=spectre` through NetlistParse.rs, a second,
independent reading of the Spectre text, and simulates it with the engine vamos drives.  For each
fixtures/spectre/cadnip/<deck>.scs (the subset Spectre and VACASK share: no type= sources, no integer
division, no `% _ c meg x` suffixes, no node groups or SPICE sections; resistor, capacitor, inductor,
vsource and isource with dc only):

  (1) the committed dump <deck>.cst (made by cst/regen.sh from the deck without its title) tiles the body
      with no Error or Incomplete node, otherwise the case errors, because the Cadnip build drops the
      statements it cannot parse silently [E76]; the tiling is T0's (test_spectre_cst_oracle's load_cst,
      untiled and file_text, reused here), so no spectre_dump runs at test time;
  (2) line 1, the title, is stripped: body.scs;
  (3) top.sim = a title line, `ground 0`, one `model <p> <p>` per primitive master the deck uses,
      `include "body.scs" lang=spectre` and vamos's rendered control block (the `control` ... `endc` of
      the deck vamos renders for VACASK), so both runs carry the same analyses, options and saves;
  (4) the oracle runs with SIM_MODULE_PATH as the needs_cadnip gate reads it from the environment (the
      Cadnip build's device directory);
  (5) vamos -> VACASK runs through the phase-1b chain (test_spectre_chain.chain and run_engine:
      spectre.parse -> plan.build -> signals.resolve -> vacask.render -> engines.vacask_bin() under
      env_for), because flow.py is written in parallel (the integration stage may switch this step to the
      command line); every rawfile column of every analysis is compared by name, rel 1e-9 (the same
      engine).

The oracle itself is also held to expect.json, the operating points it computed in phase 0 (its `file`
op1.raw is that run's op analysis, here the plan's op step), so a change of the Cadnip build or of the
wrapping shows as such, not as a vamos difference.  The tier checks hierarchy, parameter scoping and
overrides, inline subckts and conditionals independently of vamos's emitter; it is not a fidelity oracle.
VACASK's own test/spectre_*.scs (its own dialect: quoted enums, `model vsrc vsource`) serve as a smoke
test of the harness only (needs_vacask_src), never as vamos inputs.

TestCadnipFixtures runs on both legs (no engine): the fixture set is complete and inside the subset.
"""

import json
import os
import re
import shutil
import unittest
from typing import Dict, List, Optional

from vamos_testlib import TempDir, run  # noqa: F401
from spectre_testlib import (cadnip_bin, needs_cadnip, needs_vacask, needs_vacask_src, spectre_fixture,  # noqa: E402
                             vacask_src)

from test_spectre_cst_oracle import BAD, file_text, load_cst, untiled  # noqa: E402  (T0's tiling, §11.3 (b) 1)
from test_spectre_chain import chain, run_engine  # noqa: E402  (the phase-1b chain, §11.3 (b) 5)

from vamos.netlist import ir, rawfile, tables as T  # noqa: E402

CADNIP = spectre_fixture("cadnip")
DECKS = sorted(os.path.splitext(f)[0] for f in os.listdir(CADNIP) if f.endswith(".scs"))
EXPECT = os.path.join(CADNIP, "expect.json")
PRIMITIVES = ("resistor", "capacitor", "inductor", "vsource", "isource")   # §11.3 (b)'s subset
# what the Cadnip build reads differently from Spectre, so outside the subset [E76]: `1/2` is 0, `1x` 1e6,
# `50_Ohms`, `10%` and `2c` are parse errors; a SPICE section turns a .model line into the title
SUBSET_BAD = (
    ("a type= source", re.compile(r"\btype\s*=", re.I)),
    ("an integer division", re.compile(r"(?<![\w.])\d+\s*/\s*\d+(?![\w.])")),
    ("a % _ c meg x suffix", re.compile(r"(?<![\w.])\d+(?:\.\d*)?(?:[eE][-+]?\d+)?(?:%|_|(?:c|meg|x)\b)", re.I)),
    ("a SPICE section", re.compile(r"\bsimulator\s+lang\s*=\s*spice\b", re.I)),
)
REL = 1e-9
TIMEOUT = 600


class OracleUnusable(Exception):
    """The case errors (§11.3 (b) 1): the oracle cannot be trusted on this deck."""


def load_expect() -> dict:
    with open(EXPECT, encoding="utf-8") as fh:
        return json.load(fh)


# -- (1) the committed dump --------------------------------------------------------------------------------

def bad_nodes(node) -> List[str]:
    """The Error/Incomplete nodes of a dump, at any depth, as 'Kind start-end'."""
    out = ["%s %d-%d" % (node.kind, node.start, node.end)] if node.kind in BAD else []
    for c in node.children:
        out += bad_nodes(c)
    return out


def dump_problem(deck: str) -> Optional[str]:
    """Why <deck>.cst does not vouch for the deck's body (None when it tiles it with no Error node)."""
    rel = "cadnip/%s.scs" % deck
    dump = os.path.join(CADNIP, deck + ".cst")
    if not os.path.isfile(dump):
        return "%s has no committed dump %s (cst/regen.sh)" % (rel, os.path.basename(dump))
    src = file_text(rel, True)                    # the dumps are of the body, the title stripped
    root = load_cst(dump)
    miss = untiled(src, root)
    if miss is not None:
        return "%s: %r at byte %d of the body is outside every statement node of %s" % (rel, miss[1], miss[0],
                                                                                        os.path.basename(dump))
    bad = bad_nodes(root)
    if bad:
        return "%s: the dump has %s" % (rel, ", ".join(bad))
    return None


# -- (3) the oracle's deck -------------------------------------------------------------------------------

def primitives_used(nl: ir.Netlist) -> List[str]:
    """The primitive masters the deck's instances name, in PRIMITIVES order (top level, every subckt,
    every conditional branch)."""
    used = set()
    for items in [nl.body] + [s.body for s in T.all_subckts(nl)]:
        for it in ir.flat_items(items):
            if isinstance(it, ir.Instance) and it.kind != "x" and it.prim:
                used.add(it.prim)
    return [p for p in PRIMITIVES if p in used] + sorted(used - set(PRIMITIVES))


def control_block(deck: str) -> str:
    """The `control` ... `endc` block of a deck vamos rendered for VACASK."""
    lines = deck.splitlines()
    starts = [k for k, l in enumerate(lines) if l.strip() == "control"]
    ends = [k for k, l in enumerate(lines) if l.strip() == "endc"]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise OracleUnusable("vamos's VACASK deck has no single control ... endc block:\n%s" % deck)
    return "\n".join(lines[starts[0]:ends[0] + 1]) + "\n"


def oracle_deck(deck: str, prims: List[str], control: str) -> str:
    """top.sim (§11.3 (b) 3): the title, `ground 0`, a model line per primitive master used, the include
    of the body in Spectre mode, vamos's control block."""
    out = ["cadnip oracle %s" % deck, "ground 0"]
    out += ["model %s %s" % (p, p) for p in prims]
    out.append('include "body.scs" lang=spectre')
    return "\n".join(out) + "\n" + control


# -- (5) the comparison --------------------------------------------------------------------------------

def _close(a, b, rel: float) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b))


def raw_differences(got: List[rawfile.Raw], want: List[rawfile.Raw], rel: float) -> List[str]:
    """Every difference between two rawfiles (got: vamos's, want: the oracle's), column by column by name."""
    out: List[str] = []
    if len(got) != len(want):
        return ["%d plots, the oracle %d" % (len(got), len(want))]
    for k, (g, w) in enumerate(zip(got, want)):
        if sorted(g.names()) != sorted(w.names()):
            out.append("plot %d: columns %s, the oracle %s" % (k, sorted(g.names()), sorted(w.names())))
            continue
        if len(g.points) != len(w.points):
            out.append("plot %d: %d points, the oracle %d" % (k, len(g.points), len(w.points)))
            continue
        for name in w.names():
            gi, wi = g.exact(name), w.exact(name)
            for row, (gp, wp) in enumerate(zip(g.points, w.points)):
                if not _close(gp[gi], wp[wi], rel):
                    out.append("plot %d %s row %d: vamos %r, the oracle %r" % (k, name, row, gp[gi], wp[wi]))
                    break
    return out


class TestCadnipFixtures(unittest.TestCase):
    """Both legs, no engine: every deck has its dump and its expect.json entry, and stays in the subset."""

    def test_every_deck_has_a_dump_and_an_expectation(self):
        self.assertTrue(DECKS, CADNIP)
        exp = load_expect()
        self.assertEqual(sorted(k for k in exp if not k.startswith("_")), DECKS)
        for deck in DECKS:
            with self.subTest(deck=deck):
                self.assertTrue(os.path.isfile(os.path.join(CADNIP, deck + ".cst")), deck)
                e = exp[deck]
                self.assertEqual(e["argv"], [deck + ".scs"])
                self.assertEqual((e["engines"], e["status"]), (["vacask"], 0))
                self.assertTrue(e["values"], deck)
                for v in e["values"]:
                    self.assertIsNone(v["at"], deck)            # an operating point has no sweep

    def test_decks_stay_in_the_shared_subset(self):
        """§11.3 (b)'s inputs: resistor, capacitor, inductor, vsource and isource, the sources with dc only;
        no type= source, integer division, `% _ c meg x` suffix or SPICE section [E76]."""
        for deck in DECKS:
            with self.subTest(deck=deck):
                r = chain(os.path.join(CADNIP, deck + ".scs"), "vacask")
                prims = primitives_used(r.nl)
                self.assertTrue(prims, deck)
                self.assertEqual([p for p in prims if p not in PRIMITIVES], [], deck)
                for items in [r.nl.body] + [s.body for s in T.all_subckts(r.nl)]:
                    for it in ir.flat_items(items):
                        if isinstance(it, ir.Instance) and it.prim in ("vsource", "isource"):
                            self.assertEqual(sorted(it.source.spectre), ["dc"], "%s %s" % (deck, it.name))
                body = file_text("cadnip/%s.scs" % deck, True).decode("utf-8")
                body = re.sub(r"//.*", "", body)
                for what, pat in SUBSET_BAD:
                    self.assertIsNone(pat.search(body), "%s: %s" % (deck, what))


class TestCadnipOracle(TempDir):
    """WSL, needs_cadnip: the oracle against vamos -> VACASK, deck by deck."""

    def run_oracle(self, d: str) -> object:
        """The Cadnip VACASK on top.sim, SIM_MODULE_PATH from the environment as the gate reads it."""
        return run([cadnip_bin(), "top.sim"], cwd=d, env=dict(os.environ), timeout=TIMEOUT)

    def check_deck(self, deck: str) -> None:
        scs = os.path.join(CADNIP, deck + ".scs")
        problem = dump_problem(deck)                                    # (1)
        if problem is not None:
            raise OracleUnusable(problem)
        with open(scs, encoding="utf-8") as fh:
            text = fh.read()
        body = text.split("\n", 1)[1] if "\n" in text else ""          # (2)
        r = chain(scs, "vacask")
        control = control_block(r.decks["vamos.sim"])
        prims = primitives_used(r.nl)
        od = os.path.join(self.tmp, "oracle")
        os.makedirs(od)
        with open(os.path.join(od, "body.scs"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        with open(os.path.join(od, "top.sim"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(oracle_deck(deck, prims, control))                 # (3)
        p = self.run_oracle(od)                                         # (4)
        self.assertEqual(p.returncode, 0, "the oracle on %s:\n%s" % (deck, p.stdout[-3000:]))
        res = run_engine(r, "vacask", os.path.join(self.tmp, "vamos"))  # (5)
        self.assertEqual(res.status, 0, "vamos -> VACASK on %s:\n%s" % (deck, res.stdout[-3000:]))
        self.assertTrue(r.steps, deck)
        oracle: Dict[str, List[rawfile.Raw]] = {}
        for st in r.steps:
            path = os.path.join(od, st.id + ".raw")
            self.assertTrue(os.path.isfile(path), "the oracle wrote no %s.raw:\n%s" % (st.id, p.stdout[-3000:]))
            self.assertIn(st.id, res.plots, "vamos -> VACASK wrote no %s.raw" % st.id)
            oracle[st.id] = rawfile.read_all(path)
            diff = raw_differences(res.plots[st.id], oracle[st.id], REL)
            self.assertEqual(diff, [], "%s %s: vamos -> VACASK against the Cadnip oracle" % (deck, st.id))
        self.check_pinned(deck, r, oracle)

    def check_pinned(self, deck: str, r, oracle: Dict[str, List[rawfile.Raw]]) -> None:
        """The oracle's operating point against expect.json (phase 0's run of the same oracle)."""
        ops = [st for st in r.steps if st.kind == "dc"]
        self.assertEqual(len(ops), 1, "%s: expect.json holds one operating point; the plan has %s"
                         % (deck, [(s.id, s.kind) for s in r.steps]))
        plot = oracle[ops[0].id][0]
        for v in load_expect()[deck]["values"]:
            got = plot.points[0][plot.exact(v["signal"])]
            self.assertTrue(abs(got - v["value"]) <= v.get("abs", 0.0) + v["rel"] * abs(v["value"]),
                            "%s %s: the oracle gives %r, expect.json %r" % (deck, v["signal"], got, v["value"]))

    @needs_cadnip
    @needs_vacask
    @needs_vacask_src
    def test_harness_smoke_vacask_spectre_rc(self):
        """VACASK's own test/spectre_rc.scs (its dialect, never a vamos input) through the oracle's
        environment: rc 0 and the RC charge of its pulse, 5 (1 - exp(-(t - 1m) / 2m)) at t = 5 ms."""
        src = os.path.join(vacask_src(), "test")
        if not os.path.isfile(os.path.join(src, "spectre_rc.scs")):
            self.skipTest("%s has no spectre_rc.scs" % src)
        d = os.path.join(self.tmp, "smoke")
        os.makedirs(d)
        shutil.copyfile(os.path.join(src, "spectre_rc.scs"), os.path.join(d, "spectre_rc.scs"))
        with open(os.path.join(d, "top.sim"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write('cadnip harness smoke\nground 0\ninclude "spectre_rc.scs" lang=spectre\ncontrol\n'
                     '  options rawfile="binary"\n  analysis tran1 tran stop=5m step=2u icmode="op"\nendc\n')
        p = self.run_oracle(d)
        self.assertEqual(p.returncode, 0, p.stdout[-3000:])
        plot = rawfile.read(os.path.join(d, "tran1.raw"))
        got = plot.points[-1][plot.exact("2")]
        want = 5 * (1 - 2.718281828459045 ** -2.0)
        self.assertTrue(abs(got - want) <= 0.015 * want, (got, want))


def _deck_test(deck: str):
    @needs_cadnip
    @needs_vacask
    def test(self):
        self.check_deck(deck)
    test.__name__ = "test_" + deck
    test.__doc__ = "cadnip/%s.scs: the Cadnip oracle against vamos -> VACASK (§11.3 (b))" % deck
    return test


for _deck in DECKS:
    setattr(TestCadnipOracle, "test_" + _deck, _deck_test(_deck))


if __name__ == "__main__":
    unittest.main()
