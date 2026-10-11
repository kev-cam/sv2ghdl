"""The SPICE-mode acceptance corpus (docs/VAMOS_SPECTRE_DESIGN.md §11.1 "SPICE-mode acceptance"; owner S1).

    python3 -m unittest discover -s tests/vamos -p 'test_spectre_spice_corpus.py' -v

Every NetlistParse.rs tests/corpus/*.sp file (fixtures/spectre/spice/corpus, 62 files) goes through
spectre.parse as a SPICE-mode netlist (line 1 the title, §3.1), which reads it with
spice.parse_fragment in the spectre-spice dialect: it parses, or fails with a named error (a NoteError
carrying error notes; never another exception).  For the parsed files the element names, nodes and
parameter names match the expected SPICE CST dumps (fixtures/spectre/spice/expected).  The two
language-switch files split into the expected regions.  Runs on both legs, no engine.
"""

import glob
import os
import re
import unittest

from vamos_testlib import TempDir, fixture

from vamos.netlist import ir, spectre  # noqa: E402
from vamos.notes import ERROR, NoteError  # noqa: E402

CORPUS = fixture("spectre", "spice", "corpus")
EXPECTED = fixture("spectre", "spice", "expected")
ELEMENTS = {"Resistor": "r", "Capacitor": "c", "Inductor": "l", "Voltage": "v", "Current": "i", "Diode": "d",
            "MOSFET": "m", "BipolarTransistor": "q", "SubcktCall": "x"}
TWO_TERMINAL = {"r", "c", "l", "v", "i"}


def load_cst(path):
    """[(kind, start, end, children)] of a dump."""
    root = None
    stack = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^( *)(\S+) (\d+)-(\d+)\s*$", line)
            if not m:
                continue
            depth = len(m.group(1)) // 2
            node = [m.group(2), int(m.group(3)), int(m.group(4)), []]
            while len(stack) > depth:
                stack.pop()
            if stack:
                stack[-1][3].append(node)
            else:
                root = node
            stack.append(node)
    return root


def cst_elements(root, src):
    """(kind letter, name, nodes, model, parameter names) of every top-level element of a SPICE dump."""
    out = []
    for node in root[3]:
        kind = ELEMENTS.get(node[0])
        if kind is None:
            continue
        hier = [src[c[1]:c[2]].decode("utf-8", "replace").lower() for c in node[3] if c[0] == "HierarchialNode"]
        params = [src[c[3][0][1]:c[3][0][2]].decode("utf-8", "replace").lower() for c in node[3] if c[0] == "Parameter"]
        if not hier:
            continue
        name, rest = hier[0], hier[1:]
        if kind in TWO_TERMINAL:
            nodes, model = rest[:2], None
        elif kind == "x":
            nodes, model = rest[:-1], rest[-1] if rest else None
        else:
            nodes, model = rest[:-1], rest[-1] if rest else None
        out.append((kind, name, nodes, model, params))
    return out


