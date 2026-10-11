"""The spectre job record and the merged settings (docs/VAMOS_SPECTRE_DESIGN.md §2.2, §2.3; the
API is the §10 block).

Phase 0 (§12): SpectreJob (with notes and defaults) and Settings are frozen; settings() is the
signature S5 implemented in phase 1 (the four options-statement settings, the
paramdefault rows).

SpectreJob implements the job protocol optable.scan uses: note() and unmapped.  Its scalar
fields hold argv's values (None when argv does not set one) and `defaults` the defaults
layer's, by field name (§2.2): argv and the defaults are scanned as two layers and kept apart.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from vamos.job import NOTED, Unmapped
from vamos.netlist.ir import Netlist
from vamos.notes import ERROR, Note


@dataclass
class SpectreJob:
    prog: str                                           # the only positional fields: prog, argv, cwd
    argv: List[str]
    cwd: str
    netlist: Optional[str] = None                       # as given; None = stdin
    raw: Optional[str] = None
    fmt: Optional[str] = None
    outdir: Optional[str] = None
    log_mode: str = "screen"                            # screen (stdout) | both | file
    log_path: Optional[str] = None
    percent: Dict[str, Optional[str]] = field(default_factory=dict)   # +%X / -%X
    cpp: bool = False
    defines: List[str] = field(default_factory=list)
    undefines: List[str] = field(default_factory=list)
    incdirs: List[str] = field(default_factory=list)
    disable_cpp: bool = False
    config: List[str] = field(default_factory=list)      # after the =config / -config rules (§2.3)
    pre_config: List[str] = field(default_factory=list)
    paramdefault: List[str] = field(default_factory=list)
    classes: Dict[str, bool] = field(default_factory=dict)
    maxwarns: Optional[int] = None
    maxnotes: Optional[int] = None
    maxwarnstolog: Optional[int] = None
    maxnotestolog: Optional[int] = None
    errpreset: Optional[str] = None                     # +errpreset=
    aps: Optional[str] = None                           # +aps= / ++aps=
    mts: bool = True                                    # False under -mts
    ahdllibdir: Optional[str] = None
    va_defines: List[str] = field(default_factory=list)
    escchars: bool = False
    action: str = "run"                                 # run | version | subversion | help
    help_topic: Optional[str] = None
    unmapped: List[Unmapped] = field(default_factory=list)   # optable's five dispositions only
    notes: List[Note] = field(default_factory=list)          # scan-time errors (§2.3): exit 2 before the run dir
    defaults: Dict[str, object] = field(default_factory=dict)  # the defaults layer's scalars, by field name; the
    #                                                            scalar fields above hold argv's (None: not given)

    def note(self, option: str, disposition: str, note: str = "") -> None:
        """optable.scan's record of an option with a string disposition (as vamos.job.Job.note)."""
        self.unmapped.append(Unmapped(option, disposition, note))


@dataclass
class Settings:
    """The merged §2.2 layers, after parsing: argv, then the netlist's options, then job.defaults."""
    fmt: str = "psfascii"
    raw: str = ""
    outdir: Optional[str] = None
    maxwarns: Optional[int] = None
    maxnotes: Optional[int] = None
    maxwarnstolog: Optional[int] = None
    maxnotestolog: Optional[int] = None
    errpreset: Optional[str] = None
    aps: Optional[str] = None
    paramdefault: List[Tuple[str, str, str]] = field(default_factory=list)   # (primitive, parameter, value)
    options: Dict[str, object] = field(default_factory=dict)                 # global options after precedence


def _text(value: object) -> Optional[str]:
    """An options value as text: a Str or Name expression, a bare string, or a number."""
    text = getattr(value, "text", None)
    if isinstance(text, str):
        return text
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    if isinstance(value, str):
        return value
    num = getattr(value, "value", value)
    if isinstance(num, (int, float)) and not isinstance(num, bool):
        return repr(num)
    return None


def _int(value: object) -> Optional[int]:
    num = getattr(value, "value", value)
    if isinstance(num, bool) or not isinstance(num, (int, float)):
        text = _text(value)
        try:
            return int(text, 10) if text is not None else None
        except ValueError:
            return None
    return int(num)


def _first(*values):
    for v in values:
        if v is not None:
            return v
    return None


