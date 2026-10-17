"""The spectre personality's outputs (docs/VAMOS_SPECTRE_DESIGN.md §8, §11.1 "Outputs"; phase 1, S4):
output/psf.py, output/nutmeg.py, output/statefile.py, spectre/results.collect and
rawfile.read_prn, on both legs (Cygwin Python 3.9, WSL Python 3.14), no engine.

Every PSF file written here is checked by tests/vamos/psfcheck.py, the independent oracle of §8,
and (WSL, Python >= 3.10) parsed by the vendored psf-parser.  The engine outputs the reader is
exercised on are fixtures/netlist/raw_* (the rawfile goldens) and fixtures/spectre/psf/golden/engine/*
(VACASK and Xyce outputs of small divider decks, the README there); synthetic rawfiles are written
where a layout is needed that no fixture has.  No IR is passed to collect: the plan's SweepLevels
and the SignalMap are built by hand (§10).

    python3 -m unittest test_spectre_outputs
    python3 -c "import test_spectre_outputs as t; t.write_goldens(t.GOLDEN_DIR)"   # regenerate psf/golden

The goldens under fixtures/spectre/psf/golden/<case>/ are this module's own output on a fixed
date, pinned byte for byte; `rc/` rebuilds psf-parser's 23.1 run (e2e 28's structure, §11.2) from
synthetic engine output, and `compare_results_dir` holds e2e 28's structural assertions for the
phase-2 integrator.
"""

import datetime
import locale
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from typing import Dict, List, Optional, Sequence, Tuple

from vamos_testlib import ROOT, TempDir, fixture
from spectre_testlib import (RC_OMIT_KEYS, compare_results_dir, import_psf_parser,
                             needs_psf_parser, spectre_fixture)

import psfcheck
from vamos.netlist import rawfile  # noqa: E402
from vamos.netlist import plan as P  # noqa: E402
from vamos.netlist import signals as S  # noqa: E402
from vamos.netlist.expr_ast import Num  # noqa: E402
from vamos.netlist.ir import SaveSpec, SweepSpec  # noqa: E402
from vamos.output import nutmeg, psf, statefile  # noqa: E402
from vamos.spectre import results  # noqa: E402
from vamos.spectre.results import AnalysisResult, EngineResult, Signal  # noqa: E402

REAL_DIR = spectre_fixture("psf", "real")
ASCII_23 = os.path.join(REAL_DIR, "psf-parser", "tests", "data", "ascii")
LOG_23 = os.path.join(REAL_DIR, "psf-parser", "tests", "data", "binary", "logFile")
NOIVA = os.path.join(REAL_DIR, "psf_utils", "samples", "pnoise.raw", "noiva.noise")
GOLDEN_DIR = spectre_fixture("psf", "golden")
ENGINE_DIR = spectre_fixture("psf", "golden", "engine")
NETLIST_FX = fixture("netlist")

# the fixed header of every golden: a date in §8.2's layout, vamos's version field, the 23.1 design
HEAD = psf.PsfHead(version="vamos (psf/golden fixture)", date="5:46:18 PM, Tue Apr 15, 2025",
                   design="#RC Low-Pass Filter")
STAMP = "Tue Apr 15 17:51:25 2025"
OPTS: Dict[str, object] = {"reltol": 1e-3, "vabstol": 1e-6, "iabstol": 1e-12, "temp": 27.0, "tnom": 27.0,
                           "gmin": 1e-12, "tempeffects": "all"}
TRAN_ARGS = {"start": 0.0, "stop": 0.5, "step": 5e-4, "maxstep": 0.01, "errpreset": "moderate",
             "method": "traponly", "lteratio": 3.5, "relref": "sigglobal", "reltol": 1e-3, "ic": "all",
             "useprevic": "no", "skipdc": "no", "cmin": 0.0}


# -- builders: plan pieces, signal maps, synthetic rawfiles ---------------------------------------

def num(v):
    return Num(float(v))


def spec_values(*vals) -> SweepSpec:
    return SweepSpec("dev", "", "", "values", values=[num(v) for v in vals])


def spec_lin(start, stop, n) -> SweepSpec:
    return SweepSpec("dev", "", "", "lin", start=num(start), stop=num(stop), count=n)


def spec_step(start, stop, step) -> SweepSpec:
    return SweepSpec("dev", "", "", "step", start=num(start), stop=num(stop), step=num(step))


def spec_dec(start, stop, n) -> SweepSpec:
    return SweepSpec("freq", "", "", "dec", start=num(start), stop=num(stop), count=n)


def spec_log(start, stop, n) -> SweepSpec:
    """A log=N sweep as plan.build expands it: the N+1 values filled in, the mode kept (grid 3)."""
    vals = [start * (stop / start) ** (k / float(n)) for k in range(n)] + [float(stop)]
    return SweepSpec("freq", "", "", "log", start=num(start), stop=num(stop), count=n, values=[num(v) for v in vals])


def level(name, id_, spec, label, desc, units, target=("instance", "r1", "r"), continuation=0) -> P.SweepLevel:
    return P.SweepLevel(name, id_, target, spec, continuation, label, desc, units)


def freq_level(spec) -> P.SweepLevel:
    return P.SweepLevel("", "", ("freq", "", ""), spec, 1, "freq", "freq", "Hz")


def step(id_, name, kind, context=(), own=None, args=None, options=None, saves=None, noise_input=None):
    return P.AnalysisStep(id_, name, kind, list(context), own, dict(args or {}), dict(options or OPTS), saves,
                          noise_input=noise_input)


def make_plan(steps, engine="vacask", options=None) -> P.RunPlan:
    return P.RunPlan(actions=[P.Action("analysis", {}, st) for st in steps], options=dict(options or OPTS),
                     engine=engine)


def sigmap(entries: Dict[str, List[Tuple[tuple, str, str]]]) -> S.SignalMap:
    return S.SignalMap(dict(entries))


def write_raw(path: str, variables: Sequence[Tuple[str, str]], rows: Sequence[Sequence[object]],
              cplx: bool = False, binary: bool = True, plotname: str = "Transient Analysis",
              title: str = "synthetic", blank_count: bool = False) -> str:
    """A VACASK/Xyce-style rawfile with the given variables and rows (one plot; a second call with
    append... is not needed: Xyce's multi-plot files are fixtures)."""
    with open(path, "wb") as fh:
        count = ("%d" % len(rows)).ljust(18) if not blank_count else " " * 18
        fh.write(("Title: %s\nDate: now\nPlotname: %s\nFlags: %s\nNo. Variables: %d\nNo. Points: %s\nVariables:\n"
                  % (title, plotname, "complex" if cplx else "real", len(variables), count)).encode("latin-1"))
        for k, (n, t) in enumerate(variables):
            fh.write(("\t%d\t%s\t%s\n" % (k, n, t)).encode("latin-1"))
        if binary:
            fh.write(b"Binary:\n")
            for row in rows:
                if cplx:
                    flat = []
                    for v in row:
                        c = complex(v)
                        flat.extend((c.real, c.imag))
                    fh.write(struct.pack("<%dd" % len(flat), *flat))
                else:
                    fh.write(struct.pack("<%dd" % len(row), *[float(v) for v in row]))
        else:
            fh.write(b"Values:\n")
            for i, row in enumerate(rows):
                parts = ["%d" % i]
                for v in row:
                    c = complex(v)
                    parts.append("\t%.17g,%.17g" % (c.real, c.imag) if cplx else "\t%.17g" % float(v))
                fh.write(("\n".join(parts) + "\n").encode("latin-1"))
    return path


def append_raw_plot(path: str, variables, rows, cplx=False, plotname="Transient Analysis") -> None:
    """Append a plot to a binary rawfile (Xyce's one plot per .STEP point, §7.2)."""
    tmp = path + ".plot"
    write_raw(tmp, variables, rows, cplx, True, plotname)
    with open(tmp, "rb") as src, open(path, "ab") as dst:
        dst.write(src.read())
    os.remove(tmp)


def convert(d: str, pl: P.RunPlan, sm: S.SignalMap, er: EngineResult, naming: str = "modern",
            masters=None, head: psf.PsfHead = HEAD, escchars: bool = False, write_log: bool = True
            ) -> Tuple[List[AnalysisResult], list]:
    """collect + write_analysis for every result + write_logfile into d; (results, notes)."""
    notes: list = []
    res = list(results.collect(pl, sm, er, naming, masters=masters, notes=notes))
    for r in res:
        psf.write_analysis(d, r, head, escchars=escchars, notes=notes, masters=masters)
    if write_log:
        psf.write_logfile(d, res, head, escchars=escchars, stamp=STAMP)
    return res, notes


def check_dir(d: str, escchars: bool = False) -> psfcheck.LogFile:
    """psfcheck on every data file and the logFile of a results directory."""
    for f in sorted(os.listdir(d)):
        if f != "logFile" and not f.endswith((".raw", ".prn")):
            psfcheck.check(os.path.join(d, f), escchars=escchars)
    return psfcheck.check_logfile(os.path.join(d, "logFile"), escchars=escchars)


def types_of(path: str) -> List[str]:
    """The verbatim TYPE entries of a PSF ASCII file, each as its text."""
    with open(path, encoding="latin-1") as fh:
        text = fh.read()
    body = text.split("\nTYPE\n", 1)[1]
    ends = [i for i in (body.find("\nSWEEP\n"), body.find("\nTRACE\n"), body.find("\nVALUE\n"), body.find("\nEND"))
            if i >= 0]
    body = body[:min(ends) + 1]
    entries, cur, depth = [], [], 0
    for line in body.splitlines(True):
        if depth == 0 and line.startswith('"') and cur:
            entries.append("".join(cur))
            cur = []
        cur.append(line)
        depth += line.count("(") - line.count(")")
    if cur:
        entries.append("".join(cur))
    return [e.rstrip("\n") for e in entries]


def close_lists(a, b, rel=1e-14) -> bool:
    """Equal within %.15e's rounding (the mandated VALUE format, §8.3): 16 significant digits."""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        x, y = complex(x), complex(y)
        if not (math.isclose(x.real, y.real, rel_tol=rel, abs_tol=1e-300) and math.isclose(x.imag, y.imag, rel_tol=rel, abs_tol=1e-300)):
            return False
    return True


def read_text(path: str) -> str:
    with open(path, encoding="latin-1") as fh:
        return fh.read()


# -- the RC low-pass run of psf-parser's tests/data, rebuilt from synthetic engine output (e2e 28) --

def rc_filter(f):
    """The RC divider's transfer function at a frequency (R1 = 1 kOhm, C1 = 1 uF)."""
    return 1 / complex(1, 2 * math.pi * f * 1e3 * 1e-6)


def rc_plan_and_files(d: str, engine: str = "vacask"):
    """The plan, SignalMap and EngineResult of e2e 28's deck (§11.2) over synthetic VACASK output in
    d: myop, mydc (Vin:dc 0..2 step 0.1), myac (1 Hz..1 MHz, log 20), mytran (0..0.5 s) and mysweep
    (R1:r 1k 2k 3k) { dc1, ac1, mynestedsweep (C1:c 1u 2u 3u) { ac2 } }."""
    dc_lv = level("", "", spec_step(0.0, 2.0, 0.1), "dc", "Vin:dc", "V", target=("instance", "Vin", "dc"))
    dc_lv.id = "vamos_w1"
    ac_lv = freq_level(spec_log(1.0, 1e6, 20))
    s_r1 = level("mysweep", "vamos_w2", spec_values(1000, 2000, 3000), "R1:r", "R1:r", "Ohm")
    s_c1 = level("mynestedsweep", "vamos_w3", spec_values(1e-6, 2e-6, 3e-6), "C1:c", "C1:c", "F",
                 target=("instance", "C1", "c"))
    dc1_lv = level("", "vamos_w4", spec_step(0.0, 2.0, 0.1), "dc", "Vin:dc", "V", target=("instance", "Vin", "dc"))
    steps = [
        step("vamos_a1", "myop", "op"),
        step("vamos_a2", "mydc", "dc", own=dc_lv, args={"start": 0.0, "stop": 2.0, "step": 0.1}),
        step("vamos_a3", "myac", "ac", own=ac_lv, args={"start": 1.0, "stop": 1e6}),
        step("vamos_a4", "mytran", "tran", args=TRAN_ARGS),
        step("vamos_a5", "dc1", "dc", context=[s_r1], own=dc1_lv, args={"start": 0.0, "stop": 2.0, "step": 0.1}),
        step("vamos_a6", "ac1", "ac", context=[s_r1], own=ac_lv, args={"start": 1.0, "stop": 1e6}),
        step("vamos_a7", "ac2", "ac", context=[s_r1, s_c1], own=ac_lv, args={"start": 1.0, "stop": 1e6}),
    ]
    sigs = [(("v", "in"), "in", "V"), (("v", "out"), "out", "V")]
    sm = sigmap({st.id: list(sigs) for st in steps})
    nm = {st.id: {("v", "in"): "in", ("v", "out"): "out"} for st in steps}
    freqs = results.grid_values(ac_lv.spec)
    dcs = results.grid_values(dc_lv.spec)
    files = {}
    v = [("in", "notype"), ("out", "notype")]
    files["vamos_a1"] = write_raw(os.path.join(d, "vamos_a1.raw"), v, [(1.0, 1.0)], plotname="Operating Point")
    files["vamos_a2"] = write_raw(os.path.join(d, "vamos_a2.raw"), [("vamos_w1", "notype")] + v,
                                  [(x, x, x) for x in dcs], plotname="Operating Point")
    files["vamos_a3"] = write_raw(os.path.join(d, "vamos_a3.raw"), [("frequency", "notype")] + v,
                                  [(f, 1.0, rc_filter(f)) for f in freqs], cplx=True, plotname="AC Small Signal Analysis")
    times = [k * 0.05 for k in range(11)]
    files["vamos_a4"] = write_raw(os.path.join(d, "vamos_a4.raw"), [("time", "notype")] + v,
                                  [(t, 1 + math.sin(2 * math.pi * 1e3 * t), 1.0) for t in times])
    rows = [(r, x, x, x) for r in (1000.0, 2000.0, 3000.0) for x in dcs]
    files["vamos_a5"] = write_raw(os.path.join(d, "vamos_a5.raw"), [("vamos_w2", "notype"), ("vamos_w4", "notype")] + v,
                                  rows, plotname="Operating Point")
    rows = [(r, f, 1.0, rc_filter(f) * 1e3 / r) for r in (1000.0, 2000.0, 3000.0) for f in freqs]
    files["vamos_a6"] = write_raw(os.path.join(d, "vamos_a6.raw"), [("vamos_w2", "notype"), ("frequency", "notype")] + v,
                                  rows, cplx=True, plotname="AC Small Signal Analysis")
    rows = [(r, c, f, 1.0, 1 / complex(1, 2 * math.pi * f * r * c)) for r in (1000.0, 2000.0, 3000.0)
            for c in (1e-6, 2e-6, 3e-6) for f in freqs]
    files["vamos_a7"] = write_raw(os.path.join(d, "vamos_a7.raw"),
                                  [("vamos_w2", "notype"), ("vamos_w3", "notype"), ("frequency", "notype")] + v,
                                  rows, cplx=True, plotname="AC Small Signal Analysis")
    return make_plan(steps, engine), sm, EngineResult(files=files, names=nm)


