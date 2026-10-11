"""psfcheck's own tests (docs/VAMOS_SPECTRE_DESIGN.md §11 T0 "psfcheck"; both legs, no engine).

psfcheck must accept every real Spectre sample under fixtures/spectre/psf/real and reject each file
of fixtures/spectre/psf/bad; the rules it applies are exercised one by one on a small valid
transient file (the template below, which psf/bad's files are mutations of), and the logFile tree
rules on a copy of the 23.1 run.

    python3 -m unittest test_spectre_psfcheck
    python3 -c "import test_spectre_psfcheck as t; t.write_bad_files(t.BAD_DIR)"   # regenerate psf/bad
"""

import contextlib
import io
import math
import os
import re
import shutil
import unittest
from typing import Dict, Tuple

from vamos_testlib import TempDir, fixture

import psfcheck
from psfcheck import LogFile, PsfError, PsfFile, check, check_logfile, read

REAL_DIR = fixture("spectre", "psf", "real")
BAD_DIR = fixture("spectre", "psf", "bad")
ASCII_23 = os.path.join(REAL_DIR, "psf-parser", "tests", "data", "ascii")
LOG_23 = os.path.join(REAL_DIR, "psf-parser", "tests", "data", "binary", "logFile")
SAMPLES = os.path.join(REAL_DIR, "psf_utils", "samples")
PNOISE = os.path.join(SAMPLES, "pnoise.raw")
LOG_15 = os.path.join(PNOISE, "logFile")

# -- the template every psf/bad file is a mutation of -------------------------------------------------

TEMPLATE = '''HEADER
"PSFversion" "1.00"
"simulator" "spectre"
"version" "vamos (psf/bad fixture)"
"date" "1:02:03 PM, Sat Oct 4, 2026"
"design" "@DESIGN@"
"analysis type" "tran"
"analysis name" "t"
"analysis description" "Transient Analysis `t': time = (0 s -> 1 s)"
"xVecSorted" "ascending"
"tolerance.relative" 1.000000000000000e-03
"start" 0.000000000000000e+00
"outputstart" 0.000000000000000e+00
"stop" 1.000000000000000e+00
"step" 1.000000000000000e-03
"istep" 1.000000000000000e-03
"maxstep" 1.000000000000000e-02
"ic" "all"
"useprevic" "no"
"skipdc" "no"
"reltol" 1.000000000000000e-03
"abstol(V)" 1.000000000000000e-06
"abstol(I)" 1.000000000000000e-12
"temp" 2.700000000000000e+01
"tnom" 2.700000000000000e+01
"tempeffects" "all"
"errpreset" "moderate"
"method" "traponly"
"lteratio" 3.500000000000000e+00
"relref" "sigglobal"
"cmin" 0.000000000000000e+00
"gmin" 1.000000000000000e-12
TYPE
"sweep" FLOAT DOUBLE PROP(
"key" "sweep"
)
"V" FLOAT DOUBLE PROP(
"units" "V"
"key" "node"
"tolerance" 1.000000000000000e-06
)
SWEEP
"time" "sweep" PROP(
"sweep_direction" 0
"units" "s"
"plot" 0
"grid" 1
)
TRACE
"a" "V"
"b" "@BTYPE@"
VALUE
"time" @T0@
"a" 1.000000000000000e+00
"b" 2.000000000000000e+00
"time" @T1@
@P2@
END
'''
P2_GOOD = '"a" 3.000000000000000e+00\n"b" 4.000000000000000e+00'


def good_text(design: str = "psf/bad fixture", btype: str = "V", t0: str = "0.000000000000000e+00",
              t1: str = "1.000000000000000e+00", p2: str = P2_GOOD, end: bool = True) -> str:
    text = (TEMPLATE.replace("@DESIGN@", design).replace("@BTYPE@", btype).replace("@T0@", t0)
            .replace("@T1@", t1).replace("@P2@", p2))
    return text if end else text.replace("END\n", "")


# name -> (good_text keywords, the fragment psfcheck's message must carry); §11's list of malformations
BAD_FILES: Dict[str, Tuple[dict, str]] = {
    "ragged.tran": ({"p2": '"a" 3.000000000000000e+00'}, "point 2: expected trace 'b'"),
    "no_end.tran": ({"end": False}, "expected END"),
    "swapped_values.tran": ({"p2": '"b" 4.000000000000000e+00\n"a" 3.000000000000000e+00'},
                            "expected trace 'a', got 'b'"),
    "descending_axis.tran": ({"t1": "-1.000000000000000e+00"}, "ascending but the sweep decreases"),
    "int_for_float.tran": ({"t0": "0"}, "integer '0' where FLOAT is declared"),
    "unescaped_quote.tran": ({"design": 'a "quoted" title'}, "unexpected character 'q'"),
    "undeclared_type.tran": ({"btype": "I"}, "type 'I' is not declared in TYPE"),
    "bad_escape.tran": ({"design": "back" + chr(92) + "xslash"}, "escape " + chr(92) + "x is outside the rules"),
}


def bad_text(name: str) -> str:
    return good_text(**BAD_FILES[name][0])


def write_bad_files(directory: str) -> None:
    """Regenerate fixtures/spectre/psf/bad from the template (the README there is hand-written)."""
    for name in BAD_FILES:
        with open(os.path.join(directory, name), "w", newline="\n") as fh:
            fh.write(bad_text(name))


