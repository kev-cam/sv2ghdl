"""PSF ASCII data files and the logFile (docs/VAMOS_SPECTRE_DESIGN.md §8.2, §8.3; the API is the
§10 block).

Phase 0 (§12): the format constants and PsfHead are frozen; psf_string() and psf_date() are the
signatures S4 implements in phase 1 and raise NotImplementedError until then.  S4 also adds
TYPES (the fixed 23.1 TYPE lists per family, copied verbatim from the samples, §8.3),
write_analysis() and write_logfile().

The format fields are constants here, not banner keys, so a banner profile (`none` included)
never changes them: readers may key on them (§8.2).  "version" is always vamos's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

# §8.2: data-format fields that readers may key on, so they keep Spectre's values
SIMULATOR = "spectre"
LOG_GENERATOR = "drlLog rev. 1.0"
SIM_MODE = "Spectre"
SIGNAL_NAME_TYPE = "spectre"

TYPES: Dict[str, List[str]]     # the fixed 23.1 TYPE lists per family (§8.3); S4 fills it from the samples


@dataclass
class PsfHead:
    """Phase 0: the header values every data file and the logFile carry (§8.2, §8.3)."""
    version: str = ""
    date: str = ""
    design: str = ""
    simulator: str = SIMULATOR
    psfversion: str = "1.4.0"
    precision: str = "%.15e"                            # VALUE reals (options precision, §8.3)
    prop_precision: str = "%#g"                         # logFile PROP reals


def psf_string(s: str, escchars: bool = False) -> str:
    """The one PSF string encoder (§8.3): a backslash before `"` and `\\`; under +escchars (True) also
    before every character outside [A-Za-z0-9_.:!].  Applied to the unescaped name or text only."""
    raise NotImplementedError("psf.psf_string is implemented in phase 1 (S4; design §8.3, §10)")


def psf_date(t: datetime) -> str:
    """§8.2's "date": fixed English tables, never strftime: "%d:%02d:%02d %s, %s %s %d, %d" with the
    hour on a 12-hour clock unpadded, AM/PM, Sun..Sat, Jan..Dec, the day unpadded, the year
    (1:07:28 PM, Tue Feb 2, 2021)."""
    raise NotImplementedError("psf.psf_date is implemented in phase 1 (S4; design §8.2, §10)")