# -- goldens ---------------------------------------------------------------------------------------

def _noise_case(d: str, engine_dir: str):
    """nz1.raw-like VACASK noise output with three STRUCT kinds (rref, resistor, a Verilog-A module)
    and gain/in: the noiva.noise layout (§8.4)."""
    freqs = [4.0 * 10 ** (k / 2.0) for k in range(6)]
    vars_ = [("frequency", "notype"), ("onoise", "notype"), ("gain", "notype"), ("n(RESref)", "notype"),
             ("n(RESref,thermal)", "notype"), ("n(RESref,flicker)", "notype"), ("n(Rva)", "notype"),
             ("n(Rva,thermal)", "notype"), ("n(RESva)", "notype"), ("n(RESva,flicker)", "notype"),
             ("n(RESva,thermal)", "notype")]
    rows = []
    for f in freqs:
        th, fl, va = 1.657575e-22, 2.5e-11 / f, 1.0e-20
        rows.append((f, 2 * (th + fl) + va, 0.25, th + fl, th, fl, va, va, th + fl, fl, th))
    path = write_raw(os.path.join(engine_dir, "vamos_a1.raw"), vars_, rows, plotname="Small-Signal Noise Analysis")
    st = step("vamos_a1", "noiva", "noise", own=freq_level(spec_dec(4.0, 4.0e6, 2)),
              args={"start": 4.0, "stop": 4e6, "p": "noiva", "n": "0"}, noise_input="v1")
    sm = sigmap({"vamos_a1": [(("noise", "RESref"), "RESref", "rref"), (("noise", "Rva"), "Rva", "resistor"),
                              (("noise", "RESva"), "RESva", "res_va"), (("onoise",), "out", "V/sqrt(Hz)"),
                              (("gain",), "gain", "V/V"), (("inoise",), "in", "V/sqrt(Hz)")]})
    nm = {"vamos_a1": {("noise", "RESref"): "n(RESref)", ("noise", "Rva"): "n(Rva)", ("noise", "RESva"): "n(RESva)",
                       ("onoise",): "onoise", ("gain",): "gain"}}
    return make_plan([st]), sm, EngineResult(files={"vamos_a1": path}, names=nm), {"rref": "resistor"}


def _xf_case(engine_dir: str):
    freqs = [1.0, 10.0, 100.0]
    path = write_raw(os.path.join(engine_dir, "vamos_a1.raw"), [("frequency", "notype"), ("tf(v1)", "notype"), ("zin(v1)", "notype")],
                     [(f, complex(0.4, -0.01 * f), 1666.67) for f in freqs], cplx=True,
                     plotname="AC Small Signal Transfer Function Analysis")
    st = step("vamos_a1", "xf1", "xf", own=freq_level(spec_dec(1.0, 100.0, 1)), args={"start": 1.0, "stop": 100.0})
    sm = sigmap({"vamos_a1": [(("tf", "v1"), "V1:p", "V")]})
    return make_plan([st]), sm, EngineResult(files={"vamos_a1": path}, names={"vamos_a1": {("tf", "v1"): "tf(v1)"}})


def _status_cases(engine_dir: str):
    """A sweep of three points whose third failed (status 1), a run stopped after its first
    analysis (status 2), and an interrupted tran (status 3): what logFile lists (§8.2, §2.7)."""
    sw = level("sw", "vamos_w1", spec_values(1000, 2000, 3000), "R1:r", "R1:r", "Ohm")
    v = [("out", "notype")]
    cases = {}
    # status 1: the op leaves of points 1 and 2 exist, point 3 failed (VACASK: rows up to the abort)
    p1 = write_raw(os.path.join(engine_dir, "s1_a1.raw"), [("vamos_w1", "notype")] + v, [(1000.0, 0.5), (2000.0, 0.333)],
                   plotname="Operating Point", blank_count=True)
    st1 = step("vamos_a1", "op1", "op", context=[sw])
    cases["status1"] = (make_plan([st1]), sigmap({"vamos_a1": [(("v", "out"), "out", "V")]}),
                        EngineResult(rc=0, status={"vamos_a1": "failed"}, files={"vamos_a1": p1},
                                     failed_points={"vamos_a1": 3}, names={"vamos_a1": {("v", "out"): "out"}}))
    # status 2: the op ran, the tran never did (a vamos error in between)
    p2 = write_raw(os.path.join(engine_dir, "s2_a1.raw"), v, [(0.5,)], plotname="Operating Point")
    st2a, st2b = step("vamos_a1", "op1", "op"), step("vamos_a2", "tr1", "tran", args=TRAN_ARGS)
    cases["status2"] = (make_plan([st2a, st2b]), sigmap({"vamos_a1": [(("v", "out"), "out", "V")],
                                                        "vamos_a2": [(("v", "out"), "out", "V")]}),
                        EngineResult(rc=2, status={"vamos_a1": "ok", "vamos_a2": "failed"}, files={"vamos_a1": p2},
                                     names={"vamos_a1": {("v", "out"): "out"}, "vamos_a2": {("v", "out"): "out"}}))
    # status 3: a tran interrupted at 0.3 of 0.5 s, its rows kept under a blank count
    p3 = write_raw(os.path.join(engine_dir, "s3_a1.raw"), [("time", "notype")] + v,
                   [(t * 0.1, 1.0 - math.exp(-t)) for t in range(4)], blank_count=True)
    st3 = step("vamos_a1", "tr1", "tran", args=TRAN_ARGS)
    cases["status3"] = (make_plan([st3]), sigmap({"vamos_a1": [(("v", "out"), "out", "V")]}),
                        EngineResult(rc=3, status={"vamos_a1": "interrupted"}, files={"vamos_a1": p3},
                                     names={"vamos_a1": {("v", "out"): "out"}}))
    return cases


def _legacy_case(engine_dir: str):
    """The legacy names (§8.1): n.tran, s_00i_c.<ext>, timeSweep.tran, no sweep parent."""
    sw = level("sw", "vamos_w1", spec_values(1000, 2000), "R1:r", "R1:r", "Ohm")
    v = [("out", "notype")]
    pa = write_raw(os.path.join(engine_dir, "l_a1.raw"), [("time", "notype")] + v, [(0.0, 0.0), (1e-3, 0.5), (2e-3, 0.75)])
    pb = write_raw(os.path.join(engine_dir, "l_a2.raw"), [("vamos_w1", "notype")] + v, [(1000.0, 0.5), (2000.0, 0.333)],
                   plotname="Operating Point")
    steps = [step("vamos_a1", "timeSweep", "tran", args=dict(TRAN_ARGS, stop=2e-3, step=1e-5, maxstep=4e-5)),
             step("vamos_a2", "op1", "op", context=[sw])]
    sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V")], "vamos_a2": [(("v", "out"), "out", "V")]})
    nm = {"vamos_a1": {("v", "out"): "out"}, "vamos_a2": {("v", "out"): "out"}}
    return make_plan(steps), sm, EngineResult(files={"vamos_a1": pa, "vamos_a2": pb}, names=nm)


def write_goldens(golden_dir: str) -> None:
    """Regenerate every golden case under golden_dir (the committed fixtures)."""
    work = tempfile.mkdtemp(prefix="psf-golden-")
    try:
        def sub(name):
            d = os.path.join(work, name)
            os.makedirs(d)
            return d
        cases = {}
        pl, sm, er = rc_plan_and_files(sub("rc"))
        cases["rc"] = (pl, sm, er, None, "modern")
        pl, sm, er, masters = _noise_case(sub("noise"), sub("noise_eng"))
        cases["noise"] = (pl, sm, er, masters, "modern")
        pl, sm, er = _xf_case(sub("xf"))
        cases["xf"] = (pl, sm, er, None, "modern")
        for name, (pl, sm, er) in _status_cases(sub("status")).items():
            cases[name] = (pl, sm, er, None, "modern")
        pl, sm, er = _legacy_case(sub("legacy"))
        cases["legacy"] = (pl, sm, er, None, "legacy")
        for name, (pl, sm, er, masters, naming) in cases.items():
            d = os.path.join(golden_dir, name)
            if os.path.isdir(d):
                shutil.rmtree(d)
            os.makedirs(d)
            convert(d, pl, sm, er, naming, masters=masters)
        with open(os.path.join(golden_dir, "README"), "w", encoding="utf-8") as fh:
            fh.write(GOLDEN_README)
    finally:
        shutil.rmtree(work, ignore_errors=True)


GOLDEN_README = """The PSF writer's goldens (docs/VAMOS_SPECTRE_DESIGN.md §8, §11.1 "Outputs"; phase 1, S4).

Every file here was written by vamos/output/psf.py from synthetic engine output through
vamos.spectre.results.collect, with a fixed date, version and log time stamp, by

    cd tests/vamos && python3 -c "import test_spectre_outputs as t; t.write_goldens(t.GOLDEN_DIR)"

test_spectre_outputs.TestGoldens regenerates them into a scratch directory and compares byte for
byte; psfcheck.py accepts every file and the vendored psf-parser parses them (WSL).  vamos-authored,
GPL-3.0-or-later like the rest of the repository.

  rc/       psf-parser's Spectre 23.1 run (e2e 28, §11.2) rebuilt: myop, mydc, myac, mytran and the
            mysweep tree with its nested sweep, from synthetic VACASK rawfiles (11 tran points, not
            6002); compared structurally with psf/real/psf-parser/tests/data/ascii
  noise/    a noise analysis with rref, resistor and Verilog-A STRUCTs, out, gain and in (§8.4)
  xf/       an xf analysis: one complex trace per source, V1:p (§8.4)
  status1/  a three-point sweep whose third point failed: logFile lists the parent with two points
  status2/  a run stopped after its first analysis: logFile lists that analysis only
  status3/  an interrupted tran: its partial rows written and listed (§2.7, §8.2)
  legacy/   --vamos-psf-names=legacy: timeSweep.tran, sw_000_op1.dc, no sweep parent (§8.1)

engine/   the engine outputs the sweep-splitting and noise tests read (§7.1 "Reading", §7.2 "Output",
          §8.4), written by the engines from the vamos-authored decks beside them on 2026-10-10; the
          decks are read by no test.  Not regenerated by write_goldens.
  vacask/ VACASK 0.3.4-91-g64489cf7 (/opt/build.VACASK/Release), one run of nz.sim, a 1 kOhm / 1 kOhm
          divider with a 2 kOhm subckt load: the sweep columns come first, named by the sweep statement
          (SweepLevel.id), then `time` / `frequency`, then the saved signals; a swept analysis is one
          plot with the points concatenated (§7.1).
          nz1.raw  noise with `save nc(r1) nc(r2) nc('x1:r3')`: frequency onoise gain n(r1) n(r1,thermal) ...
          op1.raw  op under `sweep w1 ... values=[1k 2k]`: w1 out v1:flow(br), one row per point
          tr1.raw  tran under `sweep w2 ... values=[1k 1k 2k]` (two equal consecutive values, §7.1)
          ac1.raw  ac under two nested sweeps w3 (2 values) and w4 (3 values): w3 w4 frequency in out ...
          xf1.raw  acxf: frequency tf(v1) zin(v1)
          ac2.raw  ac at one frequency (values=[1k]) under `sweep w5`: w5 frequency in out v1:flow(br)
          nz2.raw  noise under `sweep w6` with the default noise saves: w6 frequency onoise gain n(x1:r3) ...
  xyce/   Xyce DEVELOPMENT-202610100047-(Release-7.10.0-212-g7cd78110), /usr/local/src/xyce-build/src/Xyce
          under engines.env_for, one run per deck, a 1 kOhm / 1 kOhm divider:
          nz0.prn     .NOISE without .STEP: the standard table `Index FREQ ONOISE INOISE DNO(R1) DNO(R2)`
          nz1.prn     .NOISE under one .STEP (2 points): the Index restarts at 0 per step, no separator (E33)
          nz2.prn     .NOISE under two .STEPs (2 x 3 points), LIN 3: six tables of three rows
          opstep.raw  .OP + .PRINT DC FORMAT=RAW under one .STEP: one plot per step point, `sweep` first
"""


# -- strings, dates, types, numbers ----------------------------------------------------------------

