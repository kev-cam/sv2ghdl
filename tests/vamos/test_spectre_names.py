"""The escaped names of the user's rule (2026-10-19, phase 2e FIX2E3N): vamos/names.py as property tests, and the rule
as the one way every name a deck prints is spelled.  Standard library only, Python 3.9; the engine runs on WSL.

escape_name keeps a name of ASCII letters, digits and _ that does not start with _, and writes any other as _ plus
its UTF-8 bytes, a letter or digit as it is and every other byte as _<3-digit octal>; unescape_name reverses it
exactly; is_escaped tells an escaped form from everything else.

TestEscapeName, TestUnescapeText   the function pair (both legs)
TestParser                         escaped names the Spectre parser reads (a blank in a node list, net\\<0\\>=1 in an
                                   ic, iprobe=V\\<1\\>, a dev= sweep of X\\.1.V2 without param=)
TestDecks                          both engines' decks print every netlist name by the rule (nodes, instances, subckts,
                                   ports, cards, globals, saves, sweeps, alters, noise outputs, ic/nodeset), and the
                                   name maps send each Ref to its escaped column
TestDottedPaths                    an IR path whose names hold a '.' (a subckt instance X\\.1): signals.resolve flattens
                                   it, plan.build finds its targets, VACASK's run_chain its X lines; two paths of one
                                   name are an error
TestStateFiles                     names with a blank, #, : or \\ in the state files, written and read
TestEngineText                     the engines' messages name the netlist's names
TestRuns                           (engines) every odd name kind, at the top level and in a subckt, saved, probed,
                                   swept, altered, a noise output and in a state file: every output equals that of the
                                   same deck with plain names, apart from the names"""

import os
import random
import re
import shutil
import sys
import tempfile
import types
import unittest

from vamos_testlib import LAUNCHER, TempDir, needs_vacask, needs_xyce, run  # noqa: E402  (puts the checkout on sys.path)

from vamos.names import escape_name, is_escaped, is_plain, unescape_name, unescape_text  # noqa: E402

_SAFE = re.compile(r"[A-Za-z0-9_]+\Z")


class TestEscapeName(unittest.TestCase):

    def check(self, name):
        """The properties every name has: the escape is letters, digits and _ only; it is the name itself exactly when
        the name is plain, else it starts with _ and is an escaped form; unescape_name gives the name back."""
        esc = escape_name(name)
        self.assertTrue(_SAFE.match(esc), (name, esc))
        if is_plain(name):
            self.assertEqual(esc, name)
            self.assertFalse(is_escaped(esc), name)
        else:
            self.assertNotEqual(esc, name)
            self.assertTrue(esc.startswith("_"), (name, esc))
            self.assertTrue(is_escaped(esc), (name, esc))
            self.assertFalse(is_plain(esc), (name, esc))
        self.assertEqual(unescape_name(esc), name, (name, esc))
        return esc

    def test_the_rule_s_own_examples(self):
        for name, want in (("-", "__055"), (".", "__056"), ("_", "__137"), ("<", "__074"), (" ", "__040"),
                           ("spectre-23.1", "_spectre_05523_0561"), ("spectre.23.1.0.242", "_spectre_05623_0561_0560_056242"),
                           ("b.c", "_b_056c"), ("a<0>", "_a_0740_076"), ("net<3>", "_net_0743_076"),
                           ("vdd!", "_vdd_041"), ("X.1", "_X_0561"), ("a b", "_a_040b"), ("_x", "__137x"),
                           ("_b_056c", "__137b_137056c"), ("", "_")):
            with self.subTest(name=name):
                self.assertEqual(self.check(name), want)

    def test_plain_names_are_unchanged(self):
        for name in ("spectre", "SPECTRE", "spectre231", "spectre_23", "specsim", "R1", "x1", "a_b_c", "0", "007",
                     "1a", "vamos_af1", "m_nch", "A" * 200):
            with self.subTest(name=name):
                self.assertEqual(self.check(name), name)
                self.assertEqual(unescape_name(name), name)

    def test_every_byte_value_alone_and_in_context(self):
        """Each of the code points 1-255 alone, first, last and between letters; and each byte 128-255 that is not
        UTF-8 on its own, as Python decodes it with surrogateescape (a lone surrogate, encoded with surrogatepass)."""
        for b in range(1, 256):
            c = chr(b)
            for name in (c, c + "x", "x" + c, "a" + c + "b", c + c, "_" + c, c + "_", "a" + c + "1" + c + "z"):
                with self.subTest(byte=b, name=name):
                    esc = self.check(name)
                    if is_plain(name):                  # a letter or digit, or a _ inside a plain name
                        continue
                    if b < 128 and not (c.isascii() and c.isalnum()):
                        self.assertIn("_%03o" % b, esc)
        for b in range(128, 256):
            name = bytes([0x61, b, 0x62]).decode("utf-8", "surrogateescape")
            with self.subTest(raw_byte=b):
                self.check(name)

    def test_the_octal_codes_are_three_digits(self):
        """A digit after an octal group stays a digit: a<0> is _a_074 0 _076, and \\x01 followed by 7 is _0017."""
        self.assertEqual(escape_name("\x017"), "__0017")
        self.assertEqual(unescape_name("__0017"), "\x017")
        self.assertEqual(escape_name("a\x00"), "_a_000")
        self.assertEqual(unescape_name("_a_000"), "a\x00")
        self.assertEqual(escape_name("\xff"), "__303_277")          # U+00FF is two UTF-8 bytes

    def test_non_ascii_names(self):
        for name in ("é", "Ω", "résistance", "Ω1", "日本", "ノード", "\U0001f600", "a b", " ", "x\ud800y"):
            with self.subTest(name=name):
                esc = self.check(name)
                self.assertEqual(esc, "_" + "".join(chr(b) if chr(b).isascii() and chr(b).isalnum() else "_%03o" % b
                                                    for b in name.encode("utf-8", "surrogatepass")))

    def test_leading_underscore_and_names_that_look_escaped(self):
        """A name that starts with _ is always escaped, so it never collides with an escaped form: _b_056c, the
        escape of b.c, is itself a legal name, and its own escape differs from b.c's."""
        for name in ("_", "__", "_x", "_1", "__137", "_b_056c", "_spectre_05523_0561", "__137b_137056c"):
            with self.subTest(name=name):
                esc = self.check(name)
                self.assertTrue(esc.startswith("__137"), esc)
        self.assertNotEqual(escape_name("_b_056c"), escape_name("b.c"))
        self.assertTrue(is_escaped("_b_056c"))                      # as a form it stands for b.c
        self.assertEqual(unescape_name("_b_056c"), "b.c")
        self.assertEqual(unescape_name(escape_name("_b_056c")), "_b_056c")

    def test_the_empty_name(self):
        self.assertEqual(self.check(""), "_")
        self.assertTrue(is_escaped("_"))
        self.assertFalse(is_plain(""))

    def test_forms_escape_name_never_writes(self):
        """is_escaped tells the forms apart: a letter or digit written as its octal code, a short or bad octal group, a
        byte sequence that is not UTF-8, a plain name, and anything with another character are not escaped forms, and
        unescape_name refuses all but the plain names."""
        for bad in ("_141", "_x", "_tmp", "_9", "_08", "_0", "__", "___137", "_a_", "__1x", "__400", "__377",
                    "__303", "_a.b", "a.b", "-", "a b", "x_041", " ", "_é"):
            with self.subTest(form=bad):
                self.assertFalse(is_escaped(bad), bad)
                if is_plain(bad):
                    self.assertEqual(unescape_name(bad), bad)
                else:
                    with self.assertRaises(ValueError):
                        unescape_name(bad)

    def test_random_names_round_trip_and_never_collide(self):
        rnd = random.Random(20261019)
        alphabet = ["a", "Z", "0", "9", "_", ".", "-", "<", ">", "!", " ", "\\", ":", "$", "é", "Ω", "\x01", "\x7f",
                    "\udcff", "日"]
        seen = {}
        for _ in range(20000):
            name = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 8)))
            esc = self.check(name)
            other = seen.setdefault(esc, name)
            self.assertEqual(other, name, "%r and %r have one escape %r" % (other, name, esc))
        plain = {n for n in seen.values() if is_plain(n)}
        escaped = {e for e, n in seen.items() if not is_plain(n)}
        self.assertFalse(plain & escaped, "a plain name equal to another name's escape")


