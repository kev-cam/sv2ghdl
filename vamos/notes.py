"""Diagnostics shared by the AMS flow and the netlist package.

Every construct vamos meets gets one disposition (docs/VAMOS_AMS_DESIGN.md §0):
silent (applied faithfully), note, warning (an approximation; --vamos-strict
makes it an error) or error (the compile fails).  Modules collect Note objects
and the flow prints them together; nothing is dropped without a message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

NOTE = "note"
WARNING = "warning"
ERROR = "error"
SEVERITIES = (NOTE, WARNING, ERROR)


@dataclass
class Note:
    severity: str
    origin: str          # "file:line", "vcsAD.init:3", a hierarchical name, or ""
    message: str

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError("bad note severity %r" % (self.severity,))

    def text(self) -> str:
        where = self.origin + ": " if self.origin else ""
        return "%s: %s%s" % (self.severity, where, self.message)


def note(origin: str, message: str) -> Note:
    return Note(NOTE, origin, message)


def warning(origin: str, message: str) -> Note:
    return Note(WARNING, origin, message)


def error(origin: str, message: str) -> Note:
    return Note(ERROR, origin, message)


def strict(notes: Iterable[Note], on: bool) -> List[Note]:
    """--vamos-strict: every warning becomes an error."""
    if not on:
        return list(notes)
    return [Note(ERROR, n.origin, n.message) if n.severity == WARNING else n for n in notes]


def has_errors(notes: Iterable[Note]) -> bool:
    return any(n.severity == ERROR for n in notes)


class NoteError(Exception):
    """Raised to abort a stage; carries the notes that explain why."""

    def __init__(self, notes: Iterable[Note]):
        self.notes = list(notes)
        super().__init__("; ".join(n.text() for n in self.notes))