class TestStrings(unittest.TestCase):
    def test_encoder(self):
        self.assertEqual(psf.psf_string("out"), '"out"')
        self.assertEqual(psf.psf_string('a"b'), '"a\\"b"')
        self.assertEqual(psf.psf_string("a\\b"), '"a\\\\b"')
        self.assertEqual(psf.psf_string("net<3>"), '"net<3>"')
        self.assertEqual(psf.psf_string("net<3>", escchars=True), '"net\\<3\\>"')
        self.assertEqual(psf.psf_string("I2.Idiffpair:in!", escchars=True), '"I2.Idiffpair:in!"')
        self.assertEqual(psf.psf_string("a b/c", escchars=True), '"a\\ b\\/c"')
        self.assertEqual(psf.psf_string("", escchars=True), '""')

    def test_round_trip_through_psfcheck(self):
        d = tempfile.mkdtemp(prefix="psf-str-")
        try:
            for esc in (False, True):
                res = AnalysisResult("op-dc", "dc", "op.dc", description="DC Analysis `op'",
                                     signals=[Signal("net<3>", "V", "V", [1.0]), Signal('a"b', "V", "V", [2.0]),
                                              Signal("c\\d", "V", "V", [3.0])],
                                     header={"tolerance.relative": 1e-3, "reltol": 1e-3, "abstol(V)": 1e-6,
                                             "abstol(I)": 1e-12, "temp": 27.0, "tnom": 27.0, "tempeffects": "all",
                                             "gmindc": 1e-12})
                psf.write_analysis(d, res, HEAD, escchars=esc)
                f = psfcheck.check(os.path.join(d, "op.dc"), escchars=esc)
                self.assertEqual(f.names(), ["net<3>", 'a"b', "c\\d"])
                text = read_text(os.path.join(d, "op.dc"))
                self.assertIn('"net\\<3\\>"' if esc else '"net<3>"', text)
                self.assertIn('"a\\"b"', text)
                self.assertIn('"c\\\\d"', text)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestDate(unittest.TestCase):
    def test_fixed_tables(self):
        self.assertEqual(psf.psf_date(datetime.datetime(2021, 2, 2, 13, 7, 28)), "1:07:28 PM, Tue Feb 2, 2021")
        self.assertEqual(psf.psf_date(datetime.datetime(2025, 4, 15, 17, 46, 18)), "5:46:18 PM, Tue Apr 15, 2025")
        self.assertEqual(psf.psf_date(datetime.datetime(2019, 9, 14, 9, 11, 11)), "9:11:11 AM, Sat Sep 14, 2019")
        self.assertEqual(psf.psf_date(datetime.datetime(2021, 1, 29, 22, 57, 11)), "10:57:11 PM, Fri Jan 29, 2021")
        self.assertEqual(psf.psf_date(datetime.datetime(2026, 10, 1, 0, 5, 7)), "12:05:07 AM, Thu Oct 1, 2026")
        self.assertEqual(psf.psf_date(datetime.datetime(2026, 12, 31, 12, 0, 0)), "12:00:00 PM, Thu Dec 31, 2026")
        self.assertEqual(psf.psf_date(datetime.datetime(2026, 3, 8, 23, 59, 59)), "11:59:59 PM, Sun Mar 8, 2026")

    def test_every_sample_date_round_trips(self):
        """The dates of PROVENANCE (20 dated samples, E86) are reproduced from their fields."""
        months = {m: k + 1 for k, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
                                                     "Oct", "Nov", "Dec"))}
        pat = re.compile(r"(\d+):(\d\d):(\d\d) ([AP]M), (\w\w\w) (\w\w\w) (\d+), (\d{4})$")
        seen = 0
        with open(os.path.join(REAL_DIR, "PROVENANCE"), encoding="utf-8") as fh:
            for line in fh:
                m = pat.search(line.rstrip("\n"))
                if not m or not line.startswith("psf"):
                    continue
                h, mi, s, ampm, _, mon, day, year = m.groups()
                hour = int(h) % 12 + (12 if ampm == "PM" else 0)
                t = datetime.datetime(int(year), months[mon], int(day), hour, int(mi), int(s))
                self.assertEqual(psf.psf_date(t), m.group(0))
                seen += 1
        self.assertGreaterEqual(seen, 20)

    def test_never_strftime_and_locale_independent(self):
        with open(psf.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("strftime", src.replace("never strftime", "").replace("never `strftime`", ""))
        old = locale.setlocale(locale.LC_ALL)
        try:
            for name in ("de_DE.UTF-8", "de_DE.utf8", "fr_FR.UTF-8", "C.UTF-8"):
                try:
                    locale.setlocale(locale.LC_ALL, name)
                    break
                except locale.Error:
                    continue
            self.assertEqual(psf.psf_date(datetime.datetime(2026, 10, 1, 14, 56, 17)), "2:56:17 PM, Thu Oct 1, 2026")
        finally:
            locale.setlocale(locale.LC_ALL, old)


class TestTypes(unittest.TestCase):
    def test_lists_are_the_samples_verbatim(self):
        self.assertEqual(psf.TYPES["op"], types_of(os.path.join(ASCII_23, "myop.dc")))
        self.assertEqual(psf.TYPES["swept"], types_of(os.path.join(ASCII_23, "mydc.dc")))
        self.assertEqual(psf.TYPES["swept"], types_of(os.path.join(ASCII_23, "mytran.tran.tran")))
        self.assertEqual(psf.TYPES["swept"], types_of(os.path.join(ASCII_23, "mysweep-000_dc1.dc")))
        self.assertEqual(psf.TYPES["complex"], types_of(os.path.join(ASCII_23, "myac.ac")))
        self.assertEqual(psf.TYPES["parent"], types_of(os.path.join(ASCII_23, "mysweep_ac1.sweep")))
        self.assertEqual(psf.TYPES["parent"], types_of(os.path.join(ASCII_23, "mymonte_dc2.montecarlo")))
        self.assertEqual(psf.TYPES["noise"], types_of(NOIVA)[:3])
        self.assertEqual([e.split('"')[1] for e in psf.TYPES["op"]],
                         ["LogicV", "V", "I", "MMF", "Wb", "Temp", "Pwr", "U", "V/Sec", "I/Sec", "1/Sec", "HOD", "Q"])
        self.assertEqual(sorted(psf.TYPES), ["complex", "noise", "op", "parent", "swept"])

    def test_struct_props_follow_the_noise_sample(self):
        want = types_of(NOIVA)[3:]
        self.assertEqual([e.split('"')[1] for e in want], ["res_va", "resistor", "rref"])
        self.assertIn('"rref" STRUCT(\n"rn" FLOAT DOUBLE PROP(\n"units" "V^2/Hz"\n)\n"fn" FLOAT DOUBLE PROP(\n'
                      '"units" "V^2/Hz"\n)\n"total" FLOAT DOUBLE PROP(\n"units" "V^2/Hz"\n)\n) PROP(\n"key" "inst"\n'
                      '"master" "resistor"\n)', want)


class TestNumbers(unittest.TestCase):
    def test_precision_option(self):
        notes = []
        self.assertEqual(psf.precision(None), "%.15e")
        self.assertEqual(psf.precision("%.15e"), "%.15e")
        self.assertEqual(psf.precision("%.6e", notes), "%.6e")
        self.assertEqual(psf.precision("%g", notes), "%#g")
        self.assertEqual(psf.precision("%#.3G", notes), "%#.3G")
        self.assertEqual(psf.precision("%-+ 012.4E", notes), "%-+ 012.4E")
        self.assertEqual(notes, [])
        for bad in ("%d", "%f", "%s", "15e", "%.e5", "%5.3x", "%%g"):
            n = []
            self.assertEqual(psf.precision(bad, n), "%.15e", bad)
            self.assertEqual(len(n), 1)
            self.assertEqual(n[0].severity, "warning")
            self.assertIn(bad, n[0].message)

    def test_si(self):
        for v, u, want in ((0.5, "s", "500 ms"), (1e6, "Hz", "1 MHz"), (4.19e6, "Hz", "4.19 MHz"), (0.0, "s", "0 s"),
                           (2.0, "V", "2 V"), (1.0, "Hz", "1 Hz"), (5e-3, "s", "5 ms"), (1000.0, "", "1 k"),
                           (1.0, "", "1"), (2.5e-12, "F", "2.5 pF"), (-1.5, "V", "-1.5 V"), (1e-6, "F", "1 uF"),
                           (999.99999999, "Hz", "1 kHz")):
            self.assertEqual(results.si(v, u), want, (v, u))

    def test_grid_values(self):
        self.assertEqual(results.grid_values(spec_values(1000, 1000, 2000)), [1000.0, 1000.0, 2000.0])
        self.assertEqual(results.grid_values(spec_lin(0.0, 2.0, 4)), [0.0, 0.5, 1.0, 1.5, 2.0])
        self.assertEqual(len(results.grid_values(spec_step(0.0, 2.0, 0.1))), 21)
        self.assertAlmostEqual(results.grid_values(spec_step(0.0, 2.0, 0.1))[-1], 2.0)
        g = results.grid_values(spec_dec(1.0, 1e6, 10))
        self.assertEqual(len(g), 61)
        self.assertEqual((g[0], g[-1]), (1.0, 1e6))
        self.assertAlmostEqual(g[10], 10.0)
        g = results.grid_values(spec_dec(4.0, 4.19e6, 20))                 # E21: 120.4 -> 120 intervals
        self.assertEqual(len(g), 121)
        self.assertEqual(g[-1], 4.19e6)
        self.assertAlmostEqual(g[119], 4.0 * 10 ** (119 / 20.0))
        self.assertEqual(results.grid_of(freq_level(spec_dec(1, 10, 1))), 3)
        self.assertEqual(results.grid_of(freq_level(spec_log(1, 10, 3))), 3)
        self.assertEqual(results.grid_of(level("", "", spec_values(1, 2), "dc", "V1:dc", "V")), 1)
        self.assertEqual(results.grid_of(None), 1)


# -- the writer's rules ----------------------------------------------------------------------------

def _op_header():
    return {"tolerance.relative": 1e-3, "reltol": 1e-3, "abstol(V)": 1e-6, "abstol(I)": 1e-12, "temp": 27.0,
            "tnom": 27.0, "tempeffects": "all", "gmindc": 1e-12}


class TestWriterRules(TempDir):
    def test_operating_point_file(self):
        res = AnalysisResult("myop-dc", "dc", "myop.dc", description="DC Analysis `myop'",
                             signals=[Signal("in", "V", "V", [1.0]), Signal("V1:p", "I", "A", [-1e-3])],
                             header=_op_header(), data_type="scalar")
        psf.write_analysis(self.tmp, res, HEAD)
        text = read_text(os.path.join(self.tmp, "myop.dc"))
        f = psfcheck.check(os.path.join(self.tmp, "myop.dc"))
        self.assertEqual((f.kind, f.names(), f.value("in")), ("dc", ["in", "V1:p"], 1.0))
        self.assertEqual(f.value_props["V1:p"], {"units": "A"})
        self.assertIn('"V1:p" "I" -1.000000000000000e-03 PROP(\n"units" "A"\n)', text)
        self.assertIn('"xVecSorted" "unsorted"', text)
        self.assertIn('"tolerance.relative" 1.000000000000000e-03', text)      # header reals are %.15e
        self.assertTrue(text.startswith('HEADER\n"PSFversion" "1.00"\n"simulator" "spectre"\n"version" "%s"\n"date" "%s"\n'
                                        '"design" "#RC Low-Pass Filter"\n"analysis type" "dc"\n"analysis name" "myop"\n'
                                        % (HEAD.version, HEAD.date)), text[:400])
        self.assertTrue(text.endswith("\nEND\n"))

    def test_swept_file_and_current_trace_prop(self):
        rows = [(0.0, [0.0, 0.0]), (1e-3, [0.5, -5e-4]), (2e-3, [0.75, -2.5e-4])]
        res = AnalysisResult("tr-tran", "tran", "tr.tran.tran", sweep=("time", "s", 1, []),
                             signals=[Signal("out", "V", "V"), Signal("V1:p", "I", "A")],
                             header=dict(TRAN_ARGS), description="Transient Analysis `tr': time = (0 s -> 500 ms)",
                             data_type="swept_scalar", rows=lambda: iter(rows))
        res.header = {"tolerance.relative": 1e-3, "start": 0.0, "outputstart": 0.0, "stop": 0.5, "step": 5e-4,
                      "istep": 1e-3, "maxstep": 0.01, "ic": "all", "useprevic": "no", "skipdc": "no", "reltol": 1e-3,
                      "abstol(V)": 1e-6, "abstol(I)": 1e-12, "temp": 27.0, "tnom": 27.0, "tempeffects": "all",
                      "errpreset": "moderate", "method": "traponly", "lteratio": 3.5, "relref": "sigglobal",
                      "cmin": 0.0, "gmin": 1e-12}
        psf.write_analysis(self.tmp, res, HEAD)
        p = os.path.join(self.tmp, "tr.tran.tran")
        f = psfcheck.check(p)
        self.assertEqual((f.points, f.names(), f.column("out")), (3, ["out", "V1:p"], [0.0, 0.5, 0.75]))
        self.assertEqual(f.traces[1].props, {"units": "A"})
        text = read_text(p)
        self.assertIn('TRACE\n"out" "V"\n"V1:p" "I" PROP(\n"units" "A"\n)\nVALUE\n"time" 0.000000000000000e+00\n"out" '
                      '0.000000000000000e+00\n"V1:p" 0.000000000000000e+00\n', text)
        self.assertIn('SWEEP\n"time" "sweep" PROP(\n"sweep_direction" 0\n"units" "s"\n"plot" 0\n"grid" 1\n)\n', text)
        self.assertEqual(text.count('"LogicV" FLOAT DOUBLE PROP('), 1)
        # the header keys in §8.3's order, the reals %.15e
        self.assertIn('"start" 0.000000000000000e+00\n"outputstart" 0.000000000000000e+00\n"stop" 5.000000000000000e-01\n'
                      '"step" 5.000000000000000e-04\n"istep" 1.000000000000000e-03\n"maxstep" 1.000000000000000e-02\n'
                      '"ic" "all"\n"useprevic" "no"\n"skipdc" "no"\n', text)

    def test_complex_values_and_precision(self):
        rows = [(1.0, [complex(1, 0), complex(0.5, -0.5)]), (10.0, [complex(1, 0), complex(0.1, -0.3)])]
        res = AnalysisResult("ac-ac", "ac", "ac.ac", sweep=("freq", "Hz", 3, []),
                             signals=[Signal("in", "V", "V"), Signal("out", "V", "V")],
                             header={"tolerance.relative": 1e-3, "start": 1.0, "stop": 10.0, "operating point producer": "ac"},
                             description="AC Analysis `ac': freq = (1 Hz -> 10 Hz)", data_type="swept_scalar",
                             rows=lambda: iter(rows))
        psf.write_analysis(self.tmp, res, HEAD)
        p = os.path.join(self.tmp, "ac.ac")
        f = psfcheck.check(p)
        self.assertEqual(f.column("out"), [complex(0.5, -0.5), complex(0.1, -0.3)])
        self.assertIn('"out" (5.000000000000000e-01 -5.000000000000000e-01)\n', read_text(p))
        self.assertEqual(types_of(p), psf.TYPES["complex"])
        notes = []
        psf.write_analysis(self.tmp, res, psf.PsfHead(HEAD.version, HEAD.date, HEAD.design, precision="%g"), notes=notes)
        text = read_text(p)
        self.assertIn('"out" (0.500000 -0.500000)\n', text)                   # %g -> %#g keeps the point
        self.assertIn('"start" 1.000000000000000e+00', text)                  # header reals stay %.15e
        psfcheck.check(p)
        self.assertEqual(notes, [])
        psf.write_analysis(self.tmp, res, psf.PsfHead(HEAD.version, HEAD.date, HEAD.design, precision="%d"), notes=notes)
        self.assertEqual(len(notes), 1)
        self.assertIn("precision", notes[0].message)
        self.assertIn('"out" (5.000000000000000e-01 -5.000000000000000e-01)\n', read_text(p))

    def test_non_finite_values(self):
        rows = [(0.0, [float("nan")]), (1.0, [float("inf")]), (2.0, [float("-inf")])]
        res = AnalysisResult("tr-tran", "tran", "tr.tran.tran", sweep=("time", "s", 1, []), signals=[Signal("out", "V", "V")],
                             header={"tolerance.relative": 1e-3}, description="Transient Analysis `tr': time = (0 s -> 2 s)",
                             rows=lambda: iter(rows))
        notes = []
        psf.write_analysis(self.tmp, res, HEAD, notes=notes)
        text = read_text(os.path.join(self.tmp, "tr.tran.tran"))
        self.assertIn('"out" nan\n', text)
        self.assertIn('"out" inf\n', text)
        self.assertIn('"out" -inf\n', text)
        self.assertEqual(len(notes), 1)                                        # one warning per signal
        self.assertIn("out", notes[0].message)
        self.assertEqual(notes[0].severity, "warning")
        f = psfcheck.read(os.path.join(self.tmp, "tr.tran.tran"))
        self.assertTrue(math.isnan(f.column("out")[0]))

    def test_struct_type_collisions_are_refused(self):
        rows = [(1.0, [[1e-18, 1e-18], 1e-9])]
        for bad in ("V", "sweep", "V/sqrt(Hz)"):
            res = AnalysisResult("nz-noise", "noise", "nz.noise", sweep=("freq", "Hz", 3, []),
                                 signals=[Signal("R1", bad, "", members=[("rn", []), ("total", [])]),
                                          Signal("out", "V/sqrt(Hz)", "V/sqrt(Hz)")],
                                 header={"tolerance.relative": 1e-3}, description="Noise Analysis `nz'", rows=lambda: iter(rows))
            with self.assertRaises(ValueError):
                psf.write_analysis(self.tmp, res, HEAD)
        res = AnalysisResult("nz-noise", "noise", "nz.noise", sweep=("freq", "Hz", 3, []),
                             signals=[Signal("R1", "V_1", "", members=[("rn", []), ("total", [])]),
                                      Signal("out", "V/sqrt(Hz)", "V/sqrt(Hz)")],
                             header={"tolerance.relative": 1e-3, "operating point producer": "nz", "output": "pair of nodes",
                                     "ground": "0", "positive output signal": "out", "negative output signal": "0"},
                             description="Noise Analysis `nz': freq = (1 Hz -> 1 Hz)", rows=lambda: iter(rows))
        psf.write_analysis(self.tmp, res, HEAD, masters={"V_1": "resistor"})
        f = psfcheck.check(os.path.join(self.tmp, "nz.noise"))
        self.assertEqual(f.types["V_1"].props, {"key": "inst", "master": "resistor"})
        self.assertEqual(f.column("R1"), [{"rn": 1e-18, "total": 1e-18}])

    def test_failed_result_is_not_written_and_not_listed(self):
        ok = AnalysisResult("a-dc", "dc", "a.dc", signals=[Signal("out", "V", "V", [1.0])], header=_op_header(),
                            description="DC Analysis `a'", data_type="scalar")
        bad = AnalysisResult("b-dc", "dc", "b.dc", status="failed", description="DC Analysis `b'")
        psf.write_analysis(self.tmp, ok, HEAD)
        psf.write_analysis(self.tmp, bad, HEAD)
        psf.write_logfile(self.tmp, [ok, bad], HEAD, stamp=STAMP)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["a.dc", "logFile"])
        log = psfcheck.check_logfile(os.path.join(self.tmp, "logFile"))
        self.assertEqual(log.keys(), ["a-dc"])
        self.assertEqual(log.header["Log Time Stamp"], STAMP)
        text = read_text(os.path.join(self.tmp, "logFile"))
        self.assertTrue(text.startswith('HEADER\n"PSFversion" "1.00"\n"Log Generator" "drlLog rev. 1.0"\n"Log Time Stamp" "%s"\n'
                                        '"psfversion" "1.4.0"\n"simulator" "spectre"\n"version" "%s"\n"date" "%s"\n'
                                        '"design" "#RC Low-Pass Filter"\n"signalNameType" "spectre"\n"simMode" "Spectre"\n'
                                        '"measdgt" 0\n"ingold" 2\n"sst2usecolon" 0\nTYPE\n' % (STAMP, HEAD.version, HEAD.date)))
        self.assertIn('"a-dc" "analysisInst" (\n"dc"\n"a.dc"\n"PSF"\n""\n()\n"DC Analysis `a\'"\n) PROP(\n"data_type" "scalar"\n)\n\nEND\n', text)

    def test_logfile_prop_reals_are_g_format(self):
        parent = AnalysisResult("s_a-sweep", "sweep", "s_a.sweep", tree="sweepNode", sweep=("R1:r", "Ohm", 1, [1000.0, 2000.0]),
                                header={"tolerance.relative": 1e-3}, description="Sweep parent")
        nested = AnalysisResult("s-000_t_a-sweep", "sweep", "s-000_t_a.sweep", parent="s_a-sweep", tree="sweepNode",
                                sweep=("C1:c", "F", 1, [1e-6, 2e-6]), header={"tolerance.relative": 1e-3},
                                description="Sweep parent", swept={"R1:r": 1000.0})
        leaves = []
        for i, c in enumerate((1e-6, 2e-6)):
            leaves.append(AnalysisResult("s-000_t-%03d_a-dc" % i, "dc", "s-000_t-%03d_a.dc" % i, parent="s-000_t_a-sweep",
                                         tree="leafNode", signals=[Signal("out", "V", "V", [0.5])], header=_op_header(),
                                         description="DC Analysis `s-000_t-%03d_a'" % i, swept={"C1:c": c}, data_type="scalar"))
        nested2 = AnalysisResult("s-001_t_a-sweep", "sweep", "s-001_t_a.sweep", parent="s_a-sweep", tree="sweepNode",
                                 sweep=("C1:c", "F", 1, [1e-6, 2e-6]), header={"tolerance.relative": 1e-3},
                                 description="Sweep parent", swept={"R1:r": 2000.0})
        for i, c in enumerate((1e-6, 2e-6)):
            leaves.append(AnalysisResult("s-001_t-%03d_a-dc" % i, "dc", "s-001_t-%03d_a.dc" % i, parent="s-001_t_a-sweep",
                                         tree="leafNode", signals=[Signal("out", "V", "V", [0.5])], header=_op_header(),
                                         description="DC Analysis `s-001_t-%03d_a'" % i, swept={"C1:c": c}, data_type="scalar"))
        allr = [parent, nested, leaves[0], leaves[1], nested2, leaves[2], leaves[3]]
        for r in allr:
            psf.write_analysis(self.tmp, r, HEAD)
        psf.write_logfile(self.tmp, allr, HEAD, stamp=STAMP)
        log = check_dir(self.tmp)
        text = read_text(os.path.join(self.tmp, "logFile"))
        self.assertIn('"sweep_tree_type" "sweepNode"\n"R1:r" 1000.00\n', text)
        self.assertIn('"data_type" "scalar"\n"sweep_tree_type" "leafNode"\n"C1:c" 1.00000e-06\n', text)
        self.assertEqual(log.by_key["s-000_t_a-sweep"].props, {"sweep_tree_type": "sweepNode", "R1:r": 1000.0})
        self.assertEqual(len(log.children["s_a-sweep"]), 2)
        ptext = read_text(os.path.join(self.tmp, "s_a.sweep"))
        self.assertIn('SWEEP\n"R1:r" "sweep" PROP(\n"sweep_direction" 0\n"units" "Ohm"\n"plot" 0\n"grid" 1\n)\nVALUE\n'
                      '"R1:r" 1.000000000000000e+03\n"R1:r" 2.000000000000000e+03\nEND\n', ptext)
        self.assertNotIn("TRACE", ptext)
        self.assertEqual(types_of(os.path.join(self.tmp, "s_a.sweep")), psf.TYPES["parent"])

    def test_header_table_per_kind_is_enforced_by_psfcheck(self):
        """A header missing a kind key is caught by the oracle, so the table collect builds matters."""
        res = AnalysisResult("myop-dc", "dc", "myop.dc", description="DC Analysis `myop'",
                             signals=[Signal("in", "V", "V", [1.0])], header={"tolerance.relative": 1e-3})
        psf.write_analysis(self.tmp, res, HEAD)
        with self.assertRaises(psfcheck.PsfError):
            psfcheck.check(os.path.join(self.tmp, "myop.dc"))


