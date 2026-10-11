"""Co-simulation runtime for AMS jobs: nvc + VACASK or Xyce (docs/VAMOS_AMS_DESIGN.md §6).

The compile step left a deck, a boundary file and the job's "ams" record in
the daidir.  A run gets its own directory (the engines write into their cwd),
nvc runs there with absolute --work/-L paths, every raw output line is
classified before the usual output filter sees it, and the rawfile is checked
(it must reach the end the run claims) before it is published as
<prefix>.raw in the simv cwd.

Only nvc's, the bridge's and the engines' own lines classify a run, and only
their chatter is filtered.  Testbench output always arrives as an nvc report
(backends/nvc.py REPORT_RE: "** Note: 100ns+0: <text>") and reaches the user
unchanged, whatever it says.  Tool lines are matched whole, in the formats nvc
src/cosim.c and src/cosim_bridge.cpp print (_END, _FAIL), and a failing run
names the line that decided it ("vamos: error: co-simulation failed: <line>").

API
---
run(be, compiled, rt, con, opts, stop_fs) -> (exit code, footer time or None)
EndState, ChatterFilter         the classifier and the chatter filter (see each)
abi_check(engine, nvc_libdir, ld_path) -> (notes, error or None)
    The pre-run ABI check: finds each library where nvc's dlopen does (the
    LD_LIBRARY_PATH vamos gives nvc, then the system library directories) and
    reads its ELF dynamic symbol table (no nm, nothing loaded): a library that is
    missing names the directories searched and the variable that sets them; one
    without the vamos symbol is to be rebuilt; one vamos cannot read is left to
    nvc, which checks the ABI value itself when it loads it (P1).
check_raw(path) -> (point count rewritten, last time or None)
    rawfile.fix_points plus rawfile.read(path).last_time() for the publish path,
    with the reader's rules, in one pass over a mapped file: O(1) memory however
    long the run (rawfile.scan, every plot's count, first and last time, and
    rawfile.fix_scanned; both moved there from here and imported back under
    their old names scan_raw and _fix_points, docs/VAMOS_SPECTRE_DESIGN.md §4.6).
tran_start(deck, engine) -> the .tran output start (TSTART) of an emitted deck
"""

from __future__ import annotations

import decimal
import glob
import math
import os
import re
import shutil
import struct
import tempfile
from typing import List, Optional, Tuple

from vamos.ams import engines, layout
from vamos.backends import nvc as nvcmod
from vamos.backends.nvc import (BackendError, NvcBackend, footer_time, fs_text, is_report,
                                remap_exit, signal_name)
from vamos.console import Console
from vamos.job import Job
# RawPlot, scan_raw and _fix_points moved to netlist/rawfile.py (RawPlot, scan, fix_scanned) and
# are re-exported here under their old names (docs/VAMOS_SPECTRE_DESIGN.md §4.6)
from vamos.netlist.rawfile import RawError, RawPlot  # noqa: F401
from vamos.netlist.rawfile import fix_scanned as _fix_points, scan as scan_raw  # noqa: F401

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# nvc's end lines (src/cosim.c prints exactly one when the engine ran; <t> is exact text)
_END = (
    ("stop0", re.compile(r"^\*\* Note: co-simulation finished: digital stop at 0 s "
                         r"\(before the first analog step\)$")),
    ("stop", re.compile(r"^\*\* Note: co-simulation finished: digital stop at (\S+) s$")),
    ("analog", re.compile(r"^\*\* Note: co-simulation finished: analog end at (\S+) s$")),
    ("failed", re.compile(r"^\*\* Error: (?:VACASK|Xyce) transient failed at (\S+) s$")),
    ("stalled", re.compile(r"^\*\* Error: co-simulation stalled at (\S+) s$")),
    ("interrupted", re.compile(r"^\*\* Error: co-simulation interrupted at (\S+) s$")),
)
_STARTED = re.compile(r"^\*\* Note: starting co-simulation \(stop_time=")
# Tool lines that make a run fail.  nvc prints its own errors and fatals with no time
# stamp (a report's always has one), so every such line counts: the boundary-file,
# registration, library, ABI and engine-initialisation errors of src/cosim.c among them.
_FAIL = [re.compile(p) for p in (
    r"^\*\* (?:Error|Fatal): ",
    r"^\*\* Warning: +(?:D2A|A2D) .* <-> .* \[FAILED: signal not found\]$",
    r"^\*\* Warning: cannot load ",
    r"^\*\* Warning: (?:cosim bridge missing symbols|missing (?:VACASK|Xyce) symbol )",
    r"^\[cosim_bridge\] signal '.*' not registered",
    r"^\[cosim_bridge\] code: source '.*' is not bound ",
    r"^\[cosim_bridge\] (?:missing URI args|bad URI args '.*')$",
    r"^\[cosim_bridge\] VACASK external-source ABI mismatch$",
    r"^\[cosim_bridge\] '.*': bad ramp ",
    r"^\*\*\* Caught signal \d+",                    # nvc crashed
)]

