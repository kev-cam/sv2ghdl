"""spectre command-line tests, phase 0 (docs/VAMOS_SPECTRE_DESIGN.md §11 T0, the CLI list):
the optable, cli, tools, banner-profile and licence contracts of the skeleton.

    python3 -m unittest test_spectre_cli          (from tests/vamos)

Runs on both legs (Cygwin Python 3.9 and WSL), no engine, no Rust.  S5 extends this file
in phase 1 with the personality's own tests (every §2.3 row through build_job, the defaults
layers, the invoked name's %S, spectre -h, ...); TestPhase0Stub is replaced then.
Covered here: scan(option_chars=) and the `=` family; VamosOpt.also, psf_names, the spectre
context in vamos_option_effects and vamos_options_help, and vcs's help unchanged but for the
psf_names line; unmapped_notes behind report_unmapped; the dispatch rule (personality_of,
spectre231, spectre-23.1, VAMOS_SPECTRE_NAMES, spectrespp, vamos -spectre) and tools.invoked;
the cli's effects check skipping spectre; SHIM_NAMES; banners/spectre.json; licenses.json's cpp.
"""

import contextlib
import io
import json
import os
import re
import string
import unittest

from vamos_testlib import LAUNCHER, ROOT, TempDir  # noqa: F401

from vamos import VERSION, banner, cli, optable, tools  # noqa: E402
from vamos.job import IGNORED, INAPPLICABLE, NOTED, UNKNOWN, UNSUPPORTED, Job, Unmapped  # noqa: E402
from vamos.notes import NOTE, WARNING  # noqa: E402
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


class TestPhase0Stub(TempDir):
    """The phase-0 personality stub (vamos/personalities/spectre.py); S5 replaces this class."""

    def test_main_reports_not_implemented_with_exit_status_2(self):
        os.environ["VAMOS_ARGV0"] = "vamos"
        rc, out, err = call_cli(["-spectre", "x.scs"])
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("vamos: error: the spectre personality is not implemented yet", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual((tools.invoked, tools.current), ("spectre", "spectre"))

    def test_the_frozen_signatures(self):
        self.assertIsInstance(spectre.OPTIONS, list)
        self.assertIsInstance(spectre.TABLE, Table)
        with self.assertRaises(NotImplementedError):
            spectre.build_job(["x.scs"], {}, self.tmp, "spectre")
        with self.assertRaises(NotImplementedError):
            spectre.help_text()
        with self.assertRaises(NotImplementedError):
            spectre.help_text("resistor")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(spectre.main([], {}), 2)


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