# -- goldens ---------------------------------------------------------------------------------------

class TestGoldens(TempDir):
    CASES = ("rc", "noise", "xf", "status1", "status2", "status3", "legacy")

    def test_goldens_are_reproduced_byte_for_byte(self):
        write_goldens(self.tmp)
        for case in self.CASES:
            want_dir, got_dir = os.path.join(GOLDEN_DIR, case), os.path.join(self.tmp, case)
            self.assertTrue(os.path.isdir(want_dir), want_dir)
            self.assertEqual(sorted(os.listdir(want_dir)), sorted(os.listdir(got_dir)), case)
            for f in os.listdir(want_dir):
                with open(os.path.join(want_dir, f), "rb") as a, open(os.path.join(got_dir, f), "rb") as b:
                    self.assertEqual(a.read(), b.read(), "%s/%s" % (case, f))
        with open(os.path.join(GOLDEN_DIR, "README"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), GOLDEN_README)

    def test_every_golden_passes_psfcheck(self):
        total = 0
        for case in self.CASES:
            d = os.path.join(GOLDEN_DIR, case)
            log = check_dir(d)
            total += len(log.entries)
        self.assertGreaterEqual(total, 35)

    def test_rc_rebuilds_the_23_1_run_structurally(self):
        """e2e 28's structural assertions (§11.2) on the synthetic rebuild: the file set, logFile
        keys/parents/PROPs/order, header keys, TYPE list, TRACE names, SWEEP variable/units/grid and
        the descriptions equal psf-parser's run, except version and date."""
        got = os.path.join(GOLDEN_DIR, "rc")
        diffs = compare_results_dir(got, ASCII_23, omit_keys=RC_OMIT_KEYS, want_logfile=LOG_23)
        self.assertEqual(diffs, [])
        # and against itself the oracle finds nothing; against the real logFile the omitted keys differ
        self.assertEqual(compare_results_dir(got, got), [])
        self.assertTrue(compare_results_dir(got, ASCII_23, want_logfile=LOG_23))
        log = psfcheck.check_logfile(os.path.join(got, "logFile"))
        self.assertEqual(log.keys()[:7], ["myop-dc", "mydc-dc", "myac-ac", "mytran-tran", "mysweep_dc1-sweep",
                                          "mysweep_ac1-sweep", "mysweep_ac2-sweep"])
        self.assertEqual(log.keys()[7:11], ["mysweep-000_dc1-dc", "mysweep-000_ac1-ac", "mysweep-000_mynestedsweep_ac2-sweep",
                                            "mysweep-000_mynestedsweep-000_ac2-ac"])
        self.assertEqual(len(log.entries), 7 + 3 * (2 + 1 + 3))
        ac = psfcheck.check(os.path.join(got, "myac.ac"))
        self.assertEqual((ac.points, ac.sweep.props["grid"]), (21, 3))
        freqs = ac.sweep_values()
        self.assertTrue(close_lists([abs(v) for v in ac.column("out")], [abs(rc_filter(f)) for f in freqs]))
        self.assertAlmostEqual(abs(ac.at("out", freqs[10])), abs(rc_filter(freqs[10])), places=12)

    def test_real_logfile_against_itself_and_the_rebuild_against_the_real(self):
        """The structural checker is symmetric enough: psf/real against itself is clean, and the
        rebuild differs from the real run only in what psf/real has that the rebuild leaves out."""
        d = os.path.join(self.tmp, "real")
        shutil.copytree(ASCII_23, d)
        shutil.copyfile(LOG_23, os.path.join(d, "logFile"))
        self.assertEqual(compare_results_dir(d, ASCII_23, omit_keys=(), want_logfile=LOG_23), [])
        diffs = compare_results_dir(d, os.path.join(GOLDEN_DIR, "rc"))
        self.assertTrue(any("files" in x for x in diffs))

    def test_status_cases(self):
        log1 = psfcheck.check_logfile(os.path.join(GOLDEN_DIR, "status1", "logFile"))
        self.assertEqual(log1.keys(), ["sw_op1-sweep", "sw-000_op1-dc", "sw-001_op1-dc"])
        self.assertEqual(psfcheck.check(os.path.join(GOLDEN_DIR, "status1", "sw_op1.sweep")).sweep_values(), [1000.0, 2000.0])
        log2 = psfcheck.check_logfile(os.path.join(GOLDEN_DIR, "status2", "logFile"))
        self.assertEqual(log2.keys(), ["op1-dc"])
        self.assertEqual(sorted(os.listdir(os.path.join(GOLDEN_DIR, "status2"))), ["logFile", "op1.dc"])
        log3 = psfcheck.check_logfile(os.path.join(GOLDEN_DIR, "status3", "logFile"))
        self.assertEqual(log3.keys(), ["tr1-tran"])
        f = psfcheck.check(os.path.join(GOLDEN_DIR, "status3", "tr1.tran.tran"))
        self.assertEqual(f.points, 4)
        self.assertEqual(f.header["stop"], 0.5)                     # the planned stop; the data ends at 0.3 s
        self.assertAlmostEqual(f.sweep_values()[-1], 0.3)

    def test_legacy_names(self):
        d = os.path.join(GOLDEN_DIR, "legacy")
        self.assertEqual(sorted(os.listdir(d)), ["logFile", "sw_000_op1.dc", "sw_001_op1.dc", "timeSweep.tran"])
        log = psfcheck.check_logfile(os.path.join(d, "logFile"))
        self.assertEqual(log.keys(), ["timeSweep-tran", "sw_000_op1-dc", "sw_001_op1-dc"])
        for e in log.entries:
            self.assertEqual(e.parent, "")
            self.assertEqual(list(e.props), ["data_type"])
        self.assertEqual(psfcheck.check(os.path.join(d, "timeSweep.tran")).kind, "tran")

    def test_noise_golden_layout(self):
        """The noiva.noise layout (§8.4): STRUCTs first in SignalMap order, then out, gain, in; the
        resistor contributions renamed rn/fn, the Verilog-A module's kept; total = the sum; out =
        sqrt(sum of totals); gain = sqrt(power gain); in = out/gain."""
        p = os.path.join(GOLDEN_DIR, "noise", "noiva.noise")
        f = psfcheck.check(p)
        self.assertEqual(f.names(), ["RESref", "Rva", "RESva", "out", "gain", "in"])
        self.assertEqual([t.type for t in f.traces], ["rref", "resistor", "res_va", "V/sqrt(Hz)", "V/V", "V/sqrt(Hz)"])
        self.assertEqual([n for n, _, _ in f.types["rref"].members], ["rn", "fn", "total"])
        self.assertEqual([n for n, _, _ in f.types["resistor"].members], ["rn", "total"])
        self.assertEqual([n for n, _, _ in f.types["res_va"].members], ["flicker", "thermal", "total"])
        self.assertEqual(f.types["rref"].props, {"key": "inst", "master": "resistor"})
        self.assertEqual(f.types["res_va"].props, {"key": "inst", "master": "res_va"})
        self.assertEqual(list(f.types)[:3], ["sweep", "V/sqrt(Hz)", "V^2/Hz"])
        self.assertIn("V/V", f.types)
        r0 = f.column("RESref")[0]
        self.assertAlmostEqual(r0["total"], r0["rn"] + r0["fn"])
        total = sum(f.column(n)[0]["total"] for n in ("RESref", "Rva", "RESva"))
        self.assertAlmostEqual(f.column("out")[0], math.sqrt(total))
        self.assertAlmostEqual(f.column("gain")[0], 0.5)
        self.assertAlmostEqual(f.column("in")[0], f.column("out")[0] / 0.5)
        self.assertEqual(f.header["positive output signal"], "noiva")
        self.assertEqual(f.header["analysis description"], "Noise Analysis `noiva': freq = (4 Hz -> 4 MHz)")
        self.assertEqual(f.sweep.props, {"sweep_direction": 0, "units": "Hz", "plot": 0, "grid": 3})
        text = read_text(p)
        self.assertIn('"RESref" (\n', text)
        log = psfcheck.check_logfile(os.path.join(GOLDEN_DIR, "noise", "logFile"))
        self.assertEqual(log.by_key["noiva-noise"].props, {"data_type": "swept_struct"})

    def test_xf_golden(self):
        f = psfcheck.check(os.path.join(GOLDEN_DIR, "xf", "xf1.xf"))
        self.assertEqual((f.kind, f.names(), f.points), ("xf", ["V1:p"], 3))
        self.assertEqual(f.column("V1:p")[0], complex(0.4, -0.01))
        self.assertEqual(types_of(os.path.join(GOLDEN_DIR, "xf", "xf1.xf")), psf.TYPES["complex"])
        self.assertEqual(f.header["analysis description"], "Transfer Function Analysis `xf1': freq = (1 Hz -> 100 Hz)")
        self.assertEqual(f.header["xVecSorted"], "ascending")
        log = psfcheck.check_logfile(os.path.join(GOLDEN_DIR, "xf", "logFile"))
        self.assertEqual(log.by_key["xf1-xf"].props, {"data_type": "swept_scalar"})