class TestUnescapeText(unittest.TestCase):

    def test_engine_messages(self):
        self.assertEqual(unescape_text("Node '_b_056c' not found"), "Node 'b.c' not found")
        self.assertEqual(unescape_text("node X1:_m_056n, node _X_0561:_m_056n"), "node X1:m.n, node X.1:m.n")
        self.assertEqual(unescape_text("undefined symbols in .PRINT command: node _B_056C"),
                         "undefined symbols in .PRINT command: node B.C")          # Xyce's upper case stays
        self.assertEqual(unescape_text("x1._a_0740_076", sep="."), "x1.a<0>")

    def test_words_that_are_not_escaped_forms_stay(self):
        for text in ("vamos_af1=0", "/work/_tmp/rc.scs", "_x", "a _ b", "m_nch", "$mfactor", "1e-3 _000x", "r_R_0561",
                     "0.5_056", "in /work/n.run._1_0561b/vamos.sim", "/work/_a_056b/x", "/work/_a_056b.scs",
                     "_a_056b.h"):
            with self.subTest(text=text):
                self.assertEqual(unescape_text(text), text)

    def test_a_sentence_s_full_stop_or_quotes_end_a_name(self):
        self.assertEqual(unescape_text("node _b_056c."), "node b.c.")
        self.assertEqual(unescape_text("`_b_056c' and (_a_040b)"), "`b.c' and (a b)")

    def test_a_lookup_for_an_engine_s_own_spelling(self):
        known = {"R_R_0741_076": "R<1>"}
        self.assertEqual(unescape_text("device R_R_0741_076 in X_X_0561:R_R_0741_076, node _B_056C",
                                       known=lambda c: known.get(c.upper())),
                         "device R<1> in X_X_0561:R<1>, node B.C")


# -- the deck pair: every odd name kind, and the same deck with plain names ------------------------------------------

# placeholder -> (the name as the netlist writes it, the Spectre name, the plain name of the twin deck)
NAMES = {
    "VDD": ("vdd!", "vdd!", "vddg"),                  # a global node with !
    "BUS": (r"net\<0\>", "net<0>", "net0"),           # a bus bit
    "DOT": (r"b\.c", "b.c", "bc"),                    # a dotted node
    "XI": (r"X\.1", "X.1", "X1"),                     # a subckt instance whose name holds a dot
    "MN": (r"m\.n", "m.n", "mn"),                     # a dotted node inside it
    "SPACE": (r"a\ b", "a b", "ab"),                  # a blank
    "UND": ("_u", "_u", "uu"),                        # a name that starts with _
    "LOOK": ("_b_056c", "_b_056c", "bb056c"),         # a name that looks escaped (b.c's escape)
    "DASH": (r"c\-d", "c-d", "cd"),                   # another escaped character
    "RU": (r"R\<1\>", "R<1>", "R7"),                  # an instance
    "RD": (r"R\.d", "R.d", "Rd"),
    "RL": ("_R_0741_076", "_R_0741_076", "Rlook"),    # an instance named like R<1>'s escape
    "VIN": (r"V\<1\>", "V<1>", "Vin1"),               # a source
    "NA": ("n\\\u00e9", "n\u00e9", "ne"),             # a letter that is not ASCII
    "SUB": (r"h\.alf", "h.alf", "half"),              # a subckt
    "PORT": (r"q\.x", "q.x", "qx"),                   # a port
    "MOD": (r"res\.m", "res.m", "resm"),              # a model card
    "P": ("_rv", "_rv", "rv"),                        # a parameter (only a leading _ can be odd)
    "PQ": ("_rq", "_rq", "rq"),                       # a subckt's parameter
    "COLON": (r"a\:b", "a:b", "acolb"),               # a colon, the terminal separator of a save
    "BUSI": (r"w\<1\>", "w<1>", "w1"),                # inside the subckt: a bus bit,
    "SPI": (r"s\ t", "s t", "st"),                    # a blank,
    "UNI": ("_k", "_k", "kk"),                        # a leading _,
    "LKI": ("_m_056n", "_m_056n", "mm056n"),          # m.n's escape beside m.n itself,
    "RI": (r"R\<9\>", "R<9>", "R9"),                  # an instance
}