# Chatter on any engine: nvc's co-simulation notes and the bridge's binding lines
_COSIM_CHATTER = [re.compile(p) for p in (
    r"^\[cosim_bridge\] bound ", r"^\*\* Note: (?:initializing|loaded|resolved|starting) ")]
# Xyce's banner, device counts, solver statistics and timing table, and its own finish
# line (VACASK prints none of these)
_XYCE_CHATTER = [re.compile(p) for p in (
    r"^\s*\*{5}", r"^\t", r"^\s+[A-Z][A-Za-z ]* level \d+ \(", r"^\s+-{8,}\s*$",
    r"^\s+Total Devices\s+\d+\s*$", r"^Timing summary of \d+ processor",
    r"^\s+Stats\s+Count\s+CPU Time\s+Wall Time", r"^Co-simulation finish at ",
    r"^\s*\S.*\s\d+\s+[0-9.]+ \(\s*[<0-9.]+%\)\s+[0-9.]+ \(\s*[<0-9.]+%\)\s*$")]

# Xyce's topology warning (N_TOP_Topology.C) for a node with one device terminal.  It
# reports every D2A enable node <n>_e (only the ve_ bridge source touches it; the gated
# element reads it in an expression), and that one is vamos's own: it is dropped, any
# other node's (a user's floating node) is printed.  Xyce word-wraps its messages
# (N_UTL_Misc.C word_wrap: 78 columns, continuation lines start with one space), so a
# message is reassembled from its lines first: the words below, the node in parentheses.
_ONE_TERM = ("Netlist", "warning:", "Voltage", "Node", None, "connected", "to", "only", "1",
             "device", "Terminal")
_ONE_TERM_START = "Netlist warning: Voltage Node"      # a long node name wraps after "Node"


class ChatterFilter:
    """The stdout sink of a co-simulation's OutputFilter: drops engine chatter, never a
    testbench line.

    see_raw(line) must be given every raw nvc line before the OutputFilter turns it into
    the line this filter is called with (cosim.run chains it into the stream's on_raw).
    A line that came from an nvc report - testbench output - is printed unchanged,
    whatever it says.  Every other line is tool output: blank ones are dropped (nvc and
    the bridge print none, Xyce many), and so are nvc's co-simulation notes, the bridge's
    binding lines, the deck path the C interfaces echo and, when engine is "xyce" (or
    None: unknown), Xyce's banner, device counts, statistics and timing table.

    quiet_nodes: the deck's D2A enable nodes (deck.enable_nodes), whose one-terminal
    warning is chatter.  Lines that may start that warning are held until it is
    complete; call flush() at the end of the stream."""

    def __init__(self, out, deck: str, quiet_nodes=(), engine: Optional[str] = None):
        self.out, self.deck, self.engine = out, deck, engine
        self.quiet = {n.upper() for n in quiet_nodes}
        self.held: List[str] = []
        self._report = False

    def see_raw(self, raw: str) -> None:
        self._report = is_report(raw)

    def __call__(self, line: str) -> None:
        if self._report:                                  # testbench output
            self._report = False
            if self.held:
                self.flush()
            self.out(line)
            return
        if self.held:
            if line[:1] == " " and line[1:2].strip():      # a word-wrap continuation
                self.held.append(line)
                self._settle()
                return
            self.flush()
        if line.startswith(_ONE_TERM_START):
            self.held = [line]
            self._settle()
            return
        self._emit(line)

    def flush(self) -> None:
        """Print held lines that did not complete a dropped warning, unchanged."""
        held, self.held = self.held, []
        for ln in held:
            self._emit(ln)

    def _settle(self) -> None:
        words = "".join(self.held).split()
        for k, w in enumerate(words):
            want = _ONE_TERM[k] if k < len(_ONE_TERM) else ""
            ok = (re.match(r"^\((\S+)\)$", w) is not None) if want is None else w == want
            if not ok:
                self.flush()                    # some other message: print it as it came
                return
        if len(words) < len(_ONE_TERM):
            return                              # wait for the next continuation line
        node = words[4][1:-1].upper()
        if node in self.quiet:
            self.held = []
        else:
            self.flush()

    def _emit(self, line: str) -> None:
        text = line.strip()
        if not text or text == self.deck or any(p.search(line) for p in _COSIM_CHATTER):
            return
        if self.engine in (None, "xyce") and any(p.search(line) for p in _XYCE_CHATTER):
            return
        self.out(line)


