"""T3c, the CMC benchmark decks (docs/VAMOS_SPECTRE_DESIGN.md §11.3 (c); §3.13, §4.5 item 13; §13 E91,
E99, E100, E108, E109; phase 2).

    cd tests/vamos && VAMOS_CMC=1 VAMOS_CMC_EXAMPLES=/usr/local/src/xyce/utils/ADMS/examples \\
        PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_cmc

The 48 decks <examples>/<tree>/<bench>/*.sp (BSIM6.1.1 9, bsimcmg_107.0.0 13, BSIMCMG108.0.0_20140822 13,
BSIMCMG110.0.0_20160101 13) are HSPICE decks with HSPICE G-2012.06 listings at 25 °C [E91]: spectre-spice
parse fixtures (§3.13), read in place and unmodified, never compared with a .lis (vamos runs at Spectre's
27 °C), not vcs-ams gold.  fixtures/spectre/cmc/<tree>__<stem>.json records, deck by deck, what vamos does
with it today: measured, and rewritten from the measurement with VAMOS_REGEN_GOLDEN=1 (each class rewrites
the fields it measures, on the leg it runs on: TestCmcParse on either leg, TestCmcXyce and TestCmcVacask on
WSL; the per-deck VACASK tests are generated from the records, so a new deck takes a second regen run).  A
record's "design" is what §11.3 (c) expects of the deck and "differs" where today's behaviour departs from
it; Xyce's messages are recorded once each (it repeats them per subckt instance), with their count.

TestCmcRecords (both legs; no gate)   every record has its fields; the record set and the design's counts
TestCmcCorpus (needs_cmc)   the tree holds exactly the recorded decks, with §11.3's features: .hdl in 48,
    .alter in 18, .noise in 3, and 26 decks in the two trees whose cards name `bsimcmg` while the .hdl tree
    declares bsimcmg_108 or bsimcmg_110 [E91, E100]
TestCmcParse (needs_cmc; both legs, no engine)   (1): spectre.parse of the deck as a SPICE-mode top file
    with -I <deck dir>/../code, then for each engine plan.build -> signals.resolve -> render (the steps of
    test_spectre_chain.chain, with the search path; the Xyce alters replayed as it does): the deck renders,
    or the parse or the render fails with exactly the record's error notes, each naming one of the record's
    constructs (`names`)
TestCmcXyce (needs_cmc, needs_pyms; WSL)   (2): every deck is an expected refusal on Xyce in v1 (§4.5 item
    13): refused by the parse or the render, as (1) records, or, for a deck that renders, by the -norun
    check of its first deck: engines.xyce_bin() -norun under engines.env_for, xyce.output_problems and
    xyce.device_summary_problems applied to the output (E99, E108, E109), its exit status and problems
    recorded.  A deck that the Y form refuses only because spice.va_modules did not read its module's
    header attributes (every CMC tree writes them after the port list, `module m(d, g, s, b) (* ... *);`,
    where §4.5 item 13 reads them for every CMC model [E64]) also gets that -norun check on the
    letter-device route, with the attributes read from the module header here (`letter_route`, a
    stand-in for that reading; since phase 2's integration spice.va_modules(spectre=True) reads them, so
    the decks render on the letter-device route and the stand-in is unused)
TestCmcVacask (needs_cmc, needs_openvaf; WSL)   (3): openvaf-r must reject every CMC Verilog-A tree the
    decks load [E91] (run as vcs-ams's deck compile runs it; its exit status and error count recorded in
    every record); while it does, the VACASK run of each deck that renders is a reported skip (the default
    engine's production-model coverage is e2e 29, PSP103)
"""

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from typing import Dict, List, Optional, Tuple

from vamos_testlib import TempDir
from spectre_testlib import (cmc_examples, have_cmc, have_vacask, needs_cmc, needs_pyms,  # noqa: E402
                             spectre_fixture)
from test_spectre_chain import replay  # noqa: E402  (the Xyce alter replay of the phase-1b chain)

from vamos.ams import engines  # noqa: E402
from vamos.netlist import plan as PL, signals as SG, spectre, spice, vacask, xyce  # noqa: E402
from vamos.notes import ERROR, NoteError  # noqa: E402
from vamos.spectre.job import Settings  # noqa: E402