DECK = r"""// escaped names: every kind in one deck
simulator lang=spectre
global 0 {VDD}
parameters {P}=1k
V0 ({VDD} 0) vsource dc=2
V1 ({BUS} 0) vsource dc=1 mag=1
R1 ({BUS} {DOT}) resistor r={P}
R2 ({DOT} 0) resistor r=1k
{XI} ({DOT} {SPACE}) {SUB} {PQ}=2k
R3 ({SPACE} 0) resistor r=2k
{RU} ({SPACE} {UND}) resistor r=1k
R4 ({UND} {LOOK}) resistor r=1k
R5 ({LOOK} 0) resistor r=1k
{RD} ({VDD} {DASH}) {MOD} r=1k
R6 ({DASH} 0) resistor r=1k
C1 ({DOT} 0) capacitor c=1n
{RL} ({UND} 0) resistor r=5k
{VIN} ({NA} 0) vsource dc=0.5 mag=1
R8 ({NA} {DOT}) resistor r=3k
Rc1 ({DOT} {COLON}) resistor r=2k
Rc2 ({COLON} 0) resistor r=2k
model {MOD} resistor tc1=0
subckt {SUB} (p {PORT})
  parameters {PQ}=1k
  R1 (p {MN}) resistor r=1k
  R2 ({MN} {PORT}) resistor r=1k
  Rg ({MN} {VDD}) resistor r=10k
  {RI} ({MN} {BUSI}) resistor r={PQ}
  R3 ({BUSI} {SPI}) resistor r=1k
  R4 ({SPI} {UNI}) resistor r=1k
  R5 ({UNI} {LKI}) resistor r=1k
  R6 ({LKI} {PORT}) resistor r=1k
ends {SUB}
ic {DOT}=0.3 {COLON}=0.1
nodeset {BUS}=1
op1 dc write="op1.ns"
dc1 dc dev={RD} param=r values=[1k 2k]
dc2 dc dev={XI}.R1 param=r values=[1k 2k]
dc3 dc dev={VIN} param=dc values=[0.1 0.2]
dc4 dc dev={XI}.{RI} param=r values=[1k 3k]
dc5 dc sub={XI} param={PQ} values=[1k 3k]
sw1 sweep param={P} values=[1k 3k] {{
  op2 dc
}}
al1 alter dev={RU} param=r value=3k
al2 alter dev={XI}.R2 param=r value=2k
al3 alter mod={MOD} param=tc1 value=1e-3
al4 alter param=temp value=60
al5 alter dev={XI}.{RI} param=r value=4k
op3 dc
ac1 ac start=1 stop=1k dec=2
noise1 ({DOT} 0) noise start=1 stop=1k dec=2 iprobe=V1
noise2 ({XI}.{MN} 0) noise start=1 stop=1k dec=2 iprobe=V1
noise3 ({DOT} 0) noise start=1 stop=1k dec=2 iprobe={VIN}
noise4 noise oprobe={RL} start=1 stop=1k dec=2 iprobe=V1
noise5 ({XI}.{LKI} 0) noise start=1 stop=1k dec=2 iprobe=V1
tr1 tran stop=1u writefinal="tr1.ic"
save {BUS} {DOT} {XI}.{MN} {SPACE} {UND} {LOOK} {DASH} {VDD} {RU}:1 {XI}.R1:1 V1:p {RD}:1 {RL}:1 {VIN}:p {NA}
save {XI}.{BUSI} {XI}.{SPI} {XI}.{UNI} {XI}.{LKI} {XI}.{RI}:1 {COLON}
"""

# the state files written by DECK, read back (readns, readic), and one written by hand in netlist spellings
READ_BACK = r"""// escaped names: state files read back
simulator lang=spectre
global 0 {VDD}
parameters {P}=1k
V0 ({VDD} 0) vsource dc=2
V1 ({BUS} 0) vsource dc=1 mag=1
R1 ({BUS} {DOT}) resistor r={P}
R2 ({DOT} 0) resistor r=1k
{XI} ({DOT} {SPACE}) {SUB}
R3 ({SPACE} 0) resistor r=2k
{RU} ({SPACE} {UND}) resistor r=1k
R4 ({UND} {LOOK}) resistor r=1k
R5 ({LOOK} 0) resistor r=1k
{RD} ({VDD} {DASH}) resistor r=1k
R6 ({DASH} 0) resistor r=1k
C1 ({DOT} 0) capacitor c=1n
{VIN} ({NA} 0) vsource dc=0.5
R8 ({NA} {DOT}) resistor r=3k
subckt {SUB} (p {PORT})
  R1 (p {MN}) resistor r=1k
  R2 ({MN} {PORT}) resistor r=1k
  Rg ({MN} {VDD}) resistor r=10k
ends {SUB}
op4 dc readns="%s"
tr2 tran stop=1u readic="%s" ic=all
save {BUS} {DOT} {XI}.{MN} {SPACE} {UND} {LOOK} {DASH} {VDD} {NA}
"""
HAND = "{BUS} 1\n{DOT} 0.4\n{XI}.{MN} 0.3\n{SPACE} 0.2\n{LOOK} 0.1\n{VDD} 2\n{NA} 0.5\nV1:p -1e-3\n"

