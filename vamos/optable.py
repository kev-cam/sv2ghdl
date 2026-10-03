"""Declarative option tables.

Each personality lists its options as Opt entries.  scan() walks argv,
matches each token against the table and applies the action, so "accept every
option the real tool accepts" is a data problem: an option the table does not
know is warned about and recorded, never a usage error.

Arity kinds:
  flag     exact token                          -R
  next     exact token, value is the next arg   -o simv
  eq       -opt=value (exact prefix incl '=')   -timescale=1ns/1ps
  plus     +opt+a+b+  (value = list)            +incdir+a+b
  prefix   token starts with name; value = rest -debug_access+all, -j8

vamos's own options (--vamos-<key>[=<value>]) are a separate namespace that
vamos owns: VAMOS_OPTIONS lists every one the code reads, check_vamos_opts()
turns an unknown key or a bad value into a usage error, and
vamos_option_effects() names the ones that have no effect where they were
given (a warning; --vamos-strict makes it an error).
"""

import difflib
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple, Union

from vamos.job import IGNORED, INAPPLICABLE, NOTED, UNKNOWN, UNSUPPORTED, Job

Action = Union[str, Callable]


class Opt:
    __slots__ = ("name", "arity", "act", "note")

    def __init__(self, name: str, arity: str = "flag",
                 act: Action = IGNORED, note: str = ""):
        assert arity in ("flag", "next", "eq", "plus", "prefix"), arity
        # eq names exclude the '=' (Opt("-timescale", "eq") matches -timescale=x);
        # a literal "-ad=" would never match and the option would go UNKNOWN.
        assert not (arity == "eq" and name.endswith("=")), name
        self.name, self.arity, self.act, self.note = name, arity, act, note


class ScanError(Exception):
    pass


class Table:
    def __init__(self, opts: Sequence[Opt]):
        self.exact = {}
        self.fuzzy: List[Opt] = []
        for o in opts:
            if o.arity in ("flag", "next"):
                self.exact[o.name] = o
            else:
                self.fuzzy.append(o)
        # longest name first so '+incdir+' beats '+'
        self.fuzzy.sort(key=lambda o: -len(o.name))

    def match(self, tok: str) -> Optional[Opt]:
        o = self.exact.get(tok)
        if o:
            return o
        for o in self.fuzzy:
            if o.arity == "eq" and tok.startswith(o.name + "="):
                return o
            if o.arity in ("plus", "prefix") and tok.startswith(o.name):
                return o
        return None


def value_of(opt: Opt, tok: str):
    if opt.arity == "eq":
        return tok[len(opt.name) + 1:]
    if opt.arity == "plus":
        return [p for p in tok[len(opt.name):].split("+") if p]
    if opt.arity == "prefix":
        return tok[len(opt.name):]
    return None


def scan(table: Table, args: List[str], job: Job,
         positional: Callable[[Job, str], None],
         unknown: Callable[[Job, str, List[str], int], int]) -> None:
    """Apply table to args.

    positional(job, tok) handles non-option tokens (source files).
    unknown(job, tok, args, i) handles unmatched options; returns how many
    tokens it consumed (>= 1).
    """
    i = 0
    while i < len(args):
        tok = args[i]
        opt = table.match(tok) if tok[:1] in ("-", "+") else None
        if opt is None:
            if tok[:1] in ("-", "+") and tok not in ("-", "+"):
                i += unknown(job, tok, args, i)
            else:
                positional(job, tok)
                i += 1
            continue
        if opt.arity == "next":
            if i + 1 >= len(args):
                raise ScanError("option %s needs a value" % tok)
            val = args[i + 1]
            i += 2
        else:
            val = value_of(opt, tok)
            i += 1
        if callable(opt.act):
            opt.act(job, val)
        else:
            shown = tok if opt.arity != "next" else "%s %s" % (tok, val)
            job.note(shown, opt.act, opt.note)