# -- helpers -------------------------------------------------------------------------------------------

def real_files():
    out = []
    for root, _dirs, files in os.walk(REAL_DIR):
        for f in files:
            if f not in ("PROVENANCE", "README"):
                out.append(os.path.join(root, f))
    return sorted(out)


def check_real(path: str):
    if os.path.basename(path) == "logFile":
        return check_logfile(path, datadir=ASCII_23 if path == LOG_23 else None)
    return check(path)


def read_text(path: str) -> str:
    with open(path, encoding="latin-1") as fh:
        return fh.read()


class _Scratch(TempDir):
    def accept(self, text: str, name: str = "t.tran", **kw):
        p = self.write(name, text)
        return check(p, **kw)

    def reject(self, text: str, fragment: str, name: str = "t.tran", **kw) -> str:
        p = self.write(name, text)
        with self.assertRaises(PsfError) as cm:
            check(p, **kw)
        msg = str(cm.exception)
        self.assertIn(fragment, msg)
        self.assertTrue(msg.startswith(p), msg)         # "<file>:<line>: <what>" or "<file>: <what>"
        return msg


# -- the real samples ----------------------------------------------------------------------------------

class TestRealSamples(unittest.TestCase):
    """psf/real: every native Spectre sample is accepted, and the facts the design rests on hold."""

    def test_every_real_sample_is_accepted(self):
        files = real_files()
        self.assertGreaterEqual(len(files), 49, files)
        for f in files:
            with self.subTest(file=os.path.relpath(f, REAL_DIR)):
                r = check_real(f)
                self.assertIsInstance(r, (PsfFile, LogFile))
        names = {os.path.relpath(f, REAL_DIR).replace(os.sep, "/") for f in files}
        for want in ("psf-parser/tests/data/ascii/myop.dc", "psf-parser/tests/data/ascii/mytran.tran.tran",
                     "psf-parser/tests/data/binary/logFile", "psf_utils/samples/pnoise.raw/noiva.noise",
                     "psf_utils/samples/pnoise.raw/noiref.noise", "psf_utils/samples/pnoise.raw/logFile",
                     "psf_utils/samples/pnoise.raw/aclin.ac", "psf_utils/samples/joop-banaan.dc",
                     "psf_utils/samples/joop-banaan.tran"):
            self.assertIn(want, names)
        self.assertEqual(len([n for n in names if n.startswith("psf-parser/tests/data/ascii/")]), 38)

    def test_provenance_names_every_file(self):
        prov = read_text(os.path.join(REAL_DIR, "PROVENANCE"))
        listed = set(re.findall(r"^\s*(\S+)\s", prov, re.M))
        for f in real_files():
            rel = os.path.relpath(f, REAL_DIR).replace(os.sep, "/")
            self.assertIn(rel, listed, "PROVENANCE does not list %s" % rel)
        for rel in listed:
            if "/" in rel:
                self.assertTrue(os.path.isfile(os.path.join(REAL_DIR, rel)), "PROVENANCE lists a missing %s" % rel)

    def test_rc_lowpass_run_facts(self):
        # the 23.1.0.242 run of "#RC Low-Pass Filter" (§0 [S], §13 E101)
        for f in sorted(os.listdir(ASCII_23)):
            self.assertEqual(read(os.path.join(ASCII_23, f)).header["design"], "#RC Low-Pass Filter", f)
        op = check(os.path.join(ASCII_23, "myop.dc"))
        self.assertEqual(op.kind, "dc")
        self.assertFalse(op.swept)
        self.assertEqual((op.value("in"), op.value("out")), (1.0, 1.0))
        self.assertEqual(op.value_types["in"], "V")
        self.assertEqual(op.names(), ["in", "out"])
        dc = check(os.path.join(ASCII_23, "mydc.dc"))
        self.assertEqual((dc.sweep.name, dc.sweep.props["units"], dc.points), ("dc", "V", 21))
        self.assertEqual(dc.column("in"), dc.sweep_values())
        self.assertAlmostEqual(dc.sweep_values()[1], 0.1)
        ac = check(os.path.join(ASCII_23, "myac.ac"))
        self.assertEqual((ac.sweep.props["grid"], ac.header["start"], ac.header["stop"]), (3, 1.0, 1e6))
        self.assertEqual(ac.types["V"].kind, "COMPLEX")
        out = ac.at("out", 1.0)
        self.assertAlmostEqual(out.real, 0.99996, places=5)
        self.assertAlmostEqual(out.imag, -0.00628, places=5)
        tr = check(os.path.join(ASCII_23, "mytran.tran.tran"))
        self.assertEqual((tr.points, tr.header["stop"], tr.header["step"], tr.header["maxstep"],
                          tr.header["errpreset"]), (6002, 0.5, 5e-4, 0.01, "moderate"))
        self.assertEqual(tr.at("in", 0.0), 1.0)
        self.assertAlmostEqual(tr.at("in", 8.3333e-5), 1.5, delta=2e-3)        # 1 + sin(2 pi 1 kHz t)
        par = check(os.path.join(ASCII_23, "mysweep_ac1.sweep"))
        self.assertEqual((par.kind, par.sweep.name, par.sweep.props["units"]), ("sweep", "R1:r", "Ohm"))
        self.assertEqual(par.sweep_values(), [1000.0, 2000.0, 3000.0])
        self.assertEqual(par.header["xVecSorted"], "unknown")
        nested = check(os.path.join(ASCII_23, "mysweep-000_mynestedsweep_ac2.sweep"))
        self.assertEqual((nested.sweep.name, nested.sweep.props["units"]), ("C1:c", "F"))
        self.assertEqual(nested.sweep_values(), [1e-6, 2e-6, 3e-6])
        mc = check(os.path.join(ASCII_23, "mymonte_dc2.montecarlo"))
        self.assertEqual((mc.kind, mc.sweep.name, mc.sweep.props["units"], mc.sweep_values()),
                         ("montecarlo", "iteration", "real", [1.0, 2.0, 3.0]))
        info = check(os.path.join(ASCII_23, "myinfo_Models.info"))
        self.assertEqual((info.kind, info.types, info.values, info.has_value_section), ("info", {}, {}, False))
        opp = check(os.path.join(ASCII_23, "myinfo_Oppoint.info"))
        self.assertEqual(opp.types["resistor"].kind, "STRUCT")
        self.assertEqual([m[0] for m in opp.types["resistor"].members], ["trise", "v", "i", "res", "pwr"])
        self.assertEqual(opp.value("Vin")["v"], 1.0)
        self.assertEqual(opp.value_props["Vin"], {"model": "vsource"})

    def test_noise_sample_structs(self):
        # 15.1 `noiva.noise` (§8.4 [S]): STRUCT types named after the model or master, `out` last
        n = check(os.path.join(PNOISE, "noiva.noise"))
        self.assertEqual(n.kind, "noise")
        self.assertEqual(n.names(), ["RESva", "Rref", "Rva", "RESref", "out"])
        self.assertEqual([t.type for t in n.traces], ["res_va", "resistor", "resistor", "rref", "V/sqrt(Hz)"])
        self.assertEqual([m[0] for m in n.types["res_va"].members], ["flicker", "thermal", "total"])
        self.assertEqual([m[0] for m in n.types["rref"].members], ["rn", "fn", "total"])
        self.assertEqual(n.types["rref"].props, {"key": "inst", "master": "resistor"})
        self.assertEqual(n.types["V/sqrt(Hz)"].props, {"units": "V/sqrt(Hz)", "key": "noise"})
        self.assertEqual(n.types["res_va"].members[0][2], {"units": "V^2/Hz"})
        first = {t: n.column(t)[0] for t in n.names()}
        self.assertEqual(first["RESva"], {"flicker": 2.5e-11, "thermal": 1.65757549356e-22,
                                          "total": 2.500000000016576e-11})
        total = sum(first[t]["total"] for t in ("RESva", "Rref", "Rva", "RESref"))
        self.assertAlmostEqual(first["out"], math.sqrt(total), delta=1e-20)
        self.assertEqual(first["out"], 5.000000000016576e-06)
        self.assertEqual(n.sweep.props["grid"], 3)
        self.assertEqual(n.header["output"], "pair of nodes")

    def test_logfile_23_1_tree(self):
        log = check_logfile(LOG_23, datadir=ASCII_23)
        self.assertEqual(len(log.entries), 38)
        parents = [e.key for e in log.entries if e.is_parent]
        self.assertEqual(parents, ["mysweep_dc1-sweep", "mysweep_ac1-sweep", "mysweep_ac2-sweep",
                                   "mysweep-000_mynestedsweep_ac2-sweep", "mysweep-001_mynestedsweep_ac2-sweep",
                                   "mysweep-002_mynestedsweep_ac2-sweep", "mymonte_dc2-montecarlo",
                                   "mymonte_tran1-montecarlo"])
        self.assertEqual({k: len(v) for k, v in log.children.items()}, {k: 3 for k in parents})
        leaf = log.by_key["mysweep-000_ac1-ac"]
        self.assertEqual((leaf.parent, leaf.data_file, leaf.sweep_variable), ("mysweep_ac1-sweep", "mysweep-000_ac1.ac", ["freq"]))
        self.assertEqual(list(leaf.props.items()), [("data_type", "swept_scalar"), ("sweep_tree_type", "leafNode"),
                                                    ("R1:r", 1000.0)])
        nested = log.by_key["mysweep-001_mynestedsweep_ac2-sweep"]
        self.assertEqual((nested.parent, list(nested.props.items())),
                         ("mysweep_ac2-sweep", [("sweep_tree_type", "sweepNode"), ("R1:r", 2000.0)]))
        self.assertEqual(log.by_key["mysweep-001_mynestedsweep-002_ac2-ac"].props["C1:c"], 3e-06)
        self.assertEqual(log.by_key["mymonte_dc2-montecarlo"].props, {})
        self.assertEqual(log.by_key["mymonte-002_dc2-dc"].props,
                         {"data_type": "scalar", "sweep_tree_type": "leafNode", "iteration": 2.0})
        self.assertEqual(log.by_key["myop-dc"].props, {"data_type": "scalar"})
        self.assertEqual(log.by_key["mytran-tran"].data_file, "mytran.tran.tran")
        self.assertEqual(log.by_key["myinfo_all-info"].props, {"data_type": "struct"})
        self.assertEqual(log.header["psfversion"], "1.4.0")
        self.assertEqual(log.keys()[:4], ["myinfo_Oppoint-info", "myinfo_Models-info", "myinfo_all-info", "myop-dc"])
        # `check` dispatches a logFile by its header, not by its name
        self.assertIsInstance(check(LOG_23, datadir=ASCII_23), LogFile)

    def test_logfile_15_1(self):
        log = check_logfile(LOG_15)
        self.assertEqual(log.keys(), ["noiva-noise", "noiref-noise", "pss-td.pss", "pnoiva-pnoise",
                                      "pnoiref-pnoise", "aclin-ac", "aclog-ac"])
        self.assertEqual([e.is_parent for e in log.entries], [False] * 7)
        self.assertNotIn("psfversion", log.header)
        self.assertEqual(log.by_key["pss-td.pss"].props, {"data_type": "swept_scalar"})

    def test_header_facts(self):
        # the §8.2 date layout and §8.3's per-kind header keys hold in every native sample
        for f in real_files():
            if os.path.basename(f) == "logFile":
                continue
            psf = read(f)
            with self.subTest(file=os.path.relpath(f, REAL_DIR)):
                self.assertTrue(psfcheck._DATE.match(psf.header["date"]), psf.header["date"])
                self.assertEqual(psf.header["simulator"], "spectre")
                kind = psf.kind
                if kind in psfcheck.KIND_KEYS:
                    want = set(psfcheck.COMMON_KEYS) | set(psfcheck.KIND_KEYS[kind])
                    got = set(psf.header) - set(psfcheck.OPTIONAL_KEYS.get(kind, ()))
                    self.assertEqual(got, want)
        self.assertIn("rabsshort", read(os.path.join(SAMPLES, "joop-banaan.dc")).header)   # 19.1, dc and tran
        self.assertIn("rabsshort", read(os.path.join(SAMPLES, "joop-banaan.tran")).header)
        j = check(os.path.join(SAMPLES, "joop-banaan.tran"))
        self.assertEqual(j.names()[0], "V1:p")
        self.assertEqual(j.traces[0].props, {"units": "A"})           # the current TRACE PROP [S 19.1]
        self.assertEqual(j.header["tolerance.relative"], 0.001)       # %#g in 19.1