CMC = spectre_fixture("cmc")
REGEN = bool(os.environ.get("VAMOS_REGEN_GOLDEN"))
ROOT_TOKEN = "$VAMOS_CMC_EXAMPLES"
ENGINES = ("vacask", "xyce")
TIMEOUT = 1800
needs_openvaf = unittest.skipUnless(have_vacask() and engines.openvaf(), "needs openvaf-r (Linux/WSL)")

# §11.3 (c)'s corpus [E91, E100]
DECK_COUNT, ALTER_COUNT, NOISE_COUNT = 48, 18, 3
CARD_TREES = {"BSIMCMG108.0.0_20140822": "bsimcmg_108", "BSIMCMG110.0.0_20160101": "bsimcmg_110"}
CARD_TYPE = "bsimcmg"

# The constructs today's errors name: (name, the substring of the error that names it).  Every error must
# name one; a new kind of error fails TestCmcParse until it is added here.
KNOWN_CONSTRUCTS = (
    (".alter", ".alter is not supported"),
    (".noise", ".noise is an error in v1"),
    ("noise=", "noise= is neither a SPICE R parameter nor a Spectre resistor instance parameter"),
    # the card type alone; since phase 2's integration the spectre-spice error goes on to name the module
    # the .hdl tree declares ("...; the .hdl files declare bsimcmg_108"), which is §11.3 (c)'s construct
    ("bsimcmg", "model type BSIMCMG is not supported ("),
    ("bsimcmg_108", "bsimcmg_108"),
    ("bsimcmg_110", "bsimcmg_110"),
    ("E gain", "vcvs needs gain="),
    ("-1.3.0", "cannot read -1.3.0"),
    ("dc step", "never reaches stop"),
    ("Verilog-A parameters", "Verilog-A parameters ("),
)


# -- the decks and their records ---------------------------------------------------------------------------

def deck_id(rel: str) -> str:
    """<tree>__<stem>: 40 of the 48 decks share their stem with a deck of another tree."""
    parts = rel.replace(os.sep, "/").split("/")
    return "%s__%s" % (parts[0], os.path.splitext(parts[-1])[0])


_TREE: Dict[str, Dict[str, str]] = {}


def tree_decks() -> Dict[str, str]:
    """id -> the deck's path relative to VAMOS_CMC_EXAMPLES, from the tree ({} without the gate)."""
    if not have_cmc():
        return {}
    root = os.path.abspath(cmc_examples())
    if root not in _TREE:
        out = {}
        for p in sorted(glob.glob(os.path.join(root, "*", "*", "*.sp"))):
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            out[deck_id(rel)] = rel
        _TREE[root] = out
    return dict(_TREE[root])


def record_path(did: str) -> str:
    return os.path.join(CMC, did + ".json")


def recorded_ids() -> List[str]:
    if not os.path.isdir(CMC):
        return []
    return sorted(os.path.splitext(f)[0] for f in os.listdir(CMC) if f.endswith(".json"))


class NoRecord(AssertionError):
    pass


def load_record(did: str) -> dict:
    if not os.path.isfile(record_path(did)):
        raise NoRecord("no record %s (VAMOS_REGEN_GOLDEN=1 writes it from a measurement)" % record_path(did))
    with open(record_path(did), encoding="utf-8") as fh:
        return json.load(fh)