# -- the rawfile goldens through collect (§11.1: no engine needed) ---------------------------------

class TestRawfileGoldenConversions(TempDir):
    def names_vacask(self):
        return {("v", "a"): "a", ("v", "b"): "b", ("i", "v1"): "v1:flow(br)", ("v", "x1.n"): "x1:n"}

    def names_xyce(self):
        return {("v", "a"): "V(A)", ("v", "b"): "V(B)", ("i", "v1"): "I(V1)", ("v", "x1.n"): "V(X1:N)"}

    def test_op(self):
        st = step("vamos_a1", "myop", "op")
        sm = sigmap({"vamos_a1": [(("v", "a"), "a", "V"), (("v", "b"), "b", "V"), (("i", "v1"), "V1:p", "I")]})
        er = EngineResult(files={"vamos_a1": os.path.join(NETLIST_FX, "raw_vacask_op.raw")}, names={"vamos_a1": self.names_vacask()})
        res, notes = convert(self.tmp, make_plan([st]), sm, er)
        self.assertEqual(notes, [])
        f = psfcheck.check(os.path.join(self.tmp, "myop.dc"))
        self.assertEqual((f.value("a"), f.value("b"), f.value("V1:p")), (1.0, 0.5, -0.0005))
        self.assertEqual(f.header["analysis description"], "DC Analysis `myop'")
        self.assertEqual([k for k in f.header][9:], ["tolerance.relative", "reltol", "abstol(V)", "abstol(I)", "temp", "tnom",
                                                      "tempeffects", "gmindc"])
        self.assertEqual(res[0].data_type, "scalar")

    def test_tran_both_engines_binary_and_ascii(self):
        st = step("vamos_a1", "mytran", "tran", args=dict(TRAN_ARGS, stop=1e-9, step=1e-12, maxstep=2e-11))
        sm = sigmap({"vamos_a1": [(("v", "a"), "a", "V"), (("v", "b"), "b", "V"), (("i", "v1"), "V1:p", "I"), (("v", "x1.n"), "x1.n", "V")]})
        for engine, fname, nm in (("vacask", "raw_vacask_tran.raw", self.names_vacask()),
                                  ("vacask", "raw_vacask_tran_ascii.raw", self.names_vacask()),
                                  ("xyce", "raw_xyce_tran.raw", self.names_xyce()),
                                  ("xyce", "raw_xyce_tran_ascii.raw", self.names_xyce())):
            d = os.path.join(self.tmp, fname)
            os.makedirs(d)
            path = os.path.join(NETLIST_FX, fname)
            res, notes = convert(d, make_plan([st], engine), sm, EngineResult(files={"vamos_a1": path}, names={"vamos_a1": nm}))
            self.assertEqual(notes, [], fname)
            f = psfcheck.check(os.path.join(d, "mytran.tran.tran"))
            raw = rawfile.read(path)
            self.assertEqual(f.points, len(raw.points), fname)
            self.assertEqual(f.names(), ["a", "b", "V1:p", "x1.n"])
            self.assertTrue(close_lists(f.column("b"), raw.column("b")), fname)
            self.assertTrue(close_lists(f.column("V1:p"), raw.column("i(v1)")), fname)
            self.assertTrue(close_lists(f.sweep_values(), raw.time()), fname)
            self.assertEqual(f.header["analysis description"], "Transient Analysis `mytran': time = (0 s -> 1 ns)")
            self.assertEqual(f.traces[2].props, {"units": "A"})
            log = psfcheck.check_logfile(os.path.join(d, "logFile"))
            self.assertEqual(log.keys(), ["mytran-tran"])
            self.assertEqual(log.by_key["mytran-tran"].sweep_variable, ["time"])

    def test_ac_both_engines(self):
        own = freq_level(spec_dec(1e6, 1e9, 1))
        st = step("vamos_a1", "myac", "ac", own=own, args={"start": 1e6, "stop": 1e9})
        for engine, fname, nm, sigs in (("vacask", "raw_vacask_ac.raw", self.names_vacask(), ("a", "b", "V1:p")),
                                        ("vacask", "raw_vacask_ac_ascii.raw", self.names_vacask(), ("a", "b", "V1:p")),
                                        ("xyce", "raw_xyce_ac.raw", self.names_xyce(), ("a", "b"))):
            d = os.path.join(self.tmp, fname)
            os.makedirs(d)
            entries = [(("v", "a"), "a", "V"), (("v", "b"), "b", "V")] + ([(("i", "v1"), "V1:p", "I")] if "V1:p" in sigs else [])
            path = os.path.join(NETLIST_FX, fname)
            res, notes = convert(d, make_plan([st], engine), sigmap({"vamos_a1": entries}),
                                 EngineResult(files={"vamos_a1": path}, names={"vamos_a1": nm}))
            self.assertEqual(notes, [])
            f = psfcheck.check(os.path.join(d, "myac.ac"))
            raw = rawfile.read(path)
            self.assertEqual(f.names(), list(sigs))
            self.assertEqual(f.points, 4)
            self.assertTrue(close_lists(f.column("b"), raw.column("v(b)")), fname)
            self.assertTrue(close_lists(f.sweep_values(), [p[0].real for p in raw.points]), fname)
            self.assertEqual(f.sweep.props["grid"], 3)
            self.assertEqual(f.header["operating point producer"], "myac")
            self.assertEqual((f.header["start"], f.header["stop"]), (1e6, 1e9))
            self.assertEqual(f.header["analysis description"], "AC Analysis `myac': freq = (1 MHz -> 1 GHz)")
            self.assertEqual(types_of(os.path.join(d, "myac.ac")), psf.TYPES["complex"])


# -- sweep splitting on real engine output (fixtures/spectre/psf/golden/engine) -----------------------

def vac(name):
    return os.path.join(ENGINE_DIR, "vacask", name)


def xy(name):
    return os.path.join(ENGINE_DIR, "xyce", name)