# -- psf/bad -------------------------------------------------------------------------------------------

class TestBadFiles(TempDir):
    """psf/bad: §11's malformations, one per file, each rejected for its own reason."""

    def test_bad_directory_holds_the_eight_files(self):
        self.assertEqual(sorted(f for f in os.listdir(BAD_DIR) if f != "README"), sorted(BAD_FILES))
        readme = read_text(os.path.join(BAD_DIR, "README"))
        for name in BAD_FILES:
            self.assertIn(name, readme)

    def test_each_bad_file_is_rejected_for_its_reason(self):
        for name, (_, fragment) in BAD_FILES.items():
            with self.subTest(file=name):
                with self.assertRaises(PsfError) as cm:
                    check(os.path.join(BAD_DIR, name))
                self.assertIn(fragment, str(cm.exception))

    def test_each_bad_file_is_the_template_with_one_mutation(self):
        for name in BAD_FILES:
            with self.subTest(file=name):
                with open(os.path.join(BAD_DIR, name), "rb") as fh:
                    self.assertEqual(fh.read(), bad_text(name).encode("latin-1"))

    def test_the_template_itself_passes(self):
        psf = check(self.write("good.tran", good_text()))
        self.assertEqual((psf.kind, psf.points, psf.names()), ("tran", 2, ["a", "b"]))
        self.assertEqual(psf.column("b"), [2.0, 4.0])
        self.assertEqual(psf.sweep_values(), [0.0, 1.0])


