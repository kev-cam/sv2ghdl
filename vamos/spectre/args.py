"""The spectre command line's layers and prescan (docs/VAMOS_SPECTRE_DESIGN.md §2.1, §2.2;
the API is the §10 block).

Two layers feed one SpectreJob: the defaults variable (`<prog>_DEFAULTS`, `<PROG>_DEFAULTS`,
`SPECTRE_DEFAULTS`: the first that exists, an empty one meaning no defaults) and argv.  Each is
scanned on its own (personalities/spectre.build_job); the helpers here take the tokens
optable.scan cannot (`+%X string`, `-%X` and the help options, whose topic is optional) and
filter the defaults layer: the options ref5 marks without a dagger are not read from the
variable (§2.2), nor is a `--vamos-*` token (§1).  effective() reads a setting across the two
layers once they are merged: argv's value when argv gave one, else the defaults layer's
(SpectreJob.defaults), else the field's own default.
"""

from __future__ import annotations

import dataclasses
import shlex
import string
from typing import Dict, List, Mapping, Optional, Tuple

from vamos.job import NOTED, UNSUPPORTED
from vamos.optable import ScanError
from vamos.spectre.job import SpectreJob

# The option characters of the spectre command line (§2.1): `=log f` is the third family.
OPTION_CHARS = "-+="

# The help options prescan takes (§2.1): each may be followed by a topic.
HELP_OPTIONS = ("-help", "-h", "-helpsort", "-hs", "-helpfull", "-hf", "-helpsortfull", "-hsf")

# The options Spectre does not read from the defaults variable: ref5 lists them without a dagger
# [M ref5 pp.18-21] (§2.2).  Flags, options taking the next token, and the `=value` forms.
NOT_READ_FLAGS = HELP_OPTIONS + ("-V", "-W", "+note", "-note", "+interactive", "+inter",
                                 "-interactive", "-inter")
NOT_READ_NEXT = ("-env", "-slave", "-slvhost", "+sensdata")
NOT_READ_EQ = ("+interactive", "+inter", "+mpssession", "+mpshost")

VAMOS_OPTIONS_NOTE = ("vamos options are read from the command line only, not from %s; use "
                      "VAMOS_ANALOG, VAMOS_BANNER or VAMOS_VERBOSE")

_FIELDS = {f.name: f for f in dataclasses.fields(SpectreJob)}


def defaults_vars(prog: str) -> List[str]:
    """The variables tried for the defaults layer, in order (§2.2): `<prog>_DEFAULTS`,
    `<PROG>_DEFAULTS` when different, then `SPECTRE_DEFAULTS`."""
    out = [prog + "_DEFAULTS"]
    for v in (prog.upper() + "_DEFAULTS", "SPECTRE_DEFAULTS"):
        if v not in out:
            out.append(v)
    return out


def defaults_tokens(env: Mapping[str, str], prog: str) -> Tuple[Optional[str], List[str]]:
    """(variable, tokens) of the first defaults variable that exists (§2.2).  A variable that
    exists but is empty means no defaults, and the next one is not read: (variable, []).
    (None, []) when none exists.  The value is split as a shell would (shlex); an unbalanced
    quote is a ScanError naming the variable."""
    for var in defaults_vars(prog):
        if var in env:
            try:
                return var, shlex.split(env[var])
            except ValueError as e:
                raise ScanError("%s: %s" % (var, e))
    return None, []


def is_percent(tok: str, sign: str) -> bool:
    """`+%X` / `-%X`, X a single letter (§2.4)."""
    return len(tok) == 3 and tok[0] == sign and tok[1] == "%" and tok[2] in string.ascii_letters


def _is_option(tok: str) -> bool:
    return tok[:1] in OPTION_CHARS and tok not in tuple(OPTION_CHARS)