class TestSweepSplitting(TempDir):
    """VACASK: one plot per analysis, the sweep columns first, the points concatenated (§7.1);
    Xyce: one plot per .STEP point (§7.2).  The leaves are decoded with the known outer counts and
    the restart of the inner axis, never by value."""

    def test_op_under_a_sweep(self):
        sw = level("sw", "w1", spec_values(1000, 2000), "R2:r", "R2:r", "Ohm")
        st = step("vamos_a1", "op1", "op", context=[sw])
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V"), (("i", "v1"), "V1:p", "I")]})
        res, notes = convert(self.tmp, make_plan([st]), sm, EngineResult(files={"vamos_a1": vac("op1.raw")},
                                                                         names={"vamos_a1": {("v", "out"): "out", ("i", "v1"): "v1:flow(br)"}}))
        self.assertEqual(notes, [])
        log = check_dir(self.tmp)
        self.assertEqual(log.keys(), ["sw_op1-sweep", "sw-000_op1-dc", "sw-001_op1-dc"])
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "sw-000_op1.dc")).value("out"), 0.4)
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "sw-001_op1.dc")).value("out"), 0.5)
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "sw-001_op1.dc")).value("V1:p"), -0.0005)
        self.assertEqual(log.by_key["sw-001_op1-dc"].props, {"data_type": "scalar", "sweep_tree_type": "leafNode", "R2:r": 2000.0})
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "sw-000_op1.dc")).header["analysis name"], "sw-000_op1")

    def test_tran_under_a_sweep_with_equal_consecutive_values(self):
        sw = level("sw", "w2", spec_values(1000, 1000, 2000), "R2:r", "R2:r", "Ohm")
        st = step("vamos_a1", "tr1", "tran", context=[sw], args=dict(TRAN_ARGS, stop=5e-3, step=1e-3, maxstep=1e-4))
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V"), (("i", "v1"), "V1:p", "I")]})
        res, notes = convert(self.tmp, make_plan([st]), sm, EngineResult(files={"vamos_a1": vac("tr1.raw")},
                                                                         names={"vamos_a1": {("v", "out"): "out", ("i", "v1"): "v1:flow(br)"}}))
        self.assertEqual(notes, [])
        log = check_dir(self.tmp)
        self.assertEqual(log.keys(), ["sw_tr1-sweep", "sw-000_tr1-tran", "sw-001_tr1-tran", "sw-002_tr1-tran"])
        for i, (r, out) in enumerate(((1000.0, 0.4), (1000.0, 0.4), (2000.0, 0.5))):
            f = psfcheck.check(os.path.join(self.tmp, "sw-%03d_tr1.tran.tran" % i))
            self.assertEqual(f.points, 50)
            self.assertEqual(f.sweep_values()[0], 0.0)
            self.assertAlmostEqual(f.sweep_values()[-1], 5e-3)
            self.assertAlmostEqual(f.column("out")[-1], out)
            self.assertEqual(log.by_key["sw-%03d_tr1-tran" % i].props["R2:r"], r)
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "sw_tr1.sweep")).sweep_values(), [1000.0, 1000.0, 2000.0])

    def test_nested_sweeps_ac(self):
        outer = level("outer", "w3", spec_values(1000, 2000), "R2:r", "R2:r", "Ohm", target=("instance", "r2", "r"))
        inner = level("inner", "w4", spec_values(1000, 3000, 5000), "R1:r", "R1:r", "Ohm")
        st = step("vamos_a1", "ac1", "ac", context=[outer, inner], own=freq_level(spec_dec(1.0, 100.0, 1)), args={"start": 1.0, "stop": 100.0})
        sm = sigmap({"vamos_a1": [(("v", "in"), "in", "V"), (("v", "out"), "out", "V"), (("i", "v1"), "V1:p", "I")]})
        res, notes = convert(self.tmp, make_plan([st]), sm, EngineResult(files={"vamos_a1": vac("ac1.raw")},
                                                                         names={"vamos_a1": {("v", "in"): "in", ("v", "out"): "out", ("i", "v1"): "v1:flow(br)"}}))
        self.assertEqual(notes, [])
        log = check_dir(self.tmp)
        self.assertEqual(log.keys()[:5], ["outer_ac1-sweep", "outer-000_inner_ac1-sweep", "outer-000_inner-000_ac1-ac",
                                          "outer-000_inner-001_ac1-ac", "outer-000_inner-002_ac1-ac"])
        self.assertEqual(len(log.entries), 1 + 2 * 4)
        f = psfcheck.check(os.path.join(self.tmp, "outer-001_inner-001_ac1.ac"))        # R2 = 2k, R1 = 3k
        self.assertEqual(f.points, 3)
        self.assertAlmostEqual(f.column("out")[0].real, 0.25)
        self.assertEqual(log.by_key["outer-001_inner-001_ac1-ac"].props, {"data_type": "swept_scalar", "sweep_tree_type": "leafNode", "R1:r": 3000.0})
        self.assertEqual(log.by_key["outer-001_inner_ac1-sweep"].props, {"sweep_tree_type": "sweepNode", "R2:r": 2000.0})
        self.assertEqual(log.by_key["outer-001_inner_ac1-sweep"].parent, "outer_ac1-sweep")
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "outer-001_inner_ac1.sweep")).header["analysis name"], "outer-001_inner_ac1")

    def test_ac_at_one_frequency_under_a_sweep(self):
        sw = level("sw5", "w5", spec_values(1000, 2000), "R2:r", "R2:r", "Ohm")
        st = step("vamos_a1", "ac2", "ac", context=[sw], own=freq_level(SweepSpec("freq", "", "", "values", values=[num(1000.0)])),
                  args={"start": 1000.0, "stop": 1000.0})
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V")]})
        res, notes = convert(self.tmp, make_plan([st]), sm, EngineResult(files={"vamos_a1": vac("ac2.raw")},
                                                                         names={"vamos_a1": {("v", "out"): "out"}}))
        log = check_dir(self.tmp)
        self.assertEqual(log.keys(), ["sw5_ac2-sweep", "sw5-000_ac2-ac", "sw5-001_ac2-ac"])
        f = psfcheck.check(os.path.join(self.tmp, "sw5-001_ac2.ac"))
        self.assertEqual((f.points, f.sweep_values()), (1, [1000.0]))
        self.assertAlmostEqual(f.column("out")[0].real, 0.5)

    def test_parameter_sweep_as_the_own_axis(self):
        """ac at a fixed frequency sweeping a parameter (§5.2 `own`): the SWEEP variable is the
        parameter; VACASK's column is the level's id, Xyce's points are .STEP plots (§7.2)."""
        own = level("", "w5", spec_values(1000, 2000), "r", "R2:r", "Ohm", target=("instance", "r2", "r"))
        st = step("vamos_a1", "ac2", "ac", own=own, args={"start": 1000.0, "stop": 2000.0, "freq": 1000.0})
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V")]})
        d = os.path.join(self.tmp, "v")
        os.makedirs(d)
        res, notes = convert(d, make_plan([st]), sm, EngineResult(files={"vamos_a1": vac("ac2.raw")}, names={"vamos_a1": {("v", "out"): "out"}}))
        f = psfcheck.check(os.path.join(d, "ac2.ac"))
        self.assertEqual((f.sweep.name, f.sweep.props["units"], f.sweep_values()), ("r", "Ohm", [1000.0, 2000.0]))
        self.assertEqual([v.real for v in f.column("out")], [0.4, 0.5])
        self.assertEqual(f.header["analysis description"], "AC Analysis `ac2': R2:r = (1 kOhm -> 2 kOhm)")
        # Xyce: two one-row plots, the x values from the grid
        x = os.path.join(self.tmp, "x")
        os.makedirs(x)
        p = write_raw(os.path.join(x, "vamos_a1.raw"), [("FREQUENCY", "frequency"), ("V(OUT)", "voltage")], [(1000.0, 0.4)], cplx=True)
        append_raw_plot(p, [("FREQUENCY", "frequency"), ("V(OUT)", "voltage")], [(1000.0, 0.5)], cplx=True)
        res, notes = convert(x, make_plan([st], "xyce"), sm, EngineResult(files={"vamos_a1": p}, names={"vamos_a1": {("v", "out"): "V(OUT)"}}))
        f = psfcheck.check(os.path.join(x, "ac2.ac"))
        self.assertEqual(f.sweep_values(), [1000.0, 2000.0])
        self.assertEqual([v.real for v in f.column("out")], [0.4, 0.5])

    def test_xyce_op_under_a_step(self):
        sw = level("sw", "w1", spec_values(1000, 2000), "R2:r", "R2:r", "Ohm")
        st = step("vamos_a1", "opx", "op", context=[sw])
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V"), (("i", "v1"), "V1:p", "I")]})
        res, notes = convert(self.tmp, make_plan([st], "xyce"), sm, EngineResult(files={"vamos_a1": xy("opstep.raw")},
                                                                                 names={"vamos_a1": {("v", "out"): "V(OUT)", ("i", "v1"): "I(V1)"}}))
        self.assertEqual(notes, [])
        log = check_dir(self.tmp)
        self.assertEqual(log.keys(), ["sw_opx-sweep", "sw-000_opx-dc", "sw-001_opx-dc"])
        self.assertAlmostEqual(psfcheck.check(os.path.join(self.tmp, "sw-000_opx.dc")).value("out"), 0.5)
        self.assertAlmostEqual(psfcheck.check(os.path.join(self.tmp, "sw-001_opx.dc")).value("out"), 2.0 / 3.0)

    def test_xyce_tran_under_two_steps(self):
        """Xyce's plots come innermost fastest (E20), the same order as VACASK's groups."""
        a = level("a", "w1", spec_values(1, 2), "A:p", "A:p", "")
        b = level("b", "w2", spec_values(10, 20, 30), "B:p", "B:p", "")
        st = step("vamos_a1", "tr", "tran", context=[a, b], args=dict(TRAN_ARGS, stop=1.0, step=0.1, maxstep=0.02))
        v = [("TIME", "time"), ("V(OUT)", "voltage")]
        p = os.path.join(self.tmp, "vamos_a1.raw")
        first = True
        for ia in (1, 2):
            for ib in (10, 20, 30):
                rows = [(0.0, float(ia * 100 + ib)), (1.0, float(ia * 100 + ib))]
                if first:
                    write_raw(p, v, rows)
                    first = False
                else:
                    append_raw_plot(p, v, rows)
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V")]})
        res, notes = convert(self.tmp, make_plan([st], "xyce"), sm, EngineResult(files={"vamos_a1": p}, names={"vamos_a1": {("v", "out"): "V(OUT)"}}))
        log = check_dir(self.tmp)
        self.assertEqual(len(log.entries), 1 + 2 * 4)
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "a-001_b-002_tr.tran.tran")).column("out"), [230.0, 230.0])
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "a-000_b-001_tr.tran.tran")).column("out"), [120.0, 120.0])
        self.assertEqual(log.by_key["a-001_b_tr-sweep"].props, {"sweep_tree_type": "sweepNode", "A:p": 2.0})
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "a-001_b_tr.sweep")).sweep.props["units"], "")

    def test_point_index_width(self):
        self.assertEqual(results._count_width(3), 3)
        self.assertEqual(results._count_width(1000), 3)
        self.assertEqual(results._count_width(1001), 4)
        self.assertEqual(results._count_width(12000), 5)


# -- noise and xf transforms (§8.4) ---------------------------------------------------------------

