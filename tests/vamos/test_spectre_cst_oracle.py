"""The CST differential oracle of the Spectre statement layer (docs/VAMOS_SPECTRE_DESIGN.md §11.1
"Statements and the CST oracle"; owner S1).

    python3 -m unittest discover -s tests/vamos -p 'test_spectre_cst_oracle.py' -v

For every committed fixtures/spectre/**/*.scs (and .cir) with a committed NetlistParse.rs d565fd3 dump
(cst/expected, cadnip/*.cst, lang/*.cst; no Rust at test time):
  - the CST must tile the file (every non-blank, non-comment byte inside a statement node) unless the
    file is an err_* file or cst/GAPS names it [E93];
  - spectre.statements(text, origin, title=False) is compared with the CST through a kind map on the
    fields both have: kind, name as spelled, node texts, master or analysis keyword, parameter names
    in order and value text (whitespace-normalized), the operator structure of every expression
    (except ** chains and vector splits), subckt ports, inline flag and body, if conditions and
    bodies, save items and modifiers, ic/nodeset pairs, include file and section, simulator
    parameters, global nodes, function name, arguments and body;
  - an err_* file is rejected by statements() or by parse() (reject parity);
  - cst/DIVERGENCES lists every file or statement where vamos follows the manual instead, and the
    oracle asserts vamos's reading there; cst/GAPS lists the constructs the CST cannot parse, each with
    the lang/ fixture that covers it, whose statement kinds the oracle asserts.  Every line of both
    files must be used.
Not compared: numeric values, semantics, comments, the title, SPICE sub-trees.  Runs on both legs.
"""

import glob
import os
import re
import unittest

from vamos_testlib import TempDir, fixture

from vamos.netlist import expr as E  # noqa: E402
from vamos.netlist import spectre  # noqa: E402
from vamos.netlist.expr_ast import Binary, Call, Name, Num, Str, Ternary, Unary  # noqa: E402
from vamos.notes import NoteError  # noqa: E402

ROOT = fixture("spectre")
STMT_KINDS = {"Instance": "instance", "Parameters": "parameters", "Model": "model", "Subckt": "subckt",
              "Analysis": "analysis", "Simulator": "simulator", "Save": "save", "Include": "include",
              "AHDLInclude": "ahdl_include", "Global": "global", "Ic": "ic", "NodeSet": "nodeset",
              "FunctionDecl": "function", "ConditionalBlock": "if", "SPICENetlistSource": "spice",
              "Alter": "alter", "AlterGroup": "altergroup", "Check": "check", "CheckLimit": "checklimit",
              "Info": "info", "Options": "options", "ParamTest": "paramtest", "Set": "set", "Shell": "shell"}
BAD = ("Incomplete", "Error")
EXPR_KINDS = ("BinaryExpression", "UnaryOp", "FunctionCall", "Parens", "TernaryExpr", "Identifier",
              "NumberLiteral", "BuiltinConst", "StringLiteral", "SpectreArray")


# -- the dumps -----------------------------------------------------------------------------------------

class Node:
    __slots__ = ("kind", "start", "end", "children")

    def __init__(self, kind, start, end):
        self.kind, self.start, self.end, self.children = kind, start, end, []

    def text(self, src: bytes) -> str:
        return src[self.start:self.end].decode("utf-8", "replace").strip()

    def kids(self, *kinds):
        return [c for c in self.children if c.kind in kinds]

    def first(self, *kinds):
        for c in self.children:
            if c.kind in kinds:
                return c
        return None


def load_cst(path: str) -> Node:
    root = None
    stack = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^( *)(\S+) (\d+)-(\d+)\s*$", line)
            if not m:
                continue
            depth = len(m.group(1)) // 2
            node = Node(m.group(2), int(m.group(3)), int(m.group(4)))
            while len(stack) > depth:
                stack.pop()
            if stack:
                stack[-1].children.append(node)
            else:
                root = node
            stack.append(node)
    assert root is not None, path
    return root


