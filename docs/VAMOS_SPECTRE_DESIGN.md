# vamos `spectre`: design

Status: design, revision 2, 2026-10-01. This is the implementation contract for the `spectre` personality.
It reuses the analog netlist package built for `vcs-ams` (`VAMOS_AMS_DESIGN.md` §4: `vamos/netlist/`) and
follows that document's conventions: the one rule, one disposition per construct, frozen phase-0 contracts,
and agents that own files. No Spectre binary or licence is available here, so fidelity rests on three kinds
of evidence. Load-bearing statements carry a tag. Revision 2 folds in three reviews of revision 1 (Spectre
fidelity, engine mapping, implementability); §15 lists what they changed.

| tag | evidence |
|---|---|
| **[M]** | manual text: `ref19` = Spectre Circuit Simulator Reference PV19.1 (Jan 2020), `UG` = Spectre User Guide PV5.1.41 (Jul 2004), `ref5` = Spectre Reference PV5.0 (Sep 2003, component chapters). Page numbers are the manuals' own. |
| **[S]** | real Spectre output files: psf_utils `samples/` (Spectre 15.1, 19.1, 20.1, 23.1) and psf-parser (MIT) `tests/data` (Spectre 23.1.0.242.isr1) |
| **[E]** | an experiment on the installed engines, or a check of the vamos code and tools, during this design and its review: VACASK 0.3.4-91-g64489cf7 (`/opt/build.VACASK/Release`), openvaf-r 20260616, Xyce DEVELOPMENT-202609292309 (`/usr/local/src/xyce-build`). §13 lists them. |
| **[I]** | inferred, not verified. Each one has an entry in §14 (open questions) and, where it matters, a fallback that is loud rather than silent. |

## 0. Goal, scope and the one rule

`spectre [options] [netlist]` (through a `spectre` shim, `vamos -spectre …`, or ADE configured to call it)
reads a Spectre netlist and runs its analyses on **VACASK by default**, with **Xyce as an option**
(`--vamos-analog=xyce` or `VAMOS_ANALOG=xyce`, as in vcs-ams). The netlist may be in the Spectre language,
contain `simulator lang=spice` sections, or be a SPICE-mode file. vamos writes what Spectre writes, so that
ADE-style flows and scripts work unchanged:
- the results directory (`%C:r.raw`) holding `logFile` and one PSF ASCII file per analysis, or one nutmeg rawfile;
- the `+log` file, ending with Spectre's trailer;
- state files (`write=`/`writefinal=`);
- Spectre's exit status (0/1/2/3).

**The one rule: never silently mis-simulate.** Every construct gets exactly one disposition, with the
severities of `VAMOS_AMS_DESIGN.md` §0:

| severity | meaning |
|---|---|
| (silent) | applied or mapped faithfully |
| `note` | informational; results and outputs are unaffected |
| `warning` | an approximation, or a requested output vamos does not produce; `--vamos-strict` turns it into an error |
| `error` | the run fails with exit status 2, naming the construct, its origin and, where there is one, the fix |

Spectre-specific readings of the rule:
- An option with no effect here (licensing, threading, GUI, accelerator presets) is ignored silently only if it
  cannot change results or outputs. Otherwise it gets a note.
- A requested output vamos cannot write (an `.info` file, an operating-point save, a power waveform) is a
  warning or a note naming it. It is never just missing.
- Where the manuals say Spectre rejects a construct (units without a scale factor, a sweep that changes a
  structural condition), vamos rejects it too, rather than invent a result.

**v1 includes:**
- the command line: every option of the ref19 command-option chapter accepted with a disposition (§2.3),
  including the abbreviations, the `=` family, `+%X`/`-%X`, `%S_DEFAULTS`/`SPECTRE_DEFAULTS`, `-raw`,
  `-format`, `-outdir`, `±log`/`=log`, CPP (`-E -D -U -I`), `+config`/`+pre_config`, `+paramdefault`, `-mts`,
  `-V`/`-W`/`-help`, a netlist read from stdin, and Spectre's signals (INT, TERM, HUP, QUIT, USR1, USR2);
- the Spectre language (§3): title line, comments, both continuation forms, escaped names, case-sensitive
  names and `insensitive=yes`, Spectre scale factors, `simulator lang=` switching, SPICE-mode files and
  sections (through `spice.py`), `include … section=`, `library`/`section`, `ahdl_include`, parameters and user
  functions, `subckt` and `inline subckt`, models and model groups (auto binning), structural `if`,
  `global`/ground, `ic`, `nodeset`, `save`, `options`, `set`, `paramtest`, and `alter` (dev/mod/sub/param/temp);
- primitives: `resistor capacitor inductor vsource isource iprobe vcvs vccs ccvs cccs mutual_inductor diode
  bjt jfet`, model masters `mos1 mos2 mos3 bsim3v3 bsim4`, and Verilog-A modules;
- analyses: `dc` (operating point; sweeps of a device, model, subckt-instance or netlist parameter, and of
  `temp`), `ac` (frequency sweeps, and parameter sweeps at a fixed frequency), `noise` (a node pair, a
  two-terminal output probe or a controlled source's output port; an optional vsource or isource input
  probe), `xf` (VACASK only; a node pair or a two-terminal probe), `tran`, `sweep` (nested, several
  children), and the SPICE-mode `.op`, `.dc`, `.ac` and `.tran`;
- outputs: a PSF ASCII results directory (written for every `psf*` request), nutascii/nutbin, Spectre
  signal names, sweep parent and child files, state files, and the `+log` file.

**Approximations (warnings) in v1:**
- a pulse `rise`/`fall` that is omitted or 0 becomes `transres` = 1e-9 × the shortest `tran` stop (§3.8.1).
  Spectre's default is undocumented; VACASK rejects a zero rise and reads a zero fall as "no fall";
- `prevoppoint=yes` and `useprevic` on Xyce: the operating point is recomputed (VACASK maps them, §5.4);
- a diode card without `eg` simulated at a temperature other than its `tnom`: Spectre's default `eg` is
  temperature dependent, and vamos uses its 27 °C value (§3.9);
- the `strobe*`, `infonames`/`acnames`, `currents=all|nonlinear`, `subcktprobelvl>0` and `pwr` outputs
  are not produced.

On Xyce, the tolerance and accuracy settings (`reltol vabstol iabstol`, the accuracy part of `errpreset`,
`relref`, `lteratio`) are not passed on; only the integration method is (§5.4). That is a note, not a warning:
Xyce's tolerances have other meanings (the vcs-ams rule).

**Not in v1** (each is an error unless marked):
- `montecarlo` (statistics blocks are parsed and are harmless when no Monte Carlo runs; §3.12 and §12 phase 3
  sketch the lowering); `altergroup`; `paramset`; sweep `hysteresis=yes`;
- the analyses `acmatch dcmatch sp stb pz pss pac pnoise pxf pstb psp qpss qpac qpnoise qpxf qpsp hb hbac
  hbnoise hbsp hbstb hbxf envlp tdr thermal reliability stress loadpull lf cosim uti sens`;
  `info` (note: the `.info` file is not written); `check`, `checklimit`, `assert` (note: not evaluated);
  `shell` (warning: the command is not run);
- `xf` with `--vamos-analog=xyce`;
- the primitives `bsource port nport tline mtline switch relay transformer core winding delay a2d d2a
  fourier intcap nodcap quantity node`, the polynomial and s-/z-domain controlled sources (`pvcvs svcvs
  zvcvs …`), nonlinear `coeffs=` on R/C, a series `r=` on an inductor, a resistor's third terminal and a
  jfet's fourth;
- device models with no faithful target: `bsimcmg psp psp103 hisim bsimsoi b3soipd btasoi ekv vbic bjt50x
  hbt mos9xx mos11xxx juncap tom2 tom3 hvmos gaas misnan …` (later through Verilog-A/OSDI); a diode `level`
  other than 1;
- source features `pwlperiod twidth ampl2 freq2 sinephase2`, AM/FM modulation, `tc1`/`tc2` ≠ 0;
  `noisefile`/`noisevec` while a `noise` analysis runs; `xfmag` ≠ 1 while an `xf` runs;
- device `ic=` on a capacitor or inductor (unless every `tran` uses `ic=dc` or `ic=node`), inductor-current
  initial conditions (`L1:1=…`), `force`/`readforce` other than `none`;
- `tran`: `start` ≠ 0, `tpoints`, `skipdc` values other than `no`/`yes`, transient noise (`noisefmax` > 0),
  dynamic parameters (`param=`/`param_vec`), the custom-errpreset parameters `maxstepratio`/`reltolratio`;
  `readtime`;
- an `options` statement inside a subckt (Spectre scopes it to that subckt), unless `-mts` is given (§3.6);
- a sweep or alter value that changes a structural `if` branch or a model-group bin (§3.7);
- `simulator` parameters other than `lang` and `insensitive`;
- saving `X:oppoint`, operating-point variables (`M1:gm`) or `:pwr` (warning: not written); `probelvl`
  (warning);
- a hierarchical node reference on an instance terminal (`r1 (x1.mid 0) …`); encrypted content;
- `+interactive`, `+top`, `-format uwi`, and an unknown `-format` value (errors); `+recover=f` and MDL
  (`+mdlcontrol`, `=mdlcontrol f`) (warnings: a fresh run, no measurements);
- output formats `psfbin psfbinf psfxl sst2 fsdb fsdb5 wdf wsfbin wsfascii awb tr0ascii`: psfascii is written
  instead (note).

## 1. Flow

```
spectre [opts] [input]                                                         personalities/spectre.py
 1. argv     <prog>_DEFAULTS | SPECTRE_DEFAULTS tokens, then argv → SpectreJob (§2);
             -V/-W/-h without a netlist end here                                  spectre/args.py
 2. run dir  beside the results path that argv and the defaults give (below)
 3. input    the netlist (stdin is copied to <run>/stdin, %C = stdin); +pre_config/+config
 4. CPP      only with -E, or -D/-U/-I without -disableCPP: cpp → <run>/cpp.out    spectre/cpp.py
 5. parse    Spectre/SPICE text → IR with ordered statements; language and title per file (§3.1)
                                                                                 netlist/spectre.py (+ spice.parse_fragment)
 6. settings defaults < netlist options < command line (§2.2)
 7. engine   --vamos-analog > VAMOS_ANALOG > vacask
 8. plan     statements → RunPlan in IR names: actions, sweep contexts, variables, per-analysis settings,
             paramtests, the condition/bin check (§5)                             netlist/plan.py
 9. signals  saves → Refs, probe insertion in an IR copy, SignalMap (§6)          netlist/signals.py
10. VA       ahdl_include → openvaf-r → <ahdllibdir>/<stem>_<sha1>.osdi (VACASK)
11. run      render translates IR names and records every column in `names`. VACASK: one deck, one run.
             Xyce: one deck per analysis, each scanned for unknown parameters (§7)
                                                                                 spectre/run_vacask.py, run_xyce.py
12. results  engine output → AnalysisResults through `names` (sweeps split, noise/xf transformed) (§8)
                                                                                 spectre/results.py
13. write    <raw>/logFile + one PSF file per analysis, or one nutmeg file; state files (§8)
                                                                                 output/{psf,nutmeg,statefile}.py
14. finish   the log trailer and the exit status (§2.7)                           spectre/log.py, spectre/flow.py
```

- **Run directory.** `tempfile.mkdtemp(prefix="<%C:r:t>.vamos.", dir=<the directory of the results path>)`,
  created before the input is read, from the results path that argv and the defaults give (§2.5). An `options
  rawfile=` found by the parse moves the results, not the run directory. It receives the stdin copy `stdin`
  (its relative includes resolve against the cwd) and the cpp output `cpp.out`. The engines run there: VACASK
  writes `<analysis>.raw`, `__behavioral.va` and `.osdi` caches into its cwd, and Xyce writes decks and print
  files there. The directory is removed after exit status 0 unless `--vamos-keep` is given. It is kept, and
  its path printed, after exit status 1, 2 or 3.
- **Provenance.** The header of `VAMOS_PLAN.md` §4b goes to the screen and to the log: vamos, then VACASK and
  OpenVAF-r or Xyce, then `cpp` when it ran. The licences come from `licenses.json`, which gains `cpp`
  (GCC, GPL-3.0-or-later).
- **vamos options:** `--vamos-analog=vacask|xyce`, `--vamos-strict`, `--vamos-keep`,
  `--vamos-psf-names=modern|legacy` (§8.1), `--vamos-banner=…`, `--vamos-verbose`. `--vamos-*` tokens are
  also accepted inside `SPECTRE_DEFAULTS`; argv wins.

## 2. Command line

### 2.1 Grammar
- `spectre options inputfile`. The first non-option token is the netlist. A second one is an error. With no
  netlist, Spectre reads standard input and `%C` is `stdin` [M ref19 p.25, 39; UG p.264].
- There are three option families: `-x` (off, or a plain option), `+x` (on) and `=x` (exclusive, e.g. `=log
  f` = the file only) [M ref19 pp.25-38]. A setting given again later wins. Argv is applied after the defaults.
- Abbreviations are the manual's own list (`-r`, `-f`, `+l`, `=l`, `-l`, `-c`, `-maxw`, `-maxn`, `-maxwtl`,
  `-maxntl`, `+cp`, `-cp`, `+ss`, `-ss`, `+rec`, `-rec`, `+mt`, `-mt`, `-proc`, `-inter`, `+inter`, `-mdl`,
  `=mdl`, `=mdle`, `-cl`, `+docl`, `-docl`, `+lqt`, `+lqs`, `+lsusp`, `-ac`, `-cc`, `+p`, `-p`, `-h`, `-hs`,
  `-hf`, `-hsf`) [M]. Each abbreviation is its own `Opt` entry; §2.3 shows them in parentheses.
- **`optable` extension (additive):** `scan(…, option_chars="-+")` gains a keyword parameter. Today only `-`
  and `+` tokens are options, so `=log` would be taken as the netlist. The spectre personality passes
  `"-+="`.
- `+%X string` and `-%X` (X a single letter) are removed by a pre-scan in `spectre/args.py` before `scan`,
  because the value of `+%X` is the next token [M ref19 p.28; UG p.265].
- **`argscan.expand_option_files` is not used.** In Spectre `-f` is `-format` [M], not an option file.
- A Spectre option the table does not know gets a warning ("unknown option X ignored") and is recorded. Under
  `--vamos-strict` it is exit status 2.
- **The invoked name.** `cli.py` dispatches every link name that starts with `spectre` to this personality and
  passes the name as `opts['argv0']`; under `vamos -spectre` it is `spectre`. It is `%S`, the prefix of
  `<prog>_DEFAULTS` [M ref19 p.39] and the `<prog>` of the log trailer (§8.7).

### 2.2 Defaults and precedence
- The defaults are the shlex-split tokens of the environment variable `<prog>_DEFAULTS`, where `prog` is the
  invoked name (§2.1; `spectre` → `spectre_DEFAULTS`), or else `SPECTRE_DEFAULTS` [M ref19 p.39; UG p.239].
- Precedence: the command line beats the netlist `options` statements, which beat the defaults [M ref19 p.39].
  vamos keeps the defaults and argv as two layers. Settings that an `options` statement can also set are
  resolved after parsing, as argv ⊕ options ⊕ defaults: `-format`↔`rawfmt`, `-raw`↔`rawfile`,
  `-maxwarns`↔`maxwarns`, `-maxnotes`↔`maxnotes`. Every other setting is argv ⊕ defaults. The result is a
  `Settings` (§10).

### 2.3 Options

`Option` is the literal `Opt` name, as in `VAMOS_AMS_DESIGN.md` §8: `eq` names exclude the `=`, `prefix` names
are the fixed part. "Ignored" is silent; "noted" prints one line; "unsupported" warns. Rows marked *error* end
the run with exit status 2.

| Option (abbrev.) | Kind | Disposition |
|---|---|---|
| `-help` (`-h`), `-helpsort` (`-hs`), `-helpfull` (`-hf`), `-helpsortfull` (`-hsf`) | flag, optional topic | mapped: vamos help (the supported subset; with a topic, that primitive's or analysis's dispositions); exit 0 |
| `-V`, `-W` | flag | mapped: the version / subversion line of the banner profile; exit 0 when there is no netlist, otherwise the run continues [I] |
| `-cmiversion` | flag | noted |
| `-cmiconfig` | next | noted |
| `-raw` (`-r`) | next | mapped (§2.5) |
| `-format` (`-f`) | next | mapped: `psfascii`, `nutascii`, `nutbin`; `psfbin psfbinf psfxl sst2 fsdb fsdb5 wdf wsfbin wsfascii awb tr0ascii` → psfascii (note); `uwi` or anything else → *error* |
| `-outdir` | next | mapped (§2.5) |
| `+rtsf` | flag | ignored |
| `-uwifmt`, `-uwilib` | next | unsupported |
| `+log` (`+l`), `=log` (`=l`) | next | mapped: screen and file / file only |
| `-log` (`-l`) | flag | mapped: screen only (the default) |
| `-cols` (`-c`), `-colslog` | next | ignored |
| `+error -error +warn -warn +note -note +info -info +debug -debug` | flag | mapped: whether that class is printed (screen and log); the trailer counts are unaffected |
| `-maxwarns` (`-maxw`), `-maxnotes` (`-maxn`) | next | mapped: per message id and analysis, on the screen |
| `-maxwarnstolog` (`-maxwtl`), `-maxnotestolog` (`-maxntl`) | next | mapped: the same limits for the log file |
| `+varedefnerror` | flag | noted |
| `+%X string`, `-%X` | pre-scan | mapped (§2.4) |
| `-E` | flag | mapped (§2.6) |
| `-D` (`-D<x>`, `-D<x=y>`), `-U` (`-U<x>`) | prefix | mapped: cpp defines; they make cpp run [M ref19 p.30] |
| `-I` (`-I<dir>`) | prefix | mapped: a search directory for `include`, `ahdl_include` and PWL `file=` [M UG p.73]; it also makes cpp run [M ref19 p.30] |
| `-disableCPP` | flag | mapped: -D/-U/-I no longer run cpp; -E still does [M ref19 p.37] |
| `+config`, `=config` | next | mapped: a Spectre-mode fragment appended to the netlist. Several `+config` are processed in order; `=config` appends its file, and every `+config` on the command line is then dropped [M ref19 p.38] |
| `-config` | flag | mapped: drops every `+config` and every preceding `=config` [M ref19 p.38] |
| `+pre_config` | next | mapped: a fragment prepended to the netlist, after the title line [I] |
| `-pre_config` | next | mapped: drops the `+pre_config` fragments (the manual gives it a file argument) |
| `+top` | next | unsupported (*error*) |
| `+param` (`+p`) | next | noted: soft parameter limits are not checked |
| `-param` (`-p`) | flag | ignored |
| `+paramdefault`, `=paramdefault` | next | mapped: lines `primitive parameter value` give defaults for analysis and `options` parameters that the netlist does not set [M ref19 p.26] |
| `-paramdefault` | flag | mapped |
| `+errpreset` | eq | mapped: the errpreset of every `tran` that sets none (§5.4) |
| `+aps`, `++aps` | flag | ignored (Spectre APS) |
| `+aps`, `++aps` | eq | mapped: the value overrides `errpreset` in every `tran` [M ref19 p.35] (§5.4) |
| `+preset`, `+postlpreset`, `-preset_override` | eq / flag | noted: the Spectre X accuracy preset, and its ignoring of the netlist's solver options [M ref19 p.36], are not reproduced; vamos uses the netlist's settings |
| `-mts` | flag | mapped: multi-technology mode off, so `options` statements inside subckts are global (note) [M ref19 p.501] (§3.6) |
| `+xps`, `+ms`, `+xdp`, `+speed`, `+query`, `+lite`, `-hpc`, `+dcopt`, `+disk_check`, `-clearcache` (`-cc`) | flag / eq | ignored |
| `+hosts` | next | ignored |
| `+multithread` (`+mt`) | flag, eq | ignored |
| `-multithread` (`-mt`), `-64`, `-32` | flag | ignored |
| `+mtmode`, `-processor` (`-proc`) | next | ignored |
| `+diagnose`, `+transteps`, `+diagnose_fpe`, `+detect_negcap`, `+fix_bad_pivot` | flag | noted |
| `+diagnose_minstep`, `+diagnose_top` | eq | noted |
| `+checkpoint` (`+cp`), `-checkpoint` (`-cp`), `+savestate` (`+ss`), `-savestate` (`-ss`), `-recover` (`-rec`) | flag | ignored |
| `+recover` (`+rec`) | flag, eq | unsupported: the run starts from scratch |
| `+interactive` (`+inter`) | flag, eq | unsupported (*error*) |
| `-interactive` (`-inter`) | flag | ignored |
| `+mpssession`, `+mpshost` | eq | unsupported |
| `-slave`, `-slvhost` | next | unsupported |
| `-mdlcontrol` (`-mdl`) | flag | ignored |
| `+mdlcontrol`, `+mdlcontrole` | flag | unsupported: MDL measurements are not run |
| `=mdlcontrol` (`=mdl`), `=mdlcontrole` (`=mdle`) | next | unsupported: the same |
| `-checklimitfile` (`-cl`) | next | noted |
| `+dochecklimit` (`+docl`), `-dochecklimit` (`-docl`), `-dynchecks` | flag | ignored |
| `+lqtimeout` (`+lqt`), `+lqsleep` (`+lqs`), `+lmode`, `+lorder`, `-alias` | next | ignored (licensing) |
| `+lqmmtoken`, `+lsuspend` (`+lsusp`), `+liclog` | flag | ignored |
| `-ahdllibdir` | next | mapped: where compiled Verilog-A (`.osdi`) is kept |
| `-va,define` | next | mapped on VACASK (`openvaf-r -D MACRO[=VALUE]` [E]); noted on Xyce |
| `-ahdlcom` (`-ac`), `-ahdllint_log`, `-rf_ahdl_functionality` | next | ignored |
| `-ahdllint`, `-ahdllint_maxwarn`, `-ahdllint_summary_maxentries` | flag, eq | ignored |
| `-ahdllint_warn_id`, `-ahdlshipdbdir`, `-ahdlshipdbmode` | eq | ignored |
| `-ahdlsourceramp` | flag | noted |
| `+sensdata` | next | noted |
| `+escchars` (ADE; not in ref19) | flag | mapped [I]: backslash-escape non-name characters in PSF signal names (§8.3) |
| `-env` (ADE `-env ade`; not in ref19) | next | ignored |
| `+logstatus` (ADE; not in ref19) | flag | ignored |