# -- the grammar -----------------------------------------------------------------------------------------

INFO_HEAD = '''HEADER
"PSFversion" "1.00"
"simulator" "spectre"
"version" "vamos (test)"
"date" "1:02:03 PM, Sat Oct 4, 2026"
"design" "t"
"analysis type" "info"
"analysis name" "i"
"analysis description" "Circuit Information"
"xVecSorted" "unknown"
"tolerance.relative" 1.000000000000000e-03
'''

OP_TEXT = '''HEADER
"PSFversion" "1.00"
"simulator" "spectre"
"version" "vamos (test)"
"date" "1:02:03 PM, Sat Oct 4, 2026"
"design" "t"
"analysis type" "dc"
"analysis name" "op"
"analysis description" "DC Analysis `op'"
"xVecSorted" "unsorted"
"tolerance.relative" 1.000000000000000e-03
"reltol" 1.000000000000000e-03
"abstol(V)" 1.000000000000000e-06
"abstol(I)" 1.000000000000000e-12
"temp" 2.700000000000000e+01
"tnom" 2.700000000000000e+01
"tempeffects" "all"
"gmindc" 1.000000000000000e-12
TYPE
"V" FLOAT DOUBLE PROP(
"units" "V"
"key" "node"
"tolerance" 1.000000000000000e-06
)
"I" FLOAT DOUBLE PROP(
"units" "A"
"key" "branch"
"tolerance" 1.000000000000000e-12
)
"C" COMPLEX DOUBLE PROP(
"units" "V"
)
"N" INT LONG PROP(
"units" ""
)
"S" STRUCT(
"x" FLOAT DOUBLE PROP(
"units" "V"
)
"y" FLOAT DOUBLE
) PROP(
"key" "inst"
)
"A" ARRAY ( * ) STRING *
VALUE
"out" "V" 5.000000000000000e-01
"V1:p" "I" -1.000000000000000e-03 PROP(
"units" "A"
)
"c" "C" (1.000000000000000e+00 -2.000000000000000e+00)
"n" "N" 7
"s" "S" (
1.000000000000000e+00
2.000000000000000e+00
)
"a" "A" (
"p"
"q"
)
END
'''