VRES_VA = """`include "disciplines.vams"
module vres(a, b);
  inout a, b; electrical a, b;
  parameter real r = 2000.0;
  analog I(a, b) <+ V(a, b) / r;
endmodule
"""
VA_DECK = r"""// escaped names: Verilog-A instances
simulator lang=spectre
ahdl_include "vres.va"
V1 ({BUS} 0) vsource dc=1
{RU} ({BUS} {DOT}) vres
R2 ({DOT} 0) resistor r=1k
{XI} ({DOT} 0) {SUB}
subckt {SUB} (p q)
  {RU} (p {MN}) vres
  R1 ({MN} q) resistor r=1k
ends {SUB}
op1 dc
save {DOT} {XI}.{MN} {RU}:1
"""
VA_ALTERS = "al1 alter dev={RU} param=r value=3k\nop2 dc\nal2 alter dev={XI}.{RU} param=r value=500\nop3 dc\n" \
            "sw1 dc dev={RU} param=r values=[1k 4k]\n"


def spelled(template, odd):
    """A template with every placeholder as the odd deck writes it, or as its plain twin does."""
    return template.format(**{k: (v[0] if odd else v[2]) for k, v in NAMES.items()})


def to_odd(name):
    """A plain twin's Spectre name in the odd deck's: each '.'- or ':'-separated component mapped."""
    plain = {v[2]: v[1] for v in NAMES.values()}
    return "".join(plain.get(p, p) for p in re.split(r"([.:])", name))


def parse_deck(text, files=None):
    from vamos.netlist import spectre
    d = tempfile.mkdtemp(prefix="vamos_names_")
    try:
        for fn, body in (files or {}).items():
            with open(os.path.join(d, fn), "w", encoding="latin-1") as fh:
                fh.write(body)
        path = os.path.join(d, "deck.scs")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return spectre.parse(path, d, spectre.SpectreParseOpts())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def resolved(text, engine, files=None):
    from vamos.netlist import plan as PL, signals as SG
    from vamos.spectre.job import Settings
    nl = parse_deck(text, files)
    plan = PL.build(nl, engine, Settings())
    nl2, sm, notes = SG.resolve(nl, plan)
    return nl, plan, nl2, sm, notes


def vacask_deck(text):
    from vamos.netlist import vacask
    _nl, plan, nl2, sm, _notes = resolved(text, "vacask")
    names = {}
    return vacask.render(nl2, plan=plan, sigmap=sm, names=names), names


def xyce_decks(text):
    from vamos.spectre import run_xyce
    _nl, plan, nl2, sm, _notes = resolved(text, "xyce")
    decks, names = run_xyce.render_decks(nl2, plan, sm)
    return [d.text for d in decks], names


ODD_IN_A_DECK = ("net<0>", "b.c", "X.1", "m.n", "a b", "c-d", "vdd!", "R<1>", "R.d", "V<1>", "n\u00e9", "h.alf",
                 "q.x", "res.m", "'")


class TestParser(unittest.TestCase):
    """Escaped Spectre names the parser reads (the escaped-name trial of phase 2e, FIX2E3N)."""

    def test_an_escaped_blank_in_a_node_list(self):
        """(a\\ b c) is the nodes a b and c: a node group was cut at every blank (`node a\\: a node name holds ...')."""
        nl = parse_deck("// t\nsimulator lang=spectre\nV1 (a\\ b 0) vsource dc=1\nR1 (a\\ b c) resistor r=1k\n"
                        "R2 (c 0) resistor r=1k\nop1 dc\n")
        self.assertEqual([it.nodes for it in nl.instances()], [["a b", "0"], ["a b", "c"], ["c", "0"]])

    def test_an_escaped_name_on_the_left_of_an_ic_or_a_nodeset(self):
        """nodeset net\\<0\\>=1: the escaped > made it a comparison (`nodeset needs node=value assignments')."""
        nl = parse_deck("// t\nsimulator lang=spectre\nV1 (net\\<0\\> 0) vsource dc=1\nR1 (net\\<0\\> 0) resistor r=1k\n"
                        "nodeset net\\<0\\>=1\nic net\\<0\\>=0.5 b\\>c=0.2\nop1 dc\n")
        self.assertEqual(list(nl.nodesets), ["net<0>"])
        self.assertEqual(sorted(nl.ics), ["b>c", "net<0>"])

    def test_an_escaped_name_in_iprobe_and_oprobe(self):
        nl = parse_deck("// t\nsimulator lang=spectre\nV\\<1\\> (a 0) vsource dc=1 mag=1\nR\\.1 (a 0) resistor r=1k\n"
                        "n1 noise oprobe=R\\.1 iprobe=V\\<1\\> start=1 stop=10 dec=1\n")
        a = nl.analyses[0]
        self.assertEqual((a.args["oprobe"].text, a.args["iprobe"].text), ("R.1", "V<1>"))

    def test_a_colon_in_a_node_name(self):
        """a\\:b is a node, in an ic and a save, not the terminal b of an instance a (`save a:b: no instance a; not
        saved' and `ic a\\:b=: an inst:term initial condition is an error' before)."""
        text = ("// t\nsimulator lang=spectre\nV1 (in 0) vsource dc=1\nR1 (in a\\:b) resistor r=1k\n"
                "R2 (a\\:b 0) resistor r=1k\nic a\\:b=0.2\nop1 dc\nsave a\\:b\n")
        self.assertEqual(list(parse_deck(text).ics), ["a:b"])
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                _nl, _plan, _nl2, sm, notes = resolved(text, engine)
                self.assertFalse([n for n in notes if n.severity in ("error", "warning")], notes)
                self.assertEqual([name for _r, name, _t in sm.per_step["vamos_a1"]], ["a:b"])

    def test_a_dev_sweep_without_param_of_a_source_in_a_dotted_instance(self):
        """dev= alone sweeps a source's dc: the source V2 of the instance X.1 (found name by name)."""
        nl = parse_deck("// t\nsimulator lang=spectre\nX\\.1 (a 0) cell\nsubckt cell (p q)\n  V2 (p q) vsource dc=1\n"
                        "ends cell\nR1 (a 0) resistor r=1k\nd1 dc dev=X\\.1.V2 values=[1 2]\n")
        self.assertEqual((nl.analyses[0].sweep.name, nl.analyses[0].sweep.param), ("X.1.V2", "dc"))


