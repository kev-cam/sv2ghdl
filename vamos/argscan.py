"""Shared command-line tokenizing: option-file expansion and helpers.

Vendor tools read further arguments from option files:
  -f FILE     paths inside are relative to the current directory
  -F FILE     paths inside are relative to FILE's own directory
  -file FILE  same as -f
Files may nest, contain // and /* */ comments, '#' comment lines, quoted
strings and $VAR / ${VAR} environment references.
"""

import os
import re
import shlex
from typing import List, Optional, Tuple

_ENV_RE = re.compile(r"\$\{(\w+)\}|\$(\w+)|\$\((\w+)\)")

MAX_NESTING = 32


class ArgError(Exception):
    pass


def expand_env(text: str) -> str:
    """Expand $VAR, ${VAR} and $(VAR); unset variables expand to ''."""
    def sub(m):
        name = m.group(1) or m.group(2) or m.group(3)
        return os.environ.get(name, "")
    return _ENV_RE.sub(sub, text)


def strip_comments(text: str) -> str:
    """Remove /* */ blocks, // to end of line, and lines starting with '#'.

    Comment markers inside quoted strings are left alone.
    """
    out = []
    i, n = 0, len(text)
    quote = None
    line_blank = True          # only whitespace so far on this line
    while i < n:
        c = text[i]
        if c == "\n":
            line_blank = True
        elif not c.isspace() and c != "#":
            line_blank = False
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'":
            quote = c
            out.append(c)
            i += 1
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "#" and line_blank:
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def tokenize_file(path: str) -> List[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        raise ArgError("cannot open option file '%s': %s" % (path, e.strerror))
    text = expand_env(strip_comments(text))
    try:
        return shlex.split(text, comments=False, posix=True)
    except ValueError as e:
        raise ArgError("cannot parse option file '%s': %s" % (path, e))


# Options whose value is a path.  Inside a -F file, a relative value is
# rebased onto the file's directory.  Maps option -> kind:
#   "next": value is the next token;  "plus": +opt+a+b+ list of paths
_PATH_OPTS = {"-v": "next", "-y": "next", "+incdir+": "plus"}


def _rebase(tok: str, base: str) -> str:
    if not tok or os.path.isabs(tok) or tok.startswith(("-", "+")):
        return tok
    return os.path.normpath(os.path.join(base, tok))


def expand_option_files(args: List[str], cwd: str,
                        _depth: int = 0, _rel_base: Optional[str] = None,
                        _seen: Tuple[str, ...] = ()) -> List[str]:
    """Return args with every -f/-F/-file expanded in place, recursively.

    _rel_base is set while expanding a -F file: relative source files and path
    options are rebased onto it.
    """
    if _depth > MAX_NESTING:
        raise ArgError("option files nested more than %d deep" % MAX_NESTING)
    out: List[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-f", "-F", "-file"):
            if i + 1 >= len(args):
                raise ArgError("%s needs a file argument" % a)
            fname = args[i + 1]
            if _rel_base and not os.path.isabs(fname):
                fname = os.path.join(_rel_base, fname)
            elif not os.path.isabs(fname):
                fname = os.path.join(cwd, fname)
            fname = os.path.normpath(fname)
            if fname in _seen:
                raise ArgError("option file '%s' includes itself" % fname)
            toks = tokenize_file(fname)
            base = os.path.dirname(fname) if a == "-F" else _rel_base
            out.extend(expand_option_files(toks, cwd, _depth + 1, base,
                                           _seen + (fname,)))
            i += 2
            continue
        if _rel_base:
            kind = _PATH_OPTS.get(a)
            if kind == "next" and i + 1 < len(args):
                out.extend([a, _rebase(args[i + 1], _rel_base)])
                i += 2
                continue
            if a.startswith("+incdir+"):
                parts = [p for p in a[len("+incdir+"):].split("+") if p]
                out.append("+incdir+" + "+".join(_rebase(p, _rel_base) for p in parts))
                i += 1
                continue
            out.append(_rebase(a, _rel_base))
            i += 1
            continue
        out.append(a)
        i += 1
    return out


def plus_list(arg: str, prefix: str) -> List[str]:
    """'+incdir+a+b+' with prefix '+incdir+' -> ['a', 'b']."""
    return [p for p in arg[len(prefix):].split("+") if p]