class TestGrammar(_Scratch):
    """The section order, strings and escapes, numbers, arity, declared types, unique names."""

    def test_operating_point_file_and_every_type(self):
        psf = self.accept(OP_TEXT, "op.dc")
        self.assertEqual(psf.names(), ["out", "V1:p", "c", "n", "s", "a"])
        self.assertEqual(psf.value("out"), 0.5)
        self.assertEqual(psf.value_props["V1:p"], {"units": "A"})
        self.assertEqual(psf.value("c"), complex(1.0, -2.0))
        self.assertEqual(psf.value("n"), 7)
        self.assertEqual(psf.value("s"), {"x": 1.0, "y": 2.0})
        self.assertEqual(psf.value("a"), ["p", "q"])
        self.assertEqual(psf.types["A"].kind, "ARRAY")
        self.assertEqual(psf.types["A"].elem.kind, "STRING")
        self.assertEqual(repr(psf.types["S"]), "STRUCT(x FLOAT, y FLOAT)")
        with self.assertRaises(PsfError):
            psf.column("out")
        with self.assertRaises(PsfError):
            psf.value("nope")

    def test_escapes_are_decoded(self):
        psf = self.accept(good_text(design='a \\"quoted\\" back\\\\slash'))
        self.assertEqual(psf.header["design"], 'a "quoted" back\\slash')
        esc = good_text().replace('"a" "V"', '"net\\<3\\>" "V"').replace('"a" 1.0', '"net\\<3\\>" 1.0') \
                         .replace('"a" 3.0', '"net\\<3\\>" 3.0')
        psf = self.accept(esc, escchars=True)
        self.assertEqual(psf.names(), ["net<3>", "b"])
        self.assertEqual(psf.column("net<3>"), [1.0, 3.0])
        self.reject(esc, "escape \\< is outside the rules")                 # not under +escchars
        self.reject(good_text(design="back\\xslash"), "escape \\x is outside the rules", escchars=True)
        self.reject(good_text(design='two\nlines'), "unexpected character")

    def test_section_order_and_end(self):
        self.reject(good_text() + '"x" 1\n', "text after END")
        self.reject(good_text(end=False), "expected END")
        self.reject(good_text().replace("SWEEP\n", "TRACE\nSWEEP\n"), "section SWEEP after TRACE")
        self.reject(good_text().replace("TYPE\n", "TYPE\nHEADER\n"), "section HEADER after TYPE")
        self.reject(good_text().replace("VALUE\n", "VALUE\nTYPE\n"), "section TYPE after VALUE")
        self.reject("TYPE\n" + good_text(), "expected HEADER")
        self.reject("HEADER\nEND\n", "empty HEADER")
        no_value = good_text().split("VALUE\n")[0] + "END\n"
        self.reject(no_value, "expected VALUE after SWEEP/TRACE")
        info = self.accept(INFO_HEAD + "END\n", "i.info")             # an info file may have nothing to say
        self.assertEqual((info.kind, info.has_value_section), ("info", False))
        self.accept(INFO_HEAD + "TYPE\nVALUE\nEND\n", "i.info")

    def test_declarations(self):
        self.reject(good_text().replace('"PSFversion" "1.00"\n', '"PSFversion" "1.00"\n"PSFversion" "1.00"\n'),
                    "duplicate header key 'PSFversion'")
        self.reject(good_text().replace('"V" FLOAT DOUBLE PROP(', '"sweep" FLOAT DOUBLE PROP('), "type 'sweep' declared twice")
        self.reject(good_text().replace('"b" "V"\n', '"a" "V"\n', 1), "trace 'a' listed twice")
        self.reject(good_text().replace('"b" "V"\n', '"time" "V"\n', 1), "trace 'time' listed twice")
        self.reject(good_text().replace('"a" "V"\n', '"a" GROUP 1\n'), "GROUP traces are not a vamos layout")
        self.reject(good_text().replace('"a" "V"\n"b" "V"\n', '"a" "V"\n"b" "V"\n"c" "W"\n'), "type 'W' is not declared")
        self.reject(good_text().replace("FLOAT DOUBLE PROP(\n\"units\"", "FLOAT SINGLE PROP(\n\"units\""), "FLOAT SINGLE is not a vamos layout")
        self.reject(good_text().replace('"V" FLOAT DOUBLE', '"V" REAL DOUBLE'), "unknown type keyword REAL")
        self.reject(OP_TEXT.replace('"N" INT LONG', '"N" INT'), "INT needs BYTE or LONG", "op.dc")
        self.reject(good_text().replace('"time" "sweep" PROP(', '"time" "sweep" PROP(\n)\n"t2" "sweep" PROP('),
                    "more than one SWEEP")
        self.reject(good_text().replace("TRACE\n", "TRACE\n\"a\" \"V\" PROP(\n\"u\" 1\n\"u\" 2\n)\n")
                    .replace('"a" "V"\n"b"', '"b"', 1), "duplicate PROP 'u'")

    def test_values(self):
        self.reject(good_text(t0="0"), "integer '0' where FLOAT")
        self.accept(good_text(t0="nan", t1="inf"))                       # §8.3: nan, inf, -inf are written
        self.reject(OP_TEXT.replace('"n" "N" 7', '"n" "N" 7.0'), "real '7.0' where INT is declared", "op.dc")
        self.reject(OP_TEXT.replace("(1.000000000000000e+00 -2.000000000000000e+00)", "(1.000000000000000e+00)"),
                    "expected flt", "op.dc")
        self.reject(OP_TEXT.replace("1.000000000000000e+00\n2.000000000000000e+00\n)", "1.000000000000000e+00\n)"),
                    "STRUCT value ends before member 'y'", "op.dc")
        self.reject(OP_TEXT.replace("2.000000000000000e+00\n)", "2.000000000000000e+00\n3.000000000000000e+00\n)"),
                    "more than 2 members", "op.dc")
        self.reject(OP_TEXT.replace('"out" "V" 5.000000000000000e-01\n', '"out" "V" 5.000000000000000e-01\n"out" "V" 1.0\n'),
                    "value 'out' listed twice", "op.dc")
        self.reject(good_text(p2='"b" 4.000000000000000e+00'), "point 2: expected trace 'a', got 'b'")
        self.reject(good_text(p2='"a" 3.0\n"b" 4.0\n"a" 5.0'), "point 3 starts with 'a', expected the sweep 'time'")