class TestDecks(unittest.TestCase):
    """Every name a deck prints goes through the user's rule (names.escape_name; FIX2E3N): nodes, instances, subckts,
    ports, cards, globals, saves, sweep and alter targets, noise outputs and inputs, the ic/nodeset pairs; the
    hierarchy separators are the engines' own, a parameter's name (always an identifier) is printed as it is."""

    def test_the_vacask_deck(self):
        text, names = vacask_deck(spelled(DECK, True))
        body = text.split("\ncontrol\n")[0]
        for line in ("global _vdd_041",
                     "V1 (_net_0740_076 0) vamos_vsource type=\"dc\" dc=1.0 mag=1.0 phase=0.0",
                     "parameters __137rv=vamos_v__rv",
                     "R1 (_net_0740_076 _b_056c) vamos_resistor r=__137rv",
                     "_X_0561 (_b_056c _a_040b) _h_056alf __137rq=2000.0 vamos_hf1=vamos_af1 vamos_hv1=vamos_av1",
                     "  parameters __137rq=1000.0",
                     "  _R_0749_076 (vamos_tn5 _w_0741_076) vamos_resistor r=(vamos_hf1 ? vamos_hv1 : __137rq) "
                     "$mfactor=$mfactor",
                     "R4 (__137u __137b_137056c) vamos_resistor r=1000.0",
                     "_R_056d (vamos_tn3 _c_055d) m__res_056m r=1000.0",
                     "_V_0741_076 (_n_303_251 0) vamos_vsource",
                     "model m__res_056m sp_resistor tc1=0.0",
                     "subckt _h_056alf (p _q_056x)",
                     "  R2 (_m_056n _q_056x) vamos_resistor r=1000.0 $mfactor=$mfactor",
                     "  Rg (_m_056n _vdd_041) vamos_resistor r=10000.0 $mfactor=$mfactor",
                     "_R_0741_076 (vamos_tn1 __137u) vamos_resistor r=1000.0",           # R<1>
                     "__137R_1370741_137076 (vamos_tn4 0) vamos_resistor r=5000.0"):     # the instance _R_0741_076
            self.assertIn("\n" + line, "\n" + text, line)
        control = text.split("\ncontrol\n")[1]
        for frag in ('analysis vamos_a1 op nodeset={"_net_0740_076", 1.0}',
                     "save v(_net_0740_076) v(_b_056c) v('_X_0561:_m_056n') v(_a_040b) v(__137u) v(__137b_137056c) "
                     "v(_c_055d) v(_vdd_041) i(vamos_tp1) i('_X_0561:vamos_tp2') i(V1)",
                     'sweep vamos_w1 instance="_R_056d" parameter="r" values=[1000.0, 2000.0]',
                     'sweep vamos_w2 instance="_X_0561:R1" parameter="r"',
                     'sweep vamos_w3 instance="_V_0741_076" parameter="dc"',
                     'instance="_X_0561" parameter="__137rq"', 'variable="vamos_v__rv"',
                     'alter instance("_R_0741_076") r=3000.0', 'alter instance("_X_0561:R2") r=2000.0',
                     'alter model("m__res_056m") tc1=0.001',
                     "nc(_R_0741_076)", "nc(__137R_1370741_137076)", "nc('_X_0561:R1')",
                     'noise out="_b_056c" in="V1"', 'noise out="_X_0561:_m_056n" in="V1"',
                     'noise out="_b_056c" in="_V_0741_076"', 'noise out="__137u" in="V1"',
                     'ic={"_b_056c", 0.3, "_a_072b", 0.1}', "v(_a_072b)"):
            self.assertIn(frag, control, frag)
        for odd in ODD_IN_A_DECK:
            self.assertNotIn(odd, body.split("\n", 1)[1], odd)                      # the title line aside
        self.assertEqual(names["vamos_a1"][("v", "X.1.m.n")], "_X_0561:_m_056n")
        self.assertEqual(names["vamos_a1"][("v", "_b_056c")], "__137b_137056c")
        self.assertEqual(names["vamos_a1"][("i", "V<1>")], "_V_0741_076:flow(br)")
        noise = next(m for m in names.values() if ("onoise",) in m)                # the first noise step's map
        self.assertEqual(noise[("noise", "_R_0741_076")], "n(__137R_1370741_137076)")
        self.assertEqual(noise[("noise", "X.1.R<9>")], "n(_X_0561:_R_0749_076)")      # the rawfile's column
        self.assertIn("nc('_X_0561:_R_0749_076')", control)                              # the save directive

    def test_the_xyce_decks(self):
        decks, names = xyce_decks(spelled(DECK, True))
        first = decks[0]
        for line in (".global _vdd_041", "V1 _net_0740_076 0 DC 1.0 AC 1.0 0.0", ".param __137rv=1000.0",
                     "R1 _net_0740_076 _b_056c {__137rv}", "x_X_0561 _b_056c _a_040b _h_056alf __137rq=2000.0",
                     "r_R_0741_076 vamos_tn1 __137u 1000.0",
                     "r_R_056d vamos_tn3 _c_055d _res_056m 1000.0", "r__137R_1370741_137076 vamos_tn4 0 5000.0",
                     "v_V_0741_076 _n_303_251 0 DC 0.5 AC 1.0 0.0", ".model _res_056m r tc1=0.0",
                     ".subckt _h_056alf p _q_056x params: __137rq=1000.0 vamos_mfactor=1.0",
                     "R2 _m_056n _q_056x 1000.0 m={vamos_mfactor}", "r_R_0749_076 vamos_tn5 _w_0741_076 {__137rq} m={vamos_mfactor}",
                     ".ends _h_056alf", ".NODESET V(_NET_0740_076)=1.0"):
            self.assertIn("\n" + line + "\n", "\n" + first + "\n", line)
        text = "\n".join(decks)
        for frag in (".DC R_R_056D:R LIST 1000.0 2000.0", ".DC X_X_0561:R1:R LIST 1000.0 2000.0",
                     ".param vamos_x__V_0741_076_dc=0.5", ".DC vamos_x__V_0741_076_dc LIST 0.1 0.2",
                     ".STEP __137rv LIST 1000.0 3000.0", ".NOISE V(_B_056C) V1 DEC 2", ".NOISE V(X_X_0561:_M_056N) V1",
                     "x_X_0561 _b_056c _a_040b _h_056alf __137rq={vamos_x__X_0561__rq}",
                     ".NOISE V(_B_056C) V_V_0741_076 DEC 2", ".NOISE V(__137U) V1",
                     "DNO(R_R_0741_076)", "DNO(R__137R_1370741_137076)", ".IC V(_B_056C)=0.3 V(_A_072B)=0.1",
                     "V(X_X_0561:_M_056N) V(_A_040B) V(__137U) V(__137B_137056C) V(_C_055D) V(_VDD_041)",
                     "I(X_X_0561:EVAMOS_TP2) I(V1)", "I(V_V_0741_076)"):     # the probe: a vcvs (FIX2E5)
            self.assertIn(frag, text, frag)
        for deck in decks:
            for odd in ODD_IN_A_DECK:
                self.assertNotIn(odd, deck.split("\n", 1)[1], odd)
        self.assertEqual(names["vamos_a2"][("v", "X.1.m.n")], "V(X_X_0561:_M_056N)")
        self.assertEqual(names["vamos_a2"][("v", "a b")], "V(_A_040B)")
        noise = next(m for m in names.values() if ("onoise",) in m)                # the first noise step's map
        self.assertEqual(noise[("noise", "R<1>")], "DNO(R_R_0741_076)")
        self.assertEqual(noise[("noise", "X.1.R<9>")], "DNO(X_X_0561:R_R_0749_076)")

    def test_the_plain_twin_prints_its_names_as_they_are(self):
        text, _names = vacask_deck(spelled(DECK, False))
        self.assertIn("\nX1 (bc ab) half rq=2000.0 vamos_hf1=vamos_af1 vamos_hv1=vamos_av1\n", text)
        self.assertIn('sweep vamos_w5 instance="X1" parameter="rq"', text)
        self.assertIn("\nmodel m_resm sp_resistor tc1=0.0\n", text)
        decks, _names = xyce_decks(spelled(DECK, False))
        self.assertIn("\nX1 bc ab half rq=2000.0\n", decks[0])
        self.assertIn("\nRd vamos_tn3 cd resm 1000.0\n", decks[0])
        for deck in [text] + decks:
            self.assertNotRegex(deck, r"(?<![A-Za-z0-9])_[A-Za-z0-9]*_[0-7]{3}")     # no escaped form anywhere

    def test_a_model_group_entry_keeps_its_index(self):
        """vamos names a model group's entries <base>.<k> (§3.9): the base goes through the rule, the .<k> stays (nch.1
        as before; the entry 1 of n\\.ch as _n_056ch.1)."""
        from vamos.netlist import tables as T
        self.assertEqual(T.deck_card_name("nch.1", "nch"), "nch.1")
        self.assertEqual(T.deck_card_name("n.ch.1", "n.ch"), "_n_056ch.1")
        self.assertEqual(T.deck_card_name("n.ch", None), "_n_056ch")
        self.assertEqual(T.deck_card_name("nch.x", "nch"), "_nch_056x")

    def test_mutual_inductors_and_controlled_sources_name_their_elements_escaped(self):
        deck = ("// t\nsimulator lang=spectre\nV\\<1\\> (a 0) vsource dc=1 mag=1\nL\\.1 (a b) inductor l=1u\n"
                "L\\.2 (c 0) inductor l=1u\nR1 (b 0) resistor r=1k\nR2 (c 0) resistor r=1k\n"
                "K\\.1 mutual_inductor coupling=0.5 ind1=L\\.1 ind2=L\\.2\n"
                "F1 (d 0) cccs probe=V\\<1\\> gain=2\nR3 (d 0) resistor r=1k\nop1 dc\n")
        text, _names = vacask_deck(deck)
        self.assertIn('ind1="_L_0561" ind2="_L_0562"', text)
        self.assertIn('ctlinst="_V_0741_076"', text)
        decks, _names = xyce_decks(deck)
        self.assertIn("\nk_K_0561 l_L_0561 l_L_0562 0.5\n", decks[0])
        self.assertIn("\nF1 d 0 v_V_0741_076 2.0\n", decks[0])