class TestNoise(TempDir):
    def entries(self, kinds):
        return [(("noise", "r1"), "R1", kinds[0]), (("noise", "r2"), "R2", kinds[1]), (("noise", "x1.r3"), "x1.r3", kinds[2]),
                (("onoise",), "out", "V/sqrt(Hz)"), (("gain",), "gain", "V/V"), (("inoise",), "in", "V/sqrt(Hz)")]

    def names(self):
        return {("noise", "r1"): "n(r1)", ("noise", "r2"): "n(r2)", ("noise", "x1.r3"): "n(x1:r3)", ("onoise",): "onoise", ("gain",): "gain"}

    def test_vacask_structs_with_contributions(self):
        st = step("vamos_a1", "nz1", "noise", own=freq_level(spec_dec(1.0, 100.0, 2)), args={"start": 1.0, "stop": 100.0, "p": "out", "n": "0"}, noise_input="v1")
        sm = sigmap({"vamos_a1": self.entries(("rref", "resistor", "resistor"))})
        res, notes = convert(self.tmp, make_plan([st]), sm, EngineResult(files={"vamos_a1": vac("nz1.raw")}, names={"vamos_a1": self.names()}),
                             masters={"rref": "resistor"})
        self.assertEqual(notes, [])
        f = psfcheck.check(os.path.join(self.tmp, "nz1.noise"))
        self.assertEqual(f.names(), ["R1", "R2", "x1.r3", "out", "gain", "in"])
        self.assertEqual([n for n, _, _ in f.types["rref"].members], ["rn", "total"])
        self.assertEqual(f.types["rref"].props["master"], "resistor")
        self.assertEqual(f.types["resistor"].props["master"], "resistor")
        r1 = f.column("R1")[0]
        self.assertAlmostEqual(r1["rn"], 2.652174000288e-18)
        self.assertAlmostEqual(r1["total"], 2.652174000288e-18)
        self.assertAlmostEqual(f.column("out")[0], math.sqrt(6.63043500072e-18))
        self.assertAlmostEqual(f.column("gain")[0], 0.4)
        self.assertAlmostEqual(f.column("in")[0], math.sqrt(6.63043500072e-18) / 0.4)
        self.assertEqual(f.header["analysis description"], "Noise Analysis `nz1': freq = (1 Hz -> 100 Hz)")
        self.assertEqual(f.header["positive output signal"], "out")
        self.assertEqual(f.points, 5)
        self.assertEqual(f.sweep.props["grid"], 3)
        log = psfcheck.check_logfile(os.path.join(self.tmp, "logFile"))
        self.assertEqual(log.by_key["nz1-noise"].props, {"data_type": "swept_struct"})

    def test_vacask_total_only_and_under_a_sweep(self):
        sw = level("sw6", "w6", spec_values(1000, 2000), "R2:r", "R2:r", "Ohm")
        st = step("vamos_a1", "nz2", "noise", context=[sw], own=freq_level(spec_dec(1.0, 100.0, 1)), args={"start": 1.0, "stop": 100.0, "p": "out", "n": "0"}, noise_input="v1")
        sm = sigmap({"vamos_a1": self.entries(("resistor", "resistor", "resistor"))})
        res, notes = convert(self.tmp, make_plan([st]), sm, EngineResult(files={"vamos_a1": vac("nz2.raw")}, names={"vamos_a1": self.names()}))
        self.assertEqual(notes, [])
        log = check_dir(self.tmp)
        self.assertEqual(log.keys(), ["sw6_nz2-sweep", "sw6-000_nz2-noise", "sw6-001_nz2-noise"])
        f = psfcheck.check(os.path.join(self.tmp, "sw6-001_nz2.noise"))
        self.assertEqual([n for n, _, _ in f.types["resistor"].members], ["total"])
        self.assertAlmostEqual(f.column("out")[0], math.sqrt(8.2880437509e-18))
        self.assertAlmostEqual(f.column("gain")[0], 0.5)
        self.assertEqual(f.points, 3)

    def test_verilog_a_module_keeps_its_contribution_names(self):
        p = write_raw(os.path.join(self.tmp, "n.raw"), [("frequency", "notype"), ("onoise", "notype"), ("n(x)", "notype"),
                                                       ("n(x,flicker)", "notype"), ("n(x,thermal)", "notype")],
                      [(1.0, 3e-18, 3e-18, 1e-18, 2e-18)], plotname="Small-Signal Noise Analysis")
        st = step("vamos_a1", "nz", "noise", own=freq_level(spec_dec(1.0, 1.0, 1)), args={"start": 1.0, "stop": 1.0, "p": "out", "n": "0"})
        sm = sigmap({"vamos_a1": [(("noise", "x"), "X", "res_va"), (("onoise",), "out", "V/sqrt(Hz)")]})
        res, notes = convert(self.tmp, make_plan([st]), sm, EngineResult(files={"vamos_a1": p}, names={"vamos_a1": {("noise", "x"): "n(x)", ("onoise",): "onoise"}}))
        f = psfcheck.check(os.path.join(self.tmp, "nz.noise"))
        self.assertEqual([n for n, _, _ in f.types["res_va"].members], ["flicker", "thermal", "total"])
        self.assertEqual(f.column("X")[0], {"flicker": 1e-18, "thermal": 2e-18, "total": 3e-18})
        # the same columns under a resistor master are renamed rn / fn
        sm = sigmap({"vamos_a1": [(("noise", "x"), "X", "resistor"), (("onoise",), "out", "V/sqrt(Hz)")]})
        d = os.path.join(self.tmp, "r")
        os.makedirs(d)
        convert(d, make_plan([st]), sm, EngineResult(files={"vamos_a1": p}, names={"vamos_a1": {("noise", "x"): "n(x)", ("onoise",): "onoise"}}))
        self.assertEqual([n for n, _, _ in psfcheck.check(os.path.join(d, "nz.noise")).types["resistor"].members], ["fn", "rn", "total"])

    def test_xyce_total_only_with_a_note_and_gain_from_onoise_inoise(self):
        sw = level("sw", "w1", spec_values(1000, 2000), "R2:r", "R2:r", "Ohm")
        st = step("vamos_a1", "nzx", "noise", context=[sw], own=freq_level(spec_dec(1.0, 100.0, 2)), args={"start": 1.0, "stop": 100.0, "p": "out", "n": "0"}, noise_input="v1")
        sm = sigmap({"vamos_a1": [(("noise", "r1"), "R1", "resistor"), (("noise", "r2"), "R2", "resistor"), (("onoise",), "out", "V/sqrt(Hz)"),
                                  (("gain",), "gain", "V/V"), (("inoise",), "in", "V/sqrt(Hz)")]})
        nm = {"vamos_a1": {("noise", "r1"): "DNO(R1)", ("noise", "r2"): "DNO(R2)", ("onoise",): "ONOISE", ("inoise",): "INOISE"}}
        res, notes = convert(self.tmp, make_plan([st], "xyce"), sm, EngineResult(files={"vamos_a1": xy("nz1.prn")}, names=nm))
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].severity, "note")
        self.assertIn("total", notes[0].message)
        log = check_dir(self.tmp)
        self.assertEqual(log.keys(), ["sw_nzx-sweep", "sw-000_nzx-noise", "sw-001_nzx-noise"])
        f = psfcheck.check(os.path.join(self.tmp, "sw-001_nzx.noise"))
        self.assertEqual(f.points, 5)
        self.assertEqual([n for n, _, _ in f.types["resistor"].members], ["total"])
        self.assertAlmostEqual(f.column("R1")[0]["total"], 7.36700219e-18)
        on, inn = 1.10505033e-17, 2.48636324e-17
        self.assertAlmostEqual(f.column("out")[0], math.sqrt(on))
        self.assertAlmostEqual(f.column("gain")[0], math.sqrt(on / inn))
        self.assertAlmostEqual(f.column("in")[0], math.sqrt(inn))

    def test_xyce_two_steps_prn(self):
        a = level("sw", "w1", spec_values(1000, 2000), "R2:r", "R2:r", "Ohm")
        b = level("in", "w2", spec_values(1000, 3000, 5000), "R1:r", "R1:r", "Ohm")
        st = step("vamos_a1", "nzy", "noise", context=[a, b], own=freq_level(spec_lin(1.0, 100.0, 2)), args={"start": 1.0, "stop": 100.0, "p": "out", "n": "0"})
        sm = sigmap({"vamos_a1": [(("noise", "r1"), "R1", "resistor"), (("onoise",), "out", "V/sqrt(Hz)")]})
        res, notes = convert(self.tmp, make_plan([st], "xyce"), sm, EngineResult(files={"vamos_a1": xy("nz2.prn")},
                                                                                 names={"vamos_a1": {("noise", "r1"): "DNO(R1)", ("onoise",): "ONOISE"}}))
        log = check_dir(self.tmp)
        self.assertEqual(len(log.entries), 1 + 2 * 4)
        f = psfcheck.check(os.path.join(self.tmp, "sw-001_in-002_nzy.noise"))
        self.assertEqual((f.points, f.sweep_values()), (3, [1.0, 50.5, 100.0]))
        self.assertAlmostEqual(f.column("out")[0], math.sqrt(2.36796499e-17))
        self.assertEqual(f.sweep.props["grid"], 1)

    def test_xf_writes_one_complex_trace_per_source(self):
        st = step("vamos_a1", "xf1", "xf", own=freq_level(spec_dec(1.0, 100.0, 1)), args={"start": 1.0, "stop": 100.0})
        sm = sigmap({"vamos_a1": [(("tf", "v1"), "V1:p", "V")]})
        res, notes = convert(self.tmp, make_plan([st]), sm, EngineResult(files={"vamos_a1": vac("xf1.raw")}, names={"vamos_a1": {("tf", "v1"): "tf(v1)"}}))
        f = psfcheck.check(os.path.join(self.tmp, "xf1.xf"))
        self.assertEqual((f.kind, f.names(), f.points), ("xf", ["V1:p"], 3))
        self.assertEqual(f.column("V1:p")[0], complex(0.4, 0.0))
        self.assertEqual(f.header["operating point producer"], "xf1")
        self.assertNotIn("zin", read_text(os.path.join(self.tmp, "xf1.xf")))


# -- filters, failures, missing columns ------------------------------------------------------------

class TestTranFilters(TempDir):
    def rows(self):
        return [(k * 0.1, float(k)) for k in range(11)]

    def convert_tran(self, args, saves=None):
        p = write_raw(os.path.join(self.tmp, "a.raw"), [("time", "notype"), ("out", "notype")], self.rows())
        st = step("vamos_a1", "tr", "tran", args=dict(TRAN_ARGS, stop=1.0, step=1e-3, maxstep=0.02, **args), saves=saves)
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V")]})
        d = tempfile.mkdtemp(dir=self.tmp)
        convert(d, make_plan([st]), sm, EngineResult(files={"vamos_a1": p}, names={"vamos_a1": {("v", "out"): "out"}}))
        return psfcheck.check(os.path.join(d, "tr.tran.tran"))

    def test_outputstart(self):
        f = self.convert_tran({"outputstart": 0.55})
        self.assertEqual(f.column("out"), [6.0, 7.0, 8.0, 9.0, 10.0])
        self.assertEqual(f.header["outputstart"], 0.55)

    def test_skip_window(self):
        f = self.convert_tran({"skipstart": 0.25, "skipstop": 0.85, "skipcount": 2})
        self.assertEqual(f.column("out"), [0.0, 1.0, 2.0, 3.0, 5.0, 7.0, 9.0, 10.0])
        f = self.convert_tran({"skipcount": 5})
        self.assertEqual(f.column("out"), [0.0, 5.0, 10.0])

    def test_time_window(self):
        save = SaveSpec(["out"], time_window=[0.15, 0.35, 0.75, 0.85])
        f = self.convert_tran({}, saves=[save])
        self.assertEqual(f.column("out"), [2.0, 3.0, 8.0])

    def test_no_filter_keeps_every_point(self):
        f = self.convert_tran({})
        self.assertEqual(f.points, 11)


class TestFailures(TempDir):
    def test_failed_sweep_point_truncates_the_parent_and_marks_the_rest(self):
        sw = level("sw", "w1", spec_values(1000, 2000, 3000, 4000), "R1:r", "R1:r", "Ohm")
        p = write_raw(os.path.join(self.tmp, "a.raw"), [("w1", "notype"), ("out", "notype")], [(1000.0, 0.5), (2000.0, 0.3)],
                      plotname="Operating Point", blank_count=True)
        st = step("vamos_a1", "op", "op", context=[sw])
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V")]})
        er = EngineResult(rc=0, status={"vamos_a1": "failed"}, files={"vamos_a1": p}, failed_points={"vamos_a1": 3}, names={"vamos_a1": {("v", "out"): "out"}})
        res, notes = convert(self.tmp, make_plan([st]), sm, er)
        self.assertEqual([(r.key, r.status) for r in res], [("sw_op-sweep", "ok"), ("sw-000_op-dc", "ok"), ("sw-001_op-dc", "ok"),
                                                             ("sw-002_op-dc", "failed"), ("sw-003_op-dc", "failed")])
        log = check_dir(self.tmp)
        self.assertEqual(log.keys(), ["sw_op-sweep", "sw-000_op-dc", "sw-001_op-dc"])
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "sw_op.sweep")).sweep_values(), [1000.0, 2000.0])

    def test_failed_without_failed_points_fails_every_leaf(self):
        sw = level("sw", "w1", spec_values(1000, 2000), "R1:r", "R1:r", "Ohm")
        p = write_raw(os.path.join(self.tmp, "a.raw"), [("w1", "notype"), ("out", "notype")], [], plotname="Operating Point", blank_count=True)
        st = step("vamos_a1", "op", "op", context=[sw])
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V")]})
        er = EngineResult(status={"vamos_a1": "failed"}, files={"vamos_a1": p}, names={"vamos_a1": {("v", "out"): "out"}})
        res, notes = convert(self.tmp, make_plan([st]), sm, er)
        self.assertEqual([r.status for r in res], ["ok", "failed", "failed"])
        self.assertEqual(sorted(os.listdir(self.tmp)), ["a.raw", "logFile", "sw_op.sweep"])
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "sw_op.sweep")).points, 0)

    def test_interrupted_tran_is_written_and_listed(self):
        p = write_raw(os.path.join(self.tmp, "a.raw"), [("time", "notype"), ("out", "notype")], [(0.0, 0.0), (0.1, 0.5)], blank_count=True)
        st = step("vamos_a1", "tr", "tran", args=dict(TRAN_ARGS, stop=1.0))
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V")]})
        er = EngineResult(rc=3, status={"vamos_a1": "interrupted"}, files={"vamos_a1": p}, names={"vamos_a1": {("v", "out"): "out"}})
        res, notes = convert(self.tmp, make_plan([st]), sm, er)
        self.assertEqual(res[0].status, "interrupted")
        log = check_dir(self.tmp)
        self.assertEqual(log.keys(), ["tr-tran"])
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "tr.tran.tran")).points, 2)

    def test_missing_file_and_missing_column(self):
        st = step("vamos_a1", "op", "op")
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V"), (("v", "nosuch"), "nosuch", "V")]})
        er = EngineResult(files={"vamos_a1": os.path.join(self.tmp, "none.raw")}, names={"vamos_a1": {("v", "out"): "out"}})
        res, notes = convert(self.tmp, make_plan([st]), sm, er)
        self.assertEqual(res[0].status, "failed")
        self.assertEqual(len(notes), 1)
        self.assertIn("no output file", notes[0].message)
        p = write_raw(os.path.join(self.tmp, "a.raw"), [("out", "notype")], [(0.5,)], plotname="Operating Point")
        er = EngineResult(files={"vamos_a1": p}, names={"vamos_a1": {("v", "out"): "out"}})
        d = os.path.join(self.tmp, "d")
        os.makedirs(d)
        res, notes = convert(d, make_plan([st]), sm, er)
        self.assertEqual(res[0].status, "ok")
        self.assertEqual([n.severity for n in notes], ["warning"])
        self.assertIn("nosuch", notes[0].message)
        self.assertEqual(psfcheck.check(os.path.join(d, "op.dc")).names(), ["out"])

    def test_unreadable_output_is_a_failed_analysis(self):
        p = os.path.join(self.tmp, "a.raw")
        with open(p, "w") as fh:
            fh.write("not a rawfile\n")
        st = step("vamos_a1", "op", "op")
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V")]})
        res, notes = convert(self.tmp, make_plan([st]), sm, EngineResult(files={"vamos_a1": p}, names={"vamos_a1": {("v", "out"): "out"}}))
        self.assertEqual(res[0].status, "failed")
        self.assertEqual(len(notes), 1)
        self.assertIn("unreadable", notes[0].message)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["a.raw", "logFile"])        # no data file, the logFile empty

    def test_more_groups_than_planned_is_a_warning(self):
        sw = level("sw", "w1", spec_values(1000), "R1:r", "R1:r", "Ohm")
        p = write_raw(os.path.join(self.tmp, "a.raw"), [("w1", "notype"), ("out", "notype")], [(1000.0, 0.5), (2000.0, 0.3)], plotname="Operating Point")
        st = step("vamos_a1", "op", "op", context=[sw])
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V")]})
        res, notes = convert(self.tmp, make_plan([st]), sm, EngineResult(files={"vamos_a1": p}, names={"vamos_a1": {("v", "out"): "out"}}))
        self.assertEqual(len(notes), 1)
        self.assertIn("planned", notes[0].message)
        self.assertEqual(len(res), 2)

    def test_fewer_groups_than_planned_from_a_passed_step_is_a_warning(self):
        # phase 2's integration: a step the runner passed (ok) whose output holds fewer sweep points than the
        # plan (VACASK's step= once ran 3 of 4) fails the missing leaves, and a warning names them, so the
        # run's status 1 is never silent (§0, §2.7)
        sw = level("sw", "w1", spec_values(1000, 2000, 3000), "R1:r", "R1:r", "Ohm")
        p = write_raw(os.path.join(self.tmp, "a.raw"), [("w1", "notype"), ("out", "notype")], [(1000.0, 0.5), (2000.0, 0.3)],
                      plotname="Operating Point")
        st = step("vamos_a1", "op", "op", context=[sw])
        sm = sigmap({"vamos_a1": [(("v", "out"), "out", "V")]})
        res, notes = convert(self.tmp, make_plan([st]), sm, EngineResult(files={"vamos_a1": p}, names={"vamos_a1": {("v", "out"): "out"}}))
        self.assertEqual([(r.key, r.status) for r in res], [("sw_op-sweep", "ok"), ("sw-000_op-dc", "ok"), ("sw-001_op-dc", "ok"),
                                                             ("sw-002_op-dc", "failed")])
        self.assertEqual([(n.severity, n.message) for n in notes],
                         [("warning", "analysis `op' left 2 of the 3 sweep points planned; the missing ones are not written")])
        log = check_dir(self.tmp)
        self.assertEqual(log.keys(), ["sw_op-sweep", "sw-000_op-dc", "sw-001_op-dc"])
        self.assertEqual(psfcheck.check(os.path.join(self.tmp, "sw_op.sweep")).sweep_values(), [1000.0, 2000.0])


