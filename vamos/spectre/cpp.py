"""The C preprocessor of a spectre run (docs/VAMOS_SPECTRE_DESIGN.md §2.6; the API is the §10
block).

cpp runs with -E, or with -D/-U/-I unless -disableCPP is given (args.cpp_runs).  It is the
system cpp, found through tools.find_real("cpp") (which honours VAMOS_CPP), called as
`cpp -traditional-cpp -nostdinc -undef [-D...] [-U...] [-I...] <absolute input> <run>/cpp.out`
with cwd = the job's cwd, stdin /dev/null and tools.child_env() [I, §14 q.4; E82].  Its stderr
is captured: each line becomes a vamos note (a warning when it names a file:line) and is never
printed raw.  A missing cpp and a cpp failure are errors (NoteError).  The title is line 1 of the
original top-level file, read before cpp, so it does not depend on the cpp mode [E47, E82].  The
markers cpp writes (`# <line> "<file>" [flags]`) are read by netlist/spectre.py
(SpectreParseOpts.cpp_markers); a `#` directive in a file cpp did not process is an error
(directive_errors), and a macro name inside a SPICE-mode quoted expression, which neither cpp
mode expands [E82], gets a warning (macro_warnings).
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import List, Sequence, Tuple

from vamos import tools
from vamos.notes import Note, NoteError, error, note, warning
from vamos.spectre.job import SpectreJob

CPP_FLAGS = ("-traditional-cpp", "-nostdinc", "-undef")
OUTPUT_NAME = "cpp.out"

_FILE_LINE = re.compile(r"^\S+?:\d+(?::\d+)?:")
_DIRECTIVE = re.compile(r"^\s*#\s*(include|define|undef|if|ifdef|ifndef|elif|else|endif)\b")
_DEFINE = re.compile(r"^\s*#\s*define\s+(\w+)")
_QUOTED = re.compile(r"'([^']*)'")
_WORD = re.compile(r"[A-Za-z_]\w*")


def input_path(path: str, job: SpectreJob) -> str:
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(job.cwd, path))


def command(path: str, job: SpectreJob, run_dir: str, cpp: str = "cpp") -> List[str]:
    """The cpp command line (§2.6), for the run and for the --vamos-verbose echo."""
    cmd = [cpp] + list(CPP_FLAGS)
    cmd += ["-D" + d for d in job.defines]
    cmd += ["-U" + u for u in job.undefines]
    cmd += ["-I" + i for i in job.incdirs]
    cmd += [input_path(path, job), os.path.join(run_dir, OUTPUT_NAME)]
    return cmd


def title_of(path: str, job: SpectreJob) -> str:
    """Line 1 of the original top-level file, without its newline (§2.6)."""
    with open(input_path(path, job), encoding="utf-8", errors="replace") as fh:
        return fh.readline().rstrip("\r\n")


def stderr_notes(text: str) -> List[Note]:
    """cpp's stderr, one note per line: a warning when the line names a file:line, else a
    note.  Nothing is printed raw (§2.6)."""
    out: List[Note] = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            continue
        m = _FILE_LINE.match(line)
        if m:
            origin = m.group(0)[:-1]
            out.append(warning(origin, "cpp: " + line[m.end():].strip()))
        else:
            out.append(note("", "cpp: " + line))
    return out


def preprocess(path: str, job: SpectreJob, run_dir: str) -> Tuple[str, str, List[Note]]:
    """Run cpp on the input (§2.6) -> (the cpp.out path, the title line, cpp's stderr as notes).
    tools.ToolError for a bad VAMOS_CPP (as tools.find_real raises it); NoteError, with every
    note, when there is no cpp or cpp fails."""
    cpp = tools.find_real("cpp")
    if cpp is None:
        raise NoteError([error("", "cpp not found (set VAMOS_CPP, or put cpp on PATH): -E, -D, -U "
                                   "and -I run the C preprocessor")])
    title = title_of(path, job)
    cmd = command(path, job, run_dir, cpp)
    try:
        r = subprocess.run(cmd, cwd=job.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, env=tools.child_env())
    except OSError as e:
        raise NoteError([error("", "cannot run %s: %s" % (cpp, e))])
    notes = stderr_notes(r.stderr or "")
    if r.returncode != 0:
        raise NoteError(notes + [error("", "cpp failed (exit %d) on %s" % (r.returncode, path))])
    return cmd[-1], title, notes


def directive_errors(text: str, origin: str) -> List[Note]:
    """A line starting with `#include`, `#define`, `#if...` when cpp does not run: the error
    "CPP directive without -E" at its file:line (§2.6) [M ref19 p.493; UG p.52]."""
    out: List[Note] = []
    for i, line in enumerate(text.splitlines(), 1):
        m = _DIRECTIVE.match(line)
        if m:
            out.append(error("%s:%d" % (origin, i),
                             "CPP directive without -E: #%s is not processed (give -E, or -D/-U/-I "
                             "without -disableCPP)" % m.group(1)))
    return out


def macro_names(text: str, defines: Sequence[str]) -> List[str]:
    """The macro names in force: -D names (before any `=`) and the file's own `#define`s."""
    names = [d.split("=", 1)[0] for d in defines if d]
    for line in text.splitlines():
        m = _DEFINE.match(line)
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


def macro_warnings(text: str, origin: str, defines: Sequence[str]) -> List[Note]:
    """A defined macro name inside a SPICE-mode `'...'` expression: cpp expands it in neither
    mode [E82], so it gets a warning naming the macro at its file:line (§2.6)."""
    names = set(macro_names(text, defines))
    out: List[Note] = []
    if not names:
        return out
    for i, line in enumerate(text.splitlines(), 1):
        for q in _QUOTED.findall(line):
            used = [w for w in _WORD.findall(q) if w in names]
            for w in used:
                out.append(warning("%s:%d" % (origin, i),
                                   "macro %s inside the quoted expression '%s' is not expanded by cpp"
                                   % (w, q)))
    return out
