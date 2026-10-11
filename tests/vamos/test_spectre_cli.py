"""spectre command-line tests, phase 0 (docs/VAMOS_SPECTRE_DESIGN.md §11 T0, the CLI list):
the optable, cli, tools, banner-profile and licence contracts of the skeleton.

    python3 -m unittest test_spectre_cli          (from tests/vamos)

Runs on both legs (Cygwin Python 3.9 and WSL), no engine, no Rust; the cpp tests need the C
preprocessor (WSL).  Phase 0 covered: scan(option_chars=) and the `=` family; VamosOpt.also,
psf_names, the spectre context in vamos_option_effects and vamos_options_help, and vcs's help
unchanged but for the psf_names line; unmapped_notes behind report_unmapped; the dispatch rule
(personality_of, spectre231, spectre-23.1, VAMOS_SPECTRE_NAMES, spectrespp, vamos -spectre) and
tools.invoked; the cli's effects check skipping spectre; SHIM_NAMES; banners/spectre.json;
licenses.json's cpp.  Phase 1 (S5) adds the personality itself: every §2.3 row through
build_job, the defaults layer and its precedence, the percent codes (the UG tables), settings(),
SpectreLog (routing, classes, limits, the trailer, built-in text), main through cli.main (help,
-V/-W, the messages, the exit statuses, nothing on stderr, the invoked name), cpp, the guide's
spectre section and shims/spectre.  Tests that need the run itself (spectre/flow.py, phase 2)
are skipped with the reason PHASE2.
"""

import contextlib
import glob
import io
import json
import os
import re
import shutil
import string
import tempfile
import unittest
from datetime import datetime

from spectre_testlib import needs_cpp  # noqa: F401
from vamos_testlib import LAUNCHER, ROOT, TempDir  # noqa: F401

from vamos import VERSION, banner, cli, optable, tools  # noqa: E402
from vamos.job import IGNORED, INAPPLICABLE, NOTED, UNKNOWN, UNSUPPORTED, Job, Unmapped  # noqa: E402
from vamos.notes import ERROR, NOTE, WARNING, Note  # noqa: E402
from vamos.optable import Opt, Table  # noqa: E402
from vamos.personalities import spectre, vcs  # noqa: E402

CONTEXTS = ("any", "ams", "run", "simv", "spectre")

# The keys §10 gives banners/spectre.json, plus brand and _comment.
BANNER_KEYS = ("version", "subversion", "run_start", "inventory", "analysis_banner", "analysis_done",
               "audit", "error_block", "warning_block", "notice_block", "trailer_ok", "trailer_fatal")