def untiled(src: bytes, root: Node):
    """(offset, text) of the first non-blank, non-comment byte outside every top-level statement."""
    covered = bytearray(len(src) + 1)
    for c in root.children:
        for i in range(c.start, min(c.end, len(src))):
            covered[i] = 1
    pos = 0
    for line in src.split(b"\n"):
        s = line.strip()
        if s and not s.startswith(b"//") and not s.startswith(b"*"):
            body = re.sub(rb"(^|\s)//.*$", b"", line)
            for k in range(len(body)):
                if body[k:k + 1].strip() and not covered[pos + k]:
                    return pos + k, body[k:k + 40].decode("utf-8", "replace")
        pos += len(line) + 1
    return None


def norm(text: str) -> str:
    return " ".join(text.replace("\\\n", " ").split())       # a '\' continuation is not part of a value


# -- CST -> records --------------------------------------------------------------------------------------

def cst_params(node: Node, src: bytes):
    """[(name, value text, value node)] of a statement's Parameter children."""
    out = []
    for p in node.kids("Parameter"):
        name = p.children[0].text(src)
        val = p.children[2] if len(p.children) > 2 else None
        out.append((name, norm(val.text(src)) if val is not None else "", val))
    return out


def cst_nodes(node: Node, src: bytes):
    lst = node.first("SNodeList", "SubcktNodes")
    if lst is None:
        return []
    return [n.text(src) for n in lst.kids("SNode")]


def cst_record(node: Node, src: bytes):
    """A comparable record of one CST statement node, or None for a SPICE sub-tree."""
    kind = STMT_KINDS.get(node.kind)
    rec = {"kind": kind, "name": "", "nodes": [], "master": "", "params": [], "children": [], "branches": []}
    if kind == "spice":
        return rec
    idents = node.kids("Identifier")
    if kind in ("instance", "analysis", "alter", "altergroup", "check", "checklimit", "info", "options",
                "paramtest", "set", "shell"):
        rec["name"] = idents[0].text(src)
        rec["nodes"] = cst_nodes(node, src)
        if kind == "instance":
            rec["master"] = idents[-1].text(src) if len(idents) > 1 else ""
        else:
            kw = node.first("Keyword")
            rec["master"] = kw.text(src) if kw is not None else ""
        rec["params"] = cst_params(node, src)
        if kind == "altergroup":
            rec["children"] = [cst_record(c, src) for c in node.children if c.kind in STMT_KINDS]
    elif kind == "parameters":
        rec["params"] = cst_params(node, src)
    elif kind == "model":
        rec["name"], rec["master"] = idents[0].text(src), idents[1].text(src)
        rec["params"] = cst_params(node, src)
    elif kind == "subckt":
        rec["name"] = idents[0].text(src)
        rec["nodes"] = cst_nodes(node, src)
        rec["params"] = [("inline", "yes", None)] if any(k.text(src) == "inline" for k in node.kids("Keyword")) else []
        rec["children"] = [cst_record(c, src) for c in node.children if c.kind in STMT_KINDS]
    elif kind == "if":
        for part in node.children:
            if part.kind not in ("If", "ElseIf", "Else"):
                continue
            cond = part.first(*EXPR_KINDS)
            body = [cst_record(c, src) for c in part.children if c.kind in STMT_KINDS]
            rec["branches"].append((norm(cond.text(src)) if cond is not None else "", body, cond))
    elif kind == "save":
        rec["nodes"] = [s.text(src) for s in node.kids("SaveSignal")]
        rec["params"] = cst_params(node, src)
    elif kind in ("ic", "nodeset"):
        for p in node.kids("ICParameter", "Parameter"):
            rec["params"].append((p.children[0].text(src), norm(p.children[2].text(src)), p.children[2]))
    elif kind in ("include", "ahdl_include"):
        s = node.first("StringLiteral")
        rec["name"] = s.text(src).strip('"') if s is not None else ""
        sec = node.first("IncludeSection")
        if sec is not None:
            rec["params"] = [("section", sec.first("Identifier").text(src), None)]
    elif kind == "global":
        rec["nodes"] = [n.text(src) for n in node.children if n.kind in ("Identifier", "SNode", "NumberLiteral")]
    elif kind == "simulator":
        kws = [k.text(src) for k in node.kids("Keyword")]
        if len(kws) >= 3:
            rec["params"] = [(kws[1], kws[2], None)]
        rec["params"] += cst_params(node, src)
    elif kind == "function":
        rec["name"] = idents[0].text(src)
        rec["nodes"] = [a.first("FunctionDeclArg").first("Identifier").text(src) for a in node.kids("FunctionArgs")
                        if a.first("FunctionDeclArg") is not None]
        body = None
        seen_return = False
        for c in node.children:
            if c.kind == "Keyword" and c.text(src) == "return":
                seen_return = True
            elif seen_return and c.kind in EXPR_KINDS:
                body = c
                break
        rec["params"] = [("return", norm(body.text(src)) if body is not None else "", body)]
    return rec


