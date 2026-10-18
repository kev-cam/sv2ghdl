"""X, phase 2: the Xyce runner, vamos/spectre/run_xyce.py (docs/VAMOS_SPECTRE_DESIGN.md §7.2, §1 step 11,
§2.7, §10; the stopping contract is vamos/spectre/stop.py).

    cd tests/vamos && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_spectre_run_xyce

Both legs, no engine:
  TestReplay          the state replay (e2e 6: the PULSE in the tran deck only; a card and a group
                      parameter; the renames reversed; an r/c/l value; temp; a variable and its
                      dependents in the deck's .param); phase 2c2: a gain, gm, rm, coupling and area
                      into Instance.value, an alter of isnoisy switching the form per deck
  TestRenderDecks     render_decks reproduces the chain's render goldens of e2e 1-10; xf refused
  TestPlaceVa         the Verilog-A copies: base names, the relative includes beside them, a base-name
                      clash or a climbing include under vamos_va<k>/; phase 2c2 (F4): no directory but
                      run_dir holds two modules' files
  TestCheckOutput     §7.2's print-file rule on hand-written rawfiles and .PRINT tables
  TestHelpers         ended_by, diagnostics, trim_partial_line, planned, Signals
  TestRunFake         run() against a fake Xyce (a script named by VAMOS_XYCE that writes what
                      fake.json in the run directory says): the order of the runs, the -norun check,
                      a problem named by the alter or sweep that introduced it, a failed analysis
                      among good ones, a failed .STEP point, a crash, a kill from outside, the
                      grace-period kill (its 0.5 s grace measured, the kill named), the verbose
                      echo, a Xyce that cannot start, OpenBLAS's threads bounded unless the user
                      set a thread count, the tolerance notices of the options the netlist set
  TestVerilogAChecks  phase 2c, R-run-3: a module PyMS cannot build ($abstime, a timeout, any
                      exception of its build) and a missing `include (also one only CDS_VLOGA_INCLUDE
                      finds) are status 2, never a run; the headers PyMS finds or does without pass
  TestRunFakeSignals  Linux: the stop signals reach the fake's child, as they must PyMS's compile
                      (R-run-5); a second signal raises stop.Stop before the deck is classified
                      (R-run-8); SIGUSR1 during the -norun check (R-run-11); SIGUSR2's one notice,
                      stop.CHECKPOINT (R-run-14)
  TestProgress        the status-line text while the -norun check runs (R-run-11)
WSL (needs_xyce; needs_pyms for the Verilog-A decks):
  TestRunE2E          e2e 1-10 through spectre.parse -> plan.build -> signals.resolve -> run(), against
                      the xyce parts of their engine_expect.json (05_xf and 10's deck_cross are
                      refused at plan.build)
  TestRunXyceFailures a singular .OP (exit 1: that step failed, status 1); a tran failing part-way
                      among good analyses [E66]; a .STEP point failing part-way (Xyce goes on and
                      exits 0: failed_points); an alter and a sweep of a parameter Xyce does not
                      know (status 2 naming them); R-run-3: $abstime, a missing include on a cold
                      and a warm PyMS cache (status 2), Spectre's headers (the module runs); phase
                      2c2 (F4): two modules of one library sharing ../inc keep their parameters
  TestRunXyceSignals  SIGTERM and SIGINT on a long tran (status 3, the partial rawfile readable, the
                      later deck not run), a second signal (stop.Stop), SIGUSR1 (the status line, the
                      run goes on) and SIGUSR2 (the notice), the grace-period kill of the real Xyce
                      (stopped first with SIGSTOP; R-tests-13)
"""

import glob
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import unittest
import uuid
from datetime import datetime
from typing import Dict, List, Optional
from unittest import mock

from vamos_testlib import LINUX, TempDir, procs_under, reap  # noqa: F401
from spectre_testlib import SPECTRE_FIXTURES, have_xyce, needs_pyms, needs_xyce  # noqa: E402

from vamos.banner import Banner  # noqa: E402
from vamos.netlist import ir, plan as PL, rawfile, signals as SG, spectre, xyce  # noqa: E402
from vamos.netlist.expr_ast import Num  # noqa: E402
from vamos.notes import ERROR, NoteError  # noqa: E402
from vamos.spectre import run_xyce as RX, stop  # noqa: E402
from vamos.spectre.job import Settings, SpectreJob  # noqa: E402
from vamos.spectre.log import SpectreLog  # noqa: E402
from vamos.spectre.results import EngineResult  # noqa: E402

E2E = os.path.join(SPECTRE_FIXTURES, "e2e")
CASES = sorted(d for d in os.listdir(E2E) if os.path.isdir(os.path.join(E2E, d)) and d[:2].isdigit()
               and 1 <= int(d[:2]) <= 10)


# -- the chain up to the runner ----------------------------------------------------------------------

class Chain:
    """spectre.parse -> plan.build -> signals.resolve for Xyce: the runner's inputs."""

    def __init__(self, deck: str, engine: str = "xyce") -> None:
        deck = os.path.abspath(deck)
        self.nl = spectre.parse(deck, os.path.dirname(deck), spectre.SpectreParseOpts())
        self.plan = PL.build(self.nl, engine, Settings())
        self.nl2, self.sigmap, self.notes = SG.resolve(self.nl, self.plan)

    @property
    def steps(self) -> List[PL.AnalysisStep]:
        return [a.step for a in self.plan.actions if a.op == "analysis" and a.step is not None]

    def column(self, names: Dict, step_id: str, spectre_name: str) -> str:
        for ref, sname, _ptype in self.sigmap.per_step[step_id]:
            if sname == spectre_name:
                return names[step_id][ref]
        raise KeyError("no signal %r in step %s" % (spectre_name, step_id))


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_deck(d: str, name: str, text: str) -> str:
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with open(path, "w") as fh:
        fh.write(text)
    return path


class LogFile:
    """A SpectreLog writing to a file only (=log), and its text; profile: the shipped banner
    profile (the analysis banners and times print), else none (they do not)."""

    def __init__(self, d: str, profile: bool = False) -> None:
        from vamos import banner
        self.path = os.path.join(d, "spectre.log")
        self.job = SpectreJob("spectre", ["deck.scs"], d, log_mode="file", log_path=self.path)
        prof = banner.load_profile("spectre", "spectre") if profile else None
        self.log = SpectreLog(self.job, Banner(prof, "spectre"), datetime.now())

    def text(self) -> str:
        self.log.close()
        with open(self.path, encoding="utf-8") as fh:
            return fh.read()


def run_chain(tc: TempDir, ch: Chain, name: str = "run", **kw) -> "tuple":
    """run_xyce.run on a chain in <tmp>/<name>; (EngineResult, the log text, the run directory).
    profile=True prints the analysis banners; the other keywords go to run()."""
    run_dir = os.path.join(tc.tmp, name)
    os.makedirs(run_dir, exist_ok=True)
    lf = LogFile(tc.tmp, kw.pop("profile", False))
    er = RX.run(ch.nl2, ch.plan, ch.sigmap, run_dir, lf.job, lf.log, **kw)
    return er, lf.text(), run_dir


# -- rawfiles written by hand (both legs) --------------------------------------------------------------

def raw_text(names: List[str], plots: List[List[tuple]], blank=(), cplx: bool = False,
             declared: Optional[Dict[int, int]] = None) -> str:
    """An ASCII rawfile: one plot per list of rows; `blank` plot indices get an empty No. Points,
    `declared` overrides a plot's count."""
    out = []
    for k, rows in enumerate(plots):
        n = "" if k in blank else str((declared or {}).get(k, len(rows)))
        out += ["Title: fake", "Date: today", "Plotname: fake plot %d" % k,
                "Flags: %s" % ("complex" if cplx else "real"), "No. Variables: %d" % len(names),
                "No. Points: %s" % n, "Variables:"]
        out += ["\t%d\t%s\t%s" % (i, nm, "time" if nm.lower() == "time" else "voltage")
                for i, nm in enumerate(names)]
        out.append("Values:")
        for i, row in enumerate(rows):
            vals = [("%r,0" % v) if cplx else repr(float(v)) for v in row]
            out.append("%d\t%s" % (i, vals[0]))
            out += ["\t%s" % v for v in vals[1:]]
    return "\n".join(out) + "\n"


def prn_text(names: List[str], tables: List[List[tuple]], end: bool = True) -> str:
    out = ["Index   " + "   ".join(names)]
    for rows in tables:
        for i, row in enumerate(rows):
            out.append("%d   %s" % (i, "   ".join("%.8e" % v for v in row)))
    if end:
        out.append("End of Xyce(TM) Parameter Sweep")
    return "\n".join(out) + "\n"


def print_columns(deck_text: str) -> List[str]:
    """The .PRINT items of a rendered deck, after FILE=."""
    for line in deck_text.splitlines():
        if line.startswith(".PRINT"):
            toks = line.split()
            k = next(i for i, t in enumerate(toks) if t.startswith("FILE="))
            return toks[k + 1:]
    return []


def good_output(step: PL.AnalysisStep, deck_text: str) -> str:
    """The text of a complete print file for a step, as Xyce writes its shape (§7.2)."""
    cols = print_columns(deck_text)
    total, _per, rows = RX.planned(step)
    if step.kind == "noise":
        n = rows or 1
        return prn_text(["FREQ"] + cols, [[(1.0 + i,) + (1e-18,) * len(cols) for i in range(n)]] * total)
    if step.kind == "tran":
        t_stop = float(step.args["stop"])
        pts = [tuple([t_stop * i / 4.0] + [0.5] * len(cols)) for i in range(5)]
        return raw_text(["TIME"] + cols, [pts] * total)
    if step.kind == "ac":
        pts = [tuple([float(i + 1)] + [0.5] * len(cols)) for i in range(rows or 1)]
        return raw_text(["FREQUENCY"] + cols, [pts] * total, cplx=True)
    pts = [tuple([float(i)] + [0.5] * len(cols)) for i in range(rows or 1)]
    return raw_text(["sweep"] + cols, [pts] * total)


# -- the fake Xyce (both legs) -------------------------------------------------------------------------

FAKE = '''#!%(python)s
"""A fake Xyce for test_spectre_run_xyce: what it prints, writes and returns comes from fake.json
in its working directory, keyed by the deck ("norun <deck>" for the -norun check).  Each run appends
its arguments to fake.calls and its thread variables to fake.env; "spawn" starts a child process (in
the fake's process group, as PyMS's compile is in Xyce's) whose command line holds the given marker."""
import json, os, signal, subprocess, sys, time
args = sys.argv[1:]
norun = "-norun" in args
deck = [a for a in args if a != "-norun"][-1]
with open("fake.calls", "a") as fh:
    fh.write(" ".join(args) + "\\n")
with open("fake.env", "a") as fh:
    fh.write(json.dumps({k: os.environ.get(k) for k in %(threads)r}) + "\\n")
try:
    with open("fake.json") as fh:
        spec = json.load(fh)
except (OSError, ValueError):
    spec = {}
b = spec.get(("norun " if norun else "") + deck, {})
if b.get("spawn"):
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)", b["spawn"]])
sys.stdout.write(b.get("out", ""))
sys.stdout.flush()
if "write" in b:
    with open(b["file"], "w") as fh:
        fh.write(b["write"])
if b.get("ignore_term"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if b.get("sleep"):
    time.sleep(b["sleep"])
if b.get("signal"):
    os.kill(os.getpid(), getattr(signal, b["signal"]))
    time.sleep(10)
if b.get("abort"):
    os.abort()
sys.exit(b.get("rc", 0))
'''


# the variables that bound the engines' OpenBLAS (and OpenMP) threads (R-run-4)
THREAD_VARS = ("OPENBLAS_NUM_THREADS", "GOTO_NUM_THREADS", "OMP_NUM_THREADS")


def marker_pids(marker: str) -> List[int]:
    """The processes whose command line holds `marker` (from /proc; [] where there is none)."""
    out = []
    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(p, "rb") as fh:
                if marker.encode() in fh.read():
                    out.append(int(p.split("/")[2]))
        except (OSError, ValueError):
            continue
    return [pid for pid in out if pid != os.getpid()]


def gone(marker: str, timeout: float = 5.0) -> bool:
    """Whether every process holding `marker` is gone within `timeout` seconds (a zombie counts as
    gone: its parent, which vamos no longer is, reaps it)."""
    end = time.time() + timeout
    while True:
        alive = []
        for pid in marker_pids(marker):
            try:
                with open("/proc/%d/stat" % pid) as fh:
                    if fh.read().rsplit(")", 1)[-1].split()[0] != "Z":
                        alive.append(pid)
            except (OSError, IndexError):
                continue
        if not alive or time.time() > end:
            return not alive
        time.sleep(0.05)


