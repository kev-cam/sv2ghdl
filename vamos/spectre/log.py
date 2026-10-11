"""The `+log` file and the screen of a spectre run (docs/VAMOS_SPECTRE_DESIGN.md §8.7; the API
is the §10 block).

Every line a spectre run prints goes through SpectreLog, to stdout and/or the +log/=log file
per the log mode (`-log` the screen only, `+log f` both, `=log f` the file only, §2.5): the
provenance header and other plain lines (line), vamos's notes, warnings and errors as Spectre
messages (message), the analysis banners, the SIGUSR1 status line and the trailer (finish).
Nothing goes to stderr.  The layout comes from the banner profile (banners/spectre.json), with
built-in text for every key: a profile may change the layout of version, subversion,
error_block, warning_block, notice_block, trailer_ok and trailer_fatal but never suppress them
(under --vamos-banner=none, for a missing key or an invalid template the built-in text is
used; an invalid template also gets one notice); `none` drops only run_start, inventory,
analysis_banner, analysis_done and audit.  `{prog}` is the invoked name (§2.1).

Messages take the 5.1.41 form [M UG pp.270, 274]: "Error from <prog> during <phase>." and the
indented text.  The counts of the trailer are every error, warning and notice given to
message(), printed or not.  -maxwarns/-maxnotes (and their -tolog variants) limit how often one
message id is printed per analysis, on the screen and in the file; the id is the text with
every number replaced by `#` and every quoted name by one placeholder [I, §14 q.40]; at the
limit one notice says that further ones are suppressed.  The limits are argv + defaults until
limits() applies the merged Settings (§2.2).
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime
from typing import Dict, Mapping, Optional, Tuple

from vamos import VERSION
from vamos.banner import Banner
from vamos.notes import ERROR, NOTE, WARNING, Note
from vamos.spectre import percent
from vamos.spectre.args import effective, merged_classes
from vamos.spectre.job import Settings, SpectreJob

# The built-in text of every banner key (§8.7): the shipped banners/spectre.json, and what is
# printed under --vamos-banner=none, for a missing key or an invalid template.
BUILTIN: Dict[str, str] = {
    "version": "{brand} {version} ({personality} personality)",
    "subversion": "{brand} {version} ({personality} personality)",
    "run_start": "Simulating `{netlist}' on {host} at {time}.\n",
    "inventory": "Circuit inventory:\n{inventory_lines}",
    "analysis_banner": "{rule}\n{analysis_title} `{analysis}': {range}\n{rule}",
    "analysis_done": "Total time required for {analysis_type} analysis `{analysis}' was {seconds:.2f} s.\n",
    "audit": "Aggregate audit ({time}):\nTime used: CPU = {cpu:.2f} s, elapsed = {elapsed:.0f} s.",
    "error_block": "Error from {prog} during {phase}.\n    {text}",
    "warning_block": "Warning from {prog} during {phase}.\n    {text}",
    "notice_block": "Notice from {prog} during {phase}.\n    {text}",
    "trailer_ok": "{prog} completes with {errors} errors, {warnings} warnings, and {notices} notices",
    "trailer_fatal": "{prog} terminated prematurely due to fatal error.",
}

# The keys `none` drops (VAMOS_PLAN.md §4b: `none` prints only the provenance header); the
# others always print, from the profile or the built-in text (§8.7).
OPTIONAL_KEYS = ("run_start", "inventory", "analysis_banner", "analysis_done", "audit")

ANALYSIS_TITLES = {"op": "DC Analysis", "dc": "DC Analysis", "ac": "AC Analysis", "noise": "Noise Analysis",
                   "xf": "XF Analysis", "tran": "Transient Analysis", "sweep": "Sweep"}

# severity -> (message class of ±<class>, banner key, trailer counter)
_SEVERITY = {ERROR: ("error", "error_block", "errors"), WARNING: ("warn", "warning_block", "warnings"),
             NOTE: ("note", "notice_block", "notices")}

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_QUOTED = re.compile(r"`[^']*'|'[^']*'|\"[^\"]*\"")
_NUMBER = re.compile(r"\d+(?:\.\d*)?(?:[eE][-+]?\d+)?")
_INVALID = "vamos: banner template"
_PREFIXES = ("y", "z", "a", "f", "p", "n", "u", "m", "", "k", "M", "G", "T", "P", "E", "Z", "Y")


def spectre_time(dt: datetime) -> str:
    """Spectre's date: `1:22:02 PM, Wed Aug 20, 2003` [M UG p.33], from fixed English tables, the
    hour on a 12-hour clock unpadded, the day unpadded (as §8.2's `date`, never strftime)."""
    hour = dt.hour % 12 or 12
    return "%d:%02d:%02d %s, %s %s %d, %d" % (hour, dt.minute, dt.second, "PM" if dt.hour >= 12 else "AM",
                                             _DAYS[dt.weekday()], _MONTHS[dt.month - 1], dt.day, dt.year)


def message_id(text: str) -> str:
    """The id of a message for -maxwarns/-maxnotes: every quoted name one placeholder, every
    number `#`, so two warnings that differ only in a node name or a value share an id."""
    return _NUMBER.sub("#", _QUOTED.sub("`'", text))


def si(value: float, units: str = "") -> str:
    """A number as Spectre's descriptions print it: %g with an SI prefix (`80 us`, `1 MHz`)."""
    if value == 0 or not (value == value) or value in (float("inf"), float("-inf")):
        return ("%g %s" % (value, units)).rstrip()
    exp = 0
    v = abs(value)
    while v >= 1000 and exp < 8:
        v /= 1000.0
        exp += 1
    while v < 1 and exp > -8:
        v *= 1000.0
        exp -= 1
    prefix = _PREFIXES[exp + 8]
    text = "%g" % (v if value > 0 else -v)
    if not prefix and not units:
        return text
    return "%s %s%s" % (text, prefix, units)


class SpectreLog:
    """stdout/file routing per log mode, the message classes, maxwarns/maxnotes per (id, analysis),
    the counts and the trailer; built-in text for every banner key (§8.7)."""

    def __init__(self, job: SpectreJob, banner: Banner, start: datetime) -> None:
        self.job = job
        self.banner = banner
        self.start = start
        self.prog = job.prog
        self.counts = {"errors": 0, "warnings": 0, "notices": 0}
        self.analysis = ""
        self.classes = merged_classes(job)
        self.file = None
        self.path: Optional[str] = None
        self._seen: Dict[Tuple[str, str, str, str], int] = {}
        self._bad_keys: set = set()
        self._closed = False
        self._cpu0 = _cpu()
        self._limits = {"screen": (effective(job, "maxwarns"), effective(job, "maxnotes")),
                        "file": (effective(job, "maxwarnstolog"), effective(job, "maxnotestolog"))}
        mode = effective(job, "log_mode")
        self.screen = mode != "file"
        path = effective(job, "log_path")
        if mode in ("both", "file") and path:
            p = percent.expand(path, percent.codes(job, start))
            if not os.path.isabs(p):
                base = job.cwd
                outdir = effective(job, "outdir")
                if outdir and "/" not in p and os.sep not in p:
                    base = os.path.join(job.cwd, outdir)
                p = os.path.join(base, p)
            self.path = p
            self.file = open(p, "w", encoding="utf-8")          # OSError: flow.run maps it (§2.7)

    # -- the two destinations ------------------------------------------------------------------

    def _emit(self, text: str, screen: bool = True, file: bool = True) -> None:
        if screen and self.screen:
            out = sys.stdout
            out.write(text + "\n")
            out.flush()
        if file and self.file and not self._closed:
            self.file.write(text + "\n")
            self.file.flush()

    def line(self, text: str) -> None:
        """A plain line: the provenance header, a --vamos-verbose command echo, the run-directory
        note (§1); never filtered or counted."""
        self._emit(text)

    def file_only(self, text: str) -> None:
        """A line for the log file alone (an internal error's traceback, §2.7)."""
        self._emit(text, screen=False)

    def close(self) -> None:
        if self.file and not self._closed:
            self.file.close()
        self._closed = True

    # -- banner text -------------------------------------------------------------------------

    def _vals(self, **fields) -> Dict[str, object]:
        vals: Dict[str, object] = dict(prog=self.prog, personality="spectre", version=VERSION, brand="vamos")
        vals.update(fields)
        return vals

    def text(self, key: str, **fields) -> Optional[str]:
        """The banner text of `key`: the profile's, else the built-in text; None for an optional
        key under `none` or when the profile leaves it out (§8.7)."""
        vals = dict(prog=self.prog)
        vals.update(fields)
        t = self.banner.text(key, **vals) if self.banner is not None else None
        if t is not None and t.startswith(_INVALID):
            if key not in self._bad_keys:
                self._bad_keys.add(key)
                self.message(Note(NOTE, "", "%s; the built-in text is used" % t[len("vamos: "):]), "setup")
            t = None
        elif t is not None:
            return t
        if key in OPTIONAL_KEYS and (self.banner is None or self.banner.profile is None
                                     or key not in self.banner.profile):
            return None
        return BUILTIN[key].format(**self._vals(**fields))

    def version_text(self, which: str = "version") -> str:
        """The -V / -W line (§2.3, §8.7)."""
        return self.text("subversion" if which == "subversion" else "version") or ""

    # -- messages ----------------------------------------------------------------------------

    def _allowed(self, dest: str, severity: str, mid: str) -> Optional[bool]:
        """Whether a message of this id may still print on `dest`: True, False (over the limit),
        None (the first one over the limit: print the suppression notice instead)."""
        limit = self._limits[dest][0 if severity == WARNING else 1]
        if severity == ERROR or limit is None:
            return True
        key = (dest, severity, mid, self.analysis)
        n = self._seen.get(key, 0) + 1
        self._seen[key] = n
        if n <= limit:
            return True
        return None if n == limit + 1 else False

    def message(self, note: Note, phase: str) -> None:
        """A note as a Spectre message [M UG pp.270, 274], counted for the trailer whether or not
        its class is printed; -maxwarns/-maxnotes apply per (id, analysis) and destination."""
        cls, key, counter = _SEVERITY[note.severity]
        self.counts[counter] += 1
        if not self.classes.get(cls, True):
            return
        body = ("%s: %s" % (note.origin, note.message)) if note.origin else note.message
        text = body.replace("\n", "\n    ")
        mid = message_id(body)
        for dest in ("screen", "file"):
            if (dest == "screen" and not self.screen) or (dest == "file" and not self.file):
                continue
            ok = self._allowed(dest, note.severity, mid)
            if ok:
                t = self.text(key, phase=phase, text=text)
                self._emit(t or "", screen=dest == "screen", file=dest == "file")
            elif ok is None:
                what = "warnings" if note.severity == WARNING else "notices"
                where = " during analysis `%s'" % self.analysis if self.analysis else ""
                limit = self._limits[dest][0 if note.severity == WARNING else 1]
                t = self.text("notice_block", phase=phase,
                              text="further %s of this kind%s are suppressed (-max%s %d)"
                              % (what, where, "warns" if note.severity == WARNING else "notes", limit))
                self._emit(t or "", screen=dest == "screen", file=dest == "file")

    def limits(self, settings: Settings) -> None:
        """The merged maxwarns/maxnotes (and their -tolog variants) from step 6 on (§2.2)."""
        self._limits = {"screen": (settings.maxwarns, settings.maxnotes),
                        "file": (settings.maxwarnstolog, settings.maxnotestolog)}

    # -- the run's layout --------------------------------------------------------------------

    def run_start(self) -> None:
        t = self.text("run_start", netlist="stdin" if self.job.netlist is None else self.job.netlist,
                      host=percent.host_name(), time=spectre_time(self.start))
        if t is not None:
            self._emit(t)

    def inventory(self, counts: Mapping[str, int]) -> None:
        """The circuit inventory (`nodes 5`, then each master's count), in the given order."""
        lines = "\n".join("%19s %s" % (k, v) for k, v in counts.items())
        t = self.text("inventory", inventory_lines=lines)
        if t is not None:
            self._emit(t)

    @staticmethod
    def banner_parts(x) -> Tuple[str, str, str, str]:
        """(title, name, range, type) of an analysis step or result: an AnalysisResult's
        description (`Transient Analysis `mytran': time = (0 s -> 500 ms)`), an AnalysisStep
        (kind, name, its own SweepLevel or a tran's stop), or a (title, name, range) tuple."""
        if isinstance(x, (tuple, list)):
            title, name, rng = (list(x) + ["", "", ""])[:3]
            kind = [k for k, t in ANALYSIS_TITLES.items() if t == title]
            return title, name, rng, kind[0] if kind else title.lower()
        desc = getattr(x, "description", None)
        if isinstance(desc, str) and desc:
            m = re.match(r"^(.*?) `(.*)'(?:: (.*))?$", desc)
            if m:
                return m.group(1), m.group(2), m.group(3) or "", getattr(x, "atype", "")
            return desc, getattr(x, "key", ""), "", getattr(x, "atype", "")
        kind = getattr(x, "kind", getattr(x, "atype", ""))
        name = getattr(x, "name", getattr(x, "key", ""))
        title = ANALYSIS_TITLES.get(kind, "%s Analysis" % kind)
        rng = ""
        own = getattr(x, "own", None)
        if own is not None and getattr(own, "spec", None) is not None:
            spec = own.spec
            start = getattr(getattr(spec, "start", None), "value", None)
            stop = getattr(getattr(spec, "stop", None), "value", None)
            if start is not None and stop is not None:
                rng = "%s = (%s -> %s)" % (own.desc or own.label, si(start, own.units), si(stop, own.units))
            elif spec.values:
                rng = "%s = (%d values)" % (own.desc or own.label, len(spec.values))
        elif kind == "tran":
            stop = (getattr(x, "args", {}) or {}).get("stop")
            if isinstance(stop, (int, float)):
                rng = "time = (0 s -> %s)" % si(float(stop), "s")
        return title, name, rng, kind

    def analysis_banner(self, res_or_step) -> None:
        """The three-line banner of an analysis; the analysis becomes the current one (%A, the
        per-analysis message limits)."""
        title, name, rng, _ = self.banner_parts(res_or_step)
        self.analysis = name
        middle = "%s `%s': %s" % (title, name, rng) if rng else "%s `%s'" % (title, name)
        t = self.text("analysis_banner", analysis=name, analysis_title=title, range=rng, rule="*" * len(middle))
        if t is not None:
            if not rng:                                  # an op has no range: `DC Analysis `myop'' (§8.3)
                t = re.sub(r"': *$", "'", t, flags=re.M)
            self._emit(t)

    def analysis_done(self, res_or_step, seconds: float) -> None:
        _, name, _, kind = self.banner_parts(res_or_step)
        t = self.text("analysis_done", analysis=name, analysis_type=kind, seconds=seconds)
        if t is not None:
            self._emit(t)
        self.analysis = ""

    def status_line(self, progress: str = "") -> None:
        """The SIGUSR1 status line, printed from the main loop (§2.7) [M UG p.235]: the running
        analysis and, where the engine reports it, its progress."""
        elapsed = time.time() - self.start.timestamp()
        if self.analysis:
            what = "analysis `%s' running" % self.analysis
        else:
            what = "no analysis running (setting up)"
        tail = (", " + progress) if progress else ""
        self._emit("%s status: %s%s, elapsed = %.0f s." % (self.prog, what, tail, elapsed))

    def audit(self) -> None:
        t = self.text("audit", time=spectre_time(datetime.now()), cpu=_cpu() - self._cpu0,
                      elapsed=time.time() - self.start.timestamp())
        if t is not None:
            self._emit(t)

    def finish(self, status: int) -> None:
        """The trailer (§8.7): after exit status 0 or 1 the audit and `<prog> completes with N
        errors, M warnings, and K notices`; after 2 or 3 `<prog> terminated prematurely due to
        fatal error.` [M UG pp.33, 270; I, §14 q.18].  Closes the file."""
        if status in (0, 1):
            self.audit()
            t = self.text("trailer_ok", **self.counts)
        else:
            t = self.text("trailer_fatal")
        self._emit(t or "")
        self.close()


def _cpu() -> float:
    t = os.times()
    return t.user + t.system + t.children_user + t.children_system