class TestSpiceCorpus(TempDir):
    def run_file(self, path):
        try:
            return spectre.parse(path, os.path.dirname(path), spectre.SpectreParseOpts()), None
        except NoteError as exc:
            errors = [n for n in exc.notes if n.severity == ERROR]
            self.assertTrue(errors, "%s: NoteError without an error note" % path)
            return None, errors

    def test_every_file_parses_or_fails_with_a_named_error(self):
        files = sorted(glob.glob(os.path.join(CORPUS, "*.sp")))
        self.assertEqual(len(files), 62)
        parsed, failed = [], []
        for path in files:
            with self.subTest(file=os.path.basename(path)):
                nl, errors = self.run_file(path)
                if nl is not None:
                    self.assertEqual(nl.dialect, "spectre")
                    parsed.append(os.path.basename(path))
                else:
                    for n in errors:
                        self.assertTrue(n.message.strip(), path)
                    failed.append(os.path.basename(path))
        # the acceptance set: at least as many as today's spice.parse (25 of the 62, E62), and the deliberate
        # err_* files fail.  Against E62's list: lost are controlled_value.sp (a B-source expression reads
        # V(in): no behavioral expressions in v1, §3.5); ex_1n3064.sp parses since the diode `is` row (below);
        # gained are b1_dots.sp and ex_tran.sp (SPICE2G's .tran fields, §4.3) and the lib_block files
        # (a .lib section defined in the netlist is indexed and skipped, as spice.py reads a whole file)
        self.assertGreaterEqual(len(parsed), 25, (parsed, failed))
        for name in ("err_binop.sp", "err_paramlist.sp", "err_parens.sp", "err_ternary.sp"):
            self.assertIn(name, failed)
        for name in ("a.sp", "ex_voltage_divider.sp", "rlc.sp", "cov_expr_ops.sp", "ex_global.sp", "ext_nodeset.sp",
                     "b1_dots.sp", "ex_tran.sp", "lib_block.sp", "lib_block_named.sp"):
            self.assertIn(name, parsed)
        # named errors, as today's spice.parse gives them too: a model defined nowhere (b1_dev: dmod), a
        # controlling source that does not exist (controlled_linear: V1), a PULSE period not longer than
        # tr+tf+pw (ex_rc_ladder), a .data block, an .if block
        for name in ("b1_dev.sp", "controlled_linear.sp", "ex_rc_ladder.sp", "controlled_value.sp", "data_basic.sp",
                     "if_basic.sp", "g.sp"):
            self.assertIn(name, failed)
        self.assertIn("model dmod is defined nowhere", self.errors_of(os.path.join(CORPUS, "b1_dev.sp")))
        self.assertIn("behavioral expressions", self.errors_of(os.path.join(CORPUS, "controlled_value.sp")))
        self.assertIn("pulse period", self.errors_of(os.path.join(CORPUS, "ex_rc_ladder.sp")))
        # ex_1n3064.sp writes is= on a diode card, ref5 p.302's own spelling (`model pdiode diode is=1.8e-5`):
        # SPECTRE_MASTERS carries the row beside js (p.304) since phase 1b, so the file parses
        self.assertIn("ex_1n3064.sp", parsed)

    def errors_of(self, path):
        """Every error message of a file that fails, joined."""
        _nl, errors = self.run_file(path)
        self.assertTrue(errors, path)
        return "\n".join(n.message for n in errors)

    def test_parsed_files_match_the_dumps(self):
        checked = wanted = 0
        for path in sorted(glob.glob(os.path.join(CORPUS, "*.sp"))):
            base = os.path.splitext(os.path.basename(path))[0]
            dump = os.path.join(EXPECTED, base + ".txt")
            if not os.path.exists(dump):
                continue
            nl, errors = self.run_file(path)
            if nl is None:
                continue
            with self.subTest(file=base):
                with open(path, "rb") as fh:
                    src = fh.read()
                want = cst_elements(load_cst(dump), src)
                insts = {it.name: it for it in ir.flat_items(nl.body) if isinstance(it, ir.Instance)}
                wanted += len(want)
                for kind, name, nodes, model, params in want:
                    self.assertIn(name, insts, "%s: element %s of the dump is not in the IR" % (base, name))
                    inst = insts[name]
                    checked += 1
                    if kind == "x":
                        self.assertEqual(inst.kind, "x", name)
                        continue
                    self.assertEqual(inst.kind, kind, name)
                    self.assertEqual(inst.nodes, nodes, name)
                    if model is not None and kind in ("d", "m", "q"):
                        self.assertEqual(inst.master, model, name)
                    for p in params:
                        self.assertTrue(p in inst.params or p in ("m", kind) or inst.value is not None,
                                        "%s: parameter %s" % (name, p))
        # every element of every parsed file's dump is compared; the 26 parsed files hold 20 of them
        # (most corpus files test dot statements, parameters or deliberate errors, not elements)
        self.assertEqual(checked, wanted)
        self.assertGreaterEqual(checked, 20)

    def test_language_switch_files_split_into_regions(self):
        cir = fixture("spectre", "cst", "corpus", "arrays_langswitch", "lang_swap_spice_start.cir")
        nl = spectre.parse(cir, os.path.dirname(cir), spectre.SpectreParseOpts())
        insts = {it.name: it for it in nl.body if isinstance(it, ir.Instance)}
        self.assertEqual(sorted(insts), ["r1", "r2", "r3"])         # line 1 (r0 ...) is the title of a top file
        self.assertEqual({k: v.nodes for k, v in insts.items()}, {"r1": ["1", "0"], "r2": ["1", "0"], "r3": ["1", "0"]})
        self.assertEqual(insts["r1"].origin, cir + ":5")
        top = self.write("top.scs", '// title\ninclude "%s"\n' % cir.replace("\\", "/"))
        nl = spectre.parse(top, self.tmp, spectre.SpectreParseOpts())
        self.assertEqual(sorted(it.name for it in nl.body if isinstance(it, ir.Instance)), ["r0", "r1", "r2", "r3"])
        lang = fixture("spectre", "lang", "lang_swap.scs")
        top = self.write("top2.scs", '// title\ninclude "%s"\n' % lang.replace("\\", "/"))
        nl = spectre.parse(top, self.tmp, spectre.SpectreParseOpts())
        insts = {it.name: it for it in nl.body if isinstance(it, ir.Instance)}
        self.assertEqual(sorted(insts), ["c1", "r1", "r2", "r3"])
        self.assertEqual((insts["r1"].prim, insts["c1"].prim, insts["r2"].prim), ("resistor", "capacitor", "resistor"))
        self.assertEqual(insts["r1"].origin, lang + ":4")


if __name__ == "__main__":
    unittest.main()
