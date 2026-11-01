"""Escaped names: one reversible mapping for a name that a consumer cannot take literally (the user's rule,
2026-10-19: "Verilog uses escaped names to handle things which have odd characters, you can do something similar -
any name with odd characters gets translated to a safe one with a reversible mapping, e.g. start with _ and use
_<3 digit octal> for the odd characters").  Standard library only, Python 3.9.

escape_name(name) -> str
    A name made only of ASCII letters, digits and _ that does not start with _ is returned unchanged.  Any other
    name becomes _ followed by its UTF-8 bytes, each ASCII letter or digit as it is and every other byte, _ itself
    included, as _ plus its 3-digit octal code: - is _055, . is _056, _ is _137, < is _074, a space is _040, so
    spectre-23.1 is _spectre_05523_0561, b.c is _b_056c and a<0> is _a_0740_076.  The result is made of letters,
    digits and _ only: an environment-variable name every POSIX shell exports, and an identifier for both engines'
    decks.  A name that starts with _ is always escaped (_x is __137x), so an escaped form never equals a name that
    is left unchanged, and a name that merely looks escaped (_b_056c) is escaped again (__137b_137056c).  The empty
    name is _.  A str that holds a lone surrogate is encoded with surrogatepass, so every str has an escape.
unescape_name(name) -> str
    The inverse: unescape_name(escape_name(n)) == n for every str n.  A name escape_name leaves unchanged is
    returned as it is; anything that is neither such a name nor an escaped form is a ValueError.
is_escaped(name) -> bool
    Whether name is an escaped form, that is escape_name(n) for some n that escape_name changes: _ then letters,
    digits and _<3 octal digits> groups whose bytes decode, and canonical (a letter or digit is never written as
    its octal code, every other byte always is).  is_plain(name) is the other form: the names escape_name leaves
    unchanged.
unescape_text(text, sep=":") -> str
    An engine's message with every escaped name in it unescaped: each maximal run of letters, digits, _, $ and
    sep is split at sep, and each component that is an escaped form (other than a lone _) is replaced by its name;
    the engine's hierarchy separator sep stays.  For the engines' own messages, which name what vamos printed: a
    word that is not an escaped form (_tmp, vamos_a1, 0.5) is left as it is, and so is a word of a file name (one
    after a / or a ., or before a / or a . and a name character: a run directory's suffix n.run._1_0561b).
FILE_ENCODING, FILE_ERRORS
    The results files' text encoding (§14 q.62, decided 2026-10-23: UTF-8, for the PSF ASCII and nutmeg ASCII
    files, the state files and the logFile; round 4 wrote them in Latin-1, a name it could not hold escaped and a
    title with ?), and the error handler they are written with: a lone surrogate that os.fsdecode made of a byte
    that is not UTF-8 (U+DC80-U+DCFF, in a file name) goes back to that byte, as surrogateescape writes it, and any
    other lone surrogate is written as ? (holds_file_text tells the writer's caller, which warns).
holds_file_text(text) -> bool
    Whether the results files write text as it is: every str but one that holds a lone surrogate outside
    U+DC80-U+DCFF.
written_name(name, use="psf") -> str, written_unname(written, use="psf") -> str
    A name as a results file writes it, and back.  UTF-8 holds every name, so a name is escaped only where the file's
    format cannot hold a character of it: each component of the name (the runs between its separators . and :) that
    such a character is in is written in its escaped form, for use="nutmeg" (a nutmeg file's variable) one that holds
    a white-space character or a comma, where ngspice's reader ends a variable name (a b is _a_040b; measured on
    ngspice-45.2: `a b' was read as `a', with `Warning: bad var param voltage'), and for use="file" (an analysis or
    sweep name, which names the results' files) one that holds a /; so is a component that is itself the escaped
    form of such a component (a nutmeg variable named _a_040b is __137a_137040b), which keeps the mapping one-to-one;
    every other component as it is, and for use="psf" (a PSF trace, a state file) every name as it is.
    written_unname(written_name(n, u), u) == n for every n.
written_note(what, name, written, use) -> str
    The text of the note a writer gives, once per run, for a name written_name writes otherwise.
"""

