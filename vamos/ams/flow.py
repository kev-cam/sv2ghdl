"""The AMS compile flow (docs/VAMOS_AMS_DESIGN.md §1).

vcs.main calls compile() when the job asks for AMS (-ad, +ad, vcs-ams).  Each
step's notes are printed as they come; an error (a warning too, under
--vamos-strict) stops the compile with AmsError.  On success the job carries
the "ams" record that ./simv's co-simulation backend reads.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import os
import re
import subprocess
from collections import Counter
from typing import Dict, Iterable, List, Optional, Tuple

from vamos.ams import (cut, deck, engines, initfile, layout, names, report, rules, shells,
                       verilog_ports, vhdl)
from vamos.ams.config import AmsConfig
from vamos.ams.model import AmsPlan, RuleHits
from vamos.backends.nvc import BackendError, NvcBackend, cpu_time
from vamos.console import Console
from vamos.job import Job, Source
from vamos.netlist import ir, spice
from vamos.notes import Note, NoteError, error, has_errors, strict

ENGINES = ("vacask", "xyce")
VENDOR_ENGINES = ("xa", "finesim", "primesim", "hsim", "nanosim")


class AmsError(BackendError):
    """The AMS compile failed; the notes explaining why were already printed."""


def choose_engine(opts: dict, cfg: Optional[AmsConfig]) -> str:
    """--vamos-analog > VAMOS_ANALOG > choose vacask|xyce > vacask."""
    for src, val in (("--vamos-analog", opts.get("analog")),
                     ("VAMOS_ANALOG", os.environ.get("VAMOS_ANALOG"))):
        if val and val is not True:
            if val.lower() not in ENGINES:
                raise AmsError("%s=%s: the analog engine must be vacask or xyce" % (src, val))
            return val.lower()
    if cfg is not None and cfg.choose is not None and cfg.choose.engine.lower() in ENGINES:
        return cfg.choose.engine.lower()
    return "vacask"


def _control_paths(job: Job) -> List[str]:
    ctl = job.ams_control or os.path.join(job.cwd, initfile.DEFAULT_CONTROL)
    ini = initfile.find_ini(job.cwd, os.environ)
    return ([ini] if ini else []) + [ctl]


def compile_tools(job: Job, opts: dict) -> List[Tuple[str, str]]:
    """[(tool, path)] for the provenance header, before the compile runs."""
    cfg = None
    try:
        cfg = initfile.parse_control(_control_paths(job), job.cwd, os.environ)
    except Exception:              # the compile reports control-file problems properly
        pass
    try:
        engine = choose_engine(opts, cfg)
    except AmsError:
        engine = "vacask"
    if engine == "vacask":
        out = [("VACASK", engines.vacask_bin())]
        ov = engines.openvaf()
        if ov:
            out.append(("OpenVAF-r", ov))
        return out
    return [("Xyce", engines.xyce_bin() or "Xyce")]


class _Printer:
    def __init__(self, con: Console, strict_on: bool):
        self.con, self.strict_on = con, strict_on

    def flush(self, notes: Iterable[Note], stage: str) -> None:
        ns = strict(list(notes), self.strict_on)
        for n in ns:
            self.con.err("vamos: " + n.text())
        if has_errors(ns):
            raise AmsError("AMS compile failed at %s" % stage)

    def run(self, fn, stage: str, *args, **kw):
        try:
            return fn(*args, **kw)
        except NoteError as e:
            self.flush(e.notes, stage)
            raise AmsError("AMS compile failed at %s" % stage)


def _agreement(deck_path: str, boundary_path: str) -> List[Note]:
    """§5.7: the deck's code: URIs and the boundary file name the same bridges."""
    notes: List[Note] = []
    d = Counter(deck.deck_uris(deck_path))
    b: Counter = Counter()
    with open(boundary_path) as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 3 and parts[0] in ("D2A", "A2D"):
                b[(parts[0], parts[2])] += 1
                if len(parts[1]) > names.MAX_FIELD:
                    notes.append(error(boundary_path, "boundary path longer than %d characters: %s"
                                       % (names.MAX_FIELD, parts[1][:80])))
    for key in sorted(set(d) | set(b)):
        if d[key] != b[key]:
            notes.append(error(boundary_path, "deck and boundary disagree on %s %s (deck %d, boundary "
                               "%d)" % (key[0], key[1], d[key], b[key])))
    for (direction, name), k in b.items():
        if k > 1:
            notes.append(error(boundary_path, "bridge name %s is used %d times" % (name, k)))
        if len(name) > 255:
            notes.append(error(boundary_path, "bridge name longer than 255 characters: %s" % name[:80]))
    return notes