def write_record(did: str, rec: dict) -> None:
    os.makedirs(CMC, exist_ok=True)
    with open(record_path(did), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rec, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


def save_record(did: str, fields: dict) -> None:
    """VAMOS_REGEN_GOLDEN=1: merge the measured fields into the record (dicts one level deep)."""
    rec = load_record(did) if os.path.isfile(record_path(did)) else {}
    for k, v in fields.items():
        if isinstance(v, dict) and isinstance(rec.get(k), dict):
            rec[k].update(v)
        else:
            rec[k] = v
    write_record(did, rec)


ALL_IDS = sorted(set(recorded_ids()) | set(tree_decks()))


def deck_rel(did: str) -> str:
    rel = tree_decks().get(did)
    if rel is None:
        rel = load_record(did)["deck"]
    return rel


def deck_abs(rel: str) -> str:
    return os.path.join(os.path.abspath(cmc_examples()), rel)


def code_dir(rel: str) -> str:
    return os.path.normpath(os.path.join(os.path.dirname(deck_abs(rel)), "..", "code"))


def norm(text: str) -> str:
    """A message with the examples root spelled $VAMOS_CMC_EXAMPLES, whatever the leg's path to it."""
    root = os.path.abspath(cmc_examples())
    for spelling in sorted({os.path.realpath(root), root}, key=len, reverse=True):
        text = text.replace(spelling, ROOT_TOKEN)
    return text


def errors_of(exc: NoteError) -> List[str]:
    return [norm(n.message) for n in exc.notes if n.severity == ERROR]


def names_in(message: str) -> List[str]:
    """The constructs one error names."""
    return [name for name, sub in KNOWN_CONSTRUCTS if sub in message]


def construct_names(errors: List[str]) -> List[str]:
    return [name for name, sub in KNOWN_CONSTRUCTS if any(sub in e for e in errors)]


def unexplained(errors: List[str]) -> List[str]:
    return [e for e in errors if not names_in(e)]


# -- §11.3 (c)'s expectation of a deck ------------------------------------------------------------------------

_HDL = re.compile(r'^\s*\.hdl\s+"([^"]+)"', re.I | re.M)


def deck_text(rel: str) -> str:
    with open(deck_abs(rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def hdl_files(rel: str) -> List[str]:
    """The deck's .hdl files, resolved as an include is (the deck's directory, then -I ../code), relative to
    the examples root."""
    out = []
    root = os.path.abspath(cmc_examples())
    for name in _HDL.findall(deck_text(rel)):
        for d in (os.path.dirname(deck_abs(rel)), code_dir(rel)):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                out.append(os.path.relpath(p, root).replace(os.sep, "/"))
                break
    return out


def design_of(rel: str) -> dict:
    """§11.3 (c) (1): the constructs the deck's failure must name, [] when it must parse and render for both
    engines: .alter (18 decks), .noise (3) [E91], and in the BSIMCMG108/110 trees the module the .hdl tree
    declares, which their `bsimcmg` cards do not name [E100]."""
    text = deck_text(rel)
    names = []
    if re.search(r"^\s*\.alter\b", text, re.I | re.M):
        names.append(".alter")
    if re.search(r"^\s*\.noise\b", text, re.I | re.M):
        names.append(".noise")
    tree = rel.split("/")[0]
    if tree in CARD_TREES:
        for h in hdl_files(rel):
            path = os.path.join(os.path.abspath(cmc_examples()), h)
            mods, _files = spice.va_modules(path, [os.path.dirname(path)])
            names += sorted(m.name for m in mods.values())
    return {"names": names}


def differences(design: dict, rec: dict) -> List[str]:
    """Where today's record departs from §11.3 (c)'s expectation, by construct name (kept in the record
    for the reader; the test holds it to the measurement)."""
    named: List[Tuple[str, str]] = [("parse", n) for m in rec.get("errors", []) for n in names_in(m)]
    for engine in ENGINES:
        e = rec.get(engine, {})
        if e.get("render") == "error":
            named += [("%s render" % engine, n) for m in e.get("errors", []) for n in names_in(m)]
    want = design["names"]
    out = ["§11.3 (c) expects an error naming %s; none does" % name for name in want
           if not any(n == name for _stage, n in named)]
    seen = set()
    for stage, n in named:
        if n not in want and (stage, n) not in seen:
            seen.add((stage, n))
            out.append("%s fails on %s, which §11.3 (c) does not list for this deck" % (stage, n))
    return out


# -- (1) the chain ---------------------------------------------------------------------------------------

def parse_deck(rel: str):
    """(Netlist, []) or (None, errors): the deck as a SPICE-mode top file with -I <deck dir>/../code."""
    path = deck_abs(rel)
    try:
        return spectre.parse(path, os.path.dirname(path), spectre.SpectreParseOpts(search=[code_dir(rel)])), []
    except NoteError as exc:
        return None, errors_of(exc)


def render_engine(nl, engine: str):
    """plan.build -> signals.resolve -> render for one engine, as test_spectre_chain.chain does: (the
    render copy, the steps, {file: deck}); NoteError propagates."""
    plan = PL.build(nl, engine, Settings())
    nl2, sigmap, _notes = SG.resolve(nl, plan)
    steps = [a.step for a in plan.actions if a.op == "analysis" and a.step is not None]
    decks = {}
    if engine == "vacask":
        decks["vamos.sim"] = vacask.render(nl2, plan=plan, sigmap=sigmap, names={})
    else:
        state = nl2
        for act in plan.actions:
            if act.op == "alter":
                state = replay(state, act)
            elif act.op == "analysis" and act.step is not None:
                decks[act.step.id + ".cir"] = xyce.render(state, step=act.step, sigmap=sigmap, names={})
    return nl2, steps, decks


def measure_chain(rel: str) -> dict:
    """The fields TestCmcParse checks: parse, errors, names, per engine the render's outcome, and the stage
    that refuses the deck on Xyce (parse, render, or the -norun check of a deck that renders)."""
    nl, errors = parse_deck(rel)
    out = {"parse": "ok" if nl is not None else "error", "errors": errors, "names": construct_names(errors)}
    for engine in ENGINES:
        if nl is None:
            out[engine] = {"render": "no parse"}
            continue
        nl_e, _ = parse_deck(rel)                   # a fresh IR per engine, as chain() parses per engine
        try:
            _nl2, steps, _decks = render_engine(nl_e, engine)
        except NoteError as exc:
            errs = errors_of(exc)
            out[engine] = {"render": "error", "errors": errs, "names": construct_names(errs)}
            continue
        out[engine] = {"render": "ok", "steps": [[s.id, s.name, s.kind] for s in steps]}
    out["xyce"]["refused"] = ("parse" if nl is None else
                              "render" if out["xyce"]["render"] == "error" else "norun")
    return out


CHAIN_FIELDS = ("render", "steps", "errors", "names", "refused")       # an engine's fields TestCmcParse owns


# -- (2) the Xyce -norun check ---------------------------------------------------------------------------

_HEADER = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\([^)]*\)\s*(?:\(\*(.*?)\*\)|`attr\((.*?)\)\s*;)", re.S)
_ATTR = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\"([^\"]*)\"")
XYCE_ATTRS = ("xyceModelGroup", "xyceLevelNumber", "xyceTypeVariable", "xycePTypeValue")


def header_attrs(path: str, module: str) -> Dict[str, str]:
    """The xyce* attributes of `module m(ports) (* ... *);` (or its `attr(...) macro form, which the
    bsimcmg trees define as (*txt*)) in path or the .va files beside it; {} when there are none."""
    d = os.path.dirname(path)
    for f in [path] + sorted(glob.glob(os.path.join(d, "*.va"))):
        with open(f, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for m in _HEADER.finditer(text):
            if m.group(1).lower() == module.lower():
                attrs = dict(_ATTR.findall(m.group(2) or m.group(3) or ""))
                return {k: v for k, v in attrs.items() if k in XYCE_ATTRS}
    return {}


class _XyceRuns(TempDir):
    """PyMS's caches and a TMPDIR shared by the class's runs, so each CMC module compiles once, and kept
    apart from every other run: PYMS_CACHE (the .hdl compile) and PYMS_VAE_CACHE (the evaluator libraries
    PyMS builds at device setup, /tmp/pyms_vae_cache by default, xyce/utils/PyMS/vae/xyce_device_gen.py)."""

    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.mkdtemp(prefix="vamos-cmc-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.shared, ignore_errors=True)

    def norun(self, nl2, decks: Dict[str, str], first: str, d: str) -> dict:
        """Xyce -norun on the first deck under env_for (§7.2); the exit status and the two checks."""
        os.makedirs(d, exist_ok=True)
        for fn, text in decks.items():
            with open(os.path.join(d, fn), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        env = engines.env_for("xyce", None, dict(os.environ))
        for var, sub in (("PYMS_CACHE", "pyms"), ("PYMS_VAE_CACHE", "pyms_vae"), ("TMPDIR", "tmp")):
            env[var] = os.path.join(self.shared, sub)
            os.makedirs(env[var], exist_ok=True)
        p = subprocess.run([engines.xyce_bin(), "-norun", first], cwd=d, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, errors="replace", timeout=TIMEOUT)
        with open(os.path.join(d, first + ".norun.log"), "w", encoding="utf-8") as fh:
            fh.write(p.stdout)
        out = [norm(n.message) for n in xyce.output_problems(p.stdout, nl2)]
        summary = [norm(n.message) for n in xyce.device_summary_problems(p.stdout, nl2)]
        # Xyce repeats a message per subckt instance (17 stages of a ring oscillator): the distinct ones
        return {"rc": p.returncode, "output_problems": sorted(set(out)), "output_problem_count": len(out),
                "device_summary_problems": sorted(set(summary)), "_out": p.stdout}


def refused(norun: dict) -> bool:
    return norun["rc"] != 0 or bool(norun["output_problems"]) or bool(norun["device_summary_problems"])


# -- (3) openvaf-r -----------------------------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_COULD_NOT = re.compile(r"could not compile `[^`]*` due to (\d+) previous errors?")


def openvaf_result(path: str, d: str) -> dict:
    """openvaf-r on a Verilog-A file, as vcs-ams's deck compile runs it (cwd: the file's directory)."""
    osdi = os.path.join(d, os.path.splitext(os.path.basename(path))[0] + ".osdi")
    p = subprocess.run([engines.openvaf(), path, "-o", osdi], cwd=os.path.dirname(path), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True, errors="replace", timeout=TIMEOUT)
    text = _ANSI.sub("", p.stdout)
    m = _COULD_NOT.search(text)
    return {"rc": p.returncode, "errors": int(m.group(1)) if m else None, "osdi": os.path.isfile(osdi),
            "_out": text}


# -- the tests --------------------------------------------------------------------------------------------

class TestCmcRecords(unittest.TestCase):
    """Both legs, no gate: the records' shape and the design's counts over them."""

    def test_every_record_has_its_fields(self):
        ids = recorded_ids()
        self.assertEqual(len(ids), DECK_COUNT, ids)
        for did in ids:
            with self.subTest(deck=did):
                rec = load_record(did)
                self.assertEqual(deck_id(rec["deck"]), did)
                for key in ("deck", "include", "hdl", "design", "parse", "errors", "names", "vacask", "xyce",
                            "differs"):
                    self.assertIn(key, rec)
                self.assertEqual(rec["include"], ["../code"])
                self.assertIn(rec["parse"], ("ok", "error"))
                self.assertEqual(rec["parse"] == "ok", not rec["errors"])
                self.assertEqual(rec["xyce"]["refused"], "parse" if rec["parse"] != "ok" else
                                 "render" if rec["xyce"]["render"] == "error" else "norun")
                if rec["xyce"]["refused"] == "norun":
                    self.assertIn("norun", rec["xyce"], did)
                self.assertEqual(sorted(rec["vacask"].get("openvaf", {})), sorted(rec["hdl"]), did)

    def test_the_design_counts(self):
        """§11.3 (c): .alter 18, .noise 3, the 26 BSIMCMG108/110 decks naming the module the tree declares;
        every deck an expected refusal on Xyce."""
        recs = [load_record(did) for did in recorded_ids()]
        names = [r["design"]["names"] for r in recs]
        self.assertEqual(sum(".alter" in n for n in names), ALTER_COUNT)
        self.assertEqual(sum(".noise" in n for n in names), NOISE_COUNT)
        for tree, module in CARD_TREES.items():
            self.assertEqual(sum(1 for r in recs if r["deck"].startswith(tree + "/")
                                 and module in r["design"]["names"]), 13, tree)
        self.assertEqual(sum(1 for r in recs if r["xyce"]["refused"] in ("parse", "render", "norun")), DECK_COUNT)


@needs_cmc
class TestCmcCorpus(unittest.TestCase):
    """The tree holds the recorded decks, with §11.3's features [E91, E100]."""

    def test_the_tree_holds_the_recorded_decks(self):
        self.assertEqual(sorted(tree_decks()), recorded_ids())
        self.assertEqual(len(tree_decks()), DECK_COUNT)

    def test_the_features(self):
        decks = tree_decks()
        texts = {did: deck_text(rel) for did, rel in decks.items()}
        self.assertEqual(sum(bool(_HDL.search(t)) for t in texts.values()), DECK_COUNT)
        self.assertEqual(sum(bool(re.search(r"^\s*\.alter\b", t, re.I | re.M)) for t in texts.values()),
                         ALTER_COUNT)
        self.assertEqual(sum(bool(re.search(r"^\s*\.noise\b", t, re.I | re.M)) for t in texts.values()),
                         NOISE_COUNT)
        root = os.path.abspath(cmc_examples())
        for tree, module in CARD_TREES.items():
            ids = [did for did, rel in decks.items() if rel.startswith(tree + "/")]
            self.assertEqual(len(ids), 13, tree)
            for did in ids:
                hdl = hdl_files(decks[did])
                self.assertEqual(hdl, ["%s/code/bsimcmg.va" % tree], did)
                path = os.path.join(root, hdl[0])
                mods, _files = spice.va_modules(path, [os.path.dirname(path)])
                self.assertEqual(sorted(mods), [module], did)           # bsimcmg.va -> bsimcmg_main.va [E100]
            types = set()
            for p in glob.glob(os.path.join(root, tree, "*", "modelcard.*")):
                with open(p, encoding="utf-8", errors="replace") as fh:
                    types |= {t.lower() for t in re.findall(r"^\s*\.model\s+\S+\s+(\w+)", fh.read(), re.I | re.M)}
            self.assertEqual(types, {CARD_TYPE}, tree)                  # the cards name bsimcmg [E100]


class TestCmcParse(unittest.TestCase):
    """(1): parse as a SPICE-mode top file with -I ../code and render for both engines, or the record's
    errors, each naming one of the record's constructs."""

    def check_deck(self, did: str) -> None:
        rel = deck_rel(did)
        got = measure_chain(rel)
        got.update(deck=rel, include=["../code"], hdl=hdl_files(rel), design=design_of(rel))
        got["differs"] = differences(got["design"], got)
        bad = unexplained(got["errors"]) + [m for e in ENGINES for m in unexplained(got[e].get("errors", []))]
        self.assertEqual(bad, [], "%s: errors that name no construct of KNOWN_CONSTRUCTS" % did)
        if REGEN:
            old = load_record(did) if os.path.isfile(record_path(did)) else {}
            new = dict(got)
            for engine in ENGINES:              # this class's fields replaced, the run classes' fields kept
                kept = {k: v for k, v in old.get(engine, {}).items() if k not in CHAIN_FIELDS}
                kept.update(got[engine])
                new[engine] = kept
            old.update(new)
            write_record(did, old)
        rec = load_record(did)
        for key in ("deck", "include", "hdl", "design", "parse", "names", "differs"):
            self.assertEqual(got[key], rec[key], "%s %s" % (did, key))
        self.assertEqual(sorted(got["errors"]), sorted(rec["errors"]), "%s: the parse's errors" % did)
        for engine in ENGINES:
            want = {k: v for k, v in rec[engine].items() if k in CHAIN_FIELDS}
            have = dict(got[engine])
            for d in (want, have):
                if "errors" in d:
                    d["errors"] = sorted(d["errors"])
            self.assertEqual(have, want, "%s on %s" % (did, engine))


class TestCmcXyce(_XyceRuns):
    """(2): every deck an expected refusal on Xyce in v1 (§4.5 item 13; E99, E108, E109)."""

    def check_deck(self, did: str) -> None:
        rel = deck_rel(did)
        chain_rec = measure_chain(rel)
        stage = chain_rec["xyce"]["refused"]
        got: Dict[str, object] = {}
        if stage == "norun":
            nl, _ = parse_deck(rel)
            nl2, steps, decks = render_engine(nl, "xyce")
            res = self.norun(nl2, decks, steps[0].id + ".cir", os.path.join(self.tmp, "norun"))
            self.assertTrue(refused(res), "%s: Xyce accepted the deck under -norun:\n%s" % (did, res["_out"][-3000:]))
            got["norun"] = {k: v for k, v in res.items() if not k.startswith("_")}
        letter = self.letter_route(did, rel, chain_rec)
        if letter is not None:
            got["letter_route"] = letter
        if REGEN:                                   # this class's fields replaced, the rest kept
            rec = load_record(did)
            rec["xyce"].pop("norun", None)
            rec["xyce"].pop("letter_route", None)
            rec["xyce"].update(got)
            write_record(did, rec)
        x = load_record(did)["xyce"]
        self.assertEqual(stage, x["refused"], did)
        self.assertEqual(got.get("norun"), x.get("norun"), did)
        self.assertEqual(got.get("letter_route"), x.get("letter_route"), did)

    def letter_route(self, did: str, rel: str, chain_rec: dict) -> Optional[dict]:
        """The -norun check on the letter-device route for a deck the Y form refuses because the module's
        header attributes went unread (the module docstring); None when that is not the case."""
        if chain_rec["xyce"]["refused"] != "render":
            return None
        if not any("Verilog-A parameters (" in m for m in chain_rec["xyce"]["errors"]):
            return None
        nl, _ = parse_deck(rel)
        filled = {}
        for vm in nl.va_modules.values():
            if any(k in vm.attrs for k in XYCE_ATTRS):
                continue                                  # va_modules read them: the regular route applies
            attrs = header_attrs(vm.path, vm.name)
            if attrs:
                vm.attrs.update(attrs)
                filled[vm.name] = attrs
        if not filled:
            return None
        try:
            nl2, steps, decks = render_engine(nl, "xyce")
        except NoteError as exc:
            return {"attrs": filled, "render": errors_of(exc)}
        res = self.norun(nl2, decks, steps[0].id + ".cir", os.path.join(self.tmp, "letter"))
        self.assertTrue(refused(res), "%s: on the letter-device route Xyce accepted the deck under -norun:\n%s"
                        % (did, res["_out"][-3000:]))
        out = {k: v for k, v in res.items() if not k.startswith("_")}
        out["attrs"] = filled
        return out


class TestCmcVacask(TempDir):
    """(3): openvaf-r rejects the CMC Verilog-A [E91]; the VACASK runs are a reported skip while it does."""

    results: Dict[str, dict] = {}

    def compiled(self, h: str) -> dict:
        if h not in self.results:
            d = tempfile.mkdtemp(prefix="vamos-cmc-ovaf-")
            try:
                self.results[h] = openvaf_result(os.path.join(os.path.abspath(cmc_examples()), h), d)
            finally:
                shutil.rmtree(d, ignore_errors=True)
        return self.results[h]

    def assert_rejected(self, h: str, what: str) -> dict:
        res = self.compiled(h)
        self.assertTrue(res["rc"] != 0 and not res["osdi"],
                        "openvaf-r compiles %s now (E91 changed): %s is no longer a skip (§11.3 (c) 3)\n%s"
                        % (h, what, res["_out"][-2000:]))
        return {"rc": res["rc"], "errors": res["errors"]}

    def openvaf_of(self, did: str) -> dict:
        """The deck's Verilog-A files through openvaf-r, each asserted rejected; the record's form."""
        return {h: self.assert_rejected(h, "the VACASK run of " + did) for h in load_record(did)["hdl"]}

    @needs_cmc
    @needs_openvaf
    def test_openvaf_rejects_every_cmc_tree(self):
        """E91 verified and recorded for every deck's Verilog-A tree, whether or not the deck renders."""
        ids = recorded_ids()
        files = sorted({h for did in ids for h in load_record(did)["hdl"]})
        self.assertEqual(len(files), 4, files)
        for did in ids:
            with self.subTest(deck=did):
                out = self.openvaf_of(did)
                if REGEN:
                    save_record(did, {"vacask": {"openvaf": out}})
                self.assertEqual(out, load_record(did)["vacask"].get("openvaf"), did)

    def check_deck(self, did: str) -> None:
        rel = deck_rel(did)
        self.assertEqual(measure_chain(rel)["vacask"]["render"], "ok", "%s: the record says it renders on VACASK"
                         % did)
        out = self.openvaf_of(did)
        self.assertEqual(out, load_record(did)["vacask"].get("openvaf"), did)
        self.skipTest("the VACASK run of %s: openvaf-r rejects %s (§11.3 (c) 3, E91)"
                      % (did, ", ".join("%s (exit %d, %s errors)" % (h, r["rc"], r["errors"]) for h, r in out.items())))


def _test(check, did: str, doc: str, gates):
    def test(self):
        check(self, did)
    for g in reversed(gates):
        test = g(test)
    test.__name__ = "test_" + re.sub(r"\W", "_", did)
    test.__doc__ = doc % did
    return test


def _vacask_renders(did: str) -> bool:
    try:
        return load_record(did)["vacask"].get("render") == "ok"
    except (NoRecord, ValueError, KeyError):
        return False


for _did in ALL_IDS:
    _name = "test_" + re.sub(r"\W", "_", _did)
    setattr(TestCmcParse, _name, _test(TestCmcParse.check_deck, _did,
                                       "%s: parses and renders for both engines, or fails naming the construct "
                                       "(§11.3 (c) 1)", [needs_cmc]))
    setattr(TestCmcXyce, _name, _test(TestCmcXyce.check_deck, _did,
                                      "%s: an expected refusal on Xyce in v1 (§11.3 (c) 2)", [needs_cmc, needs_pyms]))
    if _vacask_renders(_did):
        setattr(TestCmcVacask, _name, _test(TestCmcVacask.check_deck, _did,
                                            "%s: the VACASK run, a reported skip while openvaf-r rejects the "
                                            "module (§11.3 (c) 3)", [needs_cmc, needs_openvaf]))


if __name__ == "__main__":
    unittest.main()
