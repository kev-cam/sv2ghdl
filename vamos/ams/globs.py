"""VCS-style glob patterns (docs/VAMOS_AMS_DESIGN.md §3.2).

Only '*' is special.  '[', ']', '<', '>' and '.' are literal: a node name such
as tb.dut.din[3] must match itself, which fnmatch would read as a character
class.  Matching is case-insensitive unless set_sim_case says 'sensitive'.
"""

from __future__ import annotations

import re
from typing import Pattern


def has_wildcard(pattern: str) -> bool:
    return "*" in pattern


def compile_glob(pattern: str, case_sensitive: bool = False) -> Pattern:
    rx = ".*".join(re.escape(p) for p in pattern.split("*"))
    return re.compile(r"\A" + rx + r"\Z", 0 if case_sensitive else re.IGNORECASE)


def match(pattern: str, name: str, case_sensitive: bool = False) -> bool:
    if not has_wildcard(pattern):
        return pattern == name if case_sensitive else pattern.lower() == name.lower()
    return compile_glob(pattern, case_sensitive).match(name) is not None