def _plan_json(plan: AmsPlan) -> dict:
    """ams.json: the plan as JSON (tuple keys turned into strings)."""
    def conv(o):
        if dataclasses.is_dataclass(o):
            return {f.name: conv(getattr(o, f.name)) for f in dataclasses.fields(o)}
        if isinstance(o, dict):
            return {(",".join(str(x) for x in k) if isinstance(k, tuple) else str(k)): conv(v)
                    for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [conv(x) for x in o]
        return o
    return {"version": 1, "top": plan.analysis.top, "cells": conv(plan.analysis.cells),
            "instances": conv(plan.analysis.instances), "nodes": conv(plan.nodes),
            "bridges": conv(plan.bridges)}


_MAXSTEP_NOTE = ".tran: maximum time step "


def _netlist_notes(notes: Iterable[Note], opts: dict) -> List[Note]:
    """The parser's notes as printed at step 5: with --vamos-analog-maxstep, its note on
    the .tran's computed maximum time step is dropped (deck.py then notes the value the
    option gives, the one the deck uses)."""
    if not opts.get("analog_maxstep"):
        return list(notes)
    return [n for n in notes if not (n.severity == "note" and n.message.startswith(_MAXSTEP_NOTE))]


# shells.find_cells' errors for a cell whose subckt is not in nl.subckts() (§4.3.2)
_CELL_ERRORS = (
    (re.compile(r"^module (\S+): use_spice binds it to subckt (\S+), which is not in the SPICE "
                r"netlists$"), 1, 2),
    (re.compile(r"^module (\S+) not found in Verilog sources or SPICE netlists"), 1, 1),
    (re.compile(r"^cell (\S+): no SPICE subckt (\S+) in the netlists$"), 1, 2),
    (re.compile(r"^use_spice -cell (\S+):(\S+): no subckt (\S+) in the SPICE netlists$"), 1, 2),
)
_LEFT_WHY = re.compile(r"^subckt \S+ is left out: (.*); instantiating it is an error$", re.S)


def left_out_cells(notes: Iterable[Note], left: Dict[str, Note]) -> List[Note]:
    """Step 6's notes, where every "subckt not found" error about a subckt the parser left
    out (spice.left_out) becomes one error naming the cell, the subckt and the reason it
    was left out ("cell C: subckt S cannot be simulated: <why> (<where>)"); the step-5
    note already printed that reason, but only there."""
    if not left:
        return list(notes)
    by_low = {k.lower(): v for k, v in left.items()}
    out: List[Note] = []
    done = set()
    for n in notes:
        hit = None
        if n.severity == "error":
            for rx, gc, gs in _CELL_ERRORS:
                m = rx.match(n.message)
                if m and m.group(gs).lower() in by_low:
                    hit = (m.group(gc), m.group(gs))
                    break
        if hit is None:
            out.append(n)
            continue
        cell, sub = hit
        if (cell, sub.lower()) in done:
            continue
        done.add((cell, sub.lower()))
        why = by_low[sub.lower()]
        m = _LEFT_WHY.match(why.message)
        out.append(error(n.origin, "cell %s: subckt %s cannot be simulated: %s"
                         % (cell, sub, m.group(1) if m else why.message)))
    return out


def _analyse_cut(be: NvcBackend, path: str, top: str, printer: _Printer) -> None:
    std = be._metadata().get("NVC_STD", "2040")
    for args in (["-a", path], ["-e", top]):
        cmd = [be.nvc, "-M", "2g", "-H", "1g", "--std=" + std, "--work=" + be.work_spec(),
               "-L", be.libdir] + args
        if os.environ.get("VAMOS_VERBOSE"):
            printer.con.out("vamos: + " + " ".join(cmd))
        r = subprocess.run(cmd, cwd=be.workdir, env=be._env(), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, errors="replace")
        bad = r.returncode != 0 or re.search(r"should be reanalysed", r.stdout)
        if bad:
            for line in r.stdout.splitlines():
                printer.con.err(line)
            # an instance path the cut's tables do not hold (cut.py's vams_index_<v> reports it
            # while nvc folds the constant, and nvc then crashes): say what it is, once
            miss = re.search(r"vamos: no cut table entry for instance path (\S+)", r.stdout)
            if miss:
                printer.flush([error(path, "the cut has no table entry for instance path %s, which "
                                     "nvc elaborated: an internal error of the cut (please report it, "
                                     "with %s)" % (miss.group(1), path))], "the cut design")
            raise AmsError("nvc %s of the cut design failed" % args[0])


def compile(job: Job, be: NvcBackend, con: Console, opts: dict) -> Tuple[str, float]:
    """Run §1 steps 1-13.  Returns (top module, cpu time after translation)."""
    pr = _Printer(con, bool(opts.get("strict")))
    daidir = job.daidir
    layout.reset(daidir)

    # 1. control files
    paths = _control_paths(job)
    if not os.path.isfile(paths[-1]):
        raise AmsError("AMS control file %s not found" % paths[-1])
    cfg = initfile.parse_control(paths, job.cwd, os.environ)
    pr.flush(cfg.notes, "the control file")
    if cfg.choose is None:
        pr.flush([error(paths[-1], "the control file must contain at least choose")], "the control file")

    # 2. engine, and its tool overrides (an override that names nothing usable is never
    #    replaced by a default)
    engine = choose_engine(opts, cfg)
    pr.flush([error("", v) for v in engines.problems(engine)], "the analog engine")

    # 3. preprocess, time units, analog-access API
    pp = pr.run(verilog_ports.preprocess, "preprocessing", job, layout.pp_orig(daidir),
                override_timescale=job.override_timescale, ams=True)
    pr.flush(list(pp.notes) + verilog_ports.api_scan(pp), "preprocessing")
    job.precision = pp.precision
    # VCS's -v rule, as in a plain compile: a library copy of a module a source (or an
    # earlier -v file) defines is blanked from pp.orig.v before anything reads it
    verilog_ports.apply_library_rule(pp)

    # 4. top: find_top refuses a second -top and a -top that names no module (§1.4)
    top = pr.run(verilog_ports.find_top, "choosing the top", pp, job, exclude=shells.cell_globs(cfg))
    pr.flush(verilog_ports.precheck(job, top, job.override_timescale, pp=pp), "the source check")

    # 5. netlist
    synth_notes: List[Note] = []
    popts = ir.ParseOpts(dialect=cfg.choose.dialect,
                         case=(cfg.xa.get("case") or "lower") if cfg.xa else "lower",
                         parhier_local=str(opts.get("parhier", "")).lower() == "local",
                         synth_stop=deck.analog_stop(opts, synth_notes), synth_step=deck.SYNTH_STEP)
    pr.flush(synth_notes, "the vamos options")
    # (path, origin of the choose): §4.3.1 also tries a relative netlist beside its control file
    netlists = [(p, cfg.choose.origin) for p in cfg.choose.netlists]
    nl = pr.run(spice.parse, "the SPICE netlist", netlists, cfg.netlist_lines, job.cwd, popts)
    pr.flush(_netlist_notes(nl.notes, opts), "the SPICE netlist")

    # 6-7. cells and shells (a cell bound to a subckt the parser left out: §4.3.2)
    left = spice.left_out(nl)
    try:
        sh = shells.build(pp, top, cfg, nl, job)
    except NoteError as e:
        pr.flush(left_out_cells(e.notes, left), "the SPICE cells")
        raise AmsError("AMS compile failed at the SPICE cells")
    pr.flush(sh.notes, "the SPICE cells")

    # 8. translate
    job2 = dataclasses.replace(job, sources=[Source(os.path.abspath(sh.path), "sv")], lib_files=[],
                               tops=[top], defines={}, incdirs=[], timescale=None,
                               override_timescale=None)
    be2 = NvcBackend(job2, con.out)
    try:
        be2.analyse()
    except BackendError as e:
        for n in strict(verilog_ports.translator_warnings(be2.translator_lines, pp), pr.strict_on):
            con.err("vamos: " + n.text())
        raise AmsError("translation failed: %s" % e)
    c1 = cpu_time()
    # the translator's own warnings (a connection made one way only, a pull with nothing to
    # sit on): approximations, so errors under --vamos-strict (§0)
    xlat = verilog_ports.translator_warnings(be2.translator_lines, pp)
    deferred = be2.deferred_modules()
    if deferred:
        pr.flush(xlat + [error(top, "sv2ghdl could not translate module %s (see %s/iverilog.log)"
                               % (m, layout.nvc_dir(daidir))) for m in deferred], "translation")
    design_path = os.path.join(layout.nvc_dir(daidir), "design.vhd")
    if not os.path.isfile(design_path):
        pr.flush(xlat, "translation")
        raise AmsError("translation produced no design.vhd (see %s)" % layout.nvc_dir(daidir))
    with open(design_path, errors="replace") as fh:
        pr.flush(xlat + verilog_ports.unsupported_tasks(fh.read(), pp, ams=True), "translation")

    # 9. cut analysis and roles
    design = pr.run(vhdl.parse, "reading design.vhd", design_path)
    hits = RuleHits()
    ana = pr.run(cut.analyse, "the digital cut", design, top, sh.cells, nl, cfg, hits, pp=pp,
                 directions=sh.directions)
    pr.flush(ana.notes, "the digital cut")
    ana.notes = []
    alloc = names.NameAllocator(deck.seed_names(nl))
    nodes = pr.run(cut.assign_roles, "interface roles", ana, alloc,
                   functools.partial(rules.disabled, cfg, hits=hits),
                   functools.partial(rules.removal, cfg, hits=hits), directions=sh.directions)
    pr.flush(ana.notes, "interface roles")
    plan = AmsPlan(ana, nodes)

    # 10. deck
    dres = pr.run(deck.build, "the analog deck", nl, plan, cfg, engine, alloc, hits, daidir, opts,
                  design=design)
    pr.flush(dres.notes, "the analog deck")

    # 11. cut emit, boundary, agreement
    em = pr.run(cut.emit, "the cut VHDL", plan, layout.ams_dir(daidir))
    pr.flush(_agreement(dres.path, em.boundary_path), "the deck/boundary check")

    # 12. elaborate
    _analyse_cut(be, em.vhdl_path, top, pr)

    # 13. persist
    pr.flush(rules.unmatched(cfg, hits, names=[n.canonical for n in plan.nodes]), "the control file")
    # (ReportError is a NoteError naming the file: printed like any stage's notes)
    pr.run(report.write, "the IE report", layout.ie_report(job.exe), plan, engine, opens=dres.opens)
    try:
        with open(layout.plan_json(daidir), "w") as fh:
            json.dump(_plan_json(plan), fh, indent=1)
    except OSError as e:
        pr.flush([error(layout.plan_json(daidir), "cannot write the AMS plan: %s"
                        % (e.strerror or e))], "the AMS plan")
    job.ams = {"version": layout.RECORD_VERSION, "engine": engine,
               "deck": layout.rel(daidir, dres.path), "boundary": layout.rel(daidir, em.boundary_path),
               "analysis": dres.analysis, "raw": layout.RAW, "stop": dres.stop,
               "start": dres.start, "stop_synthesized": dres.stop_synthesized,
               "out_prefix": cfg.choose.out_prefix or layout.DEFAULT_PREFIX,
               "osdi": [layout.rel(daidir, p) for p in dres.osdi], "abi": layout.ABI}
    con.out("vamos: AMS: %d SPICE instance(s), %d analog node(s), %d bridge(s); %s deck %s"
            % (len(ana.instances), len(nodes), len(plan.bridges), engine,
               layout.rel(daidir, dres.path)))
    return top, c1