# -- the content rules -----------------------------------------------------------------------------------

class TestContentRules(_Scratch):
    """§8.3's header table, xVecSorted, the date layout, the SWEEP PROPs, the reader's accessors."""

    def test_header_table(self):
        self.reject(good_text().replace('"gmin" 1.000000000000000e-12\n', ""), "a tran header lacks 'gmin'")
        self.reject(good_text().replace('"gmin" 1', '"gmin2" 1'), "lacks 'gmin'")
        self.reject(good_text().replace('"cmin" 0.000000000000000e+00\n', '"cmin" 0.000000000000000e+00\n"extra" 1\n'),
                    "keys outside §8.3's table: 'extra'")
        self.accept(good_text().replace('"cmin" 0.000000000000000e+00\n', '"cmin" 0.000000000000000e+00\n"rabsshort" 1.000000000000000e-03\n'))
        self.accept(OP_TEXT.replace('"gmindc" 1.000000000000000e-12\n', '"gmindc" 1.000000000000000e-12\n"rabsshort" 1.000000000000000e-03\n'), "op.dc")
        self.reject(OP_TEXT.replace('"analysis name" "op"\n', '"analysis name" "op"\n"operating point producer" "op"\n'),
                    "a dc header has keys outside", "op.dc")
        self.reject(OP_TEXT.replace('"temp" 2.700000000000000e+01', '"temp" 27'), "header 'temp' must be a FLOAT", "op.dc")
        self.reject(OP_TEXT.replace('"design" "t"', '"design" 7'), "header 'design' must be a string", "op.dc")
        self.reject(OP_TEXT.replace('"analysis type" "dc"\n', ""), "header lacks 'analysis type'", "op.dc")
        unknown = INFO_HEAD.replace('"analysis type" "info"', '"analysis type" "pnoise"') \
                           .replace('"xVecSorted" "unknown"', '"xVecSorted" "ascending"\n"relharmnum" 1')
        self.accept(unknown + "END\n", "p.pnoise")                      # a kind outside the table: common keys only
        probe = good_text().replace('"analysis type" "tran"', '"analysis type" "noise"')
        self.reject(probe, "a noise header lacks")

    def test_x_vec_sorted(self):
        self.reject(good_text().replace('"xVecSorted" "ascending"', '"xVecSorted" "sorted"'), "is not ascending, unsorted or unknown")
        self.reject(good_text().replace('"xVecSorted" "ascending"', '"xVecSorted" "unsorted"'),
                    "a tran file has xVecSorted 'unsorted', expected 'ascending'")
        self.reject(OP_TEXT.replace('"xVecSorted" "unsorted"', '"xVecSorted" "ascending"'),
                    "a dc file has xVecSorted 'ascending', expected 'unsorted'", "op.dc")
        self.reject(good_text(t1="-1.000000000000000e+00"), "the sweep decreases (-1.0 after 0.0)")
        self.accept(good_text(t1="0.000000000000000e+00"))              # non-decreasing is enough

    def test_date_layout(self):
        for bad in ("02:56:17 PM, Thu Oct 01, 2026", "1:02:03 PM, Thur Oct 4, 2026", "1:02:03 PM, Sat Oct 4 2026",
                    "13:02:03 PM, Sat Oct 4, 2026", "1:02:03 pm, Sat Oct 4, 2026", "Sat Oct  4 13:02:03 2026"):
            with self.subTest(date=bad):
                self.reject(good_text().replace("1:02:03 PM, Sat Oct 4, 2026", bad), "is not `h:mm:ss AM, Day Mon d, yyyy`")
        for good in ("12:00:00 AM, Sun Jan 1, 2000", "11:59:59 PM, Wed Dec 31, 1999", "9:11:11 AM, Sat Sep 14, 2019"):
            with self.subTest(date=good):
                self.accept(good_text().replace("1:02:03 PM, Sat Oct 4, 2026", good))

    def test_sweep_props(self):
        self.reject(good_text().replace('"grid" 1', '"grid" 2'), "grid 2 is neither 1 (linear) nor 3 (log)")
        self.accept(good_text().replace('"grid" 1', '"grid" 3'))
        self.reject(good_text().replace('"sweep_direction" 0', '"sweep_direction" 0.0'), "SWEEP PROP 'sweep_direction' must be an integer")
        self.reject(good_text().replace('"plot" 0', '"plot" "no"'), "SWEEP PROP 'plot' must be an integer")
        parent = good_text().replace('"analysis type" "tran"', '"analysis type" "sweep"') \
                            .replace('"xVecSorted" "ascending"', '"xVecSorted" "unknown"')
        parent = re.sub(r'"start".*?"gmin" 1.000000000000000e-12\n', "", parent, flags=re.S)
        self.reject(parent, "a sweep parent carries traces", "s.sweep")

    def test_reader_accessors(self):
        psf = self.accept(good_text())
        self.assertEqual(psf.at("a", 0.5), 2.0)                          # linear interpolation
        self.assertEqual(psf.at("b", 1.0), 4.0)
        with self.assertRaises(PsfError):
            psf.at("a", 1.5)
        with self.assertRaises(PsfError):
            psf.column("nope")
        with self.assertRaises(PsfError):
            psf.value("a")
        self.assertEqual(psf.column("time"), [0.0, 1.0])
        self.assertEqual(str(psf.sweep), "Trace('time', 'sweep')")
        self.assertEqual(psf.swept, True)