class FakeCase(TempDir):
    """A TempDir with the fake Xyce on VAMOS_XYCE."""

    def setUp(self):
        super().setUp()
        self.fake = os.path.join(self.tmp, "fake_xyce.py")
        with open(self.fake, "w") as fh:
            fh.write(FAKE % {"python": sys.executable, "threads": THREAD_VARS})
        os.chmod(self.fake, os.stat(self.fake).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.environ["VAMOS_XYCE"] = self.fake
        os.environ.pop("VAMOS_VERBOSE", None)

    def behave(self, run_dir: str, ch: Chain, overrides: Optional[Dict[str, dict]] = None) -> Dict[str, dict]:
        """fake.json for a chain: every deck writes a complete print file, then the overrides
        (deck file -> fields; "norun <deck>" for the check)."""
        os.makedirs(run_dir, exist_ok=True)
        decks, _names = RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        spec: Dict[str, dict] = {}
        for d in decks:
            spec[d.file] = {"file": d.output, "write": good_output(d.step, d.text), "rc": 0}
        for key, fields in (overrides or {}).items():
            spec.setdefault(key, {}).update(fields)
        with open(os.path.join(run_dir, "fake.json"), "w") as fh:
            json.dump(spec, fh)
        return spec

    def calls(self, run_dir: str) -> List[str]:
        p = os.path.join(run_dir, "fake.calls")
        if not os.path.exists(p):
            return []
        with open(p) as fh:
            return fh.read().splitlines()

    def envs(self, run_dir: str) -> List[Dict[str, Optional[str]]]:
        """The thread variables each fake run saw, in order."""
        p = os.path.join(run_dir, "fake.env")
        if not os.path.exists(p):
            return []
        with open(p) as fh:
            return [json.loads(x) for x in fh if x.strip()]


SWEEP3 = """// three .STEP points of a tran, then an op
simulator lang=spectre
parameters rval=1k
V1 (in 0) vsource dc=1
R1 (in out) resistor r=rval
C1 (out 0) capacitor c=1u
op1 dc
swp sweep param=rval values=[1k 2k 3k] {
  tr1 tran stop=1m
}
op2 dc
save out
"""

ALTER_TLEV = """// an alter of a diode parameter Xyce does not know (§7.2), then a sweep of it
simulator lang=spectre
V1 (b 0) vsource dc=1
R1 (b a) resistor r=1k
D1 (a 0) dmod
model dmod diode is=1e-14
op1 dc
a1 alter mod=dmod param=tlev value=1
op2 dc
op3 dc
save a
"""

SWEEP_TLEV = """// a sweep of a diode parameter Xyce does not know (§7.2 [E43])
simulator lang=spectre
V1 (b 0) vsource dc=1
R1 (b a) resistor r=1k
D1 (a 0) dmod
model dmod diode is=1e-14
op1 dc
swp sweep mod=dmod param=tlev values=[1 2] {
  op2 dc
}
save a
"""

NOPARAM = "Netlist warning: No model parameter TLEV found for model DMOD of type D, parameter ignored.\n"

OPTS_DECK = """// D2: an options statement that sets reltol, three analyses (three Xyce decks)
simulator lang=spectre
o1 options reltol=1e-4
V1 (in 0) vsource dc=1 mag=1
R1 (in out) resistor r=1k
C1 (out 0) capacitor c=1u
op1 dc
ac1 ac start=1 stop=1k dec=2
tr1 tran stop=1m
save out
"""

# -- R-run-3: Verilog-A that PyMS cannot build, or builds without a missing include ------------------------

VRAMP_VA = """`include "disciplines.vams"
// test_spectre_run_xyce: a voltage ramp through $abstime, V = 1000 * t (1 V at 1 ms): VACASK runs it, PyMS
// cannot build it (R-run-3)
module vramp(p, n);
  inout p, n; electrical p, n;
  analog V(p, n) <+ 1000.0 * $abstime;
endmodule
"""

RAMP_DECK = """// a Verilog-A ramp across a resistor (R-run-3)
simulator lang=spectre
ahdl_include "vramp.va"
X1 (out 0) vramp
R1 (out 0) resistor r=1k
op1 dc
tr1 tran stop=1m maxstep=10u
save out
"""

VINC_VA = """`include "disciplines.vams"
`include "rdefs.vams"
// test_spectre_run_xyce: a resistor whose value comes from a header (R-run-3)
module vinc(a, b);
  inout a, b; electrical a, b;
  parameter real r = `RDEF;
  analog I(a, b) <+ V(a, b) / r;
endmodule
"""

INC_DECK = """// a Verilog-A resistor whose value comes from a header, over 1k (R-run-3)
simulator lang=spectre
ahdl_include "vinc.va"
V1 (a 0) vsource dc=1
X1 (a b) vinc
R2 (b 0) resistor r=1k
op1 dc
save b
"""

HCONST_VA = """`include "discipline.h"
`include "constants.h"
// test_spectre_run_xyce: Spectre's own headers (ref19 pp.452, 541), which PyMS skips and does without (R-run-3)
module hconst(a, b);
  inout a, b; electrical a, b;
  parameter real r = 2000.0;
  analog I(a, b) <+ V(a, b) / (r * `M_PI / 3.141592653589793);
endmodule
"""

HCONST_DECK = """// Spectre's headers in a Verilog-A resistor: b = 1/3 V (R-run-3)
simulator lang=spectre
ahdl_include "hconst.va"
V1 (a 0) vsource dc=1
X1 (a b) hconst
R2 (b 0) resistor r=1k
op1 dc
save b
"""

# PyMS's -norun output for a module it cannot build (measured 2026-10-17 on vramp.va, the cache path
# shortened): the full build fails, the retry without zero-valued parameters fails the same way, and
# Xyce registers the module and goes on without an evaluator
PYMS_TRACE = """***** Reading and parsing netlist...
Netlist warning: .HDL: compiled and registered vramp from vramp.va
***** Setting up topology...

preprocess: warning: 'disciplines.vams' not found (skipped)
build_vae_so: full build failed (g++ -O2 -std=c++17 -o /c/vae_vramp_c42ffd32ff07b125.so.build/ginac /c/vae_vramp_c42ffd32ff07b125.so.build/ginac.); retrying without zero-valued params
Traceback (most recent call last):
  File "/usr/local/share/xyce/PyMS/vae/build_vae_so.py", line 150, in main
    _attempt(params, "full")
    ~~~~~~~~^^^^^^^^^^^^^^^^
  File "/usr/local/share/xyce/PyMS/vae/build_vae_so.py", line 30, in _run
    raise BuildError("%s\\n%s" % (cmd, r.stderr[:4000]))
BuildError: g++ -O2 -std=c++17 -o /c/vae_vramp_c42ffd32ff07b125.so.build/ginac /c/vae_vramp_c42ffd32ff07b125.so.build/ginac.cpp -lginac -lcln
/c/vae_vramp_c42ffd32ff07b125.so.build/ginac.cpp: In function 'int main()':
/c/vae_vramp_c42ffd32ff07b125.so.build/ginac.cpp:127:40: error: '$abstime' was not declared in this scope
  127 |         F_contribs[br_idx] += 1000.0 * $abstime - (V_p - V_n);
      |                                        ^~~~~~~~


During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/usr/local/share/xyce/PyMS/vae/build_vae_so.py", line 163, in <module>
    main()
  File "/usr/local/share/xyce/PyMS/vae/build_vae_so.py", line 159, in main
    _attempt(nz, "nonzero-only")
  File "/usr/local/share/xyce/PyMS/vae/build_vae_so.py", line 30, in _run
    raise BuildError("%s\\n%s" % (cmd, r.stderr[:4000]))
BuildError: g++ -O2 -std=c++17 -o /c/vae_vramp_c42ffd32ff07b125.so.build/ginac /c/vae_vramp_c42ffd32ff07b125.so.build/ginac.cpp -lginac -lcln
/c/vae_vramp_c42ffd32ff07b125.so.build/ginac.cpp: In function 'int main()':
/c/vae_vramp_c42ffd32ff07b125.so.build/ginac.cpp:127:40: error: '$abstime' was not declared in this scope
  127 |         F_contribs[br_idx] += 1000.0 * $abstime - (V_p - V_n);
      |                                        ^~~~~~~~

***** Device Count Summary ...
       R level 1 (Resistor) 1
       VRAMP (vramp)        1
       ----------------------
       Total Devices        2
***** Syntax and topology analysis complete
"""

# a build step that timed out (build_vae_so allows 900 s), and an exception that names no library
PYMS_TIMEOUT = """Traceback (most recent call last):
  File "/usr/local/share/xyce/PyMS/vae/build_vae_so.py", line 163, in <module>
    main()
  File "/usr/local/share/xyce/PyMS/vae/build_vae_so.py", line 94, in _attempt
    _run("%s > %s" % (g_bin, eval_cpp), timeout=900)
  File "/usr/lib/python3.14/subprocess.py", line 556, in run
    raise TimeoutExpired(process.args, timeout, output=stdout,
subprocess.TimeoutExpired: Command '/c/vae_vramp_0a1b2c.so.build/ginac > /c/vae_vramp_0a1b2c.so.build/eval.cpp' timed out after 900 seconds
***** Device Count Summary ...
"""

PYMS_OTHER = """Traceback (most recent call last):
  File "/usr/local/share/xyce/PyMS/vae/build_vae_so.py", line 163, in <module>
    main()
  File "/usr/local/share/xyce/PyMS/vae/build_vae_so.py", line 76, in main
    mod = parse_file(va)
  File "/usr/local/share/xyce/PyMS/vae/parser.py", line 1371, in parse_file
    raise NotImplementedError("analog function idt")
NotImplementedError: analog function idt
***** Device Count Summary ...
"""

# the retry builds the module: only a warning (q.64)
PYMS_RETRIED = """build_vae_so: full build failed (g++ -O2 -std=c++17 -o /c/vae_vramp_1.so.build/ginac /c/vae_vramp_1.so.build/ginac.); retrying without zero-valued params
build_vae_so: built /c/vae_vramp_1.so (2 params, nonzero-only)
"""


# -- both legs -----------------------------------------------------------------------------------------

# phase 2c2 (F4): two Verilog-A modules of one library sharing ../inc, the pair of e2e 8 (vares carries the xyce
# attributes, vres none); the include gives both defaults
VARES_VA = """`include "disciplines.vams"
`include "../inc/rdefs.include"
(*xyceModelGroup="Resistor", xyceSpiceDeviceName="R", xyceLevelNumber="6"*)
module vares (p,n);
   electrical p,n;
   inout p,n;
   (*desc="Resistance", type="instance"*) parameter real R=`RDEF from (0:inf);
   analog I(p,n) <+ V(p,n)/R;
endmodule
"""
VRES_INC_VA = """`include "disciplines.vams"
`include "../inc/rdefs.include"
module vres(a, b);
  inout a, b; electrical a, b;
  parameter real r = `RDEF;
  analog I(a, b) <+ V(a, b) / r;
endmodule
"""
RDEFS = "`define RDEF 4000.0\n"
# b = 1k/(3k+1k) = 0.25 (vares R=3k), c = 1k/(4k+1k) = 0.2 (vres at the include's r = 4k); VACASK gives both exactly
TWOLIB_DECK = """// F4: two Verilog-A modules of one library sharing ../inc
simulator lang=spectre
ahdl_include "lib/vares.va"
ahdl_include "lib/vres.va"
V1 (a 0) vsource dc=1
X1 (a b) vares R=3k
R2 (b 0) resistor r=1k
X2 (a c) vres
R3 (c 0) resistor r=1k
save b c
op1 dc
"""

# phase 2c2: alters of every master's value parameter (the op2 reference values: out 3, o1 2, o2 2, o4 4)
F3_ALTERS = """simulator lang=spectre
V1 (in 0) vsource dc=1
E1 (out 0 in 0) vcvs gain=2
R1 (out 0) resistor r=1k
Vc (c 0) vsource dc=1
Vs (c s1) vsource dc=0
Rs1 (s1 0) resistor r=1k
G1 (0 o1 c 0) vccs gm=1m
Ro1 (o1 0) resistor r=1k
H1 (o2 0) ccvs rm=1k probe=Vs
F1 (0 o4) cccs gain=2 probe=Vs
Ro4 (o4 0) resistor r=1k
Iac (0 p) isource dc=0 mag=1m
L1 (p 0) inductor l=1m
L2 (s 0) inductor l=9m
K1 mutual_inductor coupling=0.5 ind1=L1 ind2=L2
Vd (dd 0) vsource dc=1
Rd (dd a) resistor r=1k
D1 (a 0) dmod area=1
model dmod diode is=1e-14
save out o1 o2 o4 a
op1 dc
a1 alter dev=E1 param=gain value=3
a2 alter dev=G1 param=gm value=2m
a3 alter dev=H1 param=rm value=2k
a4 alter dev=F1 param=gain value=4
a5 alter dev=K1 param=coupling value=0.25
a6 alter dev=D1 param=area value=2
op2 dc
"""


class TestReplay(TempDir):
    """§7.2's state replay (test_spectre_chain.replay is the reference)."""

    def test_alter_type_puts_the_pulse_in_the_tran_deck_only(self):
        ch = Chain(os.path.join(E2E, "06_alter_type", "deck.scs"))
        decks, _ = RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        text = {d.step.name: d.text for d in decks}
        self.assertEqual(sorted(text), ["op1", "op2", "tr"])
        self.assertIn("PULSE(", text["tr"])
        self.assertNotIn("PULSE(", text["op1"])
        self.assertNotIn("PULSE(", text["op2"])
        alters = [a for a in ch.plan.actions if a.op == "alter"]
        self.assertEqual([d.alters for d in decks], [[], [alters[0]], [alters[1]]])
        # replay works on a copy: the input IR keeps its dc source
        src = RX.find_instance(ch.nl2, "Vdif").source
        self.assertIsNone(src.wave)
        after = RX.replay(ch.nl2, alters[0])
        self.assertEqual(RX.find_instance(after, "Vdif").source.wave, "pulse")
        self.assertIsNone(RX.find_instance(ch.nl2, "Vdif").source.wave)

    def deck(self, text: str) -> Chain:
        return Chain(write_deck(self.tmp, "deck.scs", text))

    def test_model_parameter_renamed_by_the_masters_is_reversed(self):
        ch = self.deck("""simulator lang=spectre
V1 (b 0) vsource dc=1
R1 (b a) resistor r=1k
D1 (a 0) dmod
model dmod diode is=1e-14
op1 dc
a1 alter mod=dmod param=ik value=0.01
op2 dc
""")
        act = next(a for a in ch.plan.actions if a.op == "alter")
        self.assertEqual(act.args["target"], ("model", "dmod", "ikf"))      # plan.build's engine spelling
        out = RX.replay(ch.nl2, act)
        self.assertEqual(out.models()["dmod"].params["ik"], Num(0.01))      # the IR keeps Spectre's
        decks, _ = RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        self.assertNotIn("ikf", decks[0].text.lower())
        self.assertIn("ikf=0.01", decks[1].text.lower())

    def test_resistor_value_and_temperature(self):
        ch = self.deck("""simulator lang=spectre
V1 (b 0) vsource dc=1
R1 (b a) resistor r=1k
R2 (a 0) resistor r=1k
op1 dc
a1 alter dev=R2 param=r value=3k
a2 alter param=temp value=50
op2 dc
""")
        decks, _ = RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        self.assertIn("R2 a 0 1000.0", decks[0].text)
        self.assertIn("R2 a 0 3000.0", decks[1].text)
        self.assertIn("temp=27.0", decks[0].text)
        self.assertIn("temp=50.0", decks[1].text)
        work = RX.replay(ch.nl2, ch.plan.actions[[a.op for a in ch.plan.actions].index("alter") + 1])
        self.assertEqual(work.temp, 50.0)

    def test_variable_alter_changes_the_param_and_its_dependents(self):
        ch = self.deck("""simulator lang=spectre
parameters rval=1k r2=2*rval
V1 (b 0) vsource dc=1
R1 (b a) resistor r=rval
R2 (a 0) resistor r=r2
op1 dc
a1 alter param=rval value=3k
op2 dc
""")
        act = next(a for a in ch.plan.actions if a.op == "alter")
        self.assertEqual(act.args["target"], ("variable", "rval", ""))
        out = RX.replay(ch.nl2, act, ch.plan.dependents)
        self.assertEqual(out.values["rval"], 3000.0)
        self.assertEqual(out.values["r2"], 6000.0)
        self.assertEqual(next(p for p in out.body if isinstance(p, ir.Param) and p.name == "rval").expr, Num(3000.0))
        decks, _ = RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        self.assertIn(".param rval=1000.0", decks[0].text)
        self.assertIn(".param rval=3000.0", decks[1].text)
        self.assertEqual(ch.nl2.values["rval"], 1000.0)                    # the input IR is not changed

    def test_instance_rename_reversed(self):
        self.assertEqual(RX.ir_param("bsim3v3", "instance", "instance_nqsmod"), "nqsmod")
        self.assertEqual(RX.ir_param("diode", "instance", "pj"), "perim")
        self.assertEqual(RX.ir_param("diode", "card", "ikf"), "ik")
        self.assertEqual(RX.ir_param("nosuchmaster", "card", "x"), "x")
        self.assertEqual(RX.ir_value([1, 2]), [Num(1.0), Num(2.0)])

    def test_find_instance_through_subckts(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        self.assertEqual(RX.find_instance(ch.nl2, "X1.R1").name, "R1")
        with self.assertRaises(KeyError):
            RX.find_instance(ch.nl2, "X1.R9")

    def test_every_value_parameter_goes_into_instance_value(self):
        """Phase 2c2: an alter of a controlled source's gain, gm or rm, a mutual inductor's coupling or a diode's area
        went into params, so render refused the deck ("E1: parameter gain not supported on a E element", status 2)
        or, for the area, printed the nominal one; spectre.parse holds them in Instance.value (VALUE_KEY)."""
        ch = self.deck(F3_ALTERS)
        decks, _ = RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        before, after = decks[0].text, decks[1].text
        for line0, line1 in (("E1 out 0 in 0 2.0\n", "E1 out 0 in 0 3.0\n"), ("G1 0 o1 c 0 0.001\n", "G1 0 o1 c 0 0.002\n"),
                             ("H1 o2 0 Vs 1000.0\n", "H1 o2 0 Vs 2000.0\n"), ("F1 0 o4 Vs 2.0\n", "F1 0 o4 Vs 4.0\n"),
                             ("K1 L1 L2 0.5\n", "K1 L1 L2 0.25\n"), ("D1 a 0 dmod AREA=1.0\n", "D1 a 0 dmod AREA=2.0\n")):
            self.assertIn(line0, before)
            self.assertIn(line1, after)
        self.assertEqual(RX.VALUE_KEY, PL.VALUE_PARAMS)

    def test_an_alter_of_isnoisy_switches_the_form_per_deck(self):
        """R-plan-19 (phase 2c2): before the alter R1 is the noisy R, after it the noise-free G form, its saved current
        through the probe signals.resolve inserts (the guard `needs a probe' stopped the run before)."""
        ch = self.deck("""simulator lang=spectre
V1 (a 0) vsource dc=1
R1 (a b) resistor r=1k
R2 (b 0) resistor r=1k
save R1:1 b
op1 dc
a1 alter dev=R1 param=isnoisy value=no
op2 dc
a2 alter dev=R1 param=isnoisy value=yes
op3 dc
""")
        decks, names = RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        self.assertIn("\nR1 vamos_tn1 b 1000.0\n", decks[0].text)
        self.assertIn("\ngR1 vamos_tn1 b vamos_tn1 b 0.001\n", decks[1].text)
        self.assertIn("\nR1 vamos_tn1 b 1000.0\n", decks[2].text)
        for d in decks:
            self.assertIn("\nvamos_tp1 a vamos_tn1 DC 0.0\n", d.text)
            self.assertEqual(names[d.step.id][("i", "vamos_tp1")], "I(VAMOS_TP1)")


class TestRenderDecks(unittest.TestCase):
    def test_e2e_decks_equal_the_chain_goldens(self):
        for case in CASES:
            with self.subTest(case=case):
                deck = os.path.join(E2E, case, "deck.scs")
                try:
                    ch = Chain(deck)
                except NoteError as exc:
                    exp = load_json(os.path.join(E2E, case, "engine_expect.json"))["xyce"]
                    self.assertEqual(exp["status"], 2)
                    self.assertTrue(any(exp["refused"] in n.message for n in exc.notes), case)
                    continue
                hdl = [os.path.basename(h) for h in ch.nl2.hdl]
                notes: list = []
                decks, names = RX.render_decks(ch.nl2, ch.plan, ch.sigmap, hdl, notes)
                self.assertEqual(names, xyce.names_for(ch.nl2, ch.plan, ch.sigmap))
                gdir = os.path.join(E2E, case, "render", "xyce")
                self.assertEqual(sorted(d.file for d in decks), sorted(os.listdir(gdir)))
                for d in decks:
                    with open(os.path.join(gdir, d.file), encoding="utf-8") as fh:
                        self.assertEqual(d.text, fh.read(), "%s %s" % (case, d.file))
                self.assertFalse([n for n in notes if n.severity == ERROR])
                messages = [n.message for n in notes]
                self.assertEqual(len(messages), len(set(messages)), "each note once")

    def test_xf_is_refused(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        step = ch.steps[0]
        step.kind = "xf"
        with self.assertRaises(NoteError) as cm:
            RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        self.assertTrue(any("xf runs on VACASK only" in n.message for n in cm.exception.notes))


class TestPlaceVa(TempDir):
    def src(self, rel: str, text: str) -> str:
        return write_deck(os.path.dirname(os.path.join(self.tmp, "src", rel)), os.path.basename(rel), text)

    def test_base_names_and_the_includes_beside_them(self):
        a = self.src("lib/ires.va", '`include "disciplines.vams"\n`include "ires_defs.include"\nmodule ires(a, b); endmodule\n')
        self.src("lib/ires_defs.include", "`define RDEF 4000.0\n")
        b = self.src("other/vres.va", "module vres(a, b); endmodule\n")
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        self.assertEqual(RX.place_va([a, b, a], run_dir), ["ires.va", "vres.va"])
        self.assertEqual(sorted(os.listdir(run_dir)), ["ires.va", "ires_defs.include", "vres.va"])
        self.assertEqual(RX.va_closure(a), [os.path.abspath(a), os.path.join(os.path.dirname(os.path.abspath(a)),
                                                                            "ires_defs.include")])

    def test_a_base_name_clash_goes_into_a_subdirectory(self):
        a = self.src("one/r.va", "module r1(a, b); endmodule\n")
        b = self.src("two/r.va", "module r2(a, b); endmodule\n")
        c = self.src("three/r.va", "module r1(a, b); endmodule\n")       # the same text as one/r.va: shared
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        self.assertEqual(RX.place_va([a, b, c], run_dir), ["r.va", "vamos_va1/r.va", "r.va"])
        with open(os.path.join(run_dir, "vamos_va1", "r.va")) as fh:
            self.assertIn("r2", fh.read())

    def test_an_include_that_climbs_keeps_its_relative_place(self):
        a = self.src("models/m.va", '`include "../common/defs.include"\nmodule m(a, b); endmodule\n')
        self.src("common/defs.include", "`define X 1\n")
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        self.assertEqual(RX.place_va([a], run_dir), ["models/m.va"])
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "common", "defs.include")))

    def test_a_missing_source_keeps_its_path(self):
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        missing = os.path.join(self.tmp, "nope.va")
        self.assertEqual(RX.place_va([missing], run_dir), [missing])

    def test_two_modules_of_one_library_never_share_a_directory(self):
        """F4 (phase 2c2): two modules of one lib/ sharing ../inc landed side by side in run_dir/lib/, where PyMS gave
        the module without xyce attributes those of the one beside it, and both lost their parameters (status 2,
        E125): the second gets its own vamos_va<k>/ with its include tree, the .hdl names keep the base names."""
        a = self.src("lib/vares.va", VARES_VA)
        b = self.src("lib/vres.va", VRES_INC_VA)
        self.src("inc/rdefs.include", RDEFS)
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        names = RX.place_va([a, b], run_dir)
        self.assertEqual(names, ["lib/vares.va", "vamos_va1/lib/vres.va"])
        for rel in ("lib/vares.va", "inc/rdefs.include", "vamos_va1/lib/vres.va", "vamos_va1/inc/rdefs.include"):
            self.assertTrue(os.path.isfile(os.path.join(run_dir, rel)), rel)
        dirs = [os.path.dirname(n) for n in names]
        self.assertEqual(len(set(dirs)), len(dirs))                        # one module per directory
        for d in dirs:                                                     # and no other .va where PyMS looks
            self.assertEqual(len([f for f in os.listdir(os.path.join(run_dir, d)) if f.endswith(".va")]), 1, d)
        # a third module and a vamos_va<k>/ another module holds: a fresh vamos_va<k>/ each
        c = self.src("lib/vres3.va", VRES_INC_VA.replace("module vres(", "module vres3("))
        self.assertEqual(RX.place_va([a, b, c], os.path.join(self.tmp, "run2")),
                         ["lib/vares.va", "vamos_va1/lib/vres.va", "vamos_va2/lib/vres3.va"])
        # base names stay beside the deck, any number of them (E104), also next to a module with a directory
        d = self.src("flat/one.va", "module one(a, b); endmodule\n")
        e = self.src("flat/two.va", "module two(a, b); endmodule\n")
        self.assertEqual(RX.place_va([a, d, e, b], os.path.join(self.tmp, "run3")),
                         ["lib/vares.va", "one.va", "two.va", "vamos_va1/lib/vres.va"])

    def test_a_module_s_files_never_land_in_another_module_s_directory(self):
        """F4: a base-named module whose include sits in a subdirectory another module's file holds goes into its
        own vamos_va<k>/ instead (PyMS would read that include beside the other module)."""
        a = self.src("models/m.va", '`include "../common/defs.include"\nmodule m(a, b); endmodule\n')
        self.src("common/defs.include", "`define X 1\n")
        b = self.src("top/n.va", '`include "models/helper.va"\nmodule n(a, b); endmodule\n')
        self.src("top/models/helper.va", "`define Y 2\n")
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        self.assertEqual(RX.place_va([a, b], run_dir), ["models/m.va", "vamos_va1/n.va"])
        self.assertEqual(sorted(os.listdir(os.path.join(run_dir, "models"))), ["m.va"])