def _format(job: SpectreJob, origin: str, text: str) -> Optional[str]:
    """§2.3's -format rule applied to an `options rawfmt=`: the three formats as given, the psf
    family as psfascii (a note), anything else an error on job.notes."""
    v = text.lower()
    if v in ("psfascii", "nutascii", "nutbin"):
        return v
    if v in ("psfbin", "psfbinf", "psfxl", "sst2", "fsdb", "fsdb5", "wdf", "wsfbin", "wsfascii", "awb", "tr0ascii"):
        job.note("options rawfmt=%s" % text, NOTED, "psfascii is written")
        return "psfascii"
    job.notes.append(Note(ERROR, origin, "options rawfmt=%s: unknown output format (vamos writes psfascii, "
                                         "nutascii and nutbin)" % text))
    return None


def paramdefault_rows(job: SpectreJob) -> List[Tuple[str, str, str]]:
    """The +paramdefault files (after the +/=/- rules of §2.3, defaults first) as (primitive,
    parameter, value) rows [M ref19 p.26]: one row per non-blank line of three fields; a file that
    cannot be read or a malformed line is an error on job.notes."""
    rows: List[Tuple[str, str, str]] = []
    for path in job.paramdefault:
        full = path if os.path.isabs(path) else os.path.join(job.cwd, path)
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError as e:
            job.notes.append(Note(ERROR, "", "+paramdefault %s: cannot be read (%s)" % (path, e.strerror or e)))
            continue
        for i, line in enumerate(lines, 1):
            fields = line.split()
            if not fields or fields[0].startswith(("//", "#", "*")):
                continue
            if len(fields) != 3:
                job.notes.append(Note(ERROR, "%s:%d" % (path, i),
                                      "+paramdefault: a line holds three entries, primitive parameter value"))
                continue
            rows.append((fields[0], fields[1], fields[2]))
    return rows


def settings(job: SpectreJob, nl: Netlist) -> Settings:
    """§2.2: argv, then the netlist's options statements, then job.defaults (S5).

    -format/rawfmt, -raw/rawfile, -maxwarns/maxwarns and -maxnotes/maxnotes are the four settings
    an `options` statement can also set; every other setting is argv over the defaults.  `raw`
    stays "" when no layer names the results path (the flow then applies %C:r.raw and -outdir,
    §2.5).  `options` is the netlist's dict with the four resolved values written over it (as
    Str/Num expressions).  A bad rawfmt value or paramdefault file is an error on job.notes."""
    from vamos.netlist.expr_ast import Num, Str
    from vamos.spectre.args import effective

    nlopts = dict(nl.options)
    origin = nl.spelling.get("options", "") if isinstance(nl.spelling, dict) else ""
    fmt: Optional[str] = job.fmt
    if fmt is None and "rawfmt" in nlopts:
        text = _text(nlopts["rawfmt"])
        if text is not None:
            fmt = _format(job, origin, text)
    if fmt is None:
        d = job.defaults.get("fmt")
        fmt = d if isinstance(d, str) else "psfascii"
    raw = _first(job.raw, _text(nlopts["rawfile"]) if "rawfile" in nlopts else None, job.defaults.get("raw"), "")
    maxwarns = _first(job.maxwarns, _int(nlopts["maxwarns"]) if "maxwarns" in nlopts else None,
                      job.defaults.get("maxwarns"))
    maxnotes = _first(job.maxnotes, _int(nlopts["maxnotes"]) if "maxnotes" in nlopts else None,
                      job.defaults.get("maxnotes"))
    s = Settings(fmt=fmt, raw=str(raw), outdir=effective(job, "outdir"), maxwarns=maxwarns, maxnotes=maxnotes,
                 maxwarnstolog=effective(job, "maxwarnstolog"), maxnotestolog=effective(job, "maxnotestolog"),
                 errpreset=effective(job, "errpreset"), aps=effective(job, "aps"),
                 paramdefault=paramdefault_rows(job), options=nlopts)
    nlopts["rawfmt"] = Str(fmt)
    if raw:
        nlopts["rawfile"] = Str(str(raw))
    if maxwarns is not None:
        nlopts["maxwarns"] = Num(float(maxwarns))
    if maxnotes is not None:
        nlopts["maxnotes"] = Num(float(maxnotes))
    return s