class TestDottedPaths(unittest.TestCase):
    """An IR path joins Spectre names with '.', and a Spectre name may hold a '.' itself: a split follows the netlist
    (tables.split_path).  The escaped-name trial of phase 2e: a subckt instance X\\.1 was cut at its own '.', so
    signals.resolve flattened none of its body (`save X.1.m.n matches no node or instance; not saved')."""

    SUB = ("// t\nsimulator lang=spectre\nV1 (a 0) vsource dc=1\nX\\.1 (a 0) cell m=2\nsubckt cell (p q)\n"
           "  R1 (p m\\.n) resistor r=1k\n  R2 (m\\.n q) resistor r=1k\nends cell\n")

    def test_signals_flatten_a_dotted_subckt_instance(self):
        for engine in ("vacask", "xyce"):
            with self.subTest(engine=engine):
                _nl, plan, _nl2, sm, notes = resolved(self.SUB + "op1 dc\nsave X\\.1.m\\.n X\\.1.R1:1 X\\.1.p\n", engine)
                self.assertFalse([n for n in notes if n.severity in ("error", "warning")], notes)
                got = [name for _ref, name, _t in sm.per_step["vamos_a1"]]
                self.assertEqual(got, ["X.1.m.n", "X.1.R1:1", "X.1.p"])

    def test_targets_inside_a_dotted_subckt_instance(self):
        from vamos.netlist import plan as PL
        from vamos.spectre.job import Settings
        nl = parse_deck(self.SUB + "a1 alter dev=X\\.1.R1 param=r value=2k\nop1 dc\n"
                                   "d1 dc dev=X\\.1.R2 param=r values=[1k 2k]\n")
        for engine in ("vacask", "xyce"):
            plan = PL.build(nl, engine, Settings())
            targets = [a.args.get("target") for a in plan.actions if a.op == "alter"]
            self.assertEqual(targets, [("instance", "X.1.R1", "r")], engine)

    def test_vacask_reads_the_x_lines_of_a_dotted_path(self):
        """run_chain: the m of R1 inside X.1 (m=2) is VACASK's device $mfactor, which also carries X.1's m: refused, as
        for an X1 (the split cut X.1 at its '.', found no X line and printed the sweep, wrong by the factor 2)."""
        with self.assertRaises(Exception) as cm:
            vacask_deck(self.SUB + "d1 dc dev=X\\.1.R1 param=m values=[1 2]\n")
        self.assertIn("also carries the multiplier of the enclosing instance X.1 (m=2.0)", str(cm.exception))

    def test_two_paths_of_one_name_are_an_error(self):
        """A top-level node b\\.c and the node c of an instance b have one IR path, b.c: the outputs could carry one of
        them only; an instance R\\.1 and the instance 1 of an X instance R likewise."""
        from vamos.notes import ERROR
        deck = ("// t\nsimulator lang=spectre\nV1 (b\\.c 0) vsource dc=1\nb (b\\.c 0) cell\nsubckt cell (p q)\n"
                "  R1 (p c) resistor r=1k\n  R2 (c q) resistor r=1k\nends cell\nop1 dc\n")
        _nl, _plan, _nl2, _sm, notes = resolved(deck, "vacask")
        errs = [n.message for n in notes if n.severity == ERROR]
        self.assertTrue(any("have one hierarchical name, b.c" in m for m in errs), errs)
        from vamos.netlist import plan as PL
        from vamos.spectre.job import Settings
        deck = ("// t\nsimulator lang=spectre\nV1 (a 0) vsource dc=1\nR\\.1 (a 0) resistor r=1k\nR (a 0) cell\n"
                "subckt cell (p q)\n  1 (p q) resistor r=1k\nends cell\nop1 dc\n")
        with self.assertRaises(Exception) as cm:
            PL.build(parse_deck(deck), "vacask", Settings())
        self.assertIn("have one hierarchical name, R.1", str(cm.exception))