def cst_shape(node: Node, src: bytes):
    """The operator structure of a CST expression (numbers and constants are leaves of one kind)."""
    k = node.kind
    if k == "Parens":
        inner = node.first(*EXPR_KINDS)
        return cst_shape(inner, src) if inner is not None else ("num",)
    if k in ("NumberLiteral", "BuiltinConst", "StringLiteral"):
        return ("num",)
    if k == "Identifier":
        return ("name", node.text(src))
    if k == "UnaryOp":
        op = node.first("Operator").text(src)
        arg = node.first(*EXPR_KINDS)
        inner = cst_shape(arg, src)
        if op == "+":
            return inner
        if inner == ("num",):
            return ("num",)
        return ("un", op, inner)
    if k == "BinaryExpression":
        exprs = node.kids(*EXPR_KINDS)
        op = node.first("Operator").text(src)
        if op == "^~":
            op = "~^"
        if op == "**":
            return ("pow", cst_shape(exprs[0], src), cst_shape(exprs[1], src))
        return ("bin", op, cst_shape(exprs[0], src), cst_shape(exprs[1], src))
    if k == "TernaryExpr":
        exprs = node.kids(*EXPR_KINDS)
        return ("tern",) + tuple(cst_shape(e, src) for e in exprs)
    if k == "FunctionCall":
        name = node.first("Identifier", "BuiltinFunc").text(src)
        shapes = tuple(cst_shape(a.first(*EXPR_KINDS), src) for a in node.kids("FunctionArgs")
                       if a.first(*EXPR_KINDS) is not None)      # one FunctionArgs node per argument
        if name == "pow":
            return ("pow",) + shapes
        return ("call", name, shapes)
    if k == "SpectreArray":
        return ("array", tuple(cst_shape(e, src) for e in node.kids(*EXPR_KINDS)))
    raise AssertionError("unexpected expression node %s" % k)


def vamos_shape(e):
    if isinstance(e, (Num, Str)):
        return ("num",)
    if isinstance(e, Name):
        return ("name", {"temper": "temp", "$tnom": "tnom"}.get(e.name, e.name))
    if isinstance(e, Unary):
        return ("un", e.op, vamos_shape(e.arg))
    if isinstance(e, Binary):
        return ("bin", e.op, vamos_shape(e.left), vamos_shape(e.right))
    if isinstance(e, Ternary):
        return ("tern", vamos_shape(e.cond), vamos_shape(e.a), vamos_shape(e.b))
    if isinstance(e, Call):
        if e.func == "cpow":
            return ("pow",) + tuple(vamos_shape(a) for a in e.args)
        return ("call", e.func, tuple(vamos_shape(a) for a in e.args))
    raise AssertionError(repr(e))


# -- vamos -> records --------------------------------------------------------------------------------------