# Files the engines are known to write into the run directory.
_ENGINE_FILES = (re.compile(r".*\.raw$"), re.compile(r".*__behavioral\.va$"),
                 re.compile(r".*\.va\.origin$"), re.compile(r".*\.osdi$"))


class EndState:
    """How the co-simulation ended, from nvc's, the bridge's and the engines' lines.

    feed() every raw line; nvc reports (testbench output) are never looked at.
    kind: the end line's kind ("stop0", "stop", "analog", "failed", "stalled",
    "interrupted"), None without one; time_text: its time as printed (exact);
    failures: the lines that make the run fail (a "failed"/"stalled" end line too);
    started: nvc started the analog transient."""

    def __init__(self) -> None:
        self.kind: Optional[str] = None
        self.line: Optional[str] = None
        self.time_text: Optional[str] = None
        self.started = False
        self.failures: List[str] = []

    def feed(self, raw: str) -> None:
        line = _ANSI.sub("", raw).rstrip()
        if not line or is_report(line):
            return
        if _STARTED.match(line):
            self.started = True
            return
        for kind, rx in _END:
            m = rx.match(line)
            if m:
                if self.kind is None:
                    self.kind, self.line = kind, line
                    self.time_text = m.group(1) if rx.groups else "0"
                    if kind in ("failed", "stalled"):
                        self.failures.append(line)
                return
        if any(p.match(line) for p in _FAIL):
            self.failures.append(line)

    @property
    def ended(self) -> bool:
        return self.kind is not None

    @property
    def stop_at_zero(self) -> bool:
        return self.kind == "stop0"

    @property
    def interrupted(self) -> bool:
        return self.kind == "interrupted"

    @property
    def time(self) -> Optional[float]:
        try:
            return float(self.time_text) if self.time_text is not None else None
        except ValueError:
            return None

    @property
    def time_fs(self) -> Optional[int]:
        """The end time in femtoseconds, exactly as printed; None if not a finite time."""
        try:
            fs = decimal.Decimal(self.time_text) * 10 ** 15 if self.time_text else None
            return int(fs.to_integral_value()) if fs is not None and fs.is_finite() else None
        except (decimal.InvalidOperation, ValueError):
            return None

    @property
    def digital_stop(self) -> Optional[float]:
        return self.time if self.kind in ("stop0", "stop") else None

    @property
    def analog_end(self) -> Optional[float]:
        return self.time if self.kind == "analog" else None


# -- the ABI pre-check ------------------------------------------------------------------

_SYSTEM_LIB_DIRS = ("/usr/local/lib", "/usr/lib", "/lib", "/usr/lib64", "/lib64")