class TestCheckOutput(TempDir):
    """§7.2: a print file that is missing, blank or short is a failed analysis."""

    def steps(self, text: str) -> List[PL.AnalysisStep]:
        return Chain(write_deck(self.tmp, "deck.scs", text)).steps

    def setUp(self):
        super().setUp()
        st = self.steps(SWEEP3.replace("op2 dc", "op2 dc\nac1 ac start=1 stop=100 dec=1\ndc1 dc dev=V1 param=dc "
                                               "start=0 stop=1 step=0.25"))
        self.op, self.tran, self.op2, self.ac, self.dc = st

    def file(self, name: str, text: str) -> str:
        return write_deck(self.tmp, name, text)

    def test_complete_files(self):
        self.assertEqual(RX.planned(self.op), (1, 1, 1))
        self.assertEqual(RX.planned(self.tran), (3, 1, None))
        self.assertEqual(RX.planned(self.ac), (1, 1, 3))
        self.assertEqual(RX.planned(self.dc), (1, 1, 5))
        tr = [tuple([t, 0.5]) for t in (0.0, 5e-4, 1e-3)]
        self.assertTrue(RX.check_output(self.tran, self.file("t.raw", raw_text(["TIME", "V(OUT)"], [tr] * 3))).complete)
        self.assertTrue(RX.check_output(self.op, self.file("o.raw", raw_text(["sweep", "V(OUT)"], [[(0, 1)]]))).complete)
        dc = [(v / 4.0, 1.0) for v in range(5)]
        self.assertTrue(RX.check_output(self.dc, self.file("d.raw", raw_text(["sweep", "V(OUT)"], [dc]))).complete)

    def test_descending_grids_plan_the_plans_points(self):
        """R-emit-3 (the phase-2c integration's xyce.py seam): render prints a descending dec or lin grid as a LIST
        or DATA= table of plan.grid_points' points, and a descending step frequency grid as its DATA= table (it
        printed an empty one), so planned counts every point: the step ac counted 0 rows and failed a run that
        wrote its 4."""
        st = self.steps("""simulator lang=spectre
I1 (0 out) isource dc=1m mag=1
R1 (out 0) resistor r=1k
d4 dc dev=I1 param=dc start=1m stop=1u dec=1
acd ac start=1M stop=1k dec=1
acl ac start=1M stop=1k lin=3
afs ac start=1M stop=1k step=-333k
""")
        self.assertEqual([(s.name, RX.planned(s)) for s in st],
                         [("d4", (1, 1, 4)), ("acd", (1, 1, 4)), ("acl", (1, 1, 4)), ("afs", (1, 1, 4))])

    def test_missing_blank_short_and_unreadable(self):
        chk = RX.check_output(self.op, os.path.join(self.tmp, "none.raw"))
        self.assertEqual((chk.complete, chk.first_bad), (False, 0))
        self.assertIn("wrote no", chk.why)
        header_only = raw_text(["TIME", "V(OUT)"], [[]], blank=(0,))
        chk = RX.check_output(self.tran, self.file("h.raw", header_only))
        self.assertEqual((chk.complete, chk.first_bad), (False, 0))
        self.assertIn("blank No. Points", chk.why)
        dc = [(v / 4.0, 1.0) for v in range(4)]
        chk = RX.check_output(self.dc, self.file("d.raw", raw_text(["sweep", "V(OUT)"], [dc])))
        self.assertIn("4 rows where 5 were planned", chk.why)
        chk = RX.check_output(self.op, self.file("x.raw", "not a rawfile\n"))
        self.assertFalse(chk.complete)
        self.assertIn("unreadable", chk.why)

    def test_a_tran_short_of_stop_or_a_missing_step_point(self):
        tr = [tuple([t, 0.5]) for t in (0.0, 5e-4, 1e-3)]
        short = [tuple([t, 0.5]) for t in (0.0, 5e-4)]
        chk = RX.check_output(self.tran, self.file("s.raw", raw_text(["TIME", "V(OUT)"], [tr, short, tr])))
        self.assertEqual((chk.complete, chk.first_bad), (False, 1))
        self.assertIn("not at stop", chk.why)
        chk = RX.check_output(self.tran, self.file("m.raw", raw_text(["TIME", "V(OUT)"], [tr, tr])))
        self.assertEqual((chk.complete, chk.first_bad), (False, 2))
        self.assertIn("2 of the 3", chk.why)

    def test_a_failed_step_point_merged_into_the_next(self):
        # Xyce goes on after a failed .STEP point and appends the next point to its plot (measured)
        tr = [tuple([t, 0.5]) for t in (0.0, 5e-4, 1e-3)]
        merged = [tuple([t, 0.5]) for t in (0.0, 2e-4)] + tr
        chk = RX.check_output(self.tran, self.file("g.raw", raw_text(["TIME", "V(OUT)"], [tr, merged])))
        self.assertEqual((chk.complete, chk.first_bad), (False, 1))
        self.assertIn("restarts its time axis", chk.why)

    def test_declared_count_differs_and_too_many_plots(self):
        tr = [tuple([t, 0.5]) for t in (0.0, 5e-4, 1e-3)]
        chk = RX.check_output(self.tran, self.file("c.raw", raw_text(["TIME", "V(OUT)"], [tr] * 3, declared={2: 7})))
        self.assertEqual(chk.first_bad, 2)
        chk = RX.check_output(self.tran, self.file("p.raw", raw_text(["TIME", "V(OUT)"], [tr] * 4)))
        self.assertEqual((chk.complete, chk.first_bad), (False, None))
        chk = RX.check_output(self.op, self.file("o2.raw", raw_text(["sweep", "V(OUT)"], [[(0, 1), (0, 1)]])))
        self.assertIn("2 rows where 1 were planned", chk.why)

    def test_noise_tables(self):
        st = self.steps("""simulator lang=spectre
V1 (in 0) vsource dc=0
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
swp sweep dev=R2 param=r values=[1k 2k] {
  nz (out 0) noise iprobe=V1 start=1 stop=100 dec=1
}
""")
        nz = st[0]
        self.assertEqual(RX.planned(nz), (2, 1, 3))
        rows = [(1.0, 1e-18), (10.0, 1e-18), (100.0, 1e-18)]
        good = prn_text(["FREQ", "ONOISE"], [rows, rows])
        self.assertTrue(RX.check_output(nz, self.file("n.prn", good)).complete)
        chk = RX.check_output(nz, self.file("n2.prn", prn_text(["FREQ", "ONOISE"], [rows, rows[:2]])))
        self.assertEqual((chk.complete, chk.first_bad), (False, 1))
        chk = RX.check_output(nz, self.file("n3.prn", prn_text(["FREQ", "ONOISE"], [rows])))
        self.assertEqual(chk.first_bad, 1)