def stmt_record(st: spectre.Stmt):
    rec = {"kind": st.kind, "name": st.name, "nodes": list(st.nodes), "master": st.master,
           "params": [(k, norm(v)) for k, v in st.params], "children": [stmt_record(c) for c in st.children],
           "branches": [(norm(c), [stmt_record(s) for s in body]) for c, body in st.branches]}
    if st.kind == "if":
        rec["master"] = ""
    if st.kind == "model" and st.children:
        rec["children"] = []
    return rec


# -- the lists ----------------------------------------------------------------------------------------------

def read_list(name: str):
    out = []
    with open(os.path.join(ROOT, "cst", name), encoding="utf-8") as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            out.append([f.strip() for f in line.rstrip("\n").split("|")])
    return out


class Lists:
    def __init__(self):
        self.div = read_list("DIVERGENCES")
        self.gaps = read_list("GAPS")
        self.used_div = set()
        self.used_gaps = set()

    def divergences(self, rel: str):
        out = {}
        for i, row in enumerate(self.div):
            if row[0] == rel:
                out[row[1]] = (row[2], i)
        return out

    def gap(self, rel: str):
        for i, row in enumerate(self.gaps):
            if row[1] == rel:
                return row[2].split(), i
        return None


def files_with_dumps():
    """(relative file, dump path, title stripped)."""
    out = []
    for f in sorted(glob.glob(os.path.join(ROOT, "cst", "corpus", "*", "*.*"))):
        cat, base = os.path.basename(os.path.dirname(f)), os.path.splitext(os.path.basename(f))[0]
        dump = os.path.join(ROOT, "cst", "expected", cat, base + ".txt")
        if os.path.exists(dump):
            out.append((os.path.relpath(f, ROOT).replace(os.sep, "/"), dump, False))
    for f in sorted(glob.glob(os.path.join(ROOT, "cadnip", "*.scs"))):
        out.append((os.path.relpath(f, ROOT).replace(os.sep, "/"), f[:-4] + ".cst", True))
    for f in sorted(glob.glob(os.path.join(ROOT, "lang", "*.scs"))):
        if os.path.exists(f[:-4] + ".cst"):
            out.append((os.path.relpath(f, ROOT).replace(os.sep, "/"), f[:-4] + ".cst", False))
    return out


def file_text(rel: str, strip_title: bool):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        src = fh.read()
    if strip_title:                                    # the cadnip dumps are of the body (cst/regen.sh)
        src = src.split(b"\n", 1)[1] if b"\n" in src else b""
    return src