### 2.4 Percent codes and colon modifiers
- Codes [M UG pp.264-265]: `%A` the current analysis name (empty outside an analysis), `%C` the input file name
  as given (`stdin` for standard input), `%D` the start date as `yy-mm-dd`, `%H` the host name, `%M` the CMI
  version (empty here, no CMI), `%P` the process id, `%S` the invoked program name, `%T` the start time as
  `hh:mm:ss` (24 h), `%V` the version string (vamos's), `%%` a literal `%`.
- `+%X string` defines or redefines code X; `-%X` undefines it, so it falls back to the predefined value, or
  to an empty string. Substitution is not recursive [M UG pp.264-266].
- Colon modifiers follow any code except `%%`: `:r` root, `:e` extension, `:h` head (`.` when there is no
  slash), `:t` tail, `::` a literal colon. Any other character after `:` ends the modification and is kept
  together with the colon. Modifiers chain left to right [M UG pp.266-268]. The UG table of examples for
  `/users/maxwell/circuits/opamp.ckt` becomes unit tests. Its row `/tmp%C:t:r.raw → /tmp/opamp.raw`
  contradicts its own `%C:t` row (a tail has no leading slash); vamos follows the definition and gives
  `/tmpopamp.raw` (§14 q.1).
- Codes are expanded in: `-raw`, `±log`/`=log`, `-checklimitfile`, `-outdir`, the `options` values `rawfile`
  and `rawfmt`, `include` file names [M ref19 p.493], and quoted strings in the netlist (`readns=`, `write=`,
  `file=`, …) [M ref19 p.28].

### 2.5 Where the output goes
- **Results:** `-raw`, else `options rawfile=`, else `%C:r.raw` [M ref19 pp.27, 191; UG pp.230, 232]. Since
  `%C:r` keeps the directory, `spectre sub/x.scs` writes `sub/x.raw`. The run directory (§1) is placed beside
  the path that argv and the defaults give; a later `options rawfile=` moves only the results.
- **`-outdir d`** moves the default output files whose names contain no `/` into `d`. An explicit `-raw` and
  names containing `/` are not moved [M ref19 p.27]. So `spectre -outdir o x.scs` writes `o/x.raw`, while
  `spectre -outdir o sub/x.scs` still writes `sub/x.raw` (the literal reading; §14 q.2). Relative paths
  resolve against the cwd.
- **Shape of the results path:** for psf formats it is a directory (created if missing; vamos replaces only
  the files it writes). For nutascii/nutbin it is a single file (§8.5) [I].
- **Log:** `+log f` or `=log f`, the path %-expanded and relative to the cwd (or `-outdir` when it has no
  `/`). The default is the screen only: `-log` is listed first, which makes it the default [M ref19 pp.26, 39],
  and the UG calls `+log` "an option that is normally deactivated" [M UG p.239].
- **Verilog-A:** `-ahdllibdir`, else `%C:r:t.ahdlSimDB` in the cwd, Spectre's own location [M ref19 p.542].
- **State files** (`write=`, `writefinal=`): a relative name resolves against the cwd, or against `-outdir`
  when the name has no `/` (§14 q.3).

### 2.6 The C preprocessor
- cpp runs when `-E` is given, or when `-D`, `-U` or `-I` is given without `-disableCPP` [M ref19 pp.30, 37].
- It is the system `cpp`, found on the scrubbed PATH (`tools.find_real("cpp")`), called as `cpp -nostdinc
  -undef [-D…] [-U…] [-I…] <absolute input path> <run>/cpp.out` [I]; the flag set that reproduces Spectre's
  CPP exactly is §14 q.4. A cpp failure is an error, and so is a missing cpp (Cygwin has none [E]).
- **Markers.** The `# <line> "<file>" [flags]` lines of cpp's output decide, per source file, the statement
  origins, the directory relative `include`s resolve against, and the language. Each file gets §3.1's rule by
  its own name and first line; flag 1 enters a file, and flag 2 returns to the includer and restores its mode
  [M ref19 p.493: the language rule applies to files read by cpp's `#include` too]. The title is line 1 of the
  original top-level file, read before cpp, because cpp empties a `//` line [E47]. The name `cpp.out` is never
  used for language detection.
- `#include` is a cpp directive. A plain `include` is handled by vamos itself [M UG p.73].
- A line starting with `#include`, `#define`, `#if…` when cpp does not run is an error, "CPP directive without
  -E": Spectre errors on a `#include` that cpp did not process [M ref19 p.493], and its SPICE Reader treats such
  lines as a syntax error [M UG p.52].

### 2.7 Exit status and signals [M UG pp.235-237]

| status | Spectre's meaning | vamos |
|---|---|---|
| 0 | completed normally | every planned analysis ran and left its complete output (a note- or warning-only run included) |
| 1 | an analysis stopped because of an error | at least one analysis or sweep point failed in the engine; a missing or short rawfile counts, whatever the engine printed or returned (§7.1, §7.2). The others ran and were written |
| 2 | stopped early because of a Spectre error condition | any vamos error: an unsupported construct, a parse or plan error, a strict-mode failure, a missing netlist; an engine netlist or elaboration error; an unknown model parameter in any Xyce deck (§7.2); a failure of a non-analysis control step; an engine crash |
| 3 | stopped by the user or the operating system | SIGINT, SIGTERM, SIGHUP or SIGQUIT [M UG p.237]: the engine is stopped; the finished analyses are converted, and so is the interrupted one, from the engine's partial output (VACASK: `rawfile.fix_points` on the rawfile it leaves [E40]; Xyce: its print file if `rawfile.read` accepts it [I], §14 q.26), with a warning naming it [M UG p.236] |

- After every exit status, the analyses that finished are converted and listed in `logFile`, and the trailer
  is written (§8.2, §8.7). A run that stops before any analysis has run (a parse error) writes nothing to the
  results path.
- A second INT, TERM, HUP or QUIT exits at once with status 3, without converting [M ref19 p.521].
- SIGUSR1 prints a one-line status (the running analysis and, where the engine reports it, its progress) and
  the run continues [M UG p.235]. SIGUSR2 gets a note ("checkpoint not supported") and the run continues
  [M UG p.237]. Without these handlers Python's default action would kill vamos, leave no `logFile` or
  trailer, and orphan the engine.
- The engine runs in its own process group; vamos forwards the stopping signal to it, waits briefly, then
  kills it [I].

## 3. Spectre netlists – `netlist/spectre.py`

One stdlib parser, producing the same IR as `spice.py` (§4). NetlistParse.rs (Rust, no Python Spectre
binding), XDM (it drops most statements) and VACASK's own Spectre include path (compiled out of our build,
include-only, and it drops analyses and saves) were all evaluated by the research and rejected.

### 3.1 Files and languages
- **Title.** Line 1 of the top-level netlist is the title and is never executed: any statement on it is
  ignored as a comment [M UG p.29], with the one effect on the language below. It labels the outputs: it is
  the PSF `"design"` value [S]. An included file has no title line [M UG p.73]. With cpp, the title comes
  from the original file (§2.6).
- **Language.** A file is in Spectre mode if its name ends in `.scs` or it starts with `simulator
  lang=spectre`; otherwise it starts in SPICE mode, which is Spectre's own default [M UG pp.50, 54; ref19
  p.493]. The rule holds for the top-level netlist, for files read by `include` and for files read by cpp's
  `#include` (§2.6). For the top-level netlist, line 1 stays the title and is not executed, but a line-1
  `simulator lang=spectre …` still selects Spectre mode for the file (note); a `.scs` file whose line 1 is
  `simulator lang=spice` is §14 q.5. A netlist read from standard input has no name: SPICE mode unless it
  starts with `simulator lang=spectre`. `simulator lang=spectre|spice` switches at any statement position and
  holds until the next switch or the end of the file. An included file starts in its own language, and the
  includer's mode is restored after it; a statement cannot continue across a file boundary [M UG pp.53, 73;
  ref19 p.493]. A `simulator lang=` inside an `if` is an error in v1.
- **Case-insensitive definitions.** `simulator lang=spectre insensitive=yes|no` [M UG pp.47-49]. Models and
  subckts defined under `insensitive=yes`, and all models and subckts defined in SPICE mode (the same SPICE
  Reader [I], §14 q.10), are marked case-insensitive. A reference (an instance master, `alter dev=`/`mod=`,
  `probe=`, `ind1=`/`ind2=`, a sweep `mod=`) first tries an exact-case match, then a marked definition whose
  lower-cased name matches; Spectre mode may refer to models and subckts defined in SPICE mode [M UG p.52].
  Two marked definitions that differ only in case are an error. Node and parameter names are unaffected.
  `spectre.py` resolves each reference to the definition's IR name, so the IR carries no mark. Any `simulator`
  parameter other than `lang` and `insensitive` is an error.
- **include** `"file" [section=name]`: an absolute path is used as is. A relative one is tried against the
  directory of the including file, then each `-I` directory [M UG p.73]. `~`, `$VAR`, `${VAR}` and %-codes are
  expanded [M UG pp.73-74; ref19 p.493]. If none exists, the error lists every path tried. Each `(realpath,
  section)` is expanded once per scope; a repeat is a note (the `spice.py` rule).
- **library / section:** `library L` … `section S` … `endsection [S]` … `endlibrary [L]`; the optional
  trailing names must match [M UG pp.75-76]. `include "f" section=S` reads only section S. A plain `include`
  of a file that defines sections is an error naming them [I].
- **ahdl_include** `"file.va"` uses the include path rules. Its modules become instance masters (IR kind
  `y`, existing rules) and the absolute path goes into `Netlist.hdl`.
- **Fragments:** `+pre_config` and `+config` files are Spectre-mode fragments, parsed before or after the
  netlist, with origins `+config:<file>:<line>`.
- **protect / unprotect**, also abbreviated `prot`/`unprot`, in any case [M ref19 p.497]: a note (they only
  hide listing output). Encrypted content is an error.

### 3.2 Lexical rules [M UG pp.59-62]
- `//` starts a comment at the start of a line or after white space. A `*` as the first non-blank character
  makes the line a comment.
