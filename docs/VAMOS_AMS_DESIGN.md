# vamos `vcs-ams`: design

Status: as built after round 6, revision 6, 2026-10-03 (revision 5, 2026-10-02, recorded the review
fixes). It is the contract for the implementation
and the record of what was built; `VAMOS_PLAN.md` §6 points here. Revision 1 went through an adversarial
review: five lenses (VCS fidelity, the co-simulation protocol, the digital cut, netlist translation,
implementability), with every finding re-checked by an independent verifier, usually by experiment on the
installed engines. Revision 2 folded the confirmed findings in; revision 3 folded in an audit of revision 2
against those verdicts plus an internal-consistency pass. Revision 4 is the as-built contract: ten phase-1
agents, the phase-2 integrator, five phase-3 end-to-end test writers, two fix rounds and two verifier runs
implemented revision 3 (§10), and wherever they chose, deviated or extended, this text says what the code
does (§11); two checkers then compared it with the code and the agents' reports. Revision 5 records the
review fixes: phase 4 was an adversarial review of the whole diff, every finding re-checked by an independent
skeptic, and a first fix round for its cut, engine, netlist and Verilog findings; phase 5 was the final fix
round for its translator and integration findings, the first round's hand-offs, a user-guide trial
(docs/VAMOS_GUIDE.md followed literally by a fresh user) and this document's open items, each fix with a
regression test that fails without it (§9, §10, §11); a repair round after phase 5's ivtest gate and
document check then fixed most of what they had left open (T21–T24 and the integration items, §10).
Revision 6 records the merge of the other kev-cam translator and library work into the stack (§7, Upstream
merge) and round 6, which ran on the merged code: six fixers, each owning its files, took revision 5's open
items – the translator's Verilog process semantics (T25), file I/O (T26), waves and nvc's own items (P11),
the random generators, switches and the sv2vhdl library (T2, T16, P12, P13), HSPICE model defaults and two
engine defects (§4.3, P14), the cut and the regression harness (§5.4, §9) – each fix with a regression test
that fails without it (§9, §11). What is still not done is marked **open** and collected
in §10. The research and review reports were session artefacts; anything an implementer needs from them is
stated here. Every module's docstring is authoritative for its API, and the frozen phase-0 modules (§10) are
part of the contract: where this text and a docstring differ, fix both (§10 lists the known drifts).

## 0. Goal, scope and the one rule

`vcs ... -ad[=ctl]` (or `+ad[=ctl]`, or `vamos -vcs-ams ...`, or a `vcs-ams` symlink) compiles a VCS AMS
"Verilog-SPICE" design and `./simv` runs it:

- digital (Verilog/SV) through sv2ghdl → nvc, as the `vcs` personality does;
- analog (HSPICE-dialect SPICE subckts, optionally Verilog-A through `.hdl`) on **VACASK by default**,
  **Xyce as an option** (`--vamos-analog=xyce`, `VAMOS_ANALOG=xyce`, or `choose xyce` in the control file);
- coupled by nvc's analog-master co-simulation (`nvc -r --vacask-netlist=|--xyce-netlist= --cosim-config=`).

**The one rule: never silently mis-simulate.** Every construct vamos meets gets exactly one disposition:

| severity | meaning |
|---|---|
| (silent) | applied or mapped faithfully |
| `note` | informational; the result is unaffected |
| `warning` | an approximation vamos has to make; `--vamos-strict` turns it into an error |
| `error` | the compile (or the run) fails, naming the construct, its origin and, where there is one, the fix |

An IE-rule selector that matches nothing is an error (`[MSV-IE-OPT-TNF]`, as VCS reports it; downgradable
with `downgrade_to_warn MSV-IE-OPT-TNF`). A setting vamos cannot honour is never dropped without a message.

**v1 includes** (exercised end to end, §9):
- the two-step flow (`vcs files -ad=… [-R]`, then `./simv`);
- the control-file (`vcsAD.init`) subset in §2;
- Verilog-top designs whose SPICE cells are instantiated from Verilog, at any depth, including inside
  modules instantiated more than once, generate blocks and instance arrays;
- auto-binding of SPICE-only cells (VCS "shadow modules") and `use_spice` / `partition -cell` overriding a
  Verilog view of every instance of a cell;
- buses via `bus_format` and `port_index_order`;
- supplies via `port_connect`, `.global`, `d2a powernet` (PAMS Methods #2a and #2b) and Verilog
  `supply0`/`supply1` nets;
- node, instance and cell a2d/d2a rules with VCS alias matching, and `ie_reference_voltage`;
- bidirectional pins (declared `inout`, or auto ports whose net is digitally driven and read);
- tri-state digital drivers (Z means "not driving", decided bit by bit), weak drivers, static
  pull-ups/pull-downs and `tri0`/`tri1` nets;
- auto ports on a Verilog wrapper's `input` port, and cut ports behind the one-way buffer Verilog puts on such
  a port (§5.1 step 2b, §5.4);
- `$finish`, `$stop`, `$fatal` and other fatal errors ending the run (the patches in §7);
- analog output as a SPICE rawfile;
- an interface-element report in paste-back syntax (§3.6).

**Approximations (warnings):** SPICE ports connected through a Verilog *variable* (`reg`/`logic`/`bit`)
stay analog, where VCS digitises the connection (§5.4); weak and pull drivers are modelled at pull
strength (3500.2 Ω), because `logic3d` does not distinguish them; HSPICE model behaviour neither engine has,
or that the manuals leave open (the default CAPOP=2 gate capacitance runs as SPICE's Meyer model; a MOS card
with neither CJ nor NSUB, CBD/CBS beside AD/AS, a LEVEL 1 card with COX, a PHP other than PB, MOS
`sa`/`sb`/`sd` under `.option scale`; §4.3.5, §4.3.6); a connection the translator makes one way only, or a
`tri1`/`tri0` pull it has nowhere to put (T2, T4, T5); a seeded `$random`/`$urandom` that Verilog may skip (a
`?:` branch, an `&&`/`||` operand) or whose seed its statement reads first (T16); a D2A ramp shorter than the
engine resolves at that time (1e-13 × t) is stretched to that length (P3, a run-time warning from the
bridge, which `--vamos-strict` does not see). The control-file commands and
keys vamos ignores are each a warning, or a note where ignoring them leaves the result unaffected (§2.2,
§3.5).

**Not in v1** (each is an error unless marked otherwise):
- the three-step `vlogan`/`vhdlan` flow (a phase-2 item of the `vcs` personality);
- SPICE-top designs (`spice_top`, `use_verilog`, a top that is a `use_spice` cell); the Verilog-AMS flow
  (`-ams`, `.va`/`.vams` sources, connect rules);
- real-number interface elements (`e2r`/`r2e`/nettypes), except `real` ports of a multi-view cell, which
  bridge directly;
- one cell with mixed views: `use_spice -inst` must cover every instance of a multi-view cell; per-instance
  subckts or port maps on a SPICE-only cell;
- a Verilog parameter override (integer, real or string) on a cut instance, unless the cell is multi-view
  and its port ranges depend on the parameter (§4.7);
- `port_connect` to a node inside a SPICE instance (`<inst>.<internal node>`) or to a Verilog-only net, and
  `real p => net` in `port_connect` (errors, §2.2);
- a module port list with port expressions (`.a(x)`) or concatenations, on a cut cell; interface,
  user-typed, `ref`, unpacked, multi-dimensional or `integer` ports on a multi-view cell;
- non-constant (`dynamic`) supplies in `vdd=`/`vss=`/`ie_reference_voltage`; wildcard supply names
  (`vdd=`, `vss=`, `ie_reference_voltage`, and `vdd_port=`/`vss_port=` after `../`; a plain wildcard
  `vdd_port=`/`vss_port=` is matched against the instance's subckt ports, §3.3); `minv*`, `vdd_filter`,
  `dynamic_supply_filter`;
- the VCS analog-access API (`$snps_*`, `$hdl_xmr*`, `snps_above`/`snps_cross`/`snps_absdelta`) and
  hierarchical references into SPICE internals;
- system tasks tgt-vhdl does not translate (§1.3; `$dumpfile`, `$dumpvars` and the other VCD tasks are
  vamos's, as in a plain compile, §8), and system functions it replaces by a constant (none in sv2vhdl mode
  since round 6: `$fopen` and `$urandom(seed)` are translated, T26, T16);
- `force`/`release` on a net that reaches a cut port;
- weak drivers other than static pulls on a BIDIR net; `remove_d2a` on a BIDIR net;
- a cut output behind a Verilog input-port buffer, and an inout cut port behind one whose wrapper also reads
  the port (§5.4);
- digital time precision coarser than 1 ms (§1.2);
- a `.global`-named subckt port bound to a different net;
- UCLI `ace` commands; `.alter`, `.data`, `.if`, Monte Carlo; `.measure` (warning: ignored); analyses other
  than the first `.tran` (warning: ignored);
- FSDB, WDF and tr0 output (note: a rawfile is written instead); `-ad_runopt` (note);
- HSPICE model levels with no faithful target (§4.3.6), `.option dcap=3` (§4.3.5), `.option scalm≠1`,
  `geoshrink≠1` and `aspec` (other than 0), `.if` binning; a K-coupled inductor whose inductance is on a
  model card, or whose card has TC1/TC2 (§4.3.7); an R card's wire capacitance (HSPICE's CRC model) and
  `SHRINK≠1` (§4.3.6);
- a net name containing `.`, and a net named `<x>:<node>` after an internal node of an X instance `x` in
  its own scope (§4.3.2); an `i()` in a behavioral expression of an element with no branch current the
  engines can read (§4.1);
- PARHIER=GLOBAL name collisions between top-level and subckt parameters (escape: `--vamos-parhier=local`);
- PWL `R=` repeat;
- Xyce only: a binned MOS instance with `nf≠1` whose geometry is not a number on its instance path (e.g. it
  depends on `temper`); a level-3 (geometric) diode; a Verilog-A instance with parameter overrides or a
  multiplier; an R card with `DW≠DLR`, an R or C card with a model `L`, or a `RES`/`CAP` default (§4.3.6);
- VACASK only: a subckt defined inside another that reads a parameter of the enclosing subckt (§4.4).

## 1. Compile-time flow

```
vcs personality ──(-ad / +ad / vcs-ams: job.ams_control set)──► ams.flow.compile(job, be, con, opts)
 1. control files  snps_vcsAD.ini (cwd, else $HOME) then the -ad file → AmsConfig; a missing
                   control file or no choose is an error                                  ams/initfile.py
 2. engine         --vamos-analog > VAMOS_ANALOG > choose vacask|xyce > vacask; then every VAMOS_*
                   override of that engine's tools that is set must name an executable or a
                   directory (engines.problems, §6), else "AMS compile failed at the analog engine"
                                                                                         ams/flow.py, ams/engines.py
 3. preprocess     iverilog -E -g2012 through an ivlpp -L wrapper → ams/pp.orig.v (+ the origin map,
                   ams/vamos_prelude.v); time-unit rules (§1.2); API scan (§1.3); job.precision;
                   VCS's -v library rule (verilog_ports.apply_library_rule: a -v copy of a module a
                   source or an earlier -v file defines is blanked from pp.orig.v and the module
                   index rebuilt)
                                                                                         ams/verilog_ports.py
 4. top            -top (find_top: exactly one, naming a module), else a structural root scan of
                   pp.orig.v with exclusions (§1.4); then the precheck: iverilog -g2012 -tnull -s <top>
                   on the ORIGINAL sources (user file:line), or on the masked pp.orig.v when the -v
                   rule blanked a copy (lines mapped back through pp.origin; precheck(pp=))
 5. netlist        choose netlists as (path, choose origin) + netlist_commands → IR (ground pass,
                   parameter, option, model and source rules, §4)                         netlist/spice.py
 6. cell set       iverilog -tnull -s <top> on pp.orig.v with the multi-view candidates replaced by
                   placeholders that instantiate vamos_ams_probe_<k> (§5.1)              ams/shells.py
 7. shells         CutCells; masked pp.v + shells; direction probe (steps 1-4 and 2b) → shell_dir
                   (a cell bound to a subckt the parser left out fails at step 6 with one error naming
                   the cell, the subckt and why it was left out, §4.3.2)
                                                                                         ams/shells.py, ams/flow.py
 8. translate      NvcBackend(job').analyse() on ams/pp.v; backstops (§5.2); the translator's
                   warnings as vamos warnings at the user's file:line (T4, §7)
 9. cut analysis   vhdl.parse; cut.analyse(…, pp=, directions=); cut.assign_roles (disable_ie first,
                   remove_d2a after) (§5.3–5.4)                                          ams/vhdl.py, ams/cut.py
10. deck           IR + xv_ instances; levels in the §3.3 order (supply traces, rules.resolve_detail);
                   names.build_bridges; Verilog-A → OSDI; emit the deck; smoke check (§4.7)
                                                                                         ams/deck.py, ams/supply.py
11. cut emit       vams_cut_pkg + one clone per variant + re-pointed parents → ams/cut.vhd;
                   the boundary file (§5.5); then the deck↔boundary agreement check (§5.7)  ams/cut.py, ams/flow.py
12. elaborate      nvc -M 2g -H 1g -a ams/cut.vhd (into nvc/work); nvc -e <top>
13. persist        TNF diagnostics (rules.unmatched); <exe>.msv/interface_element.rpt (through the
                   stage runner: a failure is "AMS compile failed at the IE report"); ams/ams.json
                   (a write failure is "AMS compile failed at the AMS plan"); the job's ams record,
                   which carries "start", the .tran TSTART (cosim prefers it to re-reading the deck)
                                                                                         ams/report.py, ams/flow.py
```

Each step prints its notes as they come (`vamos: ` + the note); an error – a warning too under
`--vamos-strict` – stops the compile with `AmsError` ("AMS compile failed at <stage>"). On success the flow
prints `vamos: AMS: <n> SPICE instance(s), <n> analog node(s), <n> bridge(s); <engine> deck <path>`.

Runtime (`./simv`, §6): `backends/cosim.py` runs nvc in a fresh per-run directory, classifies the end of the
run, checks and moves the rawfile to `<prefix>.raw`.

### 1.1 daidir layout and the job record

No AMS file goes under `<daidir>/nvc/`: iverilog-sv2ghdl deletes that directory (`rm -rf $OUTDIR`) on every
translation. The one AMS addition there is step 12's analysis of `ams/cut.vhd` into `nvc/work`, which comes
after the translation. `<daidir>/ams/` is cleared at the start of every AMS compile (`layout.reset`, which
recreates `ams/`, `ams/deck/` and `ams/va/`).

| path (`vamos/ams/layout.py`) | contents |
|---|---|
| `nvc/` | iverilog-sv2ghdl output: `design.vhd`, `_pp.v`/`_norm.sv` (the translated text; tgt-vhdl's `-- Declared at` comments point into it), `_metadata`, `iverilog.log` (T4), the `work` library; `_mods_design.vhd` and `_mods_analysis.log` when nvc could not analyse the module-by-module translation (T4, round 6); `_sv2vhdl_cache/`, the resolver plugin's cache (§6) |
| `ams/pp.orig.v` | the preprocessed sources (no `` `line `` directives; their origin map is kept in memory, §1.2), with VCS's `-v` rule applied (§1.2) |
| `ams/vamos_prelude.v` | the `` `timescale `` prelude, when `-timescale` or `-override_timescale` gives one |
| `ams/pp.v` | `pp.orig.v` with the instantiated multi-view cells blanked (same line numbers) and the shells appended |
| `ams/cut.vhd` | `vams_cut_pkg`, clone entities/architectures, re-pointed parent architectures |
| `ams/deck/vamos.sim` or `vamos.cir` | the flattened deck (includes inlined) |
| `ams/deck/smoke.sim` + `smoke.log`, or `smoke.cir` + `smoke.norun.log` + `smoke.log` | the smoke check (§4.7) |
| `ams/va/<k>_<stem>.va`, `.osdi` | VACASK only: `0_vamos_ie` and the user `.hdl` files, compiled at compile time |
| `ams/vamos.boundary` | the boundary file |
| `ams/ams.json` | the plan (§5.7): cells, instances, nodes, bridges |
| `pp/pp.v`, `pp/vamos_prelude.v` | plain `vcs` mode: the preprocessed sources (the `-timescale`/`-override_timescale` prelude beside them), with VCS's `-v` rule applied (`verilog_ports.library_rule`, §1.2); what every plain compile translates (§8) |
| `nvc/vamos_tops.vhd` | plain `vcs` mode with several top-level modules: the entity over them (§1.4) |
| `vamos.job.json`, `vamos.tools.json` | the job, and the tools used with their versions (simv's provenance header); `vamos.job.json` is written last (a `.vamos-tmp` file and `os.replace`), only by a compile that succeeds |
| `vamos.srclines.json` | the preprocessor's line map (`backends/nvc.write_source_lines`: the display file names, and per `pp` line its file and line), written by every compile right after the preprocess; the run's output filter names the user's file:line through it (§6, round-6 repair) |
| `<exe>.msv/interface_element.rpt` | the IE report (§3.6) |

`vcs.invalidate` runs before anything touches the daidir: it replaces `./simv` with a stub that refuses to
run ("vamos: error: <exe>: the last compile into <daidir> failed or was interrupted, so there is nothing to
run; compile again", exit 1) and removes `vamos.job.json` and `<exe>.msv/interface_element.rpt`, as VCS
disables the old simv when it starts relinking. A command-line error (a source that cannot be opened, a bad
option, a `--vamos-strict` failure) stops before that, so the previous build stays runnable. A first compile
(no `./simv` before it) that fails removes the stub again: no `./simv`, as under VCS. The real stub
(`vcs.write_stub`) runs the daidir next to itself, as VCS's simv does: `$(dirname $0)/<daidir>`, or, for a
symlink whose directory holds no daidir, the daidir beside its `readlink -f` target, made absolute; so a
copied or moved `simv` + daidir pair runs its own build (the VACASK deck loads its .osdi files relative to
itself, §4.7).

`Job` (phase 0) has `schema` (2), `ams_control` (the parse-time request: the control file, `""` =
`vcsAD.init`; set by `-ad`/`+ad`/`vcs-ams`), `ams` (the compile-to-run record, written only in step 13),
`precision` (set whenever the preprocess ran: every AMS compile, and every plain compile whose preprocess
succeeded) and `override_timescale` (the `-override_timescale` value, read by the preprocess and the
precheck; the translation runs with it cleared, because the rewritten text carries it). `Job.from_json`
raises `JobVersionError` for a newer schema or an unknown key (a daidir without `schema` loads as schema 1),
and `simv.run_daidir` reports it as "<daidir>: compiled by a newer vamos (schema N[, unknown <keys>]);
recompile", a `TypeError` as "<daidir> was written by an incompatible vamos (<why>); recompile", a
missing record of an existing daidir as "<daidir> holds no finished compile (the last compile failed or was
interrupted, or the directory is incomplete); compile again", and a missing directory or an unreadable
record as "<daidir> is not a vamos simulation directory (<why>)". The dispositions of
`Job.unmapped` are `ignored`, `noted`, `unsupported`, `unknown` and `inapplicable` (a `--vamos-*` option with
no effect in this invocation, §8: warned, counted by `--vamos-strict`). The `ams` record, version 1, paths
relative to the daidir (`osdi` is empty for Xyce, which compiles Verilog-A itself):

```json
{"version": 1, "engine": "vacask", "deck": "ams/deck/vamos.sim", "boundary": "ams/vamos.boundary",
 "analysis": "vamos_tran", "raw": "vamos_tran.raw", "stop": 1e-06, "start": 0.0,
 "stop_synthesized": false, "out_prefix": "vamos_ams", "osdi": ["ams/va/0_vamos_ie.osdi"], "abi": 2}
```

`backends/cosim.py` reads only this record, `job.precision`, `job.tops[0]` and the files it names. `start`
is the `.tran` TSTART (0 when the deck has none; `DeckResult.start`), which cosim prefers; for a record
without it, from a compile before the repair round, cosim reads TSTART from the emitted deck
(`cosim.tran_start`, §6).

### 1.2 Time units

Digital time is coupled to absolute analog time, so a wrong time unit is a silent mis-simulation.
- **Preprocessing** (`verilog_ports.preprocess`): `iverilog -E -g2012` runs once over the prelude, the
  sources and the `-v` files, with the job's `-D`/`-I`, in the job's cwd. Plain `-E` emits no `` `line ``
  directives and does not keep each file's line numbers (an `` `include `` or a multi-line macro shifts
  them), so `-BP<dir>` points the driver at a one-line wrapper that runs `ivlpp` with its own `-L`; the
  `` `line `` directives are stripped from the text written out (which differs from plain `-E` output only in
  blank lines) and kept as a per-line origin map, so every diagnostic names the user's file:line
  (`PP.origin`). Without the wrapper (no `ivlpp` path in `iverilog -v -E`) origins fall back to `pp.orig.v`
  lines, with a note, and modules defined only in `-v` files can no longer be told apart, so they count as
  roots (§1.4) and can give the "several top-level modules" error. An undefined macro is an error in AMS mode
  (iverilog only warns and assumes it null) and a warning in plain mode.
- **`-v` files** are read as plain sources after the sources, in the preprocess, the precheck and the
  translation alike (iverilog's `-l` would preprocess each library file on its own, without the sources'
  macros); the precheck passes `-s <top>`, so unused `-v` modules are not elaborated. Both modes apply VCS's
  library rule to the preprocessed stream (`verilog_ports.library_rule`; `vcs.library_rule` delegates to it):
  a `-v` module is used only when no source file defines it, among `-v` files the first definition wins, and
  every other copy is blanked (lines kept); two definitions in source files are left for iverilog to report.
  AMS mode applies it in place (`verilog_ports.apply_library_rule`, §1 step 3: `pp.orig.v` rewritten, the
  module index rebuilt, `pp.library_masked` set), and the precheck then runs on the masked `pp.orig.v`
  instead of the original files, which would define the module twice ("already declared"), its lines
  reported at the user's file:line through the origin map (`precheck(pp=)`).
- `-override_timescale=u/p` is **mapped** in both personalities: the preprocess step runs (plain `vcs` mode
  writes `<daidir>/pp/pp.v` and translates it, as every plain compile does, §8); every `` `timescale ``
  directive and every `timeunit`/`timeprecision` declaration is rewritten to `u/p` (line count kept; a
  `` `resetall `` alone on its line becomes `` `resetall `timescale u/p ``, and code after a `` `resetall `` on the same line is a
  warning), and the prelude `vamos_prelude.v` carries `` `timescale u/p `` for text ahead of the first
  directive. A `-timescale` that differs is superseded, with a note.
- `-timescale=u/p` keeps its meaning, the prelude only (`vamos_prelude.v`, in AMS and plain mode alike).
- Each module's unit and precision follow IEEE 1800 §3.14.2.3: its own `timeunit`/`timeprecision`, else the
  last `` `timescale `` (a `` `resetall `` clears it), else a compilation-unit `timeunit`/`timeprecision`.
- tgt-vhdl emits one tick of the design's finest precision at its true SI size for every precision of 1 ms
  or finer: `vhdl_tick_mult` units of `vhdl_tick_unit` (T11). A 10 ps tick is `10 ps`, a 100 ps tick
  `100 ps` and a 10 ns tick `10 ns`, and `$time`, `$simtime` and `$realtime` divide by the same base. It
  **compresses** the time base only for a precision coarser than 1 ms, where one tick becomes `1 ms`.
  sv2vhdl-modules translates modules one at a time. Digital time is the analog engine's absolute time, so in
  AMS mode every module must end up with a unit and a precision ≤ 1 ms, else an error naming it: "module <m>
  has no time unit (no \`timescale, timeunit); give -timescale or -override_timescale with a precision <=
  1ms", "module <m>: digital precision <p> is coarser than 1 ms; give …" (an `-override_timescale` that is
  itself coarser is one error on the option). A module defined only in a `-v` file counts only if something
  instantiates it. (VCS would default to 1s/1s; vamos refuses rather than compress. T6 in §7 would lift
  this.) Until phase 5, a precision of 10 or 100 of a unit (1ns/10ps, 1ns/100ps, 10ns/10ns) was emitted as
  one unit per tick as well, so the digital ran 10× or 100× fast against the analog with no message.
- `job.precision` records the finest precision in effect (`PP.precision`); simv's `+vcs+finish+<time>` uses
  it (§8). With no recorded precision, `+vcs+finish+<time>` is a warning (an error with `--vamos-strict`)
  and is ignored.

### 1.3 Analog-access API and unsupported tasks

`pp.orig.v` is scanned (comment-, string- and attribute-aware) for `$snps_` and `$hdl_xmr` calls and for
`snps_above`/`snps_cross`/`snps_absdelta` (`verilog_ports.api_scan`). Any hit is an error naming the line.
After translation, `design.vhd` is grepped for tgt-vhdl's "Unsupported system task $x omitted here
(<file>:<line>)" and "Unsupported system function $f replaced by <v> here (<file>:<line>)" comments (T16,
§7; `verilog_ports.unsupported_tasks`), each task or function and line once, reported at the user's
file:line through the origin map:
- AMS mode: an error – anything tgt-vhdl drops or replaces stops an AMS compile: "system task $x is not
  translated (it would be dropped from the simulation)", "system function $f is not translated (it would
  return <v> in the simulation)";
- plain `vcs` mode: a warning, "system task $x is not translated: the simulation drops it", "system function
  $f is not translated: every call returns <v> in the simulation"; `--vamos-strict` makes it an error.

The VCD tasks are the exception in both modes (plain mode since round 6; AMS mode since the round-6 repair,
whose `ams/flow.py` calls `vcs.dump_request` on its own translation – before, they stopped an AMS compile):
`vcs.dump_request` records `$dumpfile` and `$dumpvars` for ./simv's waves, and `$dumpoff`, `$dumpon`,
`$dumpall`, `$dumpflush` and `$dumplimit` get notes instead of the warning or error (§8); the extended-VCD
`$dumpports` family keeps the warning or error.

T1 (§7) translates `$stop`, `$fatal`, `$error`, `$warning` and `$info`; T16 `$random`, `$urandom` and
`$urandom_range`, seeded or not; T25 `$dist_*`; T26 the file tasks and functions (`$fopen` and
`$fopenr`/`$fopenw`/`$fopena`, `$fdisplay`, `$fwrite`, `$fstrobe`, `$fmonitor` and their `b`/`h`/`o` forms,
`$fflush`, `$fclose`, `$readmemh`/`$readmemb`, `$writememh`/`$writememb`). They leave no such comment, except
the file-task forms T26 lists as untranslated. In sv2vhdl mode, which vamos always uses, no system function
is replaced by a constant any more (the last two, `$fopen` and `$urandom(seed)`, are translated); one with no
translation at all (`$fgets`, `$fscanf`, `$sformatf`, …) is "No translation for system function $f", a
translation error that defers its module (§5.2, T4).

### 1.4 Choosing the top

Never rely on iverilog-sv2ghdl's least-referenced-module guess (it picks a shell when a `use_spice` cell is
not instantiated, or when `$dumpvars(0, tb)` ties the counts).
1. `-top` if given: flow.py calls `verilog_ports.find_top`. A second `-top` is "-top: an AMS design has one
   top module; -top was given N times (a, b)" (vcs.main says so before the compile starts, for `-top a+b+`
   too); a `-top` naming no module is "-top: -top X: no module X in the Verilog sources".
2. Otherwise a structural root scan of `pp.orig.v` before shells exist (`find_roots`): module definitions
   minus every module named in an instantiation (the instantiation scanner, keywords excluded), minus every
   `use_spice`/`partition` cell (globs expanded; a cut cell is never the top), minus modules defined only in
   `-v` files (the origin map records each module's file). Exactly one root → the top. None → "no top-level
   module: …; give -top <module>"; several → "several top-level modules: <m> (<file:line>), …; give -top
   <module>".
3. `-s <top>` is always passed to iverilog-sv2ghdl. A top that is a `use_spice` cell is an error (SPICE-top).

Plain `vcs` mode (`vcs.plain_tops`) follows VCS instead: the tops are the `-top` modules (`-top a -top b` or
`-top a+b+`, duplicates dropped; one that names no module is the error "-top alhpa: no module alhpa in the
Verilog sources (did you mean alpha?); the top-level modules are: alpha, beta"), else every structural root
(`find_roots`, modules defined only in `-v` files excluded); no root is "no top-level module: every module is
instantiated, or only in a -v library file; give -top <module>". With several tops, `vcs.wrapper_vhdl`
writes `nvc/vamos_tops.vhd`, one entity (named `vamos_tops`, or a variant that clashes with no user module or
entity) instantiating each top: inputs tied to Z (`L3D_Z`, `(others => L3D_Z)`, `'Z'`), `0.0` or `0` (another
input type is an error suggesting `-top`), outputs and inouts open. `job.tops` is then `[vamos_tops, top1, …]`; simv
runs `job.tops[0]`, and `%m` and hierarchical names print as VCS prints them, because sv2vhdl-modules
translates each module as its own root.

Backstops after translation, in `cut.analyse`: `design.vhd` has an entity whose provenance comment names the
top module ("design.vhd has no entity for the top module <top>"); every cell of the step-6 set has at least
one cut instance in the walk ("cell <c> has no instance under top <top>": an error for a SPICE-only cell, a
warning for a multi-view `use_spice` cell); the walk finds at least one cut instance ("no SPICE instance
found under top <top>").

## 2. Control file (`vcsAD.init`) – `ams/initfile.py`

### 2.1 Lexical rules
- `//` and `/* */` comments, outside double quotes (only double quotes are tracked).
- A statement ends at `;` at bracket depth 0 (quotes, `()` and `{}` are tracked). Several Synopsys examples
  omit the final `;`, so a line whose first word is a command keyword also ends the previous statement, and
  end of file ends the last one. A keyword that is a name inside a list (`set => SET,` in a `port_map`) starts
  nothing; `set` counts only as `set bus_format`; line-start recovery uses the documented command names only
  (x-heep's cell `ams_adc_1b` is no `ams_*` command).
- A command keyword at the start of a line while depth > 0, or end of file at depth > 0, is an error
  "unterminated '(' in <cmd> at file:line"; the broken statement is dropped and parsing resumes at that
  keyword (the rest of the file is never swallowed).
- `netlist_commands_begin` … `netlist_commands_end` and `xa_commands_begin` … `xa_commands_end` are raw
  blocks: the begin statement ends at its optional `;` or at the end of its line, and text after that `;` is
  the first raw line; the block ends at the first line whose first word is the `*_end` keyword (its `;`
  optional, the rest of that line ordinary control text); lines are kept verbatim (comments included) with
  their origins; a missing `*_end` is an error.
- `` `include "f" ``: quotes and `;` optional, the statement ends at the end of its line; `$VAR`/`${VAR}`
  expanded from the environment (an unset one is an error); a relative path resolves against the directory
  vcs runs in, for nested includes too (PAMS p123); an include cycle is an error; included statements take
  effect in place.
- `snps_vcsAD.ini` is read first, from the cwd, else `$HOME` (`initfile.find_ini`). A file listed twice is
  read once (note). A command read later wins.
- Numbers: `netlist.numbers.parse_number` (engineering suffixes, trailing units; correctly rounded like
  `expr.number`, §4.1); `%` means a fraction of the reference supply span (§3.3).
- Command keywords are case-insensitive; the `ams_*`, `ie_*_rpt` and `shadow_file*` families are matched by
  prefix. An unknown command is an error naming `file:line`.
- Names are matched case-insensitively unless `set_sim_case` (XA cfg) says `sensitive`.
- Nothing raises: every problem is a `Note` in `cfg.notes` (origins relative to the cwd, `vcsAD.init:7`),
  and a statement with an error is dropped whole, so later stages see only values that parse.

### 2.2 Commands

| command | v1 handling |
|---|---|
| `choose <engine> [netlists] [options];` | Engines `xa finesim primesim hsim nanosim` → the default engine; `vacask`/`xyce` (vamos extension) → that engine (lowercased). A later `choose` replaces an earlier one (note). Netlists: `-n f`, `-hspice f`, `-nspice f` (dialect `spice`: parsed as HSPICE in v1, warning) or a positional path; several allowed; kept as written and resolved by `spice.parse` with the `choose`'s origin (§4.3.1). Flags: `-spice` (a mode flag, never takes a value: a note, the dialect stays HSPICE) and `-skipdc` (→ `uic`). Options taking a value: `-c`, `-C` (each an XA cfg; `Choose.cfgs` keeps the spelling; the path is read with `$VAR`/`${VAR}` expanded from the environment – an unset one is the error "choose: XA cfg <f>: environment variable <V> is not set" – and a leading `~` expanded, then looked up in the cwd, then beside the control file holding the `choose`; all mined for `probe_waveform_voltage`/`_current` and `set_sim_case`; any other XA command is a note), `-o` (output prefix, as written), `-wavefmt` (note), `-afile` (warning: ignoring an extra FineSim input can change the result). Any other option is a warning and takes no value. No `choose` at all is an error ("the mixed-signal control file must contain at least choose", from `initfile`). |
| `use_spice -cell c[:s]… [-inst p…] [port_map(...)] [port_index_order(...)];` | Cells join the cut set; `c:s` binds Verilog cell `c` to subckt `s` (default: same name, matched case-insensitively); `*` in a cell name is a glob; `-inst` paths have Verilog escapes undone (`\i1<1> .i2` → `i1<1>.i2`). On a **multi-view** cell, `-inst` takes full Verilog paths (`*` allowed); each `-inst` statement may carry its own `c:s` and `port_map` (kept per instance, `CutInstance.portmap`/`subckt`). A covering `-inst` statement takes precedence over the statements without `-inst` (among those, the last wins); an instance covered by two or more `-inst` statements is an error; an instance no statement covers is an error only for a multi-view cell that has `-inst` statements and no statement without `-inst` (§5.4). On a **SPICE-only** cell, `-inst` is a note (every instance is SPICE already); per-instance subckts or port maps are an error (one shell per cell); its `port_map` may only rename whole ports (`v => s`: the shell port is called `v`), so `v => snps_open`, bit items and `* => snps_open` are errors and `* => snps_by_position` is a note (its ports follow the subckt; `* => snps_by_name`, the default, is silent). `port_map` items: `v => s`, `v[i] => s`, `v => snps_open`, `* => snps_by_name / snps_by_position / snps_open`; any other form (`v => s[range]`, `v => {a, b}`, `v[0:2] => …`) or an escaped Verilog name as the source is an error. `port_index_order (*=>same, a=>dec, s=>inc)`: §5.1. |
| `partition -cell c…;` | same as `use_spice -cell` (NanoSim era) |
| `bus_format <fmt>…;` / `set bus_format <fmt>;` | SPICE bus-bit patterns (`<%d>`, `[%d]`, `_%d`). Default `[%d]`. A later one replaces an earlier one (a note when they differ). |
| `port_dir -cell c (input a, b; output y; inout z);` | Directions of SPICE-only ports. The body is split into groups on `;`; `-cell name(` without a space is accepted; the final `;` is optional; `*` is a glob in cell and port names (PAMS p259). A non-glob port name must be a subckt port (after bus grouping; a bus member names its bus), else an error – checked only when the cell name has no `*`. A repeated `port_dir` for one cell merges port by port. On a multi-view cell it is ignored with a note (the Verilog view declares the directions). |
| `port_connect -cell c [-inst i] (p => net, q => snps_open, real p => net);` | The SPICE port is wired to a deck net and is not a shell port. `-inst` may come before the group or inside it (PAMS p256); one `PortConnect` per `-inst`. Per cut instance (deck.py), the last `-inst` statement whose `-cell` matches the Verilog cell or its subckt and whose `-inst` matches the instance's Verilog path wins over the cell-level connection (`CutCell.connects`: the last cell-level statement), whatever the file order; a later statement giving a port another net is a note, and deck.py marks `port_connect_inst#i` for each `-inst` statement it applies. On a SPICE-only cell an `-inst`-only port must be covered for every instance, else "port P of subckt S is port_connect'ed only by -inst statements that do not match I (<stmt> at <origin>); add a cell-level port_connect -cell C or an -inst for I". `net` resolves, in order, to: `snps_open` (any case); an analog node's name or alias (so `<cut inst path>.<port>` of a port a Verilog net connects); ground (the alias fold, with or without `<top>.`); a `.global` net or a net of a top-level element of the netlist (with or without `<top>.`; case-insensitive unless `set_sim_case sensitive`). Anything else is an error at the statement's origin, reported once per statement: "<stmt>: X is node N inside SPICE instance I; … make N a port of its subckt and connect it from Verilog, or declare it .global" (PAMS p258's form is not in v1), or "<stmt>: no net X in the deck; … (a Verilog-only net is not in v1); nearest: …"; a misspelt net never becomes a new floating node. `real p => net` is "<stmt>: real-number interface elements are not supported in v1". Ground (§4.3.4): a ground-alias port to a ground net is a no-op; to `snps_open`, a warning (it stays ground); to any other net, the error "port gnd of subckt S is a ground alias, which is ground inside the subckt, so it cannot be connected to N; connect it to ground or rename the port"; a port connected to a ground-alias net (`vss => gnd`) is wired to `0`. A statement naming a port this instance's subckt lacks is an error. A `port_connect`ed net that no supply trace reaches and no source element (V I E F G H B or Verilog-A) touches is the warning "<stmt>: no voltage source reaches N (port P of I): the port floats". cut.py (`_passive`) matches `-cell` against the Verilog cell or its subckt too, so a multi-view statement naming the subckt leaves its bit passive and deck.py wires it; deck.py's error "... the digital cut bridged that bit (an internal disagreement between the cut and the deck; please report it)" is a backstop. |
| `a2d …;` / `d2a …;` | IE rules (§3). Every documented key is parsed; a rule with any error is dropped (§3.5). |
| `remove_d2a [dc=v] node=…;` | Removes only a D2A (§5.4); with `dc=v`, an ideal DC source of `v` replaces it. The last matching command wins. `node=*` is an error (PAMS p210). |
| `disable_ie node=…;` | No IE on that net: the analog node stays, the digital side of the cut ports is not driven (warning). `node=*` is an error (PAMS p264). |
| `netlist_commands_begin; … netlist_commands_end;` | Raw SPICE lines, parsed as a separate fragment after every choose netlist (never textually appended, so no `.end` can swallow them). Origin `vcsAD.init:<line>`; their `.inc`/`.lib`/`.hdl`/`.option search` resolve beside that control file (§4.3.1). |
| `transient_analysis $finish \| $stop;` | Note; both already end the run (§6). |
| `ie_reference_voltage node=n [voltage=v];` | Reference supply for every IE whose supply trace reaches `n` (§3.3 step 2): `n` is a trace hit outranking the sources of its stage. Several entries resolving to one net: the last applies, the earlier ones get a note. Without `voltage=`, the value is `n`'s constant value; a non-constant `n` is an error naming the entry wherever a level uses it (dynamic reference). An entry whose node is in the deck is never TNF; one no trace reaches is a warning. A wildcard is an error. |
| `ie_reference_voltage skip_node=n;` | `n` is a stop node for every supply trace (§3.3); kept in `AmsConfig.ref_voltages` with volts = NaN (`initfile.SKIP`); never TNF, but a `skip_node` that names no deck net is an error ("skip_node <n> is not a deck node", deck.py). |
| `downgrade_to_warn <id>…;` / `upgrade_to_error <id>…;` | Honoured for `MSV-IE-OPT-TNF` (the last one wins); other ids are notes. |
| `use_verilog`, `use_vcs`, `use_vhdl`, `spice_top`, `use_veriloga`, `dynamic_supply_filter` | Error (SPICE-top/VHDL/Verilog-A binding, dynamic supplies: not in v1). |
| `map_by_node r=<ohms> node=<pattern>;` | The D2A series resistance (PAMS p243): an `IeRule` of kind `map_by_node` in `AmsConfig.rules`, in file order with the a2d/d2a rules (§3.4). `r=` is a positive resistance; `node=` is the only selector; any other key is an error. |
| `e2r`, `r2e`, `e2n`, `n2e`, `e2u`, `u2e`, `udn_e2n`, `udn_n2e`, `nettype_map`, `rt_*`, `insert_cell`, `rmap_file`, `ams_*` | Warning: ignored (`rmap_file`: the resistance map is not read; a D2A's series resistance is 500.7 Ω unless `map_by_node` sets it). |
| `report_option`, `ie_*_rpt`, `param_pass`, `resolve_x_inst_prefix`, `form_spice_bus`, `optimize_shadowfile`, `shadow_file*`, `duplicate_net_inst_name`, `skip_xmr_name_check`, `print_ie_res`, `gen_spice_wrapper` | Note. |

### 2.3 Data and API

The dataclasses are frozen in **`vamos/ams/config.py`** (phase 0, unchanged): `Choose` (engine, netlists,
cfgs, out_prefix, uic, dialect, options, origin), `UseSpice`, `PortDir`, `PortConnect`, `IeRule`,
`AmsConfig`. As filled: `choose.dialect` is `'spice'` only with `-nspice`; `choose.options` lists `-wavefmt
v`, `-afile v`, `-spice` and unknown flags; `use_spice` cells are `(cell, subckt or '')`; `port_dirs` are keyed
by `cell.lower()` with lowercased ports; rule keys are lowercase and values raw strings (quotes stripped,
flags `''`); `rules` also holds the `map_by_node` commands (kind `'map_by_node'`, params `{'r': raw}`,
`node=` only); `remove_d2a` is `(node, dc or None, origin)`; `ref_voltages` holds both `ie_reference_voltage`
forms (`(node, volts or None, origin)`, and `(node, SKIP, origin)` for `skip_node=`; `rules.ref_nodes()`
separates them); `severity_overrides` is `{TNF: 'warning'|'error'}`; `xa` is always set
(`{'probe_v', 'probe_i', 'case'}`, mined from every `-c`/`-C` cfg and then the `xa_commands` blocks); `files`
lists every control file read, includes too, as absolute paths. `initfile.py`:

```python
INI_NAME = "snps_vcsAD.ini";  DEFAULT_CONTROL = "vcsAD.init";  SKIP = float("nan")
def find_ini(cwd: str, env: Mapping[str, str]) -> Optional[str]
def parse_control(paths: List[str], cwd: str, env: Mapping[str, str]) -> AmsConfig   # [ini?, adfile]
def mine_xa_cfg(path: str, label: Optional[str] = None) -> Dict[str, object]
    # {'probe_v': [(pattern, origin)], 'probe_i': [...], 'case': 'lower'|'upper'|'sensitive'|None, 'notes': [...]}
def lex(text, label, notes) -> List[Statement];  tokens(text);  Statement(keyword, text, origin, raw)
```

The caller stops on `notes.has_errors(cfg.notes)`. `Note` (`vamos/notes.py`) is shared by every module:
`Note(severity, origin, message)`. flow.py prints the control-file notes at step 1 and applies
`--vamos-strict` to every AMS note.

**Python rules** for every new module: `from __future__ import annotations`; Python 3.9 (no `match`, no
`X | Y` even in annotations of the frozen modules, no dataclass `kw_only`/`slots`, no `zip(strict=)`, no
`typing.TypeAlias`/`ParamSpec`/`Self`, no `enum.StrEnum`, no `itertools.pairwise`, no regex atomic groups or
possessive quantifiers); standard library only. Modules under `vamos/` are never run as scripts. The suite
runs under Cygwin Python 3.9.16 (the stack tests skip) and WSL Python 3.14.4.

## 3. Interface elements – `ams/rules.py`, `ams/supply.py`, `ams/deck.py`

### 3.1 Names and aliases

An interface element sits on an **analog node**: one digital net bit as traced by the cut (§5.4). Each node
has:
- a **canonical name** in VCS form: the cut instance's Verilog hierarchical path, a dot, and the SPICE port
  name of that bit in the netlist's original spelling (`Netlist.spelling`) with `bus_format` applied (e.g.
  `top.dut.a_3`, `tb.adc.sel<1>`); if the net touches several cut ports, the one whose instance path has the
  fewest labels wins, ties to walk order (§5.4). (The cut looks a port up in `Netlist.spelling` by its
  folded IR name, the key spice.py uses, so under `set_sim_case upper` or `sensitive` the original spelling
  is shown (round 6; it showed the folded name, `VIN`). `Netlist.spelling` is global, so two subckts whose
  ports differ only in case shared one spelling; since the round-6 repair the IR keeps each subckt's header
  spelling as `Subckt.spelling` (spice.py `Parser.header`), which `cut._port_spelling` prefers.)
- **aliases**: every cut port on the net in three spellings (SPICE `a_3`, Verilog `a[3]`, the bare port `a`
  for whole-bus rules), plus every parent net the trace walked through, as `<Verilog scope path>.<signal>`
  and, for vectors, `<…>.<signal>[<i>]`.
  - Parent bit indices use the parent signal's **Verilog** declared range, read from the declaration its
    `-- Declared at <file>:<line>` comment points to (the verilog_ports declaration scan, `pp=`, else
    `nvc/_norm.sv`, whose lines are `pp.v`'s while sv-normalize keeps the line count); if it cannot be read,
    only the bare-signal alias is emitted, with a note. (tgt-vhdl declares every vector `(w-1 downto 0)`, so
    VHDL indices are offsets, never Verilog indices.) Verilog names undo tgt-vhdl's `make_safe_name` and its
    `_<n>` case-clash suffix.
  - For alias collection only, the trace sees through tgt-vhdl's copy temporaries: when a cut port's actual
    is an `LPM_*`/`tmp_*` signal copied from or to `sig(i)` or a slice, the aliases come from `sig` with the
    index mapped, and the trace continues from `sig`. A temporary is never an alias.

Verilog instance paths come from the translator's instance-name comments (§7, T3); without them, VHDL labels
stand in, with a warning.

### 3.2 Selectors and merging
- Patterns are VCS globs (`vamos/ams/globs.py`, phase 0): only `*` is special; `[`, `]`, `<`, `>` and `.`
  are literal (never fnmatch).
- `node=pattern`: a wildcard pattern matches canonical names only; a pattern without `*` matches any alias.
- `cell=c port=p`, `inst=path port=p`: match if any cut port on the net matches; `port=` matches any of the
  three spellings; an a2d `except_port=` takes ports back out of a `port=` match.
- Selector shape: a rule with no selector is an error (the message suggests `node=*`); so are
  `cell=`/`inst=` without `port=`, `port=` without `cell=`/`inst=`, `cell=` with `inst=`, and `node=` with
  any of `cell=`, `inst=`, `port=`. A key given twice in one statement is a note (the last value wins).
- `library=` is a note and is ignored; when rules name two or more different libraries it is a warning,
  because ignoring it then changes which rule wins.
- Every matching rule applies in file order (`snps_vcsAD.ini` first, `` `include `` files in place), later
  rules overriding earlier ones key by key; `rf_time` sets both ramps and `delay` both delays at their place
  in that order (`rf_time` followed by `rise_time` keeps the fall time).
- **Role filter:** `resolve(…, role=node.role)` applies and marks only the rule kinds the node's role uses:
  D2A and POWERNET → d2a; A2D → a2d; BIDIR → both; DISABLED and REMOVED mark the rules that name them (the
  target exists) and apply none; THROUGH, NONE, RD2A and RA2D use none. So a d2a rule that names only an A2D
  node is a TNF, not a silent no-op. `map_by_node` counts as a d2a rule: D2A and BIDIR nodes apply it; on a
  supply net (POWERNET) or a `d2a powernet` D2A it has no effect and warns once per command ("map_by_node
  r=<r> has no effect on <node>: a supply net | d2a powernet node is an ideal source with no series
  resistance"); one that names only A2D nodes is TNF; DISABLED/REMOVED nodes mark it.
- `remove_d2a` and `disable_ie` are two ordered lists; for `remove_d2a`, the last match wins; every matching
  `disable_ie` is marked. `cut.assign_roles` asks both for every net (canonical name first, then the
  aliases).
- Whoever matches a selector marks it in the shared `RuleHits` (`model.py`; keys `rule#3`, `remove_d2a#0`,
  `disable_ie#1`, `ref_voltage#0`, `vdd#3`, `vss#3`, `use_spice_inst#2.0`, `port_connect_inst#1`): `resolve`
  marks `rule#i`, and `vdd#i`/`vss#i` when that rule's supply name exists; `removal`, `disabled`; cut.analyse
  marks `use_spice_inst#i.j` and `port_connect_inst#i` (the SPICE-only "-inst is a note" case included);
  deck.py marks `ref_voltage#i` once the node resolves in the deck. `resolve`, `removal` and `disabled` also
  record each node's canonical name under the reserved `RuleHits.counts` key prefix `rules.SEEN = "node:"`,
  and `resolve` each cut cell under `SEEN_CELL = "cell:"` (model.py's `RuleHits` has no separate registry).
  A rule whose supply name is missing is not applied (VCS ignores the command) and its `vdd=`/`vss=` is TNF.
  `rules.unmatched(cfg, hits, names=None)` runs at step 13 and turns every unmarked selector into
  `[MSV-IE-OPT-TNF]` (an error, or a warning when downgraded): `rule#i`; `vdd#i`/`vss#i` of a rule whose
  selector matched; `remove_d2a#i`; `disable_ie#i`; `ref_voltage#i` (skip entries excepted);
  `use_spice_inst#i.j`; `port_connect_inst#i`. Each message names up to three nearest canonical names (flow
  passes the plan's), instance paths or cells.

### 3.3 Reference supply

`%` values are fractions of the span `vss → vdd`. A net is **constant** if it is held by a constant
POWERNET node (a supply net, or a `d2a powernet` node whose digital driver is a proved constant, below), or
by an ideal V source to ground with no transient wave or one that never leaves a level (PWL with all values
equal, PULSE v1 = v2, SIN va = 0, EXP v1 = v2); its value is that level. A V source with a varying wave is
**dynamic**, whatever its `DC=` field.

The reference `(vss, vdd)` of a node is, in VCS order:
1. the merged rule's `vdd=`/`vss=` (or `vdd_port=`/`vss_port=`: that port of the matched instance, `../X` for
   the parent scope; a wildcard is matched, as VCS does (PAMS p194), against the ports of the matched
   instance's subckt, which deck.py passes as `rules.resolve_detail(..., subckt_ports=)`: exactly one match is
   that port, none is TNF, several are the error "<kind>: vdd_port=<p> matches <n> ports of <inst> (<ports>);
   name one"; a wildcard after `../` is a parse error), resolved through `dc_of` in this order
   (`deck._Ctx.resolve_net`): any canonical name or alias of an analog node; a top-level deck net, `.global`
   net or ground, with or without the `<top>.` prefix, through the ground fold (`port_connect` nets are
   top-level deck nets); `<cut instance Verilog path>.<subckt path>.<node>` inside that `xv_` instance,
   longest instance path first. A constant net gives its value; a dynamic one is an error (once per rule); a
   missing one leaves the rule unapplied, with its supply reported as TNF. Ground (a ground alias, or a
   subckt port bound to ground) is the constant 0 V (`SupplyGraph.constant_value`): `vss=0`, `vss=gnd` and a
   `vss_port=` on a grounded port were "not a constant supply".
2. `ie_reference_voltage`: every entry whose node resolves in the deck (`resolve_net`) is marked
   `ref_voltage#i`. Entries resolving to one net: the last applies (PAMS p106), and each earlier one gets the
   note "ie_reference_voltage node=A is replaced by node=B at <origin> (the same net; the last command is
   used)". The applied nodes are hits of the step-3 trace (`SupplyGraph.refs`) that outrank every source of
   their stage (PAMS p108: the reference overrides the ideal supply traced), so an entry sets the levels of
   every IE whose trace reaches its node, whether or not the trace would have found a source. A stage that
   reaches one is decided by its highest reference: `voltage=`, else the node's constant value. A reference
   with no known value decides as dynamic: the error "ie_reference_voltage node=N has no voltage= and N is not
   a constant supply, so the levels of <IEs> would follow it (dynamic references are not in v1); give
   voltage=" wherever a level uses it. At the trace's own start node it is not a hit. An entry no trace
   reaches is the warning "ie_reference_voltage node=N: no interface element's supply trace reaches N, so the
   command sets no levels …".
3. the **supply trace** (`supply.SupplyGraph`), staged as in VCS (PAMS p108), over the deck IR plus the
   `xv_` instances (X instances expanded with their port maps; `.global` nets and ground in the top scope):
   stage 1 is the node's channel-connected region (MOS and JFET drain–source channels, resistors and
   inductors, across subckt boundaries); if it holds no hit, step from each MOS gate in the region to that
   device's source and drain and repeat outward. The walk never enters node 0, a `skip_node`, or a hit node
   (a reference node is a hit, step 2; a V source to ground vamos cannot evaluate stops the walk as a hit does).
   A **hit** is a node held by an ideal V source to ground (either orientation; a constant value, or a
   dynamic source at its highest level with the warning "supply trace from <n> reached a varying source; its
   highest level <v> V is used") or a POWERNET node (a constant one at its value, a varying `d2a powernet` at
   its highest level with the same warning); bridge sources are not in the graph. The first stage with a hit
   decides: its highest hit is `vdd`, `vss = 0`;
4. the highest constant V source anywhere in the deck, or constant POWERNET node;
5. 3.3 V, the VCS fallback, as the **warning** "<node>: no supply reached from <node>: using the VCS 3.3 V
   fallback", emitted only if a level actually used the reference's vdd: a default `hiv`, or a `%` value. A
   supply net counts only the level it uses (supply1 `hiv`, supply0 `lov`), so a supply0 net warns only when
   its `lov` is a `%`.

A supply whose value is a parameter is evaluated (§4.1), subckt parameters with their X-line overrides. A V
source to ground whose value cannot be evaluated (one depending on `temper`) stops a trace
(`SupplyGraph.unevaluable_at`; `TraceResult.unevaluable` lists those of the deciding stage). When a level
uses a reference such a source could decide (it is in the deciding stage, or step 4 is reached while one
exists anywhere), the compile fails with one error per source at its origin: "V source S cannot be
evaluated (<why>): the supply trace of <IEs> reaches it, so their levels cannot be set; give those
interface elements their levels (a2d/d2a rules with hiv=, loth=/hith= or vdd=), or name their supply with
ie_reference_voltage node=<node> voltage=<v>". The step-4 form says "no supply trace of <IEs> finds a
source, and the highest deck source (the next step, §3.3) cannot be chosen". It is never the 3.3 V fallback.
Explicit levels still compile. A `vdd=`/`vss=` naming a net such a source holds is the error "<kind>:
vdd=<n> is held by V source <s> (<origin>), <why>, which cannot be evaluated, so it gives no level; give the
levels as values (hiv=/lov=, loth=/hith=), or name a constant supply" (`dc_of` status `'unevaluable'`).

**Evaluation order** (no node may depend on its own value; `deck._levels`):
- (a) every trace and step 4 see the user deck plus the `xv_` instances; the bridge sources are added later;
- (b1) supply nets (role POWERNET, `supply1`/`supply0` drivers), each resolved for the one level it uses –
  supply1 `hiv`, supply0 `lov` – from its own rule (`hiv=`/`lov=`, or `vdd=`/`vss=` resolved against (a)),
  else `ie_reference_voltage`, else its trace in (a), else steps 4–5; `node.dc` is that level;
- (b2) `d2a powernet` D2A nodes (PAMS Method #2a), whose traces see the (b1) supply nets as hits: an ideal
  source that follows the digital value. When the net's digital drivers provably hold one level for the whole
  run, it is a **constant** POWERNET at `hiv` (held 1) or `lov` (held 0), resolved for that level only;
  otherwise a **varying** POWERNET counted at `max(hiv, lov)`, with the trace warning when it decides a trace.
  A level is proved (`deck.driver_level`/`held_level`) by a lone constant literal assigned with no delay
  (`vdd <= L3D_1`), a constant port association (`.vdd(1'b1)`), a declaration initial value with no other
  source, or a static pull; the strongest driver class decides (supply > strong > weak/pull), every driver
  must be proved and that class must agree. (A per-bit initializer on a vector is not proved: varying,
  which is conservative.)
- (c) every other bridged, DISABLED or REMOVED node, whose traces see (b1) and (b2) as hits and whose `dc_of`
  sees the constant ones as constants.

### 3.4 Elements and API

The element dataclasses are frozen in `model.py`: `D2A_IE(hiv, lov, rise=1e-11, fall=1e-11, delay_rise=0,
delay_fall=0, x2v=0, powernet=False, r_series=500.7, weak_frac=500.7/3500.2)` and `A2D_IE(loth, hith,
xband=None, midv_time=None, midv_logic='X')`, `midv_logic` ∈ `'0' '1' 'X' 'Z'` (as written, upper-cased;
the cut's `MIDV_L` code 3 drives Z, so `midv_logic=Z` releases the A2D's net once the voltage has stayed in
the window for `midv_time`).

```python
Port5 = Tuple[str, str, str, str, str]      # (cell, inst path, spice port, verilog bit "a[3]", verilog port "a")
VRef = Tuple[float, float, Optional[Note]]  # (vss, vdd, the 3.3 V warning): steps 2-5
DcOf = Callable[[str], Tuple[str, Union[float, str, None]]]   # ('const', v) | ('dynamic', None) |
    # ('missing', None) | ('unevaluable', "<the V source that holds it, and why>")
def resolve(cfg, names: List[str], ports: List[Port5], vref: VRef, dc_of: DcOf, hits: RuleHits,
            role: Optional[str] = None) -> Tuple[D2A_IE, A2D_IE, List[Note]]
def resolve_detail(same arguments, subckt_ports: Optional[Callable[[str], Sequence[str]]] = None) -> Resolution
    # subckt_ports: instance path -> its subckt's SPICE ports, for a wildcard vdd_port=/vss_port= (§3.3)
    # d2a, a2d, notes, rules (indices applied), origins {'d2a.hiv': 'file:line', ...},
    # aliases (the node= values that matched: "User Specified Aliases"), used_ref
def removal(cfg, names, hits) -> Tuple[bool, Optional[float]]
def disabled(cfg, names, hits) -> bool
def ref_nodes(cfg) -> Tuple[List[Tuple[str, Optional[float], int]], List[str]]   # (entries, skip_nodes)
def unmatched(cfg, hits, names: Optional[Sequence[str]] = None) -> List[Note]
def check_rule(rule: IeRule) -> List[Note]       # parse-time selector, key and value checks (initfile)
def paste_line(kind: str, canonical: str, ie) -> str   # kind 'd2a' | 'a2d' | 'map_by_node': the one writer
                                                       # of the report's control lines
def ie_lines(role, canonical, d2a, a2d) -> List[str]   # a node's report lines by role (§3.6)
def midv_windows(ie: A2D_IE) -> Tuple[Tuple[float, float], Tuple[float, float]]   # (falling, rising)
# parse_level, parse_time, case_sensitive, node_matches, kinds_of, selector_text, is_skip;
# SEEN = "node:", SEEN_CELL = "cell:", MIN_RAMP = 1e-15
```
- `names[0]` is the canonical name, then every alias of §3.1 (all three cut-port spellings and the parent
  nets); deck.py calls `resolve_detail` with `role=node.role`.
- Defaults (VCS): `hiv = vdd`, `lov = vss`, rise and fall 10 ps, delays 0, `x2v = 0` (X → lov), series
  resistance 500.7 Ω (rmap strength 6) unless `map_by_node` sets it. `a2d` defaults to `loth = hith = 50 %`
  of the span. Levels parse as `1.2`, `1.2V`, `500mV` or `90%`; times as `1.5n`, `10ns`, `3e-10`. In a rule
  that gives `vdd=`, `vss=`, `vdd_port=` or `vss_port=`, `hiv`/`lov` (d2a) and `loth`/`hith` (a2d) must be
  percentages; an absolute level there is a parse error naming the key and file:line, as VCS rejects it
  (PAMS p190, p207). Rules still merge key by key, so an absolute `hiv` from one rule beside another rule's
  `vdd=` is applied.
- `map_by_node r=<ohms>` (PAMS p243; the last match wins): `D2A_IE.r_series = r` and `weak_frac = 1.0`, so
  r replaces the resistance-map value for every drive strength, including weak and pull drivers and a BIDIR
  node's moved pull. It has no effect on ideal sources (supply nets, `d2a powernet` D2As), with a warning
  (§3.2). The report's `levels: rule` line names it `map_by_node.r <file:line>`.
- A2D: `≤ loth` → 0, `≥ hith` → 1, in between the previous value is held. After `midv_time` inside the
  **window** the value becomes `midv_logic`. With `hith_hys = hith − (hith − loth)/xband` and
  `loth_hys = loth + (hith − loth)/xband` (PAMS p189), a falling voltage is in the window between `loth` and
  `hith_hys`, a rising one between `loth_hys` and `hith`; without `xband`, `hith_hys = hith` and
  `loth_hys = loth`. 0 and 1 are produced only at `loth` and `hith`.
- `rf_time` sets rise and fall; `delay` sets both delays; `rise_time`/`fall_time`/`rise_delay`/`fall_delay`
  set one each; `rf_time` with `rise_time`/`fall_time`, `delay` with `rise_delay`/`fall_delay`, and `vdd=`
  with `vdd_port=` in one rule are errors; a negative time is an error; a ramp below 1 fs is a note at parse
  time and is clamped to 1 fs; `loth` above `hith` once resolved is an error.
- `x2v` (the voltage for an X, U or W input): 0 → lov, 1 → hiv, 2 → (hiv+lov)/2, 3 → hold the previous
  voltage, 4 → the inverse of the previous digital input (PAMS p206): hiv after a 0 or L, lov after a 1 or H,
  otherwise (after Z or X, or at start-up) the previous voltage, lov at start-up. On a BIDIR node with no
  strong driver the input counts as Z.
- `powernet` on a D2A: an ideal source with no series resistance, no gating and no shunt, following the
  digital value (§4.7); it is a supply node in the §3.3 order. A supply net (`supply0`/`supply1`) is a
  constant POWERNET node by itself (§5.4).

### 3.5 Unsupported keys
Checked at parse time (`rules.check_rule`; a rule with an error is not kept). `minv`, `minv_logic`,
`minv_analog`, `vdd_filter`: error. `hiz_on`/`hiz_off` (always, since the node's role is unknown at parse
time), `strength=` other than `rmap`, `ceff=` and `queue=nonblocking` (the VCS 2019 User Guide key;
`blocking` is silent): warning, ignored. `xband` without `midv_time`: note. Wildcard `vdd=`/`vss=`:
error (`dc_of` takes one concrete name); a wildcard `vdd_port=`/`vss_port=` is matched against the
instance's subckt ports (§3.3), except after `../` (error). An absolute
`hiv`/`lov`/`loth`/`hith` in a rule with `vdd=`/`vss=`/`vdd_port=`/`vss_port=`: error ("<kind>: hiv=1.2
cannot be an absolute level in a rule with vdd=: the levels are percentages of that supply (PAMS p190,
p207)"). `map_by_node`: `r=` must be a positive resistance; `node=` only (`cell=`/`inst=`/`port=`/`library=`
are errors); no other key. Any other undocumented key: error. The keys are PAMS Appendix A's, plus `queue=`.

### 3.6 IE report (`ams/report.py`)

`<exe>.msv/interface_element.rpt` is UTF-8 whatever the locale (its direction comments carry `→`). It is
written through a `.tmp` file and `os.replace`; a write that fails raises `report.ReportError`, both a
`NoteError` and a `BackendError` naming the file; flow.py runs `report.write` as a stage (§1 step 13), so the
compile prints "vamos: error: <path>: cannot write the interface-element report: <reason>", then "vamos:
error: AMS compile failed at the IE report" and the `compile_failed` banner, and no `.tmp` is left. The report
starts with two comment lines naming the engine, then has one entry per analog node, in plan order. Its
control-file lines come from one writer, `rules.paste_line`, chosen per node by `rules.ie_lines`:
- D2A and BIDIR: `d2a [powernet] hiv=<v> lov=<v> (rf_time=<s> | rise_time=<s> fall_time=<s>) [delay=<s> |
  rise_delay=<s> fall_delay=<s>] x2v=<n> node=<canonical>;` (a powernet line keeps its ramps, delays and
  `x2v`: a `d2a powernet` D2A follows its digital value with them). Then `map_by_node r=<ohms>
  node=<canonical>;` when the D2A is gated (BIDIR, or not powernet) and its resistance or weak fraction is not
  the default.
- A2D and BIDIR: `a2d loth=<v> hith=<v> [xband=<x>] [midv_time=<s>] [midv_logic=<l>] node=<canonical>;`
  (`midv_logic` is written with `midv_time`, or whenever it is not X).
- POWERNET (`supply1`/`supply0` nets): `d2a powernet hiv=<v> lov=<v> rf_time=<s> x2v=<n> node=<canonical>;`
  with the merged levels (VCS treats supply nets as `d2a powernet` IEs, PAMS p25, p205; the net sits at
  `hiv` for supply1, `lov` for supply0), followed by `// node=<c>: supply net, ideal <v> V source
  (powernet)`.

Levels are in volts, times in seconds and r in ohms, in `numbers.fmt` spelling, e.g. `d2a hiv=1.8 lov=0.0
rf_time=1e-11 x2v=0 node=tb.adc.sel<1>;`. Pasting every report control line back in place of the control
file's a2d/d2a/map_by_node rules gives the identical deck, cut VHDL, boundary file and report lines, with no
TNF and no 3.3 V fallback (tested on both engines). A node with no IE gets one comment line instead:
- REMOVED `// node=<c>: remove_d2a dc=<v>` or `… remove_d2a (left to the analog side)`; DISABLED `// node=<c>:
  disable_ie (no interface element)`; RD2A/RA2D `// node=<c>: real port, ideal source from the digital value`
  / `… node voltage deposited into the digital`; THROUGH `// node=<c>: through-net (analog only, no interface
  element)`; NONE `// node=<c>: no interface element (unconnected | not driven)`.

Every bridged entry (D2A, A2D, BIDIR, RD2A, RA2D) continues with:
- `// Top-Net <alias>`: the alias with the fewest `.` (PAMS p177); the bare bus alias is dropped when its bit
  alias is present (`tb.arr[0]`, not `tb.arr`; VCS prints the bit, PAMS p270);
- `// All Boundary Nets <inst path>.<SPICE port> …`, every cut port on the node.

Then every node's own lines (`// ` + line). From the cut: `host <bit>`; `direction: …` (below);
`passive bits: …`; `pull-up|pull-down moved into the analog deck (<origin>)`; `supply net (supply1|supply0)`;
`unconnected bit <b> (private node)` or `not driven`; `disable_ie`; `remove_d2a` or `remove_d2a dc=<v>`;
`port buffer: input port <p>, fed one way from variable <v>, is also driven inside <scope> (a net of its
own)` (§5.4). From the deck: `levels: rule <kind.key> <file:line>, …` (`map_by_node.r` among the keys);
`levels: reference ie_reference_voltage <n> | trace <source> via <path> | highest
deck source <name> | 3.3 V fallback`; `levels: default <key>=<v> (the reference supply is not used)` (a supply
net whose level did not need the reference); `supply: constant <v> V (the digital side holds <0|1>)` or
`supply: follows a digital value that is not a proved constant; traces count its highest level <v> V`
(`d2a powernet`); `User Specified Aliases <node= values that matched>`. Last, `// shunt rsh_<n> 1e12 ohm to
ground` when the node has a shunt.

Direction lines: `direction: auto→input|output (variable actual) <bit>`; `direction: auto→inout (VCS
default; port_dir is faster) <bit>`; for an auto port that probe step 2b made an input, the probe's reason in
place of "variable actual" (`auto→input (wrap_e.u (tb.sv:13), connected to input port wrap_e.a, which tb.we
(tb.sv:7) connects to variable clk) tb.we.u.a`); for a cut port behind a joined one-way port buffer,
`direction: inout→input (one-way port buffer: input port tb.we.a fed from variable tb.clk) <bit>`
(`auto→inout→input` for an auto port). Each instance shows its own step-2b reason when its own wrapper
chain made the port an input (`shells.Direction.by_chain`, keyed by the instance's static chain of
(module, instance) pairs; `cut._probe_reason`, `cut._inst_chain`), else the reason of the instance that did
(round 6: `tb.w2.u.a` showed `tb.we`'s reason, the first instance's).

After the nodes, every `port_connect … => snps_open` port: `// node=<inst path>.<port>: snps_open
(port_connect), private node nc_<…>` and `// shunt rsh_nc_<…> 1e12 ohm to ground`.

## 4. Analog netlists – `vamos/netlist/` (shared with the later `spectre` personality)

One dialect-aware parser (HSPICE now, Spectre later), one IR, two emitters. Nothing is imported from
VACASK's `ng2vc` (AGPL-3.0-only); `cir2vacask.py` is a reference only. Xyce runs under co-simulation with the
plain argv `{"Xyce", deck}` (nvc passes nothing else), so **no command-line flag such as `-hspice-ext` or
`-redefined_params` can be relied on**: the IR resolves every dialect question, and the Xyce printer emits
only constructs plain Xyce accepts. Every printer rule below was verified on the installed engines (47
expression templates in both contexts with negative, zero, non-integer and > 2^31 arguments, each row equal to
`evaluate()`'s HSPICE value on VACASK and on Xyce; a mutation check removed each printer rule in turn and the
engine tests caught every one).

### 4.1 `numbers.py`, `expr_ast.py`, `expr.py`

- `numbers.py` (phase 0, amended while building): `parse_number(s) -> float` (suffixes `T G MEG X K M MIL U
  N P F A`, trailing unit letters ignored), correctly rounded: a power-of-ten suffix scales the decimal
  exponent (`0.22u` = 2.2e-07); only `mil` is a product. `fmt(x)`, the one float spelling every emitter uses.
  The control file and the vamos options use it.
- `expr.number(s) -> float`: the same grammar and the same values (a suffix scales the decimal exponent, as
  ngspice's INPevaluate reads it; `mil` stays a product), so `0.22u` == `2.2e-7` == 2.2e-07, plus HSPICE's D
  exponent (`1.0D+3` = 1000, `50D-15`; a D with no digits after it, `2.5D` or `10dB`, is a unit letter);
  `parse_number` has no D exponent. `spice.parse` (`_FAST_NUM`) and the expression lexer (`_NUM_RE`) read
  every literal through it. An expression keeps its own rounding:
  `0.22*1e-6` is 2.1999999999999998e-07, which the binning edge test uses (§4.3.6).
- `expr_ast.py` (phase 0): `Num`, `Name`, `Str`, `Call`, `Unary`, `Binary`, `Ternary` (frozen, hashable).
- `expr.py`:
  - `parse(text, case="lower") -> Expr`: a Pratt parser. An enclosing `'...'` or `{...}` is optional, and
    quotes and braces also group sub-expressions; identifiers are folded per `case` (`lower`, `upper`,
    `sensitive`; function names and `time`, `temper`, `hertz` are always lowercase); `"text"` is a `Str`;
    the node-access calls `v vr vi vm vp vdb vt`, `i ir ii im ip idb it`, `i1`..`i9`, `lv<k>`, `lx<k>` take
    raw names (any characters but whitespace `, ( ) ' " { } =`); `-<literal>` folds to a negative `Num`;
    `^` becomes `Binary('**')`; `& | ~ % <> =` are errors. HSPICE documents no precedence: vamos uses C's
    levels, lowest first `?:`, `||`, `&&`, `== !=`, `< <= > >=`, `+ -`, `* /`, unary `+ - !`, `** ^`, power
    right-associative, tighter than a unary minus on its left and allowing one on its right (`-2**2` = -4,
    `2**-1` = 0.5, as Xyce's grammar). `ExprError(message, text, pos)`.
  - `names(ast) -> Set[str]`: parameter names only, for dependency sorting.
  - `evaluate(ast, scope) -> float` with HSPICE semantics (below); `?:`, `if`, `&&`, `||` are lazy; anything
    not constant (node voltages, currents, `time`, `temper`, `hertz`, unknown functions or names) and every
    numeric failure (division by zero, `log(0)`, domain errors, overflow, a non-finite result) raises
    `EvalError` naming it.
  - `to_vacask(ast, ctx="param", node=None)` / `to_xyce(ast, ctx="param", node=None)`, `ctx` ∈ {`'param'`,
    `'behavioral'`}: bare expression text (no `{}`); constants folded first (`fold(strict=True)`: a failing
    constant raises only in a branch that is taken), numbers in `numbers.fmt`, parameter names through
    `param_ident`; `node(kind, name)` overrides how a `v()`/`i()` argument prints (default: `.` → `:`, and
    `vacask_quote` on VACASK). `PrintError` for `v()`/`i()`/`time` in a parameter value, `hertz`, AC and
    terminal accesses (`vm()`, `i1()`, `lv…()`), strings in a behavioral expression, unknown or user
    functions (inline them first), subnormal literals on VACASK.
  - `param_ident(name, engine)`: the spelling of a parameter name in a deck; the emitters use it for
    definitions, subckt header parameters and X-line overrides. Xyce reads `pi`, `dt`, `exp`, `ctok`,
    `constctok` as built-ins inside expressions, silently (`.param pi=2` then `{pi}` gives 3.14159), and
    rejects `vt`, `temp`, `freq`, `gmin`, `poly` as names, so those (any case) get the prefix `vamos_`; on
    VACASK only a name equal to a built-in constant (`M_PI`, `P_K`, …) does.
  - `vacask_quote(name)`: the one VACASK identifier rule, shared with the emitter: bare when
    `[A-Za-z_$][A-Za-z0-9_$]*` (not a reserved word) or all digits, else single-quoted.
  - Helpers: `walk`, `node_calls`, `is_node_call`, `is_constant`, `is_node_dependent`, `fold`, `transform`,
    `substitute`, `map_nodes` (spice.parse folds ground aliases inside `v()` with it), `inline` (user
    functions; recursion or a wrong arity is an `ExprError`), `to_text` (round-trips through `parse`),
    `hspice_power`, `check_arity`, `FUNCS`.
- HSPICE built-in semantics, reproduced by both printers (`T(y)` = truncation toward zero; `S(x)` = the
  three-way sign, `S(0) = 0`):

| HSPICE | rule |
|---|---|
| `T(y)` (`int`, `trunc`, the exponent of `pow`) | Folded when y is constant. VACASK param `integer(y)` (a real truncation: VACASK's `int()` is int32, `int(3e9)` = -2147483648); VACASK behavioral `(y>=0 ? floor(y) : -floor(-y))` (`int()`, `real()` and `round()` do not translate to Verilog-A, and `ceil()` crashes openvaf-r); Xyce `(y>=0 ? floor(y) : ceil(y))` (Xyce's `INT` is a 32-bit C cast). |
| `pow(x,y)` | `pow(x, T(y))`; with a non-constant y, `(T(y)==0 ? 1 : pow(x, T(y)))` on both engines (Xyce's `pow(0,0)` is 1e50, silently; VACASK's parameter `pow(0,0)` is an error). A conditional inside `pow()` is fine on openvaf-r, so a node-dependent exponent is supported (no generated parameter). |
| `x**y`, `x^y` | x>0 → `pow(x,y)`; x<0 → `pow(x,T(y))`; x=0 → 0. A plain `pow` when y is a constant positive integer or x is provably positive; a constant integer y ≤ 0 prints `(x==0 ? 0 : pow(x,y))`. Never print `^` (VACASK and Xyce XOR). |
| `sqrt`, `log`, `ln`, `log10`, `db`, `pwr(x,y)` | `S(x)*f(abs(x))` (`pwr`: `S(x)*pow(abs(x),y)`); skip the wrapper when x is a positive constant. Xyce needs it too (its logs return complex real parts, its `db` drops the sign). In behavioral context guard `abs(x)` with `max(abs(x),1e-300)` (VACASK starts Newton from 0). |
| `sgn`, `sign(x,y)` | VACASK `S(x)` as a conditional (its own sgn(0) is 1); Xyce native. `sign(x,y)` = `abs(x)*S(y)`; one-argument `sign` is `sgn`. |
| `nint` | Half away from zero: VACASK param `round()`; VACASK behavioral through `floor()`; Xyce native (`std::round`). |
| `atan2(y,x)` | VACASK param: an explicit quadrant formula (VACASK's returns `atan(y/x)` for x<0); VACASK behavioral and Xyce: native. |
| comparisons, `&&`, `\|\|`, `!` | 1.0/0.0. VACASK param: they give integers, wrapped in `real()` where a value is used. VACASK behavioral: integer-valued literals lose their `.0` in VACASK's translation (`1.0*c/2.0` → `1*c/2`), so an arithmetic operation on two integer-typed operands gets `floor()` around its left operand. Xyce: no `!` (`!x` prints `(x==0.0)`), and `&&`/`\|\|` share one precedence level, so every non-atomic logic operand is parenthesised. |
| large literals | VACASK behavioral: integer-valued literals of magnitude 2^31 up to 1e17 crash openvaf-r; they print as an exact real-typed expression (`(3000000000.5-0.5)`), plus `+0.0*$abstime` when the literal is the whole expression (VACASK would fold it back). |
| `if(c,a,b)`, `limit(x,lo,hi)` | `?:`, `min(max(x,lo),hi)`. |
| `agauss gauss aunif unif`, `limit(nom,var)` | The nominal (first) argument, silently: vamos runs no Monte Carlo, and HSPICE uses nominal values outside one. |
| `trunc dmin dmax`; `floor ceil asinh acosh atanh` | Accepted (the first three are HSIM aliases). |
| `temper`, `time` | VACASK `$temp`, `$abstime` (behavioral only); Xyce `temp`, `time`. `time` in a parameter value is a `PrintError` on both. |
| unary minus | parenthesised. |

  `log` in HSPICE is the natural log; the Xyce printer emits `ln`. Known limits: VACASK starts Newton from
  all-zero node voltages, so a behavioral expression undefined at 0 (`acosh`, a division by a node
  expression that is 0 there, HSPICE's own `pow(v(x), negative)`) fails loudly ("NaN found / Homotopy
  failed"); `S(x)` repeats x, so nested sign-keeping functions grow up to 3× per level (no common
  subexpressions); a VACASK parameter `pow(0, T(y)<0)` is an engine error (HSPICE gives inf). An `i()`
  inside a behavioral expression is checked against the element it names, through the hierarchy
  (`tables.current_ref_errors`, both emitters; round 6): only V, L, E, H and E `vol=` (behavioral `v=`)
  elements have a branch current both engines can read, and an `i()` of any other (R, C, D, F, G, M, a
  hierarchical `x1.r1`) is the error "i(<x>) in the expression of <e>: the <K> element has no branch
  current VACASK or Xyce can read in an expression (V, L, E, H and E VOL= elements have one); put a 0 V
  source in series with <x> and use i() of it", where it used to fail inside the engines (VACASK
  "Controlling unknown 'r1:flow(br)' not found"; Xyce aborted), although HSPICE reads it.

### 4.2 `ir.py` (frozen in phase 0)

The dataclasses (`ParseOpts`, `Param`, `Model`, `Source`, `Instance`, `Subckt`, `Analysis`, `Netlist`) and
the element conventions are in the module docstring of `vamos/netlist/ir.py`; summary:

| element | kind | nodes | other fields |
|---|---|---|---|
| R C L | `r c l` | `[a, b]` | `value`; `master` = model or None; `params` (`w l tc1 m` …) |
| K | `k` | `[]` | `value` = coupling; `ctrl` = `[l1, l2]` |
| V I | `v i` | `[p, n]` | `source` |
| E G linear | `e g` | `[p, n, cp, cn]` | `value` = gain |
| F H | `f h` | `[p, n]` | `value` = gain; `ctrl` = `[vsrc]` |
| E/G `vol=`/`cur=`/`value=` | `b` | `[p, n]` | `expr`; `expr_kind` `'v'`/`'i'` |
| D Q M J | `d q m j` | `[a,c]` / `[c,b,e(,s)]` / `[d,g,s,b]` / `[d,g,s]` | `master`; `value` = area; `params` |
| X | `x` | ports | `master` = subckt; `params` = overrides (`m` included) |
| `.hdl` device | `y` | ports | `master` = the Verilog-A module name as the `.va` file spells it; `params` (a VA `.model` card is folded in, instance values winning) |

- `Source.args` keys: pulse `v1 v2 td tr tf pw [per]`; sin `vo va freq td theta phase`; exp `v1 v2 td1 tau1
  td2 tau2`; pwl `td`, with `Source.points` the `(t, v)` pairs. `Source.dc` is None unless written;
  `Source.ac` is `(magnitude, phase)`. `Source.code_uri` is the complete engine URI
  `code:libcosim_bridge.so:<vacask_bridge_init|nvc_bridge_init>:<d2a|a2d>:<name>`, built only by `deck.py`; a
  Source with `code_uri` has no dc/ac/wave.
- `Model.kind` ∈ `nmos pmos npn pnp njf pjf d r c l`; `level` is the evaluated level (None if absent or not
  constant); every parameter stays in `params`, level and version included; binned cards carry `base` and
  `bin_index`.
- `Subckt.orig_ports` is the port list as written (case-folded per `set_sim_case`); `Subckt.ports` is after
  the ground pass; `gnd_ports` are indices into `orig_ports`; `Subckt.params` holds the header and body
  parameters merged and sorted.
- Values and params are Exprs; constant ones are folded to `Num` with HSPICE semantics, parameters stay
  symbolic. M always has `l` and `w`; D/Q/J carry the area as `value`; `m` stays in `params`; `scale=` on R,
  C, L, E and G is multiplied into the value.
- `Netlist.body` holds the top-level `Param`s first (deduplicated, sorted), then Models, Subckts and
  Instances in source order. `Netlist.values` holds the evaluated top-level parameters; `Netlist.spelling`
  maps IR names to the spelling first seen (a definition's spelling wins); `Netlist.options` may hold `scale`,
  `tnom`, `spice` (Num), `dcap` (Num 1, 2 or 3), `reltol`, `abstol`, `vntol`, `gmin` (Num) and `method` (Str,
  lowercased);
  `Netlist.temp`/`tnom` are always floats; `Netlist.parhier` is `'local'` or `'global'`; `Netlist.analyses`
  has `Analysis('tran', {step, stop, start, uic, maxstep})` with floats, and `op`/`dc`/`ac` with `{}`;
  `Netlist.probes` are `(analysis, 'v'|'i', target)` with target `'a,b'` for `v(a,b)` and `'*'` for `v(*)`;
  `ics`/`nodesets` are `{node: float}`; `hdl` holds absolute `.va` paths.
- The subckts the parser left out (§4.3.2) are `spice.left_out(nl)`, kept as the dynamic attribute
  `nl._vamos_left_out` (`copy.deepcopy` keeps it, `dataclasses.replace` loses it; a real field is **open**).
- Names are folded per `set_sim_case`.

### 4.3 `spice.py` – `parse(paths, extra, cwd, opts=None) -> Netlist`

`paths`: the choose netlists, each the path as written or a `(path, origin)` pair, the origin being the
`choose` command's `file:line` (flow passes `(netlist, cfg.choose.origin)`); `extra`:
`AmsConfig.netlist_lines` (`(line, origin)` pairs); `cwd`: the directory vcs runs in; `opts`: `ir.ParseOpts`
(dialect, case, parhier_local, synth_step/synth_stop for source defaults when there is no `.tran`, search
dirs; None means the defaults). Every error is collected; if there is one, `NoteError` is raised carrying every
note of the parse (errors, warnings and notes, in the order found); otherwise the notes are in
`Netlist.notes`. A bad `opts.case` is a `ValueError`; dialect `spice` is parsed as HSPICE (initfile already
warned); any dialect other than `hspice` and `spice` raises `NoteError`.

#### 4.3.1 Files
- A relative path in a `choose` netlist, `.inc`/`.include`/`.incl`, `.lib` or `.hdl` is tried against, in
  order: the vcs invocation cwd; the directory of the file containing the reference (for a `choose` netlist,
  the control file its origin names – a bare path has none, so the cwd only; for a `netlist_commands` line,
  the control file that line's origin names); `opts.search` and every `.option search=` directory seen so far
  (a relative one against the cwd, then the containing file). The first existing file wins. If
  another candidate is a different file, a note names the one used; if none exists, the error lists every
  path tried, reported at the `choose` command for a netlist. `$VAR`, `${VAR}` and `~` are expanded. Resolved
  paths are absolute. (x-heep needs the cwd; sky130-style libraries need the including file's directory.)
- Includes and `.lib` sections are flattened (`.lib 'file' sec` selects a section; `.lib sec … .endl`
  defines one; inside a section, `.lib other` calls another section of the same file; reading a file whole
  skips its section definitions). Each `(realpath, section)` is expanded once per subckt scope (a file
  included inside two subckts is read in each); a repeat in one scope is a note. A duplicate
  `.subckt`/`.model` name in one scope is an error naming both origins.
- `.end` ends only the file it is in (text after it: one note); a `.lib` section cannot contain `.end`
  (error).
- Only line 1 of the first choose netlist is the title (`.title` sets it too). Other netlists, includes and
  the `netlist_commands` fragment (parsed after every netlist) have no title line. A title line that looks
  like a dot-command or an element is a note.

#### 4.3.2 Lines and statements
- `*` comment lines; inline comments start, outside quotes, at `;` anywhere and at `$` or `//` at the line
  start or after a blank (HSPICE: `net$1` is a name); `+` continuation, with comment and blank lines in
  between skipped (sky130 puts `*` lines inside `.model` cards); a line ending in `\` continues on the next
  one. Fields split on blanks and commas; `'...'`, `"..."`, `{...}` and `(...)` group; `name = value` with
  blanks is one assignment. Node names: an all-digit name loses its leading zeros (`00` is ground); braces
  become brackets (HSPICE: `a{3}` is `a[3]`). A net name containing `.` (on an element, X line, `.global`
  or subckt header) is an error: HSPICE reserves the period for `<subckt>.<node>` (Star-HSPICE 3-17), and
  vamos reads every `v(a.b)` as that reference (`spice._no_period`; inside a subckt the usual deferred
  error, below). A net named `<x>:<node>` (or `<x1>:<x2>:<node>`) where `x` is an X instance of the same
  scope and `<node>` an internal node of its subckt is an error naming both (`spice._colon_clashes`,
  case-insensitive): VACASK and Xyce call that internal node `x:<node>` too, so the two silently became
  one node (a top-level `x1:n1` merged with x1's `n1`: i(v2) = 0.6 mA instead of two nodes), where HSPICE,
  whose separator is `.`, keeps them apart. Other names with `:` (extracted netlists use them) are fine
  (round 6).
- Dot-commands: `.subckt`/`.ends` (`.macro`/`.eom`, `params:` optional), `.param`/`.parameter(s)`,
  `.inc`/`.include`/`.incl`, `.lib`/`.endl`, `.global`, `.model` (binned `.N` names), `.temp`, `.option(s)`,
  `.tran`, `.op`, `.dc`, `.ac` (the three recorded, for deck.py's warning), `.print`/`.probe`,
  `.ic`/`.dcvolt`, `.nodeset`, `.hdl`, `.title`, `.end`; `.protect`/`.unprotect` ignored; `.width`,
  `.graph`, `.plot`, `.save`, `.biaschk`, `.dellib`: note, ignored; `.measure` and other analyses (`.noise`,
  `.four`, …): warning, not recorded; `.alter .data .if .connect .load .vec .stim .malias .alias .del` and
  unknown dot-commands: error.
- `.tran` follows HSPICE: `tincr1 tstop1 [tincr2 tstop2 …] [tstart] [START=t] [UIC]`; further pairs are
  later intervals, and the run goes to the last tstop with the first increment (a note when there are
  several); a SPICE3-style `tstart tmax` tail is an error with a hint. Its maximum step: §4.3.5.
- Elements: R C L K V I E F G H D Q M J X (§4.2 conventions). V/I take `dc`, `ac mag [phase]`, `ac=`, `DC=`
  and `pulse`/`pu`/`pwl`/`pl`/`sin`/`exp` with or without parentheses (`PL` value-time pairs become PWL);
  `m=` on I only. E/G take the linear form (optionally `VCVS`/`VCCS`) or `vol=`/`cur=`/`value=` (→ kind
  `b`); F/H name a V source of their own scope. M needs its bulk node (3-terminal M: error); a missing `l` or
  `w` gets DEFL/DEFW (1e-4, HSPICE's default, or the option value) with a note, and the other `def*` options
  fill instances. X lines may name the subckt after the parameters (ngspice's form, sky130's
  `special_pfet_pass`). `OFF` and `IC=`/`VBE=`/… on D/Q/J/M: warning, ignored; `IC=` on C/L is refused by
  the emitters (use `.ic`).
- Anything else is an error naming the origin: HSPICE B, S, W, P, T, U; POLY forms of E/G/F/H/C/L; E/G
  `MAX`/`MIN`/`TC`/`ABS`/`IC`, PWL/DELAY/LAPLACE/OPAMP/TRANSFORMER forms; C `Q=`/CTYPE, behavioral R/C/L,
  inductor `R=`; JFET bulk; a value that reads nodes, time or hertz outside `vol=`/`cur=`/`value=`.
- **Deferred errors in libraries.** A model library defines far more than a deck uses (sky130's tt corner
  has voltage-dependent resistors and `Q=` varactors). So inside a subckt every problem with an element line
  or a model card (an unsupported construct, an unknown model or subckt, a malformed element) leaves the
  subckt out of the IR with one note, and every subckt instantiating it too; instantiating a left-out subckt
  from a kept element is the error. At top level such problems are errors at once; a bad or
  unsupported-type model card is left out with a note at any level, and using it is the error. Header,
  `.param` and syntax-level errors stay immediate. `spice.left_out(nl)` returns the left-out top-level
  subckts with their notes. A left-out subckt is not in `nl.subckts()`, so a cut cell bound to one fails at
  step 6 with one error, "cell C: subckt S cannot be simulated: <why> (<where>)": `flow.left_out_cells`
  rewrites shells.py's four not-found errors for a left-out subckt (auto-bound, `use_spice c:s`, multi-view,
  `use_spice -cell c:s` per instance), the reason coming from the step-5 note "subckt S is left out: …".
  (sky130's tt corner parses in about 1.4 s with 21 subckts left out.)

#### 4.3.3 Parameter semantics (HSPICE dialect)
- **Duplicates** are resolved per scope (top level; each subckt with header and body parameters merged): the
  last definition wins for every use in that scope, including uses earlier in the file. Each name is
  emitted once, with a note naming both origins. (VACASK rejects a duplicate; plain Xyce silently keeps the
  first.)
- **PARHIER.** `.option parhier=local|global`, default global (the Synopsys default). Local: nothing to do.
  Global: a name defined at top level and also as a subckt header parameter, a body `.param` or an X-line
  override is an error per collision naming both sites, unless `--vamos-parhier=local` (then a note).
- **Ordering.** A topological sort over `names(ast)` runs at every scope, top level included, over each
  subckt's merged header and body list; dependent parameters come after everything they reference; a cycle
  is an error naming the parameters. User functions (`.param f(a,b)='a*b'`) are inlined into every
  expression of their scope and below; they are not in the IR.
- The top-level scope is evaluated after sorting into `Netlist.values`; a parameter that depends on `temper`
  (directly or not) or is a string stays symbolic; an unknown name or a numeric failure is an error. Those
  values feed the control block (§4.4), the stop time, temperatures, `.ic`/`.nodeset` and the supply trace
  (§3.3).

#### 4.3.4 Ground pass (inside `spice.parse`, once, before either emitter)
1. Alias names `0 gnd gnd! ground` (case-insensitive), and all-zero numbers such as `00`, on element
   terminals become `0` (inside `v()`: the last hierarchical component).
2. Collapsed elements, both output terminals `0`: a V source with DC 0 and no AC or wave is dropped with a
   note (the `vgnd gnd 0 dc 0` idiom); any other V source is an error; R, C, L, I, F, G (and a G `cur=`) and
   D are dropped with a note; an E or H (or an E `vol=`) is dropped with a note if its value/gain is 0, else
   an error. An F, H or K that references a dropped element, an `i()` of one in an expression and a probe of
   one are errors naming both.
3. An alias port of a subckt is removed from `Subckt.ports` and its index recorded in `gnd_ports`; every
   **user** X line drops the actual at that index (inside `spice.parse`). If that actual is not itself
   ground, it is an error. Generated `xv_` lines are built later from the post-pass `Subckt.ports` and are
   never dropped again.
4. `.global` loses alias names and `0` (cleanup only).
5. Every net name that enters the IR after this pass – `port_connect` nets and every node `deck.py`
   generates – goes through the same alias fold (`spice.fold_ground(name)`), so a control-file `gnd` is
   emitted as `0`. Control-file port names (`port_connect`, `port_map`, `port_dir`, `node=`) are matched
   against the original names.
6. Neither Xyce `.PREPROCESS REPLACEGROUND` nor VACASK `ground 0 gnd` is used: both are singular on this
   idiom.
7. A subckt port whose name is a `.global` net and that an X line binds to a different net is an error
   naming the instance and port, suggesting `port_connect -cell <c> (<p> => <p>)` (Xyce aborts on it, VACASK
   silently binds the port locally): `spice.parse` checks user X lines, `deck.py` the generated `xv_` lines.

#### 4.3.5 `.option` dispositions
Each key is applied, mapped, a note, a warning or an error; none is dropped silently. A repeated option
takes the last value (HSPICE), with a note when the values differ.
- `scale`: the IR keeps geometry **unscaled** and `Netlist.options["scale"]` carries `s`. Only the emitters
  apply it, once, by wrapping the instance values (`tables.scaled`): MOS `w`,`l` ×s, `ad`,`as` ×s², `pd`,`ps`
  ×s; a **LEVEL 3** (geometric) diode `area` ×s², `pj`, `w`, `l`, `wp`, `lp`, `wm`, `lm` ×s – a LEVEL 1
  diode's `area` and `pj` are unitless factors that SCALE does not touch (Star-HSPICE manual, "LEVEL=1
  Scaling"; scaling them silently mis-simulated sky130's level-1 diodes by 1e12); geometric R/C `w`,`l` ×s.
  No engine scale option is ever emitted. The binning guards (§4.3.6) use the same once-scaled values. MOS
  `sa`/`sb`/`sd`/`sc` are passed as written, as ngspice's BSIM4 code (VACASK's `sp_bsim4v8`) does with its
  own scale; whether HSPICE's SCALE reaches them is not documented, so an instance that gives one under a
  scale other than 1 gets a warning (`tables.mos_scale_warnings`, both emitters; round 6). sky130 passes 0.
- `scalm≠1`, `geoshrink≠1`: error (= 1: silent). `aspec` other than 0: error ("ASPEC compatibility mode
  sets SCALE=SCALM=1e-6, WL, LEVEL=6 and ACM=1 MOS models and the CJ=IS=0 defaults", Star-HSPICE 9-13,
  21-86; round 6). `wl`: honoured for positional L/W. `defl defw defad defas
  defpd defps defnrd defnrs`: MOSFET defaults filled into instances.
- `temp`/`tnom`: each default applies on its own – no `.temp` (or `.option temp`) → temp=25; no `.option
  tnom` → tnom=25 (27 for either under `.option spice`, also kept as `options['spice']`). `.temp`/`tnom`
  values are evaluated floats (several `.temp` values: the first, with a warning).
- `dcap`: kept as `Netlist.options['dcap']` (1, 2 or 3; any other value, or none, is an error) and selects
  the D/Q/J depletion-capacitance equations (§4.3.6, where DCAP=3 is an error). `spice`: kept as
  `options['spice']`, with the note ".option spice: temp and tnom default to 27, DCAP to 1, and the model
  defaults are SPICE's (MOS CAPOP=0, LD=0, no NSUB default; BJT MJS=0); its other SPICE-compatibility
  settings are not modelled".
- `parhier`: §4.3.3. `search`: §4.3.1.
- **Maximum time step.** The engines do not apply HSPICE's internal step bound by themselves (VACASK bounds
  its step by (stop−start)/50 only, Xyce by stop/10), and without it the e2e-6 inverter delays were 1–5 %
  off on both engines. So the first `.tran` always gets a `maxstep`: `.option delmax` when given (a positive
  number, else an error; `.option rmax` is then ignored with a note), else HSPICE's bound
  `min(TSTOP/50, RMAX·TSTEP)` with a note naming the value, TSTEP being the smallest increment of the
  `.tran` (Star-HSPICE manual 2001.2: DELMAX 9-43, the bound 11-26 and 11-36, RMAX 9-46, DVDT 9-44, LVLTIM
  9-48, METHOD 9-49, ACCURATE 11-27, DVDT=3 11-35). RMAX: `.option rmax`, `dvdt`, `lvltim`, `method` and
  `accurate` apply in order, the last setting winning (METHOD=GEAR sets LVLTIM=2; ACCURATE sets DVDT=2,
  LVLTIM=3 and RMAX=2; DVDT=3 sets LVLTIM=1 and RMAX=2); RMAX never set is 5 when DVDT=4 and LVLTIM=1
  (HSPICE's defaults), else 2. `rmax` is mapped; `dvdt`, `lvltim` and `accurate` are notes (HSPICE's step
  algorithm is not modelled; they only feed RMAX). Without a `.tran`, `delmax` is the maximum step of the
  analysis vamos synthesises (deck.py `_analysis`, §4.7; the note ".option delmax=<v>: the maximum time step of
  the analysis vamos synthesises (the netlist has no .tran)"; an explicit `--vamos-analog-maxstep` wins; it was
  a warning, ignored, before the round-6 repair) and `rmax` a note.
- `gmin`: mapped on both engines (VACASK `options gmin=`, Xyce `.options device gmin=`). `method=gear`:
  mapped (VACASK `tran_method="gear"`, Xyce `.options timeint method=gear`, both variable order up to 2, as
  HSPICE's GEAR); `trap` is both engines' default. `reltol`, `abstol`, `vntol`: a note each – Xyce's
  RELTOL/ABSTOL control its LTE and Newton residual, not SPICE's tolerances, so both engines keep their
  defaults (`tables.solver_options`, the emitters' notes).
- Output, listing and accuracy options (`post`, `probe`, `ingold`, `measout`, …): note. `gshunt`, `cshunt`
  and unknown keys: warning.

#### 4.3.6 Models

One dispatch table, `netlist/tables.py`, read by both emitters, keyed by (element kind, HSPICE level, default
1; a non-integer level is an error):

| kind / level | VACASK module | Xyce level | notes |
|---|---|---|---|
| M 1 / 2 / 3 | `sp_mos1` / `sp_mos2` / `sp_mos3` (`spice/mosN.osdi`) | 1 / 2 / 3 | drop `level`; HSPICE's defaults written (LEVEL 3: `badmos3=1`), CAPOP warned (below) |
| M 49, 53 | `sp_bsim3v3` (`spice/bsim3v3.osdi`) | 9 | drop `level`; VACASK: strip `version` (note: simulated as BSIM3v3.3); Xyce keeps `version` (note: Xyce runs BSIM3v3.2.2); LEVEL 49 XPART, CAPMOD and ACM (below) |
| M 54 | `sp_bsim4v8` (`spice/bsim4v8.osdi`, ngspice's BSIM4.8 code) | 54 | `version` printed as a string; `level` dropped; a note when the card's VERSION major.minor differs from what the engine runs (`tables.bsim4_version`: VACASK 4.8.3; Xyce 4.6.1 below 4.7, 4.7.0 below 4.8, 4.8.2 from 4.8) |
| D 1 | `sp_diode` (`spice/diode.osdi`) | 1 | `level` **kept** (it selects the model); DCAP, PB (below) |
| D 3 | `sp_diode` | — | VACASK only; refused on Xyce (no geometric diode) |
| Q 1 | `sp_bjt` (`spice/bjt.osdi`) | 1 | drop `level`; a 3-terminal Q: substrate on 0 (VACASK), left to Xyce (ground); DCAP, MJS (below) |
| J 1 | `sp_jfet1` (`spice/jfet1.osdi`) | 1 | drop `level`; DCAP, PB, CAPOP (below) |
| R/C/L cards | `sp_resistor` / `sp_capacitor` / `sp_inductor` | (no level printed) | value-only elements use `resistor`/`capacitor`/`inductor.osdi` |
| anything else (MOS 6/9/10/13/50/72, Q ≠ 1, D 2/6, J ≠ 1) | — | — | error naming the level, on both engines |

- `level` is never emitted to a VACASK target except `sp_diode`. Level 54 targets `sp_bsim4v8`, not the
  Berkeley Verilog-A `bsim4` (`bsim4v8.osdi`): that one rejects sky130's `lintnoi`, stops on `nigc=0` /
  `nigbacc=0` with `igcmod=igbmod=0` (which ngspice, HSPICE and Xyce accept) and made the x-heep operating
  point fail on VACASK; `sp_bsim4v8` equals Xyce to 7 digits on the bin deck. sky130's `version=4.5` runs as
  4.8.3 on VACASK and 4.6.1 on Xyce (HSPICE runs 4.5); inverter delays still agree within 1.7 %. With sky130
  cards VACASK logs "unknown BSIM4 version" and "Source/Drain conductance reset to 1.0e3 mho" (as ngspice
  does), and Xyce clamps `pbswgd` below 0.1 (its 4.6.1 rule).
- **HSPICE model defaults** (`tables.hspice_card`, reached by both emitters through `tables.model_params(…,
  options=nl.options)`): the targets are SPICE3 and Berkeley models, whose defaults differ from HSPICE's, so
  each card is rewritten to the values HSPICE simulates, the same way for both engines, with one note per
  card ("model <m>: written as HSPICE simulates it: …"). An HSPICE behaviour neither engine has is a warning
  (an error under `--vamos-strict`), or a `TableError`. Checked against HSPICE's numbers on both engines
  (MOS 103.59/43.16/517.97 µA for three KP/TOX cards, 136.19 µA with GAMMA, the manual's LEVEL 3 example
  6.912e-4 A; DCAP=2 diode, BJT and JFET values).
  - M 1/2/3: TOX above 1 is Ångström (a TOX expression gets `(t>1 ? t*1e-10 : t)`); the LEVEL 1 KP default
    2.0718e-5 (NMOS) / 8.632e-6 (PMOS); PMOS UO 250; UO derived from KP at LEVEL 2/3 when only KP is given;
    NSUB (default 1e15, or derived from GAMMA), GAMMA and PHI as HSPICE derives them (at the default NSUB,
    GAMMA 0.527625 and PHI 0.57604; at LEVEL 1 without TOX the target ignores NSUB, so GAMMA and PHI are
    written directly);
    LD = 0.75·XJ (LEVEL 2/3); CGSO/CGDO from LD+METO and TOX, CGBO from WD (METO removed); LEVEL 3 ETA ×
    8.14/8.15, and `badmos3=1` (round 6): HSPICE's LEVEL 3 channel-length modulation (21-26: ΔL =
    Xd·sqrt(KAPPA·(vds−vdsat)) above vdsat with VMAX=0; with VMAX>0 the pinch-off field Ep without KAPPA)
    is SPICE2's, which SPICE3 calls `.option badmos3`, and P14 gives both targets a model parameter
    `badmos3` for it; without it VACASK's `sp_mos3` runs ngspice's modified CLM (ΔL from vds−vdsat+vdsat/8,
    and a vds⁴ onset below vdsat) and Xyce SPICE3f's KAPPA·Ep (revision 5's "1.8 % apart with XJ": VACASK
    5.06197e-4 A where HSPICE's rule gives 4.9715e-4; now VACASK 4.971465e-4, Xyce 4.971456e-4, ngspice with
    `.options badmos3` 4.971470e-4; a GAMMA=0 card matches the hand formula, 2.97910e-4, on both).
    Warnings: an ambiguous KP default, a LEVEL 3 XJ below 0.05u, a VTO the target cannot derive,
    and HSPICE's default CAPOP=2 gate capacitance: "HSPICE's default CAPOP=2 gate capacitance (parameterized
    modified Meyer) is simulated as SPICE's Meyer model (CAPOP=0); add capop=0 to the card (or .option
    spice) to make HSPICE use the same model" (not under `.option spice`, nor for LEVEL 1 without TOX, which
    has no Meyer capacitance in HSPICE either). `capop=0` is removed with a note, any other CAPOP removed with
    a warning. A LEVEL 1 card without TOX gets no TOX (manual 20-56). COX (alias CO), which neither target
    has, becomes `tox=eps_ox/COX`, HSPICE's "TOX calculated from COX when COX is input" (20-69, 21-2, 21-7,
    21-19; `tables._mos_cox`), replacing a given TOX with a note; a COX that is not positive is an error. At
    LEVEL 1 it is a warning as well: HSPICE invokes the Meyer capacitance only when "TOX is specified"
    (20-57) and computes KP=UO·COX when "UO and TOX are entered" (21-2), and whether a TOX calculated from COX
    counts is not documented, while the target, given that TOX, has the Meyer capacitance. A LEVEL 2 card
    with `cox=1.7265e-3` and the one with the equivalent TOX give the same drain current to 1e-9 on both
    engines (round 6; both engines rejected COX before). CJSW (CJP) given without MJSW gets mjsw=0.33 (HSPICE
    20-28; sp_mos1/2 and Xyce LEVEL 1/2 default 0.5), unless `.option spice`.
  - MOS 1/2/3 bulk junctions (round 6; `tables._mos_junctions`, HSPICE's MOS diode, 20-26…20-48; not for
    ACM=1, whose CJ is per width): with no CJ (nor its aliases CDB, CSB, CJA), CJ is written as HSPICE's
    default for the default option ASPEC=0, `sqrt(eps_si*q*NSUB/(2*PB))` (20-28), with the NSUB HSPICE
    assumes – the card's, one derived from GAMMA, else the default 1e15; under `.option spice` only a given
    NSUB – where both targets default CJ to 0 (ngspice's and Xyce's level 2 compute that formula and never
    use it). SPICE2 and SIMetrix use the same formula; the ASPEC=1 default is 0 (21-86; `.option aspec` is
    refused, §4.3.5). `fc=0` is written wherever the card has a junction capacitance: HSPICE does not use FC,
    its forward-bias junction capacitance is linear (20-47), SPICE's formula with FC=0 (a given FC is
    replaced). PHP is removed: neither target has it, their sidewall uses PB. Warnings (each once per card,
    an error under `--vamos-strict`; `tables.mos_junction_warnings`, both emitters, for instances that give
    AD/AS): a card with neither CJ nor NSUB, because the manual's default column gives 579.11 µF/m² for the
    same parameter and no HSPICE run settles which one HSPICE uses ("model <m>: no CJ and no NSUB on a MOS
    LEVEL <n> card whose instances give AD/AS: HSPICE's default CJ is ambiguous (…); give CJ (F/m^2) or NSUB
    on the card"); CBD/CBS on the card, which HSPICE uses only when CJ·AD + CJSW·PD is 0 (20-27, 20-48), so
    it simulates CJ·AD where both targets use CBD ("… give AD=AS=0 or remove CBD/CBS"); a PHP other than PB
    on a card with a sidewall capacitance (simulated with PB). Checked on both engines against the 20-47
    equations, i = C(v)·dv/dt through vbd = −0.6…+0.4 V to 3e-3: CJ 3.2208e-4 from NSUB=1e16, 1.0185e-4 from
    the default NSUB, and a given 2e-4 whose fc=0.9 HSPICE ignores. Before round 6 no CJ default was written
    (both targets ran CJ=0, with a warning) and FC and PHP were the targets'.
  - BSIM3: LEVEL 49 gets XPART=1; CAPMOD follows VERSION (3.0: 1; 3.1: 2, with a warning at LEVEL 49, where
    HSPICE's own CAPMOD=0 model exists on neither engine; 3.2 and later: 3); LEVEL 49 without ACM gets a
    warning (HSPICE's ACM=0 junctions are simulated as BSIM3's Berkeley junctions). Where HSPICE's own
    junction model applies (LEVEL 49 without ACM, or ACM 0, 2 or 3 at LEVEL 49 or 53) its defaults CJ=5.79e-4
    and CJSW=0 are written (22-43, "Default deviates from BSIM3v3": BSIM3's are 5e-4 and 5e-10), so the
    targets' bottom junction has HSPICE's capacitance. `acm=10` is removed with a note and leaves the
    Berkeley defaults, which both targets have: ACM=10 is how LEVEL 49 achieves "compliance with Berkeley
    BSIM3v3" (22-19), and LEVEL 53 keeps BSIM3's defaults (round 6; the `js=0` that was written with
    `acm=10` is gone).
  - D/Q/J: HSPICE's default DCAP=2 is mapped exactly as `fc=0` (plus `fcs=0` for diode sidewalls; FC is 0
    for sidewall-only diodes too, which until P14 both engines needed: their sidewall charge used the area's
    F1); a `dcap=` on the card is removed; DCAP=3 is an error ("DCAP=3 (peak-limited depletion capacitance)
    has no VACASK or Xyce equivalent; use DCAP=1 or 2"); `.option spice` means DCAP=1. Defaults: diode PB 0.8
    (printed `vj`; PB, PHI and PHA are renamed) and PHP = PB; BJT MJS 0.5 (0 under `.option spice`); JFET PB
    0.8. A JFET `capop=0` is removed, any other CAPOP a warning.
  - R and C cards (round 6; `tables._wire_card`): HSPICE's wire parameters (Star-HSPICE 14-3…14-11) are
    mapped to names each engine gives the same meaning, the geometry HSPICE computes kept exact, R =
    RSH·(L−2·DLR)/(W−2·DW) and C = COX·(L−2·DEL)·(W−2·DEL) + 2·CAPSW·(L+W−4·DEL), with COX from THICK and DI
    when not given (a note). VACASK's `sp_resistor`/`sp_capacitor` are ngspice's models, which take HSPICE's
    names (`dw dlr tc1r tc2r res`; `cox capsw del di thick`), so `WIRE_RENAME` maps only `tref`→`tnom`,
    `w`→`defw`, `l`→`model_l` (R) or `defl` (C), and the capacitor's `cap`/`tc1`/`tc2`→`model_cap`/
    `model_tc1`/`model_tc2`. Xyce knows only RSH, TC1, TC2, TNOM and NARROW (R) and CJ, CJSW, NARROW, TC1,
    TC2, TNOM (C), and ignored every other model parameter silently (R came out 1000 Ω where HSPICE gives
    1347.5 Ω, C came out 0): `tc1r`/`tc2r`→`tc1`/`tc2`, `tref`→`tnom`, `cox`→`cj`, `capsw`→`cjsw`,
    `w`→`defw`, and, as Xyce subtracts one NARROW from both L and W, `DEL`→`narrow=2·DEL` and `DW`=`DLR`→
    `narrow=2·DW`. Refused with a located error: the R card's wire capacitance (HSPICE's CRC model: `CAP`,
    `CAPSW`, `COX`, `DI`, `THICK`, `BULK`, `CRATIO`, `TC1C`, `TC2C`) and `SHRINK≠1` on both engines; on Xyce
    `DW≠DLR`, a model `L`, and a `RES`/`CAP` default (Xyce's R and C model parameters are multipliers).
    sky130's C cards (`tc1 tc2 cox capsw w tnom`) map on both engines. Checked against hand values on both:
    R = 100·9.8/0.8·(1+1e-3·100) = 1347.5 Ω (to 1e-6) and C = 9.996e-14 F (to 1e-4).
  - Engine defects written around, each with a note: Xyce's diode computes no junction charge when CJO is 0
    (`if (tJctCap != 0.0)` wraps the TT and sidewall charges), so a level-1 diode card with CJSW (CJP) or TT
    but no nonzero CJO gets cjo=1e-30 on Xyce ("model <m>: cjo=1e-30 written for Xyce, …";
    `tables._xyce_diode_charge`). VACASK's sp_mos3 gave NaN when KAPPA·alpha was exactly 0 (KAPPA=0, the
    manual's own LEVEL 3 example, or no NSUB): that is fixed in the engine (P14), so kappa=0 is written as
    given (round 6; vamos wrote kappa=1e-12 on VACASK).
  - Left open (§10): which CJ HSPICE uses on a MOS 1/2/3 card with neither CJ nor NSUB (the warning above);
    no HSPICE run is available to settle it.
- **Diodes:** Xyce's diode currents differ from VACASK's by about 0.07 % (Xyce's thermal-voltage constants).
- **Polarity:** every VACASK M/Q/J target gets `type=1.0` or `type=-1.0` from `nmos/pmos`, `npn/pnp`,
  `njf/pjf` (all default to n-type; a PMOS card without it silently simulates as NMOS). Xyce keeps the type
  keyword.
- **Unknown parameters:** VACASK fails on them; Xyce silently ignores them. The smoke check (§4.7) runs
  `Xyce -norun` for the Xyce engine and treats "No model parameter … found" / "Unrecognized parameter" as
  errors (the bounds of binned cards are exempt). `STRIP_KEYS` (`acm`, `calcacm`, `hdif`, `ldif`, `rdc`,
  `rsc`: HSPICE geometry keys the targets do not honour identically) are stripped from cards on both engines
  with a warning each, except a BSIM3 `acm=10` (the targets' own Berkeley junctions), removed with a note.
  Card notes name a bin by its base (`tables.card_label`) and are given once per binned
  model.
- **Reachable only:** both emitters print, and judge, only the subckts and model cards reachable from the
  top-level instances (`tables.reachable`; the Xyce elaboration): a library defines far more than a deck
  uses, HSPICE never instantiates the rest, and an unused construct no target honours must not fail a deck.
- **Binning.** `.model <base>.<k>` cards carrying all of `lmin lmax wmin wmax` are grouped under `<base>`.
  Bins are tried in Xyce's order (card names compared case-insensitively as strings: `nch.1`, `nch.10`,
  `nch.2`); the guards are `(x >= lo || abs(x-lo) < 1e-15) && x < hi` with `x = l·s` and `w·s/nf`
  (per-finger W, as ngspice, VACASK's Cadnip parser and sky130 use), `l`, `w` the unscaled instance values,
  `s` applied exactly once. The 1e-15 lower-bound tolerance (Xyce's too) lets two bins match at an edge;
  the first in that order wins on both engines (`0.22*1e-6` = 2.1999999999999998e-07 picks the lower bin). A
  constant geometry that no bin fits is an error at emit time on both engines.
  - VACASK: each bin is `model 'm_<base>.<k>' <module>` (quoted) without the four bounds; every M instance
    whose master is a binned base becomes an `@if`/`@elseif` chain of copies, one per bin, each carrying the
    once-scaled geometry (`w l ad as pd ps`) and every other instance parameter verbatim plus `$mfactor`; the
    final `@else` binds the unsuffixed `m_<base>`, which does not exist, and `smoke()` turns the engine's
    "Master 'm_<base>' not found" into "no bin of <base> for l=…, w=… (scale …; instance …)" (l and w as the
    instance writes them).
  - No wrapper subckt with a fixed parameter list (it would reject unlisted instance parameters, and a
    forwarded default changes BSIM4 `$param_given` behaviour).
  - Xyce bins natively on total W (cards keep their bounds) when `nf` is absent or 1. Otherwise `l`, `w` and
    `nf` are evaluated on each instance path – an elaboration walk evaluates every subckt's parameters with
    the X lines' overrides, so sky130's `l={l} w={w} nf={nf}` wrappers bind – and the device is bound to the
    bin card `tables.select_bin` picks (VACASK's rule); a subckt whose bindings differ between paths is
    printed once per distinct binding as `<name>__vb<k>` (names checked case-insensitively against every
    subckt; each copy prints only the bin cards it uses). Geometry that is not a number even on its path
    (`temper`) with `nf ≠ 1` is an error; Xyce's native no-bin messages ("no valid model card found", "Unable
    to find model <base>.") are mapped by `smoke()` the same way.
  - **Open:** bin bounds are evaluated with top-level values only (a bin card inside a subckt whose bounds
    read subckt parameters is an error, worded since round 6 "binned model <m>: <k>=<e> is not constant
    (…): vamos evaluates bin bounds with the top-level parameters only, so a bin card in a subckt cannot read
    the subckt's parameters; give constant bounds"); the Xyce elaboration re-walks a subckt body per X
    instance (fine at 400 sky130 devices, unmeasured on very large flat designs).

#### 4.3.7 The multiplier (`m=`; device class decides)
VACASK (`$mfactor`):
1. Every emitted subckt declares `parameters … $mfactor=1.0`.
2. Children inside a subckt that accept `$mfactor` get `$mfactor=$mfactor*<m>` (or `$mfactor=$mfactor` with
   no `m=`): X instances, OSDI R/C/L/D/Q/J/M, Verilog-A Y, builtin I, linear G (`vccs`) and F (`cccs`).
3. At top level `m=<m>` becomes `$mfactor=<m>`. Generated `xv_` cut instances get none.
4. Behavioral current sources never get the parameter; the factor is folded into the expression:
   `i=(<expr>)*$mfactor*<m>` in a subckt, `*<m>` at top level.
5. Behavioral `v=` sources, `mutual` (K), V, linear E and H get no multiplier.

Xyce (`vamos_mfactor`): Xyce silently drops the multiplier of an enclosing `X … m=` for I sources and JFETs,
ignores `m=` on I and F lines and rejects it on J and Y lines, so the emitter never uses X-line `m`: every
subckt declares `vamos_mfactor=1.0`, X lines pass `vamos_mfactor=<effective multiplier>`, R C L D Q M G get
`m={vamos_mfactor*m}`, and the factor is folded into I source values, the F gain, the J area and a behavioral
`i=`; V, E, H, K and behavioral `v=` get nothing; a Verilog-A Y device that needs a multiplier is an error.
`$mfactor`/`vamos_mfactor` are reserved parameter names. (`tables.MULT`, `MF_PARAM`, `multiplier`.) Nested
`m=2` inside `m=3` gives 6× and `G cur=`, I and J inside an `m=3` subckt 3× on both engines. An F controlled
by a V source inside an `m=3` subckt gives 9× on both (physical replicas would give 3×; HSPICE's answer is
unknown: **open**).

**Coupled inductors** (`tables.coupled_inductance`): VACASK's `mutual` divides by `$mfactor` and reads the
nominal `l`, and Xyce's K pass keeps only L and IC of a coupled inductor's line (it drops `m=`, TC1 and TC2),
so an inductor that a K element names gets its effective multiplier and TC1/TC2 folded into its value,
`L·(1+TC1·dt+TC2·dt²)/M` with `dt = temp + DTEMP − tnom` (Star-HSPICE 2001.2, 4-8 and 14-17), printed without
`$mfactor`/`m=`/`tc1`/`tc2`/`dtemp` on both engines. An inductance on a model card, or a card with TC1/TC2,
is an error (neither can be folded); a coupled inductor whose card has neither (VACASK `mutual` over
`sp_inductor`) runs exactly on both engines (v(p) = 1 V, v(s) = 0.5 V; round 6, revision 5's "not verified"
item). `i()` of a folded inductor is its total current. Checked on both engines:
a transformer under `X … m=2` and `m=3`, TC1 at 125 °C, and a one-sided `m=2` on L1 only (0.3536 V, which
assumes HSPICE treats M as one inductor of L/M: not verified against HSPICE).

#### 4.3.8 Sources: HSPICE defaults resolved in the IR

TSTEP/TSTOP come from the first `.tran` (or `ParseOpts.synth_step`/`synth_stop`). Both emitters print every
field explicitly (`tables.wave` checks them for both: every field present, constant edges > 0, a constant
period longer than tr+tf+pw, a constant EXP td2 after td1, two or more PWL points with constant times ≥ 0
strictly increasing), so VACASK and Xyce see the same waveform.

| wave | rule |
|---|---|
| `PULSE(v1 v2 td tr tf pw per)` | td omitted → 0, negative → 0 (HSPICE); tr/tf omitted or an explicit 0 → TSTEP (a non-constant edge becomes `(x==0 ? TSTEP : x)`; one policy for both engines, unverified against HSPICE, documented); pw omitted → TSTOP; per omitted → aperiodic (VACASK rejects `period=TSTOP`): HSPICE's own defaults (the HSPICE B-2008.09 Simulation and Analysis manual gives Default=TSTOP for both; the 2001.2 Star-HSPICE manual's TSTEP is superseded), which are SPICE3's (a per of TSTOP never repeats within the run; round 6, pinned by `test_r6_N.py`); an explicit per ≤ tr+tf+pw (after edge substitution, 1e-12 relative tolerance) is an error. Never print `rise=0`/`fall=0`. |
| `SIN(vo va freq td theta phase)` | freq omitted → 1/TSTOP; td, theta, phase omitted → 0; phase → VACASK `tdphase`; before td both engines hold `vo + va·sin(phase)`. |
| `EXP(v1 v2 td1 tau1 td2 tau2)` | td1 omitted → 0; tau1/tau2 omitted → TSTEP; td2 omitted → td1+TSTEP; td2 ≤ td1 or a tau ≤ 0 is an error; VACASK gets `td2 - td1` (relative). |
| `PWL(t1 v1 …) [TD=] [R=]` | When the first time is after 0, the point `(0, DC or 0)` is put first (HSPICE uses the DC value at time zero). `TD=` → delay: VACASK `delay=`; Xyce adds it to every time point (`{t+td}` when not constant – only a constant TD was run on the engine), because Xyce's own `TD=` outputs 0 before the delay, not the first value, and stalled its time stepping at TD. Equal or decreasing times → error naming them; `R=` → error (v1). |

### 4.4 `vacask.py` – `emit(nl, path, analysis_name="vamos_tran", osdi=(), notes=None) -> None`
- The caller picks the file (`ams/deck/vamos.sim` or `smoke.sim`). `render(nl, analysis_name, osdi, notes,
  op=False) -> str` gives the text (`op=True`: an operating point in place of the transient). Problems are
  collected over the whole netlist and raised together as one `NoteError` (warnings included); without
  errors, the warnings and notes are appended to `notes`.
- `osdi`: `.osdi` paths to load, absolute or relative to the deck's directory. deck.py passes `vamos_ie.osdi`
  **and** every compiled user `.hdl`, relative (`../va/<k>_<stem>.osdi`, §4.7); with no `osdi`, the `nl.hdl`
  files are loaded as `.va` and VACASK compiles them itself.
- The deck, the smoke deck and the smoke logs are written as UTF-8 whatever the locale (`xyce.py` too), so a
  non-ASCII title compiles under any locale.
- Header: title; `ground 0`; `global …`; `load "<path>.osdi"` for each path in `osdi`, then the module files
  the dispatch uses (resolved through VACASK's module path); builtin model lines with reserved names, each
  declared once and only when used: `vamos_vsource`, `vamos_isource`, `vamos_vcvs`, `vamos_vccs`,
  `vamos_ccvs`, `vamos_cccs`, `vamos_mutual`, `vamos_resistor`, `vamos_capacitor`, `vamos_inductor`,
  `vamos_sp_resistor`/`_capacitor`/`_inductor`, `vamos_gcond` (VACASK instances name a model). User cards
  become `m_<name>` (with a suffix when one collides with a subckt name: models and subckts share one
  namespace).
- Models per §4.3.6; `scale` per §4.3.5; reachable definitions only.
- One quoting predicate for every identifier class (nodes, instances, models, subckts, parameters):
  `expr.vacask_quote` (quote if the name is not `[A-Za-z_$][A-Za-z0-9_$]*` or all digits, or is a reserved
  word: `load`, `control`, Cadence `XI0<3>`); parameter names through `expr.param_ident`.
- String-typed module parameters are printed as strings (`tables.STRING_PARAMS`: the BSIM4 `version`).
- Element mapping: R/C/L with a value only → `resistor`/`capacitor`/`inductor`; with a model card or other
  parameters → `sp_resistor`/`sp_capacitor`/`sp_inductor` (`IC=` on C/L is an error: use `.ic`); K →
  `mutual k= ind1= ind2=`; V/I → `vsource`/`isource`; E/G → `vcvs`/`vccs gain=`; F/H → `cccs`/`ccvs gain=
  ctlinst=`; V, I, E, G, H, F, K and behavioral elements take no parameter but `m` (any other is an error,
  never dropped); `b` → behavioral `v=`/`i=`; D/Q/M/J → their `m_` card; X → the subckt; Y → Verilog-A:
  `vamos_gcond` instances share one model card (`rr` is an instance parameter), any other module gets a card
  per instance, in the instance's scope (VA parameters are model parameters unless the module marks them
  instance parameters).
- A code source prints as `<inst> (<p> <n>) vamos_vsource|vamos_isource type="pwl" file="<code_uri>"`.
- **Subckt parameters:** VACASK lets an instance override only a primary parameter (a default that is
  constant when the deck is parsed), where HSPICE allows any. Top-level values are folded into defaults, so
  `wp=2.5*wn` stays overridable; a default that depends on another subckt parameter and that some X line
  overrides is declared `p=NOT_GIVEN` and read through `p__v=(p==NOT_GIVEN) ? (<default>) : p` in the body
  (verified against the HSPICE answers on both engines; Xyce needs nothing). Names an enclosing subckt
  declares are never folded. A subckt defined inside another that reads a parameter of the enclosing subckt
  is an error (`tables.enclosing_reads`, `vacask.subckt`; round 6): VACASK resolves a name in a nested
  definition only in that subckt and at top level, so the read failed ("Variable or constant p not
  defined") or, beside a same-named top-level parameter, silently read that one (0.2 mA where 0.333 mA is
  right). The message: "subckt <s>, defined inside subckt <p>, reads <names> of the enclosing subckt: VACASK
  does not pass an enclosing subckt's parameters into a nested definition; define <s> at top level and pass
  <names> on its X lines". Xyce reads the enclosing parameter, as HSPICE does.
- Control block: `abort always`, then `options tran_lteimplicit=0 temp=<f> tnom=<f> [gmin=<f>]
  [tran_method="gear"]`, the `save` list, and one `analysis <name> tran step=<f> stop=<f> [start=<f>]
  [maxstep=<f>] [icmode="uic"] [ic={…}] [nodeset={…}]` (`.ic`/`.nodeset` as analysis parameters with
  evaluated floats), or `analysis vamos_smoke op [nodeset={…}]` for the smoke deck. Every value is an
  evaluated float.
- Saves: `v(n)` (`.` → `:`), `v(a,b)` saves both nodes (silently; `rawfile.column('v(a,b)')` computes the
  difference), `*` → `save default`; `i(x)` of V, L, E, H and behavioral `v=` elements → `i(x)`, of a
  resistor → `p(x, i)`; any other `i()` is an error.
- `smoke(nl, dir, osdi=(), nvc_libdir=None, timeout=600) -> List[Note]`: writes `dir/smoke.sim` from
  `tables.smoke_netlist(nl)` with the op `vamos_smoke`, runs the standalone VACASK (`engines.vacask_bin()`,
  environment from `engines.env_for`) in `dir`, keeps its output in `dir/smoke.log`, and returns `[]` when it
  exits 0 (`abort always`: a failed analysis exits 1), else one error note with the engine's message (the
  no-bin mapping above).

### 4.5 `xyce.py` – `emit(nl, path, osdi=(), notes=None) -> None`
- Plain Xyce, no flags: floats only (no suffixes), no `^`, `ln` not bare `log`, `:` hierarchy separator,
  `{}` around non-literal values, duplicates already resolved, parameter names through `expr.param_ident`;
  `render(nl, osdi=(), notes=None, op=False)` gives the text; errors collected and files written as UTF-8 as
  in §4.4. An element's
  printed name starts with its Xyce letter: a behavioral E/G becomes `B<name>`, the gated conductance
  `g_<n>` becomes `bg_<n>`, and `i()` references, K/F/H controls and probes follow (the rawfile reader finds
  `i(<IR name>)` in the `I(B<name>)` column). IR names that differ only in case, and names Xyce cannot parse,
  are errors.
- Layout: `* title`; `.hdl` lines; `.global`; top-level `.param`; the body; `.options device temp=<f>
  tnom=<f> [gmin=<f>]` (expressions are rejected there); `.options timeint method=gear` when asked; `.ic`;
  `.nodeset` (dropped with a note when `.ic` exists: Xyce aborts on both); `.tran <step> <stop> [<start>
  [<maxstep>]] [UIC]`; `.print`; `.end`. Only the subckts and cards the deck uses are printed.
- MOS level per §4.3.6 (49/53 → 9); a level-3 diode is refused; the multiplier per §4.3.7; binning per
  §4.3.6 (`__vb<k>` copies); PWL `TD=` per §4.3.8.
- Verilog-A: `nl.hdl` (plus any `.va` path in `osdi`; an `.osdi` path is an error, and deck.py passes
  nothing) become `.hdl "f.va"` lines, a `.model <card> <module>` and `Y<module> <name> <nodes> <card>`
  instances; Xyce compiles them (PyMS) at parse time, so the smoke check and every run pay that compile. A
  Verilog-A instance with parameters is refused (PyMS ignores VA parameter overrides), and so is one that
  needs a multiplier. The gated conductance is the B source `bg_<n> <n>_d <n> I={V(<n>_e)*(V(<n>_d)-V(<n>))/rr}`
  (§4.7).
- A code source prints as `<inst> <p> <n> PWL FILE "<code_uri>"`.
- Output: `.print tran format=raw file=vamos_tran.raw <probes>` (`v(*)` when there are none) – a
  **relative** name (`xyce.RAW` == `layout.RAW`); Xyce resolves it against the process cwd, which is the
  per-run directory (§6).
- `smoke(nl, dir, osdi=(), nvc_libdir=None, timeout=600) -> List[Note]`: writes `dir/smoke.cir` from
  `tables.smoke_netlist(nl)` with `.op` in place of `.tran` and `.print`, runs `Xyce -norun` (the
  model-parameter check, §4.3.6; `dir/smoke.norun.log`), then the operating point (`dir/smoke.log`); `[]` when
  both pass, else one error note with Xyce's messages (the no-bin mapping of §4.3.6).

### 4.6 `rawfile.py`
`read(path) -> Raw` (the first plot) and `read_all(path) -> List[Raw]` read VACASK and Xyce rawfiles: binary
(padded too) or ASCII, real or complex, one or several plots; the point count comes from the data, never the
header, so a blank, wrong or missing `No. Points:` and a truncated last point are tolerated; a non-rawfile,
an unknown layout flag and LTspice's float32 "forward" data raise `RawError`. `Raw`: title, date, plotname,
flags, variables, points, `declared_points` (None when blank), binary, path, header; `names()`; `index(name)`
(case-insensitive, exact spelling first, then by node across both engines' spellings – Xyce `V(X1:N)`,
`I(V1)`, `I(BEB)` for a behavioral `eb`; VACASK `x1:n`, `v1:flow(br)`, `r1.i` for a resistor's current;
HSPICE's `.` matches `:`; `KeyError` when nothing or more than one matches); `column(name)` (also `v(a,b)` =
`v(a) − v(b)`); `time()`; `last_time()`; `at(name, t)` (linear interpolation); `crossings(name, level,
direction=0)`. `fix_points(path) -> bool` makes every plot's `No. Points:` match its data – in place when the
number fits the field (Xyce reserves 18 characters), else by rewriting the file beside it and `os.replace` –
and returns True if the file changed (§6). Xyce's ASCII rawfiles carry 9 significant digits; binary is exact.
`read` loads the whole file into memory; simv's publish path does not use it: `cosim.scan_raw`/`check_raw`
apply the same counting and repair rules to a mapped file in O(1) memory (§6; the rules are implemented
twice, kept equal by tests, **open** to merge). A blank `No. Points:` in a plot other than the last of a
multi-plot binary file cannot be resolved (neither engine writes one).

### 4.7 What the AMS layer adds (`ams/deck.py`)

`deck.build(nl, plan, cfg, engine, alloc, hits, daidir, opts, design=None) -> DeckResult` (path, engine,
analysis, stop, stop_synthesized, osdi, notes, opens, start: the `.tran` TSTART, 0 when none) composes a
deep copy of the IR; everything below is
ordinary IR built with `netlist/tables.py`'s builders (`code_uri`, `code_source`, `dc_source`, `gcond`,
`shunt`); the emitters know nothing about co-simulation except `Source.code_uri`. `design` is the parsed
`design.vhd`, which proves `d2a powernet` levels (§3.3). Errors are raised as one `NoteError` after each
stage.

- **Cut instances:** one `x` instance `xv_<name>` per cut instance (`names.xname`: `xv_` + the Verilog path
  without its top component, lowercased, non-`[a-z0-9_]` → `_`), master = that instance's subckt (per path,
  `use_spice -inst c:s`), nodes in the post-ground-pass port order:
  - a cut-port bit → its analog node;
  - a `port_connect`ed port → its net, an `-inst` statement for that instance winning over the cell-level
    one; resolution, errors and the floating-net warning as in §2.2;
  - `snps_open` → a fresh `nc_<xname>_<port>` node with an `rsh_` shunt, recorded in `DeckResult.opens` for
    the IE report;
  - a subckt port with no cut-port bit, no `port_connect` and no `snps_open` is an error ("port P of subckt S
    is not connected for SPICE instance I (no Verilog port maps to it, no port_connect, no snps_open)"), or,
    when only `-inst` statements for other instances connect the port, the §2.2 coverage error;
  - a `.global`-named port bound to a different net is an error (§4.3.4 step 7);
  - a Verilog parameter whose value on the instance differs from the cell default (`cut.param_overrides`
    over `CutInstance.params`, every elaborated value of the instance – integer, real or string, §5.4) is an
    error, "<path>: parameter override <p>=<v> on SPICE instance <path> is not passed to subckt <s>", unless
    the cell is multi-view and the parameter is one its port ranges depend on (`deck.range_params`: the
    parameters named as whole identifiers, case-sensitive, in a port's range text, followed through parameter
    defaults: `localparam W = N*2` under `[W-1:0]` exempts W and N). A localparam, a body parameter beside a
    parameter port list, and a value that differs only because its default expression uses a parameter the
    instance changed (`localparam real LSB = VREF/(1<<N)` under `#(.N(4))`) are not overrides. Covered: real
    and string `#()`, positional `#(0.9)`, real `defparam` (hierarchical too), an untyped parameter given a
    real value, a parameter declared in the module body, and `param_pass enable` with a real override.
    shells.py already refuses `#(...)`, `#n` and `defparam` on a SPICE-only cell.
- **Levels:** §3.3 (`_levels`).
- **Bridges** (`names.build_bridges`, §5.6), per analog node at its host bit; `<n>` is the node name,
  `<base>` its bridge base name:
  - **D2A** (gated): `vd_<n>` V source on `<n>_d` with `d2a:<base>__d`; `ve_<n>` V source on `<n>_e` with
    `d2a:<base>__e`; and one gated-conductance element `i(<n>_d → <n>) = v(<n>_e) * (v(<n>_d) - v(<n>)) / rr`,
    `rr = r_series` (500.7 Ω by default). The enable carries the conductance as a fraction of `1/rr`: 1.0 for
    a strong driver, `weak_frac` for a weak or pull driver, 0.0 for Z.
    - VACASK: `g_<n> (<n> <n>_d <n>_e) vamos_gcond rr=…`, the module `vamos_gcond(n, nd, ne)` of
      `vamos/ams/va/vamos_ie.va` (`I(nd, n) <+ V(ne)*(V(nd)-V(n))/rr`), compiled at compile time and loaded by
      a path relative to the deck (below).
    - Xyce: the B source `bg_<n> <n>_d <n> I={V(<n>_e)*(V(<n>_d)-V(<n>))/rr}`.
    - Polarity test: `v(nd)=1`, enable 1, 1 kΩ load, `rr=500` → `v(n)=0.6667`; enable 0 → 0 (both engines).
  - **D2A with `powernet`** and **RD2A**: an ideal V source `vd_<n>` on `<n>` with `d2a:<base>__d`; no enable,
    no element, no shunt.
  - **A2D / RA2D:** `ia_<n>` I source (0 A) on `<n>` with `a2d:<base>__a`.
  - **BIDIR:** the gated D2A elements plus the A2D probe on the same node. A static pull moved off the net
    (§5.4) is carried by the gated D2A itself (enable `weak_frac` toward the pull level whenever no external
    strong driver is active); there is no separate resistor.
  - **POWERNET** (constant) and **REMOVED with `dc=`:** an ideal DC V source `vs_<n>` on `<n>`; no bridge.
- **Shunts:** `rsh_<n>` (1e12 Ω to 0, the same element on both engines) on every analog node the AMS layer
  creates or bridges, except a node tied to an ideal source (POWERNET, RD2A, a powernet D2A, REMOVED with
  `dc=`): every `nc_*`, private unconnected-bit, D2A, BIDIR, A2D-only, RA2D, THROUGH, DISABLED, NONE and
  REMOVED-without-`dc=` node. (A gated D2A has no DC path while its enable is 0.) Shunts are listed in the IE
  report. The enable node `<n>_e` has only its `ve_` source: Xyce warns "connected to only 1 device
  Terminal", which simv drops (§6).
- **URIs:** `code:libcosim_bridge.so:vacask_bridge_init:<d2a|a2d>:<name>` (VACASK) /
  `code:libcosim_bridge.so:nvc_bridge_init:…` (Xyce); only `deck.py` builds them (`tables.code_uri`).
- **Analysis:** the first `.tran` of the netlist (evaluated, with its `maxstep`, §4.3.5); every other analysis
  line (`.op`, `.dc`, `.ac`, a further `.tran`) is a warning ".<kind> ignored in co-simulation (only the first
  .tran runs)"; `choose -skipdc` sets `uic`; `--vamos-analog-maxstep` replaces the `.tran`'s maxstep when
  given. Without a `.tran`, vamos synthesises `tran step=1e-11 stop=<stop> maxstep=<maxstep>` with `<stop>` =
  `--vamos-analog-stop` (default 3600 s; above 9000 s it is clamped with a warning: nvc time saturates near
  9223 s) and `<maxstep>` = `--vamos-analog-maxstep`, else the netlist's `.option delmax` (a note "no .tran:
  maximum time step <v> s, from .option delmax"; round-6 repair), else 10 ns, and prints a note "no .tran: the
  run ends at $finish/$stop or at <stop> s" (`stop_synthesized` in the record; simv repeats it). The same
  TSTEP/TSTOP give the parser's source defaults (`ParseOpts.synth_step`/`synth_stop`). When
  `--vamos-analog-maxstep` replaces the `.tran`'s maxstep, deck.py notes ".tran: maximum time step <v> s, from
  --vamos-analog-maxstep (in place of <old> s)" and flow.py drops the parser's computed-bound note
  (`flow._netlist_notes`); `--vamos-analog-stop`'s clamp warning is printed once (step 5; deck.py keeps only
  an error from `analog_stop`).
- **Saves:** the netlist's `.print`/`.probe` tran probes; with none, every node voltage (VACASK `save
  default`, Xyce `v(*)`); bridged nodes are always saved (unless every node already is). Every `v()` target
  must name a node of the deck (a top-level or `.global` net, or `<X instance>.<node>` through the hierarchy),
  else ".print/.probe: v(X): the deck has no node X …" (on both engines at compile time; Xyce used to fail
  only at run time). A subckt-port alias `v(x1.p)` is saved as the node its scope owns (VACASK rejects port
  aliases: "Node 'xd:a' not found"). XA cfg `probe_waveform_voltage` patterns are VCS globs matched against
  the deck's nodes under their VCS names: a netlist net by name, an analog node by its canonical name and
  aliases, a node inside a cut instance as `<Verilog path>.<subckt path>.<node>`; each matched node is saved,
  a pattern matching every node saves `v(*)`, and an unmatched pattern is the warning "probe_waveform_voltage
  P matches no node of the deck …". `probe_waveform_current` patterns match the elements the same way: V, L,
  E and H get an `i()` save, any other matched element is a warning. An `i()` of a missing element, or of one
  with no branch current the engine can save, is still an emitter error.
- **Verilog-A (VACASK):** `vamos_ie.va` and each user `.hdl` file are copied to `ams/va/<k>_<stem>.va` (`k` =
  0 for `vamos_ie`) and compiled with `openvaf-r <va> -o <osdi>` in the source's directory
  (`engines.openvaf()`; missing → "cannot find openvaf-r (set VAMOS_OPENVAF)"; one that cannot be run →
  "cannot run openvaf-r <p> (VAMOS_OPENVAF): <reason>"; a failed compile is an error with its output); the
  decks (`vamos.sim`, `smoke.sim`) load the `.osdi` files by a path relative to the deck's directory
  (`../va/<k>_<stem>.osdi`), where VACASK resolves a relative load standalone and through its C interface,
  so a copied or moved daidir loads its own. Xyce compiles Verilog-A itself (§4.5).
- **Smoke check:** a copy of the deck (`tables.smoke_netlist`: every value/probe `code_uri` source replaced by
  DC 0, every `__e` enable source by DC 1.0) with an operating point in place of the analysis, run standalone
  at compile time (`vacask.smoke`, `xyce.smoke`, §4.4–4.5). A failure is a compile error with the engine's
  message. `--vamos-no-deck-check` skips it.
- Helpers: `seed_names(nl)` (every user top-level name, for the `NameAllocator`), `analog_stop(opts, notes)`,
  `driver_level`/`held_level` (§3.3), `code_sources(path)`/`deck_uris(path)` (the agreement check, §5.7),
  `enable_nodes(path)` (the `<n>_e` nodes, for simv's output filter).

## 5. The digital cut – `ams/verilog_ports.py`, `ams/shells.py`, `ams/vhdl.py`, `ams/cut.py`

Bridge signals are VHDL `real`, one per port bit and kind, declared inside a per-variant clone of the cut
cell. The boundary file points inside each instance (`.<labels>.<bridge signal>`), so every elaborated path
gets its own bridges even when architectures are shared. The parent nets keep their digital types;
translated `logic3d` nets are never boundary signals (cosim converts by byte size, and `logic3d` is broken
in both directions).

### 5.1 Cells, ports and shells

**Ports of a cell** (`CutCell.ports`, §5.7):
1. **Multi-view cell** (a user Verilog module of the same name; `use_spice`/`partition`): the header is
   parsed from `pp.orig.v` (`verilog_ports.module_header`), ANSI and non-ANSI: ordered ports, direction,
   range text, `real`/`wreal`, parameters with defaults (and `Header.range_params`, the parameters the port
   ranges depend on, directly or through body parameters; deck.py applies the same rule through its own
   `deck.range_params`, because `CutCell` does not carry the header, §4.7). Refused: a port
   list with port expressions (`.a(x)`) or concatenations (tgt-vhdl then orders entity ports by its signal
   table), interface, user-typed, `ref`, unpacked, multi-dimensional or `integer` ports, a port listed twice,
   a range that is not `[msb:lsb]`, a missing direction, type parameters, no or several definitions.
2. **SPICE-only cell:** ports come from the subckt (`Subckt.ports`, after the ground pass), in order, minus
   `port_connect`ed ones (`ShellResult.removed`). Bus members are grouped by `bus_format` under their base
   name, wherever they appear; the Verilog port appears at its first member's position. Range direction per
   `port_index_order`: `dec` → `[max:min]`, `inc` → `[min:max]`, `same` (default) → the order of appearance,
   which must then be monotonic (else an error suggesting `port_index_order`). Direction from `port_dir`,
   else **auto**. The module name is spelled as the Verilog instantiation spells it (the "Unknown module
   type" name); each port as the instances' named connections spell it (matched case-insensitively to the
   subckt port), else with the subckt's original spelling (`Netlist.spelling`), or as a `port_map` rename
   gives it; instances that spell one port differently are an error; a SPICE port name that is not a Verilog
   identifier becomes an escaped identifier. `CutCell.origin` is the subckt's origin.

**Bit ↔ SPICE port map** (`portmap.bind_bits(cell, instance portmap, Verilog ranges, Subckt.orig_ports)`,
phase 0): explicit `port_map` items first; then the default rule – by name (scalar `p` ↔ `p`; bit `p[i]` ↔
`p` + each `bus_format` with `%d` = i, the first that exists), **by position** (Verilog bits in declaration
order against the **original** `.subckt` port list: before the ground pass, `port_connect`ed ports
included), or open. A bit that lands on a ground or `port_connect`ed SPICE port is **passive** (no node of
its own, no IE, driven Z if its mode is out/inout; a digital driver on it is a note). A Verilog bit with no
SPICE port and no `snps_open` is an error.

**Shells** (`shells.build`), one Verilog module per cut cell, appended to `pp.v` after `` `resetall ``
(which also clears `` `celldefine `` and `` `unconnected_drive ``), `` `timescale <p>/<p> `` (the job
precision) and `` `default_nettype wire ``:
- multi-view: the header's imports, `#(...)` parameter port list and the body's
  `parameter`/`localparam`/`specparam`/`import` items (plus the functions and typedefs they, the header's
  parameter port list or the port ranges use: `module_header` adds the identifiers of the `#(...)` text, so a
  function only a header `localparam` calls is copied too) copied verbatim; the port declarations synthesised
  in **non-ANSI** form (`<dir> [range] p;`,
  `<dir> real|wreal p;`), because an ANSI declaration cannot use a body `localparam`; `reg`/`logic`/`bit`,
  signedness and net-type words are dropped (every logic port is a net; connectivity is unaffected); body
  items blanked;
- SPICE-only: `module <cell> (<ports>); input|output|inout [msb:lsb] <port>; … endmodule`, with `inout` for
  auto ports, subject to the direction probe;
- **markers:** every non-`real` port bit whose `shell_dir` is `output` or `inout` gets a scalar marker gate
  `bufif1 vamos_ams_hiz_<j> (<port>[<i>], 1'b0, 1'b0);` (`<port>` alone for a scalar), where `<j>` is a
  running index over the shell's markers (a `<port>_<i>` scheme collides for a bus `d[1:0]` beside a scalar
  `d_0`). A port whose range is not constant (`[W-1:0]`) gets its markers from a generate loop over the
  header's range text: `genvar vamos_ams_g_<port>; for (vamos_ams_g_<port> = ((<msb>) < (<lsb>) ? (<msb>) :
  (<lsb>)); vamos_ams_g_<port> <= ((<msb>) > (<lsb>) ? (<msb>) : (<lsb>)); vamos_ams_g_<port> =
  vamos_ams_g_<port> + 1) begin : vamos_ams_hiz_<port> bufif1 vamos_ams_hiz (<port>[vamos_ams_g_<port>],
  1'b0, 1'b0); end`. Markers give `inout resolved_logic3d[_vector]` on inout ports and keep the vector-bit,
  generate, instance-array and plain bit-select output connections that the iverilog core otherwise drops
  (each then a `LPM_*`/`tmp_*` temporary). (The array form `bufif1 x [3:0] (p, …)` translates to one
  ill-typed scalar `sv_bufif1` that crashes `nvc -e`; it is not used.)

**Masking.** Multi-view cells are blanked in `pp.v` by a scanner aware of comments, strings and `(* … *)`
attributes; it recognises `module` and `macromodule`, an optional lifetime (`module automatic|static
<name>`), leading attributes and `endmodule : label`. Every definition of the cell is blanked (newlines
kept: `pp.v` and `pp.orig.v` share line numbers, which the origin map turns into the user's file:line);
afterwards the cell must have exactly one definition in `pp.v`, its shell, else error. A cell defined only in
a `-y` library needs no masking. The header text for the shell is taken before blanking.

**Cell set** (step 6, `shells.find_cells`; `cell_set` is its §5.8 form): `iverilog -g2012 -tnull -s <top>` on
`pp.orig.v` with every multi-view candidate (a Verilog module a `use_spice`/`partition` glob names) blanked
and replaced by a placeholder – its header shell plus an instance of the nonexistent module
`vamos_ams_probe_<k>` – so "Unknown module type vamos_ams_probe_<k>" appears exactly for the candidates
elaborated under the top (instantiations inside masked bodies and in never-taken generate branches do not
count; named connections stay valid). A candidate whose header cannot be parsed keeps its definition with the
probe inserted after its header, and its header error is reported only if it is instantiated. Every other
unknown module is a SPICE-only cell: it must name a subckt (`use_spice c:s`, else its own name,
case-insensitively), else the error "module X not found in Verilog sources or SPICE netlists" ("module X:
use_spice binds it to subckt S, which is not in the SPICE netlists" when `c:s` names one; a multi-view cell
without its subckt is "cell X: no SPICE subckt S in the netlists"). A subckt the parser left out does not
count (§4.3.2). A `use_spice`/`partition` cell never instantiated gets a note ("cell X not instantiated
under <top>; ignored") and no shell.

**Direction probe** (auto ports of SPICE-only cells). Declaring an auto port `inout` breaks a variable actual
(iverilog: "Inout port expression must support continuous assignment"). So:
1. `iverilog -g2012 -tnull -s <top>` with every auto port `inout`; an auto port whose actual is a variable, a
   constant or an expression is reported (": Port N (p) of c is connected to E");
2. those become `output` in a second probe; an instance whose variable is then reported ("Cannot perform
   procedural assignment to variable 'x' because it is also continuously assigned.", a declaration
   initializer included) or whose actual is ("Output port expression must support a continuous
   assignment.": a constant, an expression, a variable with another driver) needs `input`; the others need
   `output`;

   **2b. Wrapper inputs** (`shells.wrapper_inputs`): iverilog accepts an inout cell port on an `input` port of
   the enclosing module silently, and coerces that input to inout (port coercion, as VCS does) only where the
   module's instance connects it to a collapsible net – then the nets are one, as the cut joins them. Where an
   instance of that module connects it to a variable, an expression or a constant, leaves it open, or is an
   instance array, or the module is the top, the port stays an input: Verilog drives the module's net from
   the actual through a one-way buffer, and the cell could drive only the inside. Such an elaborated instance
   needs `input` (a whole-port actual or a bit or part of one), following chains of input ports up the
   hierarchy. The scan is structural: only instances in modules under the top count, generate conditions are
   not evaluated, and a variable is one the declaration scan recorded (user-typed variables are not seen).
   Forcing every wrapper-input auto port to `input` instead would break coerced, wire-fed pads (a tri-state
   pad behind a coerced wrapper input stays BIDIR and reads 1 after release);

3. the result is per cell: instances needing different modes are an error naming them and asking for
   `port_dir`;
4. a final probe with the decided directions must be clean; any other probe error is printed at the user's
   file:line (with hints for `port_connect`ed ports and hierarchical references into SPICE cells) and stops
   the compile; iverilog's "coerced to inout" warnings (caused by the markers) are dropped; warnings naming a
   cut cell (port width mismatches) are passed on;
5. the result goes into `CutPort.shell_dir`; `declared` stays `auto`; `ShellResult.directions` holds the
   reason per cell port (`auto->input (<why>)`) for the IE report.

A compile runs up to five whole-design `iverilog -tnull` elaborations (the precheck on the original sources,
the cell set and up to three probes).
**Parameter overrides** (`#(...)`, `#n`, `defparam`) on a SPICE-only cell's instance are errors
("parameter override on SPICE instance <scope>.<inst> is not passed to subckt <s>"); on a multi-view cell,
deck.py decides (§4.7).

Not exercised or approximate (**open**): a SPICE port name that is not a Verilog identifier becomes an
escaped identifier, which no test takes through the translator; `.*` connections bind by the shell's
spelling, case-sensitively; the probes name instances by source line, so several instances on one line are
reported together; iverilog reports a missing `` `include `` one line late, and vamos passes that on.

### 5.2 Translation and backstops

```python
job2 = dataclasses.replace(job, sources=[Source(<abs daidir>/ams/pp.v, "sv")], lib_files=[], tops=[top],
                           defines={}, incdirs=[], timescale=None, override_timescale=None)
NvcBackend(job2, con.out).analyse()
```
Then: every deferred module (`sv2vhdl:deferred` stub in `design.vhd`) is an error, "sv2ghdl could not
translate module <m> (see <daidir>/nvc/iverilog.log)" – `iverilog.log` holds iverilog's messages in sections
(T4); `design.vhd` must exist ("translation produced no design.vhd"); "Unsupported system task/function"
comments per §1.3; the top entity, in `cut.analyse` (§1.4). A SystemVerilog class or a fork in the design
makes its module a deferred stub with a located reason (T17, §7), so it ends here too, and since round 6
so does a module whose back-end run reported an error while exiting 0 (T4): the module is named with that
error, where before the statement was left out and the design simulated X. An unused module whose own
translation reports such an error is refused as well (it compiled before). When nvc cannot analyse the
module-by-module translation but the whole-design one analyses, the compile goes on and each module nvc's
errors are in gets a note on stderr (T4).

### 5.3 Parsing `design.vhd` – `ams/vhdl.py`

tgt-vhdl output is regular; the parser handles exactly what it (plus sv-rename-variants/sv-dedup-vhdl)
emits and raises `NoteError` naming file:line on anything else. It reads every genuine tgt-vhdl output of a
266-file corpus and of 255 ivtest translations.
- **Entities:** name, ports (name, mode, `TypeSpec` with range and kind `logic`/`real`/`other`), the
  preceding `-- Generated from Verilog module <name> (<file>:<line>)` provenance comment (`Entity.module`),
  the `--   P = v` lines and `attribute nvc_verilog_params of <entity> : entity is "P=v …"` (the variant's
  parameter values, localparams included), the verbatim port clause and context clause (for clones), the
  `sv2vhdl:deferred` stub marker.
- **Architectures:** signals with their initial value (`SignalDecl.init`, and `init_elems` element by
  element) and trailing comment – `-- Declared at <file>:<line>`, "Needed to connect outputs" (a `_Readable`
  shadow), `-- Temporary created at <file>:<line>`, `-- Port buffer of input <port> of instance <label> …`
  (`SignalDecl.port_buffer`, §5.4); aliases (`alias x is y(3)`, `alias x is y(4 downto 2)`, T2); constants;
  the signals each impure function reads; and the concurrent statements in textual order (`Stmt`):
  - kind `instance`: label, library, entity, port map (`Assoc`: formal, actuals and slices, reads, `open`,
    `const`, `z_only`, `weak_only`, and a constant actual element by element, `Assoc.elems`), and the
    comments above it – `-- Verilog instance: <relative path>` (T3, `Stmt.vpath`), `-- sv_strength: …`
    (`Stmt.strength`), `-- Generated from instantiation at <file>:<line>` (`Stmt.origin`);
  - kind `process` (a process, a concurrent signal assignment, a selected assignment): its assignments
    (`Assign`: `<=`, `:=`, `force`, `release`; target bits; the right-hand side as a plain copy, and element by
    element as `Assign.parts` – references and logic3d literals – so `pa <= t5 & tz & t3` and `v <=
    logic3d_vector'(L3D_Z, L3D_1)` can be read bit by bit; `z_only`, `weak_only`, `const`, `delayed`), whether
    it has control flow (`Stmt.control`, `Stmt.straight`) and what it reads: `sens_reads`, `cond_reads`,
    `ctrl_reads`, `reads`;
  - kind `call` (a concurrent procedure call or assert).
- `literal_elems(toks, width=None)`: the elements of a constant logic3d value – a literal, a positional
  aggregate with or without its type mark, `(0 => x)`, a range choice, `others` when the width is known, and
  `&` of these; the logic3d literal aliases folded (`L3D_0ZX` is `l3d_z`).
- Naming helpers: `make_safe_name(verilog, entity_collision=False)` (tgt-vhdl's rule: a leading `_` → `sig`
  prefix, a trailing `_` → `sig` suffix, `__` collapsed, `_sig` on an entity-name collision and on a reserved
  word), `safe_name_matches(vhdl, verilog)` (case-insensitive; allows the entity-collision `_sig` and the
  `_<n>` that `avoid_name_collision` appends after a case-only clash, `out`/`OUT`), `valid_entity_name`,
  `VHDL_RESERVED`.
- **sv2vhdl library entities** (`sv2vhdl.sv_*`, not in `design.vhd`): a port-mode table generated from
  `nvc/lib/sv2vhdl/{sv_gates,sv_mos,sv_pull,sv_tran,sv_tristate}.vhd` by `vhdl.render_modes_module()` and
  checked in as `ams/sv2vhdl_modes.py`; a test regenerates and compares it. An unknown library entity is an
  error.
- **Marker footprint** (dropped from clones, never a driver): instances labelled
  `sv_bufif1_vamos_ams_hiz*_inst` (generate-loop markers included), their `tmp_ivl_*` feeder signals and
  constant processes, and the concatenation process `<port> <= tmp & tmp & …`.
- `vhdl.parse(path)` / `parse_text(text, path='design.vhd') -> VhdlDesign` (entities, archs, order;
  `variants(module)`, `where(line)`). `Ref.indices` are VHDL indices left to right; `Range.offset()` gives the
  bit offset from the right bound, the bit number every other module uses.
- Limits: VHDL-2008 external names are read as opaque (tgt-vhdl does not emit them today), and the signals
  an impure function reads are attributed to the statement that calls it.

### 5.4 Cut analysis – `cut.analyse(...) -> CutAnalysis`, `cut.assign_roles(...) -> List[AnalogNode]`

1. **Variants and cut instances.** Entities whose provenance comment names a cut cell are its variants, found
   by the comment above `entity X is` whatever the entity name (`cell__xxxx`, `buffer_module__c186`,
   `dac1__b934`, …), never by a name regex. `<name>` is compared exactly with `CutCell.name` (the Verilog
   module spelling); only the cell→subckt binding is case-insensitive. Walk the elaborated instance tree from
   the top entity, in textual statement order within each architecture, depth first (**walk order**). An
   instance bound to a cut variant is a **cut instance** and is not descended into. Its path is its label
   list; its Verilog path is `<top>.` + the T3 relative names along the labels; its VHDL `'path_name` is
   `:<top>:<labels…>:` (lowercase). Per-instance subckt and port map come from the covering `use_spice`
   statement (marking `use_spice_inst#i.j`): a covering `-inst` statement takes precedence over the
   statements without `-inst`, of which the last wins; two or more covering `-inst` statements are an error;
   an instance no statement covers is an error only for a multi-view cell that has `-inst` statements and no
   statement without `-inst`. `CutInstance.spice` comes from `portmap.bind_bits`; `CutInstance.params` holds
   every elaborated parameter value of the instance as tgt-vhdl printed it: integers from
   `nvc_verilog_params`, reals and strings from the `--   P = v` lines, both taken from the top module's own
   translation in `nvc/_mods.vhd` (design.vhd can keep another translation's copy of a same-named variant:
   before T12, variant names ignored real and string parameters); each value is compared with the cell's own
   defaults, as printed for the cell elaborated alone (`cut.param_overrides`, §4.7). Also checked here: the §1.4
   backstops; `use_spice -inst` giving a SPICE-only cell its own subckt or port map (error); a shell variant
   containing a module instance (error); an unknown `sv2vhdl` entity or library (error). A statement in a cut
   variant's body other than the marker footprint and `<port>_Reg` shadows (a parent tie the translator
   folded into the shell) is kept in the clone, with the note "statement <s> in shell variant <entity> is kept
   as a digital driver/reader (a connection the translator folded into the cell)".
2. **Port binding per variant.** Entity ports are bound to `CutCell.ports` **by position** (tgt-vhdl keeps
   declaration order but renames reserved words and odd underscores, and adds `_sig` to a port named after an
   entity in some variants only, so names cannot be joined). Hard checks: equal counts; per position,
   `vhdl.safe_name_matches` (the VHDL name is `make_safe_name` of the Verilog name, optionally plus the
   entity-collision `_sig` or a case-clash `_<n>`); the kind (`real` vs logic) agrees; the width equals the
   evaluated Verilog width; the mode follows `shell_dir` (input → in, output → out, inout → inout; a
   `buffer` port is refused); every bit of an output/inout port's concrete range has a marker. Verilog bounds
   come from the header's range text evaluated with the variant's `nvc_verilog_params` (`cut.veval`:
   integers, sized literals, parameters, `$clog2`, unary/binary/ternary operators); the VHDL range is always
   `(w-1 downto 0)` and gives only the width (VHDL index = bit offset). A 1-bit Verilog bus `[0:0]` may come
   out as a scalar VHDL port (`VariantBind.vhdl_vector`).
3. **Nets** (union-find over segments `(scope path, signal, bit)`), built by a search from every cut-port bit
   through the elaborated tree:
   - a port association of a **user-module** instance (not a cut instance, not a library entity) unites the
     child formal bit with the parent actual bit (simple names, indexed names, slices); it is a net join,
     never a driver or reader in itself – the drivers and readers are the statements found inside;
   - descend into non-cut children through any connected formal (a wrapper's output may carry another cut
     instance's port);
   - aliases unite, T2's `SW…_b` aliases included;
   - **trans** (round 6; `_Index.switches`, `_Analyser._switch_joins`): an `sv_alias` or an `sv_tran`
     instance between two single bits joins their nets before the port-buffer decisions; the switch is a
     wire, neither driver nor reader, so a pull or a driver on the far side is seen as what it is. Before,
     the tran was a strong driver and a reader of each side, which left an inout SPICE pad behind a pulled
     tran at 0 V while the digital side read 1, and made a SPICE output behind a tran a compile error. A
     tran with a supply driver on either side stays a strong driver and a reader of each side, because a
     nonresistive switch reduces supply to strong (IEEE 1364); `rtran` and `tranif*` stay drivers and
     readers. A tran between two nets that both reach SPICE ports therefore joins them into one analog node,
     like a wire;
   - a `_Readable` shadow (declared with "Needed to connect outputs") is united with the out port it is
     copied to or from; the copy (`P <= S`, `S <= P`, or the matching `:=` inside a `comb_fused_N`
     process) is neither driver nor reader; the same for a formal mapped straight to an out port. The four
     shapes: (a) two cells in one wrapper sharing an output net with different formal names (copies fused
     into `comb_fused_N` as `:=`); (b) the same with equal formal names (the second formal maps straight to
     the OUT port); (c) a wrapper `inout` port (no shadow); (e) cell → wrapper output → second cell (the
     shadow is named after the child formal, `y <= vo_Readable`);
   - **port temporaries are wires:** an `LPM_*`/`tmp_ivl_*` signal (no `-- Declared at` comment) that is the
     actual of a port association, is assigned by at most one statement and is otherwise used only in
     single-assignment copy, slice or concatenation statements (`LPM_q_ivl_2 <= v(0)`, `w(4 + 1 downto 4) <=
     LPM_d0_ivl_1`, `v <= LPM_d1 & LPM_d0`, or `:=` inside `comb_fused_N`) is united bit by bit with the
     bits it is copied to or from (concatenation operands map to the target's bits from the left; slice
     bounds may be integer expressions); those statements are neither drivers nor readers. Wire temporaries
     extend through chains: a `tmp_ivl_*` whose "Temporary created at" equals the instance's "Generated from
     instantiation at", that shares a copy with a wire temporary and is used only in copies, is part of the
     same port expression (`.d({code[0], code[1]})` → `tmp_ivl_16 := code(0)` in `comb_fused`). A temporary
     with any other use, one assigned twice, or a copy with computed indices stays digital;
   - **port buffers** (one-way joins): the iverilog core buffers an `input` port of a user module whose net
     something inside also drives (a cut port's marker included) when the instance connects it to a
     variable, a select of one, an expression or a constant; tgt-vhdl draws the buffer, with any pad, prune
     or instance-array split the core puts behind it, once per instance in the parent as `PB_<label>_<port>
     <= <actual>` (T8 port networks; `vhdl.SignalDecl.port_buffer`). As in Verilog the buffer is
     one-way: a cut port behind it can drive only the wrapper's side W of the port (its net without the
     copy). Each elaborated copy is decided per bit by what else is on W:
     - the inside also drives W: W is a net of its own, driven by the copy (report line "port buffer: input
       port <p>, fed one way from variable <v>, is also driven inside <scope> (a net of its own)");
     - a cut **output** on W: error ("port a of tb.we.u is an output behind input port tb.we.a, fed one way
       from variable tb.clk (a port buffer): the cell could drive only the wrapper's side of that port,
       against the buffer; declare it input (port_dir -cell src (input a;)), or connect a net to tb.we.a");
     - an **inout** cut port on a W that the inside also reads: error (what the cell drives there would reach
       only the wrapper's side, which vamos does not model; declare it input – "(in the Verilog view of X)"
       for a multi-view cell);
     - otherwise W joins the actual's net and the copy is a wire, neither driver nor reader; W's cut ports act
       as inputs on it (never drivable, output or BIDIR candidates, but they may host a D2A), exactly as with
       `port_dir input`, and the IE report says so (`direction: inout→input (one-way port buffer: …)`). A
       select (`.a(rv[1])`, translated through `tmp_ivl_1 <= rv(1)`) joins too, so rules on `tb.rv[1]` reach
       the cell. A constant actual is not a copy: it drives W.
   - an inout cut port whose actual is still a temporary after T2 (an `SW*_b` declared as a **signal** – T2's
     one-way fallback – or `LPM_*`, `LO_*`, `tmp_*`), or any cut port whose actual is a temporary with more
     than one driver, is an error naming the instance and port and suggesting `port_dir` or a plain-net
     connection: "inout port <p> of <inst> is connected through translator temporary <t>, a one-way copy (the
     translator joins this bit- or part-select one way only, e.g. a select of an input or output port of the
     enclosing module); use port_dir or connect a plain net" (it used to say "… needs translator patch T2 …",
     and until the round-6 repair gave "a tran primitive on a select" as its example, though since round 6 a
     tran on a select is one way only on a select of a module port, T2), or "port
     <p> of <inst> is connected through translator temporary <t>, which has <n> drivers; …". (An `SW*_b`
     declared as an **alias** is the vector element or slice itself.) The net of a real cut port must be real
     throughout (`_real_backstop`, round 6, defence in depth): a non-real signal on it, or an expression
     actual reading one, is the error "real port <p> of <inst> is on a net that is not real throughout:
     <signals>; its analog value cannot pass through a logic signal (connect a real variable, a wreal net or
     a real expression)".
4. **Drivers and readers** per net bit:
   - drivers: assignment targets (`<=`, `:=`, including initial-block deposits); out/inout formals of
     **sv2vhdl library** instances (mode table); constant ties. Drivers are decided **bit by bit**: an
     `L3D_Z` element of a constant value, of a constant port actual or of a positional aggregate, and a bit
     copied from a translator temporary that is only ever assigned Z (every assignment unconditional,
     undelayed and Z; the least fixed point, so a copy cycle never qualifies; temporaries touched by an
     instance, a call or a condition excluded) drive nothing – `pa <= t5 & tmp_z & t3`, `wv <=
     logic3d_vector'(L3D_Z, L3D_1)`, `d => logic3d_vector'(L3D_Z, L3D_1)`, `wd := t9 & tmp_z & t12`; a weak
     element (`L3D_L`, `L3D_H`, `L3D_W`) is a weak driver of its bit; anything that cannot be read element by
     element falls back to the whole-target rule (a driver), so nothing is dropped silently;
   - a **declaration initializer** drives, with its own literal, every bit of the signal that has no other
     source (no value-driving assignment, no copy into it, no out/inout formal): with `reg [5:3] ca =
     3'b001` and only `ca[4]`, `ca[5]` reassigned, `ca[3]` is driven 1; X/Z/U bits drive nothing; a bit whose
     only assignments are Z-only is still driven by its initial value (a VHDL process driver starts at the
     signal's initial value). tgt-vhdl makes an initializer only the signal's initial value, so without this
     rule a cut input on it would read "not driven" and its node would sit at 0 V;
   - every statement in a cut variant's body other than the marker footprint and `<port>_Reg` shadows is
     treated as a driver/reader of the nets it touches (and gets a note, since shell bodies are blanked);
     `cut.emit` copies it verbatim;
   - `<= force` / `<= release` on a cut net: error ("force/release on mixed-signal net <name> is not
     supported");
   - strength class per driver: `supply1`/`supply0` (an `sv_pullup`/`sv_pulldown` preceded by
     `-- sv_strength: supply1 supply0`; vectors mark every bit; `pullup (supply1)` with `supply1 highz0` is a
     pull, not a supply net), `pull_up`/`pull_down` (other `sv_pullup`/`sv_pulldown`, including the pulls T5
     puts on `tri1`/`tri0` nets), `weak` (`sv_strength_buf` with weak/pull strengths, weak literals), `strong`
     otherwise. A `tri0`/`tri1` net recorded by the declaration scan that reaches a cut port with no pull in
     the right direction is an error;
   - readers: every other reference (sensitivity lists included), and the reads of assignments to process
     variables (`vhdl.Stmt.var_reads`: tgt-vhdl's `v_nba_q := l3d_strengthen(x)` for `q <= x` in a clocked
     always block; before round 6 a SPICE output that only a register reads got no A2D, and the register
     read z for the whole run);
   - cut ports: instance, port, bit, `shell_dir`; passive bits separately;
   - `Net.variable`: the trace passed a `reg`/`logic`/`bit` declaration (the verilog_ports declaration scan
     via `pp=`, else `nvc/_norm.sv`). A declaration inside a `begin` block (a generate loop or block) counts
     too, for the warning (§0) and for the `tri0`/`tri1` rule (round 6; `cut._decl_kinds`): it is found by the
     line its signal's `-- Declared at` comment names and by tgt-vhdl's generate-path name
     (`cut._block_path`: `r` in `g[0].h[1]` is `r_g_0_h_1`, in block `b` `r_b`, in an unnamed block
     `r_genblk<n>`), and it is shown, and added as an alias, by its Verilog name (`tb.g[0].r`).
5. **Roles** – `cut.assign_roles(analysis, alloc, disabled, removal, directions=None, dumped=None)` with
   `disabled(names) -> bool` and `removal(names) -> (bool, dc)` (flow.py binds `cfg` and `hits`; both are
   asked for every net, canonical name first) and `dumped(names) -> bool`, whether the run's waves record
   the net (flow.py binds `vcs.dump_covers` to `job.dump`; round-6 repair). A net is **digitally driven** if
   it has a strong, weak or pull driver (pulls count, at weak strength); **digitally read** if it has a
   reader. A cut port is **drivable** if its `shell_dir` is output or inout and it is not behind a joined
   port buffer. **First match wins:**

   | # | net | role |
   |---|---|---|
   | 1 | `disable_ie` matches | DISABLED (warning) |
   | 2 | a `real` cut port | logic ports too → error; digitally driven → RD2A (a real output too → error); else a real output digitally read → RA2D; else THROUGH |
   | 3 | a `supply1`/`supply0` driver | POWERNET (constant; both classes → error; other drivers on it → note) |
   | 4 | a strong, weak or unknown-strength driver and an `output` (shell_dir) cut port | error naming both ("declare the port inout with port_dir") |
   | 5 | digitally driven, and a declared-`inout` cut port, or an auto port left `inout` with the net digitally read | BIDIR; pulls on it move into the deck (pull-up and pull-down both → error); other weak drivers on it → error |
   | 6 | digitally driven | D2A (pulls stay digital; the gated D2A sees them as weak) |
   | 7 | a drivable cut port, digitally read, or recorded by the run's waves (`dumped`; report line "read only by the waves ($dumpvars)") | A2D (before the round-6 repair a net only the waves record was THROUGH, z in the VCD) |
   | 8 | a drivable cut port | THROUGH |
   | 9 | exactly one cut port, no digital reference | NONE: private node (unconnected bit, listed in the report) |
   | 10 | otherwise (cut inputs, nothing drives them) | NONE with a warning "input <name> is not driven" |

   - **Hosts:** D2A → the first cut port in walk order whose VHDL mode can see the net (in or inout; an out
     port sees only its own drive) – a digitally driven net whose cut ports are all outputs (possible only
     when pulls are the only drivers) is an error suggesting `port_dir … inout`; BIDIR → the first
     declared-inout or auto-inout port; A2D → the first drivable port; RD2A → the first real port; RA2D → the
     first real output. Every other cut-port bit on the node is **passive**.
   - **`remove_d2a`** is applied after the table and removes only a D2A: a D2A net becomes REMOVED (with
     `dc=v` the §4.7 DC source, else the node is left to the analog side with a shunt); a BIDIR net is an
     error (v1); on RD2A, A2D, THROUGH, NONE or POWERNET nets it changes nothing (note "remove_d2a: no D2A on
     <node>").
   - **Pulls moved into the deck** (BIDIR): the pull statement is removed from its architecture only if every
     elaborated path through that statement needs it moved; otherwise an error ("the pull … is needed
     digitally on … but moved into the deck").
   - **Warnings:** an A2D → digital → D2A round trip through a digital buffer ("analog connection
     quantised"): a driver whose value or condition reads an A2D/BIDIR net, directly or, since round 6,
     through continuous assignments, `comb_fused` processes and gate/switch primitives on nets with no SPICE
     port (`cut._comb_sources`, memoised per net; a register or another process stops the search, which
     ends after 200000 digital nets with a note); a THROUGH, D2A, A2D, BIDIR or REMOVED net with
     `Net.variable` and two or more cut ports ("variable <v> joins SPICE ports in analog; VCS digitises it:
     <ports> share one analog node here, where VCS gives each SPICE port an interface element of its own;
     declare <v> a wire if the analog connection is intended, or connect each SPICE port to a net of its own
     (wire w = <v>;) to get VCS's digital connection", PAMS p38–39; one SPICE port on a variable is not warned); a digitally read net (in an
     architecture containing cut instances) whose only driver is tgt-vhdl's undriven-net constant (`<=
     L3D_Z`, `(others => L3D_Z)`), because a cut output connection dropped by the iverilog core looks exactly
     like this.
   - Node names come from `names.node(alloc, canonical)`; `alloc` is seeded by flow.py with
     `deck.seed_names(nl)`. `AnalogNode.shunt` is set by role (deck.py clears it for a powernet D2A);
     POWERNET nodes leave `dc` to deck.py (the supply class is on the net's drivers); `node.report` gets the
     cut's IE-report lines (§3.6), the direction lines using `directions` (`shells.ShellResult.directions`,
     given to `analyse` or `assign_roles`) for step-2b reasons.

### 5.5 Cut emit – `cut.emit(plan, out_dir) -> CutEmitResult`

**One clone per cut variant**, never per instance (a module instantiated twice shares one architecture, so
per-instance clones would silently give every path the last path's roles and levels).
- `entity <variant>__vams` copies the variant's port clause and context clause verbatim; every instance
  statement bound to the variant is re-pointed to it (only cut-instance statements change). Hard checks
  afterwards: no instance still binds an un-cloned cut variant; no statement is re-pointed twice.
- Per-path behaviour is fixed at elaboration by `'path_name`. `vams_cut_pkg` holds, per variant `<v>`
  (numbered in walk order, because entity names contain `__`), the sorted, padded path array
  `VAMS_PATHS_<v>`, `vams_index_<v>(p)` (a binary search; an unknown path is `report "vamos: no cut table
  entry for instance path …" severity failure` – nvc then crashes folding the constant: loud, an nvc bug;
  flow._analyse_cut then names it: "the cut has no table entry for instance path <p>, which nvc elaborated:
  an internal error of the cut (please report it, with <cut.vhd>)"), `vams_role_<v>(i, slot)`, and
  `vams_lvl_<v>(i, slot, key)`, which reads de-duplicated level sets
  `VAMS_LVLS_<v>` through `VAMS_LIDX_<v>(i, slot)`; plus the `ROLE_*` and `K_*` constants. No linear if-chain
  (O(N²) string compares for N replicas): a 2000-instance design analyses in 0.22 s and elaborates in 0.9 s.
  `slot` is the bit's flat index within the variant (ports in order, bits by offset).
- Clone-internal names, with `<k>` the port index and `<b>` the bit offset (never the variant number):
  `vams_path`, `vams_i`, `vams_r<k>_<b>`, generate labels `vams_d<k>_<b>`, `vams_a<k>_<b>`, `vams_z<k>_<b>`,
  `vams_rd<k>`, `vams_ra<k>`, and the generate-local constants and process variables of `cut.SLOT_LOCALS`
  (`VAMS_HIV` … `vams_v`, `vams_prev`, …, every one starting `vams_`); every generated name is checked
  against the entity's port and signal names, and those of `SLOT_LOCALS` are reserved whether used or not
  (a collision is an error; unprefixed, a cut port named `v` was read as the D2A process's own variable and
  its node silently stayed at lov).
- Every bridge signal is declared unconditionally at architecture level, so boundary paths are
  `.<labels>.vb<k>_<b>_<d|e|a>` (k = port index, b = bit offset; a real port is one slot, b = 0) with no
  generate label in them; only role-dependent drivers and processes sit in if-generate blocks.
- Only the node's **host** bit gets the node's role (`ROLE_CODE`); every other cut-port bit is
  `ROLE_PASSIVE`: no bridge processes, and driven `L3D_Z` if its mode is out or inout. Only host bridges are
  in the boundary file.
- A logic port bit whose role at this path is not A2D or BIDIR, and whose mode is out/inout, is driven
  `L3D_Z` explicitly (a missing driver would contribute the type's default, a weak value). Real ports are
  never driven Z.
- Port bits are indexed `p(<b>)` only when the variant's VHDL port is a vector (`VariantBind.vhdl_vector`).
- Template (per host bit; constants are elaboration-time):

```vhdl
constant vams_path : string := <variant>__vams'path_name;
constant vams_i : natural := vams_index_<v>(vams_path);
signal vb<k>_<b>_d : real := 0.0;  signal vb<k>_<b>_e : real := 0.0;  signal vb<k>_<b>_a : real := real'low;
constant vams_r<k>_<b> : natural := vams_role_<v>(vams_i, <slot>);    -- R below
vams_d<k>_<b>: if R = ROLE_D2A or R = ROLE_BIDIR generate      -- in/inout ports only
  constant VAMS_HIV : real := vams_lvl_<v>(vams_i, <slot>, K_HIV);   -- LOV X2V DR DF WF PULL_V PULL_E alike
  -- value: 1/H → HIV, 0/L → LOV, Z → hold, X/W/U → per X2V (0 LOV, 1 HIV, 2 mid, 3 hold, 4 the inverse
  --   of the previous input: HIV after 0/L, LOV after 1/H, else hold; vams_prev starts at "none")
  -- D2A enable: strong (0 1 X U) → 1.0, weak (L H W) → WF, Z → 0.0
  -- BIDIR without a strong value (external drivers are strong or Z: pulls were moved to the deck):
  --   value PULL_V with enable PULL_E when the path has a moved pull, else hold with enable 0.0
  -- 'after DR' when the value rises, 'after DF' when it falls, on value and enable alike
vams_a<k>_<b>: if R = ROLE_A2D or R = ROLE_BIDIR generate     -- out/inout ports only
  process begin
    -- vb<k>_<b>_a < -1.0e300 (real'low: no analog value yet; never '=' on reals) → X;
    -- >= HITH → 1; <= LOTH → 0; else hold, and after MIDV_T inside the window → MIDV_L
    -- (0, 1, X; code 3 = Z). The window follows the current value: 1 → falling (LOTH..HITH_HYS),
    -- 0 → rising (LOTH_HYS..HITH), X/Z → the whole band (§3.4)
  end process;
  -- A2D drives strong values (L3D_1 / L3D_0 / X); BIDIR drives l3d_weaken(...) (L3D_H / L3D_L, unknown → L3D_W)
vams_z<k>_<b>: if <logic port, mode out/inout> and R /= ROLE_A2D and R /= ROLE_BIDIR generate p(b) <= L3D_Z; end generate;
```
  Real ports: `vams_rd<k>`: `vb<k>_0_d <= p;` (RD2A); `vams_ra<k>`: `p <= 0.0 when vb<k>_0_a < -1.0e300 else
  vb<k>_0_a;` (RA2D).
- Foreign statements of a variant are kept in its clone (`-- kept from <entity> (line N)`). A pull moved
  into the deck is replaced in a clone by `-- vamos: pull <sid> moved into the analog deck (BIDIR)`, and in a
  re-pointed parent by `-- vamos: pull moved into the analog deck (BIDIR):` followed by the statement,
  commented out.
- `ams/cut.vhd` holds, in order: `vams_cut_pkg`, the clones, then the re-pointed parent architectures (each
  re-emitted whole, entity unchanged). It is analysed after `design.vhd` into the same work library
  (`-M 2g -H 1g`); an analysis error, or "should be reanalysed", is fatal.
- **Boundary file:** exactly one `names.boundary_line(b)` per bridge in `plan.bridges`, no comment lines:
  `D2A .<labels>.vb<k>_<b>_d <base>__d rise=<s> fall=<s>`, `D2A .<labels>.vb<k>_<b>_e <base>__e rise=<s>
  fall=<s>` (the enable is a D2A line), `A2D .<labels>.vb<k>_<b>_a <base>__a`. vamos keeps bridge base names
  ≤ 200 characters (hashing, §5.6; the de-duplicating `_1`, `_2`, … and the `__d`/`__e`/`__a` suffix come
  after that), boundary paths ≤ 511 (`names.build_bridges`: a compile error naming the instance) and lines
  ≤ 1000 (`boundary_line`: a compile error naming the boundary path). These are vamos's own limits: nvc
  allows 255-character names, 4095-character paths and lines of any length since P4, and the agreement check
  (§5.7) enforces the 255.

### 5.6 Names – `ams/names.py` (phase 0)

One `NameAllocator` per deck, pre-seeded with every user top-level node, instance, model and subckt name
(`deck.seed_names`), allocates every generated name case-insensitively, derived ones included:
`names.node` reserves `<n>`, `<n>_d`, `<n>_e` together (`take_group`; `_<k>` on a collision); `names.inst`
builds `vd_ ve_ g_ ia_ rsh_ vs_ nc_` names (`vp_` is reserved for a pull source and unused: moved pulls ride
on the gated D2A); `names.xname` the `xv_` instances. Xyce prints `g_<n>` as `bg_<n>` (§4.5).
- Deck node of an analog node: `n_` + the canonical name without its top component, lowercased,
  non-`[a-z0-9_]` → `_`.
- Bridge base name: the canonical name restricted to `[A-Za-z0-9_.\[\]<>]` (others → `_`); longer than 200
  characters → the first 150 + `_h` + 12 hex digits of its SHA-1; de-duplicated with `_<k>`. Bridge names are
  matched with an exact, case-sensitive `strcmp` by libcosim_bridge; boundary paths case-insensitively.
- `names.build_bridges(plan) -> List[Bridge]` and `names.boundary_line(bridge) -> str` are the only
  builders of registry names, VHDL bridge paths and boundary lines.

### 5.7 The cut → deck contract – `ams/model.py` (phase 0)

`model.py` holds `CutPort` (with `shell_dir`), `PortMap`, `CutCell`, `VariantBind` (Verilog ranges,
`vhdl_vector`, params), `CutInstance` (per-instance `subckt`, `portmap`, `spice`, `params`; `xname` and
`port_nodes`, filled by deck.py), `PortRef` (bit = offset from the right bound, the same in Verilog and VHDL;
Verilog index = right + bit if left > right, else right − bit), `Driver` (strength, origin, removable,
`stmt` = the vhdl.py statement id), `Net` (`passive`, `variable`), `AnalogNode` (`host`, `d2a`, `a2d`, `dc`,
`shunt`, `pull`, `report`), `Bridge`, `RuleHits`, `CutAnalysis`, `AmsPlan`, `CutEmitResult`, the role names,
`ROLE_PASSIVE`/`ROLE_CODE`, `LVL_KEYS` (`MIDV_L` 0, 1, 2 = X, 3 = Z), the strength classes and
`D2A_IE`/`A2D_IE`.
- `cut.analyse` → `CutAnalysis` (its private state, the parsed design and the elaborated scopes, rides on the
  attribute `_cut`, not a dataclass field); `cut.assign_roles` → `AnalogNode`s; `deck.py` fills
  `d2a`/`a2d`/`dc` through `rules.py`; `names.build_bridges` → `AmsPlan.bridges`; `cut.emit` and `deck.py`
  both iterate it.
- **Agreement check** (flow.py `_agreement`, step 11, both engines): the multiset of `(direction, name)` taken
  from the emitted deck's `code:` URIs (`deck.code_sources`: the text after the `:libcosim_bridge.so:<init>:`
  prefix, split at the first `:` into direction and name) equals the boundary file's (`D2A`/`A2D`
  lines, third field); every boundary name is unique and ≤ 255 characters; every boundary path ≤ 511
  characters (the labels are the cut's own, so they are not re-resolved). A mismatch is a compile error.
- `ams/ams.json` (step 13): `{"version": 1, "top", "cells", "instances", "nodes", "bridges"}` as JSON, tuple
  keys (`CutInstance.spice`) joined as `"port,bit"`.

### 5.8 Phase-1 and phase-2 APIs (the module docstrings are authoritative)

```python
# ams/verilog_ports.py (V)
preprocess(job, out_path, override_timescale=None, ams=True) -> PP
    # out_path: layout.pp_orig(daidir), or layout.plain_pp(daidir) in plain vcs mode; vamos_prelude.v beside it.
    # PP: path, text, notes, precision, cwd, files, lib_files, lib_dirs, case_sensitive, modules
    # (name -> [ModuleDef(..., unit, precision)]), variables / tri_nets ([Decl]); origin(line), decls(), decl_at()
precheck(job, top=None, override_timescale=None, pp=None) -> List[Note]
    # -tnull -s top on the original sources, or on pp.path when pp.library_masked (§1.2)
library_rule(pp) -> (text, names);  apply_library_rule(pp) -> List[str]   # VCS's -v rule (§1.2)
api_scan(pp) -> List[Note];   unsupported_tasks(vhd_text, pp=None, ams=True) -> List[Note]
translator_warnings(lines, pp=None) -> List[Note]   # "iverilog-sv2ghdl: Warning:" lines as warnings (T4)
find_roots(pp, exclude=(), case_sensitive=None) -> List[str];   find_top(pp, job, exclude=()) -> str
module_header(pp, name) -> Header          # ports, params, range_params, param_text, body_text, origin
instantiations(pp) -> List[Inst]           # Inst.conns, Inst.actual(port, index)
port_directions(pp, name);  constant_names(pp, name);  ident_ref(expr);  concat_operands(expr)
from_text(text, path='pp.orig.v', ams=False, lib_lines=()) -> PP     # no iverilog (tests)
# ams/shells.py (V)
find_cells(pp, top, cfg, nl) -> CellSet;   cell_set(pp, top, cfg, nl) -> (spice_only, multi_view, notes)
build(pp, top, cfg, nl, job=None, cellset=None, out_path=None) -> ShellResult
    # cells (shell_dir set), path (ams/pp.v), notes, headers, shell_lines, directions, instances, removed
wrapper_inputs(pp, res, top, live=None) -> Dict[(cell, port), List[reason]]       # probe step 2b
wrapper_input_chains(pp, res, top, live=None) -> Dict[(cell, port), Dict[chain, reason]]   # per instance
Direction(text, by_chain=None)             # a str: ShellResult.directions entry with each chain's reason
cell_globs(cfg);  case_sensitive(pp, cfg);  binding(cfg, cell, cs);  subckt_for(cfg, nl, cell, cs)
# ams/vhdl.py, ams/cut.py (C)
vhdl.parse(path) -> VhdlDesign;  vhdl.parse_text(text, path='design.vhd');  vhdl.literal_elems(toks, width=None)
vhdl.make_safe_name(verilog, entity_collision=False);  vhdl.safe_name_matches(vhdl, verilog)
cut.analyse(design, top, cells, nl, cfg, hits, pp=None, directions=None) -> CutAnalysis
cut.assign_roles(analysis, alloc, disabled, removal, directions=None) -> List[AnalogNode]
cut.emit(plan, out_dir) -> CutEmitResult
cut.port5(analysis, portref) -> Port5;  cut.param_overrides(cell, inst) -> {p: (default text, actual)}
# netlist (N1–N4)
spice.parse(paths, extra, cwd, opts=None) -> Netlist        # paths: str | (path, origin)
spice.fold_ground(name) -> str;  spice.left_out(nl) -> Dict[str, Note]
vacask.emit(nl, path, analysis_name='vamos_tran', osdi=(), notes=None);  vacask.render(..., op=False) -> str
xyce.emit(nl, path, osdi=(), notes=None);  xyce.render(nl, osdi=(), notes=None, op=False) -> str
vacask.smoke(nl, dir, osdi=(), nvc_libdir=None, timeout=600) -> List[Note];  xyce.smoke(same) -> List[Note]
rawfile.read(path) -> Raw;  rawfile.read_all(path) -> List[Raw];  rawfile.fix_points(path) -> bool
expr.parse(text, case='lower') / names / evaluate / to_vacask(ast, ctx, node=None) / to_xyce(ast, ctx, node=None)
expr.param_ident(name, engine);  expr.vacask_quote(name);  expr.number(s)
tables.code_uri / code_source / dc_source / gcond / shunt / smoke_netlist / reachable / Scope (where, card_of)
       / select_bin / bin_guard / bsim4_version / card_label / scaled(..., level=) / solver_options
       / mos_junction_warnings(inst, model, options=None) / mos_scale_warnings(inst, options=None)
       / current_ref_errors(inst, items, scope) / has_branch(inst) / enclosing_reads(subckt, names)
       / WIRE_RENAME                                    # round 6 (§4.1, §4.3.5, §4.3.6, §4.4)
# ams/initfile.py, ams/rules.py (R): §2.3, §3.4
# integrator (phase 2)
flow.compile(job, be, con, opts) -> (top, cpu time after translation);  flow.choose_engine(opts, cfg);
flow.compile_tools(job, opts)                                # the provenance header's analog tools
flow.left_out_cells(notes, left) -> notes;  flow._netlist_notes(notes, opts)   # §4.3.2; the maxstep note
engines.problems(engine) -> List[str]                        # unusable VAMOS_* overrides (§6)
deck.build(nl, plan, cfg, engine, alloc, hits, daidir, opts, design=None) -> DeckResult
deck.seed_names(nl);  deck.analog_stop(opts, notes);  deck.driver_level(design, drv);  deck.held_level(design, drivers)
deck.range_params(cell) -> Set[str];  deck.code_sources(path) / deck_uris(path) / enable_nodes(path)
supply.SupplyGraph(nl, extra=(), powernets=None, skip=(), evaluate=None, dynamic_powernets=None, labels=None,
                   refs=None)
    # trace(node) -> TraceResult(vdd, source, path, dynamic, visited, ref_name, ref_index, ref_dynamic,
    #                            unevaluable); constant_value(node);
    # highest_constant(); node(name, scope); hit_at(node); refs (reference hits, §3.3 step 2);
    # unevaluable_at(node); unevaluable_sources()
report.text(plan, engine, opens=()) -> str;  report.write(path, plan, engine, opens=())   # ReportError
cosim.run(be, compiled, rt, con, opts, stop_fs) -> (rc, final time text, or None when nothing was simulated)
cosim.abi_check(engine, nvc_libdir, ld_path) -> (notes, error or None)
cosim.EndState  (feed(raw line); kind, time_text, failures, started, ended, stop_at_zero, interrupted,
                 digital_stop)
cosim.ChatterFilter(out, deck, quiet_nodes, engine=None) + see_raw(line)
cosim.check_raw(path) -> (count rewritten, last time or None);  cosim.scan_raw(path) -> [RawPlot]
cosim.tran_start(deck, engine);  cosim.sim_time_text(t, precision=None)
backends.nvc: NvcBackend.run_command(top, plusargs, run_args) / stream(cmd, env, cwd, out, err, on_raw)
              / work_spec() / plugins(); NvcBackend.PLUGINS, .translator_lines (analyse), .interrupted,
              .killed, .waves (a Waves, or None); remap_exit(rc, filter);
              REPORT_RE / is_report(line); fs_text(fs); footer_time(text); INTERRUPT_GRACE;
              end_time(filter, stop_fs, rc, interrupted) (OutputFilter.end_time / end_why);
              Waves(path, scopes=(), arrays=False).args(); DEFAULT_DUMPFILE; WAVE_EXCLUDES   # round 6, §8
# vcs personality (plain mode, §1.4, §8)
vcs.library_rule(pp) -> (text, names)      # delegates to verilog_ports.library_rule
vcs.plain_tops(job, pp);  vcs.wrapper_vhdl(name, tops, vhd)
vcs.deferred_reason(log, module, pp=None);  vcs.plain_compile(job, be, con, opts);  vcs.invalidate(job)
vcs.write_stub(job, failed=False)
vcs.dump_request(vhd, pp, tops, wrapper, every=False) -> (Job.dump record or None, notes)   # round 6, §8
simv.wave_request(compiled, rt) -> Optional[Waves];  simv.waves_problem(path);  simv.plusarg_test(name, plusargs)
optable.VAMOS_OPTIONS;  optable.check_vamos_opts(opts);  optable.vamos_option_effects(opts, personality, ams, run)
tools.checked_override(var, what);  tools.version_text(output)
```

## 6. Runtime – `backends/cosim.py`

- **Engines** (`vamos/ams/engines.py`, phase 0): VACASK home `VAMOS_VACASK_HOME` (default
  `/opt/build.VACASK/Release`), standalone binary `VAMOS_VACASK` (default `<home>/simulator/vacask`), C
  interface `<home>/cinterface/libvacaskcinterface.so`, module path `VAMOS_VACASK_MODULE_PATH`, else
  `<home>/lib/vacask/mod` or `<home>/devices` – always explicit, because under nvc the C interface resolves its
  built-in default relative to the nvc executable ("File 'resistor.osdi' not found") – and openvaf-r
  `VAMOS_OPENVAF`, else the newest `/opt/openvaf-r-*/openvaf-r`, else `<home>/simulator/openvaf-r`; Xyce
  libraries `VAMOS_XYCE_LIBS`, else `~/xyce-libs`, the build's `utils/XyceCInterface` and `src` (libxyce.so is
  only in the build tree), and the standalone binary `VAMOS_XYCE`, else `/usr/local/src/xyce-build/src/Xyce`;
  nvc `VAMOS_NVC`.
  `env_for(engine, nvc_libdir)`: `LD_LIBRARY_PATH` (the bridge library's directory first, then the engine's),
  `SIM_MODULE_PATH`, `SIM_OPENVAF`.
  An override that is set is used as given, never replaced by a default. `engines.problems(engine)` lists
  every set override of that engine's tools that names nothing usable – "VAMOS_OPENVAF=<p> is not an
  executable", likewise `VAMOS_XYCE` and `VAMOS_VACASK`; "VAMOS_VACASK_HOME=<p> is not a directory", likewise
  `VAMOS_VACASK_MODULE_PATH`; "VAMOS_XYCE_LIBS=<v>: <d> is not a directory" – and flow.py stops at step 2
  with them. (The digital tools' overrides, `VAMOS_NVC` and `VAMOS_IVERILOG`, are checked by
  `tools.checked_override`, §8.)
- **ABI check** (`abi_check`; nothing is loaded into the vamos process; nm is not needed). Each library is
  looked for where nvc's dlopen looks: the `LD_LIBRARY_PATH` vamos gives nvc (`env_for`: the bridge's
  directory, the engine's directories, then the inherited path), then `/usr/local/lib`, `/usr/lib`, `/lib`,
  `/usr/lib64`, `/lib64`, `/usr/lib/*-linux-gnu` and `/lib/*-linux-gnu`. vamos then reads the library's ELF
  `.dynsym`, which must define `cosim_bridge_abi` in `libcosim_bridge.so`, and `vacask_cosim_abi` or
  `xyce_cosim_abi` in the engine's C interface.
  - Not found: "vamos: error: <lib> not found (searched <dirs>): <hint>", exit 1, no footer. The hint says,
    for the bridge, that it is built with nvc (set `VAMOS_NVC`); for VACASK, set `VAMOS_VACASK_HOME`; for
    Xyce, set `VAMOS_XYCE_LIBS` to the directories holding `libxycecinterface.so` and `libxyce.so`.
  - Found without the symbol: "vamos: error: <path> does not export <sym>() (it predates the vamos
    co-simulation ABI): <rebuild that library>", exit 1.
  - Not an ELF file vamos can read: a note, and the run goes on.
  - The ABI value itself is nvc's check (P1, §7): Xyce's interface answers its libxyce.so's value, so a
    patched interface on an unpatched libxyce.so passes this check and fails nvc's.
- **Per-run directory:** `mkdtemp(prefix='<basename of prefix>.run.', dir=<directory of <prefix>.raw>)` – the
  simv cwd, or the directory part of a `choose -o dir/name` prefix (created when missing) – so concurrent
  `./simv` runs never mix files while they run (VACASK writes `<analysis>.raw`, `<deck>__behavioral.va` and
  `.osdi` caches into its cwd). Before the run, an existing `<prefix>.raw` is removed, so a failed run never
  leaves an older run's waves behind; a file that a concurrent run removed first is not an error. Runs that
  share `<prefix>.raw` (one cwd, or an absolute `choose -o` prefix) all run to completion, and the last to
  finish keeps the name; seeds that need their own waves must run in separate directories with a relative
  prefix. If the directory cannot be set up, the run stops with "cannot set up the run in <dir>: <why>" and
  prints no footer.
- **Command,** built by the run-command builder shared with digital runs (`NvcBackend.run_command`: `NVC_STD`
  from `_metadata`, `-L`, the runtime plugins that exist,
  `--load=<libdir>/sv2vhdl/libresolver.so,<libdir>/sv2vhdl/libsv_math.so` (`NvcBackend.PLUGINS`, in either
  order: they export no symbol in common, and `$random` is plain VHDL in sv_math_pkg, P10; without
  libsv_math.so `$sqrt`, `$ln`, `$pow` and the other sv_math_pkg foreign functions stopped the run), with
  libresolver.so `SV2VHDL_QUIET=1` and `PYTHONPATH` naming the directory of `sv2vhdl_resolver.py`,
  `run_args` after `-r`, then the waveform options of a `$dumpvars` run, §8):
  `nvc --std=<NVC_STD> --work=work:<abs daidir>/nvc/work -L <libdir> [--load=<resolver>,<sv_math>]
  -r --stop-time=<fs>fs --vacask-netlist=<abs deck> | --xyce-netlist=<abs deck>
  --cosim-config=<abs boundary> [--wave=<vcd> --format=vcd …] <top> <plusargs>`, cwd = the run directory,
  environment plus `engines.env_for`. nvc runs with `NVC_COLORS=never` and `NVC_REPORT_END_TIME=1` (P11),
  stdin from `/dev/null`, in a process group of its own (Signals, below). `NvcBackend.stream`, shared with
  the digital run, also gives it `SV2VHDL_FILE_DIR` = the directory simv was started in, where the sv2vhdl
  runtime resolves the testbench's relative file names (T26), and, unless the environment names others,
  `NVC_RESOLVER_DIR=<daidir>/nvc/_sv2vhdl_cache` and `NVC_WORK` = the daidir's work library, which the
  resolver plugin would otherwise put in nvc's working directory (round 6).
  - `--work` is a global option and must precede `-r`; the `NAME:PATH` form avoids misreading a `:` in the
    path; every path is absolute. `NvcBackend.elaborate`/`run` and the step-12 analysis pass the same
    `--work`.
  - `--stop-time` = `ceil(stop × 1e15) + 1` fs, where stop is the deck stop, or less with simv's
    `+vcs+finish+<time>` (§8). nvc reads the count as 64 bits (P8); before P8 its `sscanf("%u")` wrapped every
    stop ≥ 4.295 µs modulo 2^32 fs (the 3600 s default became 661 ns), which the truncation check below caught
    as "analog output ends early".
- **Output filtering:** every raw nvc line is split at `\n` only (a testbench's `\r` is kept) and goes to
  `EndState.feed` and `ChatterFilter.see_raw` before the `OutputFilter`, which passes `** Note:
  co-simulation …` lines whole, strips other notes' prefixes and sends warnings, errors and fatals to stderr.
  Since the round-6 repair it first rewrites each location in the translated text (`<path>/_norm.sv:<n>`,
  `_pp.v:<n>`) to the user's file:line through `<daidir>/vamos.srclines.json` (`nvc.source_relocator`, §1.1),
  so `$info`/`$warning`/`$error`/`$fatal` and the file tasks' vvp messages name the user's source (in a
  plain run too); and it drops nvc's `FINISH called`/`STOP called` note after the translation's `sv2vhdl: quiet
  end` note, which `$finish(0)`/`$stop(0)` emit (R6R-10), as `bin/vvp-sv2ghdl` does.
  It takes nvc's last line under `NVC_REPORT_END_TIME`, `** Note: simulation ended at <t> (stop time|no more
  events|stopped|interrupted)`, which has no time stamp, for the footer and never prints it (§8), and says
  nvc's note that a VCD leaves out arrays in simv's terms ("memories (unpacked arrays) are not in the VCD, as
  under VCS; ./simv +vcs+dumparrays adds them", §8).
  It drops the trace nvc prints after a report (`   Process …`, `   Procedure …`, and the
  `   Function <F> [...] at <file>:<n>` line nvc adds after output printed inside a Verilog function), the
  same set `bin/vvp-sv2ghdl` drops; testbench output never has that form (every line of it is a report).
  - Testbench output always arrives as an nvc report: `** <Severity>: <time>+<delta>: <text>`, or `(init): `
    during initialisation (`nvc.REPORT_RE`). The translator sends `$display`, `$write`, `$monitor` and
    `$strobe` lines, and the messages of `$error`, `$warning`, `$info` and `$fatal`, through `report`. Such
    lines are printed unchanged (but for the location rewrite above) and are never filtered or classified,
    whatever they say.
  - From the other (tool) lines, `ChatterFilter` drops blank lines; nvc's co-simulation notes (`** Note:
    initializing|loaded|resolved|starting …`); `[cosim_bridge] bound` lines; the deck path the C interfaces
    echo; and, on Xyce only (`engine="xyce"`), Xyce's banner, device counts, solver statistics and timing
    table, and its `Co-simulation finish at <t> s.  Exiting transient loop`. Xyce's one-terminal warning
    "Netlist warning: Voltage Node (<n>) connected to only 1 device Terminal" is word-wrapped by Xyce (78
    columns, one-space continuation lines: 1, 2 or 3 lines), so it is reassembled first and dropped only when
    `<n>` is one of the deck's D2A enable nodes (`deck.enable_nodes`); the same warning about any other node,
    and held lines that turn out to be something else, are printed unchanged (`flush()` at the end of the
    stream). The one-terminal-warning rule is the same on both engines.
  - The nvc scope-tree dump and the per-boundary `[OK]`/bridge notes appear only with `COSIM_TRACE=1` (P4).
    The bridge's short-ramp warning (P3) is printed: "[cosim_bridge] warning: D2A '<name>': a <d> s ramp at
    <t> s is shorter than <engine> resolves at that time; it takes <d'> s (reported once per signal)".
- **End-of-run classification** (`EndState`). Only tool lines are classified, never a report; each is
  matched as a whole line after colour codes are removed. nvc prints exactly one end line for a run that
  reaches the engine, its time exact on the digital's femtosecond clock (P1/P6):
  - end lines:
    - `** Note: co-simulation finished: digital stop at 0 s (before the first analog step)` – the digital
      stopped during the P2 settle or, on VACASK, at the operating point's A2D values; on Xyce a stop caused
      by the first A2D sample ends one step later with the plain `digital stop at 0 s` line;
    - `** Note: co-simulation finished: digital stop at <t> s` – `$finish`/`$stop`/a fatal, `<t>` the
      digital's own stop time;
    - `** Note: co-simulation finished: analog end at <t> s` – the deck stop or `--stop-time`;
    - `** Error: <VACASK|Xyce> transient failed at <t> s`;
    - `** Error: co-simulation stalled at <t> s`;
    - `** Error: co-simulation interrupted at <t> s` – a SIGINT reached nvc (Signals, below);
  - failure lines: any nvc `** Error:` or `** Fatal:` line with no time stamp (boundary-file, registration,
    library, ABI and engine-initialisation errors, `** Fatal: co-simulation ABI mismatch: …` included);
    `** Warning:   <D2A|A2D> <path> <-> <name> [FAILED: signal not found]`; `** Warning: cannot load …`,
    `cosim bridge missing symbols …` and `missing <engine> symbol …`; the bridge's `[cosim_bridge] signal
    '<n>' not registered…`, `code: source '<args>' is not bound …`, `missing URI args`, `bad URI args
    '<args>'`, `VACASK external-source ABI mismatch` and `'<n>': bad ramp …`; nvc's `*** Caught signal <n>
    …` crash line;
  - decisions, in this order: (1) interrupted (Signals); (2) nvc killed by signal N: "vamos: error: nvc was
    killed by signal N (NAME)", exit 128+N; (3) failure lines: "vamos: error: co-simulation failed: <first
    line>" (plus "(and N more failure lines above)"); (4) no end line: "vamos: error: the co-simulation ended
    without an end line (nvc exit status N)"; (5) nvc's status, remapped (`remap_exit`): exit 1 after `$error`
    reports alone becomes 0, as VCS exits 0 without `-exitstatus`; `$fatal`, failure reports, nvc's own
    errors and crashes keep their status; `$stop` (`std.env.stop`, T1) exits 0.
  - Only nvc's exact line `** Failure: <t>+<d>: SIMULATION FINISHED` maps to 0 (a translation made without
    tgt-vhdl's sv2vhdl mode; vamos always uses sv2vhdl mode, where `$finish` is `std.env.finish` and exits 0).
    Testbench text containing those words never changes the status, in AMS or plain mode.
  - A failed run exits non-zero (nvc's status, else 1), publishes nothing and keeps its run directory.
- **Output** (on success):
  - `check_raw` maps the rawfile and reads only its headers and last point (an ASCII file: one counting
    pass), O(1) memory, with `rawfile.read`'s counting rules (a 72 MB rawfile: 17 MB and 0.17 s, where reading
    it took 640 MB and 1.47 s). A blank or wrong `No. Points:` is rewritten, in place or by a streamed rewrite
    when the number is wider than its field, with the note "rawfile header point count rewritten from its
    data" (Xyce leaves it blank when it ends paused – whenever `--stop-time` is before the deck stop – and
    fills it after a finish or a normal end).
  - The last point must reach `min(deck stop, --stop-time, digital stop time)` within 1e-9 relative or 2 fs,
    whichever is larger: the engine's last point can sit up to 1.5 fs below the digital's femtosecond stop
    (`stopped_step`'s window plus the clock's rounding, P1). Otherwise "analog output ends early at <t> s
    (expected <t'> s)" (both `%.15g`), a failure, nothing published.
  - The file is then moved with `os.replace` to `<prefix>.raw` (`prefix` = choose `-o`, relative to the simv
    cwd, default `vamos_ams`); a failure there is the error "cannot publish the rawfile as <p>: <why>".
  - No analog output is not a failure when the run ended at or before the deck's `.tran` start (TSTART: no
    engine writes a point before it; TSTART comes from the ams record's `start` when present, else from the
    emitted deck, `tran_start`): the note "no analog output: the run ended at <t> s, before the deck's output
    start <t'> s (.tran TSTART)". Nor at a t=0 stop: VACASK has written the operating point (its stepper is
    installed before initialisation and it offers t=0, P1), published as a one-point rawfile (none when
    TSTART > 0); Xyce runs no transient and writes none, and simv prints the note "no analog output: the
    digital stopped at t=0".
  - Otherwise a rawfile with no points ("the analog rawfile has no points (expected output through <t> s)"),
    no rawfile ("the analog engine wrote no rawfile") and an unreadable one ("cannot read the analog rawfile:
    <why>") fail.
- **Synthesised stop:** if `stop_synthesized`, simv prints "no .tran: the run ends at $finish/$stop or at <stop>
  s (+vcs+finish+N bounds it)" at the start.
- **Cleanup:** on success, delete the files vamos knows the engines write (`*.raw`, `*__behavioral.va`,
  `*.va.origin`, `*.osdi`) and remove the directory if it is then empty; anything else keeps the directory,
  and its path is printed ("run directory kept (testbench output)"; the testbench's own files go to the
  directory simv was started in, below, so this is for a file something else left there). On failure the
  directory is kept and printed. `--vamos-keep` always keeps it.
- The testbench's relative file names (`$fopen`, `$readmemh`, `$writememh`, …) resolve against the directory
  `./simv` was started in, as under VCS, in a co-simulation too (round 6): the sv2vhdl runtime resolves them
  against `SV2VHDL_FILE_DIR` (Command, above), while nvc itself works in the run directory, where the engines
  write. A plain digital run's nvc works in the start directory itself (it worked in `<daidir>/nvc`, where a
  relative `$readmemh("m.hex")` could not be found).
- **Signals** (`backends/nvc.py`). nvc runs in a process group of its own, so a terminal's Ctrl-C reaches
  simv alone and nvc gets exactly one SIGINT: a plain nvc run takes a second pending SIGINT as "quit now"
  (exit 1, no end line); a co-simulation ends at once on a second one, with the interrupted end line (unless
  an end line is out already) and exit 130, before the engine finishes its output (P11, `cosim_ctrl_c`;
  before round 6 it too exited 1 with no end line). A SIGINT, SIGTERM or SIGHUP to simv reaches nvc
  as one SIGINT; a second one, or nvc still running `INTERRUPT_GRACE` (5 s) later, kills nvc. Ctrl-Z
  (SIGTSTP) stops nvc with simv, and nvc resumes with it. Signals that are ignored (nohup) stay ignored. nvc
  gets SIGTERM if simv dies (Linux `PR_SET_PDEATHSIG`). An interrupted run is never a stop and publishes
  nothing: simv prints "vamos: note: co-simulation interrupted (<SIGNAME>|nvc got SIGINT) at <t> s; run
  directory kept (partial waves): <dir>" and makes the partial rawfile's point count readable, then prints
  the footer and ends with the same signal, so the shell sees 128+N and a calling script stops. A SIGINT sent
  only to nvc exits 130. A Ctrl-C outside the run gives a note, not a traceback. Round 6's merged build also
  printed nvc's own report of the interrupt, `** Fatal: <t>+<d>: interrupted` (sometimes `… interrupted in
  process :tb:u1:…`), on stderr before the end line, whenever the digital was running a process when the
  SIGINT came (it came with P12's resolver change); since the round-6 repair `cosim_ctrl_c` interrupts the
  digital quietly (`model_interrupt_quiet`, R6R-22), so the end line is the only report.
- **Footer:** the time is the end line's, exact, in nvc's style: the largest exact unit of ms, us, ns, ps and
  fs (`600ns`, `2000000006ps`), and `0` for zero. Without an end line, once the co-simulation started (nvc
  killed, a crash), it is the later of the last report time and the partial rawfile's last point. No footer
  is printed when nothing was simulated: the ABI check refused, the run directory failed, nvc could not
  start, or nvc stopped before the analog transient started.
- The provenance header lists `VACASK` (AGPL-3.0-only) and `OpenVAF-r` (GPL-3.0-only), or `Xyce`
  (GPL-3.0-or-later), as used, with the versions recorded at compile time (`vamos.tools.json`).

## 7. Engine and translator patches

All but T6 have landed: on the default branches of the kev-cam forks (nvc and xyce `master`, iverilog and
VACASK `main`) and, for the sv2ghdl scripts, on sv2ghdl's `main`; round 6's changes (P11–P14, T25, T26, the
round-6 repair's R6R rows and the round-6 parts of the other rows) were merged into the development build's
working trees and gated there (§9) before they were committed. Builds, below, records how the development
build was rebuilt as they landed. The phase-4 and phase-5 fixes are in the rows they changed (P1, P3, P9,
T1–T5, T7, T8) and in T11–T20 and P10; the three phase-5 translator fixers worked in private copies of
tgt-vhdl, whose changes were merged into the iverilog tree (`git merge-file` against the common base, no
conflicts) and installed together. The repair round after the phase-5 gate added T21–T24 in that tree and
changed T1 and T4's vamos side.

**Upstream merge** (2026-10-03, before round 6). The other kev-cam work on the same forks was merged in:
iverilog c9726d635 (the merge 1434e5b83 brought 20 commits – 19 in tgt-vhdl and 5037ac349, a core
elaboration fix for a variable's prefix index into a packed dimension with msb = lsb – and c9726d635 followed
it): the VHDL-2000/2008/2019, PSL and nvc reserved words in the rename table (df8369d00); one VHDL driver per
Verilog variable, same-edge always blocks merged into one process whose comment ends "[+ merged same-edge
always block(s): f:l, …]" (130dac4ce); generate-scope suffixes from loop iterations (247f89124: a generate
local `r` in `g[0]` is `r_g_0`, no longer `r_i0`), unique labels for flattened sibling generate scopes and
process labels qualified by them (4c2c954a3, c4e13e7f3); process-local variables kept out of computed
sensitivity lists and the NBA re-arm `wait on` (0b3beddd6, c9726d635); entities in dependency order;
same-named functions of different modules, and functions in generate loops, kept apart; selects of
unpacked-array words and run-time-base bit-selects in continuous logic; constant drivers on array words
other than word 0; and `$readmemh`/`$readmemb` (b918ff864). nvc 399d83df8 brought `$readmemh`/`$readmemb` in
`logic3d_types_pkg` (2e7d16d6a; `sv_readmem_load` and its store) and an `rt` fix (f6d94cf87: a bare `wait;`
after a dynamic wait no longer resumes the process). sv2ghdl e654556 made vamos follow the translated text
(vhdl.py's `VHDL_RESERVED` gained the new reserved words, `Stmt.origin` reads a merged process's own
file:line, and `NvcBackend._resolver_pydir` also looks in the nvc build's `abs_top_srcdir`), and 1536120 added
the hazard3 regression suite (§9). After the merge: ivtest/iverilog-nvc 1347 → 1366 of 3011 and nvc/regr 1149
→ 1155, both with no pass→fail; ivtest/iverilog (3006/3020) and ivtest/nvc-vhdl (282/294) unchanged; the
vamos suite 1375 OK under WSL and Cygwin Python 3.9 (§9).

**Round 6** (on the merged code; §10). Six fixers worked in private copies and delivered patches, which were
merged into the shared working trees (iverilog: T, then F, then L, with one conflict region of F on T in
`draw_stask_readmem` and six of L on T+F, in `expr.cc`'s `$urandom` functions, `stmt.cc`'s seed write-back and
`draw_while`, and `vhdl_target.h`, each resolved by keeping both fixes; nvc: F, W, L and L's kernel patch,
no conflicts), and the plugin and nvc-build were rebuilt (Builds, below); `tests/vamos/test_r6_merge.py`
covers what the merge itself decided (§9). The changes are in the rows they changed (P10, T2, T4, T16, T18,
T20, T22, T24) and in the new rows P11–P14, T25 and T26.

**Round-6 repair** (after the merged stack's gate, §9). One agent fixed the gate's failures and the round's
open product items, R6R-01–32, each pinned by tests in `tests/vamos/test_r6_R.py` that fail without its
fix (§9): in tgt-vhdl, in nvc (R6R-21's `sched_driver`, R6R-22–24) and in the sv2ghdl scripts and vamos
(the R6R rows below T26); T1, T4, P11 and P12 changed with them. Its patches went into the same trees, and
the plugin and nvc-build were rebuilt again (Builds, below).

| id | where | change (as built) |
|---|---|---|
| P1 | nvc `src/cosim.c`, `src/rt/model.{c,h}`, `src/cosim_bridge.{h,cpp}`; VACASK `include/extsource.h`, `lib/extsource.cpp`, `lib/coretran.cpp`, `cinterface/vacaskcinterface.{h,cpp}`; Xyce `DeviceModelPKG/Core/N_DEV_SourceData.{h,C}`, `AnalysisPKG/N_ANP_Transient.C`, `utils/XyceCInterface/N_CIR_XyceCInterface.{h,C}` | **Finish protocol** and ABI handshake (below). End-line times are exact (phase 4): `fs_text`/`time_text` print the digital's femtosecond count as `%.15g` does wherever that is exact (`1.2e-07`, `0.002000000006`, `0`) and with every digit otherwise (`3600.000000000000001`); engine times are rounded to the fs clock first. Before, `%.9g` rounded 10-digit stops up and vamos failed correct runs ("analog output ends early"). **Interrupts** (phase 4): `rt/model.c` records an interrupt (only when the design had not stopped itself; `model_interrupted()`), and `model_exit_status` returns failure for one that lands between processes, so plain `nvc -r` exits 1 there too (it exited 0); `cosim_run` prints `** Error: co-simulation interrupted at <t> s` in place of the digital-stop line, at both stop sites, and returns 130 (`COSIM_EXIT_INTERRUPTED`), while the engine still finishes its output. |
| P2 | nvc `src/cosim.c` | Settle t=0 first: `model_step_to(m, 0)` after `model_run_init` and before the boundaries are registered, so the operating point sees the digital's t=0 values (a clock at 1 at t=0 gives no start-up pulse). |
| P3 | nvc boundary parser; `libcosim_bridge` | **Per-boundary ramps.** Optional `rise=<s> fall=<s>` columns on any line (keys case-insensitive, C `strtod` seconds); new export `int cosim_bridge_set_ramp(int idx, double rise, double fall)` (1, or 0 for a bad index or value). Values below 1e-15 s (`COSIM_BRIDGE_MIN_RAMP`) are clamped with a warning (`** Warning: <file>:<line>: rise=<v> is below 1 fs: clamped to 1e-15 s`); NaN, ±inf, negative, above 9e3 s (beyond nvc's femtosecond clock) or trailing junk make a malformed boundary line. A missing column keeps the 1 ns default (`COSIM_BRIDGE_DEFAULT_RAMP`); columns on an A2D line are checked and have no effect. Per-ramp state `struct d2a_ramp {t0, v0, v1, t1}`: on a change at tc, `d2a_ramp_follow` first sets `v0 = value(r, tc)` from the old ramp, then `t0 = tc; v1 = new; t1 = t0 + (v1 >= v0 ? rise : fall)`; evaluate `t <= t0` first. `d2a_callback` (`tvvec`, `addBreak`) and `vacask_d2a_value` use `r->t1`, never `sig->rise_time`. Every new ramp, a mid-ramp reversal or an `x2v=2` mid-level step included, takes the full rise or fall time from its current value. Phase 4: the ramp's end is an analog step boundary on both engines – VACASK asks for it as a breakpoint (`vacask_d2a_value`); on Xyce, which skipped a ramp shorter than its first step after a change, `xyce_bridge_step` vetoes a converged candidate step that contains a ramp end (after the last accepted point, before t) back to that end (`xyce_ramp_cut`), before the digital is advanced and at most once from a given step start (a 10 ps edge at 1.5 ms: crossings 9.4 ps late before, 5.2 ps now, VACASK 5.0 ps; `.tran 1m 2 0 5m` with 10 ps edges at 1.5 s no longer dies with "time step too small"). A ramp shorter than either engine resolves at that time (1e-13 × t: 1 fs from 10 ms on, 10 ps from 100 s on; `XYCE_MIN_RAMP_REL`, `VACASK_MIN_RAMP_REL`) is stretched to that length with the warning "[cosim_bridge] warning: D2A '<name>': a <d> s ramp at <t> s is shorter than <engine> resolves at that time; it takes <d'> s (reported once per signal)" (before, VACASK spread a 1 fs ramp at 1.5 s over a 100 ns step, silently, and Xyce over 15.6 ns or failed). |
| P4 | nvc, bridge | Registry 256 → 8192 entries (`COSIM_BRIDGE_MAX_SIGNALS`; the 8193rd is "registry full", `COSIM_BRIDGE_ERR_FULL`). `parse_boundary_config` reads lines with `getline` (any length) and tokenises with `strtok_r`: blank-separated fields, a field starting with `#` starts a comment, `key=value` columns. Names ≤ 255 characters (`COSIM_BRIDGE_NAME_MAX`), paths ≤ 4095. Every parse or registration error is fatal before the engine starts, with file:line: a malformed line (an unknown or repeated column, an extra field that is not `key=value`, a bad value, an over-long field), an unknown direction, a duplicate bridge name (`COSIM_BRIDGE_ERR_DUP`), a full registry; then "failed to parse boundary config" / "failed to register the boundary signals". An unresolved boundary path is still only the warning `[FAILED: signal not found]` (vamos fails the run on it). The registry-full and bad-name errors carry no file:line (`bridge D2A: <path> <-> '<name>' FAILED (registry full: at most 8192 boundary signals)` / `… FAILED (bad bridge name)`); at most ten registration errors are printed, then a count of the rest. `cosim_bridge_register` returns `COSIM_BRIDGE_ERR_NAME` (−3) for an empty or over-long name or a bad direction. The scope-tree dump and per-boundary `[OK]`/bridge notes print only with `COSIM_TRACE=1`. |
| P5 | bridge; Xyce `BindCB` | `nvc_bridge_init` returns the failing callback `bridge_fail` (−1 on every op; it prints "[cosim_bridge] code: source '<args>' is not bound …") for bad arguments, an unregistered name or a direction mismatch – "signal 'x' not registered", "… not registered as D2A (the boundary file has it as A2D)" – and never NULL; `vacask_bridge_init` returns 0 in the same cases. Xyce `BindCB`: an init function that returns NULL is "Netlist error: code: URI function <fn>() in <lib> returned no callback for '<args>': the source is not connected", and the source gets the failing callback `NoConnection`, so it ends in "Failed to connect URI <inst>", `*** Xyce Abort ***`, exit 1 (the unpatched Xyce segfaulted). |
| P6 | nvc `src/cosim.c` | **Xyce failures were reported as success:** `xyce_simulationComplete` returns `bool` but was called through an `int` typedef. Fixed, with the run loop below: a failed Xyce transient prints `Xyce transient failed at <t> s` and exits 1, and "stalled" no longer appears at a normal deck end. |
| P7 | nvc `src/cosim.c` | `signal_to_voltage` clamps only non-finite values and ±DBL_MAX (`real'low`/`real'high`) to 0.0; finite values pass through (5 MV does). RD2A ports therefore need no warning. |
| P8 | nvc `src/nvc.c` | `parse_time()` reads the `--stop-time` count as 64 bits (a digit loop that detects overflow) and is fatal past TIME'HIGH ("time … is too large: the largest is TIME'HIGH, 9223.37204 s"); malformed values (`-5ns`, `5`, `5xs`, `5nsec`, trailing text) are errors. Before it, `sscanf("%u")` wrapped the count modulo 2^32 fs, silently, for plain nvc too. |
| P9 | nvc `lib/sv2vhdl/sv_display_pkg.vhd` | `sv_tstr(value : integer; …)` scales decimal digit strings instead of 32-bit INTEGER arithmetic, so `%t` of any time works (a 64-bit time quotient reaches it through `integer'image`; 3 ms at 1ns/1ps used to stop the run); the output layout is unchanged (padding, `$timeformat` width and suffix, rounding half up when scaling down). New overload `sv_tstr(value : real; scope_units, scope_prec : integer)`, formatted as vvp's `get_time_real`; the translator uses it for every real `%t` argument (T7). Where vvp's `get_time` drops a trailing zero (a value of two or more digits entirely right of the decimal point, with more decimals than the shift), `sv_tstr` prints the correct digits. The 64-bit count reaches `sv_tstr` only because nvc does not range-check a time/time quotient passed to an INTEGER parameter (a hand-written `t / 1 fs` folds to `t` and is checked); `%0d` of `$time` and of a `time` variable at 3e9 ticks match vvp. The package checksum changed: designs analysed before the rebuild must be re-analysed. |
| P10 | nvc `lib/sv2vhdl/resolver.c` (phase 5), `lib/sv2vhdl/sv_math_pkg.vhd` (the push) | Phase 5: `libresolver.so` also exported `sv_random()` and `sv_srandom()`, vvp's IEEE 1364 `$random` generator with one design-wide seed, for sv_math_pkg's VHPIDIRECT `random`/`srandom` (T16). Superseded at the push (nvc 5300c1406): sv_math_pkg's `random` and `srandom` are plain VHDL, bit-exact with vvp's `rtl_dist_uniform` (the uint32 seed in 16-bit halves, the doubles in the C's operation order, C's truncating casts by hand), so a translated `$random` needs no `--load`ed library (nvc's own Verilog route loads none) and `libresolver.so` exports no generator; `libsv_math.so` still has an unused C `sv_random`. Round 6 adds `$urandom`'s own generator and the seeded draws in plain VHDL too (P12). |
| P11 | nvc `src/rt/wave.{c,h}`, `src/nvc.c`, `src/cosim.c`, `src/rt/model.{c,h}`, `src/jit/jit-core.c`, `jit.h`, `src/vhpi/vhpi-model.c`, `src/names.c`, `src/common.{c,h}`, `src/util.{c,h}`, `nvc.1` (round 6) | **Waves:** a signal of type `SV2VHDL.LOGIC3D_TYPES_PKG.LOGIC3D`, a subtype or a vector of it, is a four-state VCD wire (L and 0 are 0, H and 1 are 1, Z is z, W, X and U are x), a vector one packed variable without `--dump-arrays` (logic3d was dumped as its 32-bit integer code, `$var integer 32`, and a vectors-only design ended with "Fatal: fstReaderOpen failed for temporary FST file"); a dump with nothing in it writes a valid empty VCD and a warning; under `--std=2040` scopes are `module`/`begin` and the VCD has no `$attrbegin` extensions; `--dump-scope=PATH[,LEVELS]` gives `$dumpvars(levels, scope)` (Verilog or nvc path names; one that names nothing is a warning); an alias whose primary signal was excluded is dumped as itself; with `--include`, `--exclude` or `--dump-scope` a scope left with nothing to dump is left out (the translator's `sv_and_ivl_N` and `sv_pullup_*_inst` scopes). **End time:** with `NVC_REPORT_END_TIME` set (not empty, not 0) `-r` ends with `** Note: simulation ended at <t> (stop time\|no more events\|stopped\|interrupted)`, with no time stamp; a `--stop-time` run that still had events ends at the stop time (`report_end_time`). **Co-simulation Ctrl-C** (`cosim_ctrl_c`): the first SIGINT stops the digital (`model_interrupt`; since the round-6 repair `model_interrupt_quiet`, which prints no interrupted fatal report, R6R-22); a second prints `** Error: co-simulation interrupted at <t> s` unless an end line is out (with `write(2)`) and `_exit(130)`; the interrupt the stopped digital never takes is dropped once the stop is taken (`model_drop_interrupt`, `jit_clear_interrupt`; it exited 1 with no end line). **VHPI and a Verilog top:** a `T_VERILOG` top unit (one that fell back to nvc's own Verilog parser) has no root instance: a note, then the `vhpiCbEndOfInitialization` callbacks; `vhpi_handle(vhpiRootInst)` and `vhpi_handle_by_name` return a VHPI error naming the module; a VHDL instance bound to a Verilog module is left out of the VHPI hierarchy with a note (with any VHPI plugin loaded, such as `libsv_math.so`, it was "Fatal: unsupported tree kind T_VERILOG in build_designUnit"). **`--std=2040` character literals** (`names.c` `resolve_name`): the single enumeration type in the context's type set decides a character literal, where it was left unresolved and `simp_ref` crashed ("Caught signal 11", an `unsigned` from a bit-string literal with only numeric_std in use); one that two such types share is the usual "ambiguous use of enumeration literal". **Temporary directories:** the Verilog route's per-file and batch translation directories are made with `mkdtemp` under TMPDIR, TEMP or TMP (else /tmp) by `nvc_temp_dir` and removed at exit by the process that made them (`/tmp/nvc_sv2ghdl_<pid>` was never removed); `NVC_SV2GHDL_KEEP` keeps them and says where. **accel notes:** `fused_block_build`'s "accel-jit: NVC_FUSED_BLOCK …", NVC_SPLICE and over-budget notes print only under `NVC_ACCEL_JIT_DEBUG`, like the fast-clock notes (every vamos run past 1000 ns with four processes on one clock printed one). `nvc.1` documents `--dump-scope`, `NVC_REPORT_END_TIME`, `NVC_SV2GHDL_KEEP`, the empty scopes and "Designs translated from Verilog". |
| P12 | nvc `lib/sv2vhdl/sv_math_pkg.vhd`, `sv_tran.vhd`, `resolver.c`, `logic3d_types_pkg.vhd` (round 6) | **Random:** `sv_rng_uniform`, a bit-exact port of vvp's `rtl_dist_uniform`/`uniform` on uint32 halves, C's int32 cast done by hand (`sv_trunc32`); `sv_random_value(seed)` and `sv_random_next(seed)`, the value a seeded `$random` draws and the advanced seed (the seed read as vvp's vpiIntVal reads it: the low 32 bits, x/z as 0; the advanced seed sign-extended past bit 31 as `vpi_put_value` does); `$urandom`'s own design-wide generator, `sv_urng`: `sv_urandom` (vvp's `urandom(0, UINT32_MAX, 0)`), `sv_urandom_range(max, min)` (vvp's `urandom(max, min)`: the bounds swapped by unsigned order, no draw when they are equal) and `sv_urandom_value(seed)`/`sv_urandom_seed(seed)` for `$urandom(seed)` (it draws from the caller's seed, advances it and leaves it in the `$urandom` generator). Every 2^31 offset is integer arithmetic (`sv_flip32`): numeric_std's vector `xor` crashed nvc after some thousands of calls in one process activation (§7 list). logic3d_types_pkg's `sv_random`, an LCG, is removed (T16). **sv_tran:** an x or z control of `tranif0`/`tranif1`/`rtranif0`/`rtranif1` gives x (it blocked like a 0). **resolver.c:** `Py_InitializeEx(0)`, with the SIGINT disposition saved and restored around Python's start-up and around `resolve_net` (a SIGINT in nvc's first ~0.15 s became a Python `KeyboardInterrupt` that the run survived; with it, an interrupted co-simulation also printed nvc's own `** Fatal: <t>+<d>: interrupted` report until R6R-22, §6 Signals); every resolver error is `** Error: resolver: ERROR - <text>` on stderr, whatever `SV2VHDL_QUIET` says (it was a note on stdout). **logic3d_types_pkg:** `l3d_mod_s` is Verilog `%` (`rem`, the dividend's sign; all x for a zero divisor), where it was VHDL `mod` (T22: no translation calls it); an element-wise `l3d_strengthen(logic3d_vector)` (T13). |
| P13 | nvc `src/rt/model.c` (round 6, the "kernel" patch) | The #75 kernel net solver re-classified a member net only on events, but a member's own drive can change while its resolved value does not (the plug's rest-of-net view masks it), so the views went stale: two tri-state nets joined by a tran each kept the other's initial X. A member net's `update_driving` hook now re-classifies the member and wakes its watch when its own contribution moves without an event; its hash lookup survives the fast-clock guard's vtable copies, it is re-installed on member events after `force`/`release` revert the vtable, and it costs nothing on other nexuses. ivtest pr2832234 needs it; nvc/regr is identical per test with it. |
| P14 | VACASK `devices/spice/diode.va`, `mos3.va`; Xyce `DeviceModelPKG/OpenModels/N_DEV_Diode.C`, `N_DEV_MOSFET3.{C,h}` (round 6) | **Diode sidewall charge:** its own F1, `DIOtF1SW = PHP·(1−(1−FCS)^(1−MJSW))/(1−MJSW)`, in VACASK's `deplchargeSW`, and a local `tF1SW` in both of Xyce's diode load paths (the plain and the templated Sacado one; the header layout is unchanged). Both engines used the area's F1, discontinuous where FC differs from FCS or PB from PHP: a DCAP=1 diode with `fc=0.5 fcs=0 php=0.6` stopped VACASK with "Timestep too small. Transient analysis aborted." and failed on Xyce; now C(v)·dv/dt matches the depletion formula at 8 points from −0.8 to 0.6 V within 3e-3 on both, with no spike. **LEVEL 3:** a model parameter `badmos3` in VACASK's `sp_mos3` (its `$simparam` fallback kept) and `BADMOS3` in Xyce's MOSFET level 3: SPICE2's channel-length modulation, which HSPICE uses (21-26; vamos writes `badmos3=1`, §4.3.6); `sp_mos3`'s CLM square root is guarded where KAPPA·alpha = 0 (KAPPA=0, or no NSUB used, gave NaN). |
| T1 | iverilog `tgt-vhdl/stmt.cc` (sv2vhdl mode only) | `$stop[(n)]` → `sv_write_flush; std.env.stop;` (nvc prints "STOP called", exit 0). `$info`/`$warning`/`$error`/`$fatal(args)` print vvp's two lines through `sv_display_line` (`<SEV>: <file>:<line>: <msg>` and `<pad>Time: <ticks>  Scope: <%m>`), then `report "<SEV>"` at severity note (no clause), warning, error or failure: the run continues after `$error` (nvc exits 1 at the end, remapped by simv, §6) and stops non-zero after `$fatal`. `$fatal`'s first argument (the finish number) is skipped unless it is a string literal. `<file>` is the translated file (`nvc/_norm.sv`), which vamos's output filter rewrites to the user's file:line since the round-6 repair (R6R-20, §6), and `$info` adds a bare `INFO` output line. The Scope line and `%m` name the innermost scope of the call (T11): the function, task or named block (`top.chk`, `top.tk`, `top.blk`); a block with declarations is `top.$unm_blk_<n>` and an SV for-loop `top.$ivl_for_loop<n>`, as in vvp; at process level the process's own (generate) scope. A task or function whose whole body is one named block gives that block (`top.tk.body`, the LRM's answer) where vvp gives the task. In a module instantiated more than once every instance named the default one until the round-6 repair (R6R-07). Inside a function, nvc adds a line `   Function <F> [...] at design.vhd:<n>` after each printed line (sv_display_line prints through `report`); vamos's output filter and bin/vvp-sv2ghdl drop it. |
| T2 | iverilog `tgt-vhdl` (`IVL_SW_TRAN_VP`; `scope.cc`, `logic.cc`, `process.cc`, `vhdl_syntax.{hh,cc}`) | An inout port connected to a bit- or part-select emits `alias SW<switch><genvar suffix>[_u<k>]_b is <vec>(<off>);` or `<vec>(<off+w-1> downto <off>)` (no subtype, so the alias keeps the vector's index range; `<off>` the bit offset), with the comment `-- Inout part-select connection at <file>:<line>`, and the port map uses the alias. Fallback, when the vector is an `in` or `out` port of the enclosing entity or not a `logic3d_vector`: a signal plus a one-way copy, with the warning "Warning: inout port on <vec>(<sel>) at <file>:<line> is connected one way only: <why>" (stderr and iverilog.log); the cut's guard refuses that shape on a cut port. Three supporting changes: switch temporaries `SW*_a\|_b\|_en` carry the genvar suffix and never share two nets (`_u<k>`); `inout_driven_internally` counts a switch in a generate block of the port's own module; `fuse_comb_processes` no longer fuses a continuous assign to a resolved target into a `:=` deposit (the deposit bypassed resolution). Phase 5: an instance array on a part-select (`pad pa[1:0] (.p(bus[2:1]))`, also on the module's own inout port, e.g. `iobuf iob[3:0] (.IO(gpio[3:0]))`) aliases each element's part straight to the vector (`alias SW_ivl_2_b is bus_sig(1);`), skipping the core's temporary (`tran_vp_vector`). Every other part-select tran drawn as a one-way copy warns "Warning: <vec>(<sel>) at <file>:<line> is connected one way only: its part-select tran joins a translator temporary"; since round 6 a `tran`/`tranif` primitive is one of these only on a select of a module port (below). A net that only joins drive (switch endpoints and inout ports) gets its default too, so a bit nothing drives reads z, not x; a tri0/tri1 one keeps only its pull, and no constant is ever drawn into a port declared `in`. An inout port on a concatenation (`.y({p, q, r, s})`, `.y({bus[2:1], w, v})`) is associated part by part (`y(3) => p, y(1) => w, y(3 downto 2) => bus_sig(1 + 1 downto 1)`), a select operand mapping to its vector slice, and those part-select trans are not drawn. An operand that is an `in`/`out` port of the enclosing module goes through a resolved `PB_<label>_<formal>_<off>` signal and a one-way copy, with "Warning: inout port <path> at <file>:<line> is connected one way only to <port>: an input\|output port of the enclosing module". A SPICE-only cell's auto (inout) bus port on a concatenation therefore needs no `port_dir`: the IEs are those of `port_dir input`/`output`. Fixes plain `vcs` mode too. Round 6 (`scope.cc` `alias_tran_select_temp`, the new `vhdl_scope::remove_decl`; `logic.cc` `draw_one_switch`, `switch_terminal_ref`): a `tran`/`tranif` on a bit- or part-select of a net of the module is joined both ways – the core's temporary becomes `alias tmp is bus_sig(k)` (or a slice) and the switch takes the element itself, with no copy and no warning; a select of a module port stays one way, warned (an error under `--vamos-strict`). An array of switches, which iverilog hands the target as one switch as wide as the array (`ivl_switch_width`, never read before: silently no switch, or nvc's "type kind T_ENUM does not have item I_ELEM"), is one `sv_tran*` instance per bit, on bit k of each terminal and of the enable (IEEE 1364 7.1.6's per-instance bits, where vvp switches every bit on the enable's bit 0). With P12's x/z control and P13, ivtest pr2832234 and pr3296466a/c/d pass. |
| T3 | iverilog `tgt-vhdl/scope.cc` | Before each user-module instance statement, two comment lines: `-- Generated from instantiation at <file>:<line>` and `-- Verilog instance: <path>`, the generate-scope basenames plus the instance basename relative to the enclosing module, indices as Verilog writes them (`g[1].xb`, `ua[0]`, `genblk2.un`, `outer[0].inner[1].deep`). Library primitives get none. An escaped instance name gives its raw basename in the comment (vamos compares `-inst` paths with escapes undone, so `\a+b ` matches `a+b`) and, since phase 5, a valid VHDL label: characters other than letters, digits and `_` become `_`, and a leading digit gets `inst_` (`a_b`, `x_y`, `inst_9lives`). |
| T4 | sv2ghdl `bin/sv2vhdl-modules`, `bin/iverilog-sv2ghdl` | iverilog's stderr goes to `<outdir>/iverilog.log` as sections, each a header line then that run's output (a per-module sv2vhdl-modules section holds the run's stdout and stderr): `=== iverilog-sv2ghdl: iverilog -E (preprocess)`, `=== sv2vhdl-modules: iverilog -tvhdl -s <module>: translated \| deferred (iverilog exit <rc>) \| deferred (timed out after <n> s) \| deferred (no VHDL written)`, `=== iverilog-sv2ghdl: iverilog -tvhdl (whole design)`, `=== iverilog-sv2ghdl: iverilog -tvhdl <mod> (per-module fallback)`; a section runs to the next line starting `=== `. For every `sv2vhdl:deferred … module=X` stub left in `design.vhd`, iverilog-sv2ghdl prints "iverilog-sv2ghdl: module X was not translated (a deferred stub in design.vhd); iverilog said:" and that section (indented, or `(nothing)`). `sv2vhdl-modules [--log FILE]` appends; without `--log` it starts `<dirname -o>/iverilog.log` afresh. Stage-1 stdout ("VHDL conversion error") is printed, not logged. sv2vhdl-modules removes a partial output file left by a killed iverilog run; its own stderr is discarded inside iverilog-sv2ghdl, which prints the deferred-module report itself. iverilog-sv2ghdl exited 0 when the top was deferred until the round-6 repair (below). In AMS mode vamos refuses every deferred module (§5.2); plain `vcs` mode stops (exit 1) for every deferred top with "sv2ghdl could not translate top module '<m>': <the module's iverilog.log section, `_norm.sv`/`_pp.v` lines mapped to the user's file:line> (see <daidir>/nvc/iverilog.log)", plus a `-top` hint when the module is a top only because nothing instantiates it (`vcs.deferred_reason`); the translator's module-by-module last resort is a warning. Phase 5: under vamos (`VAMOS_STACK` set) iverilog-sv2ghdl also prints, on stderr, the iverilog.log warnings containing "connected one way only" or "not translated" from the top module's run (or the whole-design run), as `iverilog-sv2ghdl: Warning: …`; the ivtest harness, whose logs are compared with gold files, does not set it. vamos takes these lines out of the translation's output and reports each as a vamos warning at the user's file:line (`warning: tb.v:7: bus_sig(2) is connected one way only: ...`; verilog_ports.translator_warnings): an error under `--vamos-strict`, plain and AMS mode. `_metadata` now also carries the stage line `SV2VHDL_MODULES=1` or `IVERILOG_BACKEND=1` (still in `_metadata.tmp` too). Round 6 (`ivl_run`, `BACKEND_ERROR_RE`): a back-end run that exits 0 but prints an error line (`^(Error\|VHDL conversion error\|<file>:<line>: error): `) has failed: sv2vhdl-modules defers the module, status `deferred (<n> error(s) from the VHDL back end, which exited 0)`, and the whole-design and per-module stages take the run as failed and note `iverilog-sv2ghdl: <n> error(s) from the VHDL back end, which exited 0: this run failed` in iverilog.log (tgt-vhdl's `Error: first arg to $set_val must be a signal` had dropped an always block's body, so vcs exited 0 and simv printed X; T25 also makes that `$set_val` translate). When nvc cannot analyse the module-by-module translation (stage 1a) and the whole-design one analyses, nvc's messages are not printed: they are kept in `<outdir>/_mods_analysis.log`, citing the kept VHDL `<outdir>/_mods_design.vhd`, iverilog.log gets a section and a note per module, and under vamos each note also goes to stderr – `iverilog-sv2ghdl: note: module <m>, which the design does not use, does not translate on its own (nvc: <msg>, <file>:<line>); it is left out`, or `… the module-by-module translation of module <m> does not analyse (…); the whole-design translation, which analyses, was used` (every hazard3/vamos compile printed nvc's `** Error` lines for the unused `hazard3_onehot_priority_dynamic`). When the whole design fails too, they are printed as before. Round-6 repair: outside vamos iverilog-sv2ghdl exits 1 for a deferred top ("iverilog-sv2ghdl: the top module <m> was not translated (a deferred stub; see above)"), as iverilog fails on a design it cannot elaborate (R6R-12); under vamos (`VAMOS_STACK` set) it still exits 0 and vamos reports the stub (§5.2, §8); and `_metadata`'s `TOP_ENTITY` is the entity the translation made of the top (R6R-13: tgt-vhdl renames a module named after a VHDL reserved word, `pipe` → `pipe_module__<hash>`). |
| T5 | iverilog `tgt-vhdl/scope.cc`, `vhdl.cc`, `vhdl_target.h` (sv2vhdl mode) | Each `tri1`/`tri0` net gets one `-- sv_strength: pull1 pull0` comment and an `sv2vhdl.sv_pullup`/`sv_pulldown` instance per bit (`sv_tri1_<sig>[_b<i>]`), on the net's lowest plain signal, whether or not something else drives it; the net is declared resolved when it has another driver (the old strong `<= L3D_1/L3D_0` default is gone). An unconnected `tri` input port keeps the constant actual (one its module drives, declared inout, gets a `PB_` signal carrying the pull instead, since an inout formal takes no constant). Phase 5: a `tri1`/`tri0` input behind the core's port buffer (a variable, constant, expression or concatenation actual, T8) carries its pull on its `PB_<label>_<port>` signal (`sv_tri1_PB_<label>_<port>[_b<i>]`), and one on a bit- or part-select (coerced inout, T2) on its alias (`sv_tri1_SW…_b`). Only a net with neither, i.e. a root port, gets no pull, with the warning "Warning: tri1 net <path>: its pull is not translated (no internal signal to attach it to)"; under vamos the top run's such warnings reach the console (T4). logic3d still has no pull-versus-weak distinction. Fixes plain `vcs` mode too. |
| T6 | iverilog `tgt-vhdl`, sv2ghdl scripts | (Later) an SI time base for precisions coarser than 1 ms on request (`-psv2vhdl-si-time=1`); lifts the §1.2 1 ms rule. Precisions of 1 ms or finer are SI already (T11). |
| T7 | iverilog `tgt-vhdl/stmt.cc` (`%t`), `support.{cc,hh}` | A real `%t` argument (`$realtime`, a real variable) goes, in the scope's units, to P9's `sv_tstr(value : real; …)` (phase 5: `sv_tstr((real((now / (1 ps))) / 1000.0), -9, -12)`, `sv_tstr(r, -9, -12)`; the `integer(...)` cast is gone). That overload scales it in double precision and prints `%.<p>f` as vvp's `get_time_real` does, so 5.355 ns prints 5355; there is no INTEGER limit, so `%t` of `$realtime` past 2^31 precision ticks prints (3 ms at 1 ps, 3 µs at 1 fs; it used to stop the run, "value … outside of INTEGER range"); a `$timeformat` finer than the precision shows every digit (`1234.500 ps`); ties round to even and -0 prints `-0`. Without `$timeformat` the unit is the smallest precision of the whole design (`ivl_design_time_precision`, IEEE 1364 §17.3.2, vvp), not the calling scope's, for integer and real arguments alike. The field width follows vvp and VCS through the support function `Verilog_Time_Field(S, W, Z)`: `%0t` no padding, `%Nt` blanks, `%0Nt` zeros, plain `%t` the `$timeformat` width; `%.Pt` writes a warning to iverilog.log. |
| T8 | iverilog `tgt-vhdl/scope.cc`, `logic.cc`, `vhdl_target.h` | **Driven input ports.** `input_driven_inside()`: an input port whose net is driven inside its module (a gate, LPM or constant in its subtree, or a child's output `reg`) is declared `inout resolved_logic3d` and its internal constant drivers are drawn (sv2vhdl-modules translates each module as an elaboration root, where the core coerces nothing). **Port networks** (phase 5; `port_network`, `network_parent_ref`): the core's transparent port buffer (`IVL_LO_BUFT`; an input port with internal drivers and a variable, constant or expression actual), and what the core puts between it and the port – a zero pad (`IVL_LPM_CONCAT` with a constant 0), a sign pad (`IVL_LPM_SIGN_EXT`), a prune or an instance array's per-element part (`IVL_LPM_PART_VP`) – are drawn per instance in the parent and never in the child: `signal PB_<label>_<port> : resolved_logic3d; PB_<label>_<port> <= <actual>;` (or `… <= <actual>(1 downto 0);`, `… <= L3D_0 & <actual>;`), through a `PBT_<label>_<port>` temporary only when the actual is no plain parent signal, commented `-- Port buffer of input <port> of instance <label> …`, and associated with the port. An instance array has one buffer, in element [0]: a shared actual is one `PB_` signal that all elements map to (the core makes their ports one net, as vvp does); a split actual gives each element its part. `input_driven_inside()` does not count these nodes as the module's drivers; it does count (memoized) a port of an instance below that its entity declares inout, so a wrapper input passed to a pad ring is `inout resolved` too and a parent net joined to it is resolved, and a variable, gate/LPM output, temporary or constant actual of such a port goes through a one-way `PB_` copy (Verilog and VCS never coerce a variable to inout). Shapes the networks do not cover are translation errors, never silently open ports: a port-buffer-shaped BUFT with a cast of the parent behind it gives "VHDL conversion error: <file>:<line>: an input port connection of instance <path> is not translated: …"; an input fed from the parent through core nodes but left unassociated gives "… input port <p> of instance <path>: its connection is not translated". The buffer stays one-way, as in Verilog (§5.4). A signed actual narrower than a buffered port is zero-padded, as the core (and vvp) does, where the LRM sign-extends. Fixes plain `vcs` mode too (ivtest `pr841` passes; it never reads the port, so the pull semantics are covered by `tests/vamos/test_translator_ports.py`). |
| T9 | iverilog `tgt-vhdl/scope.cc` (`unique_instance_label`), `vhdl_syntax.hh` | Instance labels are unique per architecture (seeded from the existing instance and process labels): on a clash the enclosing generate block names are prefixed (`gb_u_g0`), then `_<k>`. Two generate loops that both name their instance `u` no longer give duplicate `u_g0` labels. T3's comment keeps the Verilog path. |
| T10 | iverilog `tgt-vhdl/scope.cc` (`declare_one_signal`, `map_signal`) | A formal is named with the port name the child entity declares (recorded per entity, case-collision suffix included: `OUT_sig_1 => o2`, `Q_1 => Q_1_Readable`), also as the base of the `_Readable` shadow (`port_formal_name`), so ports `out` and `OUT` on one module translate. |
| T11 | iverilog `tgt-vhdl/vhdl_syntax.{cc,hh}`, `expr.cc`, `stmt.cc`, `scope.cc` (phase 5) | **Time and scope.** `vhdl_tick_mult()` makes one tick of every precision of 1 ms or finer its SI size (10^(p mod 3) units of `vhdl_tick_unit`): `#50` at 1ns/10ps is `wait for 50000 ps` (it was `5000 ps`, so the digital ran 10× fast against the analog, and `+vcs+finish+N` stopped 10× late in plain mode), and `tick_literal`/`scope_unit_literal` share the base; coarser than 1 ms stays compressed (§1.2). `$time` and `$stime` round to the unit, half up, as vvp's `sys_time_calltf`: `((now + 500 ps) / (1000 ps))`, so `#56.93` gives 57 (it gave 56); the 64-bit quotient still reaches `%t` and `%0d` past 2^31 units. `$timeformat`'s suffix has its octal escapes decoded (`sv_set_timeformat(-12, 0, "", 10)`): an empty suffix is empty, not `\000`. The active scope follows a function, task or named block being drawn, saved and restored around each body: it drives T1's Scope line, `%m` and `$time` units (the ICG2EN module lookup climbs past TASK, FUNCTION and FORK scopes). A package function drawn on demand restores the caller's scope and entity: a delay after the call used to crash tgt-vhdl (ivtest `sv_package`, `sv_ps_function4` and `sv_ps_function5` now pass). |
| T12 | iverilog `tgt-vhdl/lpm.cc`, `scope.cc`, `state.cc`; sv2ghdl `bin/sv-rename-variants`, `bin/sv-dedup-vhdl` (phase 5) | **Real values.** An arithmetic LPM with a real operand or result (`+ - * /`, and `-r` as `0.0 - r`) is VHDL real arithmetic, with no `real_to_l3d1`; its temporaries are `real` (`nexus_is_real`). `IVL_LPM_CAST_REAL` is `l3d_to_real[_s](v)` (x/z bits count 0, as in vvp) and `IVL_LPM_CAST_INT` is `real_to_l3d(r, w)` (round half away from zero); `%` on reals is an error. A real constant net gets its value as its signal's initial value (so `r1 / 2.0` no longer divides by 0.0 at time 0). An undriven real net, and an unconnected real input, is 0.0, vvp's value (it was `<= L3D_Z`, which nvc rejected). So real expressions on a cell's real ports (`.vin(r1 + 0.2)`, `.vin(code * 0.1)`, `sel ? r1 : r2`) reach the analog. `same_scope_type_name` compares real parameter values bit for bit: several instances of a module with a real parameter translate (it asserted); any other parameter kind keeps the scopes apart. A `--   P = v` line prints a real with the fewest of 6 to 17 significant digits that read back exactly (`0.25`, `0.2500001`), so the cut sees a 7-digit override. sv-rename-variants includes those lines in the variant signature, and sv-dedup-vhdl compares them (a difference is a BODY CLASH), so design.vhd keeps each real or string variant's own values. |
| T13 | iverilog `tgt-vhdl/stmt.cc` (`draw_case_test`, `case_value_l3d`, `draw_casezx_l3d`, `make_assignment`), `logic.cc` (`default_logic`) (phase 5) | **Strength-free case.** Every scalar case selector becomes `l3d_strengthen(l3d_weaken(x))` (L,0→0; H,1→1; Z→Z; W,X,U→X), and so does a non-static item (`case (1'b1) pad:`): a pull or weak 1 (pullup, tri1, the BIDIR A2D's weak drive) matches `1'b1`, while X and Z still match only x and z items (`~x` matches `1'bx`). Scalar `casez`/`casex` compare exactly by IEEE's don't-care rules (they failed nvc analysis); a scalar selector with wider labels is a located error. The case test emits the blocking-read `wait for 0 ns` as `if` does (without it `r = pad; case (r)` read the stale r). A procedural assignment whose right-hand side reads a net stores `l3d_strengthen(value)` (a variable holds no strength), and a scalar continuous assignment always re-strengthens (the old `nexus_has_strength` gate missed the BIDIR A2D the cut adds after translation). Round 6 (`stmt.cc` `variable_value`, `logic.cc` `default_logic`): vectors too, with P12's element-wise `l3d_strengthen(logic3d_vector)` – a vector read of a net, and a vector BUFZ unless its input is a variable or strong constant that nothing else drives (`nexus_never_weak`; a reg sharing a net with pulls is strengthened); a vector `reg` copied from a weak net kept weak codes and mis-resolved against a strong driver (`bus`=1100 and `cbus`=1010 where vvp gives xxxx and x01x). A vector selector still compares only its value bits (the value-bit semantics, below). |
| T14 | iverilog `tgt-vhdl/stmt.cc` (`draw_synthesisable_wait`), `process.cc` (`nba_defer_commits`) (phase 5) | **Asynchronous reset in the NBA wake shadow.** The async-reset template registers wake-shadow arms as the generic path does (a snapshot of the reset arm with the event list's edge kind; a snapshot of the clock arm plus a missed-edge term ORed into the clock `elsif`, ICG2EN clock terms skipped), only once the template is kept, honouring `SV2VHDL_NBA_SHADOW=0`. A pass that loops back instead of re-arming skips the `v_nba_x := x` seeds (`v_nba_loopback`), which reverted first-pass NBA values still in flight. |
| T15 | iverilog `tgt-vhdl/process.cc` (`draw_process`), `stmt.cc` (`draw_block`) (phase 5) | **Time-zero initializers.** The hoist into a declaration initial value resolves the declaration by the signal's own VHDL name (`get_renamed_signal`), only when it was seen and lives in the architecture's scope (it hit `d` for `D`, a 4-bit or integer case collision, an `output reg`); a declaration that already has an initial value keeps a later time-0 process (`reg d = 0; initial d = 1;` gives 1); an initializer that calls a system function stays a time-0 process (`integer r = $random`). A named-block local whose name is already visible (a module signal or port, matched case-insensitively) gets its own name (`<name>_blk`; it was merged with the module signal). |
| T16 | iverilog `tgt-vhdl/expr.cc`, `stmt.cc` (`emit_pre_comment`; round 6: `emit_seeded_random_pre`, `draw_stask_random`, `translate_loop_test`, `draw_while_drawn_test`); nvc P10, P12 (phase 5, round 6) | **System functions.** An unseeded `$random` is sv_math_pkg `random` (plain VHDL since the push, P10): vvp's exact sequence, shared design-wide, at the expression width (truncated, or sign-extended past 32 bits); it returned 0 on every call (the bounds of `$urandom_range` were ignored too, and the run died: "foreign function sv_random not found"). Round 6: `$urandom` is `sv_urandom`, vvp's second design-wide generator (it was the `$random` draw with bit 31 flipped, so a testbench using both drew other numbers than vvp), and `$urandom_range(max[, min])` is `sv_urandom_range` on it, vvp's scaling, each bound translated once (it was `lo + draw mod (hi - lo + 1)`: the right range, other numbers). `$random(seed)` and `$urandom(seed)` draw ahead of their statement (`emit_seeded_random_pre`): `SV_Random_<n> := sv_random_value(seed); seed := sv_random_next(seed)` (`sv_urandom_value`, and `sv_urandom_seed`, which leaves the advanced seed in `$urandom`'s generator, for `$urandom(seed)`), the seed written back as a blocking assignment is (T25: a deposit, or `<=` with the wait-for-0 bookkeeping). That covers right-hand sides and inner expressions, two calls in one statement, `if` conditions, `$display` arguments, NBAs, always blocks, a call made as a task (`draw_stask_random`), a for-loop step and a function input as the seed; a while or for test draws again before every test (`translate_loop_test`, `draw_while_drawn_test`), and a loop test translated any other way is a located error. A seeded call Verilog may skip (a `?:` branch, an `&&`/`\|\|` operand) always draws, and a read of the seed earlier in its statement sees the advanced seed: each a located warning, given once, "<file>:<line>: $random(seed) is not translated faithfully: …", which iverilog-sv2ghdl passes on under vamos (T4; an error under `--vamos-strict`). A seed narrower than 32 bits, or a net, is a located error, as vvp rejects them. Until round 6 a seeded `$random` was an LCG (logic3d_types_pkg `sv_random`, removed), `draw_assign` advanced the seed after the statement, and `$urandom(seed)` was replaced by 0. Each sequence equals vvp's (1000 draws from each of the seeds 0, 1, −1, 2^31−1, −2^31 and 12345; `$urandom_range` at 14 edge bound pairs). A system function replaced by a constant – none in sv2vhdl mode since round 6 – puts `null;  -- Unsupported system function $f replaced by 0 here (<file>:<line>)` ahead of its statement (outside a procedural statement the same text stops the translation); vamos reports the comment (§1.3). |
| T17 | iverilog `tgt-vhdl/expr.cc`, `stmt.cc`, `state.cc`, `scope.cc` (phase 5) | **Classes and fork.** A call to a class method, `new` included, is "unsupported construct (class) at <file>:<line>: new() of SystemVerilog class C has no VHDL translation"; NEW, NULL, PROPERTY and SHALLOWCOPY expressions and a class task call are reported the same way; `find_entity` returns NULL for a class scope instead of asserting (`find_entity: Assertion … IVL_SCT_MODULE failed`, then an abort); a class nobody uses no longer stops its module, and a class declared inside a module works like one in `$unit`. A named fork scope and the `fork`, `join_any` and `join_none` statements give "unsupported construct (fork) at <file>:<line>". The module becomes the usual deferred stub, which vamos refuses (T4). |
| T18 | iverilog `tgt-vhdl/process.cc` (phase 5) | A process whose body is only a dropped system task (`always @(k) $fmonitor(…)`, `always @(v) ;`) keeps its sensitivity list and gets no `wait;` (nvc rejected the design: "wait statement not allowed in process with sensitivity list"). Every file and dump task (`$dump*`, `$readmem*`, `$writemem*`, `$fflush`, `$fdisplay*`, `$fwrite*`, `$fstrobe`, `$fmonitor`, `$fclose`) is located in every context (initial, edge and combinational always, task, function, final). Since round 6 the file tasks are translated (T26) and only the forms T26 lists keep the comment; the dump tasks keep theirs, which vamos reads for its waves (§8). |
| T19 | iverilog `tgt-vhdl/stmt.cc` (`make_assignment`) (phase 5) | **Compressed shifts.** `<<=`, `>>=` and `>>>=` take integer shift counts (`l3d_shcount` for a vector count, as `translate_shift` does), and `>>>=` uses `l3d_sra` on a signed target (`x >>>= n` was silently `x += n`, and a vector count gave `x sll <logic3d_vector>`, which nvc rejected); any other unknown operator is a located error. |
| T20 | iverilog `tgt-vhdl/stmt.cc` (`draw_wait`) (phase 5) | A block-level `@(a) stmt` (in an initial, a task, or after other statements) waits first: it was emitted as `stmt; wait on a;`. A translated top-level `always @(…)` ran once at time 0 until round 6; it waits for its first event since T25. |
| T21 | iverilog `tgt-vhdl/scope.cc` (`declare_one_signal`, `draw_constant_drivers`), `expr.cc` (`translate_signal`) (repair round) | **Memories at any base.** Every word address the core hands tgt-vhdl is canonical (0 = the lowest word), so a memory is `array (count-1 downto 0)`; it kept the Verilog range (`[4:7]` → `(7 downto 4)`, `[-2:1]`), so a write vanished or hit the wrong word and a read gave x or stopped the run. The static out-of-range read check and the per-word constant-driver walk use canonical indices too. |
| T22 | iverilog `tgt-vhdl/lpm.cc` (`binop_lpm_to_expr`), `expr.cc`, `stmt.cc` (`make_assignment`), `support.{cc,hh}` (`SF_REM_SIGNED`) (repair round) | **Signed / and %.** A signed IVL_LPM_DIVIDE/MOD (continuous) is `l3d_div_s` / `Verilog_Rem_S` (it was the unsigned logic3d operator: 0xFFE5/0x0077 = 550 where Verilog gives 0); every signed `%` (LPM, procedural, `%=`) is the support function `Verilog_Rem_S` (VHDL rem: the dividend's sign; nvc's `l3d_mod_s` was VHDL mod until round 6, P12, and no translation calls it); `/=` on a whole signed target is `l3d_div_s`. ivtest pr2722339a/b pass. |
| T23 | iverilog `tgt-vhdl/stmt.cc` (`build_display_text`) (repair round) | **Comparisons shown.** A VHDL boolean (a comparison) shown by $display/$write/$monitor/$strobe is cast to a logic3d bit first: `%d`, `%0d`, `%b`, `%h` and a bare argument print 1/0, never `true`/`false`. |
| T24 | iverilog `tgt-vhdl/stmt.cc` (`draw_disable`, `draw_block`, `draw_utask`, `draw_alloc_free`, `reset_automatic_vars`), `scope.cc` (`draw_function_in_entity`), `process.cc`, `vhdl_syntax.{hh,cc}` (`vhdl_labeled_loop_stmt`, labelled `vhdl_exit_stmt`, `vhdl_return_stmt`) (repair round) | **disable, SV return, task automatic.** A named block or inlined task that a disable inside it names is drawn as `sv_dis_<n>: loop … exit sv_dis_<n>; end loop`, and the disable is `exit sv_dis_<n>;`; a function's (`return expr`) is `return <f>_Result;` (every disable was `null`: `return` in a function, `disable <task>` and `disable <block>` were silently ignored). A disable of a scope that does not enclose it in its process, and `disable fork`, are located errors. IVL_ST_ALLOC of an automatic task starts a fresh activation (its locals and outputs reset to x, 0 for 2-state), FREE is nothing; an automatic block's variables are reset at entry; a recursive task (static or automatic) is a located error ("task <t> calls itself (recursion): tasks are inlined in VHDL, so a recursive task call has no translation"). An automatic task called from two processes was one too until round 6: each calling process now works on its own copy of the task's variables (T25), and only an activation entered again before it ends is an error. |
| T25 | iverilog `tgt-vhdl/process.cc` (`generate_vhdl_process`, `shadow_blocking_targets`, `settle_time_zero_waits`, `scan_shared_block_locals`, `hoist_shared_block_locals`, `fuse_comb_processes`), `stmt.cc` (`deposits_signal`, `make_assign_lhs`, `make_assignment`, `draw_stask_set_val`, `draw_stask_readmem`, `draw_wait`, `draw_delay`, the loops, `draw_break_continue`, `draw_do_while`, `emit_dist_pre`, `draw_alloc_free`), `expr.cc`, `scope.cc` (`declare_word_read`, `hoist_block_local`, `draw_constant_drivers`, `declare_one_signal`), `state.{cc,hh}` (`push_signal_home`/`pop_signal_home`), `vhdl_type.{hh,cc}`, `vhdl_syntax.{hh,cc}` (`set_deposit_blocking`), `vhdl.cc` (round 6) | **Verilog process semantics.** An `initial` or `final` block, and an `always` block that waits inside its body, deposits (`:=`) every blocking assignment to a signal and reads it back at once (`deposits_signal` is the one test, also for `$swrite`, `$set_val`, `$readmem*`, `$value$plusargs`, `$dist_*` and the seed write-back): nothing yields between two blocking assignments, so a VHDL module and a net computed from the variable see the new value when the block suspends, as in Verilog. Before, each blocking assignment was a `<=` whose following read waited a delta, and that wait let other processes run inside the Verilog time step (ivtest vhdl_test2: its VHDL dut ran between `in = in + 1` and `mask = …` and saw the new `in` with the old `mask`); an always block with two delays committed only at its end (`clk = 0; #5 clk = 1; #5;` left clk at 1). At time zero, before an initial block first suspends and outside loops, a read of what it deposited still waits a delta, so the nets computed from it have settled (the translation reads an x by its value bits: pr307a). The other always blocks keep their shadow variables, which now re-read the signal after every wait (`shadow_seed`/`shadow_commit`). **Time-zero order:** an `always @(…)` waits for its first event, as in Verilog; when an initial block assigns a signal at time zero, every initial block starts with `wait for 0 ns`, so its time-zero assignments are events to the always blocks (`settle_time_zero_waits`; all or none across the design: the initial blocks keep their order, and an initial block that only reads at time zero reads before the time-zero updates of other processes, ivtest vhdl_loop). An SV variable initializer stays hoisted into the declaration and is no event (IEEE 1800 6.8; iverilog-sv2ghdl elaborates every source with `-g2012`, also a `.v` file, where IEEE 1364 makes `reg a = 0;` an initial assignment, which vvp's default `-g2005` shows as an `always @(a)` event at time 0 and vvp `-g2012` does not). `SV2VHDL_TC08=0` turns the time-zero order off. A net that a `force` or `release` names keeps its own continuous assignment and is not fused into a comb cone (an nvc defect, §7 list; pr2849783, pr3368642). **Statements:** `repeat (n)` reads its count after a blocking assignment (`#1 n = 3; repeat (n)` looped 0 times; a task repeating over an input argument the same); SV `break`/`continue` become labelled loop exits (`sv_brk_<n>`, `sv_cont_<n>`) in `for`, `while`, `forever`, `repeat` and `do … while`, which is new (`draw_do_while`); `$dist_uniform`/`normal`/`exponential`/`poisson`/`chi_square`/`t`/`erlang` call the sv_math_pkg procedures ahead of their statement with vvp's numbers and advance the seed (`emit_dist_pre`), and where Verilog may skip or repeat the call (a `?:` branch, an `&&`/`\|\|` operand, a loop condition) the translation is a located error, as for `$value$plusargs`; an automatic task called from several processes works on a copy of its variables per process (`push_signal_home`; an activation entered again before it ends is "automatic task <t> is entered again before its activation ends: that has no VHDL translation"); a named-block local that another process names (`blk.t`) is an architecture signal under a unique name (`hoist_block_local`; nvc analysis failed, "no visible declaration"). **Memories:** a word read at a run-time index goes through `<type>_Rd` (`declare_word_read`) and gives all x outside the memory (0.0 for a real one), and such a store is dropped, as in vvp (it stopped the run: "index 5 outside of INTEGER range 3 downto 0"); `mem[i][j] = v` (a bit or part of a word, an NBA or `^=` too) takes the select's width (`make_assign_lhs`, `slice_element`; it took the word's, and Hazard3's `hazard3_onehot_priority_dynamic` failed nvc analysis); `$set_val` on a memory or vector translates (`m(i)(j) := v`, every index normalised from its declared bounds, a vector `[0:-1][14:-1]` too, the indexes captured once in `SetVal_Idx_<n>`, a store outside them dropped, every failure a located error: it was "first arg to $set_val must be a signal" and the process lost its body); strength buffers on memory words carry the word in their labels (`cd<n>w<j>b<b>_<net>`) and drive the word's bit `m(j)(b)`, and a memory of nets has resolved elements (`resolved_logic3d[_vector]`, `set_resolved_elements`; two tristate drivers on one word gave the first driver's value, `11` where vvp gives `x1`). `$urandom`'s 2^31 offset is an add (P12). Plain `-tvhdl` mode keeps its `wait for 0 ns` reads (`set_deposits_are_targets`). ivtest/iverilog-nvc 1366 → 1430 of 3011 and ivtest/nvc-vhdl 282 → 286 of 294 with this patch alone, no pass→fail (§9). |
| T26 | iverilog `tgt-vhdl/fileio.cc` (new), `stmt.cc` (`build_display_text`, `display_radix_text`, `monitor_watch`, `draw_stask_readmem`), `expr.cc`, `Makefile.in`; nvc `lib/sv2vhdl/sv_display_pkg.vhd`, `logic3d_types_pkg.vhd`; sv2ghdl `vamos/backends/nvc.py` (round 6) | **File I/O.** `$fopen`, `$fopenr`/`$fopenw`/`$fopena` (`$fopenr`/`w`/`a` were a hard translation error, `$fopen` returned 0), `$fdisplay`, `$fwrite`, `$fstrobe`, `$fmonitor` and their `b`/`h`/`o` forms, `$fclose` and `$fflush` run on the sv2vhdl runtime: descriptors are the arguments' vpiIntVal (`sv_vec_int32`) and file names their vpiStringVal (`sv_vec_str`); sv_display_pkg holds vvp's descriptor tables (`vpi_mcd.cc`: MCD bit 0 is stdout, files from bit 1; FDs from 32'h8000_0003, the lowest free slot first, up to 1024), writes the bytes vvp writes (stdout and FD 1/2 through the `$display` line buffer) and prints vvp's mode, name, invalid-descriptor and `$fclose` messages; `w+`/`a+` write as `w`/`a`, `r+` is refused with a warning. Each `$fstrobe`/`$fmonitor` statement has a postponed companion process; each call is recorded in a run-time registry under its request signal's `'path_name` (each instance its own) and written to its own descriptor with its own text; a monitor watches a constant memory word or part select by itself (`monitor_watch`; `$monitor` of `mem[0]` no longer reprints when another word changes) and stops at an `$fclose` of its descriptor, not at `$monitoroff`. `$readmemh`/`$readmemb` build on the upstream store (nvc 2e7d16d6a, iverilog b918ff864): `sv_readmem_load` follows `vpi/sys_readmem.c` (its argument checks, lexer with `//` and `/* */` comments, `@` addresses, `_` and invalid characters, the address rules and messages, words zero-filled as vvp does; `?` is an invalid character) and `$writememh`/`$writememb` are new (`sv_writemem_open` …); messages come back through `sv_memfile_msgs` and print through the line buffer. The VHDL index is the address less the lowest one (T21; upstream indexed with the address itself, which stopped the run for `b[16:19]`), a memory of 1-bit words uses `sv_readmem_bit` (ivtest pr690 failed nvc analysis), a word is written as a blocking target is (T25), file names may be non-literal, and a `$readmem` in a function that would assign a signal is a located error. Relative names resolve against `SV2VHDL_FILE_DIR` (`sv_file_path`), which vamos sets to the directory `./simv` was started in (§6). Left out with the located "Unsupported system task" comment (vamos: a warning, an error under `--vamos-strict` and in AMS): `$readmem*`/`$writemem*` of a memory in another module or of a real or multi-dimensional memory, `$fstrobe`/`$fmonitor` in a function, and `$readmempath`. The reading tasks (`$fgets`, `$fgetc`, `$ungetc`, `$fscanf`, `$sscanf`, `$fread`, `$fseek`, `$ftell`, `$rewind`, `$feof`, `$ferror`) stay a translation error (**open**). A 13-case battery matches vvp byte for byte on stdout and in every file written; ivtest fopen1, fopen2 and writememb1/2, writememh1/2 pass. |
| R6R-01 | iverilog `tgt-vhdl/stmt.cc` (`make_assignment`, `const_index_undefined`) (round-6 repair, as are the rows below) | A constant word index or bit offset with x/z bits, ivl's mark for an out-of-range constant l-value it ignores ("ignoring out of bounds l-value array access"), drops the store, as vvp does (in a concatenated target, that part only): `array1[0] = 1` on `reg array1[2:1]` wrote `array1[1]` (ivtest array_lval_select1/2). |
| R6R-02 | `stmt.cc` (`guard_index`, `draw_runtime_part_store`) | A bit store at a run-time index outside a vector, or outside a memory word, is dropped, as vvp drops it (it stopped the run: "index 6 outside of INTEGER range 3 downto 0"; T25 guarded only the word index); a part store at a run-time base, to a vector or a word, is drawn bit by bit, each bit guarded, with its intra-assignment delay (`v[k +: 2] <= #3 x` landed at once). |
| R6R-03 | `stmt.cc` (`draw_nbassign`) | An intra-assignment event control on a nonblocking assignment (`x <= @(posedge c) v`, `x <= repeat (n) @(posedge c) v`) is the located error "no VHDL translation for an intra-assignment event control on a nonblocking assignment (…)": the event control was dropped and the value stored at once (ivtest nb_ec_*: wrong values, or a run that never ended). |
| R6R-04 | `stmt.cc` (`draw_stask_swrite`) | `$sformat(dest, fmt, …)` is translated as `$swrite` is, with a string-literal format (it was dropped: dest kept its old value); any other format is the located error "no VHDL translation for a $sformat format that is not a string literal". |
| R6R-05 | `stmt.cc` (`build_display_text`), `support.{cc,hh}` (`SF_REAL_G`, `Verilog_Real_G`) | Bare arguments of `$display` and its kin print as vvp's: `$time` and `$simtime` right-aligned in 20 columns, `$stime` in 10, `$realtime` with the scope's precision digits, a real as C's `%#g` (`2.50000`). |
| R6R-06 | `stmt.cc` (`monitor_watch`), `vhdl_syntax.{hh,cc}` (`vhdl_var_ref::extra_slices_constant`) | A monitor watches a constant bit or part of a memory word by itself (`$monitor` of `array[0][1]` printed again on every write to `array[0]`; ivtest pr2785294). |
| R6R-07 | `stmt.cc` (`hier_name_expr`, `declare_hier_names`), `scope.cc` (`instance_vhdl_path`), `state.cc` (`same_type_instances`), `vhdl_syntax.{hh,cc}` (`vhdl_verbatim_decl`) | `%m` and T1's Scope: line name each instance of a module instantiated more than once (`t.u1`, `t.u2`; every instance named the first): there the instance's name is the architecture's constant `SV_Hier_Name`, which `SV_Hier_Lookup` finds at elaboration from the path name of the architecture's constant `SV_Hier_Mark` (each instance known by its labels from its design root, so `vamos_tops` above the root changes nothing), followed by the scope's path inside the module; a module instantiated once keeps a constant string. |
| R6R-08 | `scope.cc` (`undriven_bits_initial`) | The undriven bits of a vector net driven only through parts start and stay z (`wire [3:0] w; assign w[1] = 1'b0;` gives `zz0z`, as vvp; it gave `xx0x`), and so does a net with no driver at all. |
| R6R-09 | `process.cc` (`draw_process`) | A `final` block is the located error "no VHDL translation for a final block: a VHDL process cannot run when the simulation ends (it would run at time 0)": drawn as an initial block, it ran at time 0, silently. |
| R6R-26 | `process.cc` (`final_only_closes_files`) | A `final` block of only `$fclose`/`$fflush` is left out instead: the end of the run flushes every file and closes it (vhdlpp writes `final $fclose(f)` for each VHDL file object; ivtest vhdl_textio_write). |
| R6R-27 | `scope.cc` (`draw_function_in_entity`), `expr.cc` (`translate_ufunc`) | A SystemVerilog void function is the located error "no VHDL translation for the void function <f>: write it as a task": it has no result port, and drawing it crashed the translation inside ivl (an `ivl_signal_data_type` assertion; ivtest function10). |
| R6R-10 | `stmt.cc` (`quiet_end_marker`); sv2ghdl `bin/vvp-sv2ghdl`, `vamos/backends/nvc.py` (`OutputFilter`) | `$finish(0)` and `$stop(0)` print no end message, as vvp and VCS print none for 0: the translation reports the note `sv2vhdl: quiet end` before `std.env.finish`/`stop`, and both output filters drop it and nvc's next `FINISH called`/`STOP called` (ivtest nested_impl_event1). |
| R6R-21 | `stmt.cc` (`census_writers`, `sole_writer`, `deposits_signal`); nvc `src/rt/model.c` (`sched_driver`) | A nonblocking assignment in a process that deposits (T25), to a variable that process alone writes (`census_writers`, a pre-pass over every process), is a `<=` that lands in the next delta, as any NBA does: drawn as a deposit, it landed at once (`w <= 7; $display(w)` printed 7, vvp the old value); with other writers it stays a deposit. nvc no longer elides a same-value driver update when a deposit has made the signal's effective value differ from the driver (`w = 3; … w <= 7;` with the driver at 7 left w at 3). |
| R6R-22–24 | nvc `src/rt/model.{c,h}` (`model_interrupt_quiet`), `src/cosim.c` (`cosim_ctrl_c`); `src/vhpi/vhpi-model.c` (`nvc_vhpi_stitch_net`); `src/rt/mspace.c` (`eval_arena_alloc`), `src/jit/jit-intrin.c` (`__tlab_overflow`) | **Quiet co-simulation interrupt** (R6R-22): the first SIGINT aborts the process it stops without nvc's interrupted fatal report, so no `** Fatal: <t>+<d>: interrupted [in process …]` line comes out ahead of the co-simulation's end line (§6, Signals). **Memory-of-nets elements** (R6R-23): the kernel net solver takes the `(W)(B)` element paths of a memory of nets, the bit nets `m(j)(b)` (it declined them, "resolver: kernel declined .main.foo(1)(0) (UNRESOLVED)", and ivtest pr1703346's words of 2 or more bits read `xx`, exit 0); the run now stops in `l3d_resolve` for every word width, as for 1-bit words (loud, **open**, §7 list). **Aligned eval arena** (R6R-24): every eval-arena result is 16-byte aligned, as a TLAB result is, and a TLAB overflow is given the aligned size the SSE4.1 vector intrinsics write: a long run of std_logic_1164/numeric_std vector `xor` calls in one evaluation spilled into the arena and crashed (SIGSEGV in `ieee_xor_vector_sse41`; a pure-VHDL loop of 200000 `unsigned xor` calls under `--std=2008` and `2040`). |
| R6R-11–20, 25, 28–32 | sv2ghdl `bin/iverilog-sv2ghdl`, `bin/sv-normalize`, `bin/sv2vhdl-modules`, `bin/vvp-sv2ghdl`, `regress/lib/Regress/Block.pm`; vamos `personalities/vcs.py`, `ams/flow.py`, `ams/cut.py`, `ams/verilog_ports.py`, `backends/nvc.py`, `netlist/spice.py`, `ams/deck.py`, `netlist/ir.py` | **Scripts:** iverilog-sv2ghdl probes whether the iverilog in use takes a chained select on the left (`m[i][j] = v`) and then sets `SV_NORMALIZE_NO_SET_VAL=1`, which sv-normalize's Rule 6 honours (R6R-11: its `$set_val(m, i, j, v)` evaluated `v` at its own width, so `m[i][j] = a + b` lost the carry, silently); iverilog-sv2ghdl exits 1 for a deferred top outside vamos (R6R-12, T4) and writes the top's entity as `TOP_ENTITY` (R6R-13); vvp-sv2ghdl sets `SV2VHDL_FILE_DIR` to its start directory unless the caller set it, so the ivtest flow finds a testbench's relative data files (R6R-14: pr690, readmemh1, …), and drops a quiet end (R6R-10); sv2vhdl-modules passes no `-B` of its own for an `IVERILOG` with no `lib/ivl` beside its `bin/`, such as a wrapper with its own `-B` (R6R-15: it named the shared build's plugins, so the shared `vhdl.tgt` translated, silently); the regress harness's `iverilog` engine puts the build-area iverilog's `bin` first on PATH, since `vvp_reg.pl` calls a bare `iverilog` (R6R-25); sv-normalize keeps a min:typ:max specparam at its typ value (R6R-28: a syntax error before; ivtest pr1587634) and ends a specify block written on one line at once (R6R-32: it dropped every line to the next `endspecify`, `endmodule` too); sv2vhdl-modules and iverilog-sv2ghdl strip block comments over several lines, and strings, before the module list and the top guess (R6R-29). **vamos:** `$dumpfile`/`$dumpvars` in an AMS design are read as in a plain compile (`flow.py` calls `vcs.dump_request`; they were "not translated" errors), and a cut output that no digital code reads keeps its A2D when the waves record it (`vcs.dump_covers`, `cut.assign_roles(dumped=…)`; R6R-16, §1.3, §5.4, §8); `.option delmax` sets the maximum step of the analysis vamos synthesises for a netlist with no `.tran` (`spice.py`, `deck._analysis`; R6R-17, §4.3.5, §4.7); `Subckt.spelling` keeps each subckt's own header spelling of its ports (`ir.py`, `spice.py`; R6R-18, §3.1); the `+vcs+dumpvars` part of a compile note that a design's `$dumpvars` carries is labelled as the option's (R6R-19, §8); the run's messages name the user's file:line (`nvc.write_source_lines`, `nvc.source_relocator`; R6R-20, §1.1, §6); a plain compile elaborates the entity of its top (`vcs.top_entity`; R6R-13: `** Fatal: cannot find unit WORK.PIPE` for `module pipe`); the cut guard's example names a select of a module port (§5.4); the timing the simulation leaves out is warned (`verilog_ports.timing_omissions`, from `vcs.py` and `flow.py`: specify path delays, timing checks and `$sdf_annotate`, §8; R6R-30), and so are a single top's input ports, which read 0 under nvc where VCS leaves them z (`vcs.undriven_top_inputs`; R6R-31). |

**3D-logic value-bit semantics (by design).** The translator computes on value bits: `===`/`!==`, a vector
`case` selector, vector arithmetic and an index with x/z bits do not follow IEEE 1364's x/z rules (the first
four items below). This is deliberate (the user's decision, 2026-10-03): it is kept, documented as a known
difference from vvp and VCS, and no IEEE mode is planned. ivtest `tri3` fails because of it (§9: `===`), and
so do array_lval_select1/2 (their run-time x-index lines), array_select_a and pr2913927 (the x/z index);
`vhdl_smul23_stdlogic`, which revision 5 also put down to it, passes since T25 removed the delta race in its
check.

Open translator items besides those marked in the table (all **open**, except the value-bit items above):
- `===`/`!==` (`l3d_eq1`) compare only the value plane: x and z are not told apart (`rz === 1'bx` is true for
  a reg holding z, where vvp gives 0), and a weak value compares by its value only. Case equality therefore
  silently mis-simulates, also on cut nets where an A2D drives X or a BIDIR drives `L3D_W` (this is the `===`
  operator; the case statement is fixed, T13);
- a vector `case`/`casez`/`casex` selector compares only its value bits (`draw_case_test` converts it with
  `l3d_to_unsigned`, so x/z bits read as their value bits: `case (w)` with w = z0 takes the `2'b00` item,
  vvp `2'bz0`; a vector casez/casex never sees a z or x selector bit): the value-bit semantics, kept by
  design (above);
- vector arithmetic with x/z operands computes on the value bits (`4'b1x01 + 1` gives `1010` where vvp gives
  `xxxx`): the value-bit semantics, kept by design (above);
- an index with x/z bits selects by its value bits (nvc b0b5eeffa, `l3d_index`: the certainty-0 doctrine, in
  which certainty never gates a read, so an all-x index is word 0): a store at an x index lands in word 0
  and a read returns word 0 (or the bit the value bits name), where vvp drops the store and reads x: the
  value-bit semantics, kept by design (above);
- a continuous assignment that copies a variable (`wire rw = r;`, `wire m0w = mem[0];`) is updated one delta
  after a blocking write to the variable, so a read of `rw` later in the same activation sees the old value,
  where vvp, upstream Icarus and the kev-cam fork alike, sees the new one (a probe: `I t=3 r=33 rw=77` under
  vamos, `rw=33` under vvp). IEEE 1364 allows both: it leaves the order of the net's update and the writer's
  next statement open;
- a recursive task (static or automatic), and an automatic task entered again before its activation ends, are
  translation errors (T24, T25); `disable fork` and a disable of another process's block too (T24);
- a void function, a `final` block (unless it only closes files) and an intra-assignment event control on a
  nonblocking assignment are located translation errors (R6R-27, R6R-09, R6R-03): a void function could be
  translated as a procedure, as tasks are; a `final` block has no VHDL point to run at;
- specify path delays, timing checks and `$sdf_annotate` are not simulated (sv-normalize drops specify
  blocks, keeping their specparams as localparams; iverilog omits `$sdf_annotate` with them): warned
  (`verilog_ports.timing_omissions`, R6R-30);
- a single top-level module's input ports read 0 (nvc elaborates the top alone, and an input it cannot
  associate takes its type's first value), where VCS leaves them undriven (z): warned
  (`vcs.undriven_top_inputs`, R6R-31). The multi-top wrapper already ties inputs to Z: using it, or
  `:= L3D_Z` defaults on root input ports, is the fix;
- named events across scopes (`-> e` / `@e` on an event of another scope) have no translation: the wait
  translates to an empty `wait on`, which nvc rejects (ivtest scoped_events, loud);
- `$ivlh_attribute_event` has no translation (ivtest test_varray1, loud); the queue tests
  sv_queue_copy_empty1/2 and sv_queue_function1/2 abort in the iverilog core (`t-dll-api.cc:2494`,
  `word < net->array_words` in `ivl_signal_nex`), loud;
- constant drivers with strengths on the words of a memory of nets (ivtest pr1703346) translate (T25: a
  buffer per word and bit on `m(j)(b)`, resolved elements), and since the round-6 repair nvc's kernel net
  solver takes the element nets `m(j)(b)` (R6R-23; it declined them, and words of 2 or more bits read `xx`
  silently), but the run then stops at initialisation in `l3d_resolve` ("value -252 outside of LOGIC3D
  range") for every word width: loud, open in nvc (`l3d_resolve` receives a value outside LOGIC3D for these
  nets: `lib/sv2vhdl/resolver.c`, the kernel solver's strength resolution);
- a fused comb cone (`comb_fused_*`) whose first run comes before any input changed is never re-run by a
  `force` or `release` of a net it deposits (nvc's depositor bookkeeping, `rt/model.c`): T25 keeps such a net
  out of fusion; open in nvc;
- the file tasks (T26): the reading tasks (`$fgets`, `$fgetc`, `$ungetc`, `$fscanf`, `$sscanf`, `$fread`,
  `$fseek`, `$ftell`, `$rewind`, `$feof`, `$ferror`) are a translation error and `$readmempath` is dropped
  (located); end-of-step lines of several `$fstrobe`/`$fmonitor` statements come statement by statement (per
  instance, in source order), where vvp keeps call order (Verilog leaves it open); FD 2 (stderr) text comes
  out with simv's stdout; a partial stdout line (`$write` without a newline, `$fwrite(1, …)`) still pending
  when the run runs out of events without `$finish` is lost (the line buffer is flushed by `$finish`/`$stop`;
  files are flushed at the end of the run);
- waves (§8): the timing of `$dumpvars`, `$dumpoff`, `$dumpon`, `$dumpall` and simv's `+vcs+dumpon+`/`+vcs+dumpoff+`
  is not honoured (the VCD covers the whole run, a superset; each gets a note): that needs a translated call
  and an nvc hook that switches the dumper (FST has `fstWriterEmitDumpActive`); VCD names follow the
  translation (a reserved word gets `_sig`, `bus_sig`; an `output reg q` also shows its `q_reg`; generate
  blocks are flattened, so a `$dumpvars` scope inside one names nothing and nvc warns; several tops are
  `vamos_tops.top1`, `top2`, …); a `$dumpvars` scope naming another top-level module fails the translation
  (sv2vhdl-modules elaborates each top alone: "Unable to bind wire/reg/memory alpha in beta");
- real division by a real that is 0.0 (an unassigned real variable at time 0) stops nvc ("value -nan/inf
  outside of REAL range") where vvp gives inf, loudly;
- a port connection through a cast of the parent behind the core's port buffer (`sn_cast2`/`sn_cast4`
  shapes) is a translation error (T8), never a silently open port;
- a `tran`/`tranif` on a select of a module port is still a one-way copy (warned, T2); `sv_tran` does not
  drive a vector element, and ivtest `br_gh127c`, `br_gh127f` and `pr3197917` still fail;
- a weak unknown (`assign (weak0, weak1) w = d` with d = x) displays as `z`, where vvp displays `x`;
- integer `%t` values scaled down to coarser `$timeformat` units round where vvp truncates (kept on purpose);
- a gate primitive with a strength spec drives strong; the `$display` `-` flag is not parsed;
- `iverilog-sv2ghdl`'s least-referenced-module guess can pick the wrong `TOP_ENTITY` (vamos always passes
  `-s`, §1.4).

Fixed in phase 5 and removed from this list: `$time` truncating, the empty `$timeformat` suffix, undriven
`wire real` nets and real-port arithmetic (T11, T12); in the repair round, memories with a nonzero lower
bound (T21), signed `/` and `%` (T22), comparisons shown as `true`/`false` (T23), `task automatic` and the
silently ignored `disable`/`return` (T24), and the sv_math foreign functions (`$sqrt`, `$ln`, `$pow`, …,
which stopped the run: libsv_math.so is loaded now, §6). Fixed in round 6: a translated `always @(…)`
running once at time 0 and the mid-time-step yield behind `vhdl_test2`; `repeat (n)` after a blocking
assignment; a memory read or store outside the memory; SV `break`/`continue` and `$dist_*`; an automatic task
called from several processes; a named-block local read from another process (T25); `$random(seed)` and
`$urandom`'s sequences and `$urandom(seed)` (T16, P12); file I/O (T26); waves (P11, §8); a vector `reg`
copied from a weak net (T13, P12); a `tran` on a select of a net and arrays of switches (T2, P13); and,
found on the way, a 64K-iteration loop of blocking writes that hit nvc's 10000-delta limit (T25). Fixed in
the round-6 repair (the R6R rows): sv-normalize's Rule 6 width loss (R6R-11); `%m` in a module
instantiated more than once (R6R-07); a bit store outside a vector (R6R-02); the reserved-word top
(R6R-13); undriven bits of a partly driven vector net (R6R-08); nvc's vector-intrinsic crash (R6R-24); the
file tasks' `nvc/_norm.sv` locations (R6R-20) and `bin/vvp-sv2ghdl`'s missing `SV2VHDL_FILE_DIR` (R6R-14);
and, found by the merged stack's gate, constant selects ivl ignores (R6R-01), `$sformat` (R6R-04), bare
`$display` arguments (R6R-05), a monitor of a memory word's bit (R6R-06), `final` blocks run at time 0
(R6R-09, R6R-26), `$finish(0)` (R6R-10), an NBA in a depositing process (R6R-21) and the void-function
crash (R6R-27).

**P1 in detail.**
- nvc: `bool model_stopped(rt_model_t *m)` is true once `force_stop` is set (`std.env.stop`/finish, a failure
  report, a runtime fatal, `model_stop`, an interrupt); an empty event queue is no stop. `cosim_state_t` gains
  `int64_t last_acc_fs` (0) and `veto_fs` (-1).

```c
/* 0 = accept, 1 = veto (t_evt set), 2 = finish (accept this point and end) */
static int stopped_step(cosim_state_t *cs, rt_model_t *m, int64_t T, double *t_evt_s) {
   const int64_t tf = model_now(m, NULL), tol = 1 + T / 100000000000000LL;   /* 1 fs + 1e-14 rel */
   *t_evt_s = -1.0;
   if (T < tf - tol) { cs->last_acc_fs = T; return 0; }
   if (T > tf + tol && tf > cs->last_acc_fs + tol && cs->veto_fs != tf) {
      cs->veto_fs = tf; *t_evt_s = (double)tf / FS_PER_SEC; return 1; }
   cs->last_acc_fs = T; return 2;
}
```
  Called at `cosim_advance` entry (`if (model_stopped(m)) return stopped_step(...)`) and after every
  `model_step_to`; `last_acc_fs = T` on every return of 0, the digital-ahead branch included. A candidate that
  starts at or after `t_f` gets 2 again, never a veto to its own start. A `$finish` triggered by the first A2D
  sample has `t_f == last_acc_fs`, so `stopped_step` returns 2 and never vetoes. A finish lands one step past
  the stop when the digital stopped at or before the last accepted point (a stop at a step start, or Xyce at
  t=0), and on Xyce when the veto time is within its 2×minTimeStep veto guard; otherwise it lands exactly.
- Step results (bridge, `cosim_bridge_step`, `xyce_bridge_step`, `vacask_extsource_step`):
  `COSIM_STEP_ACCEPT` 0, `COSIM_STEP_VETO` 1 (`*t_evt` set), `COSIM_STEP_FINISH` 2 (`*t_evt = -1`: accept this
  point, then end the transient); the bridge and the engine glue pass 2 through.
- VACASK: `VACASK_EXTSRC_ACCEPT`/`VETO`/`FINISH` = 0/1/2 (any other nonzero answer with `tEvt >= 0` is still a
  veto); `static bool ExtSource::preAccept(tPrev, prev, t, cur, double& tEvt, bool& finish)`: a veto wins and
  `finish` is set only when nothing vetoed; `coretran` accepts the point and then yields Finished (the output
  is finalised), as a Verilog-A `$finish` does, including at the t=0 `ExtSource::preAccept` call
  (`coretran.cpp:1063` as patched, 1060 before; its result was discarded); a veto at t=0 is still ignored. A
  finish ends only the transient: later analyses in the control block would still run (vamos emits one).
- Xyce: `enum CosimStepResult { COSIM_ACCEPT = 0, COSIM_VETO = 1, COSIM_FINISH = 2 }` in
  `N_DEV_SourceData.h` (names distinct from the bridge's macros) and `int Device::cosimCandidateStep(double t,
  double &tEvt)`: every `code:` URI library exporting `xyce_bridge_step` is offered every step; VETO if any
  answered nonzero other than 2 with `tEvt >= 0` (the earliest `tEvt` wins), else FINISH if any answered 2,
  else ACCEPT (a nonzero answer other than 2 with `tEvt < 0` is an accept, as before; `tEvt` is written only for
  a veto). The stale `bool cosimCandidateStep` declaration in `N_ANP_Transient.C` is gone (the file includes
  `N_DEV_SourceData.h`; a stale bool declaration would read 2 as a veto with `tEvt=-1` and redo the step
  forever). In the transient loop a veto works as before; a finish accepts and outputs the step through
  `processSuccessfulStep()`, sets the step-error-control `finalTime` and `nextTime` to `currentTime`, prints
  `Co-simulation finish at <t> s.  Exiting transient loop` (6 significant digits; never starting `** `, so no
  end line, and simv drops it) and leaves the loop with success before the pause test, so `doFinish()`
  completes the output (`No. Points:` filled) and a finish on the very first step works. Afterwards
  `xyce_simulationComplete()` is true, `xyce_getTime()`/`getFinalTime()` return the finish time, and a further
  `simulateUntil` returns at once with success. A finish ends only the current `.STEP` iteration (vamos emits
  none).
- **ABI handshake:** `libcosim_bridge` exports `int cosim_bridge_abi(void)` (`COSIM_BRIDGE_ABI`, 2); VACASK's
  C interface `int vacask_cosim_abi(void)` (`VACASK_COSIM_ABI`, 2); libxyce.so `extern "C" int
  xyce_lib_cosim_abi(void)` (2), and the Xyce C interface's `int xyce_cosim_abi(void)` returns the loaded
  libxyce.so's `xyce_lib_cosim_abi()` through a weak reference – 1 when that library is unpatched, because the
  finish protocol lives in libxyce.so, not in the interface. nvc checks them with `dlsym` at co-simulation
  start and stops with `** Fatal: co-simulation ABI mismatch: <lib> does not export <sym>() (it predates the
  co-simulation finish protocol); <rebuild hint>` or `… has <sym>() = <n>, need 2 or later; …` (old engines
  read `(2, t_evt = -1)` as "accept" and keep going).
- **`cosim_run`** order of work: load the bridge and check its ABI; load the engine and check its ABI; parse
  the boundary file; `model_run_init`, then the t=0 settle (P2); resolve and register the boundaries with
  their ramps; install the stepper; initialise the engine (VACASK computes the operating point and offers
  t=0 here); then the stop check and the loop below (never re-enter the engine after a stop; the digital stop
  time is always the digital's own, never the engine's); clean up; after the "analog end" line the digital is
  stepped to the analog end time (pre-existing; normally nothing). The exit status is 130 for an interrupted
  run, else `model_exit_status`, or the loop's status if that is 0. The removed lines: "co-simulation
  complete", "VACASK simulation complete", "simulateUntil failed".

```c
if (model_stopped(m)) {                         /* stopped during the P2 settle or the t=0 offer */
   if ((interrupted = model_interrupted(m)))
      errorf("co-simulation interrupted at %s s", fs_text(tbuf, model_now(m, NULL)));
   else
      notef("co-simulation finished: digital stop at 0 s (before the first analog step)");
} else for (;;) {
   double r = t;
   int rc = eng.simulateUntil(eng.ptr, stop_time_s, &r);  t = r;
   if (model_stopped(m)) {
      fs_text(tbuf, model_now(m, NULL));        /* the digital's own fs count, exact */
      if ((interrupted = model_interrupted(m))) errorf("co-simulation interrupted at %s s", tbuf);
      else notef("co-simulation finished: digital stop at %s s", tbuf);
      break; }
   bool done = engine_complete(&eng);
   if (rc == 0 && !done) { errorf("%s transient failed at %s s", ename, time_text(tbuf, t));
                           status = EXIT_FAILURE; break; }
   if (done || t >= stop_time_s * (1 - 1e-12)) {
      notef("co-simulation finished: analog end at %s s", time_text(tbuf, t)); break; }
   if (t <= prev) { errorf("co-simulation stalled at %s s", time_text(tbuf, t)); status = EXIT_FAILURE; break; }
   prev = t;
}
if (interrupted) return COSIM_EXIT_INTERRUPTED;  /* 130, whatever the digital's status */
{ int drc = model_exit_status(m); return drc ? drc : status; }
```

`time_text` rounds an engine time to the fs clock and prints it as `fs_text` does (beyond the clock, about
9e3 s, or not finite: `%.15g`). Exactly one of these end lines is printed for a run that reaches the engine
loop; a run nvc refuses before it ends with a `** Fatal: …` line instead.

**Builds** (the development build: how it was rebuilt as each patch landed; the default branches have them
all, and docs/VAMOS_GUIDE.md §2 says how to build the stack afresh). Its trees are at vamos's default paths
(docs/VAMOS_GUIDE.md §2, §7). nvc: `make` in the nvc build tree (finish with `make -j1` if the sv2vhdl
library races; a stale-unit checksum error is fixed by deleting `lib/sv2vhdl/SV2VHDL.*` and `make -j1`; a
change to `sv_display_pkg.vhd` changes its checksum, so existing daidirs must be re-analysed).
`libcosim_bridge.so`: `c++ -O2 -shared -fPIC`, written to `.new` and then `mv`'d (a second, private nvc
build tree held the unpatched libraries, in `old/`, for the ABI tests). VACASK: `ninja sim vacaskcinterface`
in the VACASK build directory. Xyce, incrementally: compile `N_DEV_SourceData.C` and `N_ANP_Transient.C` with
`CMakeFiles/XyceLib.dir/flags.make`, link `libxyce.so` through `link.txt` as `libxyce.so.new` and move it into
place; the same for `N_CIR_XyceCInterface.C` and `libxycecinterface.so`; no Trilinos path redirect is needed.
Only those two XyceLib objects were recompiled after `N_DEV_SourceData.h` changed: other units that include it
are stale but compatible (the header gained an enum and function declarations, no class layout change).
`~/xyce-libs`, the first `VAMOS_XYCE_LIBS` default, holds symlinks into the build tree (its `libXyceLib.so`
link is stale and unused); the unpatched libraries are kept beside the patched ones as `*.pre-e2` (from
before work item E2, §10), the default inputs of the unpatched-Xyce test cases. iverilog: `make install` of
tgt-vhdl into the iverilog install tree (the installed `vhdl.tgt` is byte-identical to the in-tree build).
Phase 4 rebuilt nvc's `lib/libnvc.so`, `bin/nvc` and `lib/libcosim_bridge.so` by target (so the sv2vhdl
VHDL library was not re-analysed under running tests), in both nvc build trees; no VACASK or Xyce source
changed. Phase 5 rebuilt only `lib/sv2vhdl/libresolver.so` (P10). The phase-5 translator fixers each built
a private copy of tgt-vhdl from a common base and tested with `IVERILOG`/`VAMOS_IVERILOG` pointing at it;
their changes were then merged into the iverilog tree's `tgt-vhdl` with `git merge-file` (TA: `expr.cc`,
`scope.cc`, `stmt.cc`, `vhdl_syntax.{cc,hh}`; TB: `logic.cc`, `lpm.cc`, `scope.cc`, `state.cc`,
`vhdl_target.h`; TC: `expr.cc`, `logic.cc`, `process.cc`, `scope.cc`, `state.cc`, `stmt.cc`; no conflicts)
and installed; the installed plugin passes the 133 translator tests
(`test_translator_{ports,scripts,semantics,tgt,time}.py`) with `IVERILOG` unset. The repair round changed
the iverilog tree's `tgt-vhdl` itself (T21–T24: `expr.cc`, `lpm.cc`, `process.cc`, `scope.cc`, `stmt.cc`,
`support.{cc,hh}`, `vhdl_syntax.{cc,hh}`, `vhdl_target.h`) and ran `make && make install` (installed
`vhdl.tgt` md5 913a3fe2999fa8911ae4dc61ae39cf56); it rebuilt nothing in nvc, VACASK or Xyce. After the
upstream merge the iverilog `_install` and nvc-build were rebuilt from c9726d635 and 399d83df8. Round 6: each
fixer built private copies (an iverilog tree with its own plugin directory, an nvc build) and tested with
`VAMOS_IVERILOG`/`VAMOS_NVC` and `IVL_BUILD_LIB` pointing at them – without `IVL_BUILD_LIB`,
`bin/sv2vhdl-modules` fell back to the shared `_install/lib/ivl` and passed its `-B`, which overrode a
private wrapper's, so stage 1a silently used the shared plugin (R6R-15 fixed that). The merged patches were
then applied to the shared working trees; tgt-vhdl was rebuilt and installed (`make -C tgt-vhdl && make -C
tgt-vhdl install`); nvc-build was rebuilt (`make`, which stops on the sv2vhdl library's stale checksum, then
`rm lib/sv2vhdl/SV2VHDL.*` and `make -j1`: existing daidirs must be re-analysed); `libcosim_bridge.so` was
not (P11 does not touch `cosim_bridge.cpp`). P14 needs the engines rebuilt: VACASK's `ninja` in its build
directory (`devices/spice/{diode,mos3}.osdi`, staged in `lib/vacask/mod/spice`), and in the Xyce build the
`N_DEV_Diode`, `N_DEV_MOSFET3` and `N_DEV_RegisterOpenDevices` objects recompiled and `libxyce.so` relinked.
The round-6 repair rebuilt the plugin and nvc-build again (`make -C tgt-vhdl && make -C tgt-vhdl install`;
`make bin/nvc` in nvc-build). Regression runs
must use a private ivtest copy (`SV2GHDL_SRC_ROOT`, below): `vvp_reg.pl` works inside `ivtest/` (`./vsim`,
`log/`), so concurrent runs in the shared one corrupt each other's nvc WORK library (since round 6 the
harness gives every ivtest block a private work area of its own, §9).

## 8. vcs personality integration

- **Option table additions.** The Option column is the literal `Opt` name: `eq` names exclude the `=`,
  `prefix`/`plus` names include their trailing `=`/`+`. `optable.Opt` asserts the `eq` rule (phase 0); the
  `prefix`/`plus` spelling is a convention.

  | Option | Kind | Disposition |
  |---|---|---|
  | `-ad` | flag | mapped (`ams_control = ""`, i.e. `vcsAD.init`) |
  | `-ad` | eq | mapped (`ams_control = <file>`, made absolute) |
  | `+ad` | flag | mapped |
  | `+ad=` | prefix | mapped |
  | `-override_timescale` | eq | mapped (§1.2; `Job.override_timescale`) |
  | `-ams` | flag | unsupported (Verilog-AMS flow) |
  | `-ams_discipline` | next | noted |
  | `-ams_dresolution`, `-ams_iereport`, `-ad_iereport` | flag | noted (the IE report is always written) |
  | `-realport` | flag | noted |
  | `-adopt` | next | noted |
  | `-xlrm` | next | noted |
  | `-wreal` | next | noted |
  | `+verilogamsext+` | plus | noted |
  | `-sysc` | eq | unsupported |
  | `+msvsdf`, `+msvsdfext` | flag | noted |
  | `+bidir+` | prefix | noted |
  | `+print+bidir+warn` | flag | noted |
  | `+plusarg_save` | flag | unsupported (runtime options are not compiled into simv) |
  | `+plusarg_ignore` | flag | noted |

  - Never register `-ad` as a prefix: it would swallow `-adopt` and `-ad_*`.
  - `.va`/`.vams` sources on the command line are the Verilog-AMS flow: unsupported (Verilog-A goes into the
    SPICE netlist with `.hdl`).
  - A bare top name (the three-step flow) is taken as a source file and fails as one that cannot be opened.
  - `-top` is repeatable and accepts VCS's `-top a+b+` form (§1.4). `-gui` is noted: "no GUI ($dumpvars
    writes a VCD for a wave viewer)", and `-debug_access…`, `-debug…` and `+vcs+vcdpluson` "no debug database
    or VPD/FSDB waves are produced; $dumpvars writes a VCD" (round 6; they said waves were planned).
    Compile-time `+vcs+dumpvars` is mapped (Waves, below). Compile-time `+plusargs` reach a `-R` run (not
    listed as unmapped); without `-R` each gets the note "a plusarg given to vcs reaches only a -R run, as
    under VCS; give it to ./simv".
    `--vamos-*` tokens inside `-f`/`-F` files are unsupported ("vamos options are read from the command line
    only, …").
- **Plain compiles** (no `-ad`; `vcs.plain_compile`): the preprocess into `<daidir>/pp/pp.v`
  (`verilog_ports.preprocess`: the precision for `+vcs+finish+`, the `-override_timescale` rewrite; a
  failure is a compile error); the `-v` library rule (`vcs.library_rule`, which delegates to
  `verilog_ports.library_rule`, §1.2); the tops (§1.4); the translation of `pp/pp.v` (always, not only with
  `-override_timescale`); the deferred-top check (T4, §7); the translator's warnings as vamos warnings at the
  user's file:line (`verilog_ports.translator_warnings`, T4; also when the translation fails); the
  untranslated-task/function warnings (§1.3), the VCD tasks recorded for ./simv's waves instead (Waves,
  below); since the round-6 repair, warnings for the timing the simulation leaves out
  (`verilog_ports.timing_omissions`: "specify path delays are not simulated: every module path has zero
  delay (<n> specify blocks with path delays; VCS applies them unless given +nospecify)", "timing checks are
  not run (<checks>; <n> in the design; VCS runs them unless given +notimingcheck)", "$sdf_annotate is not
  simulated: the SDF file's delays are not applied"; the noted `+nospecify` silences the first two,
  `+notimingcheck` the second; an AMS compile gives them too, leaving out the cells SPICE replaces) and, with
  one top, for its input ports ("top-level module <m> has input port(s) <p> that nothing drives: they read 0
  in this simulation, where VCS leaves them undriven (z)", `vcs.undriven_top_inputs`); the elaboration (of
  the entity the translation made of the top, `vcs.top_entity`, which differs from the module's name for a
  VHDL reserved word, R6R-13; of `vamos_tops` with several tops).
  `--vamos-strict` makes every warning of a plain compile an error. A compile writes the refusing stub first
  and the job record and real stub last (`vcs.invalidate`, §1.1).
- **`simv`:** `-ad_runopt` (eq) is noted ("analog run options are not passed to the engine").
  `+vcs+finish+<time>` is mapped, in digital and AMS runs, to nvc's `--stop-time`. Forms (VCS User Guide,
  "Options for Specifying When Simulation Stops"):
  - N units of the compiled precision; each unit is min(precision, 1 ms) of nvc time, because tgt-vhdl
    translates one tick of a precision coarser than 1 ms as 1 ms (§1.2) and every finer one at its SI size;
  - N<unit> (fs, ps, ns, us, ms, s; case-insensitive; decimals allowed): an absolute time, converted to ticks
    of the precision first (`+vcs+finish+9001us`; `6s` at a 1 s precision is 6 ticks of 1 ms);
  - `<low>+<high>`: VCS's two-argument form, high × 2^32 + low units;
  - `0` stops at time 0.

  A value in none of these forms is "unknown option +vcs+finish+<v> ignored (not a time value: …)"; no
  recorded precision, or a time beyond TIME'HIGH, is "… is not supported yet (…)"; both fail
  `--vamos-strict`. In plain mode the run footer shows the time nvc reports the run ended at (round 6:
  `NVC_REPORT_END_TIME`, P11, read by `OutputFilter` and `nvc.end_time`): the time the design ended itself
  (`$finish`, `$stop` or a fatal report), the `+vcs+finish+` time of a run that still had events (`Time:
  22ns`), or the last event of a run that ran out of events first (`+vcs+finish+100000` on a run whose last
  event is at 10 ns gives `Time: 10ns`; before round 6 it showed the stop time, and a run with no reports
  showed 0). Without that line (nvc killed, or an nvc that predates P11) the earlier rules apply: the design's
  end (nvc's `FINISH/STOP called` note counts only with its `std.env` trace line, so a `$display("FINISH
  called")` does not), else the stop time of a bounded run that ended cleanly, else the last report's time.
  Times are normalised (`0`, never `0ms`), so AMS and digital footers match; an AMS run's footer follows §6.
  A plain run's nvc works in the directory simv was started in (T26, §6). An AMS job's run goes through
  `backends/cosim.py`.
  The mapped runtime options include, since round 6, `+vcs+dumpfile+<file>` and `-vcd <file>` (the VCD file
  when the design's `$dumpfile` names none: "A $dumpfile system task in the Verilog source code overrides
  this") and `+vcs+dumparrays` (memories in the VCD too, nvc `--dump-arrays`); `+vcs+dumpon+…` and
  `+vcs+dumpoff+…` are noted ("not honoured: the VCD records the whole run"), and so is `+vcs+flush+dump`
  ("the VCD is written when the run ends").
  `-h`, `-help` and `--help` print the runtime options vamos acts on, the noted, ignored and unsupported
  ones, plusargs, the vamos options and the guide's path, and exit 0 before the daidir is read. The
  `-assert` and `+notimingcheck` notes give their reason ("assertion run-time controls are not mapped yet
  (vamos prints no assertion summary, so there is none to suppress)", "timing checks are not modelled, so
  there are none to disable"). simv's `--vamos-strict` error has the compile's wording ("--vamos-strict: <n>
  unsupported/unknown option(s): …"). An option missing its value (`./simv -l`) is an error, not a
  traceback. `run_daidir` reports a `JobVersionError` or `TypeError` as "recompile" (§1.1). The provenance
  header adds the analog engine's tools as the compile recorded them.
- **Waves** (round 6; `vcs.dump_request`, `vcs._Conditions`, `Job.dump`, `simv.wave_request`,
  `backends/nvc.Waves`; nvc P11). tgt-vhdl leaves every VCD task out with its located comment, which stays. A
  plain compile, and since the round-6 repair an AMS one (`ams/flow.py` on its own translation, R6R-16),
  reads the calls from those comments – so only what the translation kept: a generate branch not taken has
  none – takes their arguments and the plusarg tests they run under from `pp`, and records them
  in `Job.dump` (`{"calls": [{"scopes": [[levels, nvc path], …], "origin", "if"}], "files": [{"name" or
  "plusarg", "origin", "if"}]}`; `"if"` holds `[plusarg, present]` tests). The notes replace the "not
  translated" warnings (§1.3): "<file>:<line>: $dumpvars: ./simv writes a VCD of tb (every level) to
  counter.vcd, from time 0 for the whole run" (with "when ./simv gets +waves" for a guarded call); `$dumpoff`
  and `$dumpon` "is not honoured: the VCD records the whole run", `$dumpall` "has no effect: the VCD records
  every change", `$dumpflush` "has no effect: the VCD is written when the run ends", `$dumplimit` "is not
  honoured: the VCD has no size limit"; the extended-VCD `$dumpports` family keeps its warning.
  - Conditions (`_Conditions`, a structural walk of each `initial`/`always`/`final` statement through
    blocks, `if`/`else`, `case`, loops and delay, event and wait controls): a call under `if
    ($test$plusargs("x"))`, its `else` branch or a `!`, and `if ($value$plusargs("x=%s", f)) $dumpfile(f)`,
    count only when ./simv's plusargs pass the test (a prefix match, IEEE 1800-2017 21.6); a call under any
    other condition (an `if`, a `case`, a loop, a task or function) gets the note "<task>: the call is inside
    <condition>, which vamos cannot evaluate before the run: ./simv acts as if it runs" and counts as made.
  - Arguments: `$dumpvars` with no arguments, or level 0 and no scope, dumps the whole design; `$dumpvars(n,
    s…)` gives nvc `--dump-scope=<path>,<n>` per scope (instance or variable names; a name relative to a module
    applies to each of its instances, `_instance_paths`; a module instantiated only in an instance array
    cannot be named: a note, and the whole design is dumped); a level that is not a number dumps every level
    and a `$dumpfile` name that is not a string literal is ignored, each with a note; `$dumpfile` without
    `$dumpvars` writes nothing (a note).
  - The file: the first `$dumpfile` that counts (a literal, or the value of the plusarg a `$value$plusargs`
    test read it from), else simv's `+vcs+dumpfile+<file>` or `-vcd <file>`, else `verilog.dump` (VCS's
    default; IEEE 1364's tools write `dump.vcd`), relative to where ./simv runs. A path nvc could not write
    (no such directory, a directory, not writable) is the warning "vamos: warning: the run writes no waves:
    <path> cannot be written (<why>)" and the run goes on without waves, as VCS does (nvc would stop before
    simulating).
  - Compile-time `+vcs+dumpvars` ("a substitute for entering the $dumpvars system task, without arguments, in
    your Verilog code") dumps the whole design ("+vcs+dumpvars: ./simv writes a VCD of the whole design to
    verilog.dump (./simv +vcs+dumpfile+<file> names another), …"), in vcs-ams too. Beside a `$dumpvars` in
    the source, the note at that call labels the option's part ("… a VCD of the whole design
    (+vcs+dumpvars); tb.dut (1 level) when ./simv gets +waves to …", R6R-19).
  - AMS (round-6 repair, R6R-16): the in-source VCD tasks are read as in a plain compile (they were refused,
    §1.3), and a cut output that no digital code reads keeps its A2D when the dump record covers its net
    (`vcs.dump_covers`, `cut.assign_roles(dumped=…)`, §5.4), so the VCD shows its digital value, as VCS
    does, not z.
  - nvc gets `--wave=<abs path> --format=vcd [--dump-scope=<path>,<n> …] --exclude=*_ivl_* [--dump-arrays]`
    after `-r` (`Waves.args`): four-state values, modules as module scopes, none of the translator's own nets
    or the scopes they leave empty, from time 0 for the whole run, with nvc's `$timescale 1fs`. Names follow
    the translation (§7 list). nvc's note that arrays of composite types are left out is shown in simv's terms
    (§6, Output filtering). Checked against vvp's own VCD of a test design: all 24 signals have identical
    value changes, past the 1000 ns fast-clock and fused-block switch-over (`test_r6_W.py`).
- **Personality:** `vcs-ams` (`vamos -vcs-ams …`, and the launcher `shims/vcs-ams` or any `vcs-ams` symlink to
  vamos: the personality is the invoked name) is the vcs personality with `ams_control` defaulting to `""`
  (`vcsAD.init`).
- **vamos options** (`--vamos-<key>[=<value>]`, removed before the personality sees the command line):
  `--vamos-analog=vacask|xyce` (env `VAMOS_ANALOG`; another value is an error); `--vamos-analog-stop=<time>`
  (above 9000 s: clamped, warning; beside a `.tran` it has no effect, a warning from
  `vcs._check_ams_options`); `--vamos-analog-maxstep=<time>` (also replaces a parsed `.tran`'s maxstep);
  `--vamos-parhier=local|global`; `--vamos-no-deck-check`; `--vamos-keep` (keep the per-run directory);
  `--vamos-strict` (unsupported, unknown and ineffective options, and every warning of a compile, become
  errors); `--vamos-append-log` (simv: with `-l`, append; `vcs -R` uses it). Times parse with
  `numbers.parse_number`. vamos owns this namespace: `optable.VAMOS_OPTIONS` lists every key the code reads
  (a test greps `vamos/` to keep it complete), and `optable.check_vamos_opts`, first thing in `cli.main`,
  makes an unknown key (with the nearest one: "unknown vamos option --vamos-analgo=xyce (did you mean
  --vamos-analog?)"; a planned key is named as planned), a value on a flag ("--vamos-strict takes no value"),
  a missing or empty value ("--vamos-analog needs a value: …") and a value outside its choices a usage error,
  exit 2. `optable.vamos_option_effects` gives the options that have no effect where they were given (an AMS
  option in a digital compile, `--vamos-keep` without `-R`, a compile-time option given to `./simv`, any
  option given to a pass-through tool): an `INAPPLICABLE` note in a compile, a warning from cli for simv and
  the pass-through, "vamos: warning: <opt> has no effect: <why>"; `--vamos-strict` fails on them (exit 1).
- **Tools and help.** `VAMOS_<TOOL>` overrides of the digital tools are checked (`tools.checked_override`):
  a value with a `/` must be an executable file (made absolute), a bare name is looked up on the scrubbed
  PATH; otherwise `tools.ToolError`, which vcs.main and cli.main turn into exit 1 with no fallback:
  "VAMOS_NVC=<p>: no such file (it must name the real nvc)", "… is a directory", "… is not executable", "…
  is vamos itself", "no executable '<x>' on PATH". Versions come from `tools.version_text`: the first line's
  dotted version, else a date-like build number (OpenVAF-r: `20260616-3-g0e83f1ed`), else `?` (error or usage
  text, which used to fill the column). The provenance and licence columns are as wide as their widest
  entry. The `-h` texts of `vamos`, `vcs` and `vcs-ams` (each its own usage line) are generated from
  `VAMOS_OPTIONS`, list `-ad[=<file>]`, `+ad[=<file>]`, `-top <mod>[+<mod>...]` and
  `-override_timescale`, and end with the absolute path of `docs/VAMOS_GUIDE.md`. The "up to date" line names
  the executable relative to the cwd (`Vamos: ./sim/tb_simv is up to date`).
- **Licences:** `licenses.json` lists VACASK (AGPL-3.0-only), OpenVAF-r (GPL-3.0-only) and Xyce
  (GPL-3.0-or-later); `--vamos-licenses` prints them. `url` is the source the stack runs (the kev-cam forks
  of nvc, iverilog, VACASK and Xyce, as each repository's `git remote -v` shows; OpenVAF-r is installed from
  its release tarball, no fork) and `upstream` the project a fork comes from (printed "(a fork of
  <upstream>)"); the `_comment` fields of `licenses.json` and `banners/vcs.json` list all five configuration
  layers (the package, the site layer `etc/vamos/`, `~/.config/vamos/`, `~/.vamos/`, `./.vamos/`).

## 9. Tests

Everything below exists. The suite (`python3 -m unittest discover -s tests/vamos -p 'test_*.py'`) has 1667
tests in 51 files: the 1375 of the upstream merge, round 6's six `test_r6_*.py` files (239), the merge's own
`test_r6_merge.py` (3), the round-6 repair's `test_r6_R.py` (48) and two more in files the fixers extended.
After the round-6 repair all 1667 pass under WSL Python 3.14.4 (1255 s; 2 skips, the two
ISO-8859-1-locale cases) and under Cygwin Python 3.9.16 (921 platform skips). On the merged round-6 stack
before the repair, under WSL Python 3.14.4, all 1619 then in the suite passed but the two stale
`TestE2E03Dac` expectations of the round-6 record below (3 skips, about 20 minutes), and under Cygwin Python
3.9.16 the 1616 tests before `test_r6_merge.py` were OK (879 platform skips). At revision 5 it had 1360
tests in 43 files (1296 before the repair round, whose four `test_repair_*` files add 64), all OK under WSL
Python 3.14.4 (2 skips: the `--old-bridge`/`--old-vacask` C-side cases) and under Cygwin Python 3.9.16 (704
platform skips). After fix round 2 it had 905, all OK under WSL Python 3.14.4 (4 skips: two opt-in C-side
cases that need `--old-bridge`/`--old-vacask`, and the two `TestE2EProbeReason` classes, which run since
flow.py passes the probe directions) and under Cygwin Python 3.9.16 (455 platform skips). In phases 4 and 5,
and again in round 6 and its repair, every fixer ran its own suites and every AMS e2e suite on both engines –
with its private translator where it had one – and checked that its new tests fail without its fix (on the
original files, a reverted copy of the tree, or the pristine plugin); the new files run under Cygwin Python
3.9 too, where the stack tests skip. Each fix's evidence is in §11 (round 6's in §7 and below).

**Unit** (fixtures under `tests/vamos/fixtures/{netlist,vhdl,ams}`; helpers in `tests/vamos/vamos_testlib.py`,
which test modules import instead of each other; stack tests skip without nvc, iverilog, VACASK or Xyce):
- `test_ams_contract.py` (24): names, globs, bridges and boundary lines, port maps, the Job record (round trip
  with and without `ams`, a newer daidir says "recompile", a phase-0 daidir without `schema` still loads),
  numbers, the IE report's Top-Net and `snps_open` lines, the deck's code sources on both engines, the Xyce
  chatter filter (wrapped warnings, long names, other nodes kept, interrupted messages printed in order), the
  option-name assertion.
- `test_ams_initfile.py` (35): x-heep `control.init` verbatim; a `port_dir` without spaces or a final `;`; the
  PAMS p180 multi-line `port_dir`; an unterminated `(` with recovery; ports named like keywords; a
  `netlist_commands` block with `$` and `;` comments that comes back as N lines with origins; `xa_commands`;
  every `` `include `` form and error; `find_ini` and ini-first order; every command form and disposition
  class; choose arity (`-spice` never eats a netlist; `-wavefmt wdb` does not become one); XA cfg mining.
- `test_ams_rules.py` (33): VCS globs (`[3]` literal); per-bit rules in `[i]`, `<i>`, `_i` and bare-bus
  spellings; `port=` in three spellings; `except_port`; the `d2a powernet node=top.vdd` alias;
  `node=top.s[0]` on a cut output connected by bit-select; a part-select input (`code[3:2]`); a `vdd=` naming
  a Verilog-net alias; `vdd_port` with `../`; last-wins merging (PAMS p176); TNF with nearest names,
  `downgrade_to_warn`/`upgrade_to_error`, the role filter, DISABLED/REMOVED marking; the xband window; the 3.3
  V warning only when used; every report line pasted back selects the same node with the same levels and no
  TNF.
- `test_ams_rules_fixes.py` (17, phase 5): the IE report written as UTF-8 under an ISO-8859-1 locale, and an
  unwritable report as `ReportError` with no `.tmp`; the report's control lines equal `rules.ie_lines` (one
  writer); supply nets' `d2a powernet` lines; every line kind pasted back resolves to identical IEs under
  another reference, with no notes and no TNF; `midv_logic=Z` gives `MIDV_L` 3; `map_by_node` forms, errors,
  last-wins, role filter, TNF and the ideal-source warning; XA cfg `$VAR`/`${VAR}`/`~` expansion and the
  unset-variable error; absolute levels beside `vdd=`/`vss=`/`vdd_port=` rejected; a wildcard `vdd_port=`
  after `../` rejected (a plain one parses and is matched against the subckt ports: `test_repair_units`).
- `test_ams_deck_fixes.py` (48, phase 5, runs anywhere): `port_connect -inst` (mixed, reversed order, the
  in-group form, `-inst` only, glob, uncovered, last-wins notes, the two multi-view cases); `port_connect` net
  resolution and its errors, the ground-alias port, the floating-net warning; `deck.range_params`;
  `ie_reference_voltage` (a regulator output, last wins, unreached entries, dynamic references) and the
  supply graph's stops; unevaluable supply sources; v() saves, port aliases and XA probe patterns; engine
  overrides (`engines.problems`); the maxstep and clamp notes; the variable warning; `flow.left_out_cells`.
- `test_ams_verilog.py` (35) and `test_ams_shells.py` (36): the lexer and design-unit scanner, the
  `` `line `` origin map (real `ivlpp -L` output), the time rules (compilation unit, coarse precision, unused
  `-v` modules, the override rewrite, a design with no `` `timescale `` using `const time PH=5ns`, the 1 s/1 s
  error, the `-timescale` prelude), the API scan, unsupported tasks, declarations, instantiations, roots and
  top, headers (ANSI/non-ANSI, `#(parameter …)`, `[W-1:0]`, refusals), masking shapes (a cell reached only
  through `` `include ``; a cell in a `-v` file; a `` `define `` inside the masked module used later and
  tested by a later `` `ifdef ``; two alternative definitions under `` `ifdef ``/`` `else ``; compiled and run
  with vvp); bus grouping and `port_index_order`, bindings, shell golden text and generate-loop markers; the
  cell set (generate-false, masked bodies, not-instantiated notes, unknown module, SPICE-top); the direction
  probe (reg → input, read-only logic → output, nets → inout; one SPICE-only cell instantiated twice with
  different needs gives the per-cell error naming both); wrapper inputs (step 2b, and its conflict error);
  parameter overrides (`#(5)`, `#(.W())`, `defparam`); bus auto ports with offset and ascending ranges; a
  translation check that markers give `inout resolved_logic3d` and keep every output connection; wrapper
  inputs that need the T8 buffers and the cut's one-way join onto step 2b's node, and an auto port behind a
  user-typed variable (phase 4); `TestDocs` (phase 4): every call in shells.py's usage sample matches the
  real signature and a call flow.py makes, the port-buffer wording, and `fixtures/vhdl/README.cut` lists every
  `cut_<case>` fixture.
- `test_ams_vhdl.py` (27; the upstream merge added the new reserved words and a merged process's origin)
  and `test_ams_cut.py` (81), on `design.vhd` fixtures captured before the translator patches (only
  `cut_portbuf` is post-T8; `cut_regen.sh` regenerates them in WSL; the `tb.v` shells are hand-written): reserved-word ports, cells named buffer/block/register, `my__cell`, `cell_`,
  `_cell`; a dac N=4/N=8 pair (two variants); a shared parent; the `_Readable` shapes a/b/c/e and bandgap→ADC;
  `comb_fused`; port temporaries; primitive drivers and strengths; supplies; vectors through an instance
  array, a generate loop, plain instances and a mixed vector; a forced cut net; initial-block deposits; real
  ports; BIDIR pull moves; `tri1`/`tri0` (the pre-T5 error case); T2 aliases (the hand-edited
  `cut_swvp/design_t2.vhd`) and the SW temporary guard; T3 paths (the hand-edited
  `cut_vec/design_t3.vhd`); per-bit Z-only drivers and initializers (with over-reach guards: a conditional,
  delayed or cyclic Z temporary still drives); port buffers (variable, bit select, expression, vector,
  inside reader, output, inside driver: `cut_portbuf`); probe directions; `remove_d2a`/`disable_ie`; one and two `use_spice -inst` statements; a
  ground port by position; emit (shared tables, clone, boundary, 600-instance scale); `nvc -a`/`-e` of
  `cut.vhd` for every fixture; an A2D xband/midv window test through an external name; two real nvc+VACASK
  co-simulations (a shared parent with per-path levels and roles; a bidirectional pad: 1.532/0/1.756 V). The
  `sv2vhdl_modes` table is regenerated and compared. The real T2/T3/T5 output is exercised end to end by e2e
  12, 13 and 28. Phase 4 added the D2A template's `x2v=4` in nvc (start-up, X after 0 and after 1, a BIDIR
  without a strong driver) and a cell port named `v`; real, string and integer parameter overrides read from
  `_mods.vhd`, with localparams and derived values not counted; phase 5 the new variable-warning text.
- `test_ams_supply.py` (19): traces on hand-built IR (a D2A into a gate reaching the cell supply, an A2D
  channel region, highest constant, no supply, a dynamic source with its warning, POWERNET and `skip_node`, a
  subckt-parameter supply, a varying `d2a powernet`), driver levels, and the deck's §3.3 order (a supply0 net
  without the fallback, levels that still warn, Method #2a constant and varying, a plain d2a rule is no
  supply).
- `test_ams_options.py` (9): `-ad`, `-ad=f`, `+ad`, `+ad=f` set `ams_control`; no `-ad` is digital; the
  `vcs-ams` personality implies `-ad`; `-adopt x` consumes x; `-ad_iereport` does not enable AMS; `-sysc=ams`
  is unsupported, not unknown; `-ams_discipline logic` is not a source; `-override_timescale` is mapped; simv
  `-ad_runopt=x` is noted; `+vcs+finish+N` and precision parsing.
- `test_netlist_expr.py` (32): the parser, inspection, the evaluator, both printers in both contexts, the
  engine rows (VACASK and Xyce, parameter and behavioral, with negative, zero, non-integer and > 2^31
  arguments), the Xyce reserved names through `param_ident`, a cross-engine parameter deck (VACASK == Xyce ==
  HSPICE within 1e-12), HSPICE's D exponent (phase 4).
- `test_netlist_rawfile.py` (21): real VACASK and Xyce fixtures (binary, ASCII, AC, op) with the decks that
  produced them, regenerated in WSL; the point-count repair (blank, wrong, missing, a truncated last point).
- `test_netlist_spice.py` (80): file handling (x-heep layout, cwd-relative; a sky130-shaped library tree,
  file-relative; the real sky130 tt corner in WSL; a netlist ending in `.end` followed by `netlist_commands`
  `.temp`; two netlists including one model file; a choose netlist beside its control file; fragment
  references beside theirs); parameter rules (duplicates, PARHIER collision, out-of-order chain, cycles); the
  ground pass (x-heep `adc.sp` verbatim; PAMS p27 `vgnd gnd 0 dc 0` with `.global vdd gnd`; PAMS p349 `vsu vdd
  gnd 3.3` with no tie, which must stay aliased; collapsed E/H/F/G/D/K cases); options (`scale` kept unscaled,
  `tnom`/`temp` defaults independent, the maximum step, RMAX ordering, `delmax` errors and the no-`.tran`
  note, `.option dcap`); sources (every omitted/zero field); D exponents in element values (phase 4).
- `test_netlist_emit.py` (69): tables (dispatch rows and refused levels, polarity, level and version, strip
  warnings, bounds, scale and multiplier, bin guard and selection at the edge, sources, deck elements); golden
  text on both engines, every golden actually run (Xyce goldens as plain `Xyce deck.cir`); quoting; no
  `$mfactor` on `b` or `mutual` lines; the explicit Xyce multiplier; dependent subckt parameters (`w`,
  `l=2*w`, `r=l*1000` overridden four ways); binning (native, explicit for `nf=2`, errors); the gated
  conductance's polarity; a floating gate with and without its shunt; smoke checks passing and failing
  (unknown parameter, no-bin mapping); `hspice_card` (MOS 1/2/3, BSIM3, D/Q/J, CAPOP and `--vamos-strict`)
  and the coupled-inductor fold, with regenerated goldens (phase 4).
- `test_netlist_integ.py` (34): HSPICE text through `spice.parse` into both emitters and both engines for
  every parser fixture, x-heep `adc.sp` (crossings at two mux settings), a sky130 tt inverter, Verilog-A,
  element currents, source waveforms, nested subckts, `temper` and `.ic`; the §9 control deck (`.tran 1n
  'tsim'`, `.temp tt`, `.ic v(b)='vh'`: every VACASK control-block value and the Xyce `.options device temp=`
  value an evaluated float, run on both); e2e 21, 22, 23 and 25 standalone (below); the maximum-step rule
  (the item-6 inverter within 1.5 % of the closed form, engines within 0.3 %); `TestHspiceDefaults` (phase 4:
  cross-engine checks against HSPICE's numbers for MOS 1/2/3 cards, the manual's LEVEL 3 example, DCAP=2
  diode, BJT and JFET values) and `TestCrossEngine.test_mfactor` with a transformer under `m=2`.
- `test_cside_engine.py` (92, from `fixtures/ams/cside_engine/run_cside.py`) and `test_cside_xyce.py` (13,
  from `fixtures/ams/cside_xyce/run_cside_xyce.py`): C-side, below.
- `test_translator_scripts.py` (7), `test_translator_tgt.py` (39), and from phase 5 `test_translator_time.py`
  (21), `test_translator_ports.py` (36) and `test_translator_semantics.py` (33): translator, below; and
  `test_random_vhdl.py` (13, from the push): sv_math_pkg's plain-VHDL `random` with no `--load`ed library,
  bit-exact with vvp (1000 draws, six seeds, the cast edges, a million draws in about 0.2 s), through plain
  nvc, `bin/vvp-sv2ghdl`, `vcs` and `vcs-ams` on each engine; `$urandom` and `$urandom_range` against vvp's
  own rows (round 6: vvp's scaling on `$urandom`'s own generator, no longer the old `lo + draw mod span`).
- `test_vamos.py` (30): the phase-0 vamos tests, unchanged in substance.
- `test_vamos_driver.py` (51, phase 5; 36 run anywhere): the refusing stub and the order of a compile (a
  failed recompile never runs a half-rebuilt daidir, also on both engines; a failed first compile leaves no
  `./simv`); the `--vamos-*` namespace (unknown, missing value, value on a flag, choices, the ineffective
  options, `-f` files, the help texts); several tops and the `vamos_tops` wrapper, `-top a+b+`, a misspelt
  `-top`; the deferred-top error with iverilog's reason; untranslated tasks and functions as warnings; the
  `-v` library rule; tool overrides and version parsing; compile messages (`-gui`, plusargs, `{exe}`); a
  copied or moved simv + daidir pair; the licence table's forks and layers.
- `test_vamos_run.py` (48, phase 5): the end-of-run classifier (testbench text never classifies a run; every
  real tool-line format does, coloured too); the chatter filter by line source; `remap_exit` and the exact
  SIMULATION FINISHED line; the design-end footer; raw-line splitting; signal relay, the grace kill, nohup,
  the process group and stdin, Ctrl-Z, parent death; the `+vcs+finish+` forms; simv `-h`, the strict wording,
  the notes, the job-version message, the footer, ending with the signal; `check_raw` against `rawfile` on
  every fixture shape plus a constant-memory bound; `tran_start`; the TSTART, 2 fs and no-output rules; the ABI
  check on synthetic ELF files and against nm; plain simv (stack).
- The repair round's files, each test failing on the pre-round code or plugin except those that pin
  unchanged behaviour: `test_repair_translator.py` (13: memories at any base, signed `/` and `%`, comparisons
  shown, `disable`, automatic tasks, and `bin/vvp-sv2ghdl`'s plugins, resolver and trace filter, each compared
  with vvp); `test_repair_e2e.py` (11: plain `$sqrt`/`$ln`/`$pow`/`$rtoi`, the function trace, translator
  warnings and `--vamos-strict`, the banner order, simv's failed-compile message; in AMS the `-v` rule, a
  non-ASCII title under `LC_ALL=C`, the record's `start`, the IE report stage, a copied VACASK daidir whose
  original is gone); `test_repair_units.py` (29: reap, banner order, plugins, trace filter, translator
  warnings, the `-v` rule, deck encoding, cut `-cell` subckt, the unevaluable-supply wording, wildcard
  `vdd_port`, ground supply, header function, cut-table miss, IE report stage, simv record);
  `test_repair_netlist.py` (11: Xyce's CJO=0 diode, VACASK's kappa=0, MOS MJSW/CJ, the first two also run on
  the engines). `vamos_testlib.TempDir.tearDown` now kills processes left running in the scratch directory
  (`reap`), so a failed signal test no longer leaves nvc running.
- Round 6's files, one per fixer, each test failing on the merged base stack (or with the fixer's change
  reverted) except those that pin unchanged behaviour, with the fixer's count of such guards; most need the
  stack and skip under Cygwin:
  - `test_r6_T.py` (39, 8 guards; T25): blocking assignments do not yield (`vhdl_test2`'s deterministic
    variant: a VHDL dut sees the whole time step), a clock with two delays, shadows that see other writers, a
    net not updated in mid-process (sched2), a long activation, `$readmem` after blocking writes, an initial
    block that ends once (pr710), time-zero reads that settle (pr307a), force/release through a net chain
    (pr2849783, pr3368642), the time-zero order and its all-or-none rule (the vhdl_loop shape), `repeat (n)`,
    `break`/`continue`, `$dist_*`, an automatic task from three overlapping processes, the located recursion,
    `disable fork` and conditional-`$dist_*` errors, a shared block local, memories read and written out of
    range, memory-word select targets (t_membit5 and a compressed form), Hazard3's
    `hazard3_onehot_priority_dynamic` through vamos (needs the verilator-hazard3-mandelbrot-testbench
    checkout), `$set_val` on memories (t_membit to t_membit4, br_gh112e's negative ranges, a located bad
    target), strength buffers on memory words and a memory of nets with two drivers; the runs are compared
    with vvp.
  - `test_r6_F.py` (22; T26): a 13-case file-I/O battery through `vcs` and `./simv`, stdout and every file
    written compared byte for byte with vvp's (`test_expected_is_vvps` checks the expectations against a
    live vvp), the translation's shape, the memory in another module still reported, ivtest pr690, the run
    directory (the start-directory variables, user overrides kept, a plain run's cwd, absolute tool paths, a
    `./simv` started from another directory reading and writing there only) and a co-simulation on both
    engines with `$readmemh`, `$fdisplay`, `$fstrobe`, `$fmonitor` and `$writememh`.
  - `test_r6_W.py` (53; P11, §8 Waves): the end-time line and the footer (a run that ran out of events at 10
    ns under `+vcs+finish+100000`, a bounded and an unbounded one), wave options, simv's wave request, the job
    record, `dump_request` and `_Conditions`, VCDs end to end (`test_same_changes_as_vvp`), AMS waves, the
    accel note, VHPI with a native-Verilog unit, the `--std=2040` analyser cases, the Verilog route's
    temporary directories, and a co-simulation's second SIGINT under gdb (exit 130, one interrupted line).
  - `test_r6_L.py` (41; T2, T13, T16, P12, P13): seeded `$random` (1000-draw sequences for six seeds, the
    statement shapes, 64-bit and time seeds) and its caveats and errors, `$urandom` seeds and ranges (14 edge
    bound pairs × 3 call forms × 40 draws, interleaved with `$random`), 5000 draws per function in one
    activation, vector strengthening, trans on bit-selects and between tri-state nets, switch arrays,
    tranif with an x/z control, the port-select warning (plain and under vamos), the resolver plugin's SIGINT
    and stderr, and `l3d_mod_s`.
  - `test_r6_N.py` (23; §4.3, P14): MOS junction defaults and their runs (i = C(v)·dv/dt against the 20-47
    equations on both engines), the diode sidewall charge, LEVEL 3 CLM and its runs, COX, the netlist items
    (SA/SB/SD under scale, the bin-bound error, node names, PULSE defaults, `i()` targets), wire models and
    their runs, VACASK's nested-subckt scoping.
  - `test_r6_C.py` (61, 9 guards; §5.4, T4, harness): switch joins, process-variable reads, quantised chains,
    begin-block declarations, the real-port backstop, port spelling, per-instance probe reasons, an e2e class
    on both engines (a pull behind a tran reaches the pad, a SPICE output through a tran, a flip-flop reads a
    SPICE output, each instance's own reason, a generate-local variable warned), translation errors (a fake
    back end through sv2vhdl-modules, vcs and the whole-design stage, and the real t_membit), the regress
    harness (the filter, the private ivtest areas and work dirs, the smak dispatch and the make environment)
    and the renamed C-side names and comments.
  - `test_r6_merge.py` (3; the merge of T, F and L): a `$readmemh` load and a seeded `$random` draw after a
    delay in an initial block (with the patches' own deposit tests the load read `m0=11` where vvp prints
    `55`, and the draw read the old seed) and a drawn loop test with T's labelled `break`/`continue` (nvc
    analysis failed: "no visible declaration for SV_BRK_1"), each compared with vvp through
    `bin/vvp-sv2ghdl`.
  - `test_r6_R.py` (48; the round-6 repair, R6R-01–32, one class, or two, per item, each failing before its
    fix): the translator items through `bin/iverilog-sv2ghdl` and `bin/vvp-sv2ghdl`, compared with vvp
    (constant and run-time stores outside a vector or a word, the NBA event-control, `final` and
    void-function errors, `$sformat`, bare arguments, a monitored word bit, `%m` per instance, undriven bits,
    a quiet `$finish(0)`, an NBA after a deposit), the scripts (the Rule 6 probe, the deferred top's exit
    status, a reserved-word top, a commented-out module, `SV2VHDL_FILE_DIR`, a wrapper's `-B`, the iverilog
    block's PATH, a min:typ:max specparam), vamos (AMS `$dumpvars` and `dump_covers`, run messages at the
    user's file:line, the synthesised analysis's `delmax`, `Subckt.spelling`, the timing and undriven-input
    warnings) and nvc (a co-simulation interrupted inside a process, the memory of nets, the vector-intrinsic
    arena).
  Assertions the fixes changed on purpose: `test_repair_translator.py`'s `TestAutomaticTaskTwoProcesses`
  now compares with vvp (it asserted the located error, T25); `test_translator_semantics.py`'s
  `TestFopenIsTranslated` replaces `TestUnsupportedFunctionIsLocated`, and `TestFileTasksLocated` locates
  only the `$dump*` tasks (T26); `test_random_vhdl.py` (T16); `test_repair_e2e.py` and
  `test_translator_ports.py` use a select of a module port for their one-way-tran warnings (T2);
  `test_vamos_driver.py`'s `-gui` note and its untranslated-task cases (`$dumpports` instead of
  `$dumpfile`, and `$dumpvars` a note, §8), whose `test_replaced_system_functions_are_warnings` now skips
  itself (no function is replaced any more); `test_netlist_emit.py`, `test_netlist_integ.py`,
  `test_repair_netlist.py` and six regenerated netlist goldens (`cj=… fc=0.0` on the level-1 cards, §4.3.6);
  `test_cside_xyce.py` and `fixtures/ams/cside_xyce/run_cside_xyce.py` (E1/E2 names neutralised); in the
  round-6 repair, `test_ams_e2e_basics.py`'s `TestE2E03Dac.check_display_values`, which expects the 10–40 ns
  displays only (a variable initializer is no event, T25).

**End-to-end** (WSL; every item compiles with `vcs-ams` (or `vcs -ad…`) and runs `./simv` on VACASK and on
Xyce unless noted, through `tests/vamos/ams_e2e_lib.py`; each class compiles and runs once per engine and its
tests assert values in the rawfile, the `$display`ed digital values and times, exit codes, end-of-run lines,
the IE report, the boundary file, the deck and `ams.json`):
1. RC driven by a Verilog `reg` clock through a D2A, with the A2D threshold read into an SV `logic`, no
   `port_dir` (`test_ams_e2e_basics.py`): crossings within 10 ps of the RC closed form with the 500.7 Ω series
   resistance; each A2D event no earlier than its analog crossing and no later than the next stored analog
   point; the report lines pasted back select the same nodes with no TNF.
2. A MOS level-1 inverter chain, a SPICE-only cell auto-bound with no `use_spice` (basics): vin = 1.8 V gives
   < 5 mV; the first-stage delay within 3 % of the level-1 closed form; chain delays agree between engines
   within 0.2 %; an upper-case netlist simulates identically; the tied variant (`wire t; assign t = 1'b1;`)
   gives two VHDL variants, both cut (only when both inputs are nets: with a `reg` clock the auto port becomes
   `input` and both instances share one variant).
3. A 2-bit DAC with a `bus_format <%d>` bus and **no** `port_dir`, plus per-instance `d2a inst= hiv=` rules
   (1.2 V and 2.4 V) (basics): levels within 0.1 mV of the divider; exactly one D2A per bit and no A2D, also
   when the code `reg` is `$display`ed; paste-back; no Xyce chatter in the simv output.
4. Run ends (`test_ams_e2e_ends.py`): `$finish` at 600 ns with the deck stop at 4 µs (a fast end, rc 0, the raw
   ends at 600 ns); `$finish` at 612.345 ns (landed exactly); a `$finish` triggered by an A2D change; `$stop`
   (rc 0); `$fatal` (rc 1, nothing published, run directory kept); `$fatal` at t=0 (rc 1); `initial $finish`
   at t=0: rc 0 and the `(before the first analog step)` end line on both engines – VACASK publishes the t=0
   operating point as a one-point rawfile, Xyce writes none and simv prints the "no analog output" note (§6).
5. A netlist without `.tran`, ending at `$finish` (basics): the note; record and deck carry stop=3600 s and
   maxstep=1e-8; exact RC values; a PULSE with omitted fields takes its defaults from the synthesised
   TSTEP/TSTOP (10 ps edge, held high to the end).
6. A multi-view cell where `use_spice` overrides the Verilog view (basics): the body is masked and the subckt
   binds by name with its ports in another order; an `output reg` view; `-cell buf_cell:inv_sp`; the inverter
   delay within 3 % of the level-1 closed form on both engines (it needs the §4.3.5 maximum step: 0.5 % from
   the closed form, engines 0.07 % apart).
7. x-heep `adc.sp` and `control.init` verbatim in the x-heep directory layout (`test_ams_e2e_supplies.py`;
   fixture `e2e_supplies/`, with a subset of PTM's 65 nm bulk model card), compiled as
   `vcs … -ad=../../../hw/ip_examples/ams/analog/control.init --vamos-analog-maxstep=1n`: the IE report (d2a
   `sel<0>`/`sel<1>` at 1.2 V, a2d `out` at 0.6 V), the SEL voltages, the mux taps (0.24/0.48/0.72 V), the
   comparator output's 0.6 V crossings agreeing between VACASK and Xyce to about 25 ps (the limit is 1 % of
   the 1 µs sine period), and a plain `./simv` run with the synthesised 3600 s stop ending at the testbench's
   `$finish` (P8).
8. A declared-inout pin driven by the digital side, released and driven back by the SPICE side; a tri-state
   `assign`, a `bufif1` and a submodule driver against a 10 kΩ pull-up; per-path BIDIR/A2D roles in one shared
   wrapper; digital values equal vvp's (Verilog models of the cells); a weak driver on a BIDIR net is refused
   (`test_ams_e2e_bidir.py`). 8b: (a) no digital driver at t=0 and a pull-up in the cell: OP at the pull-up
   voltage, enable 0, no start-up glitch; (b) a strong 1 from t=0 against a 5 kΩ pull-down: OP 1.63616 V (the
   HIV/5 kΩ divider with 500.7 Ω), readers see 1 at t=0; (c) the cell holding the pin mid-band (HITH 1.2 V, LOTH
   0.6 V, node 0.9 V, midv_time 5 ns) with no external driver: the enable stays 0 and nothing oscillates; and
   a pin released into mid-band reads x after midv_time.
9. A `.hdl` Verilog-A resistor in a SPICE cell (VACASK; `test_ams_e2e_hier.py`): resolved beside the netlist,
   compiled to `ams/va/1_vres.osdi`; the instance `r=1k` and a `.model` `r=3k` are both honoured. On Xyce the
   parameterised `.hdl` is refused with file:line.
10. A wrapper containing a SPICE cell, instantiated twice, with different `d2a inst=… hiv=` per path (1.8/3.0 V)
    and different roles (w2.u3 passive); in one replica the cell's output shares a digitally read net with
    another cut instance's output, which hosts the A2D: one clone for three paths, both digital nets toggle
    and neither sticks at X; the engines agree within 0.06 ns (hier).
11. cell → wrapper `output` → second cell: 0 IEs, one deck node; with a digital reader at the top: exactly one
    A2D. Two SPICE cells in one module sharing a net that is also that module's output (bandgap vref → ADC,
    exported): one node, no IE, vref stays 1.2 V. Cell output → `v[0]` → second cell input, and a cell output
    on `w[5:4]` read by a cell on `w[4]`: one node, 0 IEs (with readers: exactly one A2D). The same design with
    every SPICE port auto: probe step 2b makes the ports on variable-fed wrapper inputs `input` (hier).
12. A `nand` gate, a `pullup` and a weak `assign` driving cut inputs: one D2A each; an open-drain SPICE output
    (auto port) with a Verilog `pullup`, read digitally: reads 1 while the drain is off; SPICE open-drain pads
    with a Verilog `pullup`, an open-drain Verilog master and a reader: pad = 1/0/1 as vvp gives, with `port_dir
    … inout`, as an auto port and as a `tri1` net (and pulldown/`tri0` polarity); a pull-only pad at hiv (and
    1.5 V under a d2a rule); two SPICE open-drain devices on one line (one BIDIR host); all nine pulls moved into
    the deck (bidir).
13. Declared (`port_dir output`) scalar cut outputs into vector bits through a bit-select, a generate loop, an
    instance array and two plain instances on `v[0]`/`v[1]`: one A2D per bit, the parent never tied to Z,
    rules written with Verilog names (`tb.g[0].u.y`, `tb.ca[1].y`, `tb.two[1]`, `tb.bsel[2]`) matching, and
    Top-Net naming the bit; the same design with auto ports gets no spurious D2A (per-bit Z) (hier).
14. Ports named `in`/`out` on one cell and `In`/`OUT` on another (checked by value); `in`/`out`/`OUT` on one
    multi-view cell (T10) (hier).
15. A `use_spice` cell that is never instantiated, no `-top`: the top stays `tb` – once with a SPICE-only cell
    and once with a multi-view cell whose Verilog view is in the sources (ends).
16. Two concurrent `./simv` runs of one build, started from two directories with different `+plusargs`, with
    `choose -o waves/e2e16` and a behavioral source (VACASK compiles it in each run directory): both exit 0,
    and each directory's raw equals its solo run (ends).
17. `-ad`, `-ad=f`, `+ad`, `+ad=f`, the `vcs-ams` symlink and `vamos -vcs-ams`, the two control files differing
    in hiv (seen in the report, the rawfile and the crossings); `snps_vcsAD.ini` read first and merged key by
    key; `-ad=ctl/sub.init` with its netlist beside it; plain `vcs` without `-ad` stays digital (basics).
18. `-override_timescale=1ns/1ps` with a testbench that has no `` `timescale `` and a `const time PH=5ns`
    clock driving an RC: edges at exactly 5, 10, … ns, the D2A crossing at edge + 5 ps, RC crossings on time;
    `` `timescale 1s/1s `` and no option: the precision error; no time unit: the error; the option rewriting
    `` `timescale 1s/1s ``; `-timescale=1ns/1ps` alone; `%0t` of `$realtime` (T7) (basics).
19. Mid-transient analog failure, in HSPICE syntax: `ecmp out 0 vol='if(time < 150n, 0, if(v(cap) > 0.5, 0,
    1))'`, `r1 out cap 1k`, `c1 cap 0 1p`, in the SPICE cell of test 1: the deck is first shown to fail
    standalone on each engine ("Timestep too small" / "Time step too small"); the compile succeeds; `./simv`
    exits 1 with `<engine> transient failed at 1.5e-07 s` and publishes nothing; `+vcs+finish+120000` (before
    the trigger) runs cleanly (ends).
20. Parameterised supply (`.param vsup=1.2`, `v_vdd vdd 0 'vsup'`): the IE reference is 1.2 V, not 3.3 V; a
    1.2 V / 3.3 V level shifter (in hiv 1.2, outc thresholds 0.6, out 1.65); PAMS Method #2a (`d2a powernet
    hiv=1.2 lov=0 node=top.vdd`/`top.vss`, the constants written as `assign` and as `.vdd(1'b1)`): signal IEs
    1.2 V / 0.6 V, no fallback warning, the output toggles; Method #2b (a `spice_pwr_supply` subckt with `v_vdd
    vdd 0 1.8` instantiated from Verilog, reached through through-nets): 1.8 V / 0.9 V (supplies).
21. A cross-engine parameter deck (`test_netlist_integ.py`, standalone): a duplicate `.param`, a
    top-level/subckt collision under `--vamos-parhier=local`, an out-of-order chain, nested `m=2` inside `m=3`
    (6×), `G cur=` inside an `m=3` subckt (3×), a PNP bias point (0.9966 V), a BSIM3 card with `VERSION=3.1`
    (within 5 %), a level-3 diode (VACASK; refused on Xyce); a level-9 card rejected on both engines; HSPICE
    built-ins with negative, zero and non-integer arguments (pow(2,1.5)=2, sgn(0)=0, db(-10)=-20,
    log(-2)=-0.693, sqrt(-4)=-2, nint(-2.5)=-3) in parameter and behavioral contexts, identical on both
    engines.
22. A binned BSIM4 deck (standalone): `nf>1`, a bin-edge geometry (`0.22*1e-6` against 2.2e-7), a geometry
    with no bin (clear error), a device without `ad`/`as`; with bins split at W = 1 µm and a `w=1.6u nf=2`
    device, both engines select the same per-finger bin; six devices each equal their reference card, and the
    engines agree to 1e-6.
23. `PULSE(0 1 1n 0.1n 0.1n)` holds at v2 on both engines (standalone).
24. A `port_connect p => snps_open` port and a D2A input, each reaching only MOS gates, and an `inout` pin
    whose digital side is Z at t=0 with a gate-only analog side: the smoke check passes, the operating point
    converges and the IE report lists the `snps_open` shunt (supplies). The cell carries 1 fF loads on its
    outputs: a deck with no capacitance at all stalls Xyce at a 10 ps edge, standalone too, whatever the
    maximum step.
25. An sp_mos1 card with LD=0.2u and a level-54 card: `w=2 l=1` under `.option scale=1e-6` gives the same
    current as `w=2u l=1u` (level 1: 4.1667e-5 A; unscaled would be 2.5e-5 A); a level-1 diode is not scaled,
    a level-3 one once (VACASK); the default temperature against a card with `tnom=25`; standalone and inside a
    cut cell (supplies), the engines agreeing to 2e-5.
26. Tri-state on an auto port: a SPICE-only pad cell with an internal pull-up, `assign pad = en ? d : 1'bz`
    plus a reader: after release, pad = vdd and the reader sees 1. Cut inputs tied to `1'bz`, on an undriven
    wire (declared, auto, through a wrapper), and the Z bit of `assign wv = 2'bz1`, against a SPICE pull-down:
    no IE, the node keeps its analog value (0.36 V) (bidir).
27. `supply1 vdd; supply0 vss;` on SPICE supply pins, as auto, input and inout ports: ideal POWERNET sources,
    no series R, vdd at hiv and vss at 0 V under a 1 kΩ load, each cell's IEs following its own supply, no 3.3
    V fallback warning for the supply0 nets; supply pins reaching the cell through a Verilog wrapper's input
    ports, as auto ports and with `port_dir input` (supplies).
28. Generate pad rings `for (g…) pad_sp u(.pad(pbus[g]))` on bit-selects of `[5:3]` (auto ports) and `[0:2]`
    (declared inout) buses, and a wrapper ring with a pull-up inside a shared module: nine distinct nodes;
    D2A-only, A2D-only and bidirectional roles; Verilog-bit-name rules reach exactly their pads; values match
    vvp; a ring control bit held only by a `reg` vector initializer; the ring twice in one module with the same
    instance name `u` (T9) (bidir).
29. Two cut outputs on one digitally read net: exactly one A2D, and the digital value is never X after the
    first analog step (bidir).
30. Decks with `.tran 1n 3.3u` and `.tran 1n '1u/3'` end with `analog end at 3.3e-06 s` / `3.33333333e-07 s`
    and an engine-filled rawfile header; `+vcs+finish+N` before the deck stop publishes a rawfile whose `No.
    Points:` matches its data; a 6 µs deck stop and `+vcs+finish+4500000`, both above 2^32 fs, reach their end
    (P8) (ends).
31. Port buffers (`test_ams_e2e_portbuf.py`): e2e 11 with a second wrapper on a variable of its own (`clk2`,
    with its own d2a rule) whose D2A the buffered cell hosts. With `src.a` declared inout behind the
    variable-fed wrapper input, every engine compiles and runs both declarations: inout gives exactly what input
    gives (IE entries, boundary file, deck nodes, roles, `$display` edges; the report differs only by the
    direction lines). `src.a` declared output, and a wrapper that reads its own port, stop at the cut with an
    error naming `port_dir input`. `TestE2EProbeReason*`: the step-2b reason in the report. Phase 4:
    `TestE2EParamOverride*` (real, string and integer overrides on a multi-view cell refused; overrides equal
    to the default, string defaults and computed localparams pass) and `TestE2EX2v*` (`x2v=4` and the other
    X rules end to end).
32. Time base (`test_ams_e2e_timescale.py`, phase 5). Benches at `` `timescale 1ns/1ps `` (control),
    1ns/10ps, 1ns/100ps and 10ns/10ns, and one with no `` `timescale `` and `-override_timescale=1ns/10ps`,
    drive an RC cell with a 50 ns half-period clock: design.vhd waits 50 ns SI; the digital's clock edges at
    50, 100, … ns are the rawfile's D2A ramps (+5 ps, within 1 ps); the RC output crosses half supply at the
    closed form (±20 ps), and each A2D event lands within one precision tick of its crossing; the run ends at
    the digital stop, 300 ns. `+vcs+finish+20000` at 10 ps stops both sides at 200 ns. At 1 fs, `%t` of
    `$realtime` prints A2D times past 2.147 µs exactly.
33. Rules fixes (`test_ams_e2e_rulesfix.py`, phase 5): one design with every report line kind – `midv_logic`
    Z/X/1 on A2D outputs held mid-band read z/x/1 about `midv_time` after entering the window (Z was x);
    strong and weak D2As through `map_by_node r=1k` into 1 kΩ loads sit at 0.9 V, and a BIDIR pin at r=250 at
    0.0439 V; supply1/supply0 rails at 1.8/−1.5 V from `d2a powernet` rules; a `d2a powernet` pin with a 4 ns
    ramp and a 2 ns delay; the report's 15 control lines pasted back give an identical deck, cut.vhd and
    boundary – and `vcs-ams` and `./simv` under an ISO-8859-1 locale.
34. Deck fixes (`test_ams_e2e_deckfix.py`, phase 5): `port_connect -inst` wiring per instance (u1 on
    `vdd_core` at 1.2 V, u2 on `vdd_io` at 3.3 V, IE levels following each; mixed, reversed, in-group and
    `-inst`-only forms wire the same deck; an uncovered instance is an error); `port_connect` nets that are
    not in the deck, a ground-alias port; a second or misspelt `-top`, a left-out subckt, a parameter named
    inside a range parameter, the variable warning and the option notes; an `ie_reference_voltage` on a
    regulator (VCVS) output (levels 1.5/0.75 V where the highest deck source would give 5.0/2.5 V), last-wins
    and unreached entries, an unevaluable supply; `v()` saves of an unknown node, a port alias and an
    internal node, XA probe patterns; bad engine overrides.
35. Runtime (`test_ams_e2e_run.py`, phase 5): testbench output in stdout and the `-l` log unchanged
    (banners, a blank line, `\tcode=42`, dashes, text containing the tool phrases), no Xyce chatter and no
    blank line after the end line; an unresolved boundary path (also with `NVC_COLORS=always`) fails with the
    named line; TSTART (`.tran 1n 1u 500n`: `+t300` and `+vcs+finish+200000` give the note and publish
    nothing, `+t0` the t=0 note, `+t500` one point, `+t700` 5e-7 to 7e-7); `+vcs+finish+` forms
    (`300ns`, `300000+0`, `0`, `3xs`, and `--vamos-strict` with `3xs` failing); the ABI check without nm,
    readelf or objdump, and with a missing library directory (no rebuild advice, no footer); 2 rounds of 8
    concurrent `./simv` in one cwd; a `$finish` at 2000000006 ps (one end line, `digital stop at
    0.002000000006 s`, footer `Time: 2000000006ps`, published); a finish on an A2D change at the candidate
    time 0.27–0.50 fs short of the stop (VACASK R1=96k, Xyce 94k) published; Ctrl-C to the process group
    (simv dies by SIGINT, the note, a readable partial rawfile, nothing published, no nvc left), SIGTERM to
    simv, SIGKILL to nvc (exit 137, a footer of at least 150 ns), SIGINT to nvc only (exit 130), SIGKILL to
    simv (nvc gone).
36. Case statements on pads (`test_ams_e2e_semantics.py`, phase 5): `case`, `casez`, `case (1'b1)` items, and
    reg and `assign` copies on a pad released to a SPICE pull-up and on a pad the analog side drives, compared
    with vvp (Verilog models of the cells) on both engines: a weak 1 matches `1'b1`.
37. Ports (`test_ams_e2e_ports2.py`, phase 5): a port buffer on an instance array (`wrap_e we[1:0]`): the
    buffer feeds every element, and inout gives the IEs and the run of input; real expressions on real cell
    ports (`r1+0.2`, `-r1`, `r1/2.0`, `r1*2.0`, `assign rv = r1+0.2`, `code*0.1`, an undriven `wire real`) reach
    the analog as twice the input value, with no logic3d real arithmetic in design.vhd; a SPICE-only cell's
    auto bus port on a concatenation runs as with `port_dir`, associated part by part, with the same IEs.
38. Switches and registers in the cut (`test_r6_C.py` `TestE2ER6C`, round 6): a pull behind a tran reaches an
    inout SPICE pad and moves into the deck (the pad's node sat at 0 V while the digital read 1); a SPICE output
    through a tran compiles and runs (vcs-ams exited 1); a SPICE output that only a flip-flop reads gets its A2D
    (the register read z for the whole run); each instance of a wrapper on its own variable shows its own
    step-2b reason; a generate-local variable joining SPICE ports is warned.
39. File I/O in a co-simulation (`test_r6_F.py` `TestFileIOAms`): vcs-ams accepts `$readmemh`, `$fdisplay`,
    `$fstrobe`, `$fmonitor` and `$writememh` (they were refused as untranslated); the files are read and
    written in the directory `./simv` was started in, and the run directory is removed.
40. Waves of a co-simulation (`test_r6_W.py` `TestAmsWaves`): `vcs-ams … +vcs+dumpvars` writes a VCD in which
    the A2D net toggles; and a co-simulation's second SIGINT (`TestCosimSecondInterrupt`, gdb placing each
    SIGINT) ends nvc with exit 130 and exactly one interrupted line.

**C-side** (both engines unless noted; `test_cside_engine.py` over E1's 40 runner cases, `test_cside_xyce.py`
over E2's 13): the stock demos (`min`, `a2d`, `glitch`) unchanged (same point counts and values); finish at
120 ns with the deck stop at 1 µs ends at exactly 1.2e-07 with rc 0; a finish at 123.456 ns lands exactly;
`initial $finish` and `$fatal` at t=0 (a stop during the P2 settle: rc 0 / rc 1 and the t=0 line), a
`$finish` triggered by the first A2D sample and one by a threshold crossing each end promptly with the expected
end line and exit status; a digital runtime fatal (index out of range) exits non-zero without hanging;
`std.env.stop` ends the run; deck end and `--stop-time` end say "analog end", never "stalled"; transient
failures (VACASK with a negative-resistance node; the stock Xyce `a2d` demo plus `Bcmp out 0 V={IF(TIME <
150n, 0, IF(V(cap) > 0.5, 0, 1))}`, `R1 out cap 1k`, `C1 cap 0 1p`: `Xyce transient failed at 1.5e-07 s`, rc 1);
the stalled, failed and complete loop branches through a stub engine; ABI failures (a stub bridge with no ABI
or ABI 1, a stub engine with no ABI, the real unpatched bridge and VACASK library with `--old-*`, an unpatched
libxyce.so under the patched interface, an old interface); `clk=1` at t=0 gives no start-up pulse; ramps (a 10
ps edge, the 1 ns default, `rise=0` clamped with a warning, `rise=1e-11 fall=1e-9` with a reversal mid-rise: the
fall ends 1 ns after the reversal); a 5 MV value passes and `real'low` drives 0 V; 300 registry entries and
"registry full" at 8193; 11 kinds of malformed boundary line and a 3000-character line; a missing bridge name
(no segfault on either engine), a direction mismatch, a duplicate name; stop times above 2^32 fs (a 6 µs deck
with `6000000001fs`, a paused run, the 3600 s default, one fs past TIME'HIGH: an error or a run to the deck
stop, never an early end); Xyce alone, with a stub step library: a finish after a veto to 123.456 ns, a finish
on the first step (2 points), a veto beating a finish, other answers, the NULL-callback and missing-function
`BindCB` cases, a plain deck bit-identical to the unpatched Xyce, `No. Points:` filled after a finish and blank
when paused. Phase 4 added (15 of its 20 new runs fail on the old build): `finish_10_digits` and
`finish_ms_10_digits` (exact end-line text), `finish_a2d_at_candidate` (the 2 fs window), `ramp_late_10ps`,
`ramp_late_coarse_1v8`, `ramp_1p5s_10ps` and `ramp_below_resolution` (the Xyce ramp cut and the stretched
short ramp, VACASK as the cross-check), `interrupt_stub_init`, `interrupt_stub_run` (the stub engine's
`STUB_MODE` `sigint_init`/`sigint_run`) and `interrupt_running` (a real engine, SIGINT 1 s after the start);
`run_cside.py`'s `tlast` allows 1e-9 relative or 2 fs, and its `fs_text` has pure-Python tests. Every case
checks that exactly one end-of-run line appears and that it is the expected one.

**Translator (T):** the ivtest suite through sv2ghdl+nvc loses no tests (`ivtest/iverilog-nvc`, `vvp_reg.pl
--suffix=-sv2ghdl`): 1154/3011 unmodified; 1160 with T1–T5 (six real fixes; `tri3`'s new failure is an
artefact of the gate's missing resolver, and `pr1704013`'s baseline pass was transient); 1161 with T7–T10
(`pr841`), no new failure. Until the repair round the gate ran without the sv2vhdl Python resolver (the
plugin found no `sv2vhdl_resolver.py`, so every run printed an import error and strength resolution was
off); `bin/vvp-sv2ghdl` now finds the module (beside the plugin, in the nvc source tree named by the build's
Makefile `abs_top_srcdir`, or beside the build) and sets `PYTHONPATH`, loads `libsv_math.so` after the
resolver, and drops nvc's `Function` trace lines.
`test_translator_tgt.py` compares settled `$display` values per time step against vvp: T1 severity and stop;
T2 pads (plain bit-select, generate ring, a ring inside a module whose bus is its own inout port,
part-selects through a wrapper, non-zero-LSB and ascending ranges, the input-port fallback, through `vcs …
-R`); T3 paths; T5 tri nets; `%t` of reals and its widths; driven input ports and port buffers; instance
labels; port names differing in case. `test_translator_scripts.py` checks T4 with fake iverilog/nvc scripts.

Phase 5 (each run in a private ivtest copy, `SV2GHDL_SRC_ROOT`, because concurrent runs in the shared
`ivtest/vsim` corrupt each other's nvc WORK library; the run numbers here and below are run ids in the
development build's regression database, `regress/results.db`, which is not in the repository, so they
identify the runs only there):
- TA (T11, the T7 cast): 1164/3011 (run 401), no pass→fail against runs 69 and 68; `sv_package`,
  `sv_ps_function4` and `sv_ps_function5` newly pass (A/B-confirmed). `test_translator_time.py`, each compared
  with vvp: the SI tick for 11 timescales (1ns/1ps, 1ns/10ps, 1ns/100ps, 10ns/10ns, 1us/10ns, 1us/100ns,
  100ps/10fs, 1ns/100fs, 10us/10us, 1ms/100us, and 1s/10ms compressed: `wait for` literals, `$time`/`$simtime`
  divisors and output); `vcs` + `./simv +vcs+finish+N` stopping at 20 ns at 10 ps, 100 ps and 10 ns
  precision; a 10ns/100ps module in a 1ns/10ps bench; `%t` of reals past 2^31 ticks (1 ps and 1 fs), under a
  finer `$timeformat`, ties and -0; the design-wide default `%t` unit; `$time` rounding, also past 2^31 units;
  `$timeformat` suffix escapes; scope names in functions, tasks and named blocks; a package function followed
  by a delay.
- TB (T2, T3, T4, T5, T8, T12): 153 targeted ivtests (tran, tri0/tri1, pulls, inout, reals, instance arrays,
  escaped names): 41 pass with the pristine plugin, 45 with the fix (`implicit_cast4`, `inout3`, `pr1478121`,
  `pr2123190`), none regress. `test_translator_ports.py` (35): instance arrays broadcast and split, width
  mismatches, the untranslated-shape errors, tri inputs released, warnings surfaced under vamos, T2 arrays on
  part-selects and a tran on a bit-select, real arithmetic, inout concatenations, wrapper inputs on a driven
  inout, variables on a driven input, real parameters (exact printing, several instances, undriven real
  nets), variant parameter lines, escaped instance names; 33 of 34 fail with the pristine plugin.
- TC (T13–T20, P10): the iverilog-nvc block over 1156 of the 1161 baseline passes plus 627 baseline failures:
  34 fail→pass (`random`, `pullupdown`, `tran-keeper`, `pr1032`, 13 `vhdl_*` `$random` tests, …) and 4
  pass→fail, all false passes exposed by a working `$random` (`pr2722339a`/`b`: signed `/` and `%` — nvc
  `l3d_mod_s` uses VHDL `mod` where Verilog `%` takes the dividend's sign; `vhdl_smul23_stdlogic`: signed
  multiply with x/z inputs; `vhdl_test2`: the mixed-language dut's `in & mask`); the nvc-vhdl block 246 vs
  245, no regression; 14 compressed-assignment ivtests +3. `test_translator_semantics.py` (31; 22 fail with
  the pristine plugin): case strength and x/z exactness, async reset in the wake shadow, the initializer
  hoist, `$random`/`$urandom`, located unsupported functions, file tasks, classes, `@(a)` inside a block,
  compressed shifts.

Repair round (T21–T24 and the resolver-aware `bin/vvp-sv2ghdl`): the official gate, run 777 (in a private
copy of that database), passes 1347/3011 (1659 fail, 2 notimpl, 3 xfail), against 1198 in run 555: 0
pass→fail, 149 fail→pass. By the gate's diagnosis 126 of those only needed the resolver module that the gate could not
import (112 gold-file tests broken by the import error lines, 14 that need resolution, among them br918a,
br918b, br_gh99s, force_release_wire8_pv, pr1032 and pr2725700a, which had been treated as flaky); the other
23 come from the translator fixes (disblock, disblock2, pr2722339a/b, rl_pow, …). The ivtest gate baseline is
now 1347/3011 (run 777).

Pre-push regression record (2026-10-03, each tool's own suites, patched against a pristine build of its
`HEAD` on the same machine): ivtest/iverilog (vvp) 3006/3020 both, identical per test (the core binaries
are pristine; vvp never loads vhdl.tgt); nvc/regr 1149/1263 and ivtest/nvc-vhdl 282/294, identical per test
and byte-identical outputs (`binary3` passes with the stock `run_regr` once `$random` became plain VHDL in
`sv_math_pkg`, so no plugin is needed); xyce/regr (`+serial+nightly`) 2179/2472 both, the same 293 failures
(Verilog-A models this build gets only through PyMS, missing `python` in verification scripts, …), plus
xyce/ihp-pdk 93/93; VACASK ctest 73/78 both (test_pssosc2 needs the IHP PDK; four absdelay tests skip),
1224 rawfiles bit-identical. ivtest/iverilog-nvc against the pristine stack (sv2ghdl ad14935, iverilog
3c410a75a, nvc e685d1a75): 1155 → 1347 of 3011, 195 fail→pass and 3 pass→fail, each a bug older than this
work that the pristine stack hid, and each shown by a variant that fails on the pristine stack too:
`tri3` (`!== 1'bx` on a pulled X: `l3d_eq1` compares value bits, the 3D-logic semantics below; it passed
only because the resolver module failed to import, so the pulls were never resolved), `vhdl_test2` (the
delta-ordering item in §7; it passed only because `$random` was 0) and `vhdl_smul23_stdlogic` (x/z
arithmetic on value bits plus a delta race in its check; it passed only because `$random` was 0). These
three are the known failures of the pushed state.

After the upstream merge (§7; each block in a private ivtest root, on the merged iverilog c9726d635
`_install` and nvc 399d83df8 nvc-build): ivtest/iverilog-nvc 1347 (run 88) → 1366 of 3011 (run 105) and
nvc/regr 1149 of 1263 (run 89) → 1155 of 1264 (runs 102 and 107), both with no pass→fail; ivtest/iverilog
3006/3020 (run 104) and ivtest/nvc-vhdl 282/294 (run 106) unchanged; the hazard3 suite 9/9 (run 108,
below). These are round 6's baselines.

**Round 6** (each fixer gated its own changes alone, in a private source root, against those baselines; T's
and W's runs and C's hazard3 run are in the development build's database, with the run numbers below; L's,
F's and C's ivtest runs were recorded in private copies of it, whose run numbers repeat, so they are not
given):
- T (T25): ivtest/iverilog-nvc 1430/3011 (run 122), 64 fail→pass and no pass→fail against run 105, nor
  against run 88 – vhdl_test2, always3.1.6B/D and always3.1.7A–D, sv_var_init1/2, br_gh112b–f,
  br_gh191_break/continue, memidxrng, pr1008, schedule, vhdl_smul23_stdlogic, vhdl_umul23_stdlogic,
  vhdl_nand104_stdlogic, … (earlier iterations had pass→fail – pr2849783, pr307a, pr3368642 and vhdl_loop in
  run 111, always_latch_fail3 in run 117 – each fixed and now guarded); ivtest/nvc-vhdl 286/294 (run 124),
  no pass→fail (basicstate, basicstate2, dff1 and pr142 newly pass); hazard3/vamos 3/3 (run 125).
- F (T26): ivtest/iverilog-nvc 1372/3011, no pass→fail (fopen1, fopen2, writememb1/2 and writememh1/2 newly
  pass; pr690 moves from "running iverilog" to a gold mismatch, for want of `SV2VHDL_FILE_DIR` in
  `bin/vvp-sv2ghdl`, which the repair added, R6R-14); targeted file-task ivtests 1 → 7 of 43, and 21 with
  that one-liner; nvc/regr and ivtest/nvc-vhdl identical per test to their baselines; hazard3/vamos 3/3.
- W (P11): nvc/regr 1217/1264 (run 119), no pass→fail: the 62 extra passes are vhpi*, wave* and issue* tests
  that need `bin/fstdump` and `lib/vhpi_test.so`, which the shared build does not make; ivtest/nvc-vhdl
  282/294 (run 120) and ivtest/iverilog-nvc 1366/3011 (run 115) identical per test to runs 106 and 105;
  hazard3/vamos 3/3 with no accel-jit line in any run.log (run 121; run 108's each had one).
- L (T2, T13, T16, P12, P13): ivtest/iverilog-nvc 1375/3011, 9 fail→pass (pr995, urand, urand_r, urand_r2,
  urand_r3, pr2832234, pr3296466a/c/d) and no pass→fail; without P13 only pr2832234 (and three test_r6_L
  cases) fail; nvc/regr and ivtest/nvc-vhdl identical per test; hazard3/vamos 3/3 at the base's run times.
- N (§4.3, P14): no digital code changed. The netlist and repair suites and every AMS e2e suite on private
  engines; the Xyce regression subset (DIODE, DIODE_ANALYTIC, NMOS3_DC, HOMOTOPY's mos3 cases, LEAD_CURRENTS
  nmos3, MOS13_IC mos3, MOSFET_ParamAliases) 23/23 before and after, private and shared; VACASK ctest 73/78
  after the shared rebuild, its baseline (test_pssosc2 needs the IHP PDK; four delay tests skip).
- C (§5.4, T4, harness): ivtest/iverilog-nvc 1366/3011 with the R6C-05/06 script changes, identical per test
  to run 105; ivtest/iverilog 3006/3020 and ivtest/nvc-vhdl 282/294 through the smak dispatcher, identical per
  test, no smak process left behind; the ivtest tree unchanged afterwards; hazard3 9/9 under smak in 4
  minutes (run 118; run 108 hung 12 minutes in smak, then fell back to make).
- The merged stack (T, F and L on iverilog; F, W, L and P13 on nvc; P14; every sv2ghdl change):
  ivtest/iverilog-nvc 1445/3011 (run 126; 1561 fail, 2 notimpl, 3 xfail), no pass→fail against run 105, run
  88 or T's run 122, and 79 fail→pass against run 105 – exactly T's 64, F's 6 and L's 9. Of the pushed
  state's three known failures only `tri3` is left (the value-bit semantics, §7): `vhdl_test2` and
  `vhdl_smul23_stdlogic` pass since T25. Still failing among the items above: pr1703346, scoped_events,
  br_gh127c, br_gh127f and pr3197917, mem1, pr2842621, and pr690 and readmemh1 (for want of
  `SV2VHDL_FILE_DIR` in `bin/vvp-sv2ghdl`; both pass since the repair, R6R-14). The vamos suite under WSL:
  1619 tests in 1197 s, 3 skips and 2 failures, `test_ams_e2e_basics.TestE2E03Dac.test_display_values` on
  VACASK and on Xyce, whose
  expectation is stale: it wants a `c1=00` display at time 0 from `always @(c1)` with `reg [1:0] c1 = 2'b00`,
  which was the always block's time-0 run (TC-08); under `-g2012` the initializer is no event (IEEE 1800 6.8,
  T25), and vvp `-g2012` prints only the 10, 20, 30 and 40 ns lines (the repair made the test expect
  `["01", "10", "11", "00"]` at 10–40 ns). Under Cygwin Python 3.9.16: 1616 OK (879 skips, before
  `test_r6_merge.py`). The translator files `test_r6_T`, `test_r6_L`, `test_r6_F` and
  `test_repair_translator` under WSL: 115 OK. `test_r6_merge.py` (3, the merge's own: a `$readmemh` load and
  a seeded draw after a delay in an initial block, which the patches' own deposit tests read stale, and a
  drawn loop test that carries T's labelled `break`/`continue`, which failed nvc analysis) passes.
  nvc/regr 1158/1264 (run 127), no pass→fail against block_run 182 and three fail→pass (ivtest8, select5,
  vlog26; the 62 vhpi*, wave* and issue* tests W's own build passes need `bin/fstdump` and
  `lib/vhpi_test.so`, which the shared build does not make). ivtest/nvc-vhdl 286/294 (run 128), no pass→fail,
  the same four fail→pass as T's run 124. hazard3 9/9 (run 129): every variant's TOHOST image and cycle
  count equal to the reference on Verilator, Icarus and vamos; the vamos compiles take 7 s (14–15 s in run
  108, whose logs each had an nvc `** Error` line, now none) and run at 2.08, 2.03 and 1.95 kCycles/s (2.23,
  2.12 and 2.12 in run 108).
- The round-6 repair (R6R-01–32 on the merged stack; `vhdl.tgt` md5 43e6ba84…, nvc-build `lib/libnvc.so`
  4b085e71… and `bin/nvc` 9063f579…; run from a private source root, `SV2GHDL_SRC_ROOT`):
  ivtest/iverilog-nvc 1875/3015 (run 134, block_run 223, a full run: 1874; the 7 cases whose top guess
  changed after it reran in run 135, block_run 224, scope2 back to pass) against 1445/3011 in run 126
  (block_run 213): 432 fail→pass – 334 compile-error (CE) tests that now see the translation's error,
  iverilog-sv2ghdl exiting 1 for a deferred top, and 98 others: pr690, readmem*, nested_impl_event1,
  pr2785294, wiresl2, pr1716276, … – and 5 pass→fail, all spurious passes before: function10 (a void
  function crashed the translation, and the CO test passed on exit 0; now a located error), macro_args (`-y`
  is not searched; CO passed on exit 0), sdf_del_max/min/typ (SDF and path delays are not simulated; the
  tests printed their failures and then PASSED because nvc lost their `pass = 1'b0`, which R6R-21's
  `sched_driver` fix keeps). br_gh209, fdisplay2, sp2 (pass) and pr2029336 (fail) are recorded now.
  nvc/regr 1164/1264 (run 136, block_run 225) against 1158 (block_run 214): no pass→fail, 6 fail→pass
  (ivtest20, select4, vlog21, vlog3, vlog4, wide4). ivtest/nvc-vhdl 286/294 (run 137, block_run 226),
  identical per test to block_run 215. hazard3 9/9 (run 138): cycles 35720, 95038 and 247044, the reference,
  on all three engines. ivtest/iverilog filtered (4 tests) with no iverilog on PATH: 4/4 (run 139, R6R-25).
  `run_cside.py vacask,xyce`: 88 passed, 2 skipped. The vamos suite under Cygwin Python 3.9.16: 1667 OK (921
  skips); under WSL Python 3.14.4: 1667 tests in 1255 s, OK (2 skips, the ISO-8859-1-locale ones; the
  `TestE2E03Dac` failures are gone).

**Harness** (`regress/`, round 6, C): a run's work directory is `out/run-<id>-<tag>/`, the tag from the
database file's real path and the run's start (`Util::run_workdir`), so copies of one database never share
one (`run-one` and the dashboard find both forms). Every ivtest block works in a private area
`<workdir>/ivtest-<block>/` that links the ivtest tree's entries, with its own `log/` and `work/`, so the
tree is only read (`REGRESS_IVTEST_SHARED=1` restores the old behaviour). A `--filter` that selects nothing
is an error before a run is recorded, never a whole-suite run, and a filtered ivtest run reads exactly the
lists an unfiltered one reads (the runner filters its own lists through `Regress::IvtestFilter`, loaded with
`-M`). A smak dispatch that fails keeps the blocks that finished and runs the rest again under GNU make,
recording why in the run's notes; every block gets the environment `regress run` had, without MAKE,
MAKEFLAGS or SMAK_* (`run-one --env-file`), and the dispatcher's session is ended when it exits or on a
signal, so no job server or abandoned block survives. A gate whose `regress run` stopped before recording a
run fails instead of judging an older one (`Gate.pm`); a `$` in a filter survives the dispatch makefile.
There is no flaky list: the six tests once treated as flaky (§9, repair round) pass.

**hazard3** (`tests/hazard3_mandelbrot/`, `regress run hazard3/verilator hazard3/iverilog hazard3/vamos`;
from 1536120): the Verijit project's Hazard3 Mandelbrot test case – Luke Wren's Hazard3 RV32IMAC core with
two AHB-Lite SRAMs running fixed-point Mandelbrot firmware – as a portable variant every engine runs the same
way, with no hierarchical references and no C++ (a TOHOST snooper streams the image, a checksum and DONE).
Three firmware variants (8×8/16, 16×16/8, 32×32/4 iterations) pass only when the TOHOST lines equal a native
gcc golden exactly and the cycle count equals the reference (35720, 95038, 247044); Verilator, Icarus and
vamos (`vcs` then `./simv`) all do (run 108: about 1.2 MCycles/s, 4.1 kCycles/s and 2.1 kCycles/s), and an
opt-in `hazard3/bench-verilator` block runs upstream's own 1024×1024 benchmark. Its README is
`tests/hazard3_mandelbrot/README.md`.

**Review:** phase 4 reviewed the whole diff adversarially, each finding re-checked by an independent
skeptic; its confirmed findings (one blocker, 24 major, 21 minor), the first round's hand-offs, the
user-guide trial and the open items of revision 4 were fixed in phases 4 and 5 (§11), or are listed as
**open** in §10.

## 10. Work breakdown

**Phase 0 (done, frozen):** `vamos/notes.py`; `vamos/netlist/{__init__,expr_ast,ir,numbers}.py`;
`vamos/ams/{__init__,config,model,names,portmap,layout,globs,engines}.py`; `Job` (`schema`, `ams_control`,
`ams`, `precision`, strict `from_json`); the `optable` eq-name assertion; `tests/vamos/vamos_testlib.py`;
`tests/vamos/test_ams_contract.py`; the fixture directories. Amended while building: `model.py` (`A2D_IE`
allows `midv_logic='Z'`, `MIDV_L` 3 = Z), `names.py` (the `MAX_FIELD`/`MAX_LINE` comments: vamos's own limits
since P4), `engines.py` (an always explicit `SIM_MODULE_PATH`), `numbers.py` (`parse_number` correctly
rounded: a power-of-ten suffix scales the decimal exponent, §4.1), `job.py` (`override_timescale`),
`test_ams_contract.py` (report, deck-source and chatter-filter tests).

**Phase 1** (parallel; each agent owned its files and tests; nobody committed):

| agent | built |
|---|---|
| N1 netlist core | `netlist/{expr,rawfile}.py`: the parser, evaluator and both printers, proven row by row on both engines (§4.1); the rawfile reader |
| N2 parser | `netlist/spice.py`: §4.3 with deferred library errors (`left_out`), HSPICE `.tran` intervals, the PWL first point |
| N3 emitters | `netlist/{tables,vacask,xyce}.py`, `ams/va/vamos_ie.va`; goldens on both engines; smoke helpers; the Xyce `vamos_mfactor`, the VACASK NOT_GIVEN rule |
| N4 netlist integration | all of `netlist/`: reachable-only printing, Xyce per-path binning, `sp_bsim4v8`, level-3-only diode scaling, the Xyce PWL TD shift, `expr.number`, `i()` columns; `test_netlist_integ.py` with e2e 21–23, 25 standalone |
| R rules | `ams/{initfile,rules}.py`: §2, §3 with the role filter, `resolve_detail`, `paste_line`, `check_rule` |
| V Verilog side | `ams/{verilog_ports,shells}.py`: the `ivlpp -L` origin map, the time rules, probe placeholders, non-ANSI shells, the direction probe |
| C cut | `ams/{vhdl,cut,sv2vhdl_modes}.py`, VHDL fixtures captured in WSL, two real nvc+VACASK co-simulations |
| E1 engines (nvc side) | P1–P7 in nvc, `libcosim_bridge` and VACASK; `run_cside.py` and the C-side tests |
| E2 engines (Xyce) | P1/P5/ABI on the Xyce side (`CosimStepResult`, the multi-library rule, `xyce_lib_cosim_abi`); `run_cside_xyce.py` |
| T translator | T1–T5 in iverilog tgt-vhdl and the sv2ghdl scripts; translator tests; the ivtest gate |

**Phase 2 (integrator):** `ams/{flow,deck,report,supply}.py` (`supply.py`, the §3.3 trace, was not in the
plan), `backends/cosim.py`, the run-command builder refactor in `backends/nvc.py` (`run_command`, `stream` with
`on_raw`, `work_spec`, `remap_exit`), `cli.py` (`vcs-ams`), `personalities/{vcs,simv}.py` (the AMS options,
`-override_timescale`, the plain preprocess, `+vcs+finish+N`, the cosim dispatch), `licenses.json`,
`shims/vcs-ams`, `Job.override_timescale`; tests `test_ams_options.py`, `test_ams_supply.py`,
`tests/vamos/ams_e2e_lib.py`. (The planned `test_ams_flow.py`/`test_ams_e2e.py` became the phase-3 files.)

**Phase 3:** five end-to-end writers (`test_ams_e2e_{basics,ends,supplies,bidir,hier}.py`), then:
- fix round 1 – netlist: the HSPICE maximum step and `(path, origin)` choose netlists; integration: the Xyce
  warning rebuild, the b1/b2/c order with `d2a powernet` supplies, supply nets resolved for the level they
  use, `snps_open` report lines, the Top-Net bit, the choose origin in flow.py; translator: T7–T10; engines:
  the `--stop-time` wrap diagnosed, a patch and eight C-side tests; cut: per-bit Z-only drivers and per-bit
  initializers; verilog: probe step 2b;
- verifier 1;
- fix round 2 – nvc: P8 and P9 applied and nvc-build rebuilt; cut: one-way port buffers, the step-2b report
  wording (`directions=`), `test_ams_e2e_portbuf.py`; tests: the e2e harness (`AmsCase.child_env`), the e2e-24
  loads, stale bug comments;
- verifier 2: the whole suite green (§9). flow.py then passed the probe directions to the cut.

**Phase 4:** an adversarial review of the whole diff, five lenses, every finding re-checked by an independent
skeptic (46 confirmed: one blocker, 24 major, 21 minor), then a first fix round by group:
- cut: real and string parameter overrides on a multi-view cell refused, read from `_mods.vhd`; `x2v=4` per
  PAMS p206; the template's locals renamed `vams_*` and reserved (a port named `v` silently stayed at lov);
- engines: exact end-line times (P1), the Xyce ramp cut and short-ramp stretching (P3), the interrupted end
  line and exit 130 (P1); ten C-side cases;
- netlist: HSPICE's model defaults (`tables.hspice_card`: MOS 1/2/3, BSIM3, D/Q/J, CAPOP, DCAP), the
  coupled-inductor fold, D exponents (§4.1, §4.3.5–§4.3.7);
- verilog: shells.py's docstrings and `README.cut` (and `TestDocs`, which checks them).

The translator and integration findings went to phase 5, with the first round's hand-offs.

**Phase 5:** the final fix round, seven fixers, each owning its files, with a regression test per fix that
fails without it:
- TA (translator: the time-base blocker, `%t` of reals, `$time` rounding, `$timeformat` suffixes, the default
  `%t` unit, scopes in functions, tasks and blocks: T11, T7, T1);
- TB (translator: port networks, tri pulls, T2 on arrays and concatenations, real values, escaped labels,
  variant names: T2–T5, T8, T12);
- TC (translator: case strength, async reset in the NBA shadow, the initializer hoist, `$random`/`$urandom`
  and located system functions, classes and fork, compressed shifts, block-level waits: T13–T20, P10);
- Irun (`backends/cosim.py`, `backends/nvc.py`, `personalities/simv.py`: §6, §8);
- Ideck (`ams/deck.py`, `flow.py`, `supply.py`, `engines.py`: §2.2, §3.3, §4.3.2, §4.7);
- Irules (`ams/rules.py`, `initfile.py`, `report.py`: §2.2, §3.2–§3.6);
- Idriver (`personalities/vcs.py`, `cli.py`, `optable.py`, `tools.py`, `job.py`, `banner.py`, licences: §1.1,
  §1.2, §1.4, §8).

The work lists came from the phase-4 review, the phase-4 hand-offs, a user-guide trial (a fresh agent
followed docs/VAMOS_GUIDE.md literally) and revision 4's open items.

**Repair round** (after phase 5's merge, ivtest gate and document check): one agent fixed what those had
found and most of the fixers' open items, each with a regression test that fails on the pre-round code or
plugin (the four `test_repair_*` files, §9): the gate's failures (the SIGHUP test under nohup, signed `/` and
`%`: T22, and the 126 tests that failed only for want of the resolver module, `bin/vvp-sv2ghdl`), leaked nvc
processes (`vamos_testlib` reaps them), the banner order and docstring drift, and the open items fixed above
(the integration, rules, cut, Verilog-side and netlist items; T21–T24; libsv_math.so; the `Function` trace
filter). It rebuilt and installed tgt-vhdl (§7, Builds); the gate then ran as run 777 (1347/3011, §9). Left
open, with reasons: the 3D-logic doctrine items (vector x/z arithmetic, `===`, the vector case), the
time-step ordering of `vhdl_test2` and `always @` at time 0 (each needs a design-wide change and its own gate
run), the regress harness items, and the nvc library items (§7, §10).

**Upstream merge** (2026-10-03): the other kev-cam work on iverilog and nvc was merged, vamos followed the
translated text it changes, and the hazard3 suite was added (§7, §9: 1347 → 1366 of 3011, nvc/regr 1149 →
1155, no pass→fail).

**Round 6** (on the merged code; paused for the merge and resumed on it): six fixers, each owning its files
and delivering patches against the merged heads, with a regression test per fix that fails without it and
its own gate runs (§9); the patches were then merged into the shared working trees and rebuilt (§7):
- T, translator ordering (`tgt-vhdl` process, statement and memory semantics: T25);
- F, file I/O (`tgt-vhdl/fileio.cc`, the sv2vhdl display and memory-file runtime, the run directory: T26,
  §6);
- W, waves and nvc core (P11; `personalities/vcs.py`, `simv.py`, `backends/nvc.py`, `job.py`: §8);
- L, the sv2vhdl library and its translator side (random generators, switches, vector strength, the resolver
  plugin: T2, T13, T16, P12, P13);
- N, netlist and engines (`vamos/netlist`: §4.3; VACASK and Xyce: P14);
- C, the cut and the harness (`ams/cut.py`, `shells.py`, `vhdl.py`: §3.6, §5.4; `bin/` scripts: T4;
  `regress/`: §9).
The work lists were revision 5's open items and the hazard3 test case's findings (the unused
`hazard3_onehot_priority_dynamic` module that did not translate, the nvc `** Error` lines in every vamos
compile, the accel-jit note in every run, the smak dispatcher's hang).

**Round-6 repair** (after the merged stack's gate): one agent fixed the gate's failures and the round's open
product items, R6R-01–32 (§7), each with tests in `tests/vamos/test_r6_R.py` that fail without its fix,
and gated the result (§9).

**Ordering** (as built): P1 and P6 gated every e2e test (the ABI check refuses an unpatched bridge or engine,
and the end-of-run lines §6 classifies come from the P1/P6 loop); T1 gated the `$fatal` cases of e2e 4; T3 the
Verilog-name rule tests (10, 13); T5 the `tri1` variants of e2e 12; P8 the stops beyond 2^32 fs (e2e 7, 30).
All have landed.

**Open items** (as of revision 6, after round 6 and its repair; each is marked **open** where its section
describes it):
- **Rules:** a wildcard `vdd_port=`/`vss_port=` after `../` is a parse error (it would name a net of the scope
  above, which vamos does not search; §3.3, §3.5).
- **Netlist:** bin bounds read from top-level values only (a clear error); the Xyce elaboration's cost on very
  large flat designs; an F controlled by a V source in an `m=` subckt (HSPICE's answer unknown); which CJ
  HSPICE uses on a MOS 1/2/3 card with neither CJ nor NSUB (a warning, §4.3.6); MOS `sa`/`sb`/`sd` under
  `.option scale` (a warning: HSPICE's rule is undocumented, both targets read them unscaled, §4.3.5); the
  one-sided coupled-inductor multiplier assumes HSPICE's M is one inductor of L/M (§4.3.7).
- **Cut:** a cut-table miss crashes nvc at elaboration, which the compile names as an internal cut error
  (§5.5); an instance whose own wrapper chain did not make an auto port an input shows the reason of the
  instance that did (§3.6); a tran between nets that both reach SPICE ports joins them in one analog node,
  like a wire (§5.4).
- **Translator:** the items listed under §7 (by design: `===` x/z, the vector case, vector x/z arithmetic,
  the x/z index; open: a continuous-assignment copy updated a delta after a blocking write, void functions,
  `final` and NBA event controls as translation errors, specify delays, timing checks and `$sdf_annotate`
  not simulated, a single top's inputs reading 0, named events across scopes (`scoped_events`), the memory
  of nets in pr1703346, the fused cone under force/release, the file-task gaps, the waves' names and timing,
  a tran on a select of a module port, …); a signed actual narrower than a buffered input port is
  zero-padded, not sign-extended (the iverilog core does so, and vvp with it, T8).
- **Engines and test infrastructure:** `utils/test_simetrix_cosim/README.md` describes only the veto; the full
  Xyce regression suite was not run on the patched libraries (P1's library is bit-identical on decks without
  step-answering libraries; P14's change was checked on a 23-test subset); the Xyce units that include
  `N_DEV_SourceData.h`, other than the recompiled ones, are stale but compatible (§7, Builds); Xyce stops with
  "Time step too small" where a node with no capacitance meets a fast edge (a 10 ps D2A edge in e2e 24, whatever
  the maximum step; the floating series-stack node of PAMS p27), where VACASK runs both, and vamos adds no
  capacitance (e2e 24 carries 1 fF loads); `update_d2a_bridges` costs O(D2A count) per digital time point and
  thousands of active boundaries are not benchmarked; "co-simulation stalled" is exercised only through a
  stub engine; the cut fixtures (all but `cut_portbuf`) predate the translator patches and were not
  regenerated; a plain nvc run still takes a second pending SIGINT as "quit now" (exit 1, no end line; vamos
  sends exactly one); `cosim.scan_raw` repeats `rawfile.py`'s rules (kept equal by tests, §4.6); Xyce's diode
  computes no junction charge when CJO=0, written around (`cjo=1e-30`, §4.3.6). Harness: ivtest's
  `vvp_tests/*.json` cases (fdisplay3, fmonitor1/2, readmem-invalid, writemem-invalid, …; `regress-vvp.list`,
  `vvp_reg.py`) are run by no block: ivtest/iverilog-nvc runs `vvp_reg.pl` over its five lists, so those
  cases are not exercised on the sv2vhdl runtime, filtered or not; smak (a separate repository) ignores a
  rule line that starts with spaces (Verilator's makefiles), uses a pattern rule whose prerequisite cannot
  be made, hangs a recursive smak on the parent's job server and leaves its daemons running – the harness
  gives every block a clean make environment, ends the dispatcher's session and falls back to GNU make (§9).
- **Docstring drift** (the code is right; fix the text with the next change). Frozen modules: `ir.py` says a
  `y` master is "model card or module" (it is the module, §4.2), and it documents neither the probe targets
  `'a,b'`/`'*'` nor that names and `Subckt.orig_ports` are folded per `set_sim_case` (it says lowercased);
  `expr_ast.Name` is commented "lowercased identifier" (names are folded per `case`); there is no
  `AmsConfig.skip_nodes` (NaN sentinel), no `RuleHits` seen-name registry (key prefixes), no
  `Netlist.left_out` field (dynamic attribute), no `CutCell` field for port_connect'ed ports
  (`ShellResult.removed`). Other modules: `expr.py`'s module and `number()` docstrings say `parse_number`
  multiplies two rounded values (§4.1); `test_ams_rules_fixes.py`'s module docstring says a wildcard
  `vdd_port=`/`vss_port=` stays an error (only one after `../` does now, §3.5).

Fixed in phases 4 and 5 and removed from this list: the phase-4 items of revision 4 (T7's cast, the empty
`$timeformat` suffix, `$time` truncating); flow.py's `-top` checks; plain mode's silent untranslated tasks,
deferred tops and footer; the `-v` rule in plain mode; the left-out-subckt reason; `port_connect -inst`, nets
with no source, ground-alias ports; `ie_reference_voltage`'s application, order and TNF; the unevaluable
supply; the range-parameter exemption; XA probe patterns and `v()` saves; `midv_logic=Z`; `map_by_node`; XA
cfg path expansion; absolute levels beside `vdd=`; T2 on concatenations; T3 escaped names; undriven `wire
real` nets and real-port arithmetic; `README.cut` and the shells.py port-buffer wording. Fixed in the repair
round: the `-v` rule in AMS mode; a copied VACASK daidir (relative `.osdi` loads); `--vamos-strict` on the
translator's warnings; the deck and smoke writers and the e2e report reader under a non-UTF-8 locale; the ams
record's TSTART; the IE report and `ams.json` as compile stages; simv's message for a failed compile; the
wildcard `vdd_port=`/`vss_port=`; the unevaluable-supply wording and ground as a 0 V supply; `cut._passive`
and the cut guard's wording; the shell's header functions; the cut-table miss named; the `Function` trace
line; the gate's resolver; Xyce's CJO=0 diode and VACASK's kappa=0; the MOS MJSW default and the CJ warning;
memories at any base, signed `/` and `%`, comparisons shown, `task automatic`, `disable`/`return`, the
sv_math functions (T21–T24, §6); the docstring drift of `config.py`, `model.py`, `shells.py`,
`cut.param_overrides` and `test_ams_e2e_portbuf.py`. Fixed in round 6: the plain-mode footer of a run that
ran out of events before its `+vcs+finish+` time (P11, §8); HSPICE's MOS CJ default, FC and PHP, COX/CO,
LEVEL 3's channel-length modulation, the BSIM3 junction defaults, the R/C wire parameters, `i()` targets,
node names with `.` or `:`, VACASK's nested-subckt scoping, the PULSE defaults (verified), `mutual` over
`sp_inductor` (verified), `.option aspec` (§4.1, §4.3); trans and aliases as net joins, the quantised round
trip beyond one hop, each instance's own step-2b reason, `begin`-block declarations, the real-port
backstop, the port spelling under `set_sim_case`, a SPICE output only a register reads (§3.1, §3.6, §5.4);
the translator items of §7's list (T2, T13, T16, T25, T26) and waves (P11); a co-simulation's second SIGINT
(P11); the resolver plugin's SIGINT and its errors on stdout (P12); the harness's work directories, the
`--filter` that ran everything, the filtered lists and the make environment, and the "flaky" list, which does
not exist (br918a, br918b, br_gh99s, force_release_wire8_pv, pr1032 and pr2725700a pass in run 105 and in
round 6's full runs, §9); the engine defects (P14: the diode sidewall's F1,
LEVEL 3's NaN at KAPPA·alpha = 0 and its 1.8 % channel-length difference). Fixed in the round-6 repair:
`$dumpfile`/`$dumpvars` in an AMS design and the A2D of a cut output only the waves read (R6R-16); the
reserved-word top (R6R-13); an interrupted co-simulation's extra `** Fatal: …: interrupted` report (R6R-22);
`.option delmax` without a `.tran` (R6R-17); the note for `+vcs+dumpvars` beside a guarded `$dumpvars`
(R6R-19); `Subckt.spelling` and the cut guard's example (R6R-18, §5.4); the `nvc/_norm.sv` locations of
the run's messages (R6R-20) and `%m` per instance (R6R-07); the §7 list's Rule 6 width, bit stores outside
a vector, undriven vector bits and vector-intrinsic crash (R6R-11, R6R-02, R6R-08, R6R-24);
iverilog-sv2ghdl's exit status for a deferred top (R6R-12); the harness's `SV2VHDL_FILE_DIR`, `-B` and
iverilog-PATH items (R6R-14, R6R-15, R6R-25); `TestE2E03Dac`'s stale expectation. Revision 6 also fixes
the docstring drift of `tables._junctions`, `test_translator_semantics.py`'s `TestURandom` and
`tests/hazard3_mandelbrot/README.md`.

## 11. Changes

**Revision 1 → 2** (the review): per-variant clones with `'path_name` lookups replaced per-instance clones
(blocker); the bidirectional element's terminals fixed and every D2A gated; P1's precise veto/finish rule,
`cosim_run` re-entry, the VACASK t=0 call and an ABI handshake; P6; positional port identity; T3 Verilog
paths; VCS alias matching with VCS globs and TNF; the direction probe; per-bit markers on outputs too;
union-find nets with `_Readable` shadows and library primitive modes; masking and shells on the
preprocessed stream with the top fixed first; the netlist rules (evaluator, parameters, ground pass,
options, model dispatch, binning, `$mfactor`, sources, HSPICE functions, file handling, no Xyce flags); the
runtime (per-run directories, `--work`, end-of-run classification, truncation check); `-override_timescale`;
the `-ad` eq option; the phase-0 contracts; the test matrix.

**Revision 2 → 3** (the audit): the time base (tgt-vhdl compresses at ≥ 1 ms precision: refused in AMS mode)
and Verilog ranges (VHDL ranges are always `(w-1 downto 0)`: Verilog bounds from the header and
`nvc_verilog_params`) – both blockers; `shell_dir`; host-only roles (`ROLE_PASSIVE`); an ordered role list
with pulls as weak drivers, BIDIR for pull-driven auto/inout nets, `remove_d2a` removing only a D2A, real
ports and passive bits; port temporaries and user-module port maps as net joins; the staged supply trace,
constant vs dynamic supplies, `skip_node`, powernet-first evaluation; the xband window; the IE report
content; shunts on every bridged node and enables at 1.0 in the smoke deck; full `code_uri` strings and
caller-chosen emitter paths; `.option scale` applied once by the emitters; IR vocabulary (`Source.args`
keys, `points`, `orig_ports`, `ParseOpts`); the ground pass for E/H/F/K, generated names and `.global`
ports; per-instance `portmap`/`subckt`; parameter overrides on cut instances; `$fatal`/`$error`/`$warning`/
`$info` (T1), part-selects (T2), `tri0`/`tri1` (T5), real-value clamp (P7), boundary parsing limits (P3/P4),
ramp details (P3), the digital stop time and the t=0 stop line (P1); classification before the output
filter; the ABI check without loading libraries; `Job.schema`/`ams_control`/`precision`; the phase-0
additions `globs.py`, `engines.py`, `names.build_bridges`/`boundary_line`, `RuleHits`, `CutEmitResult`;
the phase-1 API block (§5.8); and the corresponding tests.

**Revision 3 → 4** (as built): the document now records the implementation. Flow: preprocessing through an
`ivlpp -L` wrapper with an origin map, `vamos_prelude.v`, the plain-mode `pp/pp.v` that records the precision,
the precheck after the top, choose netlists with their origin, the left-out-cell check, and the step-13 TNF.
Control file: the as-built dispositions (`-afile` a warning, `-spice` a mode note, `use_veriloga` and
`dynamic_supply_filter` errors, the added warning/note families, SPICE-only port maps limited to renames, the
`skip_node` NaN sentinel). Rules: the role filter, `resolve_detail`/`paste_line`/`check_rule`, the reserved
`node:`/`cell:` seen-name keys, the key dispositions (`except_port=`, `vdd_port=`, `queue=`, `ceff=`,
`midv_logic=Z` as X). Reference supply: the b1/b2/c order with `d2a powernet` nodes as supplies, constant when
the digital driver is a proved constant and varying otherwise, and supply nets resolved for the one level they
use; the report lines as built (levels, supply, snps_open, port buffer, the Top-Net bit, the direction
reasons). Netlist: correctly rounded numbers (`expr.number`), the printer rows as verified (`integer()`,
floor-based truncation, the `pow` T(y)==0 guard, the VACASK `atan2` formula, Xyce `!` and truncation, large
literals, `param_ident`), deferred library errors, HSPICE `.tran` intervals, the maximum step
min(TSTOP/50, RMAX·TSTEP) and the `delmax`/`rmax`/`dvdt`/`lvltim`/`accurate` dispositions, the PWL first point,
`sp_bsim4v8` for level 54 with a string version and version notes, BSIM3 version handling, the Xyce
`vamos_mfactor`, level-3 diodes refused on Xyce and scaled only at level 3, Xyce per-path binning with
`__vb<k>` copies, the Xyce PWL delay shift, VACASK `NOT_GIVEN` dependent parameters, reachable-only printing,
the `vamos_*` builtin model names, `bg_<n>`. Cut: probe placeholders, non-ANSI shells, probe step 2b, the
`_<n>` name suffix, per-bit Z-only drivers, per-bit declaration initializers, port-temporary chains, one-way
port buffers and their errors, the D2A host rule, clone and package names, de-duplicated level sets, the
template decisions. Runtime: the Xyce warning rebuild, the end-line forms, the VACASK one-point t=0 rawfile
(resolving the contradiction between §6 and §9 item 4), the `$error` exit remap, the footer time, the stale-raw
removal, the run directory beside the output prefix. Patches: P1 as built (`CosimStepResult`, the
multi-library rule, `xyce_lib_cosim_abi` through a weak reference, the order of work), P3/P4/P5 error
behaviour, P8 (`parse_time` 64-bit), P9 (`sv_tstr` string scaling), T1–T5 as built and T7–T10 (the `%t`
widths, driven input ports and `PB_` port buffers, unique labels, declared formal names). vcs: `+vcs+finish+N`
with the 1 ms compression cap and the plain-mode preprocess. Tests: the files that exist, e2e items 1–31. Open
items are marked and collected in §10.

**Revision 4, checked** (before phase 4): two checkers compared revision 4 with the code (the vamos modules,
the `bin/` scripts and the nvc, VACASK, Xyce and iverilog diffs) and with the phase-1 to round-2 reports,
re-running the doubtful cases; every confirmed finding is folded in. Corrected to what the code does:
`parse_number` is correctly rounded (`numbers.py` amended, §4.1); report.py writes its own lines and
`paste_line` differs in form (§3.6); a cut cell bound to a left-out subckt fails at step 6, and the
revision-4 left-out-cell check never fires (§4.3.2); a ground-alias `port_connect` port is ignored, not an
error (§2.2); an `ie_reference_voltage` entry needs a trace that finds a hit (§3.3); nvc's own ABI fatal is a
run with no end line, and the ABI check has three messages (§6); five whole-design elaborations (§5.1); P8's
digit loop; FSDB/WDF/tr0 output, `.width`-class dot-commands and the note-class control-file commands are
notes (§0, §4.3.2); `v(a,b)` saves silently (§4.4); the variant number `<v>` in the cut package names, the
parents' pull comment, the foreign-statement note and the bridge-name and boundary-limit wording (§5.4,
§5.5); the `use_spice -inst` coverage rule, the silent `snps_by_name` default, the choose error text,
`-nspice` as a netlist option, the `port_dir` check scope and the `skip_node` error (§2.2); the
multi-view parameter-override exemption, a substring test that does not use `range_params` (§0, §4.7, §5.1);
the selector-shape and wildcard-supply errors (§3.2, §3.5); the canonical-name rule (fewest labels, §3.1);
the `remove_d2a` report line (§3.6); the openvaf-r fallback and the VACASK binary default (§6); P4's messages
without file:line and `COSIM_BRIDGE_ERR_NAME`; `optable`'s assertion (§8); `vhdl_target.h` in T5/T8; the
`coretran.cpp` line; `sim_time_text`'s signature (§5.8). Newly recorded: the handling of `-v` files (§1.2);
the PULSE `pw`/`per` decision (§4.3.8); the BSIM4 engine messages and the diode-current difference
(§4.3.6); the rawfile reader's limits (§4.6); the T4 script details, the P9 corner cases and the stale Xyce
objects (§7); and the open items – case equality, `always @` at time 0, undriven `wire real` nets and
real-port arithmetic, `===` printed as true/false, a weak x shown as z, the part-select `tran` temporary,
T7's lost sub-precision digits, block-local declarations, the plain-mode deferred top and footer, XA cfg
paths, absolute levels beside `vdd=`, `delmax` without a `.tran`, `i()` in behavioral expressions, the
netlist items not verified on the engines, the Xyce capacitance-free node, the pre-patch cut fixtures, and
the docstring drifts (§10).

**Revision 4 → 5** (as built after the review fixes, phases 4 and 5, §10):
- Time and translation: one tick of every precision of 1 ms or finer at its SI size (the blocker: 1ns/10ps,
  1ns/100ps and 10ns/10ns ran the digital 10× or 100× fast against the analog, silently; §1.2, T11); `$time`
  rounds; `%t` of reals through P9's real overload (no INTEGER limit, finer `$timeformat` digits, ties to
  even) in the design's precision by default; `$timeformat` suffix escapes; scopes of functions, tasks and
  named blocks (T1, T7, T11); port networks behind the core's port buffer, with errors for the shapes not
  covered; tri pulls on `PB_` and T2 aliases; T2 on instance arrays and concatenations; escaped labels; the
  translator's warnings surfaced under vamos; `_metadata`'s stage line (T2–T5, T8); real arithmetic, real
  casts, undriven real nets, exact real parameter lines and variant names (T12); strength-free case
  statements, the async reset in the NBA shadow, the time-zero hoist, `$random`/`$urandom`/`$urandom_range`
  and located replaced functions, classes and fork as located errors, sensitivity lists of null processes,
  compressed shifts, block-level `@(a)` (T13–T20); `libresolver.so`'s `sv_random` (P10). The open translator
  items, including those the working `$random` exposed, are listed in §7.
- Engines: exact femtosecond end-line times, the interrupted end line and exit 130 (P1); the Xyce ramp cut
  and short-ramp stretching with its warning (P3); P9 now used by the translator.
- Runtime (§6): testbench output never classified or filtered, tool lines matched whole and failures named
  by their line; the SIMULATION FINISHED remap limited to nvc's exact line; the O(1)-memory rawfile check;
  TSTART; the 2 fs finish floor; the interrupted end line; signal handling with nvc in its own process
  group; the ABI check reading ELF symbol tables (no nm) where nvc's dlopen looks; the footer rules; concurrent
  runs; `+vcs+finish+<time>` forms, simv `-h` and its messages (§8).
- Control file, rules and report (§2.2, §3): `map_by_node r=`; `midv_logic=Z` applied; absolute levels
  beside a supply refused; XA cfg paths expanded; `ie_reference_voltage` as trace hits that outrank sources,
  last wins, never TNF when the node exists, unreached entries warned; unevaluable supplies an error; the IE
  report written as UTF-8 by one writer (`rules.ie_lines`/`paste_line`), supply nets with pasteable
  `d2a powernet` lines, every report line kind pasted back reproducing the deck.
- Deck and flow (§1, §4.3.2, §4.7): `port_connect -inst` wiring and net resolution with errors for nets not in
  the deck, ground-alias ports, floating `port_connect` nets warned; parameter overrides of every type, with
  the range exemption by whole identifiers (`deck.range_params`); `v()` saves checked and port aliases saved
  by their owning node; XA probe patterns; the `--vamos-analog-maxstep` and clamp notes; `-top` checked by
  `find_top`; the left-out subckt's reason; engine overrides checked (`engines.problems`); the variable
  warning only for two or more SPICE ports.
- Netlist (§4.1, §4.3.5–§4.3.7): HSPICE's model defaults written for the SPICE3 and Berkeley targets
  (`tables.hspice_card`), CAPOP and DCAP dispositions, `.option dcap`/`.option spice`; coupled inductors with
  their multiplier and TC folded into the value; D exponents.
- Cut (§5.4, §5.5): every parameter type read from `_mods.vhd` for the override check; `x2v=4` per PAMS
  p206; the template's `vams_*` locals reserved.
- vcs personality and driver (§1.1, §1.2, §1.4, §8): the refusing stub before a compile and the job record
  last; a stub that runs the daidir beside it; plain compiles through `pp/pp.v` with VCS's `-v` rule, every
  uninstantiated module a top (the `vamos_tops` wrapper), a deferred top an error quoting iverilog,
  untranslated tasks and functions warned; the `--vamos-*` namespace checked; tool overrides checked and
  versions parsed; help texts that point at docs/VAMOS_GUIDE.md; messages; licences naming the forks.
- Repair round (§10): the AMS `-v` rule and the precheck on the masked stream (§1, §1.2); relative `.osdi`
  loads, so a copied VACASK daidir runs (§4.4, §4.7); the translator's warnings as vamos warnings that
  `--vamos-strict` escalates (T4, §8); UTF-8 deck writers (§4.4); the record's `start`, the IE report and
  `ams.json` as stages, simv's "holds no finished compile" (§1.1, §3.6); wildcard `vdd_port=`/`vss_port=`
  matched against the subckt ports, ground as a 0 V supply, the unevaluable-supply wording (§3.3, §3.5);
  `cut._passive` naming the subckt, the guard's and the cut-table miss's wording (§2.2, §5.4, §5.5); the
  shell's header functions (§5.1); libsv_math.so loaded and the `Function` trace dropped (§6, T1); Xyce's
  CJO=0 diode, VACASK's kappa=0, the MOS MJSW default and the CJ warning (§4.3.6); T21–T24 (memories at any
  base, signed `/` and `%`, comparisons shown, `disable`/`return` and `task automatic`); the gate's resolver
  in `bin/vvp-sv2ghdl` (§9); newly recorded open: `repeat` after a wait, x/z arithmetic, out-of-range memory
  reads, `break`/`continue`, `$dist_*`, VCD waves (§7).
- Tests (§9): 1360 tests in 42 files (1296 in 38 before the repair round); e2e items 32–37; the phase-4
  C-side cases; the phase-5 ivtest runs; the repair round's gate, run 777.
- §10: the phases, the remaining open items (integration, rules, netlist, cut, translator, engines and
  infrastructure, engine defects found on the way) and the docstring drift, rewritten.

**Revision 5 → 6** (the upstream merge, round 6 and its repair, 2026-10-03):
- Upstream merge (§7): iverilog c9726d635 (20 upstream commits: the new reserved words, merged same-edge
  always blocks, generate-scope names, `$readmemh`/`$readmemb`, …) and nvc 399d83df8 (`$readmemh` in
  logic3d_types_pkg, an `rt` wait fix); vamos follows the translated text (e654556); the hazard3 suite
  (1536120). ivtest/iverilog-nvc 1347 → 1366 of 3011, nvc/regr 1149 → 1155, no pass→fail (§9).
- Translator (§7): T25 – blocking assignments in initial blocks and in always blocks that wait inside their
  body are deposits, read back at once, so nothing yields inside a Verilog time step (vhdl_test2); an
  `always @(…)` waits for its first event, and initial blocks start a delta late when one assigns at time
  zero; `repeat (n)` after a blocking assignment; `break`/`continue`, `do … while`, `$dist_*`; an automatic
  task called from several processes; a named-block local read from another process; memory reads and
  stores outside the memory; memory-word selects at the select's width; `$set_val` on memories; strength
  buffers on memory words. T26 – file I/O as vvp does it, relative names from where `./simv` starts. T16 –
  `$random(seed)`, `$urandom`, `$urandom(seed)` and `$urandom_range` draw vvp's numbers. T2 – a tran on a
  select of a net is joined both ways; arrays of switches. T13 – vector strengthening. T4 – a back-end error
  with exit status 0 defers the module; stage 1a's nvc errors become notes when the whole design analyses.
  T18, T20, T22 and T24 amended; the open-items list rewritten, with the round's new findings (sv-normalize
  Rule 6's width loss, `%m` in a module instantiated twice, a reserved-word top, pr1703346, nvc's vector
  intrinsics, the fused cone under force/release, the remaining file-task and wave gaps).
- nvc and engines (§7): P10 corrected (plain-VHDL `random` since the push); P11 – four-state VCD waves,
  `--dump-scope`, the end-time line, a co-simulation's second SIGINT, VHPI with a Verilog top, `--std=2040`
  character literals, temporary directories, accel notes; P12 – vvp's seeded and `$urandom` generators,
  tranif x/z control, the resolver plugin's SIGINT and stderr errors, `l3d_mod_s`, vector `l3d_strengthen`;
  P13 – the kernel net solver's member hook (tri-state nets joined by a tran); P14 – the diode sidewall's
  own F1 and LEVEL 3's `badmos3` and NaN guard, in VACASK and Xyce.
- Runtime and personality (§6, §8): `NVC_REPORT_END_TIME` for the footer (a run that ran out of events shows
  its last event); `SV2VHDL_FILE_DIR`, a plain run's nvc in the start directory, the resolver's cache and work
  library kept in the daidir; the plugin load order no longer matters; waves (`vcs.dump_request`,
  `_Conditions`, `Job.dump`, `simv.wave_request`, `+vcs+dumpvars`, `+vcs+dumpfile+`, `-vcd`,
  `+vcs+dumparrays`, the noted `+vcs+dumpon+`/`+vcs+dumpoff+`/`+vcs+flush+dump`); the `-gui` and
  `-debug_access` notes; the testbench's file paths.
- Netlist (§0, §4.1, §4.3): HSPICE's MOS CJ default (the ASPEC=0 formula), FC=0 and PHP removed with their
  warnings; COX to TOX; LEVEL 3 `badmos3=1` (the kappa=0 workaround gone); BSIM3 CJ/CJSW with HSPICE's own
  junction model (the ACM=10 `js=0` rule gone); R/C wire cards mapped per engine or refused; `i()` targets
  checked; `.` and `<x>:<node>` net names refused; `.option aspec` refused; SA/SB/SD under scale warned; the
  bin-bound error worded; VACASK's nested-subckt scoping an error; the PULSE defaults and `mutual` over
  `sp_inductor` verified.
- Cut (§1.3, §3.1, §3.6, §5.2, §5.4): trans and aliases as net joins (supply excepted); process-variable reads
  as readers; the quantised round trip through combinational chains; `begin`-block declarations; the
  real-port backstop; the port spelling by the folded name; each instance's own step-2b reason; the AMS
  refusal of a module whose back end reported an error.
- Round-6 repair (§7, the R6R rows; §9): out-of-range and x/z-marked selects dropped as vvp drops them,
  `$sformat`, bare `$display` arguments, `%m` per instance, undriven vector bits z, `$finish(0)` quiet, an
  NBA in a depositing process, and `final` blocks, void functions and NBA event controls as located errors
  (tgt-vhdl); a quiet co-simulation interrupt, the memory-of-nets element nets and an aligned eval arena
  (nvc); the Rule 6 probe, the deferred top's exit status, the top's entity, `SV2VHDL_FILE_DIR` in
  `bin/vvp-sv2ghdl`, no foreign `-B`, specparam and comment fixes (scripts); AMS `$dumpvars`, `delmax`
  without a `.tran`, `Subckt.spelling`, run messages at the user's file:line, and warnings for the timing
  the simulation leaves out and for a single top's inputs (vamos); the x/z index and the
  continuous-assignment copy recorded (§7). ivtest/iverilog-nvc 1445 → 1875 (§9).
- Tests and harness (§9): 1667 tests in 51 files (the six `test_r6_*.py` files, 239 tests, the merge's
  `test_r6_merge.py` and the repair's `test_r6_R.py`, 48); e2e items 38–40;
  each fixer's gate runs and the merged stack's; the harness's work directories, private ivtest areas,
  filters and smak dispatch; the hazard3 suite.
- §10: the upstream merge, round 6 and its repair recorded; the open items and the docstring drift brought
  up to date.