class TestStateFiles(TempDir):
    """A state file holds Spectre names; a character its line format would read otherwise takes a backslash (a blank,
    #, \\, a node's :), and the reader takes a backslash as the netlist does (the escaped-name trial of phase 2e: a
    node `a b' was the line `a b 0.2', which the reader refused)."""

    def test_round_trip(self):
        from vamos.output import statefile
        nodes = {"a b": 0.25, "x#1": 1.0, "a:b": 2.0, "b.c": 3.0, "net<0>": 4.0, "back\\slash": 5.0, "_b_056c": 6.0,
                 "X.1.m.n": 7.0, "n\u00e9": 8.0, "tab\tbed": 9.0}
        currents = {"V1:p": 1e-3, "V a:p": 2e-3, "X.1.V:2:p": 3e-3}
        p = os.path.join(self.tmp, "x.ns")
        statefile.write(p, nodes, currents, "c.scs", "op1", "1:02:03 AM, oct 19, 2026")
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        for line in ("a\\ b 0.25", "x\\#1 1", "a\\:b 2", "b.c 3", "net<0> 4", "back\\\\slash 5", "_b_056c 6",
                     "X.1.m.n 7", "n\u00e9 8", "tab\\\tbed 9", "V1:p 0.001", "V\\ a:p 0.002", "X.1.V\\:2:p 0.003"):
            self.assertIn("\n" + line + "\n", text, line)
        self.assertEqual(statefile.read(p), (nodes, currents))

    def test_netlist_spellings_read(self):
        from vamos.output import statefile
        p = self.write("h.ic", "# by hand\nnet\\<0\\> 1\nb\\.c 0.5 # a comment\nX\\.1.m\\.n 0.25\nV\\<1\\>:p 1e-3\n")
        self.assertEqual(statefile.read(p), ({"net<0>": 1.0, "b.c": 0.5, "X.1.m.n": 0.25}, {"V<1>:p": 1e-3}))
        with self.assertRaises(ValueError):
            statefile.read(self.write("bad.ic", "a b 0.5\n"))                          # an unescaped blank


class TestEngineText(unittest.TestCase):
    """The engines' messages name the netlist's names (the deck printed them escaped)."""

    def test_vacask(self):
        from vamos.spectre import run_vacask
        r = types.SimpleNamespace(by_id={}, levels={})
        got = run_vacask._Run.translate(r, "Node '_X_0561:_m_056n' not found in 'vamos_a1'.")
        self.assertEqual(got, "Node 'X.1:m.n' not found in 'vamos_a1'.")

    def test_xyce(self):
        from vamos.spectre import run_xyce
        nl = parse_deck(spelled(DECK, True))
        r = types.SimpleNamespace(nl=nl, _printed=None)
        text = ("Netlist warning: Voltage Node (_C_055D) connected to only 1 device Terminal; device R_R_0741_076, "
                "X_X_0561:R1 and R__137R_1370741_137076 at /work/n.run._1_0561b/vamos_a1.cir")
        self.assertEqual(run_xyce._Runner.unprint(r, text),
                         "Netlist warning: Voltage Node (C-D) connected to only 1 device Terminal; device R<1>, "
                         "X.1:R1 and _R_0741_076 at /work/n.run._1_0561b/vamos_a1.cir")