class TestCstOracle(TempDir):
    lists = Lists()

    @classmethod
    def setUpClass(cls):
        cls.lists = Lists()

    def statements_of(self, rel: str, text: str):
        if rel.endswith(".scs"):
            return spectre.statements(text, rel, title=False)
        return spectre._statements_lang(text, rel, False, "spice")       # a .cir starts in SPICE mode (§3.1)

    def rejected_by_parse(self, rel: str, text: str) -> bool:
        """parse() of a top netlist that includes the file as its own language."""
        top = self.write("top.scs", "// title\ninclude \"%s\"\n" % os.path.join(ROOT, rel).replace("\\", "/"))
        try:
            spectre.parse(top, self.tmp, spectre.SpectreParseOpts())
        except NoteError:
            return True
        return False

    def test_tiling(self):
        """Every statement byte of a dump lies in a statement node, unless err_* or a GAPS fixture [E93]."""
        for rel, dump, strip in files_with_dumps():
            with self.subTest(file=rel):
                src = file_text(rel, strip)
                root = load_cst(dump)
                gap = self.lists.gap(rel)
                miss = untiled(src, root)
                if os.path.basename(rel).startswith("err_") or gap is not None:
                    continue
                self.assertIsNone(miss, "%s: %r at byte %s is outside every statement node" % (rel, miss and miss[1],
                                                                                                 miss and miss[0]))

    def test_reject_parity(self):
        """Every err_* file is rejected by statements() or parse(); DIVERGENCES names the exceptions."""
        for rel, dump, strip in files_with_dumps():
            base = os.path.basename(rel)
            div = self.lists.divergences(rel)
            whole = div.get("file")
            if not base.startswith("err_") and whole is None:
                continue
            with self.subTest(file=rel):
                text = file_text(rel, strip).decode("utf-8", "replace")
                if whole is not None:
                    self.lists.used_div.add(whole[1])
                    if whole[0] == "accept":
                        sts = self.statements_of(rel, text)
                        self.assertTrue(sts, "%s: vamos accepts it (DIVERGENCES)" % rel)
                        continue
                    self.assertEqual(whole[0], "error", rel)
                try:
                    self.statements_of(rel, text)
                except NoteError:
                    continue
                self.assertTrue(self.rejected_by_parse(rel, text),
                                "%s: neither statements() nor parse() rejects it" % rel)

    def test_statements_match_the_cst(self):
        for rel, dump, strip in files_with_dumps():
            base = os.path.basename(rel)
            div = self.lists.divergences(rel)
            if base.startswith("err_") or div.get("file") is not None:
                continue
            with self.subTest(file=rel):
                self.compare_file(rel, dump, strip, div)

    def compare_file(self, rel, dump, strip, div):
        src = file_text(rel, strip)
        text = src.decode("utf-8", "replace")
        root = load_cst(dump)
        gap = self.lists.gap(rel)
        title = gap is not None and self.lists.gaps[gap[1]][0].startswith("a title line")
        sts = spectre.statements(text, rel, title=True) if title else self.statements_of(rel, text)
        self._funcs = {}
        if gap is not None:
            kinds, idx = gap
            self.lists.used_gaps.add(idx)
            self.assertEqual([s.kind for s in sts], kinds, "%s: vamos's statement kinds (GAPS)" % rel)
        # pair each vamos statement with the CST node that starts at its byte span
        cst_nodes_top = [c for c in root.children]
        used = set()
        for st in sts:
            start = len(text[:st.span[0]].encode("utf-8"))
            end = len(text[:st.span[1]].encode("utf-8"))
            node = None
            for i, c in enumerate(cst_nodes_top):
                if i in used:
                    continue
                if c.start <= start < c.end or (start <= c.start < end):
                    node = c
                    used.add(i)
                    break
            if st.kind == "simulator" and node is None:
                continue                                 # a switch the CST put inside its SPICE sub-tree
            if node is None and gap is not None:
                continue                                 # the CST dropped the text (GAPS: a truncated node group)
            self.assertIsNotNone(node, "%s: statement %s %r at byte %d has no CST node" % (rel, st.kind, st.name, start))
            if st.kind == "function":                    # later calls keep the call shape (a placeholder body)
                self._funcs[st.name] = (list(st.nodes), Call(st.name, tuple(Name(a) for a in st.nodes)))
            if node.kind in BAD:
                self.assertIsNotNone(gap, "%s: the CST has %s for %s %r; GAPS does not list the file"
                                     % (rel, node.kind, st.kind, st.name))
                continue
            if node.kind == "SPICENetlistSource":
                self.assertEqual(st.kind, "spice", rel)
                d = div.get("spice")
                if d is not None and sts.index(st) > 0:         # the region after a mid-file switch
                    self.lists.used_div.add(d[1])
                    seg = text[st.span[0]:st.span[1]]
                    first = seg.strip().split("\n")[0].strip()
                    self.assertTrue(re.match(r"[rR]1 1 0 1k", first), "%s: the first SPICE line %r is a statement" % (rel, first))
                continue
            self.assertIn(node.kind, STMT_KINDS, "%s: unexpected CST node %s" % (rel, node.kind))
            self.compare(rel, cst_record(node, src), stmt_record(st), st, src, div)
        rest = [c.kind for i, c in enumerate(cst_nodes_top) if i not in used and c.kind not in BAD]
        if gap is not None:
            return                                       # the file's reading is the GAPS kinds, asserted above
        if not any(c.kind in BAD for c in cst_nodes_top):
            self.assertEqual(rest, [], "%s: CST statements without a vamos statement" % rel)
        else:
            self.fail("%s: the CST has Incomplete/Error nodes and GAPS does not list the file" % rel)

    def compare(self, rel, cst, vam, st, src, div, path=""):
        where = "%s:%s %s %r" % (rel, path, vam["kind"], vam["name"])
        self.assertEqual(cst["kind"], vam["kind"], where)
        self.assertEqual(cst["name"], vam["name"], where)
        self.assertEqual(cst["nodes"], vam["nodes"], where + " nodes")
        if cst["kind"] not in ("if",):
            self.assertEqual(cst["master"], vam["master"], where + " master")
        cparams = [(k, v) for k, v, _n in cst["params"]]
        if vam["kind"] == "simulator":
            self.assertEqual(cparams, vam["params"], where + " params")
        else:
            self.assertEqual(cparams, vam["params"], where + " params")
        for (k, v, node), (vk, vv) in zip(cst["params"], vam["params"]):
            if node is not None and node.kind in EXPR_KINDS:
                self.compare_expr(rel, k, v, node, src, div, where)
        if cst["kind"] == "if":
            self.assertEqual(len(cst["branches"]), len(vam["branches"]), where + " branches")
            for (cc, cbody, cnode), (vc, vbody) in zip(cst["branches"], vam["branches"]):
                self.assertEqual(cc, vc, where + " condition")
                if cnode is not None:
                    self.compare_expr(rel, "if", cc, cnode, src, div, where)
                self.assertEqual(len(cbody), len(vbody), where + " body")
                for i, (c2, v2) in enumerate(zip(cbody, vbody)):
                    self.compare(rel, c2, v2, st, src, div, path + "/if[%d]" % i)
        if cst["kind"] in ("subckt", "altergroup"):
            self.assertEqual(len(cst["children"]), len(vam["children"]), where + " body")
            for i, (c2, v2) in enumerate(zip(cst["children"], vam["children"])):
                self.compare(rel, c2, v2, st, src, div, path + "/%s[%d]" % (vam["kind"], i))

    def compare_expr(self, rel, key, text, node, src, div, where):
        d = div.get("param " + key)
        if d is not None:
            self.lists.used_div.add(d[1])
            reading = d[0]
            if reading == "error":
                with self.assertRaises(E.ExprError, msg="%s: %s=%s should be an ExprError" % (where, key, text)):
                    E.parse(text, "sensitive", "spectre")
                return
            if reading == "pow-right":
                sh = vamos_shape(E.parse(text, "sensitive", "spectre"))
                self.assertEqual((sh[0], sh[2][0]), ("pow", "pow"), "%s: %r is not x**(y**z)" % (where, sh))
                return
            if reading == "constant":
                self.assertEqual(vamos_shape(E.parse(text, "sensitive", "spectre")), ("num",), where)
                return
            if reading.startswith("vector:"):
                self.assertTrue(text.startswith("["), where)
                els = spectre._vector_elements(text[1:-1])
                self.assertEqual(len(els), int(reading.split(":")[1]), "%s: elements %r" % (where, els))
                return
            self.fail("%s: unknown DIVERGENCES reading %r" % (rel, reading))
        if node.kind == "SpectreArray":
            return                                         # vector splits: DIVERGENCES or the lang fixtures
        if text.count("**") > 1:
            return                                         # ** chains: DIVERGENCES
        funcs = getattr(self, "_funcs", {})
        try:
            e = E.parse(text, "sensitive", "spectre", funcs=funcs or None)
        except E.ExprError as exc:
            self.fail("%s: %s=%s does not parse: %s (list it in DIVERGENCES if vamos is right)" % (where, key, text, exc))
        self.assertEqual(vamos_shape(e), cst_shape(node, src), "%s: %s=%s operator structure" % (where, key, text))

    def test_function_bodies_match(self):
        """Function bodies: a call of an earlier user function keeps the call shape (a placeholder body)."""
        for rel, dump, strip in files_with_dumps():
            if "funcdecl" not in rel and not rel.startswith("lang/"):
                continue
            if os.path.basename(rel).startswith("err_"):
                continue
            with self.subTest(file=rel):
                src = file_text(rel, strip)
                text = src.decode("utf-8", "replace")
                root = load_cst(dump)
                sts = [s for s in self.statements_of(rel, text) if s.kind == "function"]
                nodes = [c for c in root.children if c.kind == "FunctionDecl"]
                if not sts or len(sts) != len(nodes):
                    continue
                funcs = {}
                for st, node in zip(sts, nodes):
                    rec = cst_record(node, src)
                    body = st.params[0][1]
                    self.assertEqual(rec["params"][0][1], norm(body), rel)
                    e = E.parse(body, "sensitive", "spectre", funcs=funcs or None)
                    self.assertEqual(vamos_shape(e), cst_shape(rec["params"][0][2], src), "%s: %s body" % (rel, st.name))
                    funcs[st.name] = (list(st.nodes), Call(st.name, tuple(Name(a) for a in st.nodes)))

    def test_every_list_line_is_used(self):
        """Nothing is skipped silently: every DIVERGENCES and GAPS line was consumed by the runs above."""
        lists = Lists()
        self.lists = lists
        for rel, dump, strip in files_with_dumps():
            div = lists.divergences(rel)
            base = os.path.basename(rel)
            text = file_text(rel, strip).decode("utf-8", "replace")
            if base.startswith("err_") or div.get("file") is not None:
                if div.get("file") is not None:
                    lists.used_div.add(div["file"][1])
                continue
            self.compare_file(rel, dump, strip, div)
        unused_div = [lists.div[i] for i in range(len(lists.div)) if i not in lists.used_div]
        unused_gaps = [lists.gaps[i] for i in range(len(lists.gaps)) if i not in lists.used_gaps]
        self.assertEqual(unused_div, [], "DIVERGENCES lines no file used")
        self.assertEqual(unused_gaps, [], "GAPS lines no file used")

    def test_the_lang_fixtures_lower(self):
        """Every lang/ fixture that is a complete netlist also goes through parse() (or is rejected for the
        reason its construct implies: a missing include, an unsupported analysis)."""
        expect = {
            "vector_elements.scs": None, "pow_chain.scs": None, "lang_swap.scs": None, "controls.scs": "altergroup",
            "funcdecl_two_args.scs": None, "include_multiple.scs": "not found", "model_group.scs": None,
            "statistics.scs": None, "sweep_block.scs": None, "montecarlo_block.scs": None, "bare_nodes.scs": None,
            "title_line.scs": None, "star_comment.scs": None, "escaped_name.scs": None, "braceless_if.scs": None,
            "library.scs": "library", "prot.scs": None, "save_modifiers.scs": None, "oneline_function.scs": None,
            "node_groups.scs": None, "insensitive.scs": None, "analysis_args.scs": None,
        }
        for f in sorted(glob.glob(os.path.join(ROOT, "lang", "*.scs"))):
            base = os.path.basename(f)
            with self.subTest(file=base):
                want = expect.get(base, None)
                top = self.write("t.scs", "// title\ninclude \"%s\"\n" % f.replace("\\", "/"))
                if base == "title_line.scs":
                    top = f
                try:
                    nl = spectre.parse(top, self.tmp, spectre.SpectreParseOpts())
                except NoteError as exc:
                    self.assertIsNotNone(want, "%s: %s" % (base, "; ".join(n.text() for n in exc.notes if n.severity == "error")))
                    self.assertTrue(any(want in n.message for n in exc.notes), "%s: %s" % (base, exc.notes))
                    continue
                self.assertIsNone(want, base)
                self.assertEqual(nl.dialect, "spectre")


if __name__ == "__main__":
    unittest.main()