from __future__ import annotations

import codecs
import re
from typing import Callable, List, Optional

_PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_]*\Z")
_KEPT = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
_FORM = re.compile(r"_(?:[A-Za-z0-9]|_[0-7]{3})*\Z")
_ENCODING = "utf-8"
_ERRORS = "surrogatepass"


def is_plain(name: str) -> bool:
    """A name escape_name leaves unchanged: ASCII letters, digits and _, not starting with _ (and not empty)."""
    return _PLAIN.match(name) is not None


def escape_name(name: str) -> str:
    """The escaped form of name (the module docstring); name itself when it is plain (is_plain)."""
    if is_plain(name):
        return name
    out: List[str] = ["_"]
    for b in name.encode(_ENCODING, _ERRORS):
        out.append(chr(b) if b in _KEPT else "_%03o" % b)
    return "".join(out)


def _decode(form: str) -> str:
    """The name an escaped form's bytes spell, without the canonical check; ValueError when it is not one."""
    if _FORM.match(form) is None:
        raise ValueError("%r is not an escaped name" % (form,))
    data = bytearray()
    i = 1
    while i < len(form):
        c = form[i]
        if c == "_":
            code = int(form[i + 1:i + 4], 8)
            if code > 0o377:
                raise ValueError("%r is not an escaped name: _%s is not a byte" % (form, form[i + 1:i + 4]))
            data.append(code)
            i += 4
        else:
            data.append(ord(c))
            i += 1
    try:
        return bytes(data).decode(_ENCODING, _ERRORS)
    except UnicodeDecodeError:
        raise ValueError("%r is not an escaped name: its bytes are not UTF-8" % (form,)) from None


def is_escaped(name: str) -> bool:
    """Whether name is an escaped form (the module docstring)."""
    if not name.startswith("_"):
        return False
    try:
        return escape_name(_decode(name)) == name
    except ValueError:
        return False


def unescape_name(name: str) -> str:
    """The name an escaped form stands for; a plain name as it is; ValueError for anything else."""
    if is_plain(name):
        return name
    if not name.startswith("_"):
        raise ValueError("%r is neither a plain name nor an escaped one" % (name,))
    decoded = _decode(name)
    if escape_name(decoded) != name:
        raise ValueError("%r is not an escaped name: escape_name never writes it" % (name,))
    return decoded


def unescape_text(text: str, sep: str = ":", known: Optional[Callable[[str], Optional[str]]] = None) -> str:
    """text with every escaped name component unescaped (the module docstring); the rest as it is.  known: a lookup
    tried first for each component (an engine's own spelling of a name, Xyce's R_R_0741_076 for R<1>), None for none."""
    if "_" not in text:
        return text
    # a word inside a file name (after a '/' or a '.', or before a '/' or a '.' that a name character follows: the
    # suffix of a run directory, n.run._1_0561b) is not a name the deck printed
    s = re.escape(sep)
    pat = re.compile(r"(?<![A-Za-z0-9_$./%s])[A-Za-z0-9_$%s]+(?![A-Za-z0-9_$/%s]|\.[A-Za-z0-9_$])" % (s, s, s))

    def comp(p: str) -> str:
        got = known(p) if known is not None else None
        if got is not None:
            return got
        # a lone _ (the empty name's form) is left as it is: no deck prints an empty name
        return unescape_name(p) if len(p) > 1 and is_escaped(p) else p

    def one(m: "re.Match[str]") -> str:
        word = m.group(0)
        if "_" not in word:
            return word
        return sep.join(comp(p) for p in word.split(sep))

    return pat.sub(one, text)


# -- the results files' text (§14 q.62, FIX2E5) ----------------------------------------------------------------------

FILE_ENCODING = "utf-8"              # the PSF ASCII, nutmeg ASCII and state files and the logFile (round 4: Latin-1)
FILE_ERRORS = "vamos_results"        # registered below


