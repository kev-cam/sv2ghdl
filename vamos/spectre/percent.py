"""Percent codes and colon modifiers (docs/VAMOS_SPECTRE_DESIGN.md §2.4; the API is the §10
block) [M UG pp.264-268].

codes() gives the predefined codes of a run (%A %C %D %H %M %P %S %T %V) with the job's `+%X`
definitions and `-%X` undefinitions applied; expand() substitutes them in a text, applying the
colon modifiers `:r :e :h :t ::` left to right.  Substitution is not recursive.  The UG's
`/tmp%C:t:r.raw -> /tmp/opamp.raw` example contradicts its own definition of `:t` (a tail has no
leading slash); vamos follows the definition and gives `/tmpopamp.raw` (§14 q.1).
"""

from __future__ import annotations

import os
import socket
import string
from datetime import datetime
from typing import Dict, Mapping

from vamos import VERSION
from vamos.spectre.job import SpectreJob

MODIFIERS = "reht:"


def host_name() -> str:
    try:
        return socket.gethostname() or "localhost"
    except OSError:
        return "localhost"


def predefined(prog: str, netlist: object, start: datetime, analysis: str = "") -> Dict[str, str]:
    """The predefined codes [M UG pp.264-265]: %A the analysis, %C the input as given (`stdin`
    for standard input), %D yy-mm-dd, %H the host, %M the CMI version (none here), %P the pid,
    %S the invoked program name, %T hh:mm:ss (24 h), %V vamos's version."""
    return {
        "A": analysis,
        "C": "stdin" if netlist is None else str(netlist),
        "D": "%02d-%02d-%02d" % (start.year % 100, start.month, start.day),
        "H": host_name(),
        "M": "",
        "P": str(os.getpid()),
        "S": prog,
        "T": "%02d:%02d:%02d" % (start.hour, start.minute, start.second),
        "V": VERSION,
    }


def codes(job: SpectreJob, start: datetime, analysis: str = "") -> Dict[str, str]:
    """The run's codes: the predefined ones, then the job's `+%X string` definitions; a `-%X`
    falls back to the predefined value, or to an empty string (§2.4).  job.percent holds the
    defaults layer's entries with argv's applied over them, so argv's `-%X` undefines a `+%X`
    from the defaults [M UG p.266]."""
    out = predefined(job.prog, job.netlist, start, analysis)
    for x, val in job.percent.items():
        if val is None:
            out[x] = predefined(job.prog, job.netlist, start, analysis).get(x, "")
        else:
            out[x] = val
    return out


def _split(p: str):
    i = p.rfind("/")
    return (p[:i + 1], p[i + 1:]) if i >= 0 else ("", p)


def modify(value: str, mod: str) -> str:
    """One colon modifier [M UG pp.266-267]: `r` the root (the extension removed), `e` the
    extension, `h` the head (before the last slash; `.` when there is none), `t` the tail (after
    the last slash; the whole value when there is none)."""
    head, tail = _split(value)
    if mod == "r":
        i = tail.rfind(".")
        return head + tail[:i] if i >= 0 else value
    if mod == "e":
        i = tail.rfind(".")
        return tail[i + 1:] if i >= 0 else ""
    if mod == "h":
        return head[:-1] if head else "."
    if mod == "t":
        return tail
    raise ValueError(mod)


def expand(text: str, codes: Mapping[str, str]) -> str:
    """%X (a letter) with its colon modifiers, and %% a literal percent (§2.4).  A code with no
    value is an empty string [M UG p.266].  Any other character after a colon ends the
    modification and stays in the text with its colon; `::` appends a literal colon and ends it.
    A `%` followed by anything else is kept.  The substituted value is not rescanned."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c != "%" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        x = text[i + 1]
        if x == "%":
            out.append("%")
            i += 2
            continue
        if x not in string.ascii_letters:
            out.append(c)
            i += 1
            continue
        value = codes.get(x, "")
        i += 2
        while i + 1 < n and text[i] == ":" and text[i + 1] in MODIFIERS:
            mod = text[i + 1]
            i += 2
            if mod == ":":
                value += ":"
                break
            value = modify(value, mod)
        out.append(value)
    return "".join(out)