class TestHelpers(TempDir):
    def test_ended_by(self):
        self.assertEqual(RX.ended_by(-15), 15)
        self.assertEqual(RX.ended_by(-11), 11)
        self.assertEqual(RX.ended_by(128 + signal.SIGTERM), signal.SIGTERM)     # E72's 143
        self.assertEqual(RX.ended_by(128 + signal.SIGINT), signal.SIGINT)       # and 130
        self.assertIsNone(RX.ended_by(0))
        self.assertIsNone(RX.ended_by(1))
        self.assertIsNone(RX.ended_by(200))

    def test_diagnostics(self):
        text = """***** Welcome to the Xyce(TM) Parallel Electronic Simulator
***** Beginning Transient Calculation...
Netlist warning: Numerically singular matrix found by Amesos, returning zero
 solution to nonlinear solver!
Netlist warning: Numerically singular matrix found by Amesos, returning zero
 solution to nonlinear solver!
	Number Failed Steps Attempted:		1
	Failed DC sweep steps:
		DC Step # 1
Time step too small near step number: 464  Exiting transient loop.
function OneStep::rejectStep:
   Maximum number of failures at time 1e-06
*** Xyce Abort ***
  Netlist Import                             1        0.552 (60.87%)        0.039 (47.31%)
/tmp/pyms_vae_cache/vae_x.so.build/eval.cpp:10:24: error: '$abstime' was not declared in this scope
build_vae_so: full build failed (g++ -O2 ...); retrying without zero-valued params
"""
        self.assertEqual(RX.diagnostics(text), [
            "Netlist warning: Numerically singular matrix found by Amesos, returning zero solution to nonlinear solver!",
            "Failed DC sweep steps: DC Step # 1",
            "Time step too small near step number: 464  Exiting transient loop.",
            "function OneStep::rejectStep: Maximum number of failures at time 1e-06",
            "*** Xyce Abort ***",
            "/tmp/pyms_vae_cache/vae_x.so.build/eval.cpp:10:24: error: '$abstime' was not declared in this scope",
            "build_vae_so: full build failed (g++ -O2 ...); retrying without zero-valued params"])

    def test_trim_partial_line(self):
        p = write_deck(self.tmp, "x.prn", "Index FREQ ONOISE\n0 1.0 2.0\n1 2.0 3.")
        self.assertTrue(RX.trim_partial_line(p))
        with open(p) as fh:
            self.assertEqual(fh.read(), "Index FREQ ONOISE\n0 1.0 2.0\n")
        self.assertFalse(RX.trim_partial_line(p))
        self.assertEqual(len(rawfile.read_prn(p)[0].points), 1)
        q = write_deck(self.tmp, "y.prn", "Index FREQ" + " " * 70000)
        self.assertTrue(RX.trim_partial_line(q))
        self.assertEqual(os.path.getsize(q), 0)

    def test_signals_is_the_stop_contract(self):
        s = RX.Signals(grace=0.25)
        self.assertEqual(s.handled, stop.HANDLED)
        self.assertEqual(s.forward, stop.FORWARD)
        self.assertEqual(s.flags, stop.FLAGS)
        self.assertEqual(s.grace, 0.25)
        self.assertTrue(s.group)                         # Xyce's process group, its PyMS compile too (R-run-5)
        self.assertIsNone(s.second)
        s._on_signal(signal.SIGINT, None)
        s._on_signal(signal.SIGTERM, None)
        self.assertEqual((s.signum, s.second, s.killed), (signal.SIGINT, signal.SIGTERM, False))
        s._on_alarm(None, None)
        self.assertTrue(s.expired)