def _search_dirs(ld_path: str) -> List[str]:
    """Where nvc's dlopen of a bare library name looks: the LD_LIBRARY_PATH vamos gives
    it, then the system directories (nvc also tries /usr/local/lib and /usr/lib)."""
    dirs = [d for d in ld_path.split(os.pathsep) if d] + list(_SYSTEM_LIB_DIRS)
    dirs += sorted(glob.glob("/usr/lib/*-linux-gnu")) + sorted(glob.glob("/lib/*-linux-gnu"))
    out: List[str] = []
    for d in dirs:
        if d not in out:
            out.append(d)
    return out


def _elf_defines(path: str, sym: str) -> Optional[bool]:
    """Whether the ELF shared library at path defines the dynamic symbol sym (its
    .dynsym section, read with struct: nothing is loaded).  None when the file is not
    an ELF file with section headers that this can read."""
    with open(path, "rb") as fh:
        ident = fh.read(16)
        if len(ident) < 16 or ident[:4] != b"\x7fELF" or ident[5] not in (1, 2):
            return None
        end = "<" if ident[5] == 1 else ">"
        if ident[4] == 2:                                   # ELF64
            ehdr, shdr, symf = end + "HHIQQQIHHHHHH", end + "IIQQQQIIQQ", end + "IBBHQQ"
        elif ident[4] == 1:                                 # ELF32
            ehdr, shdr, symf = end + "HHIIIIIHHHHHH", end + "IIIIIIIIII", end + "IIIBBH"
        else:
            return None
        raw = fh.read(struct.calcsize(ehdr))
        if len(raw) < struct.calcsize(ehdr):
            return None
        e = struct.unpack(ehdr, raw)
        shoff, shentsize, shnum = e[5], e[10], e[11]
        if not shoff or shentsize < struct.calcsize(shdr) or not shnum:
            return None
        sections = []
        for k in range(shnum):
            fh.seek(shoff + k * shentsize)
            raw = fh.read(struct.calcsize(shdr))
            if len(raw) < struct.calcsize(shdr):
                return None
            s = struct.unpack(shdr, raw)
            # (type, offset, size, link, entsize) in both layouts
            sections.append((s[1], s[4], s[5], s[6], s[9]))
        dynsym = [s for s in sections if s[0] == 11]       # SHT_DYNSYM
        if not dynsym:
            return None
        stype, off, size, link, entsize = dynsym[0]
        if link >= len(sections) or entsize < struct.calcsize(symf):
            return None
        fh.seek(off)
        table = fh.read(size)
        fh.seek(sections[link][1])
        strtab = fh.read(sections[link][2])
        want = sym.encode()
        for k in range(len(table) // entsize):
            ent = struct.unpack_from(symf, table, k * entsize)
            name_off = ent[0]
            shndx = ent[3] if ident[4] == 2 else ent[5]
            stop = strtab.find(b"\0", name_off)
            if strtab[name_off:stop if stop >= 0 else len(strtab)] == want and shndx != 0:
                return True
        return False


def abi_check(engine: str, nvc_libdir: str, ld_path: str) -> Tuple[List[str], Optional[str]]:
    """The co-simulation ABI pre-check (module docstring).  Returns (notes, error): print
    the notes; an error means the run cannot work and must not start."""
    wants = [("libcosim_bridge.so", "cosim_bridge_abi",
              "it is built with nvc (lib/libcosim_bridge.so beside nvc's library directory "
              "%s); set VAMOS_NVC to an nvc built with it" % nvc_libdir,
              "rebuild it from nvc src/cosim_bridge.cpp, which nvc's own build does not make: "
              "c++ -O2 -shared -fPIC -o <nvc prefix>/lib/libcosim_bridge.so src/cosim_bridge.cpp "
              "(docs/VAMOS_GUIDE.md, Getting the stack)")]
    if engine == "vacask":
        wants.append(("libvacaskcinterface.so", "vacask_cosim_abi",
                      "set VAMOS_VACASK_HOME to the VACASK build (its cinterface/ directory "
                      "holds the library)",
                      "rebuild VACASK with the vamos co-simulation patches"))
    else:
        wants.append(("libxycecinterface.so", "xyce_cosim_abi",
                      "set VAMOS_XYCE_LIBS to the directories holding libxycecinterface.so "
                      "and libxyce.so",
                      "rebuild Xyce with the vamos co-simulation patches"))
    dirs = _search_dirs(ld_path)
    notes: List[str] = []
    for name, sym, where, rebuild in wants:
        path = next((os.path.join(d, name) for d in dirs
                     if os.path.isfile(os.path.join(d, name))), None)
        if path is None:
            return notes, "%s not found (searched %s): %s" % (name, ", ".join(dirs), where)
        try:
            ok = _elf_defines(path, sym)
        except OSError:
            ok = None
        if ok is None:
            notes.append("co-simulation ABI pre-check skipped for %s (not an ELF library "
                         "vamos can read); nvc checks the ABI when it loads it" % path)
        elif not ok:
            return notes, ("%s does not export %s() (it predates the vamos co-simulation "
                           "ABI): %s" % (path, sym, rebuild))
    return notes, None


# -- the rawfile check (O(1) memory) -----------------------------------------------------
#
# The scan (every plot's count, first and last time, where the count field is) and the
# repair from a scan live in netlist/rawfile.py (scan, fix_scanned; moved from here,
# docs/VAMOS_SPECTRE_DESIGN.md §4.6) and are imported above as scan_raw and _fix_points.

def check_raw(path: str) -> Tuple[bool, Optional[float]]:
    """(the point count was rewritten, the first plot's last time or None for no points).
    RawError for a file that is not a rawfile, or whose first plot has no time scale."""
    plots = scan_raw(path)
    if not plots[0].time_scale:
        raise RawError("%s: the first plot has no time scale" % path)
    return _fix_points(path, plots), plots[0].last


# -- the deck's output start (.tran TSTART) ------------------------------------------------

_VACASK_TRAN = re.compile(r"^\s*analysis\s+\S+\s+tran\b(.*)$")
_XYCE_TRAN = re.compile(r"^\s*\.tran\s+(.*)$", re.I)


def tran_start(deck: str, engine: str) -> float:
    """The .tran output start (TSTART) of an emitted deck, 0 when it has none or the deck
    cannot be read.  The emitters print plain numbers: VACASK "analysis <name> tran ...
    start=<t>", Xyce ".tran <step> <stop> [<start> [<maxstep>]] [UIC]"."""
    try:
        with open(deck, errors="replace") as fh:
            for line in fh:
                if engine == "vacask":
                    m = _VACASK_TRAN.match(line)
                    if m:
                        for tok in m.group(1).split():
                            key, eq, val = tok.partition("=")
                            if eq and key.lower() == "start":
                                return max(0.0, float(val))
                        return 0.0
                else:
                    m = _XYCE_TRAN.match(line)
                    if m:
                        vals = [v for v in m.group(1).split() if v.upper() not in ("UIC", "NOOP")]
                        return max(0.0, float(vals[2])) if len(vals) > 2 else 0.0
    except (OSError, ValueError):
        pass
    return 0.0


# -- the run --------------------------------------------------------------------------------

def _publish_name(rt: Job, prefix: str) -> str:
    p = prefix if os.path.isabs(prefix) else os.path.join(rt.cwd, prefix)
    return p + ".raw"


def _remove_published(path: str) -> None:
    """Remove an older run's <prefix>.raw, so a failed run never leaves it behind.  One that
    is already gone is fine: concurrent runs sharing the name remove it in any order."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _cleanup(run_dir: str, keep: bool, con: Console, failed: bool) -> None:
    if keep or failed:
        con.err("vamos: note: run directory kept: %s" % run_dir)
        return
    leftovers = []
    for name in os.listdir(run_dir):
        path = os.path.join(run_dir, name)
        if os.path.isfile(path) and any(p.match(name) for p in _ENGINE_FILES):
            os.remove(path)
        else:
            leftovers.append(name)
    if leftovers:
        con.err("vamos: note: run directory kept (testbench output): %s" % run_dir)
    else:
        shutil.rmtree(run_dir, ignore_errors=True)


def _footer(state: EndState, filt, raw: str) -> Optional[str]:
    """The footer's time: the end line's (exact); without one, once the co-simulation
    started (nvc killed, a crash), the later of the last report time and the last point
    of the partial rawfile; None when it never started (nothing was simulated)."""
    fs = state.time_fs
    if fs is not None:
        return fs_text(fs)
    if not (state.ended or state.started):
        return None
    text = footer_time(filt.last_time)
    try:
        last = scan_raw(raw)[0].last if os.path.isfile(raw) else None
    except (RawError, OSError, ValueError):
        last = None
    if last is not None and last > 0:
        m = re.match(r"^(\d+)(ms|us|ns|ps|fs)?$", text)
        reported = int(m.group(1)) * {"ms": 10 ** 12, "us": 10 ** 9, "ns": 10 ** 6, "ps": 10 ** 3,
                                      "fs": 1}[m.group(2) or "fs"] if m else 0
        analog = int(round(last * 1e15))
        if analog > reported:
            return fs_text(analog)
    return text


def run(be: NvcBackend, compiled: Job, rt: Job, con: Console, opts: dict,
        stop_fs: Optional[int]) -> Tuple[int, Optional[str]]:
    """Run an AMS job.  Returns (exit code, the footer's final simulation time, or None
    when nothing was simulated)."""
    rec = compiled.ams or {}
    daidir = compiled.daidir
    engine = rec.get("engine", "vacask")
    deck = layout.resolve(daidir, rec["deck"])
    boundary = layout.resolve(daidir, rec["boundary"])
    stop = float(rec["stop"])
    deck_fs = int(math.ceil(stop * 1e15)) + 1
    run_fs = min(deck_fs, stop_fs) if stop_fs is not None else deck_fs
    if rec.get("stop_synthesized"):
        con.out("vamos: note: no .tran: the run ends at $finish/$stop or at %g s "
                "(+vcs+finish+N bounds it)" % stop)

    eng_env = engines.env_for(engine, be.libdir)
    notes, problem = abi_check(engine, be.libdir, eng_env.get("LD_LIBRARY_PATH", ""))
    for n in notes:
        con.err("vamos: note: " + n)
    if problem:
        con.err("vamos: error: " + problem)
        return 1, None

    prefix = rec.get("out_prefix") or layout.DEFAULT_PREFIX
    published = _publish_name(rt, prefix)
    run_parent = os.path.dirname(published) or rt.cwd
    try:
        os.makedirs(run_parent, exist_ok=True)
        _remove_published(published)
        run_dir = tempfile.mkdtemp(prefix=os.path.basename(prefix) + ".run.", dir=run_parent)
    except OSError as e:
        con.err("vamos: error: cannot set up the run in %s: %s" % (run_parent, e))
        return 1, None

    netlist_opt = "--vacask-netlist=" if engine == "vacask" else "--xyce-netlist="
    run_args = ["--stop-time=%dfs" % run_fs, netlist_opt + deck, "--cosim-config=" + boundary]
    cmd, env = be.run_command(compiled.tops[0], rt.plusargs, run_args)
    env.update(eng_env)

    state = EndState()
    from vamos.ams.deck import enable_nodes
    try:
        quiet = enable_nodes(deck)
    except OSError:
        quiet = []                          # nvc reports an unreadable deck itself
    out = ChatterFilter(con.out, deck, quiet_nodes=quiet, engine=engine)

    def on_raw(line: str) -> None:
        state.feed(line)
        out.see_raw(line)

    try:
        rc, filt = be.stream(cmd, env, run_dir, out, con.err, on_raw=on_raw)
    except BackendError as e:
        con.err("vamos: error: %s" % e)
        _cleanup(run_dir, bool(opts.get("keep")), con, True)
        return 1, None
    out.flush()
    raw = os.path.join(run_dir, rec.get("raw", layout.RAW))
    simtime = _footer(state, filt, raw)

    if be.interrupted is not None or state.interrupted:
        return _interrupted(be, state, rc, raw, run_dir, con), simtime
    if rc < 0:
        con.err("vamos: error: nvc was killed by signal %d (%s)" % (-rc, signal_name(-rc)))
        _cleanup(run_dir, bool(opts.get("keep")), con, True)
        return 128 - rc, simtime
    if state.failures:
        more = len(state.failures) - 1
        con.err("vamos: error: co-simulation failed: %s%s" % (
            state.failures[0], " (and %d more failure line%s above)" % (
                more, "s" if more > 1 else "") if more else ""))
    elif not state.ended:
        con.err("vamos: error: the co-simulation ended without an end line (nvc exit status %d)"
                % rc)
    if state.failures or not state.ended or remap_exit(rc, filt) != 0:
        # (an end line with nothing else: the digital side failed - $fatal, a runtime error)
        _cleanup(run_dir, bool(opts.get("keep")), con, True)
        return rc or 1, simtime

    ok = _publish(rec, deck, engine, stop, run_fs, state, raw, published, con)
    _cleanup(run_dir, bool(opts.get("keep")), con, not ok)
    return (0 if ok else 1), simtime


def _publish(rec: dict, deck: str, engine: str, stop: float, run_fs: int, state: EndState,
             raw: str, published: str, con: Console) -> bool:
    """Check the rawfile of a clean run and publish it.  False if the run fails after all."""
    want = min(stop, run_fs / 1e15,
               state.digital_stop if state.digital_stop is not None else float("inf"))
    start = float(rec["start"]) if rec.get("start") is not None else tran_start(deck, engine)
    # The engines write no point before the .tran start (TSTART), and none at all for a
    # stop at t=0 on Xyce: no output is then not a failure.
    quiet_end = state.stop_at_zero or want <= start * (1 + 1e-9) + 2e-15
    last = None
    if os.path.isfile(raw):
        try:
            changed, last = check_raw(raw)
        except (RawError, OSError) as e:
            con.err("vamos: error: cannot read the analog rawfile: %s" % e)
            return False
        if changed:
            con.err("vamos: note: rawfile header point count rewritten from its data")
        if last is not None:
            # the engine's last point may sit up to 1.5 fs (stopped_step's window, P1) below
            # the digital's femtosecond stop time: allow 2 fs or 1e-9 relative
            if last < want - max(want * 1e-9, 2e-15):
                con.err("vamos: error: analog output ends early at %.15g s (expected %.15g s)"
                        % (last, want))
                return False
            try:
                os.replace(raw, published)
            except OSError as e:
                con.err("vamos: error: cannot publish the rawfile as %s: %s" % (published, e))
                return False
            return True
    if quiet_end:
        if state.stop_at_zero:
            con.err("vamos: note: no analog output: the digital stopped at t=0")
        elif start > 0:
            con.err("vamos: note: no analog output: the run ended at %.15g s, before the "
                    "deck's output start %.15g s (.tran TSTART)" % (want, start))
        else:
            con.err("vamos: note: no analog output: the run ended at t=0")
        return True
    if last is None and os.path.isfile(raw):
        con.err("vamos: error: the analog rawfile has no points (expected output through "
                "%.15g s)" % want)
    else:
        con.err("vamos: error: the analog engine wrote no rawfile")
    return False


def _interrupted(be: NvcBackend, state: EndState, rc: int, raw: str, run_dir: str,
                 con: Console) -> int:
    """An interrupted run (vamos got a signal, or nvc a SIGINT): never classified as a
    stop, nothing published, the run directory and its partial waves kept."""
    partial = os.path.isfile(raw)
    if partial:
        try:
            check_raw(raw)                  # a readable header for the partial waves
        except (RawError, OSError):
            pass
    why = (signal_name(be.interrupted) if be.interrupted is not None else "nvc got SIGINT")
    if be.killed:
        why += "; nvc did not stop within %g s and was killed" % nvcmod.INTERRUPT_GRACE
    at = " at %s s" % state.time_text if state.interrupted else ""
    con.err("vamos: note: co-simulation interrupted (%s)%s; run directory kept%s: %s"
            % (why, at, " (partial waves)" if partial else "", run_dir))
    if be.interrupted is not None:
        return 128 + be.interrupted
    return rc if rc > 0 else 130


def sim_time_text(t: float, precision: Optional[str] = None) -> str:
    """A time in seconds as the footer prints it (nvc's style, backends/nvc.fs_text)."""
    return fs_text(int(round(t * 1e15)))