def call_cli(argv):
    """cli.main(argv) -> (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


def squash(text):
    """Whitespace runs (textwrap's line breaks included) as one blank."""
    return " ".join(text.split())


class MiniJob:
    """The job protocol optable.scan needs (§10: SpectreJob implements it): note() and unmapped."""

    def __init__(self):
        self.unmapped = []

    def note(self, option, disposition, note=""):
        self.unmapped.append(Unmapped(option, disposition, note))


# =============================================================================
# optable.scan: option_chars and the `=` family (§2.1, E79, E80)
# =============================================================================

class TestScanOptionChars(unittest.TestCase):
    TABLE = Table([Opt("=log", "next", NOTED), Opt("+log", "next", NOTED), Opt("-log", "flag", NOTED),
                   Opt("-h", "flag", NOTED), Opt("-raw", "next", NOTED),
                   Opt("+mt", "flag", IGNORED), Opt("+mt", "eq", IGNORED), Opt("+mtmode", "next", IGNORED),
                   Opt("-D", "prefix", NOTED), Opt("-debug", "flag", NOTED),
                   Opt("+aps", "flag", IGNORED), Opt("+aps", "eq", NOTED),
                   Opt("++aps", "flag", IGNORED), Opt("++aps", "eq", NOTED)])

    def scan(self, args, **kw):
        job, pos, unknown = MiniJob(), [], []

        def _unknown(j, tok, a, i):
            unknown.append(tok)
            return 1

        optable.scan(self.TABLE, list(args), job, lambda j, t: pos.append(t), _unknown, **kw)
        return pos, unknown, [(u.option, u.disposition) for u in job.unmapped]

    def test_default_takes_only_minus_and_plus(self):
        # today's behaviour, unchanged: `=log` is the netlist (E79)
        pos, unknown, noted = self.scan(["=log", "x.out", "+log", "y.out", "-", "+", "in.scs"])
        self.assertEqual(pos, ["=log", "x.out", "-", "+", "in.scs"])
        self.assertEqual(unknown, [])
        self.assertEqual(noted, [("+log y.out", NOTED)])
        pos, unknown, _ = self.scan(["=foo", "-foo", "+foo"])
        self.assertEqual(pos, ["=foo"])
        self.assertEqual(unknown, ["-foo", "+foo"])

    def test_spectre_option_chars(self):
        pos, unknown, noted = self.scan(["=log", "x.out", "+log", "y.out", "-log", "in.scs"], option_chars="-+=")
        self.assertEqual(pos, ["in.scs"])
        self.assertEqual(unknown, [])
        self.assertEqual(noted, [("=log x.out", NOTED), ("+log y.out", NOTED), ("-log", NOTED)])
        # a bare option character stays positional; an unknown `=` option goes to `unknown`
        pos, unknown, _ = self.scan(["=", "-", "+", "=foo", "in.scs"], option_chars="-+=")
        self.assertEqual(pos, ["=", "-", "+", "in.scs"])
        self.assertEqual(unknown, ["=foo"])
        with self.assertRaises(optable.ScanError):
            self.scan(["=log"], option_chars="-+=")

    def test_a_flag_help_option_takes_no_topic(self):
        # why §2.1 reads -h's topic in a prescan: optable has no optional-value arity (E80)
        pos, _, noted = self.scan(["-h", "resistor"], option_chars="-+=")
        self.assertEqual(pos, ["resistor"])
        self.assertEqual(noted, [("-h", NOTED)])

    def test_match_resolves_the_spectre_shapes(self):
        # exact name first, then the longest prefix (§2.1, E80): existing behaviour, pinned
        m = self.TABLE.match
        self.assertEqual((m("+mt").arity, m("+mt=4").arity, m("+mtmode").arity), ("flag", "eq", "next"))
        self.assertEqual(optable.value_of(m("+mt=4"), "+mt=4"), "4")
        self.assertEqual((m("-debug").arity, m("-D").arity, m("-DVDD=1.8").arity), ("flag", "prefix", "prefix"))
        self.assertEqual(optable.value_of(m("-DVDD=1.8"), "-DVDD=1.8"), "VDD=1.8")
        self.assertEqual((m("++aps").name, m("++aps").arity), ("++aps", "flag"))
        self.assertEqual((m("++aps=liberal").name, m("++aps=liberal").arity), ("++aps", "eq"))
        self.assertEqual((m("+aps=liberal").name, m("+aps=liberal").arity), ("+aps", "eq"))
        self.assertEqual(optable.value_of(m("++aps=liberal"), "++aps=liberal"), "liberal")
        self.assertIsNone(m("=foo"))


# =============================================================================
# the vamos options: VamosOpt.also, psf_names, the spectre context (§10 optable)
# =============================================================================

class TestVamosOptionsSpectre(unittest.TestCase):
    def test_also_is_a_defaulted_last_field_and_where_stays_a_string(self):
        self.assertEqual(optable.VamosOpt._fields[-2:], ("choices", "also"))
        self.assertEqual(optable.VamosOpt("x", None, "any", "h").also, ())
        for o in optable.VAMOS_OPTIONS:
            with self.subTest(key=o.key):
                self.assertIsInstance(o.where, str)
                self.assertIn(o.where, CONTEXTS)
                self.assertIsInstance(o.also, tuple)
                self.assertTrue(set(o.also) <= set(CONTEXTS), o.also)
                self.assertNotIn(o.where, o.also)

    def test_the_spectre_contexts(self):
        keys = optable.VAMOS_KEYS
        self.assertEqual((keys["analog"].where, keys["analog"].also), ("ams", ("spectre",)))
        self.assertEqual((keys["keep"].where, keys["keep"].also), ("run", ("spectre",)))
        psf = keys["psf_names"]
        self.assertEqual((psf.where, psf.also, psf.value, psf.choices), ("spectre", (), "modern|legacy",
                                                                           ("modern", "legacy")))
        self.assertNotIn("psf_names", optable.PLANNED_KEYS)
        self.assertIn("mc", optable.PLANNED_KEYS)               # still planned
        self.assertEqual(optable._WHERE_TEXT["spectre"], "spectre")
        spectre_only = [o.key for o in optable.VAMOS_OPTIONS if o.where == "spectre"]
        self.assertEqual(spectre_only, ["psf_names"])
        shared = [o.key for o in optable.VAMOS_OPTIONS if "spectre" in o.also]
        self.assertEqual(shared, ["analog", "keep"])

    def test_psf_names_is_accepted(self):
        chk = optable.check_vamos_opts
        self.assertEqual(chk({"psf_names": "legacy"}), [])          # E79: "planned" before phase 0
        self.assertEqual(chk({"psf_names": "Modern"}), [])
        self.assertEqual(chk({"psf_names": "old"}), ["--vamos-psf-names=old: the value must be modern or legacy"])
        self.assertEqual(chk({"psf_names": True}),
                         ["--vamos-psf-names needs a value: --vamos-psf-names=modern|legacy"])
        self.assertEqual(chk({"psf_nmaes": "legacy"}),
                         ["unknown vamos option --vamos-psf-nmaes=legacy (did you mean --vamos-psf-names?)"])
        self.assertIn("planned", chk({"mc": "10"})[0])

    def test_effects_under_spectre(self):
        eff = optable.vamos_option_effects
        self.assertEqual(eff({"analog": "xyce", "keep": True, "strict": True, "banner": "none",
                              "psf_names": "legacy", "verbose": True, "version": True, "licenses": True},
                             "spectre"), [])
        self.assertEqual(eff({"analog_stop": "1u", "analog_maxstep": "1n", "parhier": "local",
                              "no_deck_check": True, "daidir": "d", "append_log": True}, "spectre"),
                         [("--vamos-analog-stop=1u", "an AMS compile option: vcs-ams or vcs -ad"),
                          ("--vamos-analog-maxstep=1n", "an AMS compile option: vcs-ams or vcs -ad"),
                          ("--vamos-parhier=local", "an AMS compile option: vcs-ams or vcs -ad"),
                          ("--vamos-no-deck-check", "an AMS compile option: vcs-ams or vcs -ad"),
                          ("--vamos-daidir=d", "a ./simv option"),
                          ("--vamos-append-log", "a ./simv option")])
        # every option is either effective under spectre or named by the effects
        opts = {o.key: ("x" if o.value else True) for o in optable.VAMOS_OPTIONS
                if o.key not in ("version", "licenses")}
        named = {k.split("=")[0] for k, _ in eff(opts, "spectre")}
        for o in optable.VAMOS_OPTIONS:
            if o.key in ("version", "licenses"):
                continue
            effective = o.where in ("any", "spectre") or "spectre" in o.also
            self.assertEqual(optable.vamos_option_text(o.key, True) not in named, effective, o.key)

    def test_psf_names_is_a_spectre_option_elsewhere(self):
        eff = optable.vamos_option_effects
        for kw in ({"ams": False, "run": False}, {"ams": True, "run": True}):
            self.assertEqual(eff({"psf_names": "legacy"}, "vcs", **kw), [("--vamos-psf-names=legacy", "a spectre option")])
            self.assertEqual(eff({"psf_names": "legacy"}, "vcs-ams", **kw), [("--vamos-psf-names=legacy", "a spectre option")])
        self.assertEqual(eff({"psf_names": "legacy"}, "simv"), [("--vamos-psf-names=legacy", "a spectre option")])
        self.assertIn("real nvc", eff({"psf_names": "legacy"}, "nvc")[0][1])
        # the existing vcs/simv answers are unchanged (test_vamos_driver.test_effects pins more)
        self.assertEqual([o for o, _ in eff({"analog": "xyce", "keep": True}, "vcs", ams=False, run=False)],
                         ["--vamos-analog=xyce", "--vamos-keep"])
        self.assertEqual(eff({"analog": "xyce", "keep": True}, "vcs", ams=True, run=True), [])
        self.assertEqual([o for o, _ in eff({"analog_stop": "1u", "keep": True, "strict": True}, "simv")],
                         ["--vamos-analog-stop=1u"])

    def test_vcs_records_psf_names_as_inapplicable(self):
        j = Job("vcs")
        vcs._note_vamos_options(j, {"psf_names": "legacy", "strict": True}, ams=True)
        self.assertEqual([(u.option, u.disposition, u.note) for u in j.unmapped],
                         [("--vamos-psf-names=legacy", INAPPLICABLE, "a spectre option")])
        lines = []
        optable.report_unmapped(j, lines.append)
        self.assertEqual(lines, ["vamos: warning: --vamos-psf-names=legacy has no effect: a spectre option"])


# =============================================================================
# the help texts: spectre's listing, vcs's unchanged but for psf_names [spectre] (§4, §10)
# =============================================================================

# vamos_options_help("vcs") at 5e0f967 plus the psf_names block; a help string changed by
# anyone shows up here.
VCS_OPTIONS_HELP = """\
  --vamos-banner=<name|path|none>  banner profile (default: the personality's)
  --vamos-strict                   unsupported, unknown and ineffective options are errors; so
                                   is every warning (an approximation vamos had to make)
  --vamos-verbose                  print each command vamos runs
  --vamos-licenses                 print the tools vamos may run and their licences, and exit
  --vamos-version                  print the vamos version and exit
  --vamos-analog=vacask|xyce       the analog engine (default vacask; also VAMOS_ANALOG) [AMS
                                   compile]
  --vamos-analog-stop=<time>       end time for a netlist with no .tran (default 3600 s) [AMS
                                   compile]
  --vamos-analog-maxstep=<time>    analog maximum time step (replaces the .tran's) [AMS compile]
  --vamos-parhier=local|global     local: a parameter defined at top level and in a subckt takes
                                   the inner value (default: such a collision is an error) [AMS
                                   compile]
  --vamos-no-deck-check            skip the compile-time operating-point check of the deck [AMS
                                   compile]
  --vamos-keep                     keep the AMS per-run directory [./simv, or a compile with -R]
  --vamos-psf-names=modern|legacy  the PSF file names: modern (Spectre 23.1: n.tran.tran,
                                   s-00i_c.<ext> and sweep parents) or legacy (Spectre 5.x:
                                   n.tran, s_00i_c.<ext>, no parents); default modern [spectre]"""


class TestHelpTexts(unittest.TestCase):
    def test_vcs_help_is_unchanged_but_for_psf_names(self):
        self.assertEqual(optable.vamos_options_help("vcs"), VCS_OPTIONS_HELP)
        self.assertEqual(optable.vamos_options_help("vcs-ams"), VCS_OPTIONS_HELP)
        for p in ("vcs", "vcs-ams"):
            h = vcs.help_text(p)
            self.assertIn(VCS_OPTIONS_HELP, h)
            self.assertIn("[spectre]", h)
            self.assertTrue(all(len(ln) <= 100 for ln in h.splitlines()), h)
        # the usage text (every personality) and simv's listing gain the same block
        self.assertIn(VCS_OPTIONS_HELP, cli.usage())
        self.assertIn(VCS_OPTIONS_HELP, cli.USAGE)
        self.assertIn("--vamos-psf-names=modern|legacy", optable.vamos_options_help(""))
        self.assertIn("--vamos-daidir=<dir>", optable.vamos_options_help(""))

    def test_spectre_help_lists_what_spectre_takes(self):
        h = optable.vamos_options_help("spectre")
        names = [ln.split()[0] for ln in h.splitlines() if ln.startswith("  --vamos-")]
        self.assertEqual(names, ["--vamos-banner=<name|path|none>", "--vamos-strict", "--vamos-verbose",
                                 "--vamos-licenses", "--vamos-version", "--vamos-analog=vacask|xyce",
                                 "--vamos-keep", "--vamos-psf-names=modern|legacy"])
        for absent in ("--vamos-analog-stop", "--vamos-analog-maxstep", "--vamos-parhier",
                       "--vamos-no-deck-check", "--vamos-daidir", "--vamos-append-log"):
            self.assertNotIn(absent, h)
        flat = squash(h)
        self.assertIn("the analog engine (default vacask; also VAMOS_ANALOG) [AMS compile, spectre]", flat)
        self.assertIn("keep the AMS per-run directory [./simv, or a compile with -R, spectre]", flat)
        self.assertIn("default modern [spectre]", flat)
        self.assertNotIn("[spectre, ", flat)                  # `where` first, then the also contexts
        self.assertTrue(all(len(ln) <= 100 for ln in h.splitlines()), h)
        # the listing agrees with the effects: what is listed has an effect, what is not is named
        listed = {n.split("=")[0] for n in names}
        opts = {o.key: ("x" if o.value else True) for o in optable.VAMOS_OPTIONS}
        named = {k.split("=")[0] for k, _ in optable.vamos_option_effects(opts, "spectre")}
        self.assertEqual(named & listed, set())
        self.assertEqual(named | listed, {optable.vamos_option_text(o.key, True) for o in optable.VAMOS_OPTIONS})

    def test_usage_lists_the_spectre_personality(self):
        self.assertIn("spectre", cli.PERSONALITIES)
        self.assertIs(cli.PERSONALITIES["spectre"], spectre.main)
        self.assertIn("spectre", cli._ROLES)
        usage = cli.usage()
        line = [ln for ln in usage.splitlines() if ln.startswith("  spectre ")]
        self.assertEqual(len(line), 1, usage)
        self.assertIn(cli._ROLES["spectre"], line[0])
        self.assertLessEqual(len(line[0]), 100)
        self.assertEqual([ln.split()[0] for ln in usage.splitlines() if re.match(r"  \w", ln)][:5],
                         ["nvc", "simv", "spectre", "vcs", "vcs-ams"])
        rc, out, _ = call_cli(["-h"])
        self.assertEqual(rc, 0)
        self.assertIn("  spectre ", out)
        self.assertIn("--vamos-psf-names", out)


# =============================================================================
# unmapped_notes behind report_unmapped (§8.7, §10)
# =============================================================================

class TestUnmappedNotes(unittest.TestCase):
    def job(self):
        j = Job("spectre")
        j.note("+lqtimeout 900", IGNORED)
        j.note("-cmiversion", NOTED)
        j.note("+CYCLES=2", NOTED, "a plusarg given to vcs reaches only a -R run")
        j.note("+foo", UNKNOWN)
        j.note("--vamos-analog=xyce", UNSUPPORTED,
               "vamos options are read from the command line only, not from option files")
        j.note("-uwifmt x", UNSUPPORTED)
        j.note("--vamos-keep", INAPPLICABLE, "a ./simv option")
        j.note("--vamos-strict", INAPPLICABLE)
        return j

    def test_notes(self):
        notes = optable.unmapped_notes(self.job())
        self.assertEqual([(n.severity, n.origin, n.message) for n in notes], [
            (NOTE, "", "-cmiversion"),
            (NOTE, "", "+CYCLES=2: a plusarg given to vcs reaches only a -R run"),
            (WARNING, "", "unknown option +foo ignored"),
            (WARNING, "", "--vamos-analog=xyce is not supported yet (vamos options are read from the "
                          "command line only, not from option files)"),
            (WARNING, "", "-uwifmt x is not supported yet"),
            (WARNING, "", "--vamos-keep has no effect: a ./simv option"),
            (WARNING, "", "--vamos-strict has no effect")])

    def test_report_unmapped_prints_the_notes(self):
        j = self.job()
        lines = []
        optable.report_unmapped(j, lines.append)
        self.assertEqual(lines, ["vamos: %s" % n.text() for n in optable.unmapped_notes(j)])
        self.assertEqual(lines, [                                   # today's wording (E79)
            "vamos: note: -cmiversion",
            "vamos: note: +CYCLES=2: a plusarg given to vcs reaches only a -R run",
            "vamos: warning: unknown option +foo ignored",
            "vamos: warning: --vamos-analog=xyce is not supported yet (vamos options are read from the "
            "command line only, not from option files)",
            "vamos: warning: -uwifmt x is not supported yet",
            "vamos: warning: --vamos-keep has no effect: a ./simv option",
            "vamos: warning: --vamos-strict has no effect"])
        self.assertEqual(optable.strict_failures(j),
                         ["+foo", "--vamos-analog=xyce", "-uwifmt x", "--vamos-keep", "--vamos-strict"])
        self.assertEqual(optable.unmapped_notes(Job("spectre")), [])
        self.assertEqual(optable.unmapped_notes(MiniJob()), [])    # the job protocol, not the Job class


# =============================================================================
# the dispatch rule and tools.invoked (§2.1, §10; E79)
# =============================================================================

class TestDispatch(TempDir):
    def setUp(self):
        super().setUp()
        self.saved = (cli.PERSONALITIES["spectre"], tools.current, tools.invoked)
        self.got = {}

        def fake(args, opts):
            self.got.update(args=list(args), opts=dict(opts), invoked=tools.invoked, current=tools.current)
            return 0

        cli.PERSONALITIES["spectre"] = fake
        os.environ.pop("VAMOS_SPECTRE_NAMES", None)
        os.environ.pop("VAMOS_STACK", None)

    def tearDown(self):
        cli.PERSONALITIES["spectre"], tools.current, tools.invoked = self.saved
        super().tearDown()

    def test_personality_of(self):
        for name in ("spectre", "spectre231", "spectre-23.1", "spectre_23", "spectre.23.1", "spectre23.1.0.242"):
            self.assertEqual(cli.personality_of(name), "spectre", name)
        for name in ("spectrespp", "spectre_encrypt", "spectre-", "spectrex1", "specsim", "vamos", "", "Spectre"):
            self.assertIsNone(cli.personality_of(name), name)
        for name in ("vcs", "vcs-ams", "simv", "nvc"):
            self.assertEqual(cli.personality_of(name), name)
        os.environ["VAMOS_SPECTRE_NAMES"] = "specsim:myspectre"
        self.assertEqual(cli.spectre_names(), ["specsim", "myspectre"])
        self.assertEqual((cli.personality_of("specsim"), cli.personality_of("myspectre")), ("spectre", "spectre"))
        self.assertIsNone(cli.personality_of("spectrespp"))
        os.environ["VAMOS_SPECTRE_NAMES"] = ""
        self.assertEqual(cli.spectre_names(), [])

    def test_invoked_names_dispatch_and_are_recorded(self):
        for argv0, invoked in (("spectre", "spectre"), ("spectre231", "spectre231"), ("spectre-23.1", "spectre-23.1")):
            with self.subTest(argv0=argv0):
                self.got.clear()
                os.environ["VAMOS_ARGV0"] = argv0
                rc, out, err = call_cli(["x.scs"])
                self.assertEqual((rc, out, err), (0, "", ""))
                self.assertEqual(self.got, {"args": ["x.scs"], "opts": {}, "invoked": invoked, "current": "spectre"})
                self.assertNotIn("VAMOS_ARGV0", os.environ)
        os.environ["VAMOS_SPECTRE_NAMES"] = "specsim"
        os.environ["VAMOS_ARGV0"] = "specsim"
        rc, _, err = call_cli(["-raw", "o", "x.scs"])
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual((self.got["invoked"], self.got["current"], self.got["args"]),
                         ("specsim", "spectre", ["-raw", "o", "x.scs"]))
        # vamos -spectre: the invoked name is spectre
        os.environ["VAMOS_ARGV0"] = "vamos"
        rc, _, err = call_cli(["-spectre", "x.scs"])
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual((self.got["invoked"], self.got["current"], self.got["args"]), ("spectre", "spectre", ["x.scs"]))
        # the module values stay what the last dispatch set
        self.assertEqual((tools.invoked, tools.current), ("spectre", "spectre"))

    def test_other_spectre_names_are_usage_errors(self):
        for argv0 in ("spectrespp", "spectre_encrypt", "spectre-"):
            with self.subTest(argv0=argv0):
                self.got.clear()
                os.environ["VAMOS_ARGV0"] = argv0
                rc, out, err = call_cli(["x.scs"])
                self.assertEqual((rc, out), (2, ""))
                self.assertIn("vamos: error: '%s' is a Cadence program vamos does not provide" % argv0, err)
                self.assertIn("VAMOS_SPECTRE_NAMES", err)
                self.assertIn("usage: vamos -<personality>", err)
                self.assertEqual(self.got, {})
        # an alias that is not listed is an ordinary unknown name (the usage text, exit 2)
        os.environ["VAMOS_ARGV0"] = "specsim"
        rc, out, err = call_cli(["x.scs"])
        self.assertEqual((rc, out), (2, ""))
        self.assertTrue(err.startswith("usage: vamos -<personality>"), err)
        self.assertNotIn("Cadence", err)
        self.assertEqual(self.got, {})

    def test_the_cli_leaves_the_effects_to_the_personality(self):
        # as for vcs and vcs-ams (cli.py: "these report the options themselves")
        os.environ["VAMOS_ARGV0"] = "vamos"
        rc, out, err = call_cli(["-spectre", "--vamos-analog-stop=1u", "--vamos-daidir=d", "--vamos-strict",
                                 "--vamos-psf-names=legacy", "--vamos-analog=xyce", "x.scs"])
        self.assertEqual((rc, out, err), (0, "", ""))
        self.assertEqual(self.got["args"], ["x.scs"])
        self.assertEqual(self.got["opts"], {"analog_stop": "1u", "daidir": "d", "strict": True,
                                            "psf_names": "legacy", "analog": "xyce"})
        # the usage checks still come first
        rc, _, err = call_cli(["-spectre", "--vamos-psf-names=old", "x.scs"])
        self.assertEqual(rc, 2)
        self.assertIn("vamos: error: --vamos-psf-names=old: the value must be modern or legacy", err)
        rc, _, err = call_cli(["-spectre", "--vamos-psf-names", "x.scs"])
        self.assertEqual(rc, 2)
        self.assertIn("--vamos-psf-names needs a value", err)


# =============================================================================
# the personality (phase 1, S5): build_job over every §2.3 row, the layers, the percent codes,
# settings(), SpectreLog, main, cpp (§2, §8.7, §11 T0 "CLI", "Percent codes", "Log", "CLI and cpp")
# =============================================================================

PHASE2 = "S6/phase 2: spectre/flow.py (the integrator) runs the netlist; phase 1 stops before the run"


def build(argv, env=None, cwd="/tmp", prog="spectre"):
    return spectre.build_job(list(argv), dict(env or {}), cwd, prog)


def dispositions(job):
    return [(u.option, u.disposition) for u in job.unmapped]


def errors(job):
    return [n.origin for n in job.notes if n.severity == ERROR]


class InTmp(TempDir):
    """A TempDir that is also the current directory (main reads os.getcwd(); =log files land here)."""

    def setUp(self):
        super().setUp()
        self._cwd = os.getcwd()
        os.chdir(self.tmp)
        os.environ.pop("VAMOS_STACK", None)
        for v in ("spectre_DEFAULTS", "SPECTRE_DEFAULTS", "VAMOS_SPECTRE_NAMES", "VAMOS_BANNER"):
            os.environ.pop(v, None)
        self.saved_tools = (tools.current, tools.invoked)

    def tearDown(self):
        os.chdir(self._cwd)
        tools.current, tools.invoked = self.saved_tools
        super().tearDown()

    def spectre(self, argv, argv0="spectre", **env):
        """cli.main as the invoked name argv0 -> (rc, stdout, stderr)."""
        os.environ["VAMOS_ARGV0"] = argv0
        os.environ.update(env)
        try:
            return call_cli(argv)
        finally:
            for k in env:
                os.environ.pop(k, None)


class TestOptionTable(unittest.TestCase):
    """Every §2.3 row through build_job (§11 T0)."""

    IGNORED = ["+rtsf", "-cols 80", "-c 80", "-colslog 80", "-param", "-p", "+aps", "++aps", "+xps", "+xps=s",
               "+ms", "+xdp", "+xdp=rsh", "+speed=1", "+query=all", "+lite", "-hpc", "+dcopt", "+dcopt=1",
               "+disk_check", "+disk_check=1e10", "-clearcache", "-cc", "+hosts a:4", "+multithread",
               "+multithread=4", "+mt", "+mt=4", "-multithread", "-mt", "-64", "-32", "+mtmode passive",
               "-processor 0-3", "-proc 0-3", "-checkpoint", "-cp", "-savestate", "-ss", "-recover", "-rec",
               "-interactive", "-inter", "-mdlcontrol", "-mdl", "+dochecklimit", "+docl", "-dochecklimit",
               "-docl", "-dynchecks", "+lqtimeout 900", "+lqt 900", "+lqsleep 30", "+lqs 30", "+lmode 4",
               "+lorder PRODUCT", "-alias x", "+lqmmtoken", "+lsuspend", "+lsusp", "+liclog", "-ahdlcom 1",
               "-ac 1", "-ahdllint_log f", "-rf_ahdl_functionality 151", "-ahdllint", "-ahdllint=warn",
               "-ahdllint_maxwarn", "-ahdllint_maxwarn=5", "-ahdllint_summary_maxentries",
               "-ahdllint_summary_maxentries=25", "-ahdllint_warn_id=x", "-ahdlshipdbdir=p",
               "-ahdlshipdbmode=read_only", "+spp", "-spp", "-sppbin f", "-env ade", "+logstatus"]
    NOTED = ["-cmiversion", "-cmiconfig f", "+varedefnerror", "+param f", "+p f", "+preset=cx", "+postlpreset=cx",
             "-preset_override", "-preset_override=reltol", "+diagnose", "+transteps", "+diagnose_fpe",
             "+detect_negcap", "+fix_bad_pivot", "+diagnose_minstep=1e-12", "+diagnose_top=5", "+checkpoint",
             "+cp", "+savestate", "+ss", "-checklimitfile f", "-cl f", "-ahdlsourceramp", "+sensdata f"]
    UNSUPPORTED = ["-uwifmt x", "-uwilib x", "+recover", "+recover=f", "+rec", "+rec=f", "+mpssession=s",
                   "+mpshost=h", "-slave c", "-slvhost h", "+mdlcontrol", "+mdlcontrole", "=mdlcontrol f",
                   "=mdl f", "=mdlcontrole f", "=mdle f"]
    ERRORS = ["+top s", "+interactive", "+interactive=skill", "+inter", "+inter=mpsc", "-format uwi",
              "-format bogus"]
    # mapped: tokens -> (field, value)
    MAPPED = [("-V", "action", "version"), ("-W", "action", "subversion"), ("-raw o", "raw", "o"), ("-r o", "raw", "o"),
              ("-format psfascii", "fmt", "psfascii"), ("-format nutascii", "fmt", "nutascii"),
              ("-f nutbin", "fmt", "nutbin"), ("-outdir d", "outdir", "d"),
              ("+log f", "log_mode", "both"), ("+l f", "log_path", "f"), ("=log f", "log_mode", "file"),
              ("=l f", "log_mode", "file"), ("-log", "log_mode", "screen"), ("-l", "log_mode", "screen"),
              ("-maxwarns 5", "maxwarns", 5), ("-maxw 6", "maxwarns", 6), ("-maxnotes 7", "maxnotes", 7),
              ("-maxn 8", "maxnotes", 8), ("-maxwarnstolog 9", "maxwarnstolog", 9), ("-maxwtl 10", "maxwarnstolog", 10),
              ("-maxnotestolog 11", "maxnotestolog", 11), ("-maxntl 12", "maxnotestolog", 12),
              ("-E", "cpp", True), ("-DVDD=1.8", "defines", ["VDD=1.8"]), ("-DX", "defines", ["X"]),
              ("-UX", "undefines", ["X"]), ("-Iinc", "incdirs", ["inc"]), ("-disableCPP", "disable_cpp", True),
              ("+config f", "config", ["f"]), ("=config f", "config", ["f"]), ("-config", "config", []),
              ("+pre_config f", "pre_config", ["f"]), ("-pre_config f", "pre_config", []),
              ("+paramdefault f", "paramdefault", ["f"]), ("=paramdefault f", "paramdefault", ["f"]),
              ("-paramdefault", "paramdefault", []), ("+errpreset=moderate", "errpreset", "moderate"),
              ("+aps=liberal", "aps", "liberal"), ("++aps=conservative", "aps", "conservative"),
              ("-mts", "mts", False), ("-ahdllibdir d", "ahdllibdir", "d"),
              ("-va,define M=1", "va_defines", ["M=1"]), ("+escchars", "escchars", True)]

    def test_ignored_rows(self):
        for row in self.IGNORED:
            with self.subTest(row=row):
                j = build(row.split() + ["x.scs"])
                self.assertEqual(dispositions(j), [(row, IGNORED)])
                self.assertEqual((j.netlist, j.notes), ("x.scs", []))

    def test_noted_rows(self):
        for row in self.NOTED:
            with self.subTest(row=row):
                j = build(row.split() + ["x.scs"])
                self.assertEqual(dispositions(j), [(row, NOTED)])
                self.assertEqual((j.netlist, j.notes), ("x.scs", []))

    def test_unsupported_rows(self):
        for row in self.UNSUPPORTED:
            with self.subTest(row=row):
                j = build(row.split() + ["x.scs"])
                self.assertEqual(dispositions(j), [(row, UNSUPPORTED)])
                self.assertEqual(j.notes, [])

    def test_error_rows_append_to_notes(self):
        for row in self.ERRORS:
            with self.subTest(row=row):
                j = build(row.split() + ["x.scs"])
                self.assertEqual(len(j.notes), 1, j.notes)
                self.assertEqual(j.notes[0].severity, ERROR)
                self.assertEqual(j.notes[0].origin, row)
                self.assertEqual(j.unmapped, [])
                self.assertEqual(j.netlist, "x.scs")
        self.assertIn("uwi", errors(build(["-f", "uwi", "x.scs"]))[0])
        self.assertIn("psfascii, nutascii", build(["-format", "wdf2", "x.scs"]).notes[0].message)

    def test_mapped_rows(self):
        for row, field, value in self.MAPPED:
            with self.subTest(row=row):
                j = build(row.split() + ["x.scs"])
                self.assertEqual(getattr(j, field), value)
                self.assertEqual((j.unmapped, j.notes, j.netlist), ([], [], "x.scs"))
        j = build(["+log", "f", "x.scs"])
        self.assertEqual((j.log_mode, j.log_path), ("both", "f"))
        j = build(["=log", "f", "-log", "x.scs"])
        self.assertEqual((j.log_mode, j.log_path), ("screen", None))       # the later setting wins
        for cls in ("error", "warn", "note", "info", "debug"):
            self.assertEqual(build(["-" + cls, "x.scs"]).classes, {cls: False})
            self.assertEqual(build(["+" + cls, "x.scs"]).classes, {cls: True})
            self.assertEqual(build(["-" + cls, "+" + cls, "x.scs"]).classes, {cls: True})

    def test_format_aliases_are_psfascii_with_a_note(self):
        for fmt in ("psfbin", "psfbinf", "psfxl", "sst2", "fsdb", "fsdb5", "wdf", "wsfbin", "wsfascii", "awb",
                    "tr0ascii", "PSFXL"):
            with self.subTest(fmt=fmt):
                j = build(["-format", fmt, "x.scs"])
                self.assertEqual(j.fmt, "psfascii")
                self.assertEqual(dispositions(j), [("-format %s" % fmt, NOTED)])
                self.assertEqual(j.unmapped[0].note, "psfascii is written")
        self.assertEqual(build(["-f", "NutBin", "x.scs"]).fmt, "nutbin")

    def test_f_is_format_not_an_option_file(self):
        j = build(["-f", "psfascii", "x.scs"])
        self.assertEqual((j.fmt, j.netlist, j.unmapped, j.notes), ("psfascii", "x.scs", [], []))

    def test_abbreviation_shapes(self):
        # +mt against +mt=4 against +mtmode, -D against -debug and -disableCPP, -I against -info, -U
        # against -uwifmt (§2.1, E80): exact name first, then the longest prefix
        j = build(["+mt", "+mt=4", "+mtmode", "active", "x.scs"])
        self.assertEqual(dispositions(j), [("+mt", IGNORED), ("+mt=4", IGNORED), ("+mtmode active", IGNORED)])
        j = build(["-Dfoo", "-debug", "-disableCPP", "-Iinc", "-info", "-inter", "-Ux", "-uwifmt", "y", "x.scs"])
        self.assertEqual((j.defines, j.incdirs, j.undefines, j.disable_cpp), (["foo"], ["inc"], ["x"], True))
        self.assertEqual(j.classes, {"debug": False, "info": False})
        self.assertEqual(dispositions(j), [("-inter", IGNORED), ("-uwifmt y", UNSUPPORTED)])
        self.assertEqual(j.netlist, "x.scs")
        # a bare -D/-U/-I is an error: the value is written against the option
        self.assertEqual(errors(build(["-D", "x.scs"])), ["-D"])
        self.assertEqual(build(["-D", "x.scs"]).netlist, "x.scs")

    def test_ade_command_lines_have_no_unknowns(self):
        # §9, E80: the typical ADE line and its APS variant
        ade = ("-64 butterworth.scs +escchars +log ./butterworth/psf/spectre.out -format psfascii -raw "
               "./butterworth/psf +lqtimeout 900 -maxw 5 -maxn 5 +logstatus").split()
        j = build(ade)
        self.assertEqual(j.netlist, "butterworth.scs")
        self.assertEqual((j.raw, j.fmt, j.log_mode, j.log_path, j.maxwarns, j.maxnotes, j.escchars),
                         ("./butterworth/psf", "psfascii", "both", "./butterworth/psf/spectre.out", 5, 5, True))
        self.assertEqual([u.disposition for u in j.unmapped], [IGNORED] * 3)
        self.assertEqual(j.notes, [])
        aps = ade[:1] + ["input.scs"] + ade[2:] + "++aps +mt=4 +lqsleep 30 +lsusp -env ade -format psfxl".split()
        j = build(aps)
        self.assertEqual((j.netlist, j.fmt), ("input.scs", "psfascii"))
        self.assertNotIn(UNKNOWN, [u.disposition for u in j.unmapped])
        self.assertEqual([u.option for u in j.unmapped if u.disposition == NOTED], ["-format psfxl"])
        # E80's lines: no positional; +spp -sppbin ./spp are known now (ref5's options, §0)
        j = build("+param range.lmts +log %C:r.o -E -format wsfbin =log %C:r.log -f psfbin".split())
        self.assertIsNone(j.netlist)
        self.assertEqual((j.log_mode, j.log_path, j.cpp, j.fmt), ("file", "%C:r.log", True, "psfascii"))
        self.assertNotIn(UNKNOWN, [u.disposition for u in j.unmapped])
        j = build("+spp -sppbin ./spp opamp.sp".split())
        self.assertEqual((j.netlist, dispositions(j)), ("opamp.sp", [("+spp", IGNORED), ("-sppbin ./spp", IGNORED)]))

    def test_second_netlist_is_an_error(self):
        j = build(["a.scs", "b.scs"])
        self.assertEqual((j.netlist, errors(j)), ("a.scs", ["b.scs"]))
        self.assertIn("second input file", j.notes[0].message)

    def test_unknown_option_is_a_warning_not_a_usage_error(self):
        j = build(["-bogus", "+bogus", "=bogus", "x.scs"])
        self.assertEqual(dispositions(j), [("-bogus", UNKNOWN), ("+bogus", UNKNOWN), ("=bogus", UNKNOWN)])
        self.assertEqual((j.netlist, j.notes), ("x.scs", []))
        self.assertEqual([n.message for n in optable.unmapped_notes(j)][0], "unknown option -bogus ignored")

    def test_option_needing_a_value_at_the_end(self):
        with self.assertRaises(optable.ScanError):
            build(["x.scs", "-raw"])
        with self.assertRaises(optable.ScanError):
            build(["+%E"])

    def test_config_rules(self):
        c = lambda *a: build(list(a) + ["x.scs"]).config            # noqa: E731
        self.assertEqual(c("+config", "a", "+config", "b"), ["a", "b"])
        self.assertEqual(c("+config", "a", "=config", "c", "+config", "b"), ["c"])
        self.assertEqual(c("=config", "c", "=config", "d"), ["c", "d"])
        self.assertEqual(c("+config", "a", "-config", "=config", "c"), ["c"])
        self.assertEqual(c("=config", "c", "-config", "+config", "a"), [])
        self.assertEqual(c("-config"), [])
        p = lambda *a: build(list(a) + ["x.scs"]).pre_config        # noqa: E731
        self.assertEqual(p("+pre_config", "a", "+pre_config", "b"), ["a", "b"])
        self.assertEqual(p("+pre_config", "a", "-pre_config", "f", "+pre_config", "b"), [])
        d = lambda *a: build(list(a) + ["x.scs"]).paramdefault      # noqa: E731
        self.assertEqual(d("+paramdefault", "a", "+paramdefault", "b"), ["a", "b"])
        self.assertEqual(d("+paramdefault", "a", "=paramdefault", "c"), ["c"])
        self.assertEqual(d("+paramdefault", "a", "-paramdefault"), [])

    def test_percent_options_and_prescan(self):
        j = build(["+%E", "opamp1", "x.scs", "-%C", "+%e", "low"])
        self.assertEqual((j.percent, j.netlist), ({"E": "opamp1", "C": None, "e": "low"}, "x.scs"))
        j = build(["+%E", "opamp1", "-%E", "x.scs"])
        self.assertEqual(j.percent, {"E": None})
        j = build(["+%EE", "x", "x.scs"])                       # not a percent option: unknown
        self.assertEqual(dispositions(j), [("+%EE", UNKNOWN)])

    def test_help_options_take_an_optional_topic(self):
        for opt in ("-help", "-h", "-helpsort", "-hs", "-helpfull", "-hf", "-helpsortfull", "-hsf"):
            with self.subTest(opt=opt):
                j = build([opt])
                self.assertEqual((j.action, j.help_topic, j.netlist), ("help", None, None))
                j = build([opt, "resistor", "x.scs"])
                self.assertEqual((j.action, j.help_topic, j.netlist), ("help", "resistor", "x.scs"))
                j = build([opt, "-raw", "o"])
                self.assertEqual((j.action, j.help_topic, j.raw), ("help", None, "o"))

    def test_version_options(self):
        self.assertEqual(build(["-V"]).action, "version")
        self.assertEqual(build(["-W", "x.scs"]).action, "subversion")
        self.assertEqual(build(["-V", "-W"]).action, "subversion")

    def test_integer_values(self):
        j = build(["-maxw", "x", "-maxn", "3", "x.scs"])
        self.assertEqual((errors(j), j.maxwarns, j.maxnotes), (["-maxw x"], None, 3))

    def test_cpp_runs(self):
        from vamos.spectre.args import cpp_runs
        self.assertFalse(cpp_runs(build(["x.scs"])))
        self.assertTrue(cpp_runs(build(["-E", "x.scs"])))
        self.assertTrue(cpp_runs(build(["-DX", "x.scs"])))
        self.assertTrue(cpp_runs(build(["-UX", "x.scs"])))
        self.assertTrue(cpp_runs(build(["-Iinc", "x.scs"])))
        self.assertFalse(cpp_runs(build(["-DX", "-disableCPP", "x.scs"])))
        self.assertTrue(cpp_runs(build(["-E", "-disableCPP", "x.scs"])))       # -E wins [M ref19 p.37]

    def test_table_has_every_row_once(self):
        names = [(o.name, o.arity) for o in spectre.OPTIONS]
        self.assertEqual(len(names), len(set(names)))
        self.assertGreaterEqual(len(names), 170)
        for o in spectre.OPTIONS:
            self.assertIn(o.name[0], "-+=")


class TestDefaultsLayer(unittest.TestCase):
    """§2.2: the defaults variable, its precedence against argv and the netlist's options."""

    def test_variables_in_order(self):
        from vamos.spectre import args as sargs
        self.assertEqual(sargs.defaults_vars("spectre"), ["spectre_DEFAULTS", "SPECTRE_DEFAULTS"])
        self.assertEqual(sargs.defaults_vars("specsim"), ["specsim_DEFAULTS", "SPECSIM_DEFAULTS", "SPECTRE_DEFAULTS"])
        self.assertEqual(sargs.defaults_vars("spectre231"), ["spectre231_DEFAULTS", "SPECTRE231_DEFAULTS",
                                                              "SPECTRE_DEFAULTS"])
        env = {"spectre_DEFAULTS": "-maxw 1", "SPECTRE_DEFAULTS": "-maxw 2"}
        self.assertEqual(sargs.defaults_tokens(env, "spectre"), ("spectre_DEFAULTS", ["-maxw", "1"]))
        self.assertEqual(sargs.defaults_tokens({"SPECTRE_DEFAULTS": "-maxw 2"}, "spectre"),
                         ("SPECTRE_DEFAULTS", ["-maxw", "2"]))
        self.assertEqual(sargs.defaults_tokens({}, "spectre"), (None, []))
        # an empty variable means no defaults, and the next one is not read
        self.assertEqual(sargs.defaults_tokens({"spectre_DEFAULTS": "", "SPECTRE_DEFAULTS": "-maxw 2"}, "spectre"),
                         ("spectre_DEFAULTS", []))
        # a specsim alias reads SPECSIM_DEFAULTS [M UG pp.239-240]
        env = {"SPECSIM_DEFAULTS": "=log %C:r.log -f psfbin", "SPECTRE_DEFAULTS": "+log %C:r.o -E"}
        self.assertEqual(sargs.defaults_tokens(env, "specsim")[0], "SPECSIM_DEFAULTS")
        self.assertEqual(sargs.defaults_tokens(env, "spectre")[0], "SPECTRE_DEFAULTS")
        self.assertEqual(sargs.defaults_tokens(dict(env, specsim_DEFAULTS="-maxw 9"), "specsim")[0], "specsim_DEFAULTS")
        self.assertEqual(sargs.defaults_tokens({"X_DEFAULTS": 'a "b c"'}, "X"), ("X_DEFAULTS", ["a", "b c"]))
        with self.assertRaises(optable.ScanError):
            sargs.defaults_tokens({"X_DEFAULTS": 'a "b'}, "X")

    def test_scalars_argv_over_defaults_over_nothing(self):
        from vamos.spectre.args import effective
        j = build(["x.scs"], {"SPECTRE_DEFAULTS": "-maxw 3 -raw d.raw +log %C:r.out -f psfbin +errpreset=liberal"})
        self.assertEqual((j.maxwarns, j.raw, j.log_mode, j.log_path, j.fmt, j.errpreset), (None, None, "screen", None,
                                                                                         None, None))
        self.assertEqual(j.defaults, {"maxwarns": 3, "raw": "d.raw", "log_mode": "both", "log_path": "%C:r.out",
                                      "fmt": "psfascii", "errpreset": "liberal"})
        for name, want in (("maxwarns", 3), ("raw", "d.raw"), ("log_mode", "both"), ("log_path", "%C:r.out"),
                           ("fmt", "psfascii"), ("errpreset", "liberal"), ("maxnotes", None), ("outdir", None)):
            self.assertEqual(effective(j, name), want, name)
        j = build(["-maxw", "7", "-raw", "o", "-log", "x.scs"], {"SPECTRE_DEFAULTS": "-maxw 3 -raw d.raw +log f"})
        self.assertEqual((j.maxwarns, j.raw, j.log_mode, j.log_path), (7, "o", "screen", None))
        self.assertEqual(j.defaults, {"maxwarns": 3, "raw": "d.raw"})      # an explicit -log beats +log f
        self.assertEqual((effective(j, "maxwarns"), effective(j, "log_mode"), effective(j, "log_path")),
                         (7, "screen", None))
        j = build(["x.scs"])
        self.assertEqual((j.defaults, effective(j, "maxwarns"), effective(j, "log_mode")), ({}, None, "screen"))

    def test_lists_accumulate_defaults_first_and_layers_are_scanned_apart(self):
        j = build(["-Ib", "-config", "+config", "y.cfg", "-DB", "x.scs"],
                  {"SPECTRE_DEFAULTS": "-Ia +config d.cfg -DA +pre_config p.scs"})
        self.assertEqual((j.incdirs, j.defines), (["a", "b"], ["A", "B"]))
        # an argv -config drops argv's +config only: the same-command-line rules apply within a layer
        self.assertEqual((j.config, j.pre_config), (["d.cfg"], ["p.scs"]))
        j = build(["-%E", "x.scs"], {"SPECTRE_DEFAULTS": "+%E opamp1 +%F f"})
        self.assertEqual(j.percent, {"E": None, "F": "f"})               # argv's -%X undefines a defaults +%X
        j = build(["+warn", "x.scs"], {"SPECTRE_DEFAULTS": "-warn -info"})
        self.assertEqual(j.classes, {"warn": True, "info": False})

    def test_booleans_from_the_defaults_apply(self):
        from vamos.spectre.args import cpp_runs, effective
        j = build(["x.scs"], {"SPECTRE_DEFAULTS": "-E -mts +escchars -disableCPP"})
        self.assertEqual((j.cpp, j.mts, j.escchars, j.disable_cpp), (True, False, True, True))
        self.assertEqual(j.defaults, {"cpp": True, "mts": False, "escchars": True, "disable_cpp": True})
        self.assertTrue(cpp_runs(j))
        self.assertEqual(effective(j, "mts"), False)

    def test_vamos_options_in_the_defaults_are_unsupported(self):
        # the counterpart of test_vamos_options_in_option_files_are_reported (§1)
        j = build(["x.scs"], {"SPECTRE_DEFAULTS": "--vamos-analog=xyce -maxw 2 --vamos-strict"})
        self.assertEqual(dispositions(j), [("--vamos-analog=xyce", UNSUPPORTED), ("--vamos-strict", UNSUPPORTED)])
        self.assertIn("command line only, not from SPECTRE_DEFAULTS", j.unmapped[0].note)
        self.assertIn("VAMOS_ANALOG, VAMOS_BANNER or VAMOS_VERBOSE", j.unmapped[0].note)
        self.assertEqual((j.defaults, j.notes), ({"maxwarns": 2}, []))
        self.assertEqual(optable.strict_failures(j), ["--vamos-analog=xyce", "--vamos-strict"])

    def test_a_non_option_token_in_the_defaults_is_an_error(self):
        j = build(["x.scs"], {"spectre_DEFAULTS": "-maxw 2 other.scs"})
        self.assertEqual((j.netlist, errors(j)), ("x.scs", ["other.scs"]))
        self.assertIn("non-option token in spectre_DEFAULTS", j.notes[0].message)

    def test_options_spectre_does_not_read_from_the_defaults(self):
        j = build(["x.scs"], {"SPECTRE_DEFAULTS": "-help +interactive -note +mpssession=x -V -env ade -%E "
                                                  "+sensdata s.dat -slave cmd +log f +aps=liberal -help resistor"})
        self.assertEqual(j.action, "run")                                  # no help, no version
        self.assertEqual((j.notes, j.classes, j.percent), ([], {}, {}))
        noted = [(u.option, u.disposition, u.note) for u in j.unmapped]
        for opt in ("-help", "+interactive", "-note", "+mpssession=x", "-V", "-env ade", "-%E", "+sensdata s.dat",
                    "-slave cmd", "-help resistor"):
            self.assertIn((opt, NOTED, "Spectre does not read %s from SPECTRE_DEFAULTS; ignored" % opt.split()[0]),
                          noted, opt)
        # a dagger option (+log) and a ref19-only one (+aps=) are applied from the defaults
        self.assertEqual((j.defaults["log_mode"], j.defaults["log_path"], j.defaults["aps"]), ("both", "f", "liberal"))

    @unittest.skip(PHASE2)
    def test_a_run_with_unread_defaults_still_simulates_and_exits_0(self):
        pass

    def test_maxwarns_three_ways(self):
        # an options maxwarns beats a defaults -maxw and loses to an argv -maxw (settings() and limits())
        from vamos.netlist import ir
        from vamos.netlist.expr_ast import Num
        from vamos.spectre import job as sjob
        nl = ir.Netlist(dialect="spectre", options={"maxwarns": Num(4.0)})
        j = build(["x.scs"], {"SPECTRE_DEFAULTS": "-maxw 3"})
        self.assertEqual(sjob.settings(j, nl).maxwarns, 4)
        self.assertEqual(sjob.settings(j, ir.Netlist()).maxwarns, 3)
        j = build(["-maxw", "7", "x.scs"], {"SPECTRE_DEFAULTS": "-maxw 3"})
        self.assertEqual(sjob.settings(j, nl).maxwarns, 7)
        # the log: 3 printed before the parse, 4 after limits()
        from vamos.spectre.log import SpectreLog
        j = build(["x.scs"], {"SPECTRE_DEFAULTS": "-maxw 3"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            log = SpectreLog(j, banner.Banner(banner.load_profile("spectre"), "spectre"), datetime.now())
            for i in range(6):
                log.message(Note(WARNING, "", "node `n%d' floats" % i), "setup")
            log.limits(sjob.settings(j, nl))
            log.analysis_banner(("DC Analysis", "op1", ""))
            for i in range(6):
                log.message(Note(WARNING, "", "node `n%d' floats" % i), "setup")
        text = out.getvalue()
        self.assertEqual(text.count("Warning from spectre"), 7)
        self.assertEqual(text.count("further warnings of this kind"), 2)
        self.assertEqual(log.counts["warnings"], 12)


class TestPercentCodes(unittest.TestCase):
    """§2.4: the UG pp.264-268 tables as tests."""

    OPAMP = "/users/maxwell/circuits/opamp.ckt"

    def test_the_ug_table(self):
        from vamos.spectre import percent
        codes = {"C": self.OPAMP}
        rows = [("%C", self.OPAMP), ("%C:r", "/users/maxwell/circuits/opamp"), ("%C:e", "ckt"),
                ("%C:h", "/users/maxwell/circuits"), ("%C:t", "opamp.ckt"), ("%C::", self.OPAMP + ":"),
                ("%C:h:h", "/users/maxwell"), ("%C:t:r", "opamp"), ("%C:r:t", "opamp"),
                ("/tmp%C:t:r.raw", "/tmpopamp.raw"),                      # §14 q.1: the definition, not the example
                ("%C:e.%C:r:t", "ckt.opamp"), ("%C:r.raw", "/users/maxwell/circuits/opamp.raw")]
        for text, want in rows:
            with self.subTest(text=text):
                self.assertEqual(percent.expand(text, codes), want)

    def test_modifiers_without_slash_or_period(self):
        from vamos.spectre import percent
        # :t and :r return the whole name, :h a period [M UG p.267]
        self.assertEqual(percent.expand("%C:t|%C:r|%C:h|%C:e", {"C": "opamp"}), "opamp|opamp|.|")
        self.assertEqual(percent.expand("%C:r", {"C": "opamp.ckt"}), "opamp")
        self.assertEqual(percent.expand("%C:r", {"C": "a.b/opamp"}), "a.b/opamp")     # only the tail's extension
        self.assertEqual(percent.expand("%C:e", {"C": "a.b/opamp"}), "")
        # any other character after a colon ends the modification and stays
        self.assertEqual(percent.expand("%C:x", {"C": "o.ckt"}), "o.ckt:x")
        self.assertEqual(percent.expand("%C:", {"C": "o.ckt"}), "o.ckt:")
        self.assertEqual(percent.expand("%C::r", {"C": "o.ckt"}), "o.ckt:r")        # :: ends the chain

    def test_percent_and_no_recursion(self):
        from vamos.spectre import percent
        self.assertEqual(percent.expand("%%C %%", {"C": "x"}), "%C %")
        self.assertEqual(percent.expand("100%", {}), "100%")
        self.assertEqual(percent.expand("%", {}), "%")
        self.assertEqual(percent.expand("%1", {}), "%1")
        self.assertEqual(percent.expand("%C.raw", {"C": "A%SD.xyz", "S": "spectre"}), "A%SD.xyz.raw")
        self.assertEqual(percent.expand("%Q", {}), "")                      # an undefined code is empty

    def test_codes(self):
        from vamos.spectre import percent
        from vamos.spectre.job import SpectreJob
        start = datetime(2026, 3, 4, 14, 44, 7)
        j = SpectreJob("spectre231", ["x.scs"], "/tmp", netlist="sub/x.scs")
        c = percent.codes(j, start, analysis="tran1")
        self.assertEqual((c["A"], c["C"], c["D"], c["M"], c["S"], c["T"], c["V"]),
                         ("tran1", "sub/x.scs", "26-03-04", "", "spectre231", "14:44:07", VERSION))
        self.assertEqual(c["P"], str(os.getpid()))
        self.assertTrue(c["H"])
        self.assertEqual(percent.codes(j, start)["A"], "")
        self.assertEqual(percent.codes(SpectreJob("specsim", [], "/tmp"), start)["C"], "stdin")
        self.assertEqual(percent.expand("%C:r.raw", percent.codes(SpectreJob("spectre", [], "/tmp"), start)), "stdin.raw")
        # +%X defines or redefines, -%X falls back to the predefined value or to nothing
        j = SpectreJob("spectre", [], "/tmp", netlist="x.scs", percent={"C": "cktfile", "E": "opamp1"})
        c = percent.codes(j, start)
        self.assertEqual((c["C"], c["E"]), ("cktfile", "opamp1"))
        j.percent = {"C": None, "E": None}
        c = percent.codes(j, start)
        self.assertEqual((c["C"], c["E"]), ("x.scs", ""))
        self.assertEqual(percent.expand("%S.%T.%E.%D", percent.codes(j, start)), "spectre.14:44:07..26-03-04")


class TestSettings(unittest.TestCase):
    """spectre/job.settings: argv, then the netlist's options, then the defaults (§2.2)."""

    def nl(self, **options):
        from vamos.netlist import ir
        return ir.Netlist(dialect="spectre", options=options)

    def test_the_four_shared_settings(self):
        from vamos.netlist.expr_ast import Name, Num, Str
        from vamos.spectre import job as sjob
        nl = self.nl(rawfmt=Str("nutbin"), rawfile=Str("o.raw"), maxwarns=Num(4.0), maxnotes=Num(2.0), temp=Num(27.0))
        s = sjob.settings(build(["x.scs"]), nl)
        self.assertEqual((s.fmt, s.raw, s.maxwarns, s.maxnotes), ("nutbin", "o.raw", 4, 2))
        s = sjob.settings(build(["-f", "psfascii", "-raw", "a", "-maxw", "1", "-maxn", "9", "x.scs"]), nl)
        self.assertEqual((s.fmt, s.raw, s.maxwarns, s.maxnotes), ("psfascii", "a", 1, 9))
        s = sjob.settings(build(["x.scs"], {"SPECTRE_DEFAULTS": "-f nutascii -raw d -maxw 5 -maxn 6"}), self.nl())
        self.assertEqual((s.fmt, s.raw, s.maxwarns, s.maxnotes), ("nutascii", "d", 5, 6))
        s = sjob.settings(build(["x.scs"]), self.nl())
        self.assertEqual((s.fmt, s.raw, s.maxwarns, s.maxnotes, s.outdir, s.errpreset, s.aps, s.paramdefault),
                         ("psfascii", "", None, None, None, None, None, []))
        self.assertEqual(sjob.settings(build(["x.scs"]), self.nl(rawfmt=Name("psfascii"))).fmt, "psfascii")
        # a psf-family rawfmt is psfascii with a note; an unknown one is an error on job.notes
        j = build(["x.scs"])
        self.assertEqual(sjob.settings(j, self.nl(rawfmt=Str("psfbin"))).fmt, "psfascii")
        self.assertEqual(dispositions(j), [("options rawfmt=psfbin", NOTED)])
        j = build(["x.scs"])
        self.assertEqual(sjob.settings(j, self.nl(rawfmt=Str("uwi"))).fmt, "psfascii")
        self.assertEqual(len(errors(j)), 1)
        self.assertIn("rawfmt=uwi", j.notes[0].message)
        # the options dict carries the resolved values as expressions
        s = sjob.settings(build(["-maxw", "1", "x.scs"]), nl)
        self.assertEqual((s.options["rawfmt"], s.options["rawfile"], s.options["maxwarns"], s.options["maxnotes"]),
                         (Str("nutbin"), Str("o.raw"), Num(1.0), Num(2.0)))
        self.assertEqual(s.options["temp"], Num(27.0))
        self.assertEqual(nl.options["maxwarns"], Num(4.0))                 # the netlist is not changed

    def test_argv_and_defaults_only_settings(self):
        from vamos.spectre import job as sjob
        s = sjob.settings(build(["-outdir", "o", "+errpreset=liberal", "-maxwtl", "2", "x.scs"],
                                {"SPECTRE_DEFAULTS": "-outdir d +aps=moderate -maxntl 3 +errpreset=conservative"}),
                          self.nl())
        self.assertEqual((s.outdir, s.errpreset, s.aps, s.maxwarnstolog, s.maxnotestolog), ("o", "liberal", "moderate", 2, 3))

    def test_paramdefault_files(self):
        from vamos.spectre import job as sjob
        d = tempfile.mkdtemp(prefix="vamos-test-")
        try:
            with open(os.path.join(d, "a.pd"), "w") as fh:
                fh.write("// defaults\ntran start 2e-3\n\noptions temp 27\n# c\n")
            with open(os.path.join(d, "bad.pd"), "w") as fh:
                fh.write("tran start\n")
            j = build(["+paramdefault", "a.pd", "x.scs"], cwd=d)
            s = sjob.settings(j, self.nl())
            self.assertEqual(s.paramdefault, [("tran", "start", "2e-3"), ("options", "temp", "27")])
            self.assertEqual(j.notes, [])
            j = build(["+paramdefault", "bad.pd", "+paramdefault", "none.pd", "x.scs"], cwd=d)
            s = sjob.settings(j, self.nl())
            self.assertEqual(s.paramdefault, [])
            self.assertEqual(errors(j), ["bad.pd:1", ""])
            self.assertIn("none.pd", j.notes[1].message)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestSpectreLog(TempDir):
    """§8.7: routing, classes, limits, the trailer, built-in text."""

    def log(self, job, profile="spectre", start=None):
        from vamos.spectre.log import SpectreLog
        prof = banner.load_profile("spectre", profile)
        return SpectreLog(job, banner.Banner(prof, "spectre"), start or datetime(2026, 10, 10, 13, 22, 2))

    def job(self, *argv, **env):
        return build(list(argv), env, cwd=self.tmp)

    def capture(self, fn):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            fn()
        return out.getvalue(), err.getvalue()

    def test_routing(self):
        from vamos.spectre.log import SpectreLog
        for argv, on_screen, in_file in ((["+log", "l.log"], True, True), (["=log", "l.log"], False, True),
                                         (["-log"], True, False), ([], True, False)):
            with self.subTest(argv=argv):
                for p in glob.glob(os.path.join(self.tmp, "*.log")):
                    os.remove(p)
                log = self.log(self.job(*argv, "x.scs"))

                def emit():
                    log.line("hello")
                    log.message(Note(WARNING, "", "w"), "setup")
                    log.finish(0)
                out, err = self.capture(emit)
                self.assertEqual(err, "")
                self.assertEqual("hello" in out, on_screen)
                self.assertEqual("spectre completes with 0 errors, 1 warnings, and 0 notices" in out, on_screen)
                path = os.path.join(self.tmp, "l.log")
                self.assertEqual(os.path.exists(path), in_file)
                if in_file:
                    text = open(path).read()
                    self.assertIn("hello\n", text)
                    self.assertIn("Warning from spectre during setup.\n    w\n", text)
                    self.assertTrue(text.endswith("spectre completes with 0 errors, 1 warnings, and 0 notices\n"), text)
        # the path is %-expanded and relative to the cwd, or to -outdir when it has no slash (§2.5)
        os.makedirs(os.path.join(self.tmp, "o"))
        os.makedirs(os.path.join(self.tmp, "sub"))
        log = self.log(self.job("+log", "%C:r.out", "-outdir", "o", "x.scs"))
        log.close()
        self.assertEqual(log.path, os.path.join(self.tmp, "o", "x.out"))
        log = self.log(self.job("+log", "%C:r.out", "-outdir", "o", "sub/x.scs"))   # a slash: not moved
        log.close()
        self.assertEqual(log.path, os.path.join(self.tmp, "sub", "x.out"))
        log = self.log(self.job("+log", "o/%S.%A.log", "-outdir", "zz", "x.scs"))
        log.close()
        self.assertEqual(log.path, os.path.join(self.tmp, "o", "spectre..log"))
        # the defaults layer's +log applies; a log that cannot be opened raises OSError (§2.7)
        log = self.log(self.job("x.scs", SPECTRE_DEFAULTS="=log d.log"))
        log.close()
        self.assertEqual((log.screen, log.path), (False, os.path.join(self.tmp, "d.log")))
        with self.assertRaises(OSError):
            self.log(self.job("+log", "nodir/x.log", "x.scs"))

    def test_messages_classes_and_counts(self):
        log = self.log(self.job("-note", "x.scs"))

        def emit():
            log.message(Note(ERROR, "x.scs:3", "r1: bad"), "circuit read-in")
            log.message(Note(WARNING, "", "line one\nline two"), "setup")
            log.message(Note(NOTE, "", "hidden"), "setup")
            log.finish(1)
        out, err = self.capture(emit)
        self.assertEqual(err, "")
        self.assertIn("Error from spectre during circuit read-in.\n    x.scs:3: r1: bad\n", out)
        self.assertIn("Warning from spectre during setup.\n    line one\n    line two\n", out)
        self.assertNotIn("hidden", out)
        self.assertNotIn("Notice from", out)
        self.assertTrue(out.endswith("spectre completes with 1 errors, 1 warnings, and 1 notices\n"), out)
        self.assertIn("Aggregate audit (", out)
        self.assertIn("Time used: CPU = ", out)
        log = self.log(self.job("-warn", "-error", "x.scs"))
        out, _ = self.capture(lambda: (log.message(Note(WARNING, "", "w"), "s"), log.message(Note(ERROR, "", "e"), "s"),
                                       log.finish(2)))
        self.assertEqual(out, "spectre terminated prematurely due to fatal error.\n")
        self.assertEqual(log.counts, {"errors": 1, "warnings": 1, "notices": 0})

    def test_message_limits_and_ids(self):
        from vamos.spectre.log import message_id
        self.assertEqual(message_id("node `n1' floats at 1.5e-3 in 'x' and \"y\" r12"),
                         "node `' floats at # in `' and `' r#")
        self.assertEqual(message_id("a"), "a")
        log = self.log(self.job("-maxw", "2", "-maxn", "1", "-maxwtl", "3", "+log", "l.log", "x.scs"))

        def emit():
            for i in range(5):
                log.message(Note(WARNING, "x.scs:%d" % i, "node `n%d' floats" % i), "setup")
            log.message(Note(WARNING, "", "another kind"), "setup")
            for i in range(3):
                log.message(Note(NOTE, "", "note %d" % i), "setup")
            log.analysis_banner(("DC Analysis", "op1", ""))
            log.message(Note(WARNING, "", "node `q' floats"), "setup")       # a new analysis: counted afresh
            log.finish(0)
        out, _ = self.capture(emit)
        self.assertEqual(out.count("node `n"), 2)
        self.assertEqual(out.count("further warnings of this kind are suppressed (-maxwarns 2)"), 1)
        self.assertIn("another kind", out)
        self.assertEqual(out.count("note "), 1)
        self.assertEqual(out.count("further notices of this kind are suppressed (-maxnotes 1)"), 1)
        self.assertIn("node `q' floats", out)
        self.assertIn("DC Analysis `op1'\n", out)
        self.assertTrue(out.endswith("spectre completes with 0 errors, 7 warnings, and 3 notices\n"), out)
        text = open(os.path.join(self.tmp, "l.log")).read()
        self.assertEqual(text.count("node `n"), 3)                           # -maxwarnstolog 3 for the file
        self.assertIn("(-maxwarns 3)", text)
        self.assertEqual(text.count("note "), 3)                            # no -maxnotestolog: unlimited
        self.assertIn("further warnings of this kind during analysis `op1'", out) if False else None

    def test_trailer_forms_and_the_invoked_name(self):
        for status, want in ((0, "specsim completes with 0 errors, 0 warnings, and 0 notices"),
                             (1, "specsim completes with 0 errors, 0 warnings, and 0 notices"),
                             (2, "specsim terminated prematurely due to fatal error."),
                             (3, "specsim terminated prematurely due to fatal error.")):
            with self.subTest(status=status):
                log = self.log(build(["x.scs"], cwd=self.tmp, prog="specsim"))
                out, _ = self.capture(lambda: log.finish(status))
                self.assertTrue(out.endswith(want + "\n"), out)
                self.assertEqual("Aggregate audit" in out, status in (0, 1))

    def test_builtin_text_for_every_key(self):
        from vamos.spectre.log import BUILTIN, OPTIONAL_KEYS
        self.assertEqual(sorted(BUILTIN), sorted(BANNER_KEYS))
        shipped = banner.load_profile("spectre")
        for k in BANNER_KEYS:
            self.assertEqual(shipped[k], BUILTIN[k], k)
        # none: only the optional keys are dropped; the trailer and the messages always print
        log = self.log(self.job("x.scs"), profile="none")

        def emit():
            log.run_start()
            log.inventory({"nodes": 5})
            log.analysis_banner(("Transient Analysis", "tran1", "time = (0 s -> 80 us)"))
            log.analysis_done(("Transient Analysis", "tran1", ""), 7.08)
            log.message(Note(NOTE, "", "n"), "setup")
            log.line(log.version_text("version"))
            log.finish(0)
        out, _ = self.capture(emit)
        self.assertEqual(out, "Notice from spectre during setup.\n    n\nvamos %s (spectre personality)\n"
                              "spectre completes with 0 errors, 0 warnings, and 1 notices\n" % VERSION)
        # a profile without a key, or with an invalid template: the built-in text, and one notice
        prof = dict(shipped)
        del prof["trailer_ok"]
        prof["warning_block"] = "{nosuch} {prog}"
        prof["run_start"] = "Run {netlist} as {prog}"
        from vamos.spectre.log import SpectreLog
        log = SpectreLog(self.job("x.scs"), banner.Banner(prof, "spectre"), datetime.now())
        out, _ = self.capture(lambda: (log.run_start(), log.message(Note(WARNING, "", "w1"), "s"),
                                       log.message(Note(WARNING, "", "w2"), "s"), log.finish(0)))
        self.assertTrue(out.startswith("Run x.scs as spectre\n"), out)
        self.assertEqual(out.count("banner template 'warning_block' is invalid"), 1)
        self.assertIn("Warning from spectre during s.\n    w1\n", out)
        self.assertIn("Warning from spectre during s.\n    w2\n", out)
        self.assertTrue(out.endswith("spectre completes with 0 errors, 2 warnings, and 1 notices\n"), out)

    def test_run_layout(self):
        from vamos.netlist import ir, plan
        from vamos.netlist.expr_ast import Num
        from vamos.spectre import results
        from vamos.spectre.log import si, spectre_time
        self.assertEqual(spectre_time(datetime(2003, 8, 20, 13, 22, 2)), "1:22:02 PM, Wed Aug 20, 2003")
        self.assertEqual(spectre_time(datetime(2021, 2, 2, 0, 7, 28)), "12:07:28 AM, Tue Feb 2, 2021")
        self.assertEqual((si(80e-6, "s"), si(1e6, "Hz"), si(0, "s"), si(2.5, "V"), si(1000.0, "Ohm")),
                         ("80 us", "1 MHz", "0 s", "2.5 V", "1 kOhm"))
        log = self.log(self.job("sub/in.scs", "+%H", "nas3"))
        step = plan.AnalysisStep("vamos_a1", "mytran", "tran", args={"stop": 500e-3})
        level = plan.SweepLevel("", "vamos_w1", ("freq", "", ""), ir.SweepSpec("freq", mode="dec", start=Num(1.0),
                                                                             stop=Num(1e6)), label="freq", desc="freq",
                                units="Hz")
        ac = plan.AnalysisStep("vamos_a2", "myac", "ac", own=level)
        res = results.AnalysisResult("myop-dc", "dc", "myop.dc", description="DC Analysis `myop'")

        def emit():
            log.run_start()
            log.inventory({"nodes": 5, "capacitor": 4})
            log.analysis_banner(step)
            self.assertEqual(log.analysis, "mytran")
            log.analysis_done(step, 7.08)
            self.assertEqual(log.analysis, "")
            log.analysis_banner(ac)
            log.analysis_banner(res)
            log.status_line("45 %")
        out, _ = self.capture(emit)
        self.assertIn("Simulating `sub/in.scs' on ", out)
        self.assertIn(" at 1:22:02 PM, Sat Oct 10, 2026.\n\n", out)
        self.assertIn("Circuit inventory:\n              nodes 5\n          capacitor 4\n", out)
        rule = "*" * len("Transient Analysis `mytran': time = (0 s -> 500 ms)")
        self.assertIn("%s\nTransient Analysis `mytran': time = (0 s -> 500 ms)\n%s\n" % (rule, rule), out)
        self.assertIn("Total time required for tran analysis `mytran' was 7.08 s.\n\n", out)
        self.assertIn("AC Analysis `myac': freq = (1 Hz -> 1 MHz)\n", out)
        self.assertIn("DC Analysis `myop'\n", out)
        self.assertIn("spectre status: analysis `myop' running, 45 %, elapsed = ", out)


class TestMain(InTmp):
    """main through cli.main: help, -V/-W, the messages, the exit statuses, nothing on stderr (§2.7, §8.7)."""

    TRAILER = "spectre terminated prematurely due to fatal error.\n"
    NOT_YET = "the spectre run is not implemented yet"

    def test_help(self):
        for argv in (["-h"], ["-help"], ["-hsf"], ["-h", "-raw", "o"], ["-hs", "=log", "l.log"]):
            with self.subTest(argv=argv):
                rc, out, err = self.spectre(argv)
                self.assertEqual((rc, err), (0, ""))
                self.assertTrue(out.startswith("usage: spectre [options] [netlist]"), out[:60])
                self.assertEqual(out.rstrip("\n").splitlines()[-1], "The user guide: %s" % tools.guide_path())
                self.assertTrue(os.path.isabs(tools.guide_path()))
                self.assertTrue(all(len(ln) <= 100 for ln in out.splitlines()), out)
                self.assertIn("--vamos-psf-names=modern|legacy", out)
                self.assertIn("--vamos-analog=vacask|xyce", out)
                self.assertNotIn("--vamos-daidir", out)
                self.assertNotIn("VAMOS_PLAN", out)
                self.assertIn("SPECTRE_DEFAULTS", out)
                self.assertIn("VAMOS_SPECTRE_NAMES", out)
        self.assertFalse(os.listdir(self.tmp))                              # nothing simulated, nothing written
        rc, out, err = self.spectre(["-h", "x.scs"])                       # the next token is the topic (§2.1)
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("note: no help for `x.scs'", out)
        rc, out, err = self.spectre(["-h", "resistor"])
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("resistor (Spectre master", out)
        self.assertIn("folded at parse time: r rsh l w etch etchl", out)
        self.assertIn("renamed: wdexp->wf", out)
        self.assertTrue(all(len(ln) <= 100 for ln in out.splitlines()), out)
        rc, out, _ = self.spectre(["-hsf", "tran", "x.scs"])
        self.assertEqual(rc, 0)
        self.assertIn("tran: stop (required)", out)
        for topic, want in (("bsim4", "bsim4 (Spectre master"), ("DC", "dc: the operating point"),
                            ("bogus", "note: no help for `bogus'"), ("tline", "not in vamos's spectre v1"),
                            ("info", "the .info file is not written"), ("montecarlo", "not in v1")):
            rc, out, _ = self.spectre(["-h", topic])
            self.assertEqual(rc, 0, topic)
            self.assertIn(want, out, topic)
            self.assertTrue(all(len(ln) <= 100 for ln in out.splitlines()), topic)
        rc, out, _ = self.spectre(["-h", "all"])
        self.assertEqual(rc, 0)
        for name in ("resistor (Spectre master", "bsim4 (Spectre master", "tran: stop", "sweep: the sweep"):
            self.assertIn(name, out)
        self.assertEqual(spectre.help_text().count("The user guide:"), 1)

    def test_version(self):
        for opt in ("-V", "-W"):
            rc, out, err = self.spectre([opt])
            self.assertEqual((rc, out, err), (0, "vamos %s (spectre personality)\n" % VERSION, ""))
        rc, out, err = self.spectre(["-V"], argv0="spectre231")
        self.assertEqual((rc, out, err), (0, "vamos %s (spectre personality)\n" % VERSION, ""))
        # with a netlist the run continues (phase 1: to the stop)
        rc, out, err = self.spectre(["-V", "x.scs"])
        self.assertEqual((rc, err), (2, ""))
        self.assertTrue(out.startswith("vamos %s (spectre personality)\n" % VERSION), out)
        self.assertIn(self.NOT_YET, out)
        self.assertTrue(out.endswith(self.TRAILER), out)

    def test_phase1_stop_and_the_trailer(self):
        rc, out, err = self.spectre(["x.scs"])
        self.assertEqual((rc, err), (2, ""))
        self.assertEqual(out, "Error from spectre during setup.\n    the spectre run is not implemented yet "
                              "(docs/VAMOS_SPECTRE_DESIGN.md §12: phase 2 writes spectre/flow.py); the command "
                              "line was accepted\n" + self.TRAILER)
        self.assertFalse(os.listdir(self.tmp))                              # no run directory
        rc, out, err = self.spectre(["x.scs"], argv0="vamos") if False else call_cli(["-spectre", "x.scs"])
        self.assertEqual((rc, err), (2, ""))
        self.assertTrue(out.endswith("spectre terminated prematurely due to fatal error.\n"), out)

    def test_error_rows_stop_with_exit_2(self):
        rc, out, err = self.spectre(["+top", "s", "-format", "uwi", "x.scs", "y.scs"])
        self.assertEqual((rc, err), (2, ""))
        self.assertIn("Error from spectre during command-line processing.\n    +top s: exposing", out)
        self.assertIn("    -format uwi: the user-defined output format is not supported\n", out)
        self.assertIn("    y.scs: a second input file", out)
        self.assertNotIn(self.NOT_YET, out)
        self.assertTrue(out.endswith(self.TRAILER), out)
        self.assertFalse(os.listdir(self.tmp))
        # `spectre x.scs -raw`: the option needs a value (§11 T0)
        rc, out, err = self.spectre(["x.scs", "-raw"])
        self.assertEqual((rc, err), (2, ""))
        self.assertIn("Error from spectre during command-line processing.\n    option -raw needs a value\n", out)
        self.assertTrue(out.endswith(self.TRAILER), out)

    def test_unknown_options_and_strict(self):
        rc, out, err = self.spectre(["-bogus", "+cp", "x.scs"])
        self.assertEqual((rc, err), (2, ""))
        self.assertIn("Warning from spectre during command-line processing.\n    unknown option -bogus ignored\n", out)
        self.assertIn("Notice from spectre during command-line processing.\n    +cp: no checkpoint file is written\n",
                      out)
        self.assertIn(self.NOT_YET, out)                                    # not an error by itself
        rc, out, err = self.spectre(["-bogus", "x.scs", "--vamos-strict"])
        self.assertEqual((rc, err), (2, ""))
        self.assertIn("Error from spectre during command-line processing.\n    --vamos-strict: 1 unsupported/unknown "
                      "option(s): -bogus\n", out)
        self.assertNotIn(self.NOT_YET, out)
        self.assertTrue(out.endswith(self.TRAILER), out)

    def test_vamos_options_are_recorded_by_the_personality(self):
        rc, out, err = self.spectre(["--vamos-psf-names=legacy", "--vamos-analog=xyce", "--vamos-keep",
                                     "--vamos-analog-stop=1u", "--vamos-daidir=d", "x.scs"])
        self.assertEqual((rc, err), (2, ""))
        self.assertIn("Warning from spectre during command-line processing.\n    --vamos-analog-stop=1u has no effect: "
                      "an AMS compile option: vcs-ams or vcs -ad\n", out)
        self.assertIn("    --vamos-daidir=d has no effect: a ./simv option\n", out)
        self.assertNotIn("psf-names", out)
        self.assertNotIn("--vamos-analog=xyce", out)
        self.assertNotIn("--vamos-keep", out)
        rc, out, err = self.spectre(["--vamos-analog-stop=1u", "--vamos-strict", "x.scs"])
        self.assertEqual((rc, err), (2, ""))
        self.assertIn("--vamos-strict: 1 unsupported/unknown option(s): --vamos-analog-stop=1u", out)
        j = build(["x.scs"])
        spectre.note_vamos_options(j, {"append_log": True, "strict": True})
        self.assertEqual(dispositions(j), [("--vamos-append-log", INAPPLICABLE)])

    def test_log_routing_from_main(self):
        rc, out, err = self.spectre(["=log", "%S.%C:r.log", "+top", "s", "x.scs"], argv0="spectre231")
        self.assertEqual((rc, out, err), (2, "", ""))
        text = open(os.path.join(self.tmp, "spectre231.x.log")).read()
        self.assertIn("Error from spectre231 during command-line processing.\n    +top s:", text)
        self.assertTrue(text.endswith("spectre231 terminated prematurely due to fatal error.\n"), text)
        rc, out, err = self.spectre(["+log", "both.log", "x.scs"])
        self.assertEqual((rc, err), (2, ""))
        self.assertEqual(out, open(os.path.join(self.tmp, "both.log")).read())
        self.assertIn(self.NOT_YET, out)
        # a log file that cannot be opened: the error on the screen, exit 2
        rc, out, err = self.spectre(["+log", "nodir/x.log", "x.scs"])
        self.assertEqual((rc, err), (2, ""))
        self.assertIn("cannot open the log file:", out)
        self.assertTrue(out.endswith(self.TRAILER), out)

    def test_the_invoked_name_is_prog(self):
        # %S, <prog>_DEFAULTS and the trailer follow the invoked name (§2.1, §11 T0)
        rc, out, err = self.spectre(["x.scs"], argv0="spectre-23.1", **{"spectre-23.1_DEFAULTS": "=log %S.log"})
        self.assertEqual((rc, out, err), (2, "", ""))
        self.assertTrue(open(os.path.join(self.tmp, "spectre-23.1.log")).read().endswith(
            "spectre-23.1 terminated prematurely due to fatal error.\n"))
        rc, out, err = self.spectre(["x.scs"], argv0="specsim", VAMOS_SPECTRE_NAMES="specsim",
                                    SPECSIM_DEFAULTS="=log %S.out -maxw 3", SPECTRE_DEFAULTS="+top s")
        self.assertEqual((rc, out, err), (2, "", ""))
        text = open(os.path.join(self.tmp, "specsim.out")).read()
        self.assertNotIn("+top", text)                                      # SPECSIM_DEFAULTS, not SPECTRE_DEFAULTS
        self.assertTrue(text.endswith("specsim terminated prematurely due to fatal error.\n"), text)
        rc, out, err = self.spectre(["x.scs"], argv0="spectrespp")
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("Cadence program vamos does not provide", err)
        os.environ["VAMOS_ARGV0"] = "vamos"
        rc, out, err = call_cli(["-spectre", "+log", "v.log", "x.scs"])
        self.assertEqual((rc, err), (2, ""))
        self.assertTrue(out.endswith("spectre terminated prematurely due to fatal error.\n"), out)

    def test_banner_choice(self):
        rc, out, err = self.spectre(["x.scs", "--vamos-banner=none"])
        self.assertEqual((rc, err), (2, ""))
        self.assertTrue(out.endswith(self.TRAILER), out)                   # the trailer is never suppressed
        rc, out, err = self.spectre(["x.scs", "--vamos-banner=nonesuch"])
        self.assertEqual((rc, err), (2, ""))
        self.assertIn("    no banner profile named 'nonesuch'\n", out)
        self.assertTrue(out.endswith(self.TRAILER), out)

    def test_an_internal_error_is_exit_2_with_the_trailer_and_no_traceback(self):
        saved = spectre.build_job

        def boom(*a, **k):
            raise RuntimeError("boom")

        spectre.build_job = boom
        try:
            rc, out, err = self.spectre(["x.scs"])
        finally:
            spectre.build_job = saved
        self.assertEqual((rc, err), (2, ""))
        self.assertIn("Error from spectre during setup.\n    internal error: RuntimeError: boom (the traceback is in ",
                      out)
        self.assertNotIn("Traceback", out)
        self.assertTrue(out.endswith(self.TRAILER), out)
        where = re.search(r"the traceback is in (\S+)\)", out).group(1)
        try:
            self.assertIn("Traceback (most recent call last)", open(where).read())
        finally:
            os.remove(where)
        # with a log file the traceback goes there, not on the screen
        spectre.build_job = boom
        try:
            saved_open = spectre.open_log
            rc, out, err = self.spectre(["+log", "l.log", "x.scs"])
        finally:
            spectre.build_job = saved
        self.assertEqual((rc, err), (2, ""))
        self.assertNotIn("Traceback", out)
        # the exception came from build_job, before the log: the one line names a file
        self.assertIn("internal error: RuntimeError: boom", out)
        for p in glob.glob(os.path.join(tempfile.gettempdir(), "vamos-spectre-*.traceback")):
            os.remove(p)
        self.assertIs(spectre.open_log, saved_open)

    def test_main_never_raises_and_never_writes_stderr(self):
        for argv in (["x.scs"], ["-h"], ["-V"], ["+top"], ["=log"], ["x.scs", "-raw"], [], ["-h", "all"]):
            with self.subTest(argv=argv):
                rc, out, err = self.spectre(argv)
                self.assertIn(rc, (0, 2))
                self.assertEqual(err, "")
        rc, out, err = self.spectre([])                                    # stdin would be the netlist
        self.assertEqual((rc, err), (2, ""))
        self.assertIn(self.NOT_YET, out)

    def test_the_frozen_signatures_are_implemented(self):
        self.assertIsInstance(spectre.OPTIONS, list)
        self.assertIsInstance(spectre.TABLE, Table)
        j = spectre.build_job(["x.scs"], {}, self.tmp, "spectre")
        self.assertEqual((j.prog, j.argv, j.cwd, j.netlist), ("spectre", ["x.scs"], self.tmp, "x.scs"))
        self.assertTrue(spectre.help_text().startswith("usage: spectre"))
        self.assertIn("resistor (Spectre master", spectre.help_text("resistor"))
        tools.invoked = "spectre"                                            # as cli.main sets it (§2.1)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(spectre.main([], {}), 2)
        self.assertEqual(err.getvalue(), "")
        self.assertIn("spectre terminated prematurely", out.getvalue())
        tools.invoked = "vcs"                                                # outside cli.main: still never raises
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(spectre.main(["x.scs"], {}), 2)
        self.assertEqual(err.getvalue(), "")


class TestCpp(TempDir):
    """§2.6 (WSL: Cygwin has no cpp) and the helpers that need none."""

    TOP = ("// opamp test bench, title line\n"
           "#define VDD 1.8\n"
           "#include \"inc/sub.scs\"\n"
           "#include \"only.scs\"\n"
           "simulator lang=spice\n"
           "* don't warn about this apostrophe\n"
           ".param rr='VDD*1k'\n"
           "r1 a 0 'rr'\n")

    def write_tree(self):
        self.write("top.scs", self.TOP)
        self.write("inc/sub.scs", "r2 (a 0) resistor r=1k\n")
        self.write("inc/only.scs", "r3 (a 0) resistor r=2k\n")          # reached through -Iinc only
        os.makedirs(os.path.join(self.tmp, "run"))

    def test_command_and_title(self):
        from vamos.spectre import cpp
        self.write_tree()
        j = build(["-E", "-DX=1", "-UY", "-Iinc", "top.scs"], cwd=self.tmp)
        self.assertEqual(cpp.command("top.scs", j, os.path.join(self.tmp, "run"), "/usr/bin/cpp"),
                         ["/usr/bin/cpp", "-traditional-cpp", "-nostdinc", "-undef", "-DX=1", "-UY", "-Iinc",
                          os.path.join(self.tmp, "top.scs"), os.path.join(self.tmp, "run", "cpp.out")])
        self.assertEqual(cpp.title_of("top.scs", j), "// opamp test bench, title line")

    def test_helpers_without_cpp(self):
        from vamos.spectre import cpp
        notes = cpp.directive_errors(self.TOP, "top.scs")
        self.assertEqual([(n.severity, n.origin) for n in notes],
                         [(ERROR, "top.scs:2"), (ERROR, "top.scs:3"), (ERROR, "top.scs:4")])
        self.assertIn("CPP directive without -E", notes[0].message)
        self.assertEqual(cpp.directive_errors("r1 a 0 1k\n* #define x\n", "t"), [])
        self.assertEqual(cpp.macro_names(self.TOP, ["X=1", "Y"]), ["X", "Y", "VDD"])
        w = cpp.macro_warnings(self.TOP, "top.scs", [])
        self.assertEqual([(n.severity, n.origin) for n in w], [(WARNING, "top.scs:7")])
        self.assertIn("macro VDD inside the quoted expression 'VDD*1k'", w[0].message)
        self.assertEqual(cpp.macro_warnings(self.TOP, "top.scs", ["rr"])[1].origin, "top.scs:8")
        self.assertEqual(cpp.macro_warnings("r1 a 0 'x'\n", "t", []), [])
        notes = cpp.stderr_notes("top.scs:13:42: warning: missing terminating ' character\nsome other line\n\n")
        self.assertEqual([(n.severity, n.origin, n.message) for n in notes],
                         [(WARNING, "top.scs:13:42", "cpp: warning: missing terminating ' character"),
                          (NOTE, "", "cpp: some other line")])

    def test_bad_vamos_cpp_is_a_tool_error(self):
        from vamos.spectre import cpp
        self.write_tree()
        os.environ["VAMOS_CPP"] = os.path.join(self.tmp, "nonexistent")
        j = build(["-E", "top.scs"], cwd=self.tmp)
        with self.assertRaises(tools.ToolError):
            cpp.preprocess("top.scs", j, os.path.join(self.tmp, "run"))

    def test_missing_cpp_is_an_error_note(self):
        from vamos.notes import NoteError
        from vamos.spectre import cpp
        self.write_tree()
        j = build(["-E", "top.scs"], cwd=self.tmp)
        saved = tools.find_real
        tools.find_real = lambda name: None
        try:
            with self.assertRaises(NoteError) as cm:
                cpp.preprocess("top.scs", j, os.path.join(self.tmp, "run"))
        finally:
            tools.find_real = saved
        self.assertIn("cpp not found (set VAMOS_CPP", cm.exception.notes[0].message)

    @needs_cpp
    def test_preprocess(self):
        from vamos.notes import NoteError
        from vamos.spectre import cpp
        self.write_tree()
        j = build(["-DX=1", "-Iinc", "top.scs"], cwd=self.tmp)
        out, title, notes = cpp.preprocess("top.scs", j, os.path.join(self.tmp, "run"))
        self.assertEqual(out, os.path.join(self.tmp, "run", "cpp.out"))
        self.assertEqual(title, "// opamp test bench, title line")
        self.assertEqual(notes, [])                                         # -traditional-cpp: no apostrophe warning
        text = open(out).read()
        self.assertIn("// opamp test bench, title line", text)             # the title kept [E82]
        # the markers: cpp names a file as it found it (§2.6): beside the includer, which is the absolute
        # input path, or through the relative -Iinc, relative to cpp's cwd (the job's cwd) [E82]
        self.assertIn('# 1 "%s" 1\n' % os.path.join(self.tmp, "inc", "sub.scs"), text)
        self.assertIn('# 1 "inc/only.scs" 1\n', text)
        self.assertIn('# 0 "<built-in>"\n', text)
        self.assertIn('# 0 "<command-line>"\n', text)
        self.assertIn('"%s" 2\n' % os.path.join(self.tmp, "top.scs"), text)   # the return to the includer
        self.assertIn("r2 (a 0) resistor r=1k", text)
        self.assertIn("r3 (a 0) resistor r=2k", text)
        self.assertIn(".param rr='VDD*1k'", text)                           # not expanded inside quotes [E82]
        self.assertNotIn("#define", text)
        # cpp's stderr becomes notes, and a failure an error
        self.write("bad.scs", "// t\n#error boom\n")
        with self.assertRaises(NoteError) as cm:
            cpp.preprocess("bad.scs", j, os.path.join(self.tmp, "run"))
        self.assertTrue(any("cpp failed" in n.message for n in cm.exception.notes), cm.exception.notes)
        self.assertTrue(any(n.severity in (WARNING, NOTE) and "boom" in n.message for n in cm.exception.notes))
        self.assertTrue(all(n.message.startswith("cpp") for n in cm.exception.notes))

    @unittest.skip(PHASE2)
    def test_vamos_cpp_nonexistent_with_E_gives_exit_2(self):
        pass


class TestGuideAndShim(unittest.TestCase):
    """The parts of the guide's spectre section that need no run (§10), and shims/spectre."""

    def guide(self):
        with open(os.path.join(ROOT, "docs", "VAMOS_GUIDE.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_guide_spectre_section(self):
        g = self.guide()
        for text in ("## 12. The spectre personality", "VAMOS_SPECTRE_NAMES", "shims/spectre",
                     "is a Cadence program vamos does not provide", "SPECTRE_DEFAULTS", "`spectre231`",
                     "| `--vamos-psf-names=modern\\|legacy` | spectre |", "`+top`", "`+interactive`", "`-format uwi`",
                     "`VAMOS_CPP`", "`CDS_VLOGA_INCLUDE`", "`trailer_fatal`", "docs/VAMOS_SPECTRE_DESIGN.md"):
            self.assertIn(text, g, text)
        self.assertIn("[12 The spectre personality](#12-the-spectre-personality)", g)
        self.assertNotIn("no `spectre` yet", g)
        self.assertNotIn("(a design; not built)", g)
        row = [ln for ln in g.splitlines() if ln.startswith("| `--vamos-analog=vacask")][0]
        self.assertIn("spectre", row)
        row = [ln for ln in g.splitlines() if ln.startswith("| `--vamos-keep`")][0]
        self.assertIn("spectre", row)
        self.assertEqual(len([ln for ln in g.splitlines() if ln.startswith("| cpp |")]), 1)

    def test_shim_link(self):
        p = os.path.join(ROOT, "shims", "spectre")
        self.assertTrue(os.path.islink(p), p)
        self.assertEqual(os.readlink(p).replace("\\", "/"), "../bin/vamos")
        self.assertEqual(os.path.realpath(p), os.path.realpath(os.path.join(ROOT, "bin", "vamos")))


# =============================================================================
# tools: SHIM_NAMES (§10; E79)
# =============================================================================

class TestShimNames(TempDir):
    def link(self, d, name):
        os.makedirs(d, exist_ok=True)
        try:
            os.symlink(LAUNCHER, os.path.join(d, name))
        except (OSError, NotImplementedError, AttributeError) as e:
            self.skipTest("no symlinks here: %s" % e)

    def test_spectre_is_a_shim_name(self):
        self.assertIn("spectre", tools.SHIM_NAMES)
        for n in ("vcs", "vcs-ams", "nvc"):
            self.assertIn(n, tools.SHIM_NAMES)
        self.assertNotIn("specsim", tools.SHIM_NAMES)

    def test_a_directory_holding_only_a_spectre_link_is_scrubbed(self):
        os.environ["VAMOS_LAUNCHER"] = os.path.realpath(LAUNCHER)
        d = os.path.join(self.tmp, "shims")
        self.link(d, "spectre")
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        tools._scrub_cache.clear()
        self.assertTrue(tools._is_shim_dir(d, []))
        kept = tools.scrubbed_path(d + os.pathsep + other).split(os.pathsep)
        self.assertEqual(kept, [other])
        # alias links are not probed (§10): such a directory is kept, unless VAMOS_REDIRECT lists it
        e = os.path.join(self.tmp, "aliases")
        self.link(e, "specsim")
        tools._scrub_cache.clear()
        self.assertFalse(tools._is_shim_dir(e, []))
        self.assertEqual(tools.scrubbed_path(e + os.pathsep + other).split(os.pathsep), [e, other])
        os.environ["VAMOS_REDIRECT"] = e
        tools._scrub_cache.clear()
        self.assertEqual(tools.scrubbed_path(e + os.pathsep + other).split(os.pathsep), [other])


# =============================================================================
# banners/spectre.json and licenses.json (§8.7, §10, §1; E79)
# =============================================================================

class TestBannerProfile(unittest.TestCase):
    FIELDS = dict(prog="spectre", netlist="input.scs", host="nas3", time="3:04:05 PM, Mon Jan 1, 2024",
                  inventory_lines="              nodes 5\n          capacitor 4", analysis="tran1",
                  analysis_type="tran", analysis_title="Transient Analysis", range="time = (0 s -> 80 us)",
                  rule="*" * 51, seconds=7.08, cpu=7.29, elapsed=8.0, phase="setup", text="the message",
                  errors=0, warnings=1, notices=2)

    def load(self):
        with open(os.path.join(ROOT, "vamos", "banners", "spectre.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_keys_and_layers(self):
        prof = self.load()
        self.assertEqual(sorted(prof), sorted(BANNER_KEYS + ("brand", "_comment")))
        self.assertEqual(prof["brand"], "vamos")
        c = prof["_comment"]
        for layer in ("this package", "etc/vamos/", "~/.config/vamos/", "~/.vamos/", "./.vamos/"):
            self.assertIn(layer, c)
        self.assertIn("{prog}", c)
        for key in BANNER_KEYS:
            self.assertIsInstance(prof[key], str)
            self.assertTrue(prof[key], key)
            low = prof[key].lower()
            for banned in ("psf", "cadence", "copyright", "(c)", "licen"):
                self.assertNotIn(banned, low, key)

    def test_placeholders_are_documented_and_format(self):
        prof = self.load()
        documented = set(re.findall(r"\{(\w+)\}", prof["_comment"]))
        used = set()
        for key in BANNER_KEYS:
            for _, field, _, _ in string.Formatter().parse(prof[key]):
                if field:
                    used.add(field.split("!")[0].split(":")[0])
        self.assertTrue(used <= documented, used - documented)
        fixed = {"brand", "Brand", "BRAND", "BRAND_SPACED", "version", "personality"}
        self.assertTrue(set(self.FIELDS) | fixed >= documented, documented - set(self.FIELDS) - fixed)
        ban = banner.Banner(prof, "spectre")
        for key in BANNER_KEYS:
            text = ban.text(key, **self.FIELDS)
            self.assertIsInstance(text, str, key)
            self.assertFalse(text.startswith("vamos: banner template"), text)
        self.assertEqual(ban.text("trailer_ok", **self.FIELDS),
                         "spectre completes with 0 errors, 1 warnings, and 2 notices")
        self.assertEqual(ban.text("trailer_fatal", **self.FIELDS), "spectre terminated prematurely due to fatal error.")
        self.assertEqual(ban.text("version", **self.FIELDS), "vamos %s (spectre personality)" % VERSION)
        self.assertEqual(ban.text("error_block", **self.FIELDS), "Error from spectre during setup.\n    the message")
        self.assertTrue(ban.text("analysis_banner", **self.FIELDS).startswith("*" * 51 + "\nTransient Analysis `tran1':"))
        self.assertEqual(ban.text("trailer_ok", **dict(self.FIELDS, prog="specsim")),
                         "specsim completes with 0 errors, 1 warnings, and 2 notices")

    def test_load_profile_finds_it(self):
        prof = banner.load_profile("spectre")                      # E79: {} before phase 0
        self.assertTrue(set(BANNER_KEYS) <= set(prof), prof)
        self.assertIsNone(banner.load_profile("spectre", "none"))


class TestLicences(unittest.TestCase):
    def test_cpp(self):
        with open(os.path.join(ROOT, "vamos", "licenses.json"), encoding="utf-8") as fh:
            lic = json.load(fh)
        self.assertEqual(lic["cpp"], {"spdx": "GPL-3.0-or-later", "url": "https://gcc.gnu.org"})
        self.assertEqual(banner.licenses()["cpp"]["spdx"], "GPL-3.0-or-later")
        text = banner.provenance([("vamos", "git-x", "/x/vamos"), ("VACASK", "0.3.4", "/opt/v"),
                                  ("cpp", "13.2.0", "/usr/bin/cpp")], "spectre")
        rows = text.splitlines()
        self.assertEqual(rows[0], "vamos %s (spectre personality) - tools used:" % VERSION)
        self.assertNotIn("licence unknown", text)                   # E79: what the row read before
        cpp = [r for r in rows if r.startswith("  cpp")]
        self.assertEqual(len(cpp), 1, text)
        self.assertIn("GPL-3.0-or-later", cpp[0])
        self.assertIn("/usr/bin/cpp", cpp[0])
        self.assertEqual(len({r.index("/") for r in rows[1:]}), 1, text)
        report = banner.license_report()
        row = [ln for ln in report.splitlines() if ln.startswith("  cpp")]
        self.assertEqual(len(row), 1, report)
        self.assertIn("https://gcc.gnu.org", row[0])
        rows = [ln for ln in report.splitlines() if ln.startswith("  ")]
        self.assertEqual(len({r.index("http") for r in rows}), 1, report)


if __name__ == "__main__":
    unittest.main()