def prescan(tokens: List[str], job: SpectreJob) -> List[str]:
    """Takes `+%X string` (the value is the next token), `-%X` and the help options out of
    `tokens`, recording them on `job` (percent, action, help_topic), and returns the rest for
    optable.scan (§2.1, §2.4).  A help option's topic is the next token when there is one and it
    does not start with an option character."""
    out: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if is_percent(tok, "+"):
            if i + 1 >= len(tokens):
                raise ScanError("option %s needs a value" % tok)
            job.percent[tok[2]] = tokens[i + 1]
            i += 2
        elif is_percent(tok, "-"):
            job.percent[tok[2]] = None
            i += 1
        elif tok in HELP_OPTIONS:
            job.action = "help"
            if i + 1 < len(tokens) and not tokens[i + 1][:1] in OPTION_CHARS:
                job.help_topic = tokens[i + 1]
                i += 2
            else:
                i += 1
        else:
            out.append(tok)
            i += 1
    return out


def not_read(tokens: List[str], job: SpectreJob, var: str) -> List[str]:
    """The defaults layer without the options Spectre does not read from `var` (§2.2); each one
    dropped is recorded on `job` as NOTED with the note "Spectre does not read <opt> from <var>;
    ignored".  A help option's topic goes with it."""
    out: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        shown = None
        if is_percent(tok, "-") or tok in NOT_READ_FLAGS or (
                "=" in tok and tok.split("=", 1)[0] in NOT_READ_EQ):
            shown = tok
            i += 1
            if tok in HELP_OPTIONS and i < len(tokens) and not tokens[i][:1] in OPTION_CHARS:
                shown = "%s %s" % (tok, tokens[i])
                i += 1
        elif tok in NOT_READ_NEXT:
            if i + 1 < len(tokens):
                shown = "%s %s" % (tok, tokens[i + 1])
                i += 2
            else:
                shown = tok
                i += 1
        else:
            out.append(tok)
            i += 1
            continue
        job.note(shown, NOTED, "Spectre does not read %s from %s; ignored" % (shown.split()[0], var))
    return out


def without_vamos_options(tokens: List[str], job: SpectreJob, var: str) -> List[str]:
    """The defaults layer without `--vamos-*` tokens, each recorded UNSUPPORTED (§1): vamos
    options are read from the command line only."""
    out: List[str] = []
    for tok in tokens:
        if tok.startswith("--vamos-"):
            job.note(tok, UNSUPPORTED, VAMOS_OPTIONS_NOTE % var)
        else:
            out.append(tok)
    return out


def field_default(name: str):
    """The SpectreJob field's own default (a fresh list or dict for a factory field)."""
    f = _FIELDS[name]
    if f.default is not dataclasses.MISSING:
        return f.default
    return f.default_factory()  # type: ignore[misc]


def effective(job: SpectreJob, name: str):
    """A setting across the two layers (§2.2): argv's value when argv set the field (it is not
    None and not the field's own default), else the defaults layer's (`job.defaults`), else the
    field's default.  List and dict fields are merged at build time and returned as they are."""
    val = getattr(job, name)
    if isinstance(val, (list, dict)):
        return val
    if val is None or val == field_default(name):
        if name in job.defaults:
            return job.defaults[name]
    return val


def cpp_runs(job: SpectreJob) -> bool:
    """§2.6: cpp runs with -E, or with -D, -U or -I unless -disableCPP is given."""
    if effective(job, "cpp"):
        return True
    if effective(job, "disable_cpp"):
        return False
    return bool(job.defines or job.undefines or job.incdirs)


def merged_classes(job: SpectreJob) -> Dict[str, bool]:
    """The message classes printed (+error/-error, +warn/-warn, +note/-note, +info/-info,
    +debug/-debug, §2.3): every class on unless turned off; argv wins over the defaults per class
    (job.classes holds the merge)."""
    on = {"error": True, "warn": True, "note": True, "info": True, "debug": True}
    on.update(job.classes)
    return on