class TestRunFake(FakeCase):
    """run() against the fake Xyce: the control flow of §7.2 and §2.7 on both legs."""

    def test_every_deck_runs_after_the_norun_check(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch)
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 0, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "ok", "vamos_a3": "ok"})
        self.assertEqual(er.files, {s: os.path.join(run_dir, s + ".raw") for s in ("vamos_a1", "vamos_a2", "vamos_a3")})
        self.assertEqual(er.failed_points, {})
        self.assertEqual(er.names, xyce.names_for(ch.nl2, ch.plan, ch.sigmap))
        self.assertEqual(self.calls(run_dir), ["-norun vamos_a1.cir", "vamos_a1.cir", "vamos_a2.cir", "vamos_a3.cir"])
        for k in (1, 2, 3):
            with open(os.path.join(E2E, "01_rc", "render", "xyce", "vamos_a%d.cir" % k)) as g, \
                    open(os.path.join(run_dir, "vamos_a%d.cir" % k)) as fh:
                self.assertEqual(fh.read(), g.read())
            self.assertTrue(os.path.isfile(os.path.join(run_dir, "vamos_a%d.log" % k)))
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "vamos_a1.norun.log")))
        self.assertTrue(any(line.startswith("vamos_a3.cir: vamos_a3.cir -> exit 0") for line in er.log), er.log)
        self.assertNotIn("Error from", log)

    def test_a_norun_problem_stops_before_any_run(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", ALTER_TLEV.replace("is=1e-14", "is=1e-14 tlev=1")))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"norun vamos_a1.cir": {"out": NOPARAM}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual((er.rc, er.status), (2, {}), log)
        self.assertEqual(self.calls(run_dir), ["-norun vamos_a1.cir"])
        self.assertIn("Error from spectre during initial setup.", log)
        self.assertIn("No model parameter TLEV found for model DMOD", log)

    def test_a_problem_after_an_alter_names_the_alter(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", ALTER_TLEV))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {"out": NOPARAM}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 2, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed"})       # op3 never ran
        self.assertEqual(self.calls(run_dir), ["-norun vamos_a1.cir", "vamos_a1.cir", "vamos_a2.cir"])
        self.assertIn("alter `a1' sets a parameter Xyce does not know", log)
        self.assertIn("Error from spectre during DC analysis `op2'.", log)

    def test_a_problem_in_a_sweep_names_the_sweep(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", SWEEP_TLEV))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {
            "out": "Netlist error: Function or variable DMOD:TLEV is not defined\n*** Xyce Abort ***\n", "rc": 1}})
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 2, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed"})
        self.assertIn("sweep `swp' sets a parameter Xyce does not know", log)

    def test_an_unattributed_netlist_error_names_the_analysis(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {"out": "Netlist error: something Xyce dislikes\n", "rc": 1}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 2, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed"})
        self.assertIn("Xyce, the deck of analysis `myac' (vamos_a2.cir): Netlist error: something Xyce dislikes", log)

    def test_a_failed_analysis_among_good_ones(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        header = raw_text(["FREQUENCY", "V(IN)"], [[]], blank=(0,), cplx=True)
        self.behave(run_dir, ch, {"vamos_a2.cir": {"write": header, "rc": 1,
                                                   "out": "Time step too small near step number: 4  Exiting transient loop.\n"}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 1, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed", "vamos_a3": "ok"})
        self.assertEqual(self.calls(run_dir)[-1], "vamos_a3.cir")
        self.assertIn("analysis `myac' failed in Xyce (exit status 1; vamos_a2.raw has a blank No. Points (0 rows))", log)
        self.assertIn("Time step too small", log)
        self.assertIn("vamos_a2", er.files)

    def test_a_missing_print_file_with_exit_0_is_a_failure(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        spec = self.behave(run_dir, ch)
        del spec["vamos_a1.cir"]["write"]
        with open(os.path.join(run_dir, "fake.json"), "w") as fh:
            json.dump(spec, fh)
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 1, log)
        self.assertEqual(er.status["vamos_a1"], "failed")
        self.assertNotIn("vamos_a1", er.files)
        self.assertIn("Xyce wrote no vamos_a1.raw", log)

    def test_a_failed_step_point_gives_failed_points(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", SWEEP3))
        run_dir = os.path.join(self.tmp, "run")
        tr = [tuple([t, 0.5]) for t in (0.0, 5e-4, 1e-3)]
        merged = [tuple([t, 0.5]) for t in (0.0, 2e-4)] + tr
        self.behave(run_dir, ch, {"vamos_a2.cir": {"write": raw_text(["TIME", "V(OUT)"], [tr, merged])}})
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 1, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed", "vamos_a3": "ok"})
        self.assertEqual(er.failed_points, {"vamos_a2": 2})
        self.assertIn("the sweep points before point 2 are written", log)

    def test_a_first_step_point_failed_fails_the_whole_step(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", SWEEP3))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {"write": raw_text(["TIME", "V(OUT)"], [[]], blank=(0,)),
                                                   "rc": 1}})
        er, log, _ = run_chain(self, ch)
        self.assertEqual((er.rc, er.status["vamos_a2"], er.failed_points), (1, "failed", {}), log)

    @unittest.skipUnless(LINUX, "a self-inflicted signal of the fake Xyce (Linux/WSL)")
    def test_a_crash_is_status_2(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {"abort": True}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 2, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed"})
        self.assertIn("Xyce crashed (ended by SIGABRT)", log)
        self.assertEqual(self.calls(run_dir)[-1], "vamos_a2.cir")

    @unittest.skipUnless(LINUX, "a self-inflicted signal of the fake Xyce (Linux/WSL)")
    def test_a_kill_from_outside_is_status_3(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {"signal": "SIGKILL"}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 3, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "interrupted"})
        self.assertIn("analysis `myac' was interrupted (SIGKILL from outside vamos); its partial results "
                      "(vamos_a2.raw) are written", log)
        self.assertNotIn("vamos_a3.cir", self.calls(run_dir))

    def grace_kill(self) -> "tuple":
        """A Xyce that ignores the forwarded SIGTERM in the second deck, the grace set to 0.5 s:
        (EngineResult, log, seconds from the SIGTERM to run()'s return)."""
        from vamos import proc
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {"ignore_term": True, "sleep": 60}})
        late: List[int] = []
        saved = signal.signal(signal.SIGTERM, lambda s, f: late.append(s))
        grace, proc.INTERRUPT_GRACE = proc.INTERRUPT_GRACE, 0.5
        sent: List[float] = []

        def send() -> None:
            t0 = time.time()
            while time.time() - t0 < 60 and "vamos_a2.cir" not in self.calls(run_dir):
                time.sleep(0.02)
            time.sleep(0.3)
            sent.append(time.time())
            os.kill(os.getpid(), signal.SIGTERM)
        th = threading.Thread(target=send, daemon=True)
        th.start()
        t0 = time.time()
        try:
            er, log, _ = run_chain(self, ch)
            ended = time.time()
        finally:
            proc.INTERRUPT_GRACE = grace
            signal.signal(signal.SIGTERM, saved)
        th.join(10)
        self.assertLess(ended - t0, 30)
        self.assertEqual(late, [])
        self.assertTrue(sent, "the SIGTERM was sent")
        return er, log, ended - sent[0]

    @unittest.skipUnless(LINUX, "signals to the test process (Linux/WSL)")
    def test_the_grace_kill_is_converted_as_status_3(self):
        """stop.py: a Xyce still running proc.INTERRUPT_GRACE seconds after the forwarded SIGTERM is
        killed; that is not a second signal: the run is converted (rc 3), no stop.Stop.  R-tests-3: the
        run ends the grace the test sets after the signal (0.5 s), not the 5 s default: a runner that
        ignored proc.INTERRUPT_GRACE took 5.4 s here."""
        er, log, took = self.grace_kill()
        self.assertEqual(er.rc, 3, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "interrupted"})
        self.assertIn("analysis `myac' was interrupted (SIGTERM", log)
        self.assertTrue(any("vamos_a2.cir -> exit -9" in line for line in er.log), er.log)
        self.assertGreater(took, 0.4, "the grace was waited for")
        self.assertLess(took, 2.5, "the 0.5 s grace, not the 5 s default")

    @unittest.skipUnless(LINUX, "signals to the test process (Linux/WSL)")
    def test_the_grace_kill_warning_names_the_kill(self):
        """R-run-17: the interrupted analysis's warning says that Xyce outlived the grace and was killed,
        naming the grace, as run_vacask's does (it said only `(SIGTERM)')."""
        er, log, _took = self.grace_kill()
        self.assertEqual(er.rc, 3, log)
        self.assertIn("analysis `myac' was interrupted (SIGTERM; Xyce did not stop within 0.5 s and was killed); "
                      "its partial results (vamos_a2.raw) are written", log)

    def test_openblas_is_bounded_unless_the_user_sets_a_thread_count(self):
        """R-run-4: every Xyce process (the -norun check and each deck) runs with OPENBLAS_NUM_THREADS=1:
        OpenBLAS's pool otherwise starts one thread per CPU, which spins (about 2 s of CPU per process
        on 32 CPUs, no wall time gained); a thread count the user set (OPENBLAS_NUM_THREADS, or
        GOTO_NUM_THREADS or OMP_NUM_THREADS, which OpenBLAS falls back on) is left as it is, and
        vamos's own environment is not changed."""
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        for v in THREAD_VARS:
            os.environ.pop(v, None)
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch)
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 0, log)
        self.assertEqual(self.envs(run_dir), [{"OPENBLAS_NUM_THREADS": "1", "GOTO_NUM_THREADS": None,
                                               "OMP_NUM_THREADS": None}] * 4)
        self.assertFalse([v for v in THREAD_VARS if v in os.environ])
        for var, val in (("OPENBLAS_NUM_THREADS", "8"), ("GOTO_NUM_THREADS", "2"), ("OMP_NUM_THREADS", "4")):
            with self.subTest(var=var):
                for v in THREAD_VARS:
                    os.environ.pop(v, None)
                os.environ[var] = val
                name = "run_" + var
                run_dir = os.path.join(self.tmp, name)
                self.behave(run_dir, ch)
                er, log, _ = run_chain(self, ch, name)
                self.assertEqual(er.rc, 0, log)
                want: Dict[str, Optional[str]] = {v: None for v in THREAD_VARS}
                want[var] = val
                self.assertEqual(self.envs(run_dir), [want] * 4)

    def test_verbose_echo_and_no_analyses(self):
        os.environ["VAMOS_VERBOSE"] = "1"
        ch = Chain(os.path.join(E2E, "06_alter_type", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch)
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 0, log)
        self.assertIn("vamos: + (cd %s && %s -norun vamos_a1.cir)" % (run_dir, self.fake), log)
        self.assertIn("vamos: + (cd %s && %s vamos_a3.cir)" % (run_dir, self.fake), log)
        empty = PL.RunPlan(actions=[], engine="xyce")
        lf = LogFile(self.tmp)
        er = RX.run(ch.nl2, empty, ch.sigmap, os.path.join(self.tmp, "empty"), lf.job, lf.log)
        self.assertEqual((er.rc, er.status, er.files), (0, {}, {}))
        self.assertEqual(lf.text(), "")

    def test_a_xyce_that_cannot_start(self):
        os.environ["VAMOS_XYCE"] = os.path.join(self.tmp, "no", "Xyce")
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        er, log, _ = run_chain(self, ch)
        self.assertEqual((er.rc, er.status), (2, {}), log)
        self.assertIn("cannot run", log)

    def test_xf_guard(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        ch.steps[1].kind = "xf"
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual((er.rc, er.status), (2, {}))
        self.assertIn("xf runs on VACASK only", log)
        self.assertEqual(self.calls(run_dir), [])

    def test_render_notes_are_reported_once(self):
        """Render's notes print once each, in circuit read-in (§8.7).  D2 (R-run-15): Xyce's tolerance
        notices name only the settings the netlist set: e2e 01, which sets none, prints none (it printed
        reltol, vabstol and iabstol, the plan's defaults), and an options statement's reltol is noted
        once for the run's three decks."""
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch)
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 0)
        self.assertNotIn("is not mapped on Xyce", log)
        ch = Chain(write_deck(self.tmp, "opts.scs", OPTS_DECK))
        run_dir = os.path.join(self.tmp, "run2")
        self.behave(run_dir, ch)
        er, log, _ = run_chain(self, ch, "run2")
        self.assertEqual(er.rc, 0, log)
        noted = [ln for ln in log.splitlines() if "is not mapped on Xyce" in ln]
        self.assertEqual(len(noted), 1, log)
        self.assertIn("reltol", noted[0])
        self.assertIn("Notice from spectre during circuit read-in.", log)

    def test_each_deck_between_its_banner_and_its_time(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch)
        er, log, _ = run_chain(self, ch, profile=True)
        self.assertEqual(er.rc, 0, log)
        marks = ["DC Analysis `myop'", "Total time required for dc analysis `myop' was ",
                 "AC Analysis `myac': freq = (1 Hz -> 1 MHz)", "Total time required for ac analysis `myac' was ",
                 "Transient Analysis `tr': time = (0 s -> 5 ms)", "Total time required for tran analysis `tr' was "]
        at = [log.find(m) for m in marks]
        self.assertTrue(all(a >= 0 for a in at), "%s\n%s" % (at, log))
        self.assertEqual(at, sorted(at), log)

    def test_xyce_messages_once_per_run_without_the_registrations(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        out = ("Netlist warning: .HDL: compiled and registered vres from vres.va\n"
               "Netlist warning in file vamos_a1.cir at or near line 3 Device model DM: Given pbswgs is less than\n"
               " 0.1. Pbswgs is set to 0.1\n")
        self.behave(run_dir, ch, {"vamos_a1.cir": {"out": out}, "vamos_a2.cir": {"out": out}})
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 0, log)
        self.assertEqual(log.count("Xyce: Netlist warning in file vamos_a1.cir at or near line 3 Device model DM: "
                                   "Given pbswgs is less than 0.1. Pbswgs is set to 0.1"), 1, log)
        self.assertIn("Warning from spectre during DC analysis `myop'.", log)
        self.assertNotIn("compiled and registered", log)
        self.assertIn("vamos_a2.log: Netlist warning: .HDL: compiled and registered vres from vres.va", er.log)

    def test_strict_makes_render_warnings_errors(self):
        deck = write_deck(self.tmp, "deck.scs", """// a noise-free resistor with a temperature coefficient: render warns
simulator lang=spectre
V1 (a 0) vsource dc=1
R1 (a b) resistor r=1k isnoisy=no tc1=0.001
R2 (b 0) resistor r=1k
op1 dc
""")
        ch = Chain(deck)
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch)
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 0, log)
        self.assertIn("Warning from spectre during circuit read-in.", log)
        self.assertIn("isnoisy=no with tc1/tc2", log)
        er, log, run_dir = run_chain(self, ch, "strict", strict=True)
        self.assertEqual((er.rc, er.status), (2, {}), log)
        self.assertIn("Error from spectre during circuit read-in.", log)
        self.assertEqual(self.calls(run_dir), [])