# -- the logFile tree -------------------------------------------------------------------------------------

class TestLogFileRules(TempDir):
    """A copy of the 23.1 run with one change at a time (§8.2's layout, the parent/leaf rules)."""

    def setUp(self):
        super().setUp()
        self.run = os.path.join(self.tmp, "run")
        shutil.copytree(ASCII_23, self.run)
        self.log = os.path.join(self.run, "logFile")
        shutil.copyfile(LOG_23, self.log)

    def mutate(self, old: str, new: str, fragment: str, count: int = 1) -> str:
        """Write the pristine 23.1 logFile with `old` replaced by `new` (one change per call) and
        check that check_logfile rejects it with `fragment` in its message."""
        text = read_text(LOG_23)
        self.assertIn(old, text)
        text = text.replace(old, new, count)
        with open(self.log, "w", encoding="latin-1", newline="\n") as fh:
            fh.write(text)
        with self.assertRaises(PsfError) as cm:
            check_logfile(self.log)
        self.assertIn(fragment, str(cm.exception))
        return str(cm.exception)

    def test_the_copy_passes(self):
        self.assertEqual(len(check_logfile(self.log).entries), 38)
        self.assertEqual(len(check(self.log).entries), 38)              # datadir defaults to the logFile's own

    def test_header_rules(self):
        self.mutate('"ingold" 2\n', '"ingold" 2\n"extra" 1\n', "keys outside §8.2's layout: 'extra'")
        self.mutate('"measdgt" 0', '"measdgt" 0.0', "header 'measdgt' must be an integer")
        self.mutate('"Log Time Stamp" "Tue Apr 15 17:51:25 2025"', '"Log Time Stamp" "17:51:25 2025-04-15"',
                    "is not asctime() layout")
        self.mutate('"simMode" "Spectre"\n', "", "logFile header lacks 'simMode'")
        self.mutate('"analysisInst" STRUCT(\n"analysisType" STRING *', '"analysisInst" STRUCT(\n"analysisKind" STRING *',
                    "is not §8.2's STRUCT")

    def test_keys_and_files(self):
        self.mutate('"myop-dc" "analysisInst"', '"myop-op" "analysisInst"', "is not `<name>-dc`")
        self.mutate('"mytran-tran" "analysisInst"', '"myop-dc" "analysisInst"', "value 'myop-dc' listed twice")
        os.remove(os.path.join(self.run, "myac.ac"))
        self.mutate('"format" STRING *', '"format" STRING *', "names a dataFile that does not exist")
        shutil.copyfile(os.path.join(ASCII_23, "myac.ac"), os.path.join(self.run, "myac.ac"))
        self.mutate('"myop.dc"\n"PSF"', '"myop.dc"\n"PSFBIN"', "has format 'PSFBIN', expected PSF")
        self.mutate('"mytran.tran.tran"\n"PSF"\n""', '"myop.dc"\n"PSF"\n""', "name the same dataFile 'myop.dc'")
        self.mutate('"DC Analysis `myop\'"', '"DC Analysis `myop2\'"', "describes itself as")
        self.mutate('"mydc.dc"\n"PSF"\n""\n("dc")', '"mydc.dc"\n"PSF"\n""\n()', "has sweepVariable [] but mydc.dc sweeps ['dc']")
        self.mutate('"myac.ac"\n"PSF"\n""\n("freq")\n"AC Analysis `myac\': freq = (1 Hz -> 1 MHz)"\n) PROP(\n"data_type" "swept_scalar"',
                    '"myac.ac"\n"PSF"\n""\n("freq")\n"AC Analysis `myac\': freq = (1 Hz -> 1 MHz)"\n) PROP(\n"data_type" "scalar"',
                    "has data_type 'scalar', expected 'swept_scalar'")

    def test_parents_and_leaves(self):
        self.mutate('"mysweep-000_ac1.ac"\n"PSF"\n"mysweep_ac1-sweep"', '"mysweep-000_ac1.ac"\n"PSF"\n"nosuch-sweep"',
                    "parent that does not exist: 'nosuch-sweep'")
        self.mutate('"mysweep-000_ac1.ac"\n"PSF"\n"mysweep_ac1-sweep"', '"mysweep-000_ac1.ac"\n"PSF"\n"myop-dc"',
                    "which is a 'dc', not a sweep or Monte Carlo parent")
        self.mutate('"sweep_tree_type" "leafNode"\n"R1:r" 1000.00', '"sweep_tree_type" "leafNode"\n"R1:r" 1500.00',
                    "carries R1:r = 1500.0 but its parent's value at index 0 is 1000.0")
        self.mutate('"sweep_tree_type" "leafNode"\n"R1:r" 1000.00', '"sweep_tree_type" "leafNode"\n"C1:c" 1000.00',
                    "must end its PROP with its parent's sweep variable 'R1:r'")
        self.mutate('"data_type" "scalar"\n"sweep_tree_type" "leafNode"\n"iteration" 1.00000',
                    '"data_type" "scalar"\n"sweep_tree_type" "leafNode"\n"iteration" 3.00000',
                    "carries iteration = 3.0 but its parent's value at index 0 is 1.0")
        self.mutate('"Monte Carlo parent"\n)\n"mymonte_tran1-montecarlo"',
                    '"Monte Carlo parent"\n) PROP(\n"sweep_tree_type" "sweepNode"\n)\n"mymonte_tran1-montecarlo"',
                    "Monte Carlo parent 'mymonte_dc2-montecarlo' carries a PROP")
        self.mutate('"Sweep parent"\n) PROP(\n"sweep_tree_type" "sweepNode"\n)', '"Sweep parent"\n) PROP(\n"data_type" "scalar"\n)',
                    "sweep parent 'mysweep_dc1-sweep' carries a data_type")
        self.mutate('"data_type" "swept_scalar"\n"sweep_tree_type" "leafNode"\n"R1:r" 1000.00',
                    '"data_type" "swept_scalar"\n"R1:r" 1000.00', "must carry `sweep_tree_type leafNode` after data_type")
        self.mutate('"myop.dc"\n"PSF"\n""\n()\n"DC Analysis `myop\'"\n) PROP(\n"data_type" "scalar"\n)',
                    '"myop.dc"\n"PSF"\n""\n()\n"DC Analysis `myop\'"\n) PROP(\n"data_type" "scalar"\n"R1:r" 1.0\n)',
                    "carries PROPs beyond data_type")

    def test_counts_and_indices(self):
        text = read_text(self.log)
        start = text.index('"mysweep-002_dc1-dc" "analysisInst"')
        end = text.index('"mysweep-002_ac1-ac" "analysisInst"')
        with open(self.log, "w", encoding="latin-1", newline="\n") as fh:
            fh.write(text[:start] + text[end:])
        with self.assertRaises(PsfError) as cm:
            check_logfile(self.log)
        self.assertIn("parent 'mysweep_dc1-sweep' has 3 points but 2 children", str(cm.exception))
        # the third leaf renamed to point 5, its data file copied along (same description inside)
        shutil.copyfile(os.path.join(self.run, "mysweep-002_dc1.dc"), os.path.join(self.run, "mysweep-005_dc1.dc"))
        self.mutate('"mysweep-002_dc1-dc" "analysisInst" (\n"dc"\n"mysweep-002_dc1.dc"',
                    '"mysweep-005_dc1-dc" "analysisInst" (\n"dc"\n"mysweep-005_dc1.dc"',
                    "has point index 5 but its parent 'mysweep_dc1-sweep' has 3 points")
        # a data file whose own header describes another analysis than its logFile entry
        p = os.path.join(self.run, "mysweep-002_dc1.dc")
        edited = read_text(p).replace("`mysweep-002_dc1'", "`mysweep-005_dc1'")
        with open(p, "w", encoding="latin-1", newline="\n") as fh:
            fh.write(edited)
        self.mutate('"measdgt" 0', '"measdgt" 0',
                    "entry 'mysweep-002_dc1-dc' describes itself as \"DC Analysis `mysweep-002_dc1': Vin:dc = (0 V -> 2 V)\" "
                    "but mysweep-002_dc1.dc says \"DC Analysis `mysweep-005_dc1': Vin:dc = (0 V -> 2 V)\"")

    def test_not_a_logfile(self):
        with self.assertRaises(PsfError) as cm:
            check_logfile(os.path.join(self.run, "myop.dc"))
        self.assertIn("not a logFile", str(cm.exception))


class TestCommandLine(TempDir):
    def test_main_reports_pass_and_fail(self):
        good = self.write("good.tran", good_text())
        bad = os.path.join(BAD_DIR, "no_end.tran")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = psfcheck.main([good, bad])
        lines = out.getvalue().splitlines()
        self.assertEqual(rc, 1)
        self.assertEqual(lines[0], "PASS good.tran: tran, 2 types, sweep=time, 2 traces, 2 points, 0 values")
        self.assertTrue(lines[1].startswith("FAIL " + bad + ":"), lines[1])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = psfcheck.main(["--datadir", ASCII_23, LOG_23])
        self.assertEqual((rc, out.getvalue().strip()), (0, "PASS logFile: logFile, 38 entries, 8 parents"))


if __name__ == "__main__":
    unittest.main()
