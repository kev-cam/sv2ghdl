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
"""

from typing import Callable, List, Optional, Sequence, Union

from vamos.job import IGNORED, NOTED, UNKNOWN, UNSUPPORTED, Job

Action = Union[str, Callable]


class Opt:
    __slots__ = ("name", "arity", "act", "note")

    def __init__(self, name: str, arity: str = "flag",
                 act: Action = IGNORED, note: str = ""):
        assert arity in ("flag", "next", "eq", "plus", "prefix"), arity
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


def strict_failures(job: Job) -> List[str]:
    return [u.option for u in job.unmapped
            if u.disposition in (UNSUPPORTED, UNKNOWN)]