class TestVerilogAChecks(FakeCase):
    """R-run-3 (§0: never silently mis-simulate): a module PyMS cannot build runs as a device that
    contributes nothing, and a module whose `include PyMS does not find is built without it; both are
    exit status 2, never a run (VACASK's openvaf-r refuses the second, and Spectre either)."""

    def va_chain(self, va_name: str, va_text: str, deck_text: str) -> Chain:
        write_deck(self.tmp, va_name, va_text)
        return Chain(write_deck(self.tmp, "deck.scs", deck_text))

    def test_a_failed_pyms_build_stops_the_run_after_the_norun_check(self):
        ch = self.va_chain("vramp.va", VRAMP_VA, RAMP_DECK)
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"norun vamos_a1.cir": {"out": PYMS_TRACE}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual((er.rc, er.status), (2, {}), log)
        self.assertEqual(self.calls(run_dir), ["-norun vamos_a1.cir"])       # no analysis ran
        self.assertIn("Error from spectre during initial setup.", log)
        self.assertIn("PyMS could not build Verilog-A module vramp", log)
        self.assertIn(os.path.join(self.tmp, "vramp.va"), log)
        self.assertIn("'$abstime' was not declared in this scope", log)
        self.assertIn("contributes nothing", log)

    def test_a_timeout_or_another_exception_of_the_build_is_a_failed_build_too(self):
        for name, out, who in (("timeout", PYMS_TIMEOUT, "Verilog-A module vramp"),
                               ("other", PYMS_OTHER, "a Verilog-A module")):
            with self.subTest(case=name):
                ch = self.va_chain("vramp.va", VRAMP_VA, RAMP_DECK)
                run_dir = os.path.join(self.tmp, "run_" + name)
                self.behave(run_dir, ch, {"norun vamos_a1.cir": {"out": out}})
                er, log, run_dir = run_chain(self, ch, "run_" + name)
                self.assertEqual((er.rc, er.status), (2, {}), log)
                self.assertEqual(self.calls(run_dir), ["-norun vamos_a1.cir"])
                self.assertIn("PyMS could not build %s" % who, log)
                self.assertIn("timed out after 900 seconds" if name == "timeout" else "analog function idt", log)

    def test_a_build_the_retry_saves_is_a_run(self):
        ch = self.va_chain("vramp.va", VRAMP_VA, RAMP_DECK)
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"norun vamos_a1.cir": {"out": PYMS_RETRIED}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 0, log)
        self.assertEqual(self.calls(run_dir), ["-norun vamos_a1.cir", "vamos_a1.cir", "vamos_a2.cir"])
        self.assertNotIn("PyMS could not build", log)

    def test_a_deck_whose_own_build_fails_stops_the_later_decks(self):
        """A build in a deck run (an alter or a sweep gives the module other parameters, so another
        library) that fails: that deck is failed, status 2, the later decks are not run, and the
        build's own lines are not narrated beside the error."""
        ch = self.va_chain("vramp.va", VRAMP_VA, RAMP_DECK)
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a1.cir": {"out": PYMS_TRACE}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual((er.rc, er.status), (2, {"vamos_a1": "failed"}), log)
        self.assertEqual(self.calls(run_dir), ["-norun vamos_a1.cir", "vamos_a1.cir"])
        self.assertIn("Error from spectre during DC analysis `op1'.", log)
        self.assertIn("PyMS could not build Verilog-A module vramp", log)
        self.assertNotIn("Xyce: BuildError", log)

    def test_pyms_failures(self):
        got = RX.pyms_failures(PYMS_TRACE)
        self.assertEqual([(f.module, f.detail) for f in got],
                         [("vramp", "'$abstime' was not declared in this scope")])
        self.assertTrue(any(ln.startswith("BuildError: g++") for ln in got[0].lines), got[0].lines)
        self.assertEqual([f.module for f in RX.pyms_failures(PYMS_TIMEOUT)], ["vramp"])
        self.assertEqual([(f.module, f.detail) for f in RX.pyms_failures(PYMS_OTHER)],
                         [("", "NotImplementedError: analog function idt")])
        self.assertEqual(RX.pyms_failures(PYMS_RETRIED), [])
        self.assertEqual(RX.pyms_failures("Traceback (most recent call last):\n  File \"x.py\", line 1\n"
                                          "ValueError: not PyMS's\n"), [])

    def test_a_missing_include_is_refused_before_xyce_starts(self):
        """PyMS skips an `include it does not find and builds the module without it (rc 0, a wrong
        device, on a cold cache and, with the warning gone, on a warm one): refused before Xyce runs."""
        ch = self.va_chain("vinc.va", VINC_VA, INC_DECK)
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch)
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual((er.rc, er.status), (2, {}), log)
        self.assertEqual(self.calls(run_dir), [])                             # Xyce never started
        self.assertIn("Error from spectre during AHDL read-in.", log)
        self.assertIn("%s: `include \"rdefs.vams\"" % os.path.join(self.tmp, "vinc.va"), log)
        self.assertNotIn("disciplines.vams", log)

    def test_a_header_found_only_through_cds_vloga_include_is_refused(self):
        """§2.8: Xyce's compile searches no include directory, so a header that only CDS_VLOGA_INCLUDE
        (or -I) finds is missing there (VACASK finds it: b = 1/3 V; Xyce gave b = 0, rc 0)."""
        ch = self.va_chain("vinc.va", VINC_VA, INC_DECK)
        hdr = os.path.dirname(self.write("hdr/rdefs.vams", "`define RDEF 2000.0\n"))
        os.environ["CDS_VLOGA_INCLUDE"] = hdr
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch)
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual((er.rc, er.status), (2, {}), log)
        self.assertEqual(self.calls(run_dir), [])
        self.assertIn("rdefs.vams", log)
        self.assertIn("it is in %s (CDS_VLOGA_INCLUDE or -I)" % hdr, log)

    def test_the_headers_pyms_finds_or_does_without_pass(self):
        """Spectre's and the standard headers (discipline.h, constants.h, disciplines.vams,
        constants.vams: PyMS skips the ones it lacks and the module runs, measured), a header beside the
        top file reached from a nested include (PyMS's second place), one in PYMS_INCLUDE_PATH, and one
        in a branch of `ifdef that vamos cannot evaluate are not refused; the nested one is copied."""
        self.write("src/sub/mid.vams", '`include "deep.vams"\n')
        self.write("src/deep.vams", "`define RT 2000.0\n")
        pinc = os.path.dirname(self.write("pinc/pyms_only.vams", "// found through PYMS_INCLUDE_PATH\n"))
        os.environ["PYMS_INCLUDE_PATH"] = pinc
        top = ('`include "discipline.h"\n`include "constants.h"\n`include "disciplines.vams"\n'
               '`include "constants.vams"\n`include "sub/mid.vams"\n`include "pyms_only.vams"\n'
               '`ifdef NOT_DEFINED\n`include "nowhere.vams"\n`endif\n'
               '// `include "commented.vams"\n'
               'module vtop(a, b);\n  inout a, b; electrical a, b;\n  parameter real r = `RT;\n'
               '  analog I(a, b) <+ V(a, b) / r;\nendmodule\n')
        self.write("src/top.va", top)
        ch = Chain(write_deck(self.tmp, "deck.scs", INC_DECK.replace("vinc.va", "src/top.va").replace("vinc", "vtop")))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch)
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 0, log)
        self.assertEqual(self.calls(run_dir), ["-norun vamos_a1.cir", "vamos_a1.cir"])
        self.assertEqual(RX.missing_includes([os.path.join(self.tmp, "src", "top.va")]), [])
        for rel in ("top.va", os.path.join("sub", "mid.vams"), "deep.vams"):
            self.assertTrue(os.path.isfile(os.path.join(run_dir, rel)), rel)
        self.assertFalse(os.path.exists(os.path.join(run_dir, "pyms_only.vams")))

    def test_a_skipped_include_in_xyce_s_output_is_refused(self):
        """The include vamos cannot judge (in a branch of `ifdef) that PyMS then skips: its preprocessor's
        warning in the -norun output stops the run; the standard headers' skips do not."""
        va = VINC_VA.replace('`include "rdefs.vams"\n', '`ifdef insideADMS\n`include "rdefs.vams"\n`endif\n')
        ch = self.va_chain("vinc.va", va, INC_DECK)
        out = ("preprocess: warning: 'disciplines.vams' not found (skipped)\n"
               "preprocess: warning: 'rdefs.vams' not found (skipped)\n"
               "build_vae_so: built /c/vae_vinc_1.so (1 params, full)\n")
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"norun vamos_a1.cir": {"out": out}})
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual((er.rc, er.status), (2, {}), log)
        self.assertEqual(self.calls(run_dir), ["-norun vamos_a1.cir"])
        self.assertIn("PyMS did not find the Verilog-A include \"rdefs.vams\"", log)
        self.assertNotIn("\"disciplines.vams\"", log)
        run_dir = os.path.join(self.tmp, "run2")
        self.behave(run_dir, ch, {"norun vamos_a1.cir": {"out": out.replace("'rdefs.vams'", "'discipline.h'")}})
        er, log, run_dir = run_chain(self, ch, "run2")
        self.assertEqual(er.rc, 0, log)

    def test_missing_includes(self):
        a = self.write("lib/a.va", '`include "disciplines.vams"\n`include "here.vams"\n`include "gone.vams"\n'
                                   '`ifdef X\n`include "cond.vams"\n`else\n`include "cond2.vams"\n`endif\n'
                                   '`include "/no/such/abs.vams"\nmodule a(p); endmodule\n')
        self.write("lib/here.vams", '`include "inner_gone.h"\n')
        self.assertEqual(RX.missing_includes([a, a]), [
            (os.path.abspath(a), os.path.abspath(a), "gone.vams"),
            (os.path.abspath(a), os.path.abspath(a), "/no/such/abs.vams"),
            (os.path.abspath(a), os.path.join(self.tmp, "lib", "here.vams"), "inner_gone.h")])
        self.assertEqual(RX.missing_includes([os.path.join(self.tmp, "nope.va")]), [])   # Xyce reports that


# -- signals with the fake Xyce (Linux: a signal interrupts a blocked wait at once) ------------------------

@unittest.skipUnless(LINUX, "signals to the test process and the fake Xyce's process group (Linux/WSL)")
class TestRunFakeSignals(FakeCase):
    """§2.7 and stop.py with the fake Xyce: the stop signals reach Xyce's whole process group (R-run-5),
    a second signal raises stop.Stop before anything is classified (R-run-8), SIGUSR1 during the -norun
    check names it (R-run-11), and SIGUSR2's notice is stop.CHECKPOINT (R-run-14)."""

    SIGS = (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1, signal.SIGUSR2)

    def setUp(self):
        super().setUp()
        for s in self.SIGS:
            if signal.getsignal(s) == signal.SIG_IGN:
                self.skipTest("%s is ignored in this process (a background launch, E81)" % signal.Signals(s).name)
        self.late: List[int] = []
        self.saved = {s: signal.signal(s, lambda signum, frame: self.late.append(signum)) for s in self.SIGS}
        self.marker = "vamos-test-marker-" + uuid.uuid4().hex

    def tearDown(self):
        for s, h in self.saved.items():
            signal.signal(s, h)
        for pid in marker_pids(self.marker):          # a child that outlived its fake Xyce
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        super().tearDown()

    def sender(self, run_dir: str, call: str, sigs: List[int], gap: float = 0.3,
               when=None) -> threading.Thread:
        """Once `call` is in fake.calls (and `when()` holds, when given): each signal of sigs to this
        process, `gap` seconds apart."""
        def send() -> None:
            end = time.time() + 60
            while time.time() < end and (call not in self.calls(run_dir) or (when is not None and not when())):
                time.sleep(0.02)
            time.sleep(0.2)
            for k, s in enumerate(sigs):
                if k:
                    time.sleep(gap)
                os.kill(os.getpid(), s)
        th = threading.Thread(target=send, daemon=True)
        th.start()
        return th

    def test_the_stop_signal_reaches_the_child_of_the_norun_check(self):
        """R-run-5: Xyce's PyMS compile runs in children of Xyce (sh, python3, g++, cc1plus); the
        forwarded SIGTERM reached Xyce's pid only, and the compile went on after vamos ended."""
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"norun vamos_a1.cir": {"spawn": self.marker, "sleep": 60}})
        th = self.sender(run_dir, "-norun vamos_a1.cir", [signal.SIGINT], when=lambda: marker_pids(self.marker))
        er, log, run_dir = run_chain(self, ch)
        th.join(10)
        self.assertEqual((er.rc, er.status), (3, {}), log)
        self.assertTrue(gone(self.marker, 2.0), "the child of the -norun check outlived the run")
        self.assertEqual(self.late, [])

    def test_the_stop_signal_reaches_the_child_of_a_deck(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {"spawn": self.marker, "sleep": 60}})
        th = self.sender(run_dir, "vamos_a2.cir", [signal.SIGINT], when=lambda: marker_pids(self.marker))
        er, log, run_dir = run_chain(self, ch)
        th.join(10)
        self.assertEqual(er.rc, 3, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "interrupted"})
        self.assertTrue(gone(self.marker, 2.0), "the child of the deck outlived the run")
        self.assertEqual(self.late, [])

    def test_a_second_signal_raises_stop_before_the_deck_is_classified(self):
        """R-run-8: the runner said the interrupted analysis's partial results were written, then the
        flow that nothing was converted; now only Stop (the deck's time line still closes its banner)."""
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"vamos_a2.cir": {"ignore_term": True, "sleep": 60}})
        th = self.sender(run_dir, "vamos_a2.cir", [signal.SIGINT, signal.SIGINT])
        lf = LogFile(self.tmp, profile=True)
        try:
            with self.assertRaises(stop.Stop) as cm:
                RX.run(ch.nl2, ch.plan, ch.sigmap, run_dir, lf.job, lf.log)
        finally:
            th.join(10)
            log = lf.text()
        self.assertEqual(cm.exception.signum, signal.SIGINT)
        self.assertNotIn("partial results", log)
        self.assertNotIn("was interrupted", log)
        self.assertIn("Total time required for ac analysis `myac' was ", log)
        self.assertEqual(self.late, [])

    def test_sigusr1_during_the_norun_check_names_it(self):
        """R-run-11: the status line said `Xyce not running' while the -norun check ran (on a cold PyMS
        cache it compiles every Verilog-A module, about 15 s for e2e 8)."""
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        run_dir = os.path.join(self.tmp, "run")
        self.behave(run_dir, ch, {"norun vamos_a1.cir": {"sleep": 1.5}})
        th = self.sender(run_dir, "-norun vamos_a1.cir", [signal.SIGUSR1])
        er, log, run_dir = run_chain(self, ch)
        th.join(10)
        self.assertEqual(er.rc, 0, log)
        self.assertIn("spectre status: no analysis running, Xyce checks the netlist (-norun on vamos_a1.cir), "
                      "elapsed = ", log)
        self.assertNotIn("Xyce not running", log)

    def test_sigusr2_prints_the_one_checkpoint_notice(self):
        """R-run-14: SIGUSR2's notice is stop.CHECKPOINT, the text the flow and run_vacask print too."""
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        for text in (None, "a sentinel for stop.CHECKPOINT"):
            with self.subTest(text=text):
                name = "run" if text is None else "run_patched"
                run_dir = os.path.join(self.tmp, name)
                self.behave(run_dir, ch, {"vamos_a2.cir": {"sleep": 1.5}})
                th = self.sender(run_dir, "vamos_a2.cir", [signal.SIGUSR2])
                if text is None:
                    er, log, run_dir = run_chain(self, ch, name)
                    want = "checkpoint not supported: SIGUSR2 is ignored and the run continues"
                    self.assertEqual(stop.CHECKPOINT, want)
                else:
                    with mock.patch.object(stop, "CHECKPOINT", text):
                        er, log, run_dir = run_chain(self, ch, name)
                    want = text
                th.join(10)
                self.assertEqual(er.rc, 0, log)
                self.assertIn("Notice from spectre during AC analysis `myac'.\n    %s\n" % want, log)
                self.assertEqual(log.count(want), 1, log)