# -- read_prn --------------------------------------------------------------------------------------

class TestReadPrn(TempDir):
    def test_fixtures(self):
        one = rawfile.read_prn(xy("nz0.prn"))
        self.assertEqual(len(one), 1)
        self.assertEqual(one[0].variables, [("FREQ", "frequency"), ("ONOISE", ""), ("INOISE", ""), ("DNO(R1)", ""), ("DNO(R2)", "")])
        self.assertEqual((len(one[0].points), one[0].declared_points, one[0].binary), (5, 5, False))
        self.assertEqual(one[0].points[0], (1.0, 8.28787747e-18, 3.31515099e-17, 4.14393873e-18, 4.14393873e-18))
        self.assertEqual(one[0].exact("DNO(R1)"), 3)
        self.assertEqual(one[0].column("ONOISE"), [8.28787747e-18] * 5)
        two = rawfile.read_prn(xy("nz1.prn"))
        self.assertEqual([len(r.points) for r in two], [5, 5])
        self.assertEqual(two[1].points[0][1], 1.10505033e-17)
        six = rawfile.read_prn(xy("nz2.prn"))
        self.assertEqual([len(r.points) for r in six], [3] * 6)
        self.assertEqual([r.points[0][1] for r in six], [8.28787747e-18, 1.10505033e-17, 1.24318162e-17, 1.98909059e-17, 1.38131291e-17, 2.36796499e-17])
        self.assertEqual(six[5].points[1][0], 50.5)

    def test_header_only_empty_and_malformed(self):
        p = self.write("h.prn", "Index       FREQ             ONOISE\nEnd of Xyce(TM) Simulation\n")
        rs = rawfile.read_prn(p)
        self.assertEqual((len(rs), rs[0].points, rs[0].names()), (1, [], ["FREQ", "ONOISE"]))
        with self.assertRaises(rawfile.RawError):
            rawfile.read_prn(self.write("e.prn", ""))
        with self.assertRaises(rawfile.RawError):
            rawfile.read_prn(self.write("n.prn", "Title: x\n0 1 2\n"))
        with self.assertRaises(rawfile.RawError):
            rawfile.read_prn(self.write("r.prn", "Index FREQ ONOISE\n0 1.0\n"))
        with self.assertRaises(rawfile.RawError):
            rawfile.read_prn(self.write("v.prn", "Index FREQ ONOISE\n0 1.0 abc\n"))
        with self.assertRaises(OSError):
            rawfile.read_prn(os.path.join(self.tmp, "missing.prn"))
        # a TIME table and a restart without a trailer
        rs = rawfile.read_prn(self.write("t.prn", "Index TIME V(OUT)\n0 0.0 1.0\n1 1e-3 2.0\n0 0.0 3.0\n"))
        self.assertEqual([r.points for r in rs], [[(0.0, 1.0), (1e-3, 2.0)], [(0.0, 3.0)]])
        self.assertEqual(rs[0].variables[0], ("TIME", "time"))
        self.assertEqual(rs[0].time(), [0.0, 1e-3])


# -- nutmeg (§8.5) ---------------------------------------------------------------------------------

class TestNutmeg(TempDir):
    def test_plots_read_back_binary_and_ascii(self):
        pl, sm, er = rc_plan_and_files(self.tmp)
        res = list(results.collect(pl, sm, er, "modern"))
        for binary in (True, False):
            p = os.path.join(self.tmp, "rc_%s.raw" % ("bin" if binary else "ascii"))
            nutmeg.write(p, res, binary, title="#RC Low-Pass Filter")
            plots = rawfile.read_all(p)
            self.assertEqual(len(plots), 4 + 3 * (1 + 1 + 3))                       # no parents (§8.5)
            self.assertEqual(plots[0].plotname, "DC Analysis `myop'")
            self.assertEqual(plots[0].variables, [("in", "voltage"), ("out", "voltage")])
            self.assertEqual(plots[0].points, [(1.0, 1.0)])
            self.assertEqual(plots[1].variables[0], ("dc", "voltage"))
            self.assertEqual(len(plots[1].points), 21)
            self.assertEqual(plots[2].variables[0], ("freq", "frequency"))
            self.assertTrue(plots[2].complex)
            self.assertEqual(len(plots[2].points), 21)
            self.assertEqual(plots[3].variables[0], ("time", "time"))
            self.assertEqual(plots[3].title, "#RC Low-Pass Filter")
            self.assertEqual(plots[3].binary, binary)
            self.assertEqual(plots[3].declared_points, 11)
            self.assertAlmostEqual(plots[3].at("in", 0.25), 1 + math.sin(2 * math.pi * 1e3 * 0.25), places=9)
            self.assertEqual(plots[-1].plotname, "AC Analysis `mysweep-002_mynestedsweep-002_ac2': freq = (1 Hz -> 1 MHz)")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "logFile")))

    def test_noise_plot_columns_and_currents(self):
        pl, sm, er, masters = _noise_case(self.tmp, self.tmp)
        res = list(results.collect(pl, sm, er, "modern", masters=masters))
        p = os.path.join(self.tmp, "n.raw")
        nutmeg.write(p, res, True)
        [plot] = rawfile.read_all(p)
        self.assertEqual(plot.names(), ["frequency", "RESref:rn", "RESref:fn", "RESref:total", "Rva:rn", "Rva:total",
                                        "RESva:flicker", "RESva:thermal", "RESva:total", "out", "gain", "in"])
        self.assertEqual(len(plot.points), 6)
        st = step("vamos_a1", "op", "op")
        q = write_raw(os.path.join(self.tmp, "op.raw"), [("a", "notype"), ("v1:flow(br)", "notype")], [(1.0, -1e-3)], plotname="Operating Point")
        res = list(results.collect(make_plan([st]), sigmap({"vamos_a1": [(("v", "a"), "a", "V"), (("i", "v1"), "V1:p", "I")]}),
                                   EngineResult(files={"vamos_a1": q}, names={"vamos_a1": {("v", "a"): "a", ("i", "v1"): "v1:flow(br)"}}), "modern"))
        nutmeg.write(p, res, False)
        [plot] = rawfile.read_all(p)
        self.assertEqual(plot.variables, [("a", "voltage"), ("V1:p", "current")])
        self.assertEqual(plot.points, [(1.0, -1e-3)])


# -- state files (§8.6) ----------------------------------------------------------------------------

class TestStateFile(TempDir):
    def test_write_and_read(self):
        p = os.path.join(self.tmp, "spectre.ic")
        self.assertEqual(statefile.state_date(datetime.datetime(1992, 1, 21, 17, 39, 38)), "5:39:38 PM, jan 21, 1992")
        self.assertEqual(statefile.state_date(datetime.datetime(2026, 10, 1, 0, 5, 7)), "12:05:07 AM, oct 1, 2026")
        nodes = {"out": 1.17406247989272, "I2.Mid": 0.588793510612534, "vdd!": 14.9900516233357}
        currents = {"V1:p": -0.001, "I2.Idiffpair:in": 4.767791259200037e-08}
        statefile.write(p, nodes, currents, "opamp.scs", "stepresponse", "5:39:38 PM, jan 21, 1992")
        text = read_text(p)
        self.assertEqual(text, "# State file generated by Spectre from circuit file 'opamp.scs'\n"
                               "# during 'stepresponse' at 5:39:38 PM, jan 21, 1992.\n"
                               "out 1.17406247989272\nI2.Mid 0.588793510612534\nvdd! 14.9900516233357\n"
                               "V1:p -0.001\nI2.Idiffpair:in 4.76779125920004e-08\n")
        got_nodes, got_currents = statefile.read(p)
        self.assertEqual(list(got_nodes), ["out", "I2.Mid", "vdd!"])
        self.assertEqual(got_nodes["I2.Mid"], 0.588793510612534)
        self.assertEqual(list(got_currents), ["V1:p", "I2.Idiffpair:in"])
        self.assertEqual(got_currents["V1:p"], -0.001)

    def test_read_rules(self):
        p = self.write("x.ic", "# comment\n\nout 1 # trailing comment\n  in   0.5\nV1:p 2e-3\n")
        self.assertEqual(statefile.read(p), ({"out": 1.0, "in": 0.5}, {"V1:p": 2e-3}))
        with self.assertRaises(ValueError):
            statefile.read(self.write("bad.ic", "out\n"))
        with self.assertRaises(ValueError):
            statefile.read(self.write("bad2.ic", "out x\n"))


# -- streaming (§4.6, §11.1; E85) -------------------------------------------------------------------

STREAM_SCRIPT = r'''
import os, struct, sys, resource
sys.path.insert(0, %(root)r)
from vamos.output import psf, nutmeg
from vamos.spectre import results
from vamos.netlist import plan as P, signals as S
n, d = %(n)d, %(dir)r
p = os.path.join(d, "a.raw")
with open(p, "wb") as fh:
    fh.write(b"Title: s\nDate: now\nPlotname: Transient Analysis\nFlags: real\nNo. Variables: 2\nNo. Points: %%d\n"
             b"Variables:\n\t0\ttime\tnotype\n\t1\tout\tnotype\nBinary:\n" %% n)
    row, buf = struct.Struct("<2d"), bytearray()
    for i in range(n):
        buf += row.pack(i * 1e-9, float(i %% 7))
        if len(buf) > (1 << 20):
            fh.write(buf); buf = bytearray()
    fh.write(buf)
st = P.AnalysisStep("vamos_a1", "tr", "tran", args={"stop": (n - 1) * 1e-9, "step": 1e-9}, options={})
pl = P.RunPlan(actions=[P.Action("analysis", {}, st)], engine="vacask")
sm = S.SignalMap({"vamos_a1": [(("v", "out"), "out", "V")]})
er = results.EngineResult(files={"vamos_a1": p}, names={"vamos_a1": {("v", "out"): "out"}})
head = psf.PsfHead("0.1.0", "1:07:28 PM, Tue Feb 2, 2021", "s")
res = list(results.collect(pl, sm, er, "modern"))
psf.write_analysis(d, res[0], head)
nutmeg.write(os.path.join(d, "n.raw"), res, True)
# The process's own peak: /proc VmHWM is reset at exec, while ru_maxrss on Linux keeps the peak of the
# forked parent (the test runner, hundreds of MB late in a whole-suite run) across fork and exec.
rss = None
try:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmHWM:"):
                rss = int(line.split()[1]) * 1024
except OSError:
    pass
if rss is None:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
print("RSS", rss)
'''


class TestStreaming(TempDir):
    def test_a_million_points_under_200_mb(self):
        n = 1000000
        script = STREAM_SCRIPT % {"root": ROOT, "n": n, "dir": self.tmp}
        r = subprocess.run([sys.executable, "-B", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=1500)
        self.assertEqual(r.returncode, 0, r.stdout)
        rss = int(r.stdout.strip().rsplit(" ", 1)[1])
        self.assertLess(rss, 200 * 1024 * 1024, r.stdout)
        plots = rawfile.scan(os.path.join(self.tmp, "n.raw"))
        self.assertEqual((plots[0].n, plots[0].declared), (n, n))
        psf_path = os.path.join(self.tmp, "tr.tran.tran")
        self.assertGreater(os.path.getsize(psf_path), 40 * 1000 * 1000)
        with open(psf_path, "rb") as fh:
            fh.seek(-200, os.SEEK_END)
            tail = fh.read().decode("latin-1")
        self.assertTrue(tail.endswith('"out" %s\nEND\n' % ("%.15e" % float((n - 1) % 7))), tail)
        self.assertIn('"time" %s\n' % ("%.15e" % ((n - 1) * 1e-9)), tail)


# -- the third-party consumer (WSL) ----------------------------------------------------------------

def _decode(name: str) -> str:
    return re.sub(r"\\(.)", r"\1", name)


class TestPsfParserConsumer(unittest.TestCase):
    @needs_psf_parser
    def test_psf_parser_reads_every_golden(self):
        pp = import_psf_parser()
        count = 0
        for case in TestGoldens.CASES:
            d = os.path.join(GOLDEN_DIR, case)
            for f in sorted(os.listdir(d)):
                path = os.path.join(d, f)
                parsed = pp.PsfFile(path)
                if f == "logFile":
                    self.assertTrue(parsed.values)
                    continue
                ours = psfcheck.check(path)
                count += 1
                self.assertEqual(ours.header["analysis name"], parsed.header["analysis name"])
                if ours.swept:
                    theirs = {_decode(t.name): t for t in parsed.traces}
                    for t in ours.traces:
                        self.assertIn(t.name, theirs, path)
                        data = theirs[t.name].data
                        self.assertEqual(len(data), ours.points, (path, t.name))
                        for a, b in zip(ours.column(t.name), data):
                            if isinstance(a, dict):
                                self.assertEqual(sorted(a), sorted(b))
                                for k in a:
                                    self.assertEqual(a[k], b[k])
                            else:
                                self.assertEqual(a, b)
                    self.assertEqual(len(parsed.sweeps), 1)
                    self.assertEqual(list(parsed.sweeps[0].data), ours.sweep_values())
                else:
                    theirs = {_decode(v.name): v for v in parsed.values}
                    for name, val in ours.values.items():
                        self.assertEqual(theirs[name].data, val, (path, name))
        self.assertGreater(count, 30)


if __name__ == "__main__":
    unittest.main()
