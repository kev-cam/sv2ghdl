"""The document check of docs/VAMOS_SPECTRE_DESIGN.md §11 T0 (the vcs-ams TestDocs pattern; both
legs, no engine): every inferred tag of the design before §13 names an open question that exists,
and the fixture documentation that phase 0 lays down stays consistent with the tree.
"""

import os
import re
import unittest

from vamos_testlib import ROOT, fixture

DOC = os.path.join(ROOT, "docs", "VAMOS_SPECTRE_DESIGN.md")
SPECTRE = fixture("spectre")


def doc_lines():
    with open(DOC, encoding="utf-8") as fh:
        return fh.read().split("\n")


def section_index(lines, heading_prefix):
    for i, ln in enumerate(lines):
        if ln.startswith(heading_prefix):
            return i
    raise AssertionError("no heading %r in %s" % (heading_prefix, DOC))


def prose_before_13(lines):
    """The text before §13 without fenced code, inline code spans and §0's evidence table."""
    s13 = section_index(lines, "## 13.")
    hdr = section_index(lines, "| tag | evidence |")
    end = hdr
    while lines[end].strip():
        end += 1
    out = []
    fence = False
    for i, ln in enumerate(lines[:s13]):
        if ln.startswith("```"):
            fence = not fence
            continue
        if fence or hdr <= i < end:
            continue
        out.append(ln)
    return re.sub(r"`[^`\n]*`", "", "\n".join(out))


def inferred_tags(text):
    """The `I …` parts of every [tag]: `[I, §14 q.N]` and the `I …` half of `[M …; I …]`."""
    tags = []
    for body in re.findall(r"\[([^\[\]]*)\]", text):
        for part in body.split(";"):
            part = part.strip()
            if re.match(r"I\b", part):
                tags.append(part)
    return tags


def open_questions(lines):
    """The numbers of §14's questions, in order."""
    s14 = section_index(lines, "## 14.")
    s15 = section_index(lines, "## 15.")
    return [int(m.group(1)) for m in (re.match(r"^(\d+)\. ", ln) for ln in lines[s14:s15]) if m]


class TestDesignDoc(unittest.TestCase):
    def test_every_inferred_tag_names_an_existing_open_question(self):
        lines = doc_lines()
        questions = set(open_questions(lines))
        tags = inferred_tags(prose_before_13(lines))
        self.assertGreater(len(tags), 0)
        bad = []
        for t in tags:
            refs = re.findall(r"§14\s+q\.(\d+)", t)
            if not refs or any(int(n) not in questions for n in refs):
                bad.append(t)
        self.assertEqual(bad, [], "inferred tags naming no existing §14 question: %r" % bad)

    def test_open_questions_are_numbered_contiguously(self):
        nums = open_questions(doc_lines())
        self.assertGreaterEqual(len(nums), 49)
        self.assertEqual(nums, list(range(1, len(nums) + 1)))

    def test_sources_name_the_vendored_commits(self):
        # every commit the spectre fixtures' README cites is one §0's Sources list gives
        lines = doc_lines()
        s0 = section_index(lines, "**Sources.**")
        end = s0
        while lines[end].strip():
            end += 1
        sources = "\n".join(lines[s0:end])
        with open(os.path.join(SPECTRE, "README"), encoding="utf-8") as fh:
            readme = fh.read()
        commits = set(re.findall(r"\bcommit ([0-9a-f]{7,40})\b", readme))
        self.assertGreaterEqual(len(commits), 4, commits)
        for c in sorted(commits):
            self.assertIn(c[:7], sources, "fixtures/spectre/README cites commit %s, which §0 Sources lacks" % c)
        for name in ("NetlistParse.rs", "psf-parser", "psf_utils", "Xyce_Regression"):
            self.assertIn(name, sources)
            self.assertIn(name, readme)


class TestFixtureDocs(unittest.TestCase):
    def test_spectre_readme_lists_every_directory(self):
        with open(os.path.join(SPECTRE, "README"), encoding="utf-8") as fh:
            readme = fh.read()
        dirs = []
        for top in sorted(os.listdir(SPECTRE)):
            p = os.path.join(SPECTRE, top)
            if os.path.isdir(p):
                dirs.append(top + "/")
                for sub in sorted(os.listdir(p)):
                    if os.path.isdir(os.path.join(p, sub)) and top in ("cst", "psf", "spice"):
                        dirs.append(top + "/" + sub + "/")
        self.assertIn("cst/corpus/", dirs)
        for d in dirs:
            self.assertIn(d, readme, "fixtures/spectre/README does not mention %s" % d)

    def test_top_readme_and_license_name_the_spectre_sets(self):
        with open(fixture("README"), encoding="utf-8") as fh:
            top = fh.read()
        with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as fh:
            lic = fh.read()
        for name in ("spectre/README", "NetlistParse.rs", "psf-parser", "psf_utils", "Xyce_Regression",
                     "LICENSES/MIT.txt"):
            self.assertIn(name, top, "fixtures/README does not mention %s" % name)
        for name in ("NetlistParse.rs", "psf-parser", "psf_utils", "Xyce_Regression", "LICENSES/MIT.txt",
                     "tests/vamos/third_party/psf_parser"):
            self.assertIn(name, lic, "LICENSE does not mention %s" % name)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "LICENSES", "MIT.txt")))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "tests", "vamos", "third_party", "psf_parser", "LICENSE")))

    def test_excluded_corpus_files_are_absent(self):
        # §11 Licences: the 20 manual-citing corpus files and var_reference.scs are not vendored (§14 q.41)
        corpus = os.path.join(SPECTRE, "cst", "corpus")
        excluded = ("arrays_langswitch/array_sweep.scs", "control_named/alter.scs", "funcdecl/two_args.scs",
                    "include_global/include_multiple.scs", "julia_parse_tests/var_reference.scs")
        for rel in excluded:
            self.assertFalse(os.path.exists(os.path.join(corpus, rel)), rel)
        kept = []
        for root, _d, files in os.walk(corpus):
            kept += [os.path.join(root, f) for f in files]
        self.assertEqual(len(kept), 106)
        for f in kept:
            with open(f, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            self.assertNotRegex(text, r"(?i)spectre_reference|manual", os.path.relpath(f, corpus))
        expected = os.path.join(SPECTRE, "cst", "expected")
        for f in kept:
            rel = os.path.relpath(f, corpus)
            self.assertTrue(os.path.isfile(os.path.join(expected, os.path.splitext(rel)[0] + ".txt")), rel)


if __name__ == "__main__":
    unittest.main()