class TestProgress(FakeCase):
    """R-run-11, both legs: the SIGUSR1 text while the -norun check runs."""

    def test_progress_names_the_norun_check(self):
        ch = Chain(os.path.join(E2E, "01_rc", "deck.scs"))
        lf = LogFile(self.tmp)
        r = RX._Runner(ch.nl2, ch.plan, ch.sigmap, os.path.join(self.tmp, "run"), lf.job, lf.log)
        r.decks, _names = RX.render_decks(ch.nl2, ch.plan, ch.sigmap)
        self.assertEqual(r.progress(), "Xyce not running")
        r.checking = r.decks[0]
        self.assertEqual(r.progress(), "Xyce checks the netlist (-norun on vamos_a1.cir)")
        r.checking, r.running = None, r.decks[1]
        self.assertEqual(r.progress(), "Xyce deck vamos_a2.cir (2 of 3)")
        lf.text()


# -- WSL: the real Xyce --------------------------------------------------------------------------------

def _close(got, want, rel: float, abs_: float) -> bool:
    if isinstance(want, (list, tuple)):
        g = complex(got)
        return _close(g.real, want[0], rel, abs_) and _close(g.imag, want[1], rel, abs_)
    got = got.real if isinstance(got, complex) else got
    return abs(got - want) <= abs_ + rel * abs(want)


def _plots(path: str) -> List[rawfile.Raw]:
    return rawfile.read_prn(path) if path.endswith(".prn") else rawfile.read_all(path)


def _value(plot: rawfile.Raw, col: str, chk: dict):
    k = plot.exact(col)
    rows = plot.points
    if "row" in chk:
        return rows[chk["row"]][k]
    x = chk["at"]
    a = next(i for i, (name, _t) in enumerate(plot.variables) if name.lower() in ("time", "frequency", "freq"))
    xs = [(p[a].real if isinstance(p[a], complex) else p[a]) for p in rows]
    for i in range(1, len(rows)):
        if xs[i - 1] <= x <= xs[i]:
            y0, y1 = rows[i - 1][k], rows[i][k]
            return y1 if xs[i] == xs[i - 1] else y0 + (y1 - y0) * (x - xs[i - 1]) / (xs[i] - xs[i - 1])
    if rows and abs(xs[-1] - x) <= 1e-9 * max(abs(x), 1e-30):
        return rows[-1][k]
    raise AssertionError("%s: x=%s outside the plot" % (plot.path, x))