def _results_errors(exc: UnicodeError) -> tuple:
    """The results files' encoding error handler: a lone surrogate os.fsdecode made of a byte that is not UTF-8
    (U+DC80-U+DCFF, surrogateescape's range) is written as that byte, as surrogateescape writes it, so a file name
    that is not UTF-8 goes into a state file's header as it is on disk; any other lone surrogate (none that a
    netlist read with errors="replace" or a file name can hold) is written as `?'."""
    if not isinstance(exc, UnicodeEncodeError):
        raise exc
    out = bytearray()
    for c in exc.object[exc.start:exc.end]:
        o = ord(c)
        out += bytes([o - 0xDC00]) if 0xDC80 <= o <= 0xDCFF else b"?"
    return bytes(out), exc.end


codecs.register_error(FILE_ERRORS, _results_errors)


def holds_file_text(text: str) -> bool:
    """Whether the results files write text as it is (FILE_ENCODING with FILE_ERRORS writes every character but a lone
    surrogate outside U+DC80-U+DCFF, which becomes `?': the caller warns of such a title or file name)."""
    try:
        text.encode(FILE_ENCODING, "surrogateescape")
    except UnicodeEncodeError:
        return False
    return True


_NAME_PARTS = re.compile(r"([.:])")
# what a results file's format cannot hold in a name, by use: ngspice's rawfile reader ends a variable name at white
# space or a comma (ngspice-45.2), and a file name cannot hold a /; a PSF string and a state-file entry hold every
# character (UTF-8, FILE_ENCODING; the state file's line format escapes a blank, a # and a : with a backslash itself)
_ODD = {"psf": None, "nutmeg": re.compile(r"[\s,]"), "file": re.compile(r"/")}


def _odd_escaped(part: str, use: str) -> bool:
    """Whether written_name writes a component in its escaped form: the use's format cannot hold a character of it
    (_ODD), or it is the escaped form of a component that is so written (its form decodes to one; each decoding is
    shorter, so this ends)."""
    odd = _ODD[use]
    if odd is None:
        return False
    if odd.search(part) is not None:
        return True
    return is_escaped(part) and _odd_escaped(_decode(part), use)


def written_name(name: str, use: str = "psf") -> str:
    """name as a results file writes it (the module docstring); use: "psf" (a PSF trace, a state file: always the name
    itself), "nutmeg" (a nutmeg variable) or "file" (an analysis or sweep name); the name itself when no component
    needs escaping."""
    odd = _ODD[use]
    if odd is None or ("_" not in name and not odd.search(name)):
        return name
    return "".join(p if p in (".", ":") or not _odd_escaped(p, use) else escape_name(p)
                   for p in _NAME_PARTS.split(name))


def written_note(what: str, name: str, written: str, use: str) -> str:
    """The text of the note a writer gives once per run for a name written_name writes otherwise."""
    odd = _ODD[use]
    if use == "nutmeg" and odd is not None and odd.search(name):
        return ("%s %s is written as %s in the nutmeg file: ngspice ends a variable name at a blank or a comma, so it "
                "is written escaped by the rule of the engines' decks" % (what, name, written))
    if use == "file" and odd is not None and odd.search(name):
        return ("%s %s is written as %s: a / cannot be part of the name of its results file, so the name is written "
                "escaped by the rule of the engines' decks" % (what, name, written))
    return ("%s %s is written as %s: the name reads as the escaped form of another, which the results files write so, "
            "so it is escaped too" % (what, name, written))


def written_unname(written: str, use: str = "psf") -> str:
    """The name written_name writes as `written` (the inverse; a text written_name never writes comes back as it is)."""
    if "_" not in written or _ODD[use] is None:
        return written
    out: List[str] = []
    for p in _NAME_PARTS.split(written):
        if p not in (".", ":") and is_escaped(p):
            got = _decode(p)
            if _odd_escaped(got, use):
                p = got
        out.append(p)
    return "".join(out)


__all__ = ["escape_name", "unescape_name", "is_escaped", "is_plain", "unescape_text", "FILE_ENCODING", "FILE_ERRORS",
           "holds_file_text", "written_name", "written_unname", "written_note"]