- A line ending in `\` continues on the next line. A line whose first non-blank character is `+` continues
  the previous statement. Both work inside `{…}` blocks.
- Fields are separated by white space. `( ) [ ] { } = :` are significant. Strings are `"…"`.
- **Instance node lists:** `name [(]n…[)]{(n…)} master …`: a bare list, or one or more parenthesized groups,
  which are concatenated in order (`Gm (1 2)(3 4) vccs gm=.01` [M UG p.30]).
- **Case sensitive** in Spectre mode, apart from §3.1's case-insensitive definitions. SPICE mode lower-cases
  everything outside quotes [M UG p.59].
- **Names** use letters, digits and `_`, and start with a letter or `_`. Node names may be integers, and
  their leading zeros are significant: `007` is not `7` [M UG p.60]; VACASK needs such a name quoted (§4.5
  item 9). `!` is allowed (`vdd!`). A `\` makes the next printable character part of the name (`\2N2222`)
  [M UG pp.60-62]. The IR holds the unescaped name; `Netlist.spelling` keeps the written one.
- **Reserved words.** The netlist keywords (`altergroup correlate else end ends export for function global ic
  if in inline input invisible library local march model nodeset out output parameters paramset plot print
  protect pwr real return save sens statistics subckt to truncate unprotect vary visible`, and the reserved
  words `freq scalem temp temper time tnom`) may not name instances, subckts, models or functions; the `M_*`
  and `P_*` constants may not name anything [M ref19 pp.495-497]. Function names, and keywords used as node or
  parameter names, are accepted. A name that is a VACASK reserved word is quoted by the emitter (the existing
  rule).
- **Multiple namespaces:** an instance, a node and a parameter may share a name (`res c10 0 resistor
  r=res`). In `save vcc`, the node wins over an instance of the same name [M UG p.61].

### 3.3 Numbers [M UG pp.83-84]
- **Spectre mode:** `[+-]`, then `digits[.[digits]]` or `.digits`, then an optional `(e|E)[+-]digits`, then an
  optional scale factor from `T G M K k _ % c m u n p f a` (case sensitive: `M` is 1e6, `m` is 1e-3), then
  optional unit letters, which are ignored. `.55`, `.148p` and `1E-14` are numbers [M UG pp.30, 83, 95, 114].
  - An exponent together with a scale factor: the scale factor is ignored (`1.234E-3p` = 1.234e-3) [M]. vamos
    reads it so and adds a note.
  - Unit letters without a scale factor are an error: `r=50Ohms` is rejected, `r=50_Ohms` is fine [M].
  - `1meg` and `1mil` read as 1e-3 (`m` plus the units `eg`/`il`), and `1MEG` as 1e6. vamos reads them per
    the table, with a warning: "Spectre mode: m is milli" (§14 q.6).
  - `P` and `x` are not Spectre-mode scale factors in the 5.1.41 table. `1P` and `1x` are therefore unit
    letters without a scale factor, and an error (§14 q.6 on later versions).
  - Values are read correctly rounded: the scale shifts the decimal exponent, as `expr.number` does
    (`numbers.py` stays frozen).
- **SPICE mode:** `t g meg k m mil u n p f`, case-insensitive. "Any other scale factor is ignored (treated as
  1.0)" [M UG p.84], so in Spectre's SPICE mode `1x` and `1a` are 1, where HSPICE reads 1e6 and 1e-18. Through
  `spice.py`'s dialect `spectre-spice` (§4.3), they read as 1 with a warning.

### 3.4 Ground, globals and hierarchical names
- **Ground** is `0` unless there is a `global` statement. Then the first name of the first `global`
  statement is ground. Several `global` statements accumulate [M ref19 p.482].
- The parser folds the ground name to `0` in the IR, so the emitters stay unchanged. If the ground is not `0`
  and a node literally named `0` is also used, that node is renamed `vamos_node0` (spelling `0`), with a
  warning (§14 q.7).
- **No ground aliases.** `gnd`, `gnd!` and `ground` are ordinary nodes unless declared ground. This differs
  from `spice.py`'s HSPICE fold, which `spice.parse_fragment` therefore switches off in the `spectre-spice`
  dialect (§4.3).
- Other `global` names go into `Netlist.globals`.
- A hierarchical node name (`x1.mid`) on an instance terminal is an error in v1: printed as is, it would most
  likely become a new top-level node of that name [I]. In `save`, `ic` and `nodeset` it is mapped.

### 3.5 Expressions – `expr.parse(text, case, dialect="spectre")` [M ref19 pp.474-477]

| item | Spectre | `expr.py` today (HSPICE) | v1 rule |
|---|---|---|---|
| precedence | unary `+ -` bind tighter than `**`: `-2**2` = 4 | `-2**2` = −4 | dialect precedence (VACASK itself gives 4 [research]) |
| `**` | right associative | same | same |
| `^` | not an operator (bitwise XNOR is `~^` or `^~`) | power | error |
| `x**y`, `pow(x,y)` | x to the power y, "all x, all y" [M ref19 p.476]: C pow | HSPICE guards: `x**0.5` prints as `(x>0.0 ? pow(x, 0.5) : (x<0.0 ? 1.0 : 0.0))`; 0\*\*0 = 0 and (−2)\*\*0.5 = 1 in the evaluator [E45] | `Call('cpow', (x, y))`. The Spectre evaluator gives C pow, with EvalError for x < 0 with a non-integer y and for x = 0 with y < 0. Both printers print `(y==0 ? 1 : pow(x,y))`, because Xyce's pow(0,0) is 1e50 and VACASK refuses it [E45] |
| `& \| ~^ << >>` | bitwise, on integer operands | errors | folded when constant; otherwise PrintError |
| `!` | not in the table | logical not | accepted |
| `log ln log10 sqrt` | domain x > 0 | sign-keeping | identical in the domain; outside it the Spectre evaluator raises EvalError. The printers keep the HSPICE guards, a documented deviation for out-of-domain values only |
| `int(x)` | "integer value" | truncation | truncation [I] |
| `sgn sign(x,y) atan2(x,y)=atan(x/y) hypot fmod floor ceil min max abs exp` and the trig functions | as in C | same | same |
| `M_*`, `P_*` constants | table [M ref19 pp.466-467] | printed bare to VACASK | folded to numbers with Spectre's values (VACASK's table has the same values [VACASK docs]) |
| division | parameters are real-valued [M UG p.85] | real | real (the printers already force real arithmetic on VACASK) |
| `?:` | right associative | same | same |
| `&&` `\|\|` | not short-circuited | lazy | lazy (same value) |
| vectors `[0.5 1 +p2 (sqrt(p2*p2))]` | elements may be space separated; non-trivial ones parenthesised | — | `spectre.py` parses them into lists of Expr |

- `temp` and `tnom` are predefined netlist parameters [M UG p.93]. `temp` prints as `$temp` (VACASK) or
  `temp` (Xyce), through `expr.py`'s `temper` handling. `tnom` prints as `$tnom` on VACASK; on Xyce it is
  folded to the tnom value, which cannot be swept.
- User functions `real f(real a, real b) { return expr; }`, at top level only [M UG p.92], are inlined with
  `expr.inline`.

### 3.6 Statements → IR

| statement | IR |
|---|---|
| `parameters a=1 b=a*2` | `Param` (top level, or merged into `Subckt.params`) |
| `name [(]n…[)]{(n…)} master [(]p=v…[)]` | `Instance`: kind per §3.8; kind `x` for a subckt, `y` for a Verilog-A module; node groups concatenated (§3.2) |
| `model name master p=v…` | `Model` (§3.9) |
| `model name master { 1: … 2: … }` | binned `Model`s with `bin_rule="spectre"` |
| `[inline] subckt name [(]ports[)]` … `ends [name]` | `Subckt` (`inline` flag) |
| `if (c) {…} else if (c) {…} else {…}`, and the brace-less one-statement form | `Cond` |
| `real f(…) {…}` | inlined; not in the IR |
| `global g n…` | ground and `Netlist.globals` (§3.4) |
| `ic n=v …`, `nodeset n=v …` | `Netlist.ics` / `nodesets`, names checked by `signals.py` (§3.10); an `inst:term=` entry is an error |
| `save X[:p] … [depth= sigtype= devtype= subckt= exclude= probelvl= time_window=]` | `Netlist.saves` (`SaveSpec`) |
| `name options p=v…` | at top level, or as a child of a sweep or montecarlo block [M UG p.47]: `Netlist.options`, global, merged in netlist order (§3.10). Inside a subckt body, directly or through an include: an error in v1 naming the subckt, because Spectre's multi-technology mode (on by default) scopes `temp`, `tnom`, `scale` and `scalem` given there to that subckt [M ref19 p.501]. Under `-mts` (MTS off) such options are global, with a note |
| `name set p=v…` | `Analysis(kind="set")`, positional |
| an analysis (§3.11) | `Analysis` in `Netlist.analyses`, in netlist order |
| `name sweep … {…}`, `name montecarlo … {…}` | `Analysis(kind=…, children=[…])` |
| `name alter dev=\|mod=\|sub= param=p value=v`, `name alter param=temp\|<netlist param> value=v` | `Analysis(kind="alter")` |
| `name altergroup {…}` | `Analysis(kind="altergroup")`: error in v1 |
| `name info …` | `Analysis(kind="info")`: note, the file is not written |
| `name paramtest [printif=\|warnif=\|errorif=]… [message=] [severity=]` | `ParamTest`, a component in the body (§4.1 item 4). `plan.build` evaluates it once per instance path (inside subckts and taken `if` branches) with that path's parameter values; a test that reads a parameter a sweep or alter changes is evaluated at every value the plan sets. Several tests act if any passes. `errorif` → error (exit status 2); `warnif` → warning; `printif` → note; with no test, `severity` `debug`/`status` → note, `warning` → warning, `error` → that analysis fails (exit status 1), `fatal` → exit status 2; `message` is the text [M UG pp.286-287] |
| `statistics {…}` | `Netlist.statistics` (§3.12) |
| `include`, `ahdl_include`, `library`, `section`, `simulator lang=` | file handling (§3.1) |
| `name check …`, `name checklimit …`, `name assert …` | note: not evaluated |
| `name shell …` | warning: the command is not run |
| anything else | error naming it |

### 3.7 Parameters, subcircuits and conditionals
- Subckt `parameters` (header or body) are defaults that each instance can override. An inner scope shadows
  an outer one; top-level parameters are visible everywhere [M UG pp.85-86, 93]. This is PARHIER=local, so
  `ParseOpts.parhier_local=True`.
- Ordering, duplicates and evaluation follow `spice.py`: a topological sort per scope, the last definition
  winning with a note, and top-level values evaluated into `Netlist.values`.
- Every subckt instance has an implicit `m` [M UG p.63]. The existing `$mfactor`/`vamos_mfactor` rules apply.
- An `inline subckt` is printed as a plain subckt with `Subckt.inline=True`. In outputs, its component named
  like the subckt takes the caller's name (§6.4) [M UG p.111].
- `Cond` holds a structural `if` [M UG pp.109-111]. Conditions are parameter expressions, evaluated per
  instance path. Same-named instances in different branches are allowed, in an `if` that has an `else`
  [M UG p.111].
- **Sweeps and alters of what a condition or a bin reads.** A sweep, alter or Monte Carlo target that a
  structural condition or a model-group bin guard reads (as an instance's `l`/`w`/`nf`, through a subckt
  parameter, or through a netlist parameter) is evaluated by `plan.build` for every value the plan sets, on
  every instance path.
  - If any value changes a branch, the run is an error, as in Spectre: "this error is produced for any
    analysis that changes the value of the conditional expression" [M UG p.111].
  - If any value changes the bin a model-group instance selects, the run is an error in v1. VACASK keeps the
    nominal bin when the instance's own geometry is swept [E41], and the Xyce emitter binds the card at
    emission (§4.5 item 3), so a re-bind would otherwise be silent.
  - Otherwise it runs: VACASK keeps its `@if`, which it re-evaluates at each point [E39]; Xyce renders the
    unchanged branch and card.
  - UG p.115 says instead that instances keep the model chosen at input when selection parameters change; the
    manual is inconsistent, so vamos refuses either change (§14 q.13).

### 3.8 Instances and primitives

| Spectre master | IR kind | terminals (output names) | parameters |
|---|---|---|---|
| `resistor` | `r` | `1 2` | `r`: its default is the card's `r`, else `rsh·(l−2·etchl)/(w−2·etch)`, else ∞ [M ref5 p.627]. An infinite resistor is not printed (note), but it stays in the instance table: `probe=`, `oprobe=` and saves resolve to its node pair, and its terminal currents are 0. `r` = 0 (the resistance form below `thresh` [M ref5 p.630]) prints as a short (§4.5 item 10); a non-constant `r` that evaluates to 0 is an error. `m`; `isnoisy` (§4.5 item 7); `tc1 tc2 w l` → `sp_resistor`. The instance `scale` is never printed: Spectre applies it to `w` and `l` only, overriding the `scale` option [M ref5 p.629], and vamos applies it the same way (with `r` given it has no effect), while `sp_resistor` would multiply the resistance by it and Xyce rejects it [E23]. `tnom` and the other temperature data are card parameters [M ref5 p.630]. `c=` (wire RC), `coeffs=` and a third terminal → error |
| `capacitor` | `c` | `1 2` | `c` (default: the card's `c`, else 0 [M ref5 p.282]); `m`; `ic`; `coeffs=` → error |
| `inductor` | `l` | `1 2` | `l` (default: the card's `l`, else 0 [M ref5 p.372]); `m`; `ic`; `r=` → error (v1) |
| `vsource` | `v` | `p n` | §3.8.1 |
| `isource` | `i` | `sink src` [M ref5 p.380] | §3.8.1 |
| `iprobe` | `v` with dc 0, `prim="iprobe"` | `in out` [M ref5 p.380; S] | — |
| `vcvs` | `e` | `p n ps ns` [M ref5 p.681] | `gain` |
| `vccs` | `g` | `sink src ps ns` [M ref5 p.678] | `gm` |
| `ccvs` | `h` | `p n` [M ref5 p.288] | `rm`, `probe=` (a vsource or iprobe) |
| `cccs` | `f` | `sink src` [M ref5 p.286] | `gain`, `probe=` (a vsource or iprobe) |
| `mutual_inductor` | `k` | — | `coupling`, `ind1`, `ind2` |
| `diode` model | `d` | `a c` [M ref5 p.302] | model; `area`, `m` |
| `bjt` model | `q` | `c b e [s]` | model; `area`, `m` |
| `jfet` model | `j` | `d g s [b]` [M ref5 p.385]; a fourth terminal is an error in v1 | model; `area`, `m` |
| `mos1 mos2 mos3 bsim3v3 bsim4` model | `m` | `d g s b` | model; `w l ad as pd ps nrd nrs nf m …`; default `w`/`l` per §3.9 |
| a Verilog-A module | `y` | the module's ports | instance parameters |
| a subckt | `x` | the ports | overrides, `m` |

- `X:term` accepts a terminal name from this table or the terminal's 1-based index [M ref19 p.518]; outputs
  use the name. The terminal order is unchanged: the first terminal is the IR's first node (`p` of a source).
- The emitters print every R/C/L value explicitly. VACASK's own defaults differ: resistor r = 1 Ω, capacitor
  c = 1e-12, inductor l = 1e-6 (`devices/*.va`) [E]. A card's `r`/`c`/`l` is folded into the instances
  (§3.9). A `vsource`/`isource` parameter outside the table below is an error.

#### 3.8.1 Independent sources → `ir.Source` (HSPICE field names, every field resolved) [M ref5 pp.380-383, 682-686]

`Source.spectre` keeps every parameter written on the instance, in Spectre names, whatever its `type` (§4.1
item 11). `spectre.resolve_source(params, type, tran_stops, notes) -> Source` applies this table; alters use it
again (§5.3).

| Spectre | IR | rule |
|---|---|---|
| `dc` | `Source.dc`; None when absent | Given: the value of every DC-type solve, which is `dc` (operating points and sweeps) and the operating point of `ac`, `noise` and `xf`; the waveform acts in `tran` only [M ref5 pp.383, 686]. Absent: the waveform's t = 0 value [M ref5 p.682], kept derived (None) so that it follows alters and sweeps of the waveform fields. The engines need different prints (§4.5 item 8): VACASK ignores `dc` unless `type="dc"` [E17], and Xyce gets the derived value written out. The `tran` initial point uses the t = 0 waveform value on both engines [E17; I for Spectre]. |
| `type=dc` | no active wave (`Source.wave` None) | the waveform fields the instance gives stay in `Source.spectre`, for `alter … param=type` (§5.3) |
| `type=pulse val0 val1 delay rise fall width period` | `pulse v1 v2 td tr tf pw [per]` | `width` and `period` default to ∞ [M]: `pw` = 1e30 s (accepted by VACASK [E]) and no `per`. `rise`/`fall` absent or 0: Spectre's `tran` default `transres` = 1e-9 × stop [M ref19 p.420], taken from the shortest `tran` (one edge serves every analysis), with a warning when a `tran` runs (Spectre's default edge is undocumented [M ref5 lists no default]). The rule applies to every value that reaches the engine, not only to literals: a non-constant `rise`/`fall` prints as `(e==0 ? <transres> : e)`, and an alter or sweep value of 0 becomes transres. VACASK reads `fall` ≤ 0 as "no fall", so the pulse stays at val1, and stops the run on `rise` ≤ 0 [E25, E37]; Xyce makes either an ideal step [E25]. An explicit `period` ≤ rise+fall+width is an error (existing check). |
| `type=sine sinedc ampl freq sinephase damp delay` | `sin vo va freq td theta phase` | `sinedc` defaults to `dc`; `freq` defaults to 0, which gives a constant (folded into dc when `damp`=0, else an error); `ampl2 freq2 sinephase2`, `fmmod*`, `ammod*` → error |
| `type=exp val0 val1 td1 tau1 td2 tau2 delay` | `exp v1 v2 td1' tau1 td2' tau2` | `td1' = delay+td1`, `td2' = delay+td2` [I]; a missing `tau1`/`tau2` is an error (Spectre's defaults are not given) |
| `type=pwl wave=[t v …] offset scale stretch delay`, or `file=` | `pwl` with `points` (t·stretch, v·scale+offset), `td=delay` | `file=` holds two columns without scale factors [M UG p.74] and is searched in the cwd, then the `-I` directories; `pwlperiod`/`twidth` → error; `allbrkpts` is ignored |
| `mag`, `phase` | `Source.ac` | |
| `xfmag` | — | ≠ 1 while an `xf` runs → error |
| `pacmag pacphase fundname` | — | silent: only `pac`/`pdisto`/`envlp` use them, and none runs |
| `noisefile noisevec` | — | error while a `noise` analysis runs; otherwise silent |
| `tc1 tc2 tnom` | — | ≠ 0 → error |
| `m` | multiplier | existing rules |

### 3.9 Models and model groups
- `model name master params` becomes `Model`, with `Model.prim` = the Spectre master (§4.1 item 12):
  - kind: `mos*`/`bsim*` with `type=n|p` → `nmos`/`pmos` (default n [I]); `bjt` with `type=npn|pnp`
    (default npn); `jfet` with `type=n|p`; `diode` → `d`; resistor/capacitor/inductor cards → `r c l`
    (existing);
  - level, from the master: `mos1`→1, `mos2`→2, `mos3`→3, `bsim3v3`→49, `bsim4`→54, `diode`→1, `bjt`→1,
    `jfet`→1. The existing `tables.DISPATCH` then applies unchanged (§4.4). A Spectre diode `level` other than
    1 is an error: Spectre's diode levels are 1 junction, 2 Fowler–Nordheim and 3 junction plus metal and poly
    capacitance [M ref5 p.303], not HSPICE's;
  - `tables.SPECTRE_MASTERS` (phase-0 data, §4.4) gives per master the parameter renames and strips (e.g.
    diode `imax` is stripped with a warning) and three card rules:
    - a resistor, capacitor or inductor card's `r`/`c`/`l` is the default of every instance that gives no
      value [M ref5 pp.282, 372, 627]. It is folded into those instances and stripped from the card, because
      Xyce's model `R`/`C`/`L` is a multiplier: `.model rm R R=2` doubles every instance, including Xyce's 1 kΩ
      default for an instance with no value [E26];
    - a diode card without `eg` gets `eg=1.124481`, Spectre's value at 27 °C [M ref5 p.306]; `sp_diode` and
      Xyce default to 1.11, which moves a 1 mA diode by 4.8 mV at 127 °C [E34]. Spectre's default is
      temperature dependent, so a run that simulates such a card at a temperature other than its `tnom` gets
      a warning naming the card (§14 q.27);
    - a MOS card's `w`/`l` are default instance geometry (below).
  - A parameter an engine does not know fails loudly: VACASK at elaboration, Xyce through the output scan of
    every deck (§7.2).
- **Default MOS geometry.** A MOS instance without `w`/`l` takes the card's `w`/`l`, else the master's default
  (mos1, mos2, mos3: 3e-6; bsim3v3, bsim4: 5e-6 [M ref5, "Default channel width/length"]). The value is
  written on the instance with a note, and `w`/`l` are removed from the card. A model-group instance without
  `w`/`l` uses the master's default; a group whose cards give `w`/`l` for such an instance is an error in v1.
  Model-group selection uses the filled values. The engines' own default (1e-4, `$simparam("defl")` in
  VACASK's `sp_mos1`) is never reached: there, `w=30u` alone gave 25.35 µA where `l=3u` gives 845 µA [E42].
- A model whose master is a Verilog-A module gives `y` instances with the card's parameters merged in (the
  `spice.py` rule).
- **Model groups:** `model base master { k: params … }` gives Models `base.k` (with `base`, `bin_index` and
  `bin_rule="spectre"`). Selection is `lmin <= l < lmax and wmin <= w < wmax`, the first matching entry in
  group order winning, and Spectre names the chosen card `base.k` [M UG p.108]. Bounds are exact (no 1e-15
  tolerance). `w` is the instance's total `w`, also with `nf` > 1 (§14 q.8).

### 3.10 Control statements
- `options` (global, read with the circuit [M ref19 p.187]; inside a subckt, §3.6). Keys are matched
  case-insensitively (the UG itself writes `o1 options TEMP=55` [M UG p.93; I as a general rule]) and stored
  lower-cased in `Netlist.options`; the written spelling goes to `Netlist.spelling`. Keys:

| key | disposition |
|---|---|
| `reltol`, `vabstol`, `iabstol`, `chargeabstol` | VACASK `reltol`, `vntol`, `abstol`, `chgtol` (the same SPICE-style meaning); Xyce: note |
| `temp`, `tnom` | `netlist/spectre.py` always sets `Netlist.temp` and `Netlist.tnom`: 27.0 unless an `options` statement sets them [M ref19 p.187]. The emitters' fallback for None (25 °C, the HSPICE default) is never reached |
| `gmin` | mapped on both engines |
| `scale` | the IR's `scale` (HSPICE rule); ≠ 1 → warning [I] |
| `scalem` | ≠ 1 → error |
| `rawfmt`, `rawfile`, `precision` | output settings (§2.2, §2.5, §8.3) |
| `save`, `nestlvl`, `currents`, `subcktprobelvl`, `pwr`, `useprobes`, `useterms`, `saveahdlvars` | §6.1 |
| `maxwarns`, `maxnotes` | message limits |
| `tempeffects` | `all` silent; any other value → error |
| `rabsshort rcut ccut cgnd cmax cclamp lshort kcut dcut qcut` (element reduction) | the default is silent; any other value → warning: not applied |
| `homotopy newton limit maxdeltav gmin_start gmin_converge try_fast_op dcmaxiters pivrel pivabs rforce residualtol digits cols checklimitdest sensfile psfversion topcheck compatible approx icversion` | note |
| `multithread` | ignored |
| anything else | warning |

- `set`: positional options (§5.1).
- `ic` / `nodeset`: node or hierarchical-node assignments. `signals.py` resolves their names, and the names
  in `readic`/`readns` files, against the flattened IR like save items; an unmatched name is a warning naming
  it (VACASK drops it silently, Xyce with a warning [E31]). Wildcards → error (v1).
- `alter`: the targets of §5.3; `annotate=` is ignored.

### 3.11 Analyses: parameter dispositions [M ref19 pp.43-47, 64-69, 181-186, 406-410, 416-427, 438-441]

| analysis | parameters |
|---|---|
| `dc` | `start stop center span step lin dec log values valuesfile` and `dev mod sub param` (§5.2); no sweep target → operating point; `readns` (mapped), `write`/`writefinal` (mapped), `useprevic` (VACASK mapped, Xyce warning), `save`/`nestlvl` (§6), `print=yes` (mapped: the values go to the log), `oppoint`≠no (note), `force`/`readforce`≠none (error), `restart` and `swpuseprevic` (mapped to VACASK's sweep `continuation`, §5.2), `homotopy newton maxsteps swp1stpointic` (note), `maxiters` (VACASK `op_itl`), `hysteresis=yes` (error), `check annotate title emir*` (ignored) |
| `ac` | as `dc`, plus `freq` (the fixed frequency of a parameter sweep) and `prevoppoint=yes` (VACASK mapped, Xyce warning); `skipdc=yes` and `perturbation`≠linear (with `out1 out2 contriblist rf* flin_out fim_out maxharm_nonlin`) → error |
| `noise` | as `ac`. Output: `p n`, or `oprobe=`: a two-terminal component → its terminal pair (an infinite resistor included, §3.8); a controlled source with `oportv=1` or absent → its output port (p, n); any other port number → error; a current output through a vsource or iprobe → error. Input: `iprobe=` a vsource or isource (a `port` → error), `iportv` 1 or absent. It is optional [M ref19 p.181]: without it §5.6 adds an input, and `in`/`gain` are not written. `separatenoise` (note) |
| `xf` | as `ac`; output `p n` or `probe=` (two-terminal, an infinite resistor included; a current probe → error); `stimuli=sources` (`nodes_and_terminals` → error); `--vamos-analog=xyce` → error |
| `tran` | `stop` (required); `start`≠0 → error; `outputstart` (mapped); `step` (VACASK `step`); `maxstep` (§5.4); `minstep` (note); `istep pstep` (ignored); `tpoints readtime` (error); `ic`, `skipdc` (§5.4); `readic readns write writefinal` (mapped); `useprevic` (VACASK mapped, Xyce warning); `linearic=yes` and `rampup*` (error); `oscfreq` (ignored); `cmin`≠0 (warning); `method errpreset relref lteratio` (§5.4); `maxstepratio reltolratio` (custom errpreset [M ref19 p.427]: error); `maxiters` (VACASK `tran_itl`); `transres` (feeds the pulse edge policy); `restart` (§5.2); `skipstart skipstop skipcount` (mapped: output filtering); `strobe*` (warning); `infonames infotimes infotime_pair acnames actimes actime_pair` (warning: not run); `ckptperiod saveperiod saveclock savetime savefile recover` (note); `circuitage`≠0 (error); `noisefmax`>0 (error); `param paramset param_vec param_file sub` (error); `annotate* title progress_* compression comp* complvl flush* fastbreak d2a* fastcross lteminstep ltethstep vref* iref* emir* autostop dcmaxiters` (ignored or noted) |
| `sweep` | the sweep parameters of `dc` plus `sub`; `paramset` and `faults*` (error); `distribute numprocesses savedatainseparatedir annotate title` (ignored) |
| `montecarlo` | error in v1 |

### 3.12 sweep, montecarlo and statistics blocks
- `name sweep params {` must end its line with the opening brace [M ref19 p.406]. Its children are analyses,
  nested sweeps, `options` statements (global options [M UG p.47]), or `alter`/`set`/`info` statements (§5.3
  restricts them). The block closes with `}`.
- `name montecarlo params { … }` holds children and `export` lines. It is parsed in v1 and is an error at
  plan time.
- `statistics { process { vary p dist=gauss|unif|lnorm std= [N=] [percent=yes] } mismatch { … } correlate
  param=[…] cc=… ; correlate dev=[…] param=[…] cc=… ; truncate tr=… }` becomes `Netlist.statistics`
  (§4.1 item 8) [M ref19 pp.175-176; UG]. A netlist without a `montecarlo` analysis simulates at nominal
  values, as Spectre does, so PDK libraries that carry statistics blocks parse and run.

### 3.13 SPICE-mode text
- SPICE sections and SPICE-mode files go through `spice.parse_fragment` with dialect `spectre-spice` and a
  resolver (§4.3): elements, models, subckts and `.param` follow `spice.py`'s rules, with lower-cased names,
  the §3.3 number table and the §3.4 ground rule. Names defined outside the fragment, in either language and
  in either order, are resolved through the resolver.
- In this dialect `spice.py` interprets no dot control: every one comes back to `spectre.py`, which
  interprets it (Spectre maps SPICE statements to Spectre ones [M UG p.54]):
  - `.op` → `opBegin`, `.dc` (a source sweep) → `srcSweep`, `.ac` → `frequencySweep`, `.tran` →
    `timeSweep` [M UG p.232]. `.tran` takes SPICE3 fields, `tstep tstop [tstart [tmax]] [uic]`, giving
    `step`, `stop`, `outputstart`, `maxstep` and `skipdc=yes` [I for the field mapping, §14 q.9];
  - HSPICE's `.tran` interval syntax, RMAX/DELMAX, 25 °C and DEFL/DEFW never apply [E46];
  - a second analysis of a type gets `<type><n>` (`ac2`, `ac3`) [M UG p.232, for `ac`; §14 q.9 for the
    others];
  - `.print`/`.probe` → saves; `.ic`, `.nodeset`, `.temp` → as in Spectre; `.option(s)` → the Spectre
    options table;
  - `.measure` → warning (ignored); `.noise`, `.four`, `.alter` and every other dot statement → error.
- Spectre parameters appended to a SPICE line [M UG p.54] fail loudly in `spice.py`.

## 4. IR and package extensions

Every change is additive. Defaults keep today's behaviour, and the vcs-ams goldens and the `test_netlist_*`
and `test_ams_*` suites must pass unchanged; that is the merge gate.

### 4.1 `ir.py` (frozen; additive)
1. **`Analysis`** gains the kinds `noise xf sweep montecarlo alter altergroup info set`. `op dc ac tran`
   now carry full Spectre arguments. New fields, all defaulted:
   - `name: str = ""`: the analysis instance name (SPICE mode: `opBegin` …);
   - `sweep: Optional[SweepSpec] = None`: the analysis's own swept variable (a dc sweep; the frequency or
     parameter sweep of ac/noise/xf);
   - `nodes: List[str]`: the output node pair of noise or xf;
   - `children: List[Analysis]`: the body of a sweep or montecarlo, in order;
   - `spice: bool = False`: written in SPICE syntax (it decides the output naming).

   The `args` keys per kind are the normalized names of §3.11, documented in the module docstring.
2. **`SweepSpec`** (new): `target` ('dev' | 'mod' | 'sub' | 'param' | 'temp' | 'freq'), `name`, `param`,
   `mode` ('lin' | 'dec' | 'log' | 'step' | 'values'), `start`, `stop`, `count`, `step`, `values:
   List[float]`, `origin`. Names are IR names, resolved by the parser (§3.1).
3. **`SaveSpec`** (new): `items: List[str]` (raw tokens), `depth`, `sigtype`, `devtype`, `subckt`,
   `exclude`, `probelvl`, `time_window`, `origin`; and `Netlist.saves: List[SaveSpec]`. HSPICE probes stay in
   `Netlist.probes`.
4. **`Cond`** (new): `branches: List[Tuple[Expr, List[Item]]]`, `default: List[Item]`, `origin`.
   **`ParamTest`** (new): `name`, `tests: List[Tuple[str, Expr]]` (`printif`/`warnif`/`errorif`), `message`,
   `severity`, `origin`; the emitters print nothing for it. `Item` gains both. **`flat_items(items) ->
   Iterator[Item]`** (new) yields every item, with the branches and default of each `Cond`, recursively.
   `Netlist.instances()` and `models()` are documented as not descending into `Cond`.
5. `Subckt.inline: bool = False`.
6. `Model.bin_rule: Optional[str] = None`. `"spectre"` means exact bounds, group order and total `w`.
7. `Instance.prim: str = ""`: the Spectre primitive master, used for output terminal names and to mark iprobes.
8. `Netlist.statistics: List[StatBlock]` (parse-only in v1): `StatBlock(kind, varies, correlates, truncate,
   origin)` with `kind` `process` or `mismatch` (or `statistics` for the `correlate`/`truncate` lines at the
   block's own level); `Vary(param, dist, std, n, percent, origin)`; `Correlate(params, devs, cc, origin)`.
9. `Netlist.dialect: str = "hspice"` (`"spectre"` here).
10. `ParseOpts.dialect` documents the value `"spectre-spice"`.
11. `Source.spectre: Dict[str, Expr] = {}`: every parameter written on a vsource/isource, in Spectre names,
    whatever its `type` (§3.8.1).
12. `Model.prim: str = ""`: the Spectre master; `plan.py` applies its `SPECTRE_MASTERS` renames to `mod=`
    sweep and alter parameters (§5.2).

**No IR change is needed for:**
- user functions: inlined by the parser, as `spice.py` does for `.param f(a)=…`;
- `ahdl_include`: `Netlist.hdl` and `y` instances;
- `options`: `Netlist.options` holds Num/Str values by lower-cased key; the written spelling is in
  `Netlist.spelling` (§3.10);
- `ic`/`nodeset`: `Netlist.ics`/`nodesets`;
- `global`: `Netlist.globals`, with the ground folded to `0` (§3.4);
- `inline` naming and terminal names: the output layer reads `Subckt.inline` and `Instance.prim`;
- a derived source `dc`: `Source.dc` is None (§3.8.1);
- case-insensitive definitions: the parser resolves every reference to an IR name (§3.1).

### 4.2 `expr_ast.py` / `expr.py`
- `Binary.op` gains `<< >> & | ~^`. For the frozen `expr_ast.py` this is a docstring change only.
- `expr.py` gains `parse(text, case="lower", dialect="hspice")`, `number(s, dialect="hspice")` and
  `evaluate(ast, scope, dialect="hspice")`, with the §3.5 rules. The Spectre dialect parses `**` and `pow`
  to `Call('cpow', (x, y))`.
- The printers gain `cpow` (`(y==0 ? 1 : pow(x,y))` on both engines [E45]), a PrintError for the bitwise
  operators, and `$tnom` on VACASK. Unknown operators already raise PrintError/EvalError ("operator … is not
  HSPICE").
- `vacask_quote` quotes an all-digit name with a leading zero (§4.5 item 9).

### 4.3 `spice.py`
- **New:** `parse_fragment(lines: List[Tuple[str, str]], cwd: str, opts: ParseOpts, resolver: Resolver) ->
  Fragment`. `resolver` is a phase-0 Protocol with `subckt(name)`, `model(name)`, `param(name)` and
  `va_module(name)`; it answers for names defined outside the fragment, in either language and in either
  order. A name it cannot answer yet goes to `Fragment.pending` and is resolved by `spectre.py`'s final pass;
  `spice.py` never reports it. Today `spice.py` resolves X masters and models at element time in its private
  scope tree and fails on an unknown name ("subckt X not found", "model X not found").
- `Fragment` holds `items` (params, models, subckts, instances, in order), `controls` (every dot control, as
  `(keyword, fields, origin)`), `notes`, `left_out` and `pending`. It reuses the whole-file machinery:
  includes, `.lib`, duplicates, sorting.
- **Dialect `spectre-spice`:** the §3.3 number table; no ground aliases (the ground is the Spectre ground);
  lower case; PARHIER local; no title line inside a fragment; no dot control interpreted (§3.13). HSPICE's
  `.tran` interval syntax, RMAX/DELMAX, 25 °C and DEFL/DEFW never apply: today `.tran 1n 1u 0 10n` is an
  error and `.tran 1n 1u` gets HSPICE's maxstep and 25 °C [E46]. `synth_step`/`synth_stop` come from the
  netlist's `tran` analyses (the shortest `stop`, §3.8.1).

### 4.4 `tables.py`
- `SPECTRE_MASTERS` (data, frozen in phase 0): master → element kind, level, polarity key, terminal names,
  default geometry, parameter renames, strip keys and card rules (§3.8, §3.9). `DISPATCH` is unchanged.
- `bin_guard_spectre` / `select_bin_spectre` implement the `bin_rule="spectre"` selection.
- `reachable`, `all_subckts` and `smoke_netlist` walk `Cond` (§4.5 item 1).

### 4.5 `vacask.py` / `xyce.py`
1. **Every walk sees `Cond`.** The walks that skip non-`Instance` items today use `ir.flat_items` or handle
   `Cond` explicitly: `tables.reachable`, `all_subckts` and `smoke_netlist`; in `vacask.py` the override
   scan in `_Deck.__init__`, `_names`, `_Deck.items` and `_resolve`; in `xyce.py` `elaborate`/`body_of`,
   `check_scope`, `printed_path`, `_multiplied`, `_models` and `_Deck.items`. On Xyce, conditions are resolved
   per instance path (item 2). A walk that meets an unknown item type raises; it never skips. Today the
   `npn_mod` subckt of UG p.110 renders empty, because its cards are used only inside branches [E44].
2. **`Cond`.**
   - VACASK prints `@if c` / `@elseif c` / `@else` / `@end`. Their allowed contents are instances, models and
     nested blocks [VACASK `cir-conditional.md`]; a parameter or subckt inside one is an error. Verified [E]:
     an `@if` on a subckt parameter selects per instance, with the same instance name in both branches.
   - Xyce resolves conditions per instance path in the elaboration walk. The `__vb<k>` binning-variant
     mechanism is generalized to variants keyed by bin bindings and branch choices. A condition that is not a
     number on its path is an error.
3. **`bin_rule="spectre"`.** VACASK prints an `@if` chain in group order, with exact guards on total `w`.
   Xyce always binds the instance to its card per path (no native binning).
4. **Variables and overridable subckt parameters.** `variables` (from the plan): a top-level parameter that a
   sweep or alter targets.
   - VACASK prints it as `parameters p=vamos_v_p`, with `var vamos_v_p=<value>` in the control block [E].
   - Xyce keeps `.param p=<nominal>` and sweeps it with `.STEP`/`.DC` [E].
   - Such a parameter is never folded into subckt defaults, bin guards or conditions. Today's folding of
     top-level values would freeze it silently. VACASK's `NOT_GIVEN` scheme keeps those defaults overridable.
   - VACASK can sweep or alter only a primary subckt parameter: a dependent one fails ("parameter 'l' of
     instance 'x1' not found"; an alter stops the run) [E24]. A subckt parameter that a `sub=` sweep or alter
     targets is therefore declared overridable exactly like one an X line overrides (`p=NOT_GIVEN` plus
     `p__v`): `RunPlan.overridden` adds these targets to the emitter's overridden set.
5. **Plan rendering.** `plan.py` and `signals.py` use IR names only (`.` paths, IR card names, IR parameter
   names); the emitters own every engine spelling.
   - `vacask.render(nl, analysis_name, osdi, notes, op=False, plan=None, sigmap=None, names=None)` prints the
     plan's control block (§7.1) in place of the single analysis. It translates every IR name in the actions,
     prints each step's saves from `sigmap.per_step[step.id]`, and treats every `sub=X param=p` target as an
     override of p (item 4).
   - `xyce.render(nl, osdi, notes, op=False, step=None, sigmap=None, names=None)` prints one step (§7.2) and a
     `.PRINT` with exactly that step's SignalMap entries, in order.
   - With `plan`/`step` None the output is byte-identical to today.
6. **Name map.** `render()` fills `names: Dict[str, Dict[Ref, str]]` (step id → Ref → engine column) for
   every Ref it printed; `results.py` reads columns only through it. The engine names of instances, model
   cards (`m_<name>` and its suffixes) and quoted nodes stay inside the emitter, which applies them to the
   plan's actions and saves. Each emitter also exports `names_for(nl, plan, sigmap)`, which computes the same
   map without rendering. These signatures are phase 0 (§10).
7. **iprobe and `isnoisy`.** `prim="iprobe"` prints as a 0 V source. `isnoisy=no`: VACASK `noisy=0`
   (resistor and `sp_resistor`); Xyce has no resistor noise switch (`NOISE=0` is "Unrecognized parameter"),
   so the resistor prints as `G<name> a b a b {1/r}`, the same current with no noise [E32]. With `tc1`/`tc2`
   it stays an `R` and noisy, with a warning.
8. **Independent sources** (`Netlist.dialect == "spectre"`; the HSPICE path is unchanged, and vcs-ams runs
   only `tran`, where both engines use the waveform):
   - VACASK uses `dc` only when `type="dc"`. For any other type it takes the t = 0 waveform value in every
     operating point, and a sweep of `dc` changes nothing: AC through a diode came out 12× wrong [E17]. A
     waveform source whose `dc` is given therefore prints `type="dc" dc=<dc>` plus every field of its
     waveform, and §5.3 switches it to its waveform around each `tran`; VACASK keeps every other parameter
     across a type change [E7, E17]. A source whose `dc` is derived prints its waveform type and no `dc`:
     VACASK's t = 0 value is then Spectre's derived dc, and it follows alters and sweeps of the fields [E38].
   - Xyce uses `DC` in `.OP`, `.DC` and the `.AC`/`.NOISE` bias, and the waveform in `.TRAN` [E17]. A given
     dc prints as `DC <dc>`; a derived one as `DC {<the t = 0 expression of the waveform fields>}`,
     recomputed in every state-replay deck, so that it never depends on Xyce's own default for a source
     without `DC` [I].
9. **Quoting.** An all-digit node name with a leading zero is printed quoted on VACASK (`'007'`): bare, VACASK
   rejects it, and quoted, `'007'`, `7` and `'00'` are distinct nodes, `'00'` not ground [E35]. Xyce keeps
   them distinct unquoted [E35]. (The HSPICE path never reached this: `spice.py` strips leading zeros.)
10. **Zero resistors.** `r` = 0 prints on VACASK as a 0 V `vsource`, a short whose flow is the resistor
    current: VACASK's `resistor` aborts every analysis with "NaN found", and `sp_resistor` clamps to 1e-12 Ω
    with a 0.08 % current error [E22]. Xyce takes R=0 [E22].

## 5. The run plan – `netlist/plan.py`

### 5.1 Ordering
- Spectre runs analyses in netlist order, and there is no default analysis [M UG pp.66-68].
- `options` statements are global: they are set while the circuit is read [M ref19 p.187]. `set`, `alter` and
  `info` are positional.
- `plan.build` walks `Netlist.analyses` and produces an ordered list of **actions**:
  - `options` (the global set first);
  - `var` (variables, §4.5 item 4);
  - `alter`;
  - `source` (a VACASK source-state switch, §5.3);
  - `analysis` (an `AnalysisStep` with its sweep context);
  - `note`.

  Actions name their targets by IR names (`.` paths, IR card names, IR parameter names); `render` translates
  them (§4.5 item 5). `plan.build` also evaluates the `ParamTest`s (§3.6) and the condition/bin rule (§3.7)
  on every instance path. A netlist without analyses runs nothing; it exits 0 with a notice and an empty
  `logFile`.

### 5.2 Sweep specifications [M ref19 pp.43-47, 64-69, 406-410]

| Spectre | meaning | VACASK (printed by render) | Xyce (printed by render) |
|---|---|---|---|
| `values=[…]`, `valuesfile=` | explicit values | `values=[…]` | `LIST …` / `.DATA` |
| `lin=N` | N steps, so N+1 points [M "number of steps"] | `mode="lin" points=N` (intervals [VACASK doc]) | `LIN N+1` (`.AC`), a step (`.DC`) |
| `step=s` | linear step | `step=s` | step / computed count |
| `dec=N` | points per decade, anchored at `start` with ratio 10^(1/N); the interval count is ⌊N·log10(stop/start)⌋ (with a 1e-9 relative tolerance), and the last point is exactly `stop` [S: Spectre 15.1 `noiva`, 4 Hz to 4.19 MHz, dec=20: 120.4 → 120 intervals, 121 points, E21] | `mode="dec" points=N` when N·log10(stop/start) is an integer; otherwise `values=[…]` computed by vamos, because VACASK rescales the span to end at `stop` [E21] | `DEC N` when an integer; otherwise `.AC DATA=`, `.NOISE DATA=` or `.DC … LIST`, because Xyce's DEC stops at the last start·10^(k/N) ≤ stop (2 Hz to 500 Hz, dec=10: 24 points ending at 399.05 Hz) [E21] |
| `log=N` | N steps, logarithmic | the N+1 log-spaced values computed | `.DATA` / `LIST` |
| no step parameter | linear when stop/start < 10, else logarithmic [M]; default lin=50 or log=50 | as above | as above |
| `center`, `span` | start/stop = center ∓ span/2 | as above | as above |

- start ≤ 0 without a step parameter is linear [I]. `center`/`span` with a log sweep is an error [I]. The open
  points of `dec`, `center`/`span` and `log` are §14 q.11.

**Targets** (IR names in the action; the engine forms come from render):
- `dev=X param=p` → `("instance", "<path>", p')`, where p' applies the instance master's `SPECTRE_MASTERS`
  renames, as on the cards; a stripped parameter is an error. VACASK `instance="x1:r1" parameter="p"`
  (hierarchical `X1.R1` → `"x1:r1"`) [E]; Xyce `.DC R1:R …` (§7.2) [E20]. A field of an independent source
  (`dc`, `val0`, `ampl`, …) follows §5.3's source rules.
- `mod=M param=p` → `("model", "<IR card>", p')`, where p' applies the card's `Model.prim` renames from
  `SPECTRE_MASTERS`; a stripped parameter is an error. VACASK `model="<card>"` [E]; Xyce `.DC M:P …` [E20]. A
  model group cannot be swept this way (a VACASK sweep names one model): error in v1.
- `sub=X1 param=p` → `("instance", "x1", p)`, with p made overridable on VACASK (§4.5 item 4) [E24]; Xyce
  sweeps a generated `.param` (§7.2) [E20].
- `param=temp` → `("option", "temp")`: VACASK `option="temp"` [E]; Xyce `.DC TEMP` [E].
- `param=<top-level parameter>` → `("variable", p)`: VACASK `variable="vamos_v_p"` [E] (a same-named `var`
  does not override a `parameters` entry [E2], hence the hook); Xyce `.DC p` [E].
- `dc` with no target is an operating point.
- A target that a condition or a bin guard reads: §3.7.

**Restore.** Spectre returns the swept parameter to its original value after the analysis [M ref19 pp.43, 64,
406]. VACASK restores a swept instance, model, option or variable after the sweep [E], although
`cmd-sweep.md` says otherwise. vamos relies on this and pins it with an engine test.

**Continuation.** Spectre's analyses restart their DC solution from scratch when a condition has changed
(`restart=yes`, the default [M ref19 pp.66, 423]); a dc sweep starts each point from the previous one
(`swpuseprevic=yes` [M ref19 p.67]). VACASK continues from the previous point by default (`continuation=1`,
`cmd-sweep.md`). So the sweep statements generated for a Spectre `sweep` block carry `continuation=0`, unless
the child sets `restart=no`; a dc analysis's own sweep keeps the default, unless it sets `swpuseprevic=no`.
On a bistable circuit the two settings give 1.83 V and 0 V at the same point [E29].

### 5.3 Sweep blocks, alter and set
- **One sweep per analysis.** A VACASK sweep applies to the single analysis after it [VACASK doc]. Each child
  of a Spectre `sweep` block is therefore printed with the enclosing sweep statement(s) (outermost first)
  directly before it. A child's own dc sweep becomes the innermost VACASK sweep.
- **Order.** Spectre runs point-major (at each point, every child); VACASK runs analysis-major. The results
  are the same, because each child starts from the netlist state at its sweep point and, with §5.2's
  continuation rule, from the same starting solution. Anything whose result could depend on that order is an
  error inside a sweep block:
  - `alter`, `set` and nested `montecarlo` children (an `info` child is a note);
  - `prevoppoint`/`useprevic`;
  - `readns`/`readic`/`write`/`writefinal` state files.
- **`alter`.** The action holds IR names and values; render prints:
  - `dev=X param=p` (not a source field) → VACASK `alter instance("x1:r1") p=v` [E: hierarchical targets
    work]; Xyce: the IR copy used for every later deck is changed (state replay, §7.2).
  - `dev=V param=type`, a waveform field or `dc`, on a vsource or isource: `Source.spectre` is updated and the
    source re-resolved with `spectre.resolve_source`. VACASK gets one complete `alter instance("<v>") type=…
    <every field of the wave> [dc=]`, every field explicit, each from `Source.spectre` or Spectre's defaults:
    val0 0, val1 1, width and period ∞ (`pw`=1e30 and no period), rise and fall by the §3.8.1 edge policy with
    its warning. `type` is never printed alone: VACASK would fill in its own defaults (val1 1, delay 0, rise
    1 ns, fall 0 = "no fall", width 0), and with the fields but no `fall` the pulse never falls [E37]. The
    printed `type` follows the source states below. Back to dc: `type="dc" dc=<dc>`. Xyce: the replayed IR
    copy gets the re-resolved Source.
  - `mod=M param=p` → `alter model("<card>") p'=v` (renamed per `Model.prim`); for a model group, every bin
    card at once (`alter model("m_b.1", "m_b.2", …)`; `alter` takes several names [VACASK doc]).
  - `sub=X1 param=p` → `alter instance("x1") p=v`, with p overridable (§4.5 item 4) [E24].
  - `param=temp` → `options temp=v`; `param=<netlist param>` → `var vamos_v_p=v`.
  - A value that changes a condition or a bin: §3.7.
- **Source states on VACASK.** VACASK uses `dc` only when `type="dc"` [E17], so a waveform source whose `dc` is
  given is printed `type="dc"` with its waveform fields (§4.5 item 8), and `plan.build` switches it for every
  `tran`: `alter instance("<v>") type="<wave>"` before the tran action (ahead of its options and sweep
  lines, §7.1) and `alter instance("<v>") type="dc"` after it, one `alter` per source [E17]. A source whose
  `dc` is derived stays in its waveform type [E38]. An alter of a derived source's `dc` makes it given from
  then on (`alter instance("<v>") type="dc" dc=<v>`); a sweep of it brackets that one analysis with
  `type="dc"` before and `type="<wave>"` after. Xyce needs no switching: its decks print `DC` and the
  waveform together (§4.5 item 8).
- **`set`** → an `options` action. Option changes between analyses are accepted by VACASK [E].

### 5.4 Transient settings
- **errpreset.** The value is the `+aps=`/`++aps=` value, which overrides every tran [M ref19 p.35], else the
  tran's own `errpreset`, else `+errpreset=` [I] (§14 q.25), else `moderate`:

| errpreset | reltol (from the options reltol) | relref | method | maxstep ≤ | lteratio |
|---|---|---|---|---|---|
| liberal | ×10, at most 0.01 | sigglobal | trapgear2 | T/50 | 3.5 |
| moderate | ×1 | sigglobal | traponly | T/50 | 3.5 |
| conservative | ×0.1 | alllocal | gear2only | T/100 | 10 |

  - T = stop − start [M ref19 p.431]; the 5.1.41 UG liberal row (allglobal, gear2, T/10) is obsolete. Real
    headers agree for all three rows [S: 19.1 isr7 liberal (reltol 0.01, trapgear2, maxstep 2e-11 for
    stop = 1 ns), 23.1 moderate, 19.1 isr8 conservative (reltol 1e-7, gear2only, T/100)].
  - reltol is always the options reltol times the factor, capped at 0.01. `tran` has no reltol of its own,
    and a reltol the user set (in `options` or `set`) is scaled too: "Except for reltol and maxstep, errpreset
    does not change the value of any parameters you have explicitly set" [M ref19 p.431]. ADE always writes
    `reltol=1e-3`, so conservative gives 1e-4.
  - A tran's own `relref`, `method` or `lteratio` wins over the table; `maxstep` becomes min(user, bound) [M].
  - errpreset also selects Spectre's LTE check (liberal: capacitor and inductor states; moderate and
    conservative: node voltages, loosely or strictly [M ref19 p.431]). vamos keeps `tran_lteimplicit=0` (LTE on
    charges only, the vcs-ams setting) for every preset (§14 q.22).
  - VACASK gets `options reltol= relref= tran_method= tran_lteratio=` before the analysis and the previous
    values after it [E]. `relref=pointlocal` → `relrefsol=relrefres=relreflte="pointlocal"`.
  - VACASK method map: `traponly`/`trap` → `"trap"`; `gear2`/`gear2only` → `"gear2"`; `euler` → `"euler"`;
    `gear3`/`gear3only`/`gear2gear3` → `"gear"` with `tran_maxord=3`; `trapgear2`/`trapeuler` → `"trap"`
    (note). So liberal gives `"trap"`, with that note.
  - Xyce gets the method only, and accepts `METHOD=TRAP` and `METHOD=GEAR` only [E30]: `trap traponly
    trapgear2 trapeuler` → `TRAP`; `gear2 gear2only` → `GEAR` with `MAXORD=2`; `gear3 gear3only gear2gear3` →
    `GEAR` with `MAXORD=3`; `euler` → `GEAR` with `MAXORD=1`. The tolerances are a note.
- **Initial conditions.**
  - `ic=all` (default): `ic` statements plus device `ic` parameters. `node`: the statements only. `dev`: the
    device parameters only. `dc`: none [M ref19 p.417].
  - `ic` statements set the starting point of a transient analysis only [M UG p.200]: VACASK `ic={…}` with
    `icmode="op"` (forced during the initial operating point) on the `tran`; Xyce `.IC` only in a tran deck
    whose `ic` is `all` or `node`, because Xyce applies `.IC` to every DC solve, so an `ic` statement would pin
    the op, every `.DC` point and the `.AC`/`.NOISE` bias [E18].
  - Nodesets: VACASK `nodeset={…}` on every analysis that solves an operating point (op, swept op, ac,
    noise, acxf, tran); Xyce `.NODESET` in every deck except a tran deck that has `.IC`, which drops it with a
    note, because Xyce aborts on both [E18].
  - `skipdc=yes` → VACASK `icmode="uic"`, Xyce `UIC` [I].
- **prevoppoint / useprevic** on VACASK [E]:
  - the previous analysis stores its solution (`store="vamos_s<k>"`);
  - `prevoppoint` → `nodeset="vamos_s<k>" opsolve=0`. After a `tran` this linearizes at the final transient
    point (a diode testbench: AC gain 0.019 against 0.99996 from a fresh operating point);
  - `useprevic=ns` → `nodeset=`; `useprevic=yes` → `ic=` (a stored-solution name [VACASK doc]).
- **State files.** `readns`/`readic` become nodeset/ic lists read from the file (§8.6). A missing file is a
  note: the UG's own idiom reads and writes the same file. `write`/`writefinal` hold the full solution, every
  node voltage and source current [M UG pp.203-204], but an explicit save list limits what the engines write.
  So an analysis that writes a state file saves every node in the engine: VACASK `clear saves` + `save
  default` around it; Xyce `V(*)` plus every V and L current. These extra signals feed the file and are
  filtered out of the PSF, which keeps the user's selection.

### 5.5 Per-analysis saves
An analysis-level `save=`/`nestlvl=` gives VACASK `clear saves` plus that analysis's saves before it, and the
global saves again after it. On Xyce each deck has its own `.PRINT`.

### 5.6 Noise without an input probe
Spectre's `iprobe` is optional [M ref19 p.181], but VACASK's `noise` needs `in=` (without it: "Instance ''
not found.", and the analysis aborts) and Xyce's `.NOISE` names a source [E19]. Without `iprobe`, `plan.build`
names an input `vamos_nin<k>` in the step, and `signals.resolve` adds `vamos_nin<k> (p n) isource dc=0`
across the output pair (for `oprobe`, its terminals) to its IR copy. A 0 A current source is an open circuit
with no noise, so `out` and every contribution are unchanged [E19]. `in` and `gain` are not written.
`vamos_nin<k>` is a vamos probe (§6.4): never an output, and acxf drops its `tf()`.

## 6. Signals and names – `netlist/signals.py`

### 6.1 What is saved [M ref19 pp.188-190]
- `options save=` defaults to `selected`: the user's saves, or `allpub` when no node voltage is saved.
- `allpub`: every node voltage (public nets of the netlist at every level, not device-internal nodes), the
  vsource currents, the inductor currents and the iprobe currents. `lvlpub` limits it to `nestlvl` levels.
  `all`/`lvl` would add device-internal nodes; in v1 they are not written (note).
- `none`: one node voltage, the first in sorted order (note). `nooutput`: no data files.
- `currents=selected` is the default. `all` or `nonlinear` → warning: only the explicitly saved currents are
  written. `subcktprobelvl>0` and `pwr≠none` → warning.

### 6.2 save statement items [M ref19 pp.517-519]
- Items: a node (`out`, `OpAmp1.comp`, integer names), `X:term` (a terminal name of §3.8 or its 1-based
  index [M ref19 p.518]; outputs use the name), `X:currents` (all terminals; a two-terminal component gives
  only the first [M]), `X:oppoint`, `X:<opvar>`, `X:pwr` (both warnings: not written), a bare component name
  (its currents and, as a warning, its operating point), and the wildcards `*` and `?` with `depth`,
  `sigtype`, `devtype`, `subckt` and `exclude`.
- Items are resolved against the flattened IR. When a name is both a node and an instance, the node wins
  [M UG p.61]. An item that matches nothing gets a warning and is not passed on: Xyce aborts on an unknown
  node, and VACASK fails every analysis but exits 0 [E28]. `time_window` filters the written points.

### 6.3 Currents
- vsource `V1:p` → VACASK `i(v1)` (column `v1:flow(br)`) / Xyce `I(V1)`. The sign matches Spectre's "positive
  into the terminal" [M ref19 p.518]: −1 mA for 1 V across 1 kΩ [research E], and −0.5 mA for 1 V across
  2 kΩ on both engines [E]. `V1:n` is the negation.
- inductor `L1:1` → `i(l1)` (column `l1:flow(br)`) / `I(L1)`; iprobe `I1:in` → `i(i1)` / `I(VI1)`: +1 mA for
  1 mA into terminal 1 or `in`, on both engines [E36]. `L1:2` and `I1:out` are the negation.
- **Every other terminal** (resistor, capacitor, diode, BJT, MOS, a source's `sink`/`src`, a subckt-instance
  port, a Verilog-A port): `signals.py` inserts a 0 V probe source `vamos_tp<k>` in series with the terminal,
  in a copy of the IR. The terminal moves to a new node `vamos_tn<k>`, and the source runs from the original
  node to the new one. Its branch current is the current into the terminal, Spectre's sign, on both engines
  alike. An infinite resistor's terminal currents are written as 0 (§3.8).

### 6.4 Forward name map
`signals.resolve` lists, per step, the requested signals as `(Ref, Spectre name, psf type)`; `render` records
each Ref's engine column in `names` (§4.5 item 6). Columns outside the map, such as VACASK's
`d1:implicit_equation_0` [E] or device-internal nodes, are never written.

| quantity | Ref | VACASK column | Xyce `.PRINT` column | Spectre name |
|---|---|---|---|---|
| top-level node `out` | `('v', 'out')` | `out` [E] | `V(OUT)` [E] | `out` |
| node `mid` in instance `x1` | `('v', 'x1.mid')` | `x1:mid` | `V(X1:MID)` [E] | `x1.mid` (original case) [S] |
| vsource current | `('i', 'v1')` | `v1:flow(br)` [E] | `I(V1)` [E] | `V1:p` |
| inductor, iprobe current | `('i', 'l1')`, `('i', 'i1')` | `l1:flow(br)`, `i1:flow(br)` [E36] | `I(L1)`, `I(VI1)` [E36] | `L1:1`, `I1:in` |
| probed terminal | `('i', 'vamos_tp<k>')` | `vamos_tp<k>:flow(br)` | `I(VAMOS_TP<K>)` | `<inst path>:<term>`, e.g. `I2.Idiffpair:in` [S] |
| sweep variable | — | the sweep's name [E] | the `sweep` column / one plot per `.STEP` [E] | §8.3 |
| AC frequency | — | `frequency` | `FREQUENCY` | `freq` [S] |
| noise total | `('onoise',)` | `onoise`, V²/Hz [E] | `ONOISE`, V²/Hz [E] | `out` = √, V/sqrt(Hz) [S] |
| noise gain, input noise | `('gain',)`, `('inoise',)` | `gain` [E] | `INOISE` [E] | `gain`, `in` (§8.4) |
| per-instance noise | `('noise', 'r1')` | `n(r1)`, `n(r1,thermal)` [E] | `DNO(R1)` [E] | STRUCT `R1` with `rn`, `total` [S] |
| xf | `('tf', 'v1')` | `tf(v1)` [E] | — | `V1:p` for a vsource [M UG p.113]; an isource: §14 q.15 |

- The hierarchy separator is `.`, and the case is the original (Spectre is case sensitive [M]).
- An inline subckt's component named like the subckt takes the instance's name (`q1`, not `q1.npn_mod`) [M UG
  p.111].
- With `+escchars`, characters outside `[A-Za-z0-9_.:!]` are backslash-escaped (`"I48.LOGIC_OUT\<3\>"` [S],
  §14 q.12).
- **Columns come only from `names`.** `xyce.render` prints exactly the step's SignalMap entries, in order, so
  Xyce's upper-casing [E] never matters. For allpub (`SignalMap.allpub`), where listing every node would make
  huge `.PRINT` lines, the decks print `save default` (VACASK) or `V(*)` plus the source currents (Xyce), and
  `names` maps each IR net to its column, case-insensitively on Xyce; a column that is not an IR net is
  dropped. Spectre names that differ only in case are an error on Xyce (the existing `xyce.py` rule).
- vamos's own probe sources (§5.6, §6.3) are never outputs. In particular `xf` writes transfer functions only
  for the netlist's sources, although the engine also computes them for the probes.

## 7. Engines

### 7.1 VACASK: one deck, one run
- `vacask.render(nl, …, plan=…, sigmap=…, names=…)` writes `<run>/vamos.sim`, run as `vacask vamos.sim` in
  the run directory, with `engines.env_for("vacask", …)` (`SIM_OPENVAF`, `SIM_MODULE_PATH`). The Verilog-A
  `.osdi` files come from §1 step 10.
- Analysis names in the deck are `vamos_a<k>`. That avoids quoting, reserved words and file-name collisions
  on case-insensitive filesystems. The plan maps them back.

Control block of the worked example in §7.3:

```
control
  abort except analysis
  options rawfile="binary" strictoutput=0 tran_lteimplicit=0 temp=27.0 tnom=27.0 reltol=0.001 vntol=1e-06 abstol=1e-12 gmin=1e-12
  var vamos_v_rval=1000.0
  save default
  analysis vamos_a1 op
  analysis vamos_a2 ac from=1.0 to=1000000.0 mode="dec" points=10
  options relref="sigglobal" tran_method="trap" tran_lteratio=3.5
  sweep vamos_w1 variable="vamos_v_rval" values=[1000.0, 2000.0] continuation=0
    analysis vamos_a3 tran step=5e-06 stop=0.005 maxstep=0.0001
  options relref="alllocal"
endc
```

A `sweep` statement binds to the analysis directly after it. An `options` line between them is a parse
error ("expecting sweep or analysis") [E], so per-analysis `options` lines and the source-state `alter`s of
§5.3 go before the sweep statements, and the restoring ones after the analysis [E].

| Spectre | VACASK |
|---|---|
| `dc` (no target) | `analysis a op` |
| `dc` with a target | `sweep … ` + `analysis a op` (a nested sweep for an enclosing block) |
| `ac` frequency sweep | `analysis a ac from= to= mode= points= \| step= \| values=` (§5.2) |
| `ac` parameter sweep at `freq` | `sweep …` + `analysis a ac values=[freq]` |
| `noise` | `analysis a noise out="p" \| out=["p","n"] in="<src>" …` [E], `in` being `vamos_nin<k>` when the netlist gives none (§5.6); `save full` for per-source contributions [E] |
| `xf` | `analysis a acxf out=…` [E] |
| `tran` | `analysis a tran step= stop= [start=<outputstart>] maxstep= [icmode=] [ic=] [nodeset=]` |
| `sweep {…}` | sweep statements with `continuation=0` (§5.2) before every child |
| `alter`, `set`, `options` | `alter instance(…)`, `alter model(…)`, `var`, `options` |
| `ic` / `nodeset` | `ic={"n", v, …}` on `tran` only; `nodeset={…}` on every analysis that solves an operating point (op, swept op, ac, noise, acxf, tran) |
| saves | `save default` (allpub), or explicit `v('x1:mid')` / `i(v1)` lists from the SignalMap |

**Failures:**
- An analysis succeeded only if `vamos_a<k>.raw` exists and holds the planned points (for a swept analysis,
  the product of the outer counts in groups). A planned analysis without its rawfile, or with fewer groups,
  has failed (exit status 1), whatever VACASK printed or returned [E28].
- VACASK's messages are reported with the analysis name: `Analysis 'vamos_aK' aborted.` (VACASK still exits 0
  under `abort except analysis`, its default policy [E6]); `Sweep aborted @ k/n (v).`; and `Failed to bind
  analysis outputs.` / `Failed to bind sweep parameters.`, which print no "aborted" line, write no rawfile
  and exit 0 [E28].
- Inside a sweep, VACASK prints `Sweep aborted @ k/n (v).`, runs no later point, and restores the target.
  Under the default `strictoutput=2` no rawfile of that analysis is left [E6]; vamos sets `strictoutput=0`,
  so the rawfile keeps the complete rows of points 1…k−1 under a blank `No. Points:`, which `rawfile.read`
  accepts [E27]. vamos writes those leaves, marks points k…n failed (exit status 1), and warns.
- A non-zero VACASK exit (parse, elaboration, unknown parameter, a failed `alter`/`var`/`options`) → exit
  status 2, with the engine's message; the analyses that finished before it are converted (§2.7).

**Reading.**
- `rawfile.read` reads the binary rawfiles, through `names` (§6.4).
- A swept analysis is one plot: the sweep columns come first and the points are concatenated, with no
  `Dimensions` header [E]. Consecutive sweep values may be equal (`values=[1k 1k 2k]`) [E], so the split never
  goes by value. It uses the known number of outer points and the restart of the inner axis: time and
  frequency increase within a point, and an `op` contributes one row per point. It checks that the number of
  groups equals the product of the outer counts.

### 7.2 Xyce: one deck per analysis
- Xyce runs one analysis type per netlist [research E: "Analysis type TRAN and print type AC are
  inconsistent"].
- Each `analysis` action becomes `<run>/vamos_a<k>.cir`. It is rendered from a copy of the IR with the
  alters, sets and options up to that point applied (state replay), with `xyce.render(…, step=…)`, and run
  sequentially as plain `Xyce vamos_a<k>.cir`.
- **Parameter check.** Xyce ignores an unknown model parameter with only a warning ("No model parameter …
  found"), and a plain run exits 0; only a `.STEP` of it is an error [E43]. Before the first run, `Xyce -norun`
  on the first deck checks the netlist (the `xyce.smoke` patterns); the output of every deck run is then
  scanned for the same patterns (`No model parameter … found`, `Unrecognized parameter`; binned-card bounds
  exempt). A match is exit status 2, naming the alter or sweep that introduced it.

| Spectre | Xyce |
|---|---|
| op | `.OP` + `.PRINT DC FORMAT=RAW FILE=vamos_a<k>.raw <columns>` [E]. Under two or more `.STEP`s: `.param vamos_op=0` + `.DC vamos_op LIST 0`, because `.OP` there stops with "Analysis mode 4 is not available" and a segfault, while one `.STEP` with `.OP` works [E20] |
| `dc dev=V param=dc` | `.DC V start stop step` / `.DC V LIST …` [E20] / `.DC DEC V …` (integer spans only, §5.2) [E21] |
| `dc param=temp` | `.DC TEMP …` [E] |
| `dc param=<top-level param>` | `.DC p …` [E] |
| `dc dev=R param=r`, `mod=M param=x` | `.DC R1:R …` / `.DC M:X …` (hierarchical `X2:R1:R` too): one DC plot, like every other dc sweep [E20] |
| `dc sub=X1 param=w` | X1's override becomes `w={vamos_x_x1_w}`, with `.param vamos_x_x1_w=<nominal>`, swept by `.DC vamos_x_x1_w …`: an X-line override follows a swept `.param` [E20] |
| `dev=` a field of an independent source | the field prints through a generated `.param vamos_x_<src>_<field>` (as for `sub=`), which a derived `DC` expression reads too [I] |
| `ac` | `.AC DEC n f1 f2` (integer spans, §5.2) / `.AC LIN N+1 f1 f2` / `.AC DATA=<t>` with `.DATA` [E] |
| `ac` parameter sweep at `freq` | `.STEP …` + `.AC DATA=<t>` with one frequency [E] |
| `noise` | `.NOISE V(p,n) <src> …` (frequencies per §5.2; `DATA=` works [E21]) + `.PRINT NOISE FILE=vamos_a<k>.prn ONOISE INOISE DNO(<inst>)…`; the input source prints as `AC 1 0` (§8.4) [E19]. Xyce refuses RAW for noise ("Noise output cannot be written in PROBE, RAW or Touchstone") and writes a standard table [E], read by a new `rawfile.read_prn`. Under `.STEP` the table's Index column restarts at 0 for every step, with no label or separator [E33], so `read_prn` splits it at each reset and takes the step values from the deck's `.STEP` order |
| `xf` | error |
| `tran` | `.TRAN step stop [outputstart [maxstep]] [UIC]`; `.OPTIONS TIMEINT METHOD=TRAP\|GEAR [MAXORD=n]` (§5.4) [E30] |
| sweep blocks | `.STEP` lines innermost first: Xyce's first `.STEP` is the fastest loop, the reverse of VACASK's order [E20]; the leaves are decoded in that order |
| options `temp tnom gmin` | existing emitter rules (temp and tnom are always set, §3.10) |
| `ic` statements, `readic` | `.IC` only in a tran deck whose `ic` is `all` or `node`: Xyce applies `.IC` to every DC solve [E18], and Spectre's `ic` is a transient initial condition [M UG p.200] |
| `nodeset`, `readns` | `.NODESET` in every other deck; a tran deck with `.IC` drops it with a note, because Xyce aborts on both [E18] |

- **Output.** `.PRINT … FORMAT=RAW FILE=` writes exactly the printed columns (`V(OUT)`, `I(V1)`), where `-r`
  writes every node [E]. Each `.STEP` point is one plot, with the step value in its plot name [E].
- **Failures.** A non-zero exit [E: a singular `.OP` exits 1], or a missing or short print file, means the
  analysis failed (exit status 1). "Netlist error" or a parse error in the output means exit status 2. Either
  way the decks that finished are converted (§2.7).

### 7.3 Worked example
```
// RC Low-Pass Filter
simulator lang=spectre
parameters rval=1k
V1 (in 0) vsource type=pulse val0=0 val1=1 delay=1m rise=1u fall=1u width=4m period=10m mag=1
X1 (in out) rcsec r=rval
subckt rcsec (a b)
  parameters r=1k
  R1 (a b) resistor r=r
  C1 (b 0) capacitor c=1u
ends rcsec
myop dc
myac ac start=1 stop=1M dec=10
swp sweep param=rval values=[1k 2k] {
  tr tran stop=5m
}
```
- **VACASK:** the control block of §7.1, with `rval` hooked as a variable, `tr`'s maxstep = T/50 = 1e-4, and
  the sweep block's `continuation=0`. V1 has no `dc`, so it stays a pulse and needs no state switching (§5.3).
- **Xyce:** three decks: `.OP`, `.AC DEC 10 1 1e6` (6 whole decades), and `.STEP rval LIST 1000 2000` with
  `.TRAN 5e-06 0.005 0 0.0001`. V1 prints `DC 0` (its derived t = 0 value, `val0`) with its `PULSE`.
- **Results** (modern names): `myop.dc`, `myac.ac`, `swp_tr.sweep`, `swp-000_tr.tran.tran`,
  `swp-001_tr.tran.tran`, and `logFile`.
- **Reference values** (research [E], the same RC on VACASK): `out(5 ms)` = 0.98173, and `out(1 Hz)` =
  0.99996 − j0.00628 for `rval` = 1k.

## 8. Outputs

### 8.1 Results directory and file names

| analysis | modern (default) [S 23.1] | legacy (`--vamos-psf-names=legacy`) [M UG pp.230-232] |
|---|---|---|
| `dc` (op or sweep), name n | `n.dc` | `n.dc` |
| `ac` / `noise` / `xf` | `n.ac` / `n.noise` / `n.xf` | same |
| `tran` | `n.tran.tran` | `n.tran` |
| child c of sweep s, point i | `s-00i_c.<ext>` | `s_00i_c.<ext>` |
| sweep parent (one per child) | `s_c.sweep` | — [I] |
| nested sweep t in s | `s-00i_t-00j_c.<ext>`; parent `s-00i_t_c.sweep` | — |
| SPICE mode | `opBegin.dc`, `srcSweep.dc`, `frequencySweep.ac`, `timeSweep.tran.tran` [I] | `timeSweep.tran` [M] |

The point index has 3 digits [S] (more for ≥ 1000 points [I]). The psf format family is irrelevant to the
names: psfascii is always what is written.

### 8.2 `logFile` [S]
```
HEADER
"PSFversion" "1.00"
"Log Generator" "drlLog rev. 1.0"
"Log Time Stamp" "<ctime>"
"psfversion" "1.4.0"
"simulator" "spectre"
"version" "<vamos version>"
"date" "<h:mm:ss AM, Day Mon d, yyyy>"
"design" "<title line>"
"signalNameType" "spectre"
"simMode" "Spectre"
"measdgt" 0
"ingold" 2
"sst2usecolon" 0
TYPE
"analysisInst" STRUCT(
"analysisType" STRING *
"dataFile" STRING *
"format" STRING *
"parent" STRING *
"sweepVariable" ARRAY ( * ) STRING *
"description" STRING *
)
VALUE
"<key>" "analysisInst" ( "<type>" "<dataFile>" "PSF" "<parent key>" (<sweep vars>) "<description>" ) PROP( … )
END
```
- **Keys:**
  - `<name>-<type>`: `myop-dc`, `mytran-tran`; the type is `tran` although the file is `.tran.tran`;
  - sweep parents `<s>_<c>-sweep`, leaves `<s>-NNN_<c>-<type>`;
  - Monte Carlo (phase 3): nominal run `<c>-<type>`, then parents `<m>_<c>-montecarlo` without a PROP, then
    leaves with `"iteration" k`.
- **PROP:**
  - `data_type` is `scalar` for `dc` (op and sweep alike), `swept_scalar` for `ac`, `tran` and `xf` [I for
    `xf`], `swept_struct` for `noise`, and `struct` for `info`;
  - parents carry `"sweep_tree_type" "sweepNode"`;
  - leaves carry `"sweep_tree_type" "leafNode"` and the swept value (`"R1:r" 1000.00`).
- **Order:** execution order, with sweeps point-major as Spectre runs them: all parent entries of a sweep,
  then per point its leaves and nested parents [S]. vamos re-orders VACASK's analysis-major results.
- **Writing:** `logFile` is written once at the end, after every exit status (§2.7). It lists the analyses
  that finished, and after status 3 the interrupted one. It is always PSF ASCII.
- **Format fields.** `"simulator" "spectre"`, `"Log Generator"`, `"simMode"` and `"signalNameType"` are
  data-format fields that readers may key on, so they keep Spectre's values. They are constants of
  `output/psf.py`, not banner keys, so a banner profile (`none` included) never changes them. `"version"` is
  always vamos's, never a Spectre version string (`VAMOS_PLAN.md` §4b).

### 8.3 PSF ASCII data files [S]
- **Grammar:** `HEADER` named values, `TYPE`, `SWEEP`, `TRACE`, `VALUE`, `END` (the psf_utils grammar,
  matching every sample).
- **Strings** escape `"` and `\` with a backslash: a 19.1 file has `"design" "(\"test\" \"sweep\"
  \"schematic\" \"\")"` [S]. A title containing `"` written unescaped would make the file unparsable.
- **Header:**
  - `PSFversion`, `simulator`, `version`, `date`, `design`, `analysis type`, `analysis name`,
    `analysis description`;
  - `xVecSorted`: `ascending` for ac, tran and Monte Carlo parents, `unsorted` for dc, `unknown` for sweep
    parents and info;
  - `tolerance.relative`;
  - the analysis settings vamos asked the engine for (tran: `start outputstart stop step maxstep ic useprevic
    skipdc reltol abstol(V) abstol(I) temp tnom tempeffects errpreset method lteratio relref cmin gmin`);
  - noise: `"operating point producer"`, `"output" "pair of nodes"`, `"ground" "0"`, `"positive output
    signal"`, `"negative output signal"`.
- **TYPE:** only the types used. `"V" FLOAT|COMPLEX DOUBLE PROP("units" "V" "key" "node" "tolerance" …)`, `"I"
  … "branch"`, and `"sweep" FLOAT DOUBLE PROP("key" "sweep")`. Readers resolve types by name, and psf-parser
  reads files that carry only the types used [research E].
- **SWEEP:** `"time"` (s), `"freq"` (Hz), or the dc sweep variable, each with
  `PROP("sweep_direction" 0 "units" … "plot" 0 "grid" g)`. `grid` is 1 for a linear sweep and 3 for a
  logarithmic one [S]. The dc sweep variable is the parameter name: `"dc"` for a source [S], `"temp"` and the
  netlist parameter name [I]. Parents use `"<dev>:<param>"` (`"R1:r"`, units `"Ohm"`) [S], and Monte Carlo
  parents `"iteration"` (units `"real"`) [S].
- **VALUE:**
  - swept: the sweep value, then one `"sig" v` per trace, repeated per point;
  - op: `"sig" "V" v`, and for currents `"V1:p" "I" v PROP("units" "A")` [S];
  - complex values are `(re im)`;
  - numbers are printed `%.15e` [S], or per `options precision`.
- **Descriptions** [S]: `` DC Analysis `myop' ``, `` DC Analysis `mydc': Vin:dc = (0 V -> 2 V) ``,
  `` AC Analysis `myac': freq = (1 Hz -> 1 MHz) ``, `` Transient Analysis `mytran': time = (0 s -> 500 ms) ``,
  `` Noise Analysis `noiva': freq = (4 Hz -> 4.19 MHz) ``, `Sweep parent`, `Monte Carlo parent`. Numbers are
  `%g` with an SI prefix and unit.

### 8.4 Noise and xf transforms
- **Noise [S layout, E values]:**
  - `out` = √onoise, in V/sqrt(Hz). A 1 kΩ/1 kΩ divider gives onoise = 8.288e-18 V²/Hz (VACASK) and 8.2879e-18
    (Xyce) [E], so out = 2.879e-9.
  - Per instance there is one STRUCT (V^2/Hz) with members from `n(inst,contrib)` plus `total`. The resistor
    contribution `thermal` is renamed `rn` [S]; a resistor card with flicker noise adds `fn` [S]; a Verilog-A
    module keeps its own contribution names (`flicker`, `thermal`) [S]. Other devices keep VACASK's
    contribution names (note; §14 q.14). Each distinct member set is a STRUCT type named after the instance's
    model, or its master when it has none, with `PROP("key" "inst" "master" "<master>")`: `"rref"` for a
    resistor with model `rref`, `"resistor"` for one without, `"res_va"` for a Verilog-A module [S: 15.1
    `noiva`]. On Xyce there is only the `total` member (from `DNO`), with a note.
  - With an input probe: `gain` = √(power gain) and `in` = out/gain = √(input-referred PSD) [M: IRN =
    sqrt(No²/G²)] [I for the units and type names of `gain`/`in`]. On VACASK the power gain is its `gain`,
    which does not depend on the source's `mag` [E19]. On Xyce, gain = √(ONOISE/INOISE) and in = √INOISE, with
    the input source printed `AC 1 0` whatever its netlist `mag`/`phase` (noise does not use them): Xyce divides
    INOISE by that source's AC magnitude squared, so Spectre's default `mag=0` would give INOISE = 8.3e2 V²/Hz
    for the divider [E19]. `F` and `NF` need a noisy input `port`, which is not in v1.
- **xf:** one complex trace per independent source, `tf(<src>)`, named `<src>:p` for a vsource ("closed-loop
  gain, Av = Vdif:p" [M UG p.113]); isource names are §14 q.15. `zin`/`yin` are not written.

### 8.5 nutascii / nutbin [I]
One rawfile at the results path, with one plot per analysis (sweep leaves included) in execution order. The
variables carry the §6.4 names and the types `time`, `frequency`, `voltage` and `current`. If the path is an
existing directory, the file is `<dir>/%C:r:t.raw` (note). It is written by `output/nutmeg.py`; the tests read
it back with `rawfile.read_all`.

### 8.6 State files [M UG pp.203-204]
A text file: `#` starts a comment, then one `signal value` per line, holding node voltages and source currents
(`vcc:p`). `write=` takes the first point of the analysis and `writefinal=` the last, from the full-solution
saves of §5.4. `readns`/`readic` parse the same format into nodeset and ic lists, with names checked as in
§3.10.

### 8.7 The `+log` file and the screen [M UG pp.32-33, 270, 274]
The same lines go to the screen and/or the file (§2.5), through `spectre/log.py`, with layout from the banner
profile `banners/spectre.json`:
```
<provenance header, VAMOS_PLAN §4b>
Simulating `input.scs' on <host> at <time>.

Circuit inventory:
              nodes 5
              bjt 2
          capacitor 4
              …
***************************************************
Transient Analysis `OscResp': time = (0 s -> 80 us)
***************************************************
<filtered engine narration>
Total time required for tran analysis `OscResp' was 7.08 s.

Aggregate audit (<time>):
Time used: CPU = 7.29 s, elapsed = 8 s.
<prog> completes with 0 errors, 0 warnings, and 0 notices.
```
- **Messages** use the 5.1.41 form: `Error from <prog> during <phase>.`, `Warning from <prog> …`, `Notice from
  <prog> …`, each followed by the indented text [M UG pp.270, 274].
- **Counts** are vamos's notes plus the engine's failures and warnings.
- **The last line** is `<prog> completes with N errors, M warnings, and K notices.` after exit status 0 or 1,
  and `<prog> terminated prematurely due to fatal error.` after exit status 2 [M]. After exit status 3 it is
  the latter as well [I].
- **The trailer is always written.** When the banner profile is `none` or lacks the key, `log.py` uses its
  built-in text (`Banner.text()` returns None then, and `VAMOS_PLAN.md` §4b's `none` prints only the provenance
  header).
- `<prog>` is the invoked name (§2.1). That is the program the user ran, not vendor dialogue, and scripts grep
  that line.
- **Not printed:** VACASK's and Xyce's own banners, and Spectre's version banner (`VAMOS_PLAN.md` §4b).

## 9. ADE compatibility
- **A typical ADE command line** (cited verbatim by the research from a Cadence command-line tutorial):
  `spectre -64 butterworth.scs +escchars +log ./butterworth/psf/spectre.out -format psfascii -raw
  ./butterworth/psf +lqtimeout 900 -maxw 5 -maxn 5 +logstatus`. Every token has a disposition in §2.3. An APS
  variant adds `++aps +mt=4 +lqsleep 30 +lsusp -env ade` and `-format psfxl` (psfascii is written, note).
- **`input.scs` features** (the BAM control file [research V] and ADE habit):

| feature | disposition |
|---|---|
| `simulator lang=spectre`, `global 0`, `include "…" section=tt` | mapped |
| `simulatorOptions options temp tnom scale=1.0 scalem=1.0 reltol vabstol iabstol gmin rforce maxnotes maxwarns digits pivrel checklimitdest psfversion sensfile cols` | §3.10 (mapped or noted) |
| `tran tran stop=… write="spectre.ic" writefinal="spectre.fc" annotate=status maxiters=5` | mapped (state files written from full saves; `tran_itl=5` on VACASK) |
| `finalTimeOP info what=oppoint where=rawfile`, `modelParameter info what=models …`, `element info what=inst …`, `outputParameter info what=output …`, `designParamVals info what=parameters …`, `primitives`/`subckts` | note: the `.info` files are not written; the `logFile` does not list them |
| `saveOptions options save=allpub` | mapped (§6.1) |
| `wave_out options rawfmt=sst2` | psfascii written (note) |
| instance names `I0`, bus nets `net\<3\>` | escapes unescaped (§3.2); written back escaped under `+escchars` |
| vpulse/vpwl cells with a separate "DC voltage" | `dc` given: the DC-type analyses use it (§3.8.1, §4.5 item 8) |

- **Not vamos's job:** ADE itself writes `runObjFile`, `artistLogFile`, `variables_file` and `simRunData` [I].
- **Open:** whether ADE probes `-V`/`-W` output (§14 q.16), accepts psfascii where it asked for psfxl (q.17),
  and parses `spectre.out` beyond the trailer (q.18).

## 10. Modules and APIs

Python rules as in `VAMOS_AMS_DESIGN.md` §2.3: `from __future__ import annotations`, Python 3.9, stdlib only,
no module run as a script. Dataclasses are valid on 3.9: a field with no default never follows one with a
default (there is no `kw_only`) [E48]. Everything marked phase 0 is frozen before phase 1 (§12).

```python
# vamos/personalities/spectre.py
OPTIONS: List[Opt]; TABLE: Table                       # §2.3
def build_job(argv: List[str], env: Mapping[str, str], cwd: str, prog: str) -> SpectreJob
def main(args: List[str], opts: dict) -> int            # cli entry; opts['argv0'] = the invoked name (§2.1);
                                                        # returns the Spectre exit status

# vamos/spectre/job.py  (phase 0)
@dataclass
class SpectreJob:                       # implements the job protocol optable.scan uses: note(), unmapped
    prog: str                                           # the only positional fields: prog, argv, cwd
    argv: List[str]
    cwd: str
    netlist: Optional[str] = None                       # as given; None = stdin
    raw: Optional[str] = None
    fmt: Optional[str] = None
    outdir: Optional[str] = None
    log_mode: str = "screen"                            # screen | both | file
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
    unmapped: List[Unmapped] = field(default_factory=list)
    def note(self, option: str, disposition: str, note: str = "") -> None

@dataclass
class Settings:                                         # the merged §2.2 layers, after parsing
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

# vamos/spectre/args.py
def defaults_tokens(env: Mapping[str, str], prog: str) -> List[str]
def prescan(tokens: List[str]) -> Tuple[List[str], Dict[str, Optional[str]]]   # removes +%X s / -%X
# vamos/spectre/percent.py
def codes(job: SpectreJob, start: datetime, analysis: str = "") -> Dict[str, str]
def expand(text: str, codes: Mapping[str, str]) -> str  # %X, colon modifiers, %%
# vamos/spectre/cpp.py
def preprocess(path: str, job: SpectreJob, run_dir: str) -> Tuple[str, str, List[Note]]   # (cpp.out, title, notes)

# vamos/netlist/spectre.py
@dataclass
class SpectreParseOpts:
    search: List[str] = field(default_factory=list)          # the -I directories
    percent: Mapping[str, str] = field(default_factory=dict) # %-codes for quoted strings (§2.4)
    pre: List[str] = field(default_factory=list)             # +pre_config fragment files
    post: List[str] = field(default_factory=list)            # +config fragment files
    cpp_markers: bool = False   # the input is cpp output: origins, include directory and language per
                                # '# <line> "<file>" [flags]' marker (§2.6)
    title: Optional[str] = None # line 1 of the original file when cpp ran
    mts: bool = True            # False under -mts: options inside subckts are global (§3.6)
def parse(path: str, cwd: str, opts: SpectreParseOpts) -> Netlist   # NoteError with every note on error
def number(text: str) -> float                          # §3.3 (Spectre mode)
def resolve_source(params: Mapping[str, Expr], type: str, tran_stops: Sequence[float],
                   notes: List[Note]) -> Source          # §3.8.1; plan.py reuses it for alters (§5.3)

# vamos/netlist/spice.py  (additions, §4.3; Fragment and Resolver are phase 0)
class Resolver(Protocol):
    def subckt(self, name: str) -> Optional[Subckt]: ...
    def model(self, name: str) -> Optional[Model]: ...
    def param(self, name: str) -> Optional[Expr]: ...
    def va_module(self, name: str) -> Optional[str]: ...
@dataclass
class Fragment:
    items: List[Item] = field(default_factory=list)
    controls: List[Tuple[str, List[str], str]] = field(default_factory=list)   # (keyword, fields, origin)
    notes: List[Note] = field(default_factory=list)
    left_out: Set[str] = field(default_factory=set)
    pending: List[Tuple[str, str, str]] = field(default_factory=list)          # (kind, name, origin)
def parse_fragment(lines: List[Tuple[str, str]], cwd: str, opts: ParseOpts, resolver: Resolver) -> Fragment

# vamos/netlist/plan.py  (dataclasses frozen in phase 0; IR names only, §5.1)
Target = Tuple[str, str, str]   # ("instance", "x1.r1", "r") | ("model", "nch", "vth0") | ("option", "temp", "")
                                # | ("variable", "rval", "")
@dataclass
class AnalysisStep:
    id: str                       # vamos_a<k>
    name: str                     # Spectre name (child name inside sweeps)
    kind: str                     # op dc ac noise xf tran
    context: List[Tuple[str, SweepSpec]] = field(default_factory=list)   # enclosing Spectre sweeps, outermost first
    own: Optional[SweepSpec] = None
    continuation: Dict[str, int] = field(default_factory=dict)          # sweep name → VACASK continuation (§5.2)
    args: Dict[str, object] = field(default_factory=dict)               # normalized settings, errpreset applied (§5.4)
    saves: Optional[List[SaveSpec]] = None
    full_solution: bool = False   # write=/writefinal=: save every node (§5.4)
    noise_input: Optional[str] = None   # IR name of the input source; vamos_nin<k> when the netlist gives none (§5.6)
    stores: Optional[str] = None  # prevoppoint/useprevic stored-solution names
    uses: Optional[str] = None
@dataclass
class Action:
    op: str                       # options | var | alter | source | analysis | note
    args: Dict[str, object] = field(default_factory=dict)
    step: Optional[AnalysisStep] = None
    origin: str = ""
@dataclass
class RunPlan:
    actions: List[Action] = field(default_factory=list)
    variables: Dict[str, float] = field(default_factory=dict)
    overridden: Dict[str, Set[str]] = field(default_factory=dict)  # subckt → parameters sub= targets make overridable
    options: Dict[str, object] = field(default_factory=dict)
    notes: List[Note] = field(default_factory=list)
def build(nl: Netlist, engine: str, settings: Settings) -> RunPlan

# vamos/netlist/signals.py  (dataclasses frozen in phase 0)
Ref = Tuple[str, ...]   # ('v', node path) | ('i', element path) | ('noise', instance path) | ('onoise',)
                        # | ('inoise',) | ('gain',) | ('tf', source path); IR names, '.' hierarchy
@dataclass
class SignalMap:
    per_step: Dict[str, List[Tuple[Ref, str, str]]] = field(default_factory=dict)   # step id → (Ref, Spectre name, psf type)
    allpub: Set[str] = field(default_factory=set)       # step ids printed as save default / V(*) (§6.4)
def resolve(nl: Netlist, plan: RunPlan) -> Tuple[Netlist, SignalMap, List[Note]]   # an nl copy with the probes

# vamos/netlist/vacask.py, xyce.py  (additions; with plan/step None the output is byte-identical to today)
def render(nl, analysis_name="vamos_tran", osdi=(), notes=None, op=False,           # vacask.py
           plan: Optional[RunPlan] = None, sigmap: Optional[SignalMap] = None,
           names: Optional[Dict[str, Dict[Ref, str]]] = None) -> str
def render(nl, osdi=(), notes=None, op=False,                                      # xyce.py: one step
           step: Optional[AnalysisStep] = None, sigmap: Optional[SignalMap] = None,
           names: Optional[Dict[str, Dict[Ref, str]]] = None) -> str
def names_for(nl: Netlist, plan: RunPlan, sigmap: SignalMap) -> Dict[str, Dict[Ref, str]]   # both; no rendering

# vamos/spectre/results.py  (dataclasses frozen in phase 0)
@dataclass
class EngineResult:
    rc: int = 0
    status: Dict[str, str] = field(default_factory=dict)        # step id → ok | failed | interrupted
    files: Dict[str, str] = field(default_factory=dict)         # step id → engine output file
    failed_points: Dict[str, int] = field(default_factory=dict) # step id → first failed sweep point (1-based)
    names: Dict[str, Dict[Ref, str]] = field(default_factory=dict)   # from render (§4.5 item 6)
    log: List[str] = field(default_factory=list)
@dataclass
class Signal:
    name: str
    ptype: str
    units: str
    values: list = field(default_factory=list)
    members: Optional[List[Tuple[str, list]]] = None
@dataclass
class AnalysisResult:
    key: str
    atype: str
    file: str
    parent: str = ""
    tree: str = ""                                               # "", "leafNode" or "sweepNode"
    sweep: Optional[Tuple[str, str, int, List[float]]] = None    # (name, units, grid, values)
    signals: List[Signal] = field(default_factory=list)
    header: Dict[str, object] = field(default_factory=dict)
    description: str = ""
    swept: Dict[str, float] = field(default_factory=dict)
    data_type: str = ""
    status: str = "ok"                                           # ok | failed | interrupted
def collect(plan: RunPlan, sigmap: SignalMap, er: EngineResult, naming: str) -> List[AnalysisResult]

# vamos/spectre/run_vacask.py, run_xyce.py
def run(nl: Netlist, plan: RunPlan, sigmap: SignalMap, run_dir: str, job: SpectreJob, log: SpectreLog) -> EngineResult

# vamos/output/psf.py
SIMULATOR = "spectre"; LOG_GENERATOR = "drlLog rev. 1.0"; SIM_MODE = "Spectre"; SIGNAL_NAME_TYPE = "spectre"   # §8.2
@dataclass
class PsfHead:                                          # phase 0
    version: str = ""
    date: str = ""
    design: str = ""
    simulator: str = SIMULATOR
    psfversion: str = "1.4.0"
    precision: str = "%.15e"
def write_analysis(dirpath: str, res: AnalysisResult, head: PsfHead) -> None
def write_logfile(dirpath: str, results: List[AnalysisResult], head: PsfHead) -> None
# vamos/output/nutmeg.py
def write(path: str, results: List[AnalysisResult], binary: bool) -> None
# vamos/output/statefile.py
def write(path: str, values: Mapping[str, float], comment: str) -> None
def read(path: str) -> Dict[str, float]
# vamos/netlist/rawfile.py (addition)
def read_prn(path: str) -> List[Raw]                    # Xyce standard-format .PRINT tables (noise), split per .STEP

# vamos/spectre/log.py
class SpectreLog:   # screen/file routing, classes, maxwarns/maxnotes, counts, the trailer (built-in fallback)
    def message(self, note: Note, phase: str) -> None
    def analysis_banner(self, res_or_step) -> None
    def status_line(self) -> None                       # SIGUSR1 (§2.7)
    def finish(self, status: int) -> None
# vamos/spectre/flow.py
def run(job: SpectreJob, opts: dict) -> int             # the §1 pipeline and the §2.7 signal handling; the exit status
```

Also:
- `cli.py`: `PERSONALITIES["spectre"]`; every link name that starts with `spectre` dispatches to it, with
  `opts['argv0']` (§2.1).
- `ir.py`: the §4.1 additions, among them `Cond`, `ParamTest`, `flat_items`, `StatBlock`, `Vary`, `Correlate`.
- `tables.SPECTRE_MASTERS`: data, phase 0 (§4.4).
- `tools.SHIM_NAMES` gains `spectre`, so PATH scrubbing and lock-out cover it.
- `shims/spectre`.
- `banners/spectre.json` with the keys `version`, `subversion`, `run_start`, `inventory`, `analysis_banner`,
  `analysis_done`, `audit`, `error_block`, `warning_block`, `notice_block`, `trailer_ok`, `trailer_fatal`. No
  key holds licence or copyright text, and no key holds a PSF format field (§8.2).
- `licenses.json` gains `cpp`.

## 11. Tests

**Unit** (Cygwin Python 3.9 and WSL Python 3.14 unless marked; fixtures under `tests/vamos/fixtures/spectre/`;
helpers from `tests/vamos/vamos_testlib.py`):
- **CLI:**
  - every §2.3 row through `build_job` (the `=` family, abbreviations, `+mt` against `+mt=4` against
    `+mtmode`, `-D` against `-debug`);
  - `+%E opamp1` and `-%E`;
  - precedence of `spectre_DEFAULTS` over `SPECTRE_DEFAULTS` over nothing, and of argv over both;
  - `+config`/`=config`/`-config`/`+pre_config`/`-pre_config` dropping rules; `+aps=` against a tran's
    `errpreset` against `+errpreset=`; `-mts`;
  - a link name `spectre231` and `vamos -spectre` (`%S`, `<prog>_DEFAULTS`, the trailer's `<prog>`);
  - `-f psfascii` is not an option file;
  - unknown options, and `--vamos-strict` → exit status 2;
  - the ADE command line of §9 gives no unknowns.
- **Percent codes:** every row of the UG pp.266-268 table, `:h` without a slash giving `.`, `::`, no
  recursion, `%C` = `stdin`; codes inside netlist strings (`readns="%C:r.dc"`) and `include` names.
- **Lexer and numbers:**
  - `//` after a blank and not after a word; `*` lines; `\` and `+` continuation, also inside braces;
  - escaped names; `007` ≠ `7`; several node groups (`(1 2)(3 4)`);
  - the full scale-factor table in both modes; `.01`, `.148p`, `-.5u`, `1E-14`; `1.234E-3p` (note);
    `50Ohms` (error); `50_Ohms`; `1meg` (warning, 1e-3); `1x` in SPICE mode (warning, 1).
- **Parser:**
  - the title rule; language by extension, by first line (a non-`.scs` included file, and a top-level one,
    starting with `simulator lang=spectre`) and by `simulator lang`, with the mode restored after an include;
  - `insensitive=yes`: an exact match first, then a marked lower-case one; a Spectre-mode `M1 … NCH` using a
    SPICE-mode `.model NCH nmos`; two marked definitions differing in case (error); another `simulator`
    parameter (error);
  - include path order, `~`/`${VAR}`/%-codes; `section=`; `ahdl_include`; parameters with shadowing; user
    functions;
  - `inline subckt`; model groups; `if`/`else if`/`else` with the one-statement form;
  - `global gnd vdd` and node `0` (warning); `ic`/`nodeset`; `save` variants; `options` merge, `options` in a
    subckt (error; global with a note under `-mts`) and in a sweep block; `prot`/`unprot` in any case;
  - reserved words: a keyword as an instance name (error) and as a node or parameter name (accepted); `M_PI`
    as a node name (error);
  - every statement of §3.6, including the error rows; a `statistics` block from a PDK-style file;
  - SPICE sections with `.op`/`.ac`/`.tran` naming and SPICE3 `.tran` fields; a SPICE section that uses a
    Spectre subckt defined later (resolver, `pending`).
- **Sources and models:** `resolve_source` for every type; `Source.spectre` keeping the fields of a `type=dc`
  source; the edge policy on literal, non-constant and altered edges; R/C/L card folding; the diode `eg`
  default and its temp ≠ tnom warning; diode `level` ≠ 1 (error); MOS default geometry from the card and from
  the master, and its use in bin selection; the resistor `scale` and `r` = 0 rules.
- **Expression dialect:** `-2**2` = 4; `pow(2,1.5)` = 2.828…; `0**0` = 1; `(-8)**(1/3)` and `0**-1` raise
  EvalError; `^` is an error; bitwise folding; `log(-1)` raises EvalError in the Spectre evaluator; the
  constants.
- **Plan:**
  - each §5.2 sweep form and target, with IR names only in the actions; `mod=` renames per `Model.prim`;
  - the dec grid: 4 Hz to 4.19 MHz at dec=20 gives 121 points ending exactly at 4.19e6; integer spans keep
    `mode="dec"`/`DEC`;
  - errpreset rows (liberal: trapgear2 → `"trap"` with a note), reltol scaling (options reltol 1e-3: liberal
    1e-2, conservative 1e-4), maxstep = min(user, bound), `maxstepratio` (error);
  - sweep blocks expanded (outer first) with `continuation=0`, and `restart=no` keeping 1; the order-dependent
    child constructs rejected;
  - the condition and bin rule: a sweep inside one branch or bin runs, a value that crosses is an error;
  - `ParamTest` per instance path, at nominal and swept values, with every severity;
  - alter lowering per engine: complete source alters, never `type` alone; source-state switching around each
    tran; variables never folded; `sub=` targets in `overridden`;
  - the noise input of §5.6; `write`/`writefinal` full saves; prevoppoint `store`/`opsolve` pairing.
- **Signals:** allpub/selected/lvlpub/nestlvl; wildcards with `depth`/`exclude`/`devtype`/`subckt`; the
  node-over-instance rule; terminal names and indices (`D1:a` = `D1:1`, `I1:sink`); probe insertion (a copy,
  never the input IR); `ic`/`nodeset`/`readns` names resolved, unmatched ones warned; the SignalMap, including
  inline collapse and `+escchars`.
- **Outputs:**
  - PSF goldens for op, dc sweep, ac, noise, tran, sweep parent, nested parent and leaves, and `logFile`
    (after status 0, 1, 2 and 3);
  - string escaping; noise STRUCT types named per model (`rref`, `resistor`, a Verilog-A module);
  - the psf-parser oracle: from `VAMOS_PSF_PARSER` (default: the clone's `src/`) on Python ≥ 3.10, every golden
    parses and round-trips its values [E: it reads the real noise STRUCT, `logFile` and info samples].
    psf-parser needs Python ≥ 3.10, so the Cygwin 3.9 leg skips it, and a missing oracle is a reported skip;
    the WSL run requires it;
  - nutmeg written and read back by `rawfile.read_all`; state files round-trip; the trailer singular and
    plural forms [I], and the built-in trailer under the `none` banner profile.
- **Emitter extensions:** golden decks for `Cond` (the UG p.110 `npn_mod` netlist renders its three cards on
  both engines), Spectre bins, variables and overridable `sub=` targets, plan control blocks, source prints
  (a given-dc waveform as `type="dc"` on VACASK; a derived dc as an expression on Xyce), `'007'` quoting,
  `r` = 0, the `G` element for `isnoisy=no`, the noise input `AC 1 0`, `.IC` only in tran decks, the Xyce
  `.STEP` order and an op under two `.STEP`s, `read_prn` with `.STEP`; an options-free netlist prints
  temp=27 tnom=27 in both decks. Every existing `test_netlist_*` golden is unchanged.
- **CLI and cpp** (WSL only: Cygwin has no cpp): `-E -D -U -I` with and without `-disableCPP`; origins,
  title and language per file from the markers; a `#include`d SPICE file; a `//` title kept; `#` lines
  without cpp (error).
- **Fragments:** `+config`, `+pre_config` and `+paramdefault`, with origins `+config:<file>:<line>`.
- **Log:** `+log`/`=log`/`-log` routing, the message classes, and `-maxwarns`/`-maxnotes` with their to-log
  variants.

**Engine end-to-end** (WSL; both engines unless marked; values asserted in the PSF, `logFile` contents and
exit status; cross-engine agreement within ~1 %, analytic checks tighter):
1. The §7.3 RC example: `out(5 ms)` = 0.98173 and `out(1 Hz)` = 0.99996 − j0.00628; sweep parent and leaves.
2. dc sweeps of a source, `temp`, a top-level parameter, a model parameter (one renamed by `SPECTRE_MASTERS`)
   and a subckt-instance parameter, primary and dependent (`X2.R1`); the swept value restored for the next
   analysis.
3. A sweep block with dc and ac children and a nested sweep; repeated sweep values (`values=[1k 1k 2k]`);
   the nested leaves in the right order on Xyce; an op child under two sweeps.
4. Noise of a 1 kΩ/1 kΩ divider: `out` = 2.879e-9 V/sqrt(Hz) at 27 °C; a `R1` STRUCT with `rn` and `total`;
   `in`/`gain` with `iprobe=V1` at V1's default `mag=0`; the same `out` without `iprobe` (§5.6).
5. `xf` (VACASK): the transfer function 0.5, named `V1:p`; on Xyce the run is rejected (exit status 2).
6. `alter dev=Vdif param=type value=pulse` between an op and a tran, then back to dc (the UG op-amp pattern:
   `type=dc dc=0 val0=0 val1=2 width=1u delay=10ns`): the source's node is 2 V at 0.5 µs and 0 V at 1.5 µs
   on both engines, and the op before and after it sees dc = 0.
7. A SPICE-mode file (`.op .ac .tran`): `opBegin.dc`, `frequencySweep.ac`, `timeSweep.tran.tran`; a
   `simulator lang=spice` model section inside a `.scs` file.
8. `include … section=` with a library file; `ahdl_include` of a Verilog-A resistor (VACASK; on Xyce only
   unparameterized).
9. A model group (BSIM4 bins split in L and W): every device on its group entry; an edge geometry exactly on
   `lmin`; a MOS without `l`, taking the card's and the master's default, equal on both engines.
10. An inline subckt binning by `area` with same-named branches (the UG `npn_mod` structure): the outputs name
    the devices `q1 q2 q3`; a sweep of `area` inside one branch runs, one that crosses it is an error.
11. Exit status: one failing analysis among good ones (status 1, the others written); a failing sweep point
    (the leaves before it written, status 1); a VACASK bind failure (status 1, not 0); a parse error (status
    2, the `terminated prematurely` trailer); SIGINT during a long tran (status 3: the finished analyses and
    the partial tran written, the trailer); SIGUSR1 (a status line, the run continues).
12. The §9 ADE command line end to end: `psf/` holds `logFile` and the data files, `spectre.out` ends with
    the trailer, and `spectre.ic`/`spectre.fc` hold every node.
13. `-format nutascii` and `nutbin`.
14. `SPECTRE_DEFAULTS="+log %C:r.out"` writes `x.out`; `-outdir`; a netlist on stdin.
15. A fixture modelled on the UG 5.1.41 fully differential op-amp (pp.112-115: mos3, diodes, library
    sections, infinite probe resistors, `xf`, `noise` with `oprobe=Edif oportv=1`, `alter`, two `tran`s with
    `errpreset=conservative`), run with `-E` (it starts with `#define`). The engines agree within ~1 % (`xf`
    on VACASK only).
16. prevoppoint after a tran (VACASK): the AC gain at the final transient state against a fresh operating
    point.
17. Sources: a pulse without `dc` (the op uses the t = 0 value; an alter of `val0` moves it on both engines);
    a pulse with no rise/fall (warning, transres edge); a pulse with `dc=0.9` and `val0=0`: the op, a dc
    sweep of its `dc`, the AC bias through a diode and the tran start, equal on both engines (0.9 V for the
    op and ac, `val0` at the tran start).
18. Initial conditions: each tran `ic` mode with `ic` statements and capacitor `ic=`; `skipdc=yes`; a
    nodeset; `readns`/`readic` feeding a run; an `ic` statement leaves the op and ac unchanged on both engines.
19. `set reltol=` between analyses; `-va,define` (VACASK).
20. `alter mod=` on Xyce (state replay changes the later deck), and an alter of a parameter Xyce does not know
    (status 2, naming the alter).
21. Resistors: `r` = 0 (a short on both engines), an instance `scale` (w and l only), `isnoisy=no` (no noise
    on both), a card's `r` folded into an instance.
22. Temperature: an options-free netlist runs at 27 °C on both engines (noise 4kT); a diode card without `eg`
    at 127 °C (the warning).

**Review:** an adversarial review of the whole diff before commit, as for vcs-ams.

## 12. Work breakdown

**Ordering with vcs-ams.** Agents are editing the shared netlist modules for vcs-ams right now. Spectre
phase 1 starts after the vcs-ams phase-2 merge. Agents that touch `spice.py`, `expr.py`, `tables.py`,
`vacask.py`, `xyce.py` or `rawfile.py` work on that state, and keep every `test_netlist_*` and `test_ams_*`
test green unchanged.

**Phase 0 (contracts, frozen afterwards):**
- the `ir.py` additions (§4.1), including `Source.spectre`, `Model.prim`, `Cond`, `ParamTest`, `flat_items`
  and the `StatBlock`/`Vary`/`Correlate` fields;
- `optable.scan(option_chars=)`;
- `vamos/spectre/job.py`: `SpectreJob` and `Settings`;
- the dataclasses of `netlist/plan.py` (`AnalysisStep`, `Action`, `RunPlan`), `netlist/signals.py` (`Ref`,
  `SignalMap`) and `spectre/results.py` (`EngineResult`, `Signal`, `AnalysisResult`);
- `output/psf.py`'s `PsfHead` and format constants;
- `spice.Fragment` and the `Resolver` protocol;
- the emitter signatures (the `render` keywords and `names_for`), as stubs;
- `tables.SPECTRE_MASTERS` as data: S1 builds models from it, S3 prints from it;
- the `banners/spectre.json` skeleton; `tests/vamos/test_spectre_contract.py`; the fixture directories.

**Phase 1** (parallel; each agent owns its files and tests; nobody commits):

| agent | files |
|---|---|
| S1 language | `netlist/spectre.py` (with `resolve_source`); the `expr.py` dialect and `cpow` (§4.2); `spice.parse_fragment`, the resolver and the `spectre-spice` dialect (§4.3, coordinated with the `spice.py` owner) |
| S2 plan and signals | `netlist/plan.py` (paramtests, the condition/bin rule, source states, continuation), `netlist/signals.py` (probes, the noise input) |
| S3 emitters | the `netlist/{vacask,xyce,tables}.py` extensions (§4.4, §4.5: every walk over `Cond`, `render` and `names_for`, sources, quoting, zero resistors); the `cpow` printers; `rawfile.read_prn`; goldens and engine tests of every §7 mapping row |
| S4 outputs | `output/{psf,nutmeg,statefile}.py`, `spectre/results.py` (sweep splitting, noise/xf transforms); the psf-parser oracle tests |
| S5 command line and log | `personalities/spectre.py`, `spectre/{args,percent,cpp,log}.py`, `banners/spectre.json`, `cli.py`, `tools.SHIM_NAMES`, `shims/spectre`, `licenses.json` |

**Phase 1b (S6 netlist integration, after S1–S3):** `spectre.parse` → `plan.build` → `signals.resolve` →
`render` on both engines for e2e 1–10, without `flow.py`. S6 owns the seam fixes in `netlist/`, keeping every
agent's tests green (vcs-ams needed its N4 integration agent for this, and it found eight seams).

**Phase 2 (integrator):** `spectre/{flow,run_vacask,run_xyce}.py`, with the §2.7 signal handling; the
end-to-end tests of §11; the `VAMOS_PLAN.md` status update.

**Phase 3 (after v1):**
- **montecarlo on VACASK**, sketched:
  - a process `vary` becomes a top-level `agauss`/`aunif` draw;
  - mismatch becomes a per-subckt-instance call site (`parameters q_l=agauss(q, s, 1)` inside each subckt
    that reads q; VACASK draws per call site and instance [VACASK doc]);
  - the process value is the mismatch mean [M ref19 p.175];
  - `correlate` is built from independent normals (Cholesky);
  - `lnorm` is p·exp(agauss(0,s,1));
  - `truncate` has no VACASK equivalent and stays an error;
  - the outputs: nominal files under the child names, `<m>_<c>.montecarlo` parents, `<m>-NNN_<c>` leaves
    [S];
  - a note that the draws differ from Spectre's random sequence;
- `altergroup` (trivial on Xyce through state replay; a model-parameter diff on VACASK);
- `info` (`what=parameters`, then `what=oppoint` with per-device output-variable maps);
- `xf` on Xyce; `bsource`; `pss`/`pac`/`pnoise`/`hb` on VACASK; a psfbin writer.

## 13. Verification record

Manual checks [M] are cited where they are used. Real Spectre output files [S] are cited the same way. The
experiments [E] were run in WSL in throw-away directories; E17–E48 come from the review of revision 1 (decks
under the session's `rv_engmap/`, `sfid/` and `rv_impl/` scratch directories). Each one becomes an engine or
unit test in phase 1.

| # | experiment | result |
|---|---|---|
| E1 | VACASK: top-level `r=rval` with `var rval=1k`, then `sweep variable="rval" values=[1k,3k]`, then an op | the sweep gives b = 0.5 and 0.25; the op after it gives 0.5, so the variable was restored |
| E2 | VACASK: `parameters rval=1k` plus `var rval=3k` | the `parameters` entry wins (b stays 0.5), so a variable cannot override a parameter |
| E3 | VACASK: `parameters rval=vamos_v_rval rdep=rval*2`, sweeping `vamos_v_rval` | dependents follow (c 0.5 → 0.25), and `var` restores it |
| E4 | VACASK: `sweep option="temp"`, `instance="x1" parameter="rr"` (a subckt instance), `model="dm" parameter="is"` | all work; each is restored after the sweep |
| E5 | VACASK: swept ac, tran and nested op | one plot, sweep columns first, points concatenated; equal sweep values give indistinguishable groups |
| E6 | VACASK: a failing op (two parallel sources), default policy | `Analysis 'bad1' aborted.`, exit 0, later analyses run; inside a sweep, `Sweep aborted @ 2/3 (2).` and, under `strictoutput=2`, no rawfile for that analysis |
| E7 | VACASK: `alter instance("v1") type="pulse"`, `options reltol=… tran_method="gear2"` between analyses | the source becomes a pulse with its earlier printed val0/val1/width/delay (the edge is VACASK's 1 ns default); options lines are accepted |
| E8 | VACASK: noise and acxf of a 1 kΩ/1 kΩ divider | onoise 8.288e-18 V²/Hz (= 4kT·500 Ω at 27 °C), gain 0.25 (power), `n(r1)` and `n(r1,thermal)` with `save full`; acxf `tf(v1)` 0.5, `zin(v1)` 2 kΩ |
| E9 | VACASK: `@if area < 2` inside a subckt, two instances; `ground gnd` and `global 'vdd!'`; nodes `out<3>`, `007` and `7`; instance and node `c10` | selection per instance with the same instance name in both branches; quoted names in the rawfile; `007` and `7` are distinct nodes; instance and node names coexist |
| E10 | VACASK: a pulse without `dc`, `width=1e30` | the op uses the t = 0 value; the huge width is accepted |
| E11 | VACASK: sweep and alter of `instance="x2:r1"`; `tran store="final"`, then `ac nodeset="final" opsolve=0` | hierarchical targets work; AC at the final transient state 0.019 against 0.99996 from a fresh op |
| E12 | Xyce: `.STEP` + `.AC` with `-r`; `.NOISE` with `.PRINT NOISE FORMAT=RAW` | one plot per step, step value in the plot name; `-r` writes every node, upper-cased; noise RAW is refused with a warning and a standard table `FREQ ONOISE INOISE DNO(R1) DNO(R2)` is written (ONOISE 8.2879e-18) |
| E13 | Xyce: `.DC` of a global `.param`; `.STEP DMOD:IS`; `.DC TEMP`; `.STEP R1:R` with `.AC DATA=`; `.PRINT … FORMAT=RAW FILE=` after `.DC` and after `.OP`; a singular `.OP` | all work; the print file holds exactly the printed columns (`V(OUT)`, `V(X1:MID)`, `I(V1)`; after `.OP` one point, plot "DC transfer characteristic", plus a `sweep` column); I(V1) has VACASK's flow(br) sign; the singular op exits 1 |
| E14 | psf-parser (MIT) on the real Spectre noise file, a `logFile` and an info file | all parse, so it can be the test oracle |
| E15 | `openvaf-r --help` | `-D MACRO[=VALUE]` exists |
| E16 | VACASK: `options reltol=…` before a `sweep`, and between a `sweep` and its analysis | before: accepted, and the sweep still binds; between: a parse error, "expecting sweep or analysis or newline" |
| E17 | VACASK and Xyce: a pulse with `dc=0.9 val0=0`, a sine with `dc=0.5`, a pwl with `dc=0.7`, an isource pulse with `dc=1m` into 1 kΩ: op, a sweep of `dc`, AC through a diode, tran; then the pulse printed `type="dc"` with its waveform fields and `alter … type="pulse"`/`"dc"` around the tran | VACASK ignores `dc` unless `type="dc"`: op 0.0 / 0.0 / 0.2 / 0 V, the `dc` sweep a no-op, AC 0.99999 where Xyce gives 0.08511. Xyce: op 0.9 / 0.5 / 0.7, `.DC` 0.1 / 0.2, `.TRAN` starting at the t = 0 waveform value. The `type="dc"` form: op 0.9, sweep 0.6 / 0.8, AC 0.08512; the tran starts at 0 and reaches 1.8; AC after it 0.08512 again |
| E18 | Xyce: an RC divider with `.IC V(out)=0.2` under `.OP`, `.DC`, `.AC` through a diode and `.NOISE`; `.IC` with `.NODESET` | `.IC` pins every DC solve: `.OP` 0.2 (correct 0.5), `.DC` 0.2 / 0.2 / 0.2 (correct 0 / 0.5 / 1.0), AC 0.49999 (0.47804 without), ONOISE 1.66e-17 (5.76e-19); with `.NODESET`: "Cannot set both .IC and .NODESET simultaneously." |
| E19 | noise of the divider: VACASK without `in=`; a 0 A isource across the output as the input on both engines; Xyce input source `AC` 0, 1 and 2; VACASK `mag=2` | VACASK without `in=`: "Instance '' not found.", "Analysis 'nz2' aborted." (a failed analysis: vamos exit status 1). With the 0 A input: onoise 8.288e-18 / 8.2879e-18, as with V1. Xyce INOISE 3.3152e-17 (AC 1), 8.2879e-18 (AC 2), 8.2879e+02 (AC 0), ONOISE unchanged; VACASK gain 0.25 with `mag=2` |
| E20 | Xyce: two and three nested `.STEP`s with `.OP`; `.DC vamos_dummy LIST 0` under two `.STEP`s; `.DC R1:R LIST`, `.DC DMOD:IS LIST`, `.STEP pa` + `.DC R1:R`, `.STEP X1:R1:R`, a swept `.param` read by an X-line override; one `.STEP` with `.OP` | the first `.STEP` is the innermost loop (plots (1,10), (2,10), (1,20), …); `.OP` under two `.STEP`s: "Netlist error: Analysis mode 4 is not available", then a segfault (rc 139); all the others work (`.DC R1:R LIST 1k 3k` gives 0.4988 / 0.2500) |
| E21 | 2 Hz to 500 Hz at dec=10: VACASK `mode="dec"`, Xyce `.AC DEC` and `.DC DEC`, Xyce `.NOISE … DATA=` | VACASK: 25 points ending exactly at 500 (ratio 250^(1/24)); Xyce: 24 points ending at 399.05; `DATA=` works. Real Spectre [S: 15.1 `noiva`, 4 Hz to 4.19 MHz, dec=20]: 121 points, 4·10^(k/20) for k ≤ 119, then exactly 4.19e6 |
| E22 | `r=0` on VACASK `resistor` and `sp_resistor`; Xyce `R0 in a 0` | `resistor`: "NaN found in vector, row node 'a'", op, ac and tran aborted; `sp_resistor`: "Value is too small, set to 1.000000e-12", source current −0.99920 mA instead of −1 mA; Xyce: V(A) = 1.0 |
| E23 | `r=1k scale=0.5` on VACASK `sp_resistor` and on Xyce | VACASK 500 Ω, silently; Xyce "Unrecognized parameter SCALE" |
| E24 | VACASK: subckt parameter `l` with default `2*w`: a sweep of `l`, `alter instance("x1") l=5000`, a sweep of `w` | sweep: "Sweep 's2': parameter 'l' of instance 'x1' not found.", analysis failed; alter: "Parameter 'l' not found.", rc 1; sweeping `w` works and `l` follows |
| E25 | VACASK: `fall=tf` with `var tf=0`; `alter instance("v1") rise=0`; Xyce zero edges | a(300 ns) = 1.0 for a 100 ns pulse (no fall); "Rise time of pulse transient must be grater than 0.", "Circuit setup failed.", rc 1; Xyce makes ideal steps (`PulseData::updateSource`) |
| E26 | Xyce `.model rm R R=2` with `R1 in out rm` and `R3 … rm 1k`; VACASK `sp_resistor` card `r=2000` | Xyce gives 2 kΩ for both: the model R multiplies Xyce's 1 kΩ default and the 1k value; VACASK's card `r` is the instance default (2 kΩ) |
| E27 | VACASK: a sweep whose second point fails, `strictoutput=0` | `op1.raw` holds the complete first group under a blank `No. Points:`, which `rawfile.read` accepts; `tr1.raw` holds the 71 rows of point 1; no later point runs; the target is restored |
| E28 | VACASK under `abort except analysis`: `save v(nosuch)`; a sweep of a dependent subckt default; `p(m1, id)` of a missing output variable | "Node 'nosuch' not found. Failed to bind analysis outputs." / "Failed to bind sweep parameters." / "Output variable … not found. Failed to bind analysis outputs.": no rawfile, no "aborted" line, exit 0 |
| E29 | VACASK: a Schmitt trigger at vin = 0 under a sweep, `continuation=1` and `0` | out = 1.831 V and 0 V |
| E30 | Xyce `.OPTIONS TIMEINT METHOD=` | `TRAP` and `GEAR` accepted, and `GEAR` with `MAXORD=1` or `3`; `BE`, `EULER`, `BDF`: "Unsupported time integration method"; `1`: "Invalid integration method 1 specified", abort |
| E31 | `ic`/`nodeset` naming a node that does not exist | VACASK accepts `ic={"nosuch",1}` and `nodeset={"nosuch",1}` silently; Xyce warns "Initial conditions specified at nodes not present in circuit … Ignoring nodes: NOSUCH" |
| E32 | Xyce `R1 … NOISE=0`; `GR1 in out in out 1m` in a divider | "Unrecognized parameter NOISE"; the G element gives DC 0.5 V, and ONOISE holds only R2's 4.144e-18 V²/Hz |
| E33 | Xyce `.NOISE` under one and two `.STEP`s | the standard table's Index restarts at 0 every step, with no step label or separator |
| E34 | VACASK `sp_diode` at 1 mA with `eg` 1.11 and 1.124481, at 127 °C and −40 °C | 4.8 mV and 3.2 mV apart |
| E35 | VACASK nodes `007`, `7`, `00`; Xyce the same | bare `007` is a parse error; quoted `'007'` (2.0 V) and `7` (1.0 V) are distinct, and `'00'` (−1.5 V) is not ground; Xyce keeps them distinct unquoted |
| E36 | inductor and iprobe currents, 1 mA into terminal 1 / `in` | VACASK `l1:flow(br)`, `ip:flow(br)`; Xyce `I(L1)`, `I(VIP)`; all +1 mA |
| E37 | VACASK: a dc source with `val0=0 val1=2 width=1u delay=10n`; `alter instance(…) type="pulse"` alone; then with the fields printed but no `fall` | alone: VACASK's defaults, a 0→1 V step at about 1 ns that stays high for the whole 3 µs; with the fields: 2 V, still 2 V at 1.5 µs (`fall=0` is "no fall"); both exit 0 |
| E38 | VACASK: pulse sources with and without `dc`, an alter and a sweep of `val0` | the op follows `val0` in both (0.7, then 0.2 / 0.4): a derived dc follows the fields, and an explicit `dc` is ignored (E17) |
| E39 | VACASK: `@if area < 2` in a subckt, a sweep of `area` over [1, 1.5] and over [1, 3] | inside the branch: 0.5 / 0.4; across it VACASK re-evaluates the condition: 0.5 / 0.0909 (the 10 kΩ branch) |
| E40 | VACASK: SIGINT and SIGTERM during a long tran | the interrupted analysis's rawfile is left with a blank `No. Points:`; `rawfile.fix_points` plus `read` recover 467,960 points up to t = 46.8 ms |
| E41 | VACASK: a binned MOS (w < 10 µm: vto 0.5; w ≥ 10 µm: vto 1.0), `sweep instance="m1" parameter="w" values=[5u, 20u]` | 2.25 mA at 20 µm, bin 1's current (bin 2's card gives 1.00 mA); exit 0 |
| E42 | VACASK `sp_mos1`: `m1 (d g 0 0) nch w=30e-6` without `l`, and with `l=3e-6` | 25.35 µA (VACASK's default l = 1e-4) against 845 µA; exit 0 |
| E43 | Xyce: a diode card with an unknown parameter, plain and under `.STEP DMOD:BOGUSPARAM` | plain: "No model parameter BOGUSPARAM found for model DMOD" as a warning, exit 0; under `.STEP`: an error, exit 1 |
| E44 | `tables.reachable` and today's renders on the IR of UG p.110's `npn_mod` (cards used only inside `Cond` branches) | neither card is reachable; the subckt renders empty |
| E45 | `expr` printing and evaluation of `x**0.5`, `x**0`, `0**0`, `(−2)**0.5`; pow(0,0) on the engines | `(x>0.0 ? pow(x, 0.5) : (x<0.0 ? 1.0 : 0.0))` and an `x==0` guard; 0\*\*0 = 0 and (−2)\*\*0.5 = 1 in the evaluator; Xyce pow(0,0) = 1e50, VACASK refuses it |
| E46 | `spice.py` on `.tran 1n 1u 0 10n` and `.tran 1n 1u` | "tstop values must increase"; HSPICE's RMAX maxstep 5e-9 and temp = tnom = 25; source defaults from synth_step/synth_stop (1e-11 s / 3600 s) |
| E47 | `cpp -nostdinc -undef` on a netlist whose line 1 is a `//` comment | the title line comes out empty |
| E48 | revision 1's `SpectreJob` on Cygwin Python 3.9.16 | "TypeError: non-default argument 'percent' follows default argument" |

## 14. Open questions
1. The UG's `/tmp%C:t:r.raw` example against its `:t` definition (§2.4). vamos follows the definition.
2. `-outdir` with a netlist path containing `/` (§2.5): the literal reading is used.
3. Where `write=`/`writefinal=` state files go (the cwd, `-outdir` or the raw directory).
4. The exact cpp flags that reproduce Spectre's preprocessing (§2.6).
5. A `.scs` file whose line 1 is `simulator lang=spice`: vamos selects SPICE mode with a note, the mirror of
   §3.1's `lang=spectre` rule.
6. Spectre-mode `1meg`/`1mil` (read as 1e-3, warned); whether newer versions add `P`/`E` suffixes.
7. Whether node `0` is also ground when `global gnd …` names another ground node.
8. Model-group selection with `nf` > 1: total or per-finger W.
9. SPICE-mode names for a second `.op`/`.dc`/`.tran`; the modern name of `timeSweep` (`.tran` or
   `.tran.tran`); whether Spectre's SPICE Reader maps `.tran`'s `tstart` to `outputstart` (§3.13).
10. Whether every SPICE-mode model and subckt is case-insensitive, as converted ones are under
    `insensitive=yes` (§3.1).
11. `dec` placement: floor or round of the interval count (one real sample: 120.4 → 120); `center`/`span` with
    log sweeps; `log=N` endpoint inclusion (§5.2).
12. `+escchars` semantics (inferred from a 19.1 ADE-produced file).
13. A sweep that changes a condition or a bin: UG p.111 says Spectre stops with an error, UG p.115 that
    instances keep the model chosen at input. vamos refuses either change (§3.7).
14. Spectre's noise contribution names for diodes, BJTs and MOSFETs (the resistor's `rn`/`fn` and Verilog-A
    names are known [S]).
15. xf trace names for an isource (a vsource's is `<src>:p` [M UG p.113]).
16. Whether ADE parses `spectre -V`/`-W` output, and in which format.
17. Whether ADE accepts psfascii results when it asked for psfxl (it reads `logFile`'s `dataFile` and
    `format`).
18. Which `spectre.out` lines ADE parses beyond the trailer; the modern message format (`ERROR (SFE-…)`)
    against the 5.1.41 one used here.
19. Spectre's `exp` waveform: are `td1`/`td2` measured after `delay`; the default `tau1`/`tau2`.
20. Spectre's default pulse rise and fall (vamos uses transres, with a warning).
21. The allpub names of inductor and iprobe currents.
22. errpreset's LTE check (liberal: capacitor and inductor states; moderate and conservative: node voltages
    [M ref19 p.431]) against VACASK's `tran_lteimplicit=0`, used for every preset (§5.4).
23. The nutmeg layout Spectre writes (one file or several; the plot and variable names).
24. Tran `start` ≠ 0 semantics (refused in v1).
25. Whether a tran's own `errpreset` beats `+errpreset=` (assumed; `+aps=` beats both [M ref19 p.35]).
26. Whether Xyce's print file is readable after an interrupt (§2.7).
27. Spectre's temperature law for the default diode `eg` (§3.9); with it, the warning could go.

## 15. Review changes

Revision 2 folds in every confirmed finding of the three reviews of revision 1 (fidelity 18, engine mapping
23, implementability 23; overlaps merged).
- **Seam (blocker).** Plan and signals speak IR names only; `render` translates them, prints the SignalMap
  and fills `names`, the only source of engine columns (§4.5 items 5-6, §6.4, §10).
- **Source `dc` (blocker).** VACASK ignores `dc` on waveform sources: a given dc prints `type="dc"` and is
  switched around each tran, a derived dc stays derived (§3.8.1, §4.5 item 8, §5.3). Alters of `type` and
  waveform fields re-resolve the whole source from `Source.spectre`, never `type` alone.
- **Xyce `.IC` (blocker).** Only in tran decks; nodesets elsewhere (§5.4, §7.2).
- **Fidelity.** errpreset table, reltol scaling and `+aps=` precedence (§5.4); language by first line and per
  file under cpp, `insensitive=yes` (§2.6, §3.1); number grammar, node groups, `prot`, reserved-word scope
  (§3.2-§3.3); ref5 terminal names (§3.8); signal handling and partial output (§2.7); MTS options (§3.6);
  paramtest per instance path (§3.6); dec placement from a real file (§5.2); `+config`/`+preset` rules
  (§2.3); options in sweep blocks (§3.12); conditions refused only on an actual branch change (§3.7); PSF
  string escaping, noise STRUCT names, xf names `V1:p` (§8.3-§8.4).
- **Engine mapping.** Noise input probe and Xyce `AC 1` (§5.6, §8.4); Xyce `.STEP` order, no `.OP` under two
  `.STEP`s, `.DC` device sweeps, TIMEINT methods, `read_prn` steps, unknown-parameter scan of every deck
  (§5.4, §7.2); `r` = 0, resistor `scale`, `G` for `isnoisy=no`, card R/C/L folding (§3.8-§3.9, §4.5);
  overridable `sub=` targets, zero edges, `'007'` quoting, `continuation=0` (§4.5, §5.2, §3.8.1); failure
  detection by rawfile (§7.1); diode `eg`/`level`, 27 °C, full saves for state files, ic/nodeset name checks
  (§3.9-§3.10, §5.4).
- **Implementability.** `Cond` in every walk (`flat_items`), `ParamTest` item; MOS default geometry; bin
  sweeps (§3.7); `parse_fragment` resolver and SPICE3 `.tran` (§3.13, §4.3); infinite-resistor and
  controlled-source probes for the UG fixture; phase-0 types (`Settings`, `PsfHead`, `EngineResult` in
  results.py, statistics fields, `Fragment`, `Model.prim`); a 3.9-valid `SpectreJob`; `cpow`; run-directory
  placement; banner-independent format fields and trailer; `argv0`; option-key case; the tests; phase 1b.
- **Resolved differently from a reviewer's text.** Source dc combines the engine-mapping print rule with the
  fidelity rule for derived dc. The dec grid follows Spectre's own placement [S], not an evenly spaced grid.
  Model-group bins are refused only when a value changes the bin, the same rule as conditions, not for every
  geometry sweep. `Source.spectre` (all written parameters) replaces the proposed `Source.inactive`. The
  noise input is named by the plan and inserted by `signals.py`. The diode `eg` default is a warning (an
  approximation, §0) only when temp ≠ tnom, not a blanket note. Xyce keeps its `-norun` check on the first
  deck as well as the per-deck scan.
- **Questions.** Closed: revision 1's q.10 (Xyce noiseless R), q.13 (partial rawfile) and q.22 (Xyce
  noise/`.STEP`/DEC/METHOD). Narrowed: q.4 (a `#` line without cpp is now [M]), q.5, q.11, q.15 and q.21.
  Extended: q.9 (SPICE `.tran` fields). New: q.10, q.13, q.22, q.25-q.27.