class PymsCache(TempDir):
    """One PyMS cache for the whole class (a Verilog-A module compiles once, ~15 s)."""
    cache = ""

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.cache = tempfile.mkdtemp(prefix="vamos-test-pyms-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.cache, ignore_errors=True)

    def setUp(self):
        super().setUp()
        os.environ["PYMS_CACHE"] = self.cache
        os.environ.pop("VAMOS_VERBOSE", None)


@needs_xyce
class TestRunE2E(PymsCache):
    """e2e 1-10 on Xyce through run(), against engine_expect.json (§11 T2 1-10)."""

    def check_case(self, case: str) -> None:
        exp_all = load_json(os.path.join(E2E, case, "engine_expect.json"))
        entries = [("deck.scs", exp_all.get("xyce"))] + [(a["deck"], a.get("xyce")) for a in exp_all.get("also", [])]
        for deck, exp in entries:
            if exp is None:
                continue
            with self.subTest(deck=deck):
                path = os.path.join(E2E, case, deck)
                if exp.get("status") == 2 and "refused" in exp:
                    with self.assertRaises(NoteError) as cm:
                        Chain(path)
                    msgs = [n.message for n in cm.exception.notes if n.severity == ERROR]
                    self.assertTrue(any(exp["refused"] in m for m in msgs), msgs)
                    continue
                ch = Chain(path)
                er, log, run_dir = run_chain(self, ch, "run_" + os.path.splitext(deck)[0])
                self.assertEqual(er.rc, exp["status"], log + "\n".join(er.log[-20:]))
                self.check(ch, er, exp)
                from vamos.spectre import results                         # what results.collect reads
                notes: list = []
                res = list(results.collect(ch.plan, ch.sigmap, er, "modern", notes=notes))
                self.assertTrue(res)
                self.assertEqual([r.key for r in res if r.status != "ok"], [])
                self.assertEqual([n.message for n in notes if n.severity == "warning"], [])
                for k, d in enumerate(ch.steps):            # the decks Xyce ran are the chain's goldens
                    gold = os.path.join(E2E, case, "render", "xyce", d.id + ".cir")
                    if deck == "deck.scs" and os.path.exists(gold):
                        with open(gold) as g, open(os.path.join(run_dir, d.id + ".cir")) as fh:
                            self.assertEqual(fh.read(), g.read(), d.id)

    def check(self, ch: Chain, er: EngineResult, exp: dict) -> None:
        plots: Dict[str, List[rawfile.Raw]] = {}
        for sid, se in exp.get("steps", {}).items():
            self.assertEqual(er.status.get(sid), "ok", sid)
            self.assertTrue(os.path.isfile(er.files.get(sid, "")), sid)
            plots[sid] = _plots(er.files[sid])
            for sname, col in se.get("columns", {}).items():
                self.assertEqual(ch.column(er.names, sid, sname), col)
                plots[sid][0].exact(col)
        for chk in exp.get("checks", []):
            label = json.dumps(chk)
            sid = chk["step"]
            if "plots" in chk:
                self.assertEqual(len(plots[sid]), chk["plots"], label)
                continue
            plot = plots[sid][chk.get("plot", 0)]
            if "points" in chk:
                self.assertEqual(len(plot.points), chk["points"], label)
                continue
            rel, abs_ = chk.get("rel", 1e-6), chk.get("abs", 0.0)
            if "equal" in chk:
                vals = [_value(plot, ch.column(er.names, sid, s), chk) for s in chk["equal"]]
                for v in vals[1:]:
                    self.assertTrue(_close(v, vals[0].real if isinstance(vals[0], complex) else vals[0], rel, abs_),
                                    "%s: %s" % (label, vals))
                continue
            col = chk["col"] if "col" in chk else ch.column(er.names, sid, chk["signal"])
            got = _value(plot, col, chk)
            self.assertTrue(_close(got, chk["value"], rel, abs_), "%s: got %r" % (label, got))


def _e2e_test(case):
    def test(self):
        self.check_case(case)
    test.__name__ = "test_" + case
    test.__doc__ = "e2e %s on Xyce through run_xyce.run (§11 T2 %d)" % (case, int(case[:2]))
    return test


for _case in CASES:
    setattr(TestRunE2E, "test_" + _case, _e2e_test(_case))


NANC_VA = """`include "disciplines.vams"
// test_spectre_run_xyce: a source that goes NaN once node c passes 0.5 V (PyMS has no $abstime), so a
// tran fails part-way where the RC node crosses (E66's pattern on Xyce), vamos-authored
module nanc(a, c);
  inout a, c; electrical a, c;
  analog V(a) <+ (V(c) > 0.5) ? sqrt(-1.0 - V(a)*0) : 1.0;
endmodule
"""

NAN_HEAD = """// a Verilog-A NaN past the RC node's threshold: the tran fails at 0.69 us for r = 1k
simulator lang=spectre
ahdl_include "nanc.va"
parameters rval=1k
Vs (in 0) vsource type=pulse val0=0 val1=1 delay=0 rise=1n fall=1n width=1 period=2
R1 (in c) resistor r=rval
C1 (c 0) capacitor c=1n
N1 (a c) nanc
R2 (a 0) resistor r=1k
save a c
"""


@needs_xyce
class TestRunXyceFailures(PymsCache):
    """§7.2's failure rules on the real Xyce."""

    def test_a_singular_op_is_a_failed_analysis(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", """// two parallel sources: a singular operating point
simulator lang=spectre
V1 (a 0) vsource dc=1
V2 (a 0) vsource dc=2
R1 (a 0) resistor r=1k
op1 dc
"""))
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 1, log)
        self.assertEqual(er.status, {"vamos_a1": "failed"})
        self.assertNotIn("vamos_a1", er.files)                            # Xyce writes no print file [E13]
        self.assertIn("analysis `op1' failed in Xyce (exit status 1", log)

    @needs_pyms
    def test_a_tran_failing_part_way_among_good_analyses(self):
        write_deck(self.tmp, "nanc.va", NANC_VA)
        ch = Chain(write_deck(self.tmp, "deck.scs", NAN_HEAD + "op1 dc\ntr1 tran stop=3u\nop2 dc\n"))
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 1, log + "\n".join(er.log))
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed", "vamos_a3": "ok"})
        plots = rawfile.scan(er.files["vamos_a2"])
        self.assertEqual([(p.declared, p.n) for p in plots], [(None, 0)])  # header only [E66]
        self.assertIn("analysis `tr1' failed in Xyce (exit status 1", log)
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "nanc.va")))   # beside the decks [E104]
        with open(os.path.join(run_dir, "vamos_a1.cir")) as fh:
            self.assertIn('.hdl "nanc.va"', fh.read())
        op = rawfile.read(er.files["vamos_a3"])
        self.assertAlmostEqual(op.points[0][op.exact("V(A)")], 1.0, delta=0.01)    # PyMS: 1.001 (measured)

    @needs_pyms
    def test_a_step_point_failing_part_way(self):
        write_deck(self.tmp, "nanc.va", NANC_VA)
        ch = Chain(write_deck(self.tmp, "deck.scs", NAN_HEAD + "swp sweep param=rval values=[10k 1k 20k] {\n"
                              "  tr1 tran stop=3u\n}\nop2 dc\n"))
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 1, log + "\n".join(er.log))
        self.assertEqual(er.status, {"vamos_a1": "failed", "vamos_a2": "ok"})
        self.assertEqual(er.failed_points, {"vamos_a1": 2})               # Xyce went on and exited 0
        self.assertIn("restarts its time axis", log)
        self.assertEqual(len(rawfile.scan(er.files["vamos_a1"])), 2)
        from vamos.spectre import results                                 # the leaves before it are written
        status = {r.key: r.status for r in results.collect(ch.plan, ch.sigmap, er, "modern", notes=[])}
        self.assertEqual(status["swp-000_tr1-tran"], "ok")
        self.assertEqual(status["swp-001_tr1-tran"], "failed")
        self.assertEqual(status["swp-002_tr1-tran"], "failed")
        self.assertEqual(status["op2-dc"], "ok")

    def test_an_alter_of_a_parameter_xyce_does_not_know(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", ALTER_TLEV))
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 2, log + "\n".join(er.log))
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed"})
        self.assertIn("alter `a1' sets a parameter Xyce does not know", log)
        self.assertIn("TLEV", log)

    def test_a_sweep_of_a_parameter_xyce_does_not_know(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", SWEEP_TLEV))
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 2, log + "\n".join(er.log))
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "failed"})
        self.assertIn("sweep `swp' sets a parameter Xyce does not know", log)

    @needs_pyms
    def test_a_module_pyms_cannot_build_is_status_2(self):
        """R-run-3 on the real Xyce: PyMS's evaluator has no $abstime, so both builds fail; the -norun
        check's output names it and no analysis runs (the module ran as a device that contributes
        nothing: out = 0 at 1 ms where VACASK gives 1 V, exit 0, three warnings, even under
        --vamos-strict)."""
        write_deck(self.tmp, "vramp.va", VRAMP_VA)
        ch = Chain(write_deck(self.tmp, "deck.scs", RAMP_DECK))
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual((er.rc, er.status), (2, {}), log + "\n".join(er.log))
        self.assertIn("Error from spectre during initial setup.", log)
        self.assertIn("PyMS could not build Verilog-A module vramp", log)
        self.assertIn(os.path.join(self.tmp, "vramp.va"), log)
        self.assertIn("$abstime", log)
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "vamos_a1.norun.log")))
        self.assertFalse(os.path.exists(os.path.join(run_dir, "vamos_a1.log")))      # no deck ran

    @needs_pyms
    def test_a_missing_include_is_refused_on_a_cold_and_a_warm_cache(self):
        """R-run-3 on the real Xyce: PyMS built vinc.va without its missing header (b = 0 V, exit 0, no
        warning), and once its evaluator library is cached it prints nothing about the header at
        all; vamos refuses the module before Xyce starts, whatever the cache holds."""
        cache = os.path.join(self.tmp, "vae")
        os.environ["PYMS_VAE_CACHE"] = cache
        write_deck(self.tmp, "vinc.va", VINC_VA)
        ch = Chain(write_deck(self.tmp, "deck.scs", INC_DECK))
        er, log, run_dir = run_chain(self, ch, "cold")
        self.assertEqual((er.rc, er.status), (2, {}), log)
        self.assertIn("`include \"rdefs.vams\"", log)
        self.assertEqual([f for f in os.listdir(run_dir) if f.endswith(".log")], [])     # Xyce never started
        warm = os.path.join(self.tmp, "warm")                 # the cache a run without the check leaves
        os.makedirs(warm)
        decks, _names = RX.render_decks(ch.nl2, ch.plan, ch.sigmap, RX.place_va(ch.nl2.hdl, warm))
        write_deck(warm, decks[0].file, decks[0].text)
        p = subprocess.run([RX.xyce_exe(), decks[0].file], cwd=warm, env=RX.engines_env(),
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, errors="replace", timeout=900)
        self.assertEqual(p.returncode, 0, p.stdout[-3000:])
        self.assertIn("'rdefs.vams' not found (skipped)", p.stdout)
        self.assertTrue(glob.glob(os.path.join(cache, "vae_vinc_*.so")), os.listdir(cache))
        er, log, run_dir = run_chain(self, ch, "warm_run")
        self.assertEqual((er.rc, er.status), (2, {}), log)
        self.assertIn("`include \"rdefs.vams\"", log)
        self.assertEqual([f for f in os.listdir(run_dir) if f.endswith(".log")], [])

    @needs_pyms
    def test_the_headers_pyms_does_without_still_run(self):
        """R-run-3's refusal spares the headers PyMS skips and does without: a module that includes
        Spectre's discipline.h and constants.h (ref19's examples, pp.452 and 541) runs, b = 1/3 V."""
        write_deck(self.tmp, "hconst.va", HCONST_VA)
        ch = Chain(write_deck(self.tmp, "deck.scs", HCONST_DECK))
        er, log, _ = run_chain(self, ch)
        self.assertEqual(er.rc, 0, log + "\n".join(er.log))
        op = rawfile.read(er.files["vamos_a1"])
        self.assertAlmostEqual(op.points[0][op.exact("V(B)")], 1.0 / 3.0, delta=1e-6)

    @needs_pyms
    def test_two_modules_of_one_library_keep_their_parameters(self):
        """F4 (phase 2c2): vares and vres of one lib/ sharing ../inc; place_va put both in run_dir/lib/, where PyMS
        built vres with vares's attributes and both lost their parameters: "Unrecognized parameter R for device
        RX1", "Unrecognized parameter A for device YVRES!X2", status 2 for a valid deck.  Now vres sits in its own
        vamos_va1/ and the deck gives VACASK's values, b = 0.25 (R=3k) and c = 0.2 (the include's r = 4k)."""
        write_deck(os.path.join(self.tmp, "lib"), "vares.va", VARES_VA)
        write_deck(os.path.join(self.tmp, "lib"), "vres.va", VRES_INC_VA)
        write_deck(os.path.join(self.tmp, "inc"), "rdefs.include", RDEFS)
        ch = Chain(write_deck(self.tmp, "deck.scs", TWOLIB_DECK))
        er, log, run_dir = run_chain(self, ch)
        self.assertEqual(er.rc, 0, log + "\n".join(er.log))
        op = rawfile.read(er.files["vamos_a1"])
        self.assertAlmostEqual(op.points[0][op.exact("V(B)")], 0.25, delta=1e-6)
        self.assertAlmostEqual(op.points[0][op.exact("V(C)")], 0.2, delta=1e-6)
        with open(os.path.join(run_dir, "vamos_a1.cir")) as fh:
            text = fh.read()
        self.assertIn('.hdl "lib/vares.va"\n.hdl "vamos_va1/lib/vres.va"\n', text)
        self.assertNotIn("Unrecognized parameter", log)


LONG = """// a long tran for the signal tests (E72's circuit)
simulator lang=spectre
V1 (in 0) vsource type=sine ampl=1 freq=1k
R1 (in out) resistor r=1k
C1 (out 0) capacitor c=1u
op1 dc
tr1 tran stop=%s step=1e-7 maxstep=1e-7
op2 dc
save out
"""


class Sender(threading.Thread):
    """Sends signals to this process once the second deck runs (its log names `trigger`; None:
    its log exists, which run_xyce creates just before Xyce starts), `delay` seconds later, then
    waits for the run to end; a run that outlives `limit` seconds after the signals gets its Xyce
    killed (reap).  Xyce's output to its log is block-buffered: a trigger line shows up when a
    buffer fills (a tran's progress lines fill it at once; a noise analysis prints none)."""

    def __init__(self, run_dir: str, sigs: List[int], limit: float = 90.0, gap: float = 0.0,
                 trigger: Optional[str] = "Beginning Transient Calculation", delay: float = 0.3) -> None:
        super().__init__(daemon=True)
        self.run_dir, self.sigs, self.limit, self.gap = run_dir, sigs, limit, gap
        self.trigger, self.delay = trigger, delay
        self.done = threading.Event()
        self.sent = False

    def run(self) -> None:
        log = os.path.join(self.run_dir, "vamos_a2.log")
        t0 = time.time()
        while time.time() - t0 < 120:
            try:
                with open(log) as fh:
                    if self.trigger is None or self.trigger in fh.read():
                        break
            except OSError:
                pass
            time.sleep(0.02)
        else:
            reap(self.run_dir)
            return
        time.sleep(self.delay)
        for s in self.sigs:
            os.kill(os.getpid(), s)
            if self.gap:
                time.sleep(self.gap)
        self.sent = True
        if not self.done.wait(self.limit):
            reap(self.run_dir)


@needs_xyce
class TestRunXyceSignals(PymsCache):
    """§2.7 and stop.py on the real Xyce: the first signal is passed on as SIGTERM, the run is
    converted as status 3; a second one raises stop.Stop; SIGUSR1/SIGUSR2 only report."""

    SIGS = (signal.SIGINT, signal.SIGTERM, signal.SIGUSR1, signal.SIGUSR2)

    def setUp(self):
        super().setUp()
        for s in self.SIGS:
            if signal.getsignal(s) == signal.SIG_IGN:
                self.skipTest("%s is ignored in this process (a background launch, E81)" % signal.Signals(s).name)
        # a signal that came after run() returned must not end the test process: a recording
        # handler outside run() (proc.Interrupts installs its own during the run and restores this)
        self.late: List[int] = []
        self.saved = {s: signal.signal(s, lambda signum, frame: self.late.append(signum)) for s in self.SIGS}

    def tearDown(self):
        for s, h in self.saved.items():
            signal.signal(s, h)
        super().tearDown()

    def interrupted(self, sig: int) -> None:
        ch = Chain(write_deck(self.tmp, "deck.scs", LONG % "0.5"))
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        snd = Sender(run_dir, [sig])
        snd.start()
        try:
            er, log, _ = run_chain(self, ch)
        finally:
            snd.done.set()
        self.assertTrue(snd.sent)
        self.assertEqual(er.rc, 3, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "interrupted"})   # op2 never ran
        plots = rawfile.scan(er.files["vamos_a2"])
        self.assertEqual(len(plots), 1)
        self.assertIsNone(plots[0].declared)                              # readable after a signal [E72]
        self.assertGreater(plots[0].n, 10)
        self.assertLess(plots[0].last, 0.5)
        self.assertEqual(len(rawfile.read(er.files["vamos_a2"]).points), plots[0].n)
        self.assertIn("analysis `tr1' was interrupted at time = ", log)
        self.assertIn(" (%s); its partial results (vamos_a2.raw) are written" % signal.Signals(sig).name, log)
        self.assertNotIn("vamos_a3.cir", "\n".join(er.log))
        self.assertEqual(self.late, [])
        from vamos.spectre import results                                 # the partial tran converts
        res = {r.key: r for r in results.collect(ch.plan, ch.sigmap, er, "modern", notes=[])}
        self.assertEqual(res["tr1-tran"].status, "interrupted")
        self.assertEqual(sum(1 for _ in res["tr1-tran"].rows()), plots[0].n)
        self.assertEqual(res["op1-dc"].status, "ok")

    def test_sigterm(self):
        self.interrupted(signal.SIGTERM)

    def test_sigint(self):
        self.interrupted(signal.SIGINT)

    def test_an_interrupted_noise_prn_is_converted(self):
        """§14 q.26, measured 2026-10-16: after SIGTERM Xyce's noise .prn holds complete rows (each
        line written whole, no `End of Xyce' line); a cut last line would be dropped."""
        ch = Chain(write_deck(self.tmp, "deck.scs", """// a long noise analysis (2.7e6 points, ~70 s)
simulator lang=spectre
V1 (in 0) vsource dc=0
R1 (in out) resistor r=1k
R2 (out 0) resistor r=1k
C1 (out 0) capacitor c=1n
op1 dc
nz1 (out 0) noise iprobe=V1 start=1 stop=1G dec=300000
op2 dc
"""))
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        snd = Sender(run_dir, [signal.SIGTERM], trigger=None, delay=4.0)
        snd.start()
        try:
            er, log, _ = run_chain(self, ch)
        finally:
            snd.done.set()
        self.assertTrue(snd.sent)
        self.assertEqual(er.rc, 3, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "interrupted"})
        with open(er.files["vamos_a2"], "rb") as fh:
            data = fh.read()
        self.assertTrue(data.endswith(b"\n"))
        self.assertNotIn(b"End of Xyce", data)
        tables = rawfile.read_prn(er.files["vamos_a2"])
        self.assertEqual(len(tables), 1)
        self.assertGreater(len(tables[0].points), 100)
        self.assertLess(len(tables[0].points), 2700001)
        from vamos.spectre import results
        notes: list = []
        res = {r.key: r for r in results.collect(ch.plan, ch.sigmap, er, "modern", notes=notes)}
        self.assertEqual(res["nz1-noise"].status, "interrupted")
        self.assertEqual(sum(1 for _ in res["nz1-noise"].rows()), len(tables[0].points))
        self.assertEqual(res["op1-dc"].status, "ok")
        self.assertEqual(self.late, [])

    def test_a_second_signal_raises_stop(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", LONG % "0.5"))
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        snd = Sender(run_dir, [signal.SIGINT, signal.SIGTERM])
        snd.start()
        lf = LogFile(self.tmp)
        before = signal.getsignal(signal.SIGTERM)
        try:
            with self.assertRaises(stop.Stop) as cm:
                RX.run(ch.nl2, ch.plan, ch.sigmap, run_dir, lf.job, lf.log)
        finally:
            snd.done.set()
            lf.text()
        self.assertTrue(snd.sent)
        self.assertEqual(cm.exception.signum, signal.SIGTERM)
        self.assertIs(signal.getsignal(signal.SIGTERM), before)          # the handlers are restored
        self.assertEqual(self.late, [])
        self.assertEqual(reap(run_dir), [])                                # Xyce was killed

    def test_sigusr1_and_sigusr2_report_and_the_run_goes_on(self):
        ch = Chain(write_deck(self.tmp, "deck.scs", LONG % "0.03"))
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        snd = Sender(run_dir, [signal.SIGUSR1, signal.SIGUSR2], gap=0.3)
        snd.start()
        try:
            er, log, _ = run_chain(self, ch)
        finally:
            snd.done.set()
        self.assertTrue(snd.sent)
        self.assertEqual(er.rc, 0, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "ok", "vamos_a3": "ok"})
        self.assertIn("spectre status: analysis `tr1' running, Xyce deck vamos_a2.cir (2 of 3)", log)
        self.assertIn("Notice from spectre during transient analysis `tr1'.\n    %s\n" % stop.CHECKPOINT, log)
        self.assertEqual(self.late, [])

    def test_the_grace_kill_on_the_real_engine(self):
        """R-tests-13: the grace-period kill on the real Xyce, not a stand-in.  Xyce ends at once on the
        forwarded SIGTERM [E81], so it is stopped (SIGSTOP) first, which keeps that SIGTERM pending: 0.5 s
        after the SIGINT, Xyce's process group gets SIGKILL and the run is converted as status 3, its
        warning naming the kill."""
        from vamos import proc
        ch = Chain(write_deck(self.tmp, "deck.scs", LONG % "0.5"))
        run_dir = os.path.join(self.tmp, "run")
        os.makedirs(run_dir)
        sent: List[float] = []

        def send() -> None:
            log = os.path.join(run_dir, "vamos_a2.log")
            end = time.time() + 120
            while time.time() < end:
                try:
                    with open(log) as fh:
                        if "Beginning Transient Calculation" in fh.read():
                            break
                except OSError:
                    pass
                time.sleep(0.02)
            time.sleep(0.3)
            pids = procs_under(run_dir)
            for pid in pids:
                os.kill(pid, signal.SIGSTOP)
            end = time.time() + 10
            while time.time() < end and not all(_proc_state(pid) in ("T", "t", "") for pid in pids):
                time.sleep(0.01)
            sent.append(time.time())
            os.kill(os.getpid(), signal.SIGINT)
        th = threading.Thread(target=send, daemon=True)
        grace, proc.INTERRUPT_GRACE = proc.INTERRUPT_GRACE, 0.5
        th.start()
        try:
            er, log, _ = run_chain(self, ch)
            ended = time.time()
        finally:
            proc.INTERRUPT_GRACE = grace
            th.join(30)
        self.assertTrue(sent, "the SIGINT was sent")
        self.assertEqual(er.rc, 3, log)
        self.assertEqual(er.status, {"vamos_a1": "ok", "vamos_a2": "interrupted"})
        self.assertIn("analysis `tr1' was interrupted", log)
        self.assertIn("(SIGINT; Xyce did not stop within 0.5 s and was killed); its partial results (vamos_a2.raw) "
                      "are written", log)
        self.assertGreater(ended - sent[0], 0.4)
        self.assertLess(ended - sent[0], 2.5)
        self.assertTrue(any("vamos_a2.cir -> exit -9" in line for line in er.log), er.log)
        self.assertEqual(procs_under(run_dir), [])
        self.assertEqual(self.late, [])


def _proc_state(pid: int) -> str:
    """A process's state letter from /proc ("" when it is gone)."""
    try:
        with open("/proc/%d/stat" % pid) as fh:
            return fh.read().rsplit(")", 1)[-1].split()[0]
    except (OSError, IndexError):
        return ""


if __name__ == "__main__":
    unittest.main()