def _psf_signals(path, esc=False):
    import psfcheck
    p = psfcheck.read(path, escchars=esc)
    if p.points:
        return {n: p.column(n) for n in p.names()}
    return {n: [p.value(n)] for n in p.names()}


def _close(a, b):
    if isinstance(a, dict) or isinstance(b, dict):
        return isinstance(a, dict) and isinstance(b, dict) and set(a) == set(b) and all(_close(a[k], b[k]) for k in a)
    try:
        a, b = complex(a), complex(b)
    except (TypeError, ValueError):
        return a == b
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b)) + 1e-18


class TestRuns(TempDir):
    """Through bin/vamos on both engines: every odd name kind (NAMES), at the top level and inside a subckt, saved,
    probed, swept, altered, a noise output and input, in the state files written and read: every output (the
    results' files, signals and values, the state files) equals that of the same deck with plain names, apart from
    the names; and the messages name the netlist's names."""

    def spectre(self, sub, text, engine, files=None, args=()):
        d = os.path.join(self.tmp, sub)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "n.scs"), "w", encoding="utf-8") as fh:
            fh.write(text)
        for fn, body in (files or {}).items():
            with open(os.path.join(d, fn), "w", encoding="latin-1") as fh:
                fh.write(body)
        env = {k: v for k, v in os.environ.items() if not k.endswith("_DEFAULTS") and k != "VAMOS_ANALOG"}
        r = run([LAUNCHER, "-spectre", "--vamos-analog=" + engine, "n.scs"] + list(args), cwd=d, env=env, timeout=1200)
        return d, r

    def same(self, engine, sub, template, files=None, args=(), esc=False, read_back=None):
        """The odd deck and its plain twin: equal exit statuses, results, state files."""
        out = {}
        for odd in (True, False):
            kind = "odd" if odd else "plain"
            fs = {fn: spelled(body, odd) for fn, body in (files or {}).items()}
            text = spelled(template, odd)
            if read_back is not None:
                text = text % tuple(os.path.join(self.tmp, "%s_%s" % (read_back, kind), f) for f in ("op1.ns", "tr1.ic"))
            d, r = self.spectre("%s_%s" % (sub, kind), text, engine, fs, args)
            out[odd] = (d, r)
        (do, ro), (dp, rp) = out[True], out[False]
        self.assertEqual(ro.returncode, rp.returncode, "%s %s:\n%s\n----\n%s" % (engine, sub, ro.stdout[-3000:],
                                                                                 rp.stdout[-3000:]))
        self.assertEqual(ro.returncode, 0, ro.stdout[-3000:])
        fo, fp = sorted(os.listdir(os.path.join(do, "n.raw"))), sorted(os.listdir(os.path.join(dp, "n.raw")))
        self.assertEqual(fo, fp)
        for f in fp:
            if f == "logFile":
                continue
            so = _psf_signals(os.path.join(do, "n.raw", f), esc)
            sp = {to_odd(k): v for k, v in _psf_signals(os.path.join(dp, "n.raw", f), esc).items()}
            self.assertEqual(sorted(so), sorted(sp), (engine, sub, f))
            for k in so:
                self.assertEqual(len(so[k]), len(sp[k]), (engine, sub, f, k))
                self.assertTrue(all(_close(x, y) for x, y in zip(so[k], sp[k])), (engine, sub, f, k, so[k][:3], sp[k][:3]))
        from vamos.output import statefile
        for sf in ("op1.ns", "tr1.ic"):
            po, pp = os.path.join(do, sf), os.path.join(dp, sf)
            self.assertEqual(os.path.exists(po), os.path.exists(pp), sf)
            if os.path.exists(po):
                no, co = statefile.read(po)
                np_, cp = statefile.read(pp)
                self.assertEqual({k: v for k, v in list(no.items()) + list(co.items())},
                                 {to_odd(k): v for k, v in list(np_.items()) + list(cp.items())}, (engine, sub, sf))
        return ro.stdout, rp.stdout

    def run_every_kind(self, engine):
        out, _plain = self.same(engine, "a", DECK)
        self.assertNotIn("Warning from", out)
        with open(os.path.join(self.tmp, "a_odd", "op1.ns"), encoding="utf-8") as fh:
            self.assertIn("\na\\ b ", fh.read())                                  # the blank escaped in the file
        self.same(engine, "b", READ_BACK, read_back="a")
        self.same(engine, "c", READ_BACK.replace("%s", "hand.ns", 1).replace("%s", "hand.ic", 1),
                  files={"hand.ns": HAND, "hand.ic": HAND})
        self.same(engine, "e", DECK, args=("+escchars",), esc=True)
        out, _plain = self.same(engine, "y", VA_DECK, files={"vres.va": VRES_VA})
        if engine == "vacask":
            self.same(engine, "yv", VA_DECK.replace("op1 dc\n", "op1 dc\n" + VA_ALTERS), files={"vres.va": VRES_VA})

    @needs_vacask
    def test_vacask(self):
        self.run_every_kind("vacask")

    @needs_xyce
    def test_xyce(self):
        self.run_every_kind("xyce")

    @needs_xyce
    def test_xyce_s_messages_name_the_netlist_s_names(self):
        deck = READ_BACK.replace('op4 dc readns="%s"\ntr2 tran stop=1u readic="%s" ic=all',
                                 "Rdg ({DASH} d\\-angle) resistor r=1k\nop6 dc")
        _d, r = self.spectre("w", spelled(deck, True), "xyce")
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertIn("Xyce: Netlist warning: Voltage Node (D-ANGLE) connected to only 1 device Terminal", r.stdout)


if __name__ == "__main__":
    unittest.main()