def report_unmapped(job: Job, emit: Callable[[str], None]) -> None:
    """One line per option that needs the user's attention."""
    for u in job.unmapped:
        if u.disposition == IGNORED:
            continue
        if u.disposition == NOTED:
            emit("vamos: note: %s%s" % (u.option, ": " + u.note if u.note else ""))
        elif u.disposition == UNSUPPORTED:
            emit("vamos: warning: %s is not supported yet%s"
                 % (u.option, " (" + u.note + ")" if u.note else ""))
        elif u.disposition == UNKNOWN:
            emit("vamos: warning: unknown option %s ignored%s"
                 % (u.option, " (" + u.note + ")" if u.note else ""))
        elif u.disposition == INAPPLICABLE:
            emit("vamos: warning: %s has no effect%s" % (u.option, ": " + u.note if u.note else ""))


def strict_failures(job: Job) -> List[str]:
    return [u.option for u in job.unmapped
            if u.disposition in (UNSUPPORTED, UNKNOWN, INAPPLICABLE)]


def strict_message(bad: Sequence[str]) -> str:
    """The --vamos-strict error line (without the "vamos: error: " prefix)."""
    return "--vamos-strict: %d unsupported/unknown option(s): %s" % (len(bad), " ".join(bad))


# =============================================================================
# vamos's own options: --vamos-<key>[=<value>]
# =============================================================================

class VamosOpt(NamedTuple):
    key: str                     # opts key: the option name after --vamos-, '-' as '_'
    value: Optional[str]         # the value's form for the help ("<time>"); None: a flag (no value)
    where: str                   # where it has an effect: any | ams | run | simv
    help: Optional[str]          # one help line; None: internal, not listed
    choices: Tuple[str, ...] = ()  # the allowed values (case-insensitive); () = any non-empty value


# Every --vamos-* key the code reads (cli, the personalities, ams/flow.py, ams/deck.py,
# backends/cosim.py).  where:
#   any   every personality
#   ams   an AMS compile: vcs-ams, or vcs with -ad/+ad
#   run   a run: ./simv, or a compile with -R (the AMS run directory)
#   simv  ./simv only
VAMOS_OPTIONS: Tuple[VamosOpt, ...] = (
    VamosOpt("banner", "<name|path|none>", "any", "banner profile (default: the personality's)"),
    VamosOpt("strict", None, "any", "unsupported, unknown and ineffective options are errors; so is "
             "every warning (an approximation vamos had to make)"),
    VamosOpt("verbose", None, "any", "print each command vamos runs"),
    VamosOpt("licenses", None, "any", "print the tools vamos may run and their licences, and exit"),
    VamosOpt("version", None, "any", "print the vamos version and exit"),
    VamosOpt("analog", "vacask|xyce", "ams", "the analog engine (default vacask; also VAMOS_ANALOG)",
             ("vacask", "xyce")),
    VamosOpt("analog_stop", "<time>", "ams", "end time for a netlist with no .tran (default 3600 s)"),
    VamosOpt("analog_maxstep", "<time>", "ams", "analog maximum time step (replaces the .tran's)"),
    VamosOpt("parhier", "local|global", "ams", "local: a parameter defined at top level and in a "
             "subckt takes the inner value (default: such a collision is an error)", ("local", "global")),
    VamosOpt("no_deck_check", None, "ams", "skip the compile-time operating-point check of the deck"),
    VamosOpt("keep", None, "run", "keep the AMS per-run directory"),
    VamosOpt("daidir", "<dir>", "simv", "the compiled directory (the generated ./simv passes it)"),
    VamosOpt("append_log", None, "simv", "with -l, append to the log instead of starting it afresh"),
)

VAMOS_KEYS: Dict[str, VamosOpt] = {o.key: o for o in VAMOS_OPTIONS}

# Named in docs/VAMOS_PLAN.md and docs/VAMOS_SPECTRE_DESIGN.md, not implemented.
PLANNED_KEYS = frozenset(("selfcheck", "env", "config", "site", "sites", "shell", "mc", "corner",
                          "wave", "psf_names"))

_WHERE_TEXT = {"ams": "AMS compile", "run": "./simv, or a compile with -R", "simv": "./simv"}


def vamos_option_text(key: str, val) -> str:
    """The option as it reads on a command line: --vamos-analog=xyce, --vamos-keep."""
    name = "--vamos-" + key.replace("_", "-")
    return name if val is True else "%s=%s" % (name, val)


def check_vamos_opts(opts: Dict[str, object]) -> List[str]:
    """Usage errors in the --vamos-* options (cli.split_vamos_opts's dict): an unknown key
    (with the nearest known one), a value given to a flag, a missing value, a value outside
    its choices.  vamos owns this namespace, so a misspelt key is never dropped."""
    errs: List[str] = []
    for key, val in opts.items():
        spec = VAMOS_KEYS.get(key)
        text = vamos_option_text(key, val)
        if spec is None:
            if key in PLANNED_KEYS:
                errs.append("%s is planned (docs/VAMOS_PLAN.md) but not implemented yet" % text)
                continue
            near = difflib.get_close_matches(key, list(VAMOS_KEYS), 1, 0.6)
            errs.append("unknown vamos option %s%s" % (
                text, " (did you mean %s?)" % vamos_option_text(near[0], True) if near else ""))
            continue
        name = vamos_option_text(key, True)
        if spec.value is None:
            if val is not True:
                errs.append("%s takes no value (%s)" % (name, text))
            continue
        if val is True or val == "":
            errs.append("%s needs a value: %s=%s" % (name, name, spec.value))
            continue
        if spec.choices and str(val).lower() not in spec.choices:
            if key == "analog":
                errs.append("%s: the analog engine must be vacask or xyce" % text)
            else:
                errs.append("%s: the value must be %s" % (text, " or ".join(spec.choices)))
    return errs


def vamos_option_effects(opts: Dict[str, object], personality: str, ams: bool = False,
                         run: bool = False) -> List[Tuple[str, str]]:
    """(option, why) for each --vamos-* option that has no effect in this invocation.

    personality: vcs (a compile; ams: an AMS compile, run: with -R), simv, or a
    pass-through tool name.  version and licenses never get here (vamos exits first)."""
    out: List[Tuple[str, str]] = []
    for key, val in opts.items():
        spec = VAMOS_KEYS.get(key)
        if spec is None or key in ("version", "licenses"):
            continue
        text = vamos_option_text(key, val)
        if personality not in ("vcs", "vcs-ams", "simv"):
            out.append((text, "the %s personality hands its command line to the real %s unchanged"
                        % (personality, personality)))
            continue
        why = None
        if personality == "simv":
            if spec.where == "ams":
                why = ("a compile-time option (./simv runs the design as it was compiled; compile "
                       "again to change it)")
        else:
            if spec.where == "ams" and not ams:
                why = "not an AMS compile (no -ad, +ad or vcs-ams)"
            elif spec.where == "run" and not run:
                why = "a ./simv option, and this compile runs nothing (no -R): give it to ./simv"
            elif spec.where == "run" and not ams:
                why = "a digital run has no run directory to keep"
            elif spec.where == "simv":
                why = "a ./simv option"
        if why:
            out.append((text, why))
    return out


def vamos_options_help(personality: str = "") -> str:
    """The --vamos-* part of a usage text: every option the code accepts (internal ones too),
    with where it has an effect.  personality "vcs"/"vcs-ams" lists what a compile takes."""
    import textwrap
    lines = []
    for o in VAMOS_OPTIONS:
        if o.help is None:
            continue
        if personality in ("vcs", "vcs-ams") and o.where == "simv":
            continue
        name = vamos_option_text(o.key, True) + ("=" + o.value if o.value else "")
        where = _WHERE_TEXT.get(o.where)
        text = o.help + (" [%s]" % where if where else "")
        lines.append(textwrap.fill(text, width=96, initial_indent="  %-32s " % name,
                                   subsequent_indent=" " * 35, break_on_hyphens=False))
    return "\n".join(lines)
