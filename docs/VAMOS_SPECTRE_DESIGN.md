# vamos `spectre`: design

Status: design, revision 3, 2026-10-04, with the fixes of its own adversarial review (three lenses, §16's
last part) applied the same day. This is the implementation contract for the `spectre` personality.
It reuses the analog netlist package built for `vcs-ams` (`VAMOS_AMS_DESIGN.md` §4: `vamos/netlist/`) and
follows that document's conventions: the one rule, one disposition per construct, frozen phase-0 contracts,
and agents that own files. No Spectre binary or licence is available here, so fidelity rests on three kinds
of evidence. Load-bearing statements carry a tag. Revision 2 (2026-10-01) folded in three reviews of
revision 1 (§15). Revision 3 re-validates revision 2 against the netlist package as committed at sv2ghdl
5e0f967 (vcs-ams phases 2-5 and round 6) and today's engine builds, makes every §4/§10 contract match that
code, and builds on the assets that already exist instead of new ones: cadence2xyce.pl's dialect rules and
Verilog-A module scan, NetlistParse.rs and the Cadnip VACASK as oracles, the XDM and CMC decks, and real
PSF samples and readers. §16 lists the changes by area.

| tag | evidence |
|---|---|
| **[M]** | manual text: `ref19` = Spectre Circuit Simulator Reference PV19.1 (Jan 2020), `UG` = Spectre User Guide PV5.1.41 (Jul 2004), `ref5` = Spectre Reference PV5.0 (Sep 2003, component chapters). Page numbers are the manuals' own. |
| **[S]** | real Spectre output files and real ADE netlists. Native psfascii: psf-parser (MIT) `tests/data` (Spectre 23.1.0.242.isr1); psf_utils `samples/` `joop-banaan.{dc,tran}` (19.1), `pnoise.raw` (15.1), `fracpole.dc` (20.1). Cadence-converted (psf GROUP layout): `fracpole.ac`, `dan-zilla`, `rushikesh`. Netlists: Xyce_Regression `Netlists/XDM/SPECTRE` (ADE, ic-5.1.41usr5). psf_utils' `escaped-strings.dc` and `bus_chevrons.tran` are hand-edited fixtures, not [S] [E86]. |
| **[E]** | an experiment on the installed engines, or a check of the vamos code and tools. Revision 3's experiments (§13, E49-E93), those of its review (E94-E101) and its re-runs of E1-E48 used: VACASK 0.3.4-91-g64489cf7 (`/opt/build.VACASK/Release`) with `diode.osdi` and `mos3.osdi` rebuilt from VACASK 3becb73d on 2026-10-03 (sha1 da0cc2e90ce8, adff42fbc900 [E92]); openvaf-r 20260616-3-g0e83f1ed as `engines.openvaf()` resolves it (never `/opt/openvaf-r/openvaf-r`, OpenVAF 23.5.0, whose OSDI files VACASK refuses [E68]); Xyce DEVELOPMENT-202609292309 (`/usr/local/src/xyce-build/src/Xyce`) and DEVELOPMENT-202609292231 (`/usr/local/bin/Xyce`). Both launchers carry PyMS (`.hdl`), and under `engines.env_for`, which vamos always uses, both load the same `libxyce.so`, from xyce 7cd78110 of 2026-10-03; run without it, `/usr/local/bin/Xyce` loads its own 2026-09-29 `/usr/local/lib/libxyce.so`, which vamos never runs [E68, E96]. §13 names the binary and the library where a row depends on them. E1-E48 first ran on the 2026-10-01 builds. |
| **[I]** | inferred, not verified. Each one names its entry in §14 (open questions) as `[I, §14 q.N]` (a document check in §11 T0 enforces it) and, where it matters, has a fallback that is loud rather than silent. |

**Sources.** Every reused asset this document cites, with its public origin and the commit or version the text
refers to; every later mention follows this list:
- NetlistParse.rs: https://github.com/NyanCAD/NetlistParse.rs, commit d565fd3 (MIT; copyright JuliaHub, Inc.
  and contributors);
- psf-parser: https://github.com/ToonBettens/psf-parser, commit 05021e6 (MIT);
- psf_utils: https://github.com/KenKundert/psf_utils, commit 6797146 (v1.11, GPL-3.0-or-later);
- XDM 2.7.0: https://github.com/Xyce/XDM; Xyce_Regression: https://github.com/Xyce/Xyce_Regression, commit
  d4685581 (its `Netlists/XDM/SPECTRE` decks);
- the CMC benchmark decks: `utils/ADMS/examples/**/*.sp` in https://github.com/kev-cam/xyce (also upstream,
  https://github.com/Xyce/Xyce), at kev-cam/xyce commit 7cd78110;
- cadence2xyce.pl: https://github.com/kev-cam/xyce/blob/master/utils/cadence2xyce.pl, last changed in commit
  6e81e73f; the line numbers §3.1, §3.5 and §4.3 cite are those of that commit;
- VACASK: https://github.com/kev-cam/VACASK (a fork of https://github.com/arpadbuermen/VACASK): the engine
  build of the [E] row (0.3.4-91-g64489cf7, device libraries from 3becb73d) and the Cadnip evaluation build of
  §3 (0.3.4-94-gcdf13d22 with `-DCADNIP_PARSERS=ON`, recipe `tests/vamos/fixtures/spectre/cadnip/BUILD`);
- the real Spectre PSF samples: psf_utils' `samples/` and psf-parser's `tests/data`, at the commits above (the
  Spectre versions are in the [S] row).

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
- the command line: every option of the ref19 command-option chapter, plus ref5's `+spp`, `-spp` and
  `-sppbin`, accepted with a disposition (§2.3), including the abbreviations, the `=` family,
  `+%X`/`-%X`, `%S_DEFAULTS`/`SPECTRE_DEFAULTS`, `-raw`, `-format`, `-outdir`, `±log`/`=log`, CPP
  (`-E -D -U -I`), `+config`/`+pre_config`, `+paramdefault`, `-mts`, `-V`/`-W`/`-help`, a netlist read from
  stdin, and Spectre's signals (INT, TERM, HUP, QUIT, USR1, USR2);
- the Spectre language (§3): title line, comments, both continuation forms, escaped names, case-sensitive
  names and `insensitive=yes`, Spectre scale factors, `simulator lang=` switching, SPICE-mode files and
  sections (through `spice.py`'s `spectre-spice` dialect), `include … section=`, `library`/`section`,
  `ahdl_include`, parameters and user functions, `subckt` and `inline subckt` (nested definitions inherit
  the enclosing parameters), models and model groups (auto binning), structural `if`, `global`/ground, `ic`,
  `nodeset`, `save`, `options`, `set`, `paramtest`, and `alter` (dev/mod/sub/param/temp);
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
  Spectre's default is undocumented (§14 q.20); VACASK rejects a zero rise and reads a zero fall as "no fall";
- `prevoppoint=yes` and `useprevic` on Xyce: the operating point is recomputed (VACASK maps them, §5.4);
- a diode card without `eg` simulated at a temperature other than its `tnom`: Spectre's default `eg` is
  temperature dependent, and vamos uses its 27 °C value (§3.9);
- a `mos1`/`mos2`/`mos3` card under Spectre's default `capmod=bsim` when an `ac`, `noise`, `xf` or `tran`
  runs: both targets have only Meyer's gate charge (§3.9);
- a `mos1`/`mos2`/`mos3` card that gives `nsub` but not `gamma`, `phi` or `vto`: the targets derive them as
  SPICE3 does; Spectre's derivation is undocumented (§3.9, §14 q.32);
- every `mos3` card: Spectre's channel-length modulation is undocumented, and vamos writes SPICE2's
  (`badmos3=1`), on which the two engines agree (§3.9, §14 q.31);
- in SPICE mode, a `.tran` whose third field is not 0: it is read as SPICE2G's TSTART, Spectre's
  `outputstart` (§4.3, §14 q.9); an `x.N` bin that HSPICE's selection rule would choose differently for an
  instance (§4.3, §14 q.8);
- a parameter reference from a Spectre section to a SPICE one or the reverse (§3.7);
- the `strobe*`, `infonames`/`acnames`, `currents=all|nonlinear`, `subcktprobelvl>0`, `pwr` and
  `saveahdlvars` outputs are not produced.

On Xyce, the tolerance and accuracy settings (`reltol vabstol iabstol`, the accuracy part of `errpreset`,
`relref`, `lteratio`) are not passed on; only the integration method is (§5.4). That is a note, not a warning:
Xyce's tolerances have other meanings (the vcs-ams rule).

**Not in v1** (each is an error unless marked):
- `montecarlo` (statistics blocks are parsed and are harmless when no Monte Carlo runs; §3.12, and §12
  phase 3 maps it to VACASK's own `mc` loop); `altergroup`; `paramset`; sweep `hysteresis=yes`;
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
- on Xyce, the parameters of a Verilog-A model card, which covers the CMC Verilog-A models (§4.5 item 13):
  PyMS ignores them on a Y device and gave a wrong value on a letter device [E63, E64];
- source features `pwlperiod twidth ampl2 freq2 sinephase2`, AM/FM modulation, `tc1`/`tc2` ≠ 0;
  `noisefile`/`noisevec` while a `noise` analysis runs; `xfmag` ≠ 1 while an `xf` runs;
- device `ic=` on a capacitor or inductor (unless every `tran` uses `ic=dc` or `ic=node`), inductor-current
  initial conditions (`L1:1=…`), `force`/`readforce` other than `none`;
- `tran`: `start` ≠ 0 (§14 q.24), `tpoints`, `skipdc` values other than `no`/`yes`, transient noise (`noisefmax` > 0),
  dynamic parameters (`param=`/`param_vec`), the custom-errpreset parameters `maxstepratio`/`reltolratio`;
  `readtime`;
- an `options` statement inside a subckt (Spectre scopes it to that subckt), unless `-mts` is given (§3.6);
- a sweep or alter value that changes a structural `if` branch or a model-group bin (§3.7); a sweep or alter
  of a parameter vamos folded at parse time (a resistor or capacitor card's geometry, or an instance `w`, `l`,
  `area` or `perim` that fed a computed `r` or `c`, §3.8, §5.2);
- on Xyce, an alter, a source-field sweep or a `sub=` sweep whose target lies inside a subckt that more than
  one instance path reaches (§5.3, §7.2; VACASK maps it per path);
- `simulator` parameters other than `lang` and `insensitive`;
- saving `X:oppoint`, operating-point variables (`M1:gm`) or `:pwr` (warning: not written); `probelvl`
  (warning);
- a hierarchical node reference on an instance terminal (`r1 (x1.mid 0) …`); encrypted content; Spectre
  text hidden in a SPICE comment (`* spectre: …`, §3.13);
- behavioral expressions in parameter values (`V(…)`, `I(…)`), and an analysis, `ic` or `nodeset` value
  that reads a swept or altered parameter (§3.5, §5.1);
- in SPICE mode: `.noise`, `.alter`, `.four`, a `.temp` list, `.dc` `LIN/DEC/OCT/POI/DATA=` (§4.3);
- `+interactive`, `+top`, `-format uwi`, and an unknown `-format` value (errors); `+recover=f` and MDL
  (`+mdlcontrol`, `=mdlcontrol f`) (warnings: a fresh run, no measurements);
- output formats `psfbin psfbinf psfxl sst2 fsdb fsdb5 wdf wsfbin wsfascii awb tr0ascii`: psfascii is written
  instead (note).

## 1. Flow

```
spectre [opts] [input]                                                         personalities/spectre.py
 1. argv     <prog>_DEFAULTS | <PROG>_DEFAULTS | SPECTRE_DEFAULTS tokens, then argv → SpectreJob (§2);
             -V/-W/-h without a netlist end here; option errors end here (exit 2)  spectre/args.py
 1b. log     open the +log/=log file (percent codes expanded, %A empty); the provenance header
                                                                                    spectre/log.py
 2. run dir  beside the results path that argv and the defaults give (below)
 3. input    the netlist (stdin is copied to <run>/stdin, %C = stdin); +pre_config/+config
 4. CPP      only with -E, or -D/-U/-I without -disableCPP: cpp → <run>/cpp.out    spectre/cpp.py
 5. parse    Spectre/SPICE text → statements (spectre.statements) → IR with ordered statements;
             language and title per file (§3.1)                                     netlist/spectre.py (+ spice.parse_fragment)
 6. settings defaults < netlist options < command line (§2.2); the log takes the merged limits
                                                                                    spectre/job.settings, log.limits
 7. engine   --vamos-analog > VAMOS_ANALOG > vacask (engines.choose_engine); engines.problems → exit 2
 8. plan     statements → RunPlan in IR names: actions, sweep contexts, variables, per-analysis settings,
             paramtests, every reachable expression evaluated per instance path and parameter state, the
             condition/bin check (§5)                                               netlist/plan.py
 9. signals  saves → Refs, probe insertion in an IR copy, SignalMap (§6)          netlist/signals.py
10. VA       ahdl_include → openvaf-r → <ahdllibdir>/<stem>_<sha1>.osdi (VACASK), written atomically
11. run      render translates IR names and records every column in `names`. VACASK: one deck, one run.
             Xyce: one deck per analysis, each scanned for unknown parameters (§7)
                                                                                    spectre/run_vacask.py, run_xyce.py
12. results  engine output → AnalysisResults through `names`, streamed (sweeps split, noise/xf transformed) (§8)
                                                                                    spectre/results.py
13. write    <raw>/logFile + one PSF file per analysis, or one nutmeg file; state files (§8)
                                                                                    output/{psf,nutmeg,statefile}.py
14. finish   the log trailer and the exit status (§2.7)                           spectre/log.py, spectre/flow.py
```

- **Run directory** (the vcs-ams convention, `backends/cosim.py:750-756`).
  `tempfile.mkdtemp(prefix="<%C:r:t>.run.", dir=<the directory of the results path>)`, creating that
  directory when missing, from the results path that argv and the defaults give (§2.5). An `options
  rawfile=` found by the parse moves the results, not the run directory. Before the input is read, an
  existing `<raw>/logFile` (psf formats) or nutmeg results file is removed, so a failed run never leaves an
  earlier run's index behind (`cosim.py:676-682`). The run directory receives the stdin copy `stdin` and the
  cpp output `cpp.out`. `spectre.parse` reads the copy with `SpectreParseOpts.top_dir` = the cwd and
  `top_name` = `stdin` (§10), so its relative includes resolve against the cwd and its origins read
  `stdin:<line>`, also when they come through cpp's markers, which name the copy. The engines run there: VACASK
  writes `<analysis>.raw`, `__behavioral.va` and `.osdi` caches into its cwd, and Xyce writes decks and
  print files there. After exit status 0 the directory is removed with `shutil.rmtree` (vamos owns
  everything in it) unless `--vamos-keep` is given. After status 1, 2 or 3 it is kept and `SpectreLog`
  prints `run directory kept: <dir>`; an empty run directory is always removed.
- **Provenance.** The header of `VAMOS_PLAN.md` §4b goes to the screen and/or the log (§8.7):
  `banner.provenance([("vamos", …)] + the engine rows + [("cpp", …)] when cpp runs, "spectre")`, versions
  from `tools.version_of`. The engine rows come from `engines.tool_rows(engine, xyce=…)` (§10): VACASK and
  OpenVAF-r, or Xyce given as the path the run will execute (`engines.xyce_bin()`, else
  `tools.find_real("Xyce")`, §7.2), so the row names the binary that runs. `ams/flow.compile_tools` is not
  called: it takes a vcs `Job` (it reads `job.ams_control`, and a `SpectreJob` would raise `AttributeError`
  there, swallowed by its `except Exception`), imports the whole AMS compile stack, and names Xyce as
  `engines.xyce_bin() or "Xyce"` (`ams/flow.py:51-75`); it becomes a caller of `tool_rows` with that
  argument, so vcs-ams's header is unchanged. `licenses.json` gains `"cpp": {"spdx": "GPL-3.0-or-later", "url":
  "https://gcc.gnu.org"}`; without it the row reads `licence unknown` [E79]. A spectre run has no daidir, so
  it writes no `vamos.tools.json` (`VAMOS_GUIDE.md` §8 says so).
- **vamos options:** `--vamos-analog=vacask|xyce`, `--vamos-strict`, `--vamos-keep`,
  `--vamos-psf-names=modern|legacy` (§8.1), `--vamos-banner=…`, `--vamos-verbose`. They are read from the
  command line only, as for vcs option files (`vcs.py:321-326`): a `--vamos-*` token in `<prog>_DEFAULTS`
  is not applied and is recorded UNSUPPORTED ("vamos options are read from the command line only, not from
  <var>; use VAMOS_ANALOG, VAMOS_BANNER or VAMOS_VERBOSE"), a warning and exit status 2 under
  `--vamos-strict`. The vcs-ams-only options get `INAPPLICABLE` entries (§10, `optable`).

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
- **`optable` extension (additive):** `scan(table, args, job, positional, unknown, option_chars: str =
  "-+")`. Today only `-` and `+` tokens are options (`optable.py:92, 94`), so `=log` would be taken as the
  netlist [E79]. Both tests use `option_chars`, and a bare option character stays positional (`tok not in
  tuple(option_chars)`). The spectre personality passes `"-+="`. `Table.match` (exact name first, then the
  longest prefix) resolves `+mt`, `+mt=4` and `+mtmode`, `-D` against `-debug`, and `++aps=` [E80].
- `+%X string` and `-%X` (X a single letter) are removed by `spectre/args.prescan` before `scan`, because the
  value of `+%X` is the next token [M ref19 p.28; UG p.265].
- **Help.** `prescan` also takes `-help`, `-h`, `-helpsort`, `-hs`, `-helpfull`, `-hf`, `-helpsortfull` and
  `-hsf` [M ref19 pp.25-26]. When the next token exists and does not start with `-`, `+` or `=`, it becomes
  `help_topic` (optable has no optional-value arity, so a flag entry would make the topic the netlist
  [E80]). A help request never simulates and exits 0; an unknown topic gets a note. Every spectre help text,
  with or without a topic, stays within 100 columns and ends with the absolute path of
  `docs/VAMOS_GUIDE.md`, the rule every vamos help text follows (`VAMOS_GUIDE.md` §2, `test_vamos_driver.py:151-166`).
- **`argscan.expand_option_files` is not used.** In Spectre `-f` is `-format` [M], not an option file.
- A Spectre option the table does not know gets a warning ("unknown option X ignored") and is recorded. Under
  `--vamos-strict` it is exit status 2.
- **The invoked name.** `cli.main` dispatches to this personality when the invoked name (`VAMOS_ARGV0` or
  `argv[0]`'s basename) is `spectre`; `spectre` plus a version suffix (`^spectre[-_.]?[0-9][0-9A-Za-z._-]*$`,
  e.g. `spectre231`, `spectre-23.1`); or a name listed in `VAMOS_SPECTRE_NAMES` (colon-separated, e.g.
  `specsim`, the manual's own alias example [M UG pp.239-240]). Other `spectre*` names never dispatch:
  `spectrespp` [M UG pp.42-43], `spectre_encrypt` [M ref19 p.471] and the like get a usage error (exit
  2) naming them as Cadence programs vamos does not provide. Today only exact `PERSONALITIES` keys dispatch
  and anything else prints the usage text with exit 2 [E79]. `cli.main` stores the name as
  `tools.invoked`, next to `tools.current` (`cli.py:103`); under `vamos -spectre` it is `spectre`. It is
  `%S`, the `<prog>` of `<prog>_DEFAULTS` [M ref19 p.39] and of the log trailer (§8.7). The `--vamos` option
  dict is not used for it.

### 2.2 Defaults and precedence
- `args.defaults_tokens(env, prog)` returns the shlex-split tokens of the first of these variables that
  exists: `<prog>_DEFAULTS` (`spectre_DEFAULTS` for `spectre` [M ref19 p.39]), then `<PROG>_DEFAULTS`
  upper-cased, when different (the UG names `SPECSIM_DEFAULTS` for a `specsim` link [M UG pp.239-240]; the
  order is [I, §14 q.39]), then `SPECTRE_DEFAULTS` [M UG p.239]. A variable that exists but is empty means
  no defaults, and the next one is not read: both manuals fall back only when the variable "does not exist".
- Precedence: the command line beats the netlist `options` statements, which beat the defaults [M ref19 p.39].
  vamos keeps the defaults and argv as two layers, and `SpectreJob` keeps them apart (§10).
  - Each layer is scanned on its own. The same-command-line rules of `=config`, `-config` and `-pre_config`
    [M ref19 p.38] apply within a layer.
  - List options (`-D`, `-U`, `-I`, `+config`, `+paramdefault`, `-va,define`) accumulate, defaults first. For
    scalar settings argv wins: the job's scalar fields hold argv's values (None when argv does not set one),
    and `SpectreJob.defaults` the defaults layer's, by field name. Argv's `-%X` undefines a `+%X` from the
    defaults [M UG p.266].
  - A non-option token in the defaults is an error (exit 2).
  - Spectre reads from the defaults variable only the options ref5 marks † [M ref5 p.18; ref19 pp.25, 39].
    An option ref5 lists without † is ignored there, with the note "Spectre does not read <opt> from <var>;
    ignored": `-help` and its forms, `-V`, `-W`, `-env`, `-%X`, `+note`/`-note`, `-slave`, `-slvhost`,
    `+sensdata`, `±interactive`, `+mpssession` and `+mpshost` [M ref5 pp.18-21]. Applying them would make a
    `-help` in `SPECTRE_DEFAULTS` turn every run into a help request, and an `+interactive` (an *error* row)
    fail every run. An option ref5 does not list (ref19's additions: the ref19 text carries no † marks) is
    applied from the defaults [I, §14 q.39].
- Settings that an `options` statement can also set are resolved after parsing, as argv ⊕ options ⊕
  defaults: `-format`↔`rawfmt`, `-raw`↔`rawfile`, `-maxwarns`↔`maxwarns`, `-maxnotes`↔`maxnotes`. Every
  other setting is argv ⊕ defaults. `spectre/job.settings(job, nl)` builds the result, a `Settings` (§10;
  S5 owns it), from the job's argv fields, the netlist's `options` and `SpectreJob.defaults`, in that order.
  `SpectreLog` is opened at step 1b with argv ⊕ defaults; `SpectreLog.limits(settings)` then applies the
  merged `maxwarns`/`maxnotes` from step 6 on (messages printed before the parse keep the early limits).

### 2.3 Options

`Option` is the literal `Opt` name, as in `VAMOS_AMS_DESIGN.md` §8: `eq` names exclude the `=`, `prefix` names
are the fixed part. "Ignored" is silent; "noted" prints one line; "unsupported" warns. Rows marked *error*
are actions that append `notes.error(<option>, <why>)` to `SpectreJob.notes` (as do a second netlist and a
bad `-format` value); `flow.run` prints them through `SpectreLog` and stops with exit status 2 before the
run directory is created. `SpectreJob.unmapped` holds only optable's five dispositions, because
`optable.report_unmapped` and `strict_failures` skip any other (`optable.py:115-134`).

| Option (abbrev.) | Kind | Disposition |
|---|---|---|
| `-help` (`-h`), `-helpsort` (`-hs`), `-helpfull` (`-hf`), `-helpsortfull` (`-hsf`) | prescan, optional topic | mapped: vamos help (the supported subset; with a topic, that primitive's or analysis's dispositions); exit 0 (§2.1) |
| `-V`, `-W` | flag | mapped: the version / subversion line (§8.7); the run continues when a netlist is given [M UG p.234]; exit 0 when there is none [I, §14 q.16] |
| `-cmiversion` | flag | noted |
| `-cmiconfig` | next | noted |
| `-raw` (`-r`) | next | mapped (§2.5) |
| `-format` (`-f`) | next | mapped: `psfascii`, `nutascii`, `nutbin`; `psfbin psfbinf psfxl sst2 fsdb fsdb5 wdf wsfbin wsfascii awb tr0ascii` → psfascii (note); `uwi` or anything else → *error* |
| `-outdir` | next | mapped (§2.5) |
| `+rtsf` | flag | ignored |
| `-uwifmt`, `-uwilib` | next | unsupported |
| `+log` (`+l`), `=log` (`=l`) | next | mapped: stdout and file / file only (§8.7) |
| `-log` (`-l`) | flag | mapped: stdout only (the default) |
| `-cols` (`-c`), `-colslog` | next | ignored |
| `+error -error +warn -warn +note -note +info -info +debug -debug` | flag | mapped: whether that class is printed (screen and log); the trailer counts are unaffected |
| `-maxwarns` (`-maxw`), `-maxnotes` (`-maxn`) | next | mapped: per message id and analysis, on the screen (the id is §8.7's normalized text [I, §14 q.40]) |
| `-maxwarnstolog` (`-maxwtl`), `-maxnotestolog` (`-maxntl`) | next | mapped: the same limits for the log file |
| `+varedefnerror` | flag | noted |
| `+%X string`, `-%X` | prescan | mapped (§2.4) |
| `-E` | flag | mapped (§2.6) |
| `-D` (`-D<x>`, `-D<x=y>`), `-U` (`-U<x>`) | prefix | mapped: cpp defines; they make cpp run [M ref19 p.30] |
| `-I` (`-I<dir>`) | prefix | mapped: a search directory for `include` [M UG p.73] and PWL `file=` [M UG p.74], and also for `ahdl_include` and Verilog-A `` `include``, after `CDS_VLOGA_INCLUDE` (§3.1) [I, §14 q.42: the manuals name only `CDS_VLOGA_INCLUDE` for Verilog-A includes, ref19 p.551]; it also makes cpp run [M ref19 p.30] |
| `-disableCPP` | flag | mapped: -D/-U/-I no longer run cpp; -E still does [M ref19 p.37] |
| `+config`, `=config` | next | mapped: a Spectre-mode fragment appended to the netlist. Several `+config` are processed in order; `=config` appends its file, and every `+config` on the command line is then dropped [M ref19 p.38] |
| `-config` | flag | mapped: drops every `+config` and every preceding `=config` [M ref19 p.38] |
| `+pre_config` | next | mapped: a fragment prepended to the netlist, after the title line [I, §14 q.43] |
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
| `+checkpoint` (`+cp`), `+savestate` (`+ss`) | flag | noted: no checkpoint or savestate file is written |
| `-checkpoint` (`-cp`), `-savestate` (`-ss`), `-recover` (`-rec`) | flag | ignored |
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
| `-ahdllibdir` | next | mapped: where compiled Verilog-A (`.osdi`) is kept (§2.5) |
| `-va,define` | next | mapped on VACASK (`openvaf-r -D MACRO[=VALUE]` [E75]); noted on Xyce |
| `-ahdlcom` (`-ac`), `-ahdllint_log`, `-rf_ahdl_functionality` | next | ignored |
| `-ahdllint`, `-ahdllint_maxwarn`, `-ahdllint_summary_maxentries` | flag, eq | ignored |
| `-ahdllint_warn_id`, `-ahdlshipdbdir`, `-ahdlshipdbmode` | eq | ignored |
| `-ahdlsourceramp` | flag | noted |
| `+sensdata` | next | noted |
| `+spp`, `-spp` | flag | ignored: vamos picks SPICE mode itself (§3.1) [M ref5 p.21; UG p.42] |
| `-sppbin` | next | ignored [M ref5 p.21] |
| `-env` | next | ignored [M ref5 p.20: artist2 artist4 awb edge opus solo; ADE passes `ade`] |
| `+escchars` (ADE; not in ref19) | flag | mapped [I, §14 q.12]: backslash-escape non-name characters in PSF signal names (§8.3) |
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
  the files it writes, after removing a stale `logFile`, §1). For nutascii/nutbin it is a single file, and
  no `logFile` is written [M UG p.229] (§8.5).
- **Log:** `+log f` or `=log f`, the path %-expanded and relative to the cwd (or `-outdir` when it has no
  `/`). The default is stdout only: `-log` is listed first, which makes it the default [M ref19 pp.26, 39],
  and the UG calls `+log` "an option that is normally deactivated" [M UG p.239]. `-log` displays the log "on
  the standard output (shell) only" and `=log` "does not display it on the standard output" [M ref19
  pp.26-27].
- **Verilog-A:** `-ahdllibdir`; else `$CDS_AHDLCMI_SIMDB_DIR`, an error when it is missing or not writable
  [M ref19 p.542]; else `%C:r:t.ahdlSimDB` under `-outdir` when one is given [M ref19 p.34; I for `-outdir`
  alone, §14 q.3]; else in the cwd, Spectre's own location [M ref19 p.542]. Concurrent runs share it, so each `.osdi`
  is written to a temporary file and moved with `os.replace`.
- **State files** (`write=`, `writefinal=`): a relative name resolves against the cwd, or against `-outdir`
  when the name has no `/` (§14 q.3).

### 2.6 The C preprocessor
- cpp runs when `-E` is given, or when `-D`, `-U` or `-I` is given without `-disableCPP` [M ref19 pp.30, 37].
- It is the system `cpp`, found through `tools.find_real("cpp")` (which honours `VAMOS_CPP`), called as `cpp
  -traditional-cpp -nostdinc -undef [-D…] [-U…] [-I…] <absolute input path> <run>/cpp.out` [I, §14 q.4],
  with cwd = the job's cwd, stdin `/dev/null` and `tools.child_env()`. ISO mode is not used: it empties a
  `//` line, and it warns, naming file:line, about an apostrophe in a SPICE `*` comment (`don't`), which the
  rule below would make a warning and `--vamos-strict` an exit status 2 for a valid netlist; traditional mode
  does neither [E82]. Whether Spectre's own CPP behaves as ISO or as traditional cpp stays §14 q.4. A cpp
  failure is an error, and so is a missing cpp (Cygwin has none [E79]). cpp's stderr is captured: each line
  becomes a vamos note (a warning when it names a file:line) and is never printed raw.
- **Markers.** The `# <line> "<file>" [flags]` lines of cpp's output decide, per source file, the statement
  origins, the directory relative `include`s resolve against, and the language. cpp names an included file
  as it found it, relative to its own cwd (`# 1 "inc/sub.scs" 1` for `-Iinc` [E82]), so marker paths
  resolve against the job's cwd; the `<built-in>` and `<command-line>` markers name no file and are skipped.
  Each file gets §3.1's rule by its own name and first line; flag 1 enters a file, and flag 2 returns to the
  includer and restores its mode [M ref19 p.493: the language rule applies to files read by cpp's `#include`
  too]. The title is line 1 of the original top-level file, read before cpp, so it does not depend on the
  cpp mode (ISO mode empties a `//` line [E47, E82]). The name `cpp.out` is never used for language detection.
- `#include` is a cpp directive. A plain `include` is handled by vamos itself [M UG p.73].
- A line starting with `#include`, `#define`, `#if…` when cpp does not run is an error, "CPP directive without
  -E": Spectre errors on a `#include` that cpp did not process [M ref19 p.493], and its SPICE Reader treats such
  lines as a syntax error [M UG p.52].
- A macro inside a SPICE-mode quoted expression (`.param rr='VDD*1k'`) is not expanded in either cpp mode
  [E82]: a defined macro name inside a `'…'` expression gets a warning.

### 2.7 Exit status and signals [M UG pp.235-237]

| status | Spectre's meaning | vamos |
|---|---|---|
| 0 | completed normally | every planned analysis ran and left its complete output (a note- or warning-only run included) |
| 1 | an analysis stopped because of an error | at least one analysis or sweep point failed in the engine; a missing, incomplete or short rawfile counts, whatever the engine printed or returned (§7.1, §7.2). The others ran and were written |
| 2 | stopped early because of a Spectre error condition | any vamos error: an unsupported construct, a parse or plan error, a strict-mode failure, a missing netlist; an engine netlist or elaboration error; an unknown model parameter in any Xyce deck (§7.2); a failure of a non-analysis control step; an engine crash or an engine ended by a signal other than those of status 3; any exception inside vamos (below) |
| 3 | stopped by the user or the operating system | SIGINT, SIGTERM, SIGHUP or SIGQUIT to vamos [M UG p.237], or SIGKILL, SIGTERM, SIGINT or SIGHUP to the engine from someone other than vamos (the OOM killer, an operator): the engine is stopped; the finished analyses are converted, and so is the interrupted one, from the engine's partial output (VACASK: `rawfile.fix_points` on the rawfile it leaves [E40, E81]; Xyce: its `FORMAT=RAW` print file, which `rawfile.read` accepts after a signal [E72]; its noise `.prn` is §14 q.26), with a warning naming it [M UG p.236] |

- After every exit status, the analyses that finished are converted and listed in `logFile`, and the trailer
  is written (§8.2, §8.7). A run that stops before any analysis has run (a parse error) writes no data file
  (a stale `logFile` was removed at the start, §1).
- **No exception reaches `cli.py`.** `personalities/spectre.main` returns `flow.run`'s status, and
  `flow.run` maps all of these to exit status 2 with the `terminated prematurely` trailer:
  `optable.ScanError`, `tools.ToolError` (a bad `VAMOS_<TOOL>`; `cli.main` would make it 1 [E79]), a banner
  `ValueError`, engine-choice errors (`VAMOS_ANALOG`, `engines.problems`), an `OSError` while setting up the
  log, the run directory or the results, and any other exception (an internal error: one line on the
  screen, the traceback written into the run directory). In Spectre 1 means "stopped an analysis because of
  an error" [M UG p.235], so a setup failure must not look like a partial result. The cli-level usage errors
  (`check_vamos_opts`, `cli.py:67-71`) stay exit 2; they happen before `+log` is read, so they have no log
  file and no trailer.
- **Signals.** `flow.run` installs its handlers before the input is read, in the main thread, for INT, TERM,
  HUP, QUIT, USR1, USR2 and TSTP. A signal that was ignored at start stays ignored: under `nohup` or as a
  background job of a non-interactive shell SIGHUP/SIGINT are `SIG_IGN` [E81], and a run must not then stop
  with status 3 at logout.
  - Before an engine runs, INT, TERM, HUP or QUIT end the run with status 3 and the trailer.
  - While an engine runs, the vcs-ams machinery applies, moved from `backends/nvc.py` (`_Interrupts`,
    `_child_setup`, `signal_name`, lines 612-728) to `vamos/proc.py` and imported back by `nvc.py`,
    parameterised by the signals handled, the signal forwarded and the grace period (§10). `nvc.py` keeps its
    own `INTERRUPT_GRACE` (line 55) and passes it to each `Interrupts` when it creates one, so
    `test_vamos_run.py`'s `TestStreamSignals.test_grace_period_kills`, which sets `nvc.INTERRUPT_GRACE = 0.5`,
    still tests the grace it sets: with the constant imported from `proc.py`, the assignment would no longer
    reach the handler, and the test would still pass only because the real 5 s is under its 10 s bound.
    `cosim.py` keeps reading `nvc.INTERRUPT_GRACE` (`cosim.py:870-872`). The engine gets its own process
    group, stdin `/dev/null` and `PR_SET_PDEATHSIG`, so it never outlives vamos.
  - The first INT, TERM, HUP or QUIT is passed to the engine as SIGTERM: both engines install no handlers and
    stop at once, leaving a readable rawfile [E81]. SIGKILL follows after `INTERRUPT_GRACE` (5 s), or at a
    second signal, which also makes vamos exit 3 at once without converting [M ref19 p.521]. Ctrl-Z
    (SIGTSTP) stops the engine together with vamos [M ref19 p.32] and SIGCONT resumes both.
  - SIGUSR1 and SIGUSR2 are never forwarded: each kills both engines [E81]. Their handlers only set a flag
    (printing from a handler can raise `RuntimeError`, since Python's buffered writers are not reentrant),
    which the main loop collects with `Interrupts.take_flags()` (§10). It then prints the SIGUSR1 status
    line (the running analysis and, where the engine reports it, its progress) [M UG p.235] or the SIGUSR2
    note "checkpoint not supported" [M UG p.237], and the run continues.
  - The exit status is 3, not the re-raised signal that `simv` uses (`simv.py:304-317`) [M UG p.235].

### 2.8 Environment
- `<prog>_DEFAULTS`, `<PROG>_DEFAULTS`, `SPECTRE_DEFAULTS`: §2.2. `VAMOS_SPECTRE_NAMES`: §2.1. `VAMOS_ANALOG`,
  `VAMOS_BANNER`, `VAMOS_VERBOSE` and the `VAMOS_<TOOL>` overrides: as for every personality (`VAMOS_GUIDE.md`
  §7).
- `CDS_AHDLCMI_SIMDB_DIR`: mapped (§2.5) [M ref19 p.542].
- `CDS_VLOGA_INCLUDE` [M ref19 pp.550-551]: mapped. The module scan of §3.1 and, on VACASK, openvaf-r get the
  same directory list (openvaf-r 20260616 takes `-I, --include <DIR>` [E98]); noted on Xyce, whose `.hdl`
  compile vamos gives no include directories (it resolves an include beside the `.va` file [E75]).
- `CDS_AHDL_FINISH_MODE`, `CDS_AHDL_DDT_SCALE`, `CDS_AHDL_IDT_SCALE`, `CDS_AHDL_IGNORE_HIDDEN_STATE`,
  `CDS_AHDL_IGNORE_OPPOINT`, `CDS_AHDL_AUTOGDEV_SUPPORT`, `CDS_AHDL_LRM_COMPATIBILITY` set to a non-default
  value: a warning, not applied (they change results [M ref19 pp.550-551]).
- `CDS_AHDLCMI_SHIPDB_COPY`, `CDS_AHDLCMI_SHIPDB_DIR`, `CDS_AHDL_COMPILEC_MAX_LOAD`, `CDS_AHDL_REUSE_LIB`,
  `CDS_CMI_COMPLEVEL` (caches and performance): ignored.

## 3. Spectre netlists – `netlist/spectre.py`

One stdlib parser, producing the same IR as `spice.py` (§4). vamos must stay pure Python 3.9 with no build
step, and must give every construct of either language one disposition on an IR both engines print. The
existing translators were re-evaluated on 2026-10-04; none meets that, and each is used instead (§11):
- **NetlistParse.rs** (NyanCAD, MIT, d565fd3; a port of Cadnip.jl's parser): Rust, and its Python binding
  exposes only `parse_spice` (`crates/netlist-py/src/lib.rs:106-128`). Its Spectre CST cannot parse model
  groups, statistics, sweep/montecarlo bodies, bare instance node lists, library/section, prot/unprot or
  save modifiers, and differs from the manuals in six places [E88]. Used as the CST differential oracle
  (§11 T0) and its SPICE corpus as an acceptance set (§11 T0).
- **XDM** 2.7.0: Xyce only; drops options, info and tran accuracy and state-file parameters, drops analyses
  not named after their keyword, and mis-maps terminal saves [E89]. Its regression decks, real ADE netlists
  with hand-written Xyce golds, are §11 T3a.
- **VACASK's Spectre include path** (Cadnip: VACASK's CMake option `CADNIP_PARSERS`, which fetches
  NetlistParse.rs; off in the `/opt` build, on in an evaluation build of VACASK with `-DCADNIP_PARSERS=ON`,
  recipe `tests/vamos/fixtures/spectre/cadnip/BUILD`): include-only; drops analyses, saves and `ic`; passes
  parameter text verbatim (integer division, `meg` and `x` as mega, no `_ % c`); needs model lines for
  Spectre's primitives; silently drops statements it cannot parse [E76]. Not a fidelity oracle; §11 T3b
  uses it, with the primitives declared by the harness, as an optional structural oracle.
- **cadence2xyce.pl** (kev-cam/xyce's `utils/cadence2xyce.pl`, Perl, Xyce only; the line numbers §3.1, §3.5
  and §4.3 cite are those of commit 6e81e73f, §0 Sources): its SPICE-dialect rules are carried into the
  `spectre-spice` dialect (§4.3, rule by rule) and its Verilog-A module scan into §3.1; its stem alias and its
  inlining of Verilog-A `` `include``s are not adopted (§3.1).

### 3.1 Files and languages
- **Title.** Line 1 of the top-level netlist is the title and is never executed: any statement on it is
  ignored as a comment [M UG p.29], with the one effect on the language below. It labels the outputs: it is
  the PSF `"design"` value [S]. An included file has no title line [M UG p.73]. With cpp, the title comes
  from the original file (§2.6).
- **Language.** A file is in Spectre mode if its name ends in `.scs` or it starts with `simulator
  lang=spectre`; otherwise it starts in SPICE mode, which is Spectre's own default [M UG pp.50, 54, 73; ref19
  p.493]. The rule holds for the top-level netlist, for files read by `include` or by a SPICE `.include` or
  `.lib`, and for files read by cpp's `#include` (§2.6). For the top-level netlist, line 1 stays the title and
  is not executed, but a line-1 `simulator lang=spectre …` still selects Spectre mode for the file (note); a
  `.scs` file whose line 1 is `simulator lang=spice` is §14 q.5. A netlist read from standard input has no
  name: SPICE mode unless it starts with `simulator lang=spectre`. `simulator lang=spectre|spice` switches
  at any statement position and holds until the next switch or the end of the file. An included file starts
  in its own language, and the includer's mode is restored after it; a statement cannot continue across a
  file boundary [M UG pp.53, 73; ref19 p.493]. A `simulator lang=` inside an `if`, or inside a subckt body in
  either language, is an error in v1 naming the subckt (a SPICE region that ends inside `.subckt` would
  otherwise get spice.py's "has no .ends", `spice.py:852-856`).
- **spectre.py owns every file.** It reads the Spectre and the SPICE files alike and hands each SPICE region
  to `spice.parse_fragment`, which reads no file in the `spectre-spice` dialect. The first phase (§4.3)
  returns every SPICE file statement (`.include`/`.inc`/`.incl`, both `.lib` forms, `.hdl`) with the
  `.subckt` it sits in. spectre.py resolves each, declares its names, and splices a SPICE-language file (or
  the `.lib` section) into the region's `(line, origin)` list in place of the statement, so it is read in the
  scope where it is named, inside an open `.subckt` too, as spice.py reads it today (`spice.py:993-1090`). A
  Spectre-language file named by a SPICE include ends the SPICE region there and is read as Spectre; inside an
  open `.subckt` it is an error in v1, like a `simulator lang=` there. `.end` comes back as a control (§4.3).
  Today spice.py reads every file it includes as SPICE (a `.scs` with `simulator lang=spectre` became an
  "S-parameter element" [E60]) and tries the cwd first [E60].
- **Case-insensitive definitions.** `simulator lang=spectre insensitive=yes|no` [M UG pp.47-49]. Models and
  subckts defined under `insensitive=yes`, and all models and subckts defined in SPICE mode, are marked
  case-insensitive: the SPICE Reader lower-cases them and inserts `simulator lang=spectre insensitive=yes`
  into each converted netlist [M UG pp.49, 52] (whether the built-in reader of later versions does the same
  is §14 q.10). A reference (an instance master, `alter dev=`/`mod=`, `probe=`, `ind1=`/`ind2=`, a sweep
  `mod=`) first tries an exact-case match, then a marked definition whose lower-cased name matches; Spectre
  mode may refer to models and subckts defined in SPICE mode [M UG p.52]. Two marked definitions that differ
  only in case are an error. Node and parameter names are unaffected. `spectre.py` resolves each reference
  to the definition's IR name, so the IR carries no mark. Any `simulator` parameter other than `lang` and
  `insensitive` is an error.
- **include** `"file" [section=name]`: an absolute path is used as is. A relative one is tried against the
  directory of the including file, then each `-I` directory: "relative to the directory of the including
  file …, not from the directory in which the Spectre simulator was called" [M UG p.73]. `~`, `$VAR`,
  `${VAR}` and %-codes are expanded [M UG pp.73-74; ref19 p.493]. If none exists, the error lists every path
  tried. Each `(realpath, section)` is expanded once per scope (the top level or the enclosing subckt); a
  repeat is a note (the `spice.py` rule). A SPICE `.include`/`.lib` follows the same rules; a SPICE file's
  `.lib` sections are indexed with `spice._File` (`spice.py:555-587`), and a Spectre-language file named by a
  SPICE `.lib` is an error (use `include … section=`).
- **library / section:** `library L` … `section S` … `endsection [S]` … `endlibrary [L]`; the optional
  trailing names must match [M UG pp.75-76]. `include "f" section=S` reads only section S. A plain `include`
  of a file that defines sections is an error naming them [I, §14 q.43].
- **ahdl_include** `"file.va"` (and SPICE mode's `.hdl`, §3.13) uses the include path rules. The absolute
  path goes into `Netlist.hdl`, and the file's modules become instance masters (IR kind `y`, existing
  rules). **Module discovery follows `` `include``**, recursively, relative to the including Verilog-A file,
  then the `CDS_VLOGA_INCLUDE` directories [M ref19 p.551] and the `-I` directories [I, §14 q.42] (the list
  openvaf-r gets too, §2.8), comments stripped, the standard headers (`*.vams`) skipped: the CMC trees put
  the module in an included file (BSIM-CMG's `bsimcmg.va` holds only `` `define``s and `` `include
  "bsimcmg_main.va"``), so today's scan of the named file alone reports "declares no Verilog-A module"
  (`spice.py:1169-1177`) [E57, E61], although both engines' compilers follow the include [E63, E75]. A
  module still not found is an error naming the files read. The scan is one helper, `spice.va_modules`
  (§10), shared with `spice.d_hdl`, so the HSPICE path gets the same fix (§4); its result is kept in
  `Netlist.va_modules` (§4.1) for the emitters. cadence2xyce.pl's `scan_va_modules` (lines 390-444 at
  6e81e73f, one level of `` `include``) is the precedent. Its stem alias (lines 103-112: a card naming `bsimcmg` bound to
  `bsimcmg_108`) is not adopted: a heuristic that could bind a card to the wrong module; a netlist names the
  module.
- **Parameter declarations go through a preprocessor.** Real compact models declare their parameters
  through macros: VACASK's PSP103 module body has 413 `` `MPR…``/`` `IPR…`` lines and no plain `parameter`
  line, BSIM6.1.1 870, VBIC 1.3 129 and BSIM-BULK 106 950, the macros being defined in included files
  (`Common103_macrodefs.include:140`: `` `define MPRoo(nam,def,uni,lwr,upr,des) (*units=uni, desc=des*)
  parameter real nam=def from(lwr:upr);``); even the ADMS toy resistor puts an attribute first (`(*desc=
  "Resistance", type="instance"*) parameter real R=1 …`) [E97]. So `va_modules` runs a minimal stdlib
  Verilog-A preprocessor over each module: `` `define`` with and without arguments, `` `undef``, `` `ifdef``/
  `` `ifndef``/`` `elsif``/`` `else``/`` `endif`` (with `-va,define`'s macros defined), `` `include`` on the
  directory list above, comments stripped. It then collects the names declared by `parameter` and
  `aliasparam` inside the module (not `localparam`), skipping the `(* … *)` attributes before them, and reads
  the attributes of the module header (§4.5 item 13). When it cannot finish (an undefined macro in a
  declaration, an unbalanced `` `ifdef``, an include not found), the module's parameter list is unknown
  (`VaModule.params` None): its parameter names pass unchecked, lower-cased
  on VACASK, with one note naming the module, and VACASK's "Parameter … not found" (exit status 2, §7.1) or
  Xyce's unknown-parameter scan (§7.2) still fails loudly on a wrong name.
- **Fragments:** `+pre_config` and `+config` files are Spectre-mode fragments, parsed before or after the
  netlist. Their statement origins are `file:line`, like every other origin (spice.py derives an include's
  directory from it, `spice.py:899-904`); the `+config:` prefix is added in messages only.
- **protect / unprotect**, also abbreviated `prot`/`unprot`, in any case [M ref19 p.497]: a note (they only
  hide listing output). Encrypted content is an error.

### 3.2 Lexical rules [M UG pp.59-62]
- Spectre text is read by `spectre.statements(text, origin, title)` (§10), the lexical and statement layer
  without file access or lowering, which the CST oracle compares with NetlistParse.rs (§11 T0); `parse()`
  is built on it.
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
  parameter names, are accepted, except that a parameter named `temp`, `tnom`, `temper`, `time` or `hertz` is
  §3.5's v1 error. A name that is a VACASK reserved word is quoted by the emitter (the existing rule).
- **Multiple namespaces:** an instance, a node and a parameter may share a name (`res c10 0 resistor
  r=res`). In `save vcc`, the node wins over an instance of the same name [M UG p.61].

### 3.3 Numbers [M UG pp.83-84]
One reader, `expr.number(s, dialect="hspice", warn=None)`, serves the statements and the expression lexer
(`expr.py:239` reads every literal through it), so `r=2*1M` follows the same table as `r=1M`. `warn(severity,
message)` reports the notes and warnings below; spectre.py adds the origin. Today's `expr.number` differs from
the Spectre table on 12 of 38 test literals, among them HSPICE's `D` exponent added after revision 2
(`expr.py:149-152`) [E52]. A prototype of the reader below agrees with UG p.84 on all 38 and reads `0.22u`
as exactly 2.2e-07 on both Pythons [E52]. `numbers.py` stays frozen.
- **Spectre mode** (`dialect="spectre"`): `[+-]`, then `digits[.[digits]]` or `.digits`, then an optional
  `(e|E)[+-]digits`, then an optional scale factor from `T G M K k _ % c m u n p f a` (case sensitive: `M` is
  1e6, `m` is 1e-3), then optional unit letters `[A-Za-z_]*`, which are ignored. The scale shifts the decimal
  exponent (correctly rounded). `.55`, `.148p` and `1E-14` are numbers [M UG pp.30, 83, 95, 114]; the
  expression lexer accepts `%` and `_` only as a scale factor directly after a number (`5%` is 0.05;
  NetlistParse.rs reads it as a modulo [E55]).
  - An exponent together with a scale factor: the scale factor is ignored (`1.234E-3p` = 1.234e-3) [M]; a
    note.
  - Unit letters without a scale factor are an error ("units without a scale factor"): `r=50Ohms` is
    rejected, `r=50_Ohms` is fine [M].
  - `1meg` and `1mil` read as 1e-3 (`m` plus the units `eg`/`il`), with the warning "Spectre mode: m is
    milli"; `1MEG` is 1e6 (§14 q.6). `0.3MHz` is 3e5 [E52].
  - `P`, `x`, `F` and `D` are not Spectre-mode scale factors: `1P`, `1x`, `1F` and `1D3` are errors (§14 q.6
    on later versions). There is no `D` exponent.
- **SPICE mode** (`dialect="spectre-spice"`, numbers in SPICE text and in its expressions): `t g meg k m mil
  u n p f`, case-insensitive. "Any other scale factor is ignored (treated as 1.0)" [M UG p.84], so `1x` and
  `1a` are 1 where HSPICE reads 1e6 and 1e-18: a warning when the letters start with `x` or `a`; other unit
  letters (`5V`, `10Ohm`) are silent. An exponent with a scale factor: the factor is ignored, with a note (UG
  p.83 states it before the two mode tables) [M]. A `D` exponent (`1.0D+3`) is an error [I, §14 q.6: not in
  Spectre's documented SPICE syntax].

### 3.4 Ground, globals and hierarchical names
- **Ground** is `0` unless there is a `global` statement. Then the first name of the first `global`
  statement is ground. Several `global` statements accumulate [M ref19 p.482].
- The parser folds the ground name to `0` in the IR, so the emitters stay unchanged. If the ground is not `0`
  and a node literally named `0` is also used, that node is renamed `vamos_node0` (spelling `0`), with a
  warning (§14 q.7).
- **No ground aliases.** `gnd`, `gnd!` and `ground` are ordinary nodes unless declared ground. This differs
  from `spice.py`'s HSPICE fold, which `spice.parse_fragment` therefore switches off in the `spectre-spice`
  dialect (§4.3).
- **Node names are strings in both modes.** `00`, `007` and `7` are three nodes and only `0` is ground: "The
  SPICE Reader treats these like SPICE3" [M UG pp.56, 60]. spice.py's HSPICE fold merges `007` with `7` and
  makes `00` ground [E58] (`spice.py:272-277`); the `spectre-spice` dialect switches it off (§4.3).
- Other `global` names go into `Netlist.globals`.
- A hierarchical node name (`x1.mid`) on an instance terminal is an error in v1: printed as is, it would most
  likely become a new top-level node of that name [I, §14 q.43]. In `save`, `ic` and `nodeset` it is mapped.

### 3.5 Expressions – `expr.parse(text, case, dialect="spectre", funcs=…, warn=…)` [M ref19 pp.474-477]

Spectre mode parses with `case="sensitive"`; SPICE mode with `case="lower"` and `dialect="spectre-spice"`
(below). Both dialects follow this table.

| item | Spectre | `expr.py` today (HSPICE) | v1 rule |
|---|---|---|---|
| precedence | highest first: unary `+ -`, `**`, `* /`, `+ -`, `<< >>`, `< <= > >=`, `== !=`, `&`, `~^ ^~`, `\|`, `&&`, `\|\|`, `?:` [M ref19 p.475]; `-2**2` = 4 | C's levels, `**` above unary minus: `-2**2` = −4 (`expr.py:311-313, 550`) [E50] | per-dialect binding powers, lowest first, scaled by 10 to leave room: `?:` 10 (right), `\|\|` 20, `&&` 30, `\|` 40, `~^ ^~` 50, `&` 60, `== !=` 70, `< <= > >=` 80, `<< >>` 90, `+ -` 100, `* /` 110, `**` 120 (right; its right operand may start with a unary sign), unary `+ - !` 130. VACASK's grammar matches the manual (`dflparser.y:216-228`): `-2**2` = 4, `2**3**2` = 512 [E51]; Xyce's −4 does not matter, the printers emit `pow()` |
| `**` | right associative | same | same |
| `^` | not an operator (bitwise XNOR is `~^` or `^~`) | power | ExprError (in SPICE mode too [I, §14 q.30]) |
| `x**y`, `pow(x,y)` | "x to the power of y, all x, all y" [M ref19 p.476]: C pow | HSPICE: `pow(x,y)` uses the integer part of y (`pow(2,1.5)` = 2); `x**y` guards x ≤ 0 (x < 0: the integer part of y; 0\*\*0 = 0) [E45, E50] | `Call('cpow', (x, y))`, never the HSPICE forms. The evaluator is `math.pow`: EvalError for x < 0 with a non-integer y, for x = 0 with y < 0, and on overflow. Printed `(y==0 ? 1 : pow(x,y))` on both engines, because Xyce's pow(0,0) is 1e50 and VACASK refuses it [E45, E51]; plain `pow(x,y)` when x or y is a nonzero constant, and `1` when y is a constant 0. A negative base with a non-integer exponent never reaches an engine (evaluation, below): Xyce returns the real part of the complex power silently, VACASK aborts the analysis with NaN [E51] |
| `& \| ~^ ^~ << >>` | bitwise, on integer operands | lexer errors; a `Binary` with these operators makes every message raise `KeyError` (`to_text`, `expr.py:573`) [E50] | `Binary` ops `& \| ~^ << >>` (`^~` parses to `~^`). Folded when both operands are integral and in [0, 2**53) and a `<<` result stays below 2**53; otherwise EvalError ("bitwise operator on a non-integer"). `~^` is always an EvalError: its value needs an integer width Spectre does not document [I, §14 q.44]. The evaluator folds them in every dialect (§4.2), so a constant one never reaches a printer; both printers raise PrintError for all five |
| `!` | not in the table [M ref19 p.475] | logical not | accepted (VACASK accepts it; NetlistParse.rs rejects it [E55]) |
| `log ln log10` | domain x > 0 | sign-keeping, S(x)·f(\|x\|) | EvalError for x ≤ 0 |
| `sqrt` | domain "x>0" | sign-keeping | EvalError for x < 0 (C's domain, not the table's literal x > 0 [I, §14 q.44]) |
| `exp` | domain x < 80 [M ref19 p.476; UG p.88] | C | EvalError for x ≥ 80 (Spectre's own behaviour there is §14 q.33) |
| `int(x)` | "integer part of x (number before the decimal)" [M UG p.89] | truncation | truncation |
| functions | a closed table [M ref19 pp.476-477]. One argument: `log ln log10 exp sqrt abs int floor ceil sgn sin cos tan asin acos atan sinh cosh tanh asinh acosh atanh`. Two: `min max pow fmod hypot atan2 sign`, with `sign(x,y)` = sgn(y)·\|x\| and `atan2(x,y)` = atan(x/y) | `FUNCS` is HSPICE's table: `hypot` and `fmod` are unknown; the HSPICE-only `db pwr nint trunc if limit dmin dmax`, the one-argument `sign` and `agauss gauss aunif unif` (their nominal argument) are accepted [E50] | `SPECTRE_FUNCS` with these arities; a call to any other name that is not a user function is an ExprError ("unknown function"). `hypot` and `fmod` are added (§4.2) |
| `M_*`, `P_*` constants | 22 names and values [M ref19 pp.466-467], equal to VACASK's `lib/context.cpp:13-35` (the old CODATA values included) [E] | names: `M_PI` prints as `vamos_M_PI` on VACASK (`expr.py:836-837`) and as `M_PI` on Xyce, both undefined; `M_DEGPERRAD` is missing from `VACASK_CONSTANTS` [E50] | a `Num` at parse time (`SPECTRE_CONSTANTS`). Constants may not name parameters [M ref19 p.495], so nothing shadows them. SPICE mode matches them case-insensitively [I, §14 q.44] |
| division | parameters are real-valued [M UG p.85] | real | real (`7/2` = 3.5; the printers already force real arithmetic on VACASK) |
| `?:` | right associative | same | same |
| `&&` `\|\|` | "no short circuiting" [M ref19 p.475] | lazy | the evaluator evaluates both operands, so an EvalError in either is an error; the printed forms give the same value for every value that reaches an engine |
| vectors | elements space separated "for clarity, though this is not mandatory"; anything but a constant, a parameter or a unary expression must be parenthesized [M ref19 p.477] | — | an element is a run of unary signs followed by a number, a name or a parenthesized expression, so `[0.5 1 +p2 (sqrt(p2*p2))]` has four elements and `[1+p2]` two; spectre.py splits them before `expr.parse` and keeps a list of Expr |

- **`temp` and `tnom`** are predefined netlist parameters [M UG p.93]. Both dialects map the identifier
  `temp` to `Name("temper")` and `tnom` to `Name("$tnom")`. `RESERVED` gains `"$tnom"`; HSPICE text can never
  produce a `$` name, so the HSPICE path is unchanged. `names()` therefore excludes both, `evaluate` and
  `fold` treat them as non-constant, and values that read them stay symbolic: changing temp or tnom by
  `set`, `alter` or `options` re-evaluates every expression that reads them [M UG p.93]. VACASK prints
  `$temp` and `$tnom` [E51]. Xyce has `temp` but no `tnom` (`ExpressionLexer.l:592-597`): the Xyce emitter
  replaces `Name("$tnom")` by `Num(<the deck's tnom>)` with `expr.substitute` before printing, its printer
  raises PrintError for an unsubstituted `$tnom`, and a sweep of `tnom` is an error on Xyce (an alter is
  replayed per deck, §7.2). `plan.build` evaluates expressions that read them after
  `expr.substitute(ast, {"temper": Num(temp), "$tnom": Num(tnom)})`. Today nothing maps them: `temp` parses
  to a plain parameter, and `evaluate` refuses `temper` even when the scope holds a value (`expr.py:641`)
  [E50].
- **The other reserved names.** Today's `_Parser.ident` maps every `RESERVED` name (`time temper hertz`) to
  its special variable in every case mode (`expr.py:333-337`), and the evaluator and printers read `temper`
  as the temperature (`$temp` on VACASK, `expr.py:973-974, 996-998`). In the Spectre dialects `ident` maps
  only `temp` and `tnom`: an identifier `time` or `hertz` is an ExprError (no behavioral expressions in v1),
  and `temper` is an ExprError in `spectre` ("the temperature is temp") and the temperature in
  `spectre-spice`, HSPICE's spelling there [I, §14 q.29]. A netlist that defines a parameter named `temp`,
  `tnom`, `temper`, `time` or `hertz` is an error in v1 (§14 q.29), so `parameters temper=5 r=temper*1k`
  can never read the temperature silently, and no user parameter reaches a name the evaluator or the
  printers reserve.
- **User functions** `real f(real a, real b) { return expr; }` [M UG p.92]: top level only; one `return`; a
  body may read only its own arguments (`names(body)` ⊆ the arguments, else an error); a function must be
  defined before its first call; a user function named like a built-in wins, with a warning [M UG p.92].
  They are inlined as the text is parsed: `parse(…, funcs=…)` maps each name (case as written) to (parameter
  names, body Expr). `expr.inline` is not used on Spectre text: it skips calls whose names match HSPICE's
  node-access pattern (`vt` among them, `expr.py:175, 535`) and keeps free names in a body; and today's
  parser lower-cases function names (`expr.py:387`) [E50].
- **No behavioral expressions in v1.** The Spectre dialects produce no node-access tokens: `V(…)` or `I(…)`
  in a parameter expression is an ExprError ("behavioral expressions are not supported in v1").
- **Evaluation** (`plan.build`, §5.1). Every expression of every reachable item (`tables.item_exprs`) is
  evaluated on every instance path with `evaluate(…, dialect="spectre")`, for each parameter state the plan
  sets: the sweep and alter values, with each analysis's temp and tnom put in by `expr.substitute`. The
  per-path environments come from `tables.path_envs(…, strict=True)` (§4.4, phase 0), the walker the Xyce
  emitter also uses (non-strict) to choose branches and bins per path, so the values plan.build checks and
  the ones the Xyce deck is built from are computed once, by one rule (today the rule lives only in the
  private `xyce._Deck.child_env`, `xyce.py:276-290`). An EvalError is an error naming the expression and the
  instance path. No out-of-domain value therefore reaches an engine: the printers keep the HSPICE forms of
  `sqrt` and the logs (identical in the domain), and every later evaluation of a Spectre expression with the
  default dialect (constant folding at print time, `expr.py:884`, which prints `sqrt(-4)` as `(-2.0)`
  [E50]; the emitters' and `tables`' evaluations) never sees a value plan.build refused. Those evaluations
  know `cpow`, `hypot`, `fmod` and the bitwise operators in every dialect (§4.2).
- **SPICE mode.** Expressions in `spectre-spice` text use `parse(text, case="lower",
  dialect="spectre-spice")`: SPICE-mode lexing (case folded; quote, brace and Cadence `par'…'` grouping, with
  or without a space, as cadence2xyce.pl:177-178 (6e81e73f) reads it; the SPICE number table of §3.3) with Spectre
  semantics (this table: `**` and `pow()` are `cpow`, the Spectre functions and constants, temp and tnom).
  Spectre's SPICE Reader converts SPICE input into the Spectre language [M UG pp.42, 49-52], so these
  expressions get Spectre's evaluator [I, §14 q.44 for the expression mapping]. spice.py passes its dialect
  to `expr.number` and `expr.parse` (`spice.py:1394, 1405`), and its own expression handling switches too
  (§4.3's switch points: the fast paths, the function check and the parse-time fold, which today turns
  `'sqrt(-4)'` into `Num(-2.0)` before any later stage sees it [E94]). Today it reads them with HSPICE's truncating
  `pow`: `.param a='pow(2, 1.5)'` is 2.0 and `r0='1k*pow(x, 1.5)'` at x = 0.5 is 500 (C pow: 353.6, −41 %)
  [E54]. The 48 CMC decks contain no `pow`, `**` or `^`, so that corpus would not catch it (§11 has the test).

### 3.6 Statements → IR

| statement | IR |
|---|---|
| `parameters a=1 b=a*2` | `Param` (top level, or merged into `Subckt.params`) |
| `name [(]n…[)]{(n…)} master [(]p=v…[)]` | `Instance`: kind per §3.8; kind `x` for a subckt, `y` for a Verilog-A module; node groups concatenated (§3.2) |
| `model name master p=v…` | `Model` (§3.9) |
| `model name master { 1: … 2: … }` | binned `Model`s with `bin_rule="spectre"` |
| `[inline] subckt name [(]ports[)]` … `ends [name]` | `Subckt` (`inline` flag); a nested definition stays in its parent's body (§3.7) |
| `if (c) {…} else if (c) {…} else {…}`, and the brace-less one-statement form [M UG p.109] | `Cond`. Its branches hold instances, nested `if`s and `paramtest`s only: "ordinary instance statements, if-statements, or a list of these within braces" [M ref19 p.491; UG p.109]; a model, parameter, subckt or any other statement inside an `if` is an error |
| `real f(…) {…}` | inlined by `expr.parse` (§3.5); not in the IR |
| `global g n…` | ground and `Netlist.globals` (§3.4) |
| `ic n=v …`, `nodeset n=v …` | `Netlist.ics` / `nodesets` (values may be expressions, §4.1), names checked by `signals.py` (§3.10); an `inst:term=` entry is an error |
| `save X[:p] … [depth= sigtype= devtype= subckt= exclude= probelvl= time_window= ports= filter= compression=]` | `Netlist.saves` (`SaveSpec`); `compression=` is noted; `ports=`, `filter=`: §6.2 |
| `name options p=v…` | at top level, or as a child of a sweep or montecarlo block [M UG p.47]: `Netlist.options`, global, merged in netlist order (§3.10). Inside a subckt body, directly or through an include: an error in v1 naming the subckt, because Spectre's multi-technology mode (on by default) scopes `temp`, `tnom`, `scale` and `scalem` given there to that subckt [M ref19 p.501]. Under `-mts` (MTS off) such options are global, with a note |
| `name set p=v…` | `Analysis(kind="set")`, positional |
| an analysis (§3.11) | `Analysis` in `Netlist.analyses`, in netlist order |
| `name sweep … {…}`, `name montecarlo … {…}` | `Analysis(kind=…, children=[…])` |
| `name alter dev=\|mod=\|sub= param=p value=v`, `name alter param=temp\|<netlist param> value=v` | `Analysis(kind="alter")` |
| `name altergroup {…}` | `Analysis(kind="altergroup")`: error in v1 |
| `name info …` | `Analysis(kind="info")`: note, the file is not written |
| `name paramtest [printif=\|warnif=\|errorif=]… [message=] [severity=]` | `ParamTest`, a component in the body (§4.1). `plan.build` evaluates it once per instance path (inside subckts and taken `if` branches) with that path's parameter values; a test that reads a parameter a sweep or alter changes is evaluated at every value the plan sets. Several tests act if any passes. `errorif` → error (exit status 2); `warnif` → warning; `printif` → note; with no test, `severity` `debug`/`status` → note, `warning` → warning, `error` → that analysis fails (exit status 1), `fatal` → exit status 2; `message` is the text [M UG pp.286-287] |
| `statistics {…}` | `Netlist.statistics` (`StatBlock`, §3.12) |
| `include`, `ahdl_include`, `library`, `section`, `simulator lang=` | file handling (§3.1) |
| `name check …`, `name checklimit …`, `name assert …` | note: not evaluated |
| `name shell …` | warning: the command is not run |
| anything else | error naming it |

### 3.7 Parameters, subcircuits and conditionals
- Subckt `parameters` (header or body) are defaults that each instance can override. An inner scope shadows
  an outer one; top-level parameters are visible everywhere [M UG pp.85-86, 93]. This is PARHIER=local, so
  `ParseOpts.parhier_local=True`.
- **Nested definitions inherit.** "any parameters accessible within the scope of s1 are also accessible from
  within s2" [M UG pp.85-86]. Xyce passes them [E65]; VACASK does not ("Variable or constant 'rr' not
  defined." [E65]), and today's emitter refuses such a definition (`vacask.py:310-319`). For
  `Netlist.dialect == "spectre"` the VACASK emitter lifts it instead (§4.5 item 11).
- Ordering, duplicates and evaluation follow `spice.py`: a topological sort per scope, the last definition
  winning with a note, and top-level values evaluated into `Netlist.values`. spectre.py applies them once,
  over the Spectre statements and the raw parameters every SPICE fragment returns (§4.3), so a SPICE `.param`
  may read a Spectre parameter and the reverse. Such a reference gets a warning naming it: the UG says that
  "statements in the SPICE sections that are dependent on Spectre statements might get improperly mapped.
  Except for model and subcircuit names, do not have any dependencies between these two language sections"
  [M UG pp.47-48], so vamos's reading (the natural one) may not be Spectre's. Today a fragment's own pass
  refuses it ("parameter p uses q, which is not defined at top level" [E60]).
- A top-level parameter that a sweep or alter targets, and every parameter that reads one, are never folded
  into what an engine prints as a constant (§4.1, `Netlist.values` in the render copy).
- Every subckt instance has an implicit `m` [M UG p.63]. The existing `$mfactor`/`vamos_mfactor` rules apply.
- An `inline subckt` is printed as a plain subckt with `Subckt.inline=True`. In outputs, its component named
  like the subckt takes the caller's name (§6.4) [M UG p.111].
- `Cond` holds a structural `if` [M UG pp.109-111]. Conditions are parameter expressions, evaluated per
  instance path. **Duplicate instance names** are allowed only as ref19 p.491 states them: in an `if` that has
  both an `if` part and an `else` part, each a single statement or a nested `if`, with the duplicates on the
  same number of terminals bound to the same nodes and referring to the same primitive or to models of the
  same primitive [M ref19 p.491; UG p.111]; any other duplicate is an error. Name-uniqueness checks (the Xyce
  emitter's case collisions, `xyce.py:387-393`) run on the resolved branch.
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
| `resistor` | `r` | `1 2` | `r`: the instance's, else the card's `r`, else `rsh·(l−2·etchl)/(w−2·etch)` with the instance's `l`/`w` or the card's, else ∞ [M ref5 p.627; the card defaults `w`=1e-6 m and `l`=∞ are on p.630]. spectre.py computes it per instance and strips `r rsh l w etch etchl` from the card, so that only temperature and noise parameters reach the card printer (§3.9); a geometry that is not constant on an instance is an error in v1. When it computes `r`, it records the instance parameters that fed it (`w`, `l`) in `Instance.folded` (§4.1): a sweep or alter of one of them, or of a stripped card parameter, would change nothing that is printed, and `plan.build` refuses it (§5.2). An infinite resistor is not printed (note), but it stays in the instance table: `probe=`, `oprobe=` and saves resolve to its node pair, and its terminal currents are 0. `r` = 0 (the resistance form below `thresh` [M ref5 p.630]) prints as a short (§4.5 item 10); a non-constant `r` that evaluates to 0 is an error. `m`; `isnoisy` (§4.5 item 7); `tc1 tc2 w l` → `sp_resistor`. The instance `scale` is never printed: Spectre applies it to `w` and `l` only, overriding the `scale` option [M ref5 p.629], and vamos applies it the same way (with `r` given it has no effect), while `sp_resistor` would multiply the resistance by it and Xyce rejects it [E23]. `tnom` and the other temperature data are card parameters [M ref5 p.630]. `c=` (wire RC), `coeffs=`, `trise` ≠ 0 and a third terminal → error |
| `capacitor` | `c` | `1 2` | `c`: the instance's, else the card's `c`, else `cj·Area_eff + cjsw·Perim_eff` from the card's `etch` and the instance's (or card's) `w`/`l`, or `area`/`perim` [M ref5 pp.282-283]; computed per instance by spectre.py, which strips `c w l etch cj cjsw` from the card and records the instance parameters that fed it (`w l area perim`) in `Instance.folded`, as for the resistor; else 0. `m`; `ic`; `coeffs=`, `trise` ≠ 0, `scalec` ≠ 1 → error |
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
| a Verilog-A module | `y` | the module's ports | instance parameters, matched case-insensitively against the module's declarations (§3.9) |
| a subckt | `x` | the ports | overrides, `m` |

- `X:term` accepts a terminal name from this table or the terminal's 1-based index [M ref19 p.518]. The
  terminal order is unchanged: the first terminal is the IR's first node (`p` of a source). How a saved item
  is named in outputs is §6.2.
- The emitters print every R/C/L value explicitly. VACASK's own defaults differ: resistor r = 1 Ω, capacitor
  c = 1e-12, inductor l = 1e-6 (`devices/*.va`) [E]. A card's `r`/`c`/`l` is folded into the instances
  (§3.9). A `vsource`/`isource` parameter outside the table below is an error.

#### 3.8.1 Independent sources → `ir.Source` (HSPICE field names, every field resolved) [M ref5 pp.380-383, 682-686]

`Source.spectre` keeps every parameter written on the instance, in Spectre names, whatever its `type` (§4.1):
numbers as Expr, vectors (`wave=`) as lists of Expr, enumerations and file names as `Str`. A SPICE-mode V or I
line fills it too, in Spectre names (§4.3). `spectre.resolve_source(params: Mapping[str, Value], type,
tran_stops, notes) -> Source` applies this table; alters use it again (§5.3).

| Spectre | IR | rule |
|---|---|---|
| `dc` | `Source.dc`; None when absent | Given: the value of every DC-type solve, which is `dc` (operating points and sweeps) and the operating point of `ac`, `noise` and `xf`; the waveform acts in `tran` only [M ref5 pp.383, 686]. Absent: the waveform's t = 0 value [M ref5 p.682], kept derived (None) so that it follows alters and sweeps of the waveform fields. The engines need different prints (§4.5 item 8): VACASK ignores `dc` unless `type="dc"` [E17], and Xyce's operating point for a waveform source printed without `DC` is 0, not its t = 0 value [E70], so Xyce gets the derived value written out. The `tran` initial point uses the t = 0 waveform value on both engines [E17; I for Spectre, §14 q.45]. |
| `type=dc` | no active wave (`Source.wave` None) | the waveform fields the instance gives stay in `Source.spectre`, for `alter … param=type` (§5.3) |
| `type=pulse val0 val1 delay rise fall width period` | `pulse v1 v2 td tr tf pw [per]` | `width` and `period` default to ∞ [M]: `pw` = 1e30 s (accepted by VACASK [E10] and by Xyce, whose default period does not cut it [E73]) and no `per`. `rise`/`fall` absent or 0: Spectre's `tran` default `transres` = 1e-9 × stop [M ref19 p.420], taken from the shortest `tran` (`ir.flat_analyses`, so a `tran` inside a sweep block counts; one edge serves every analysis), with a warning when a `tran` runs (Spectre's default edge is undocumented [M ref5 lists no default]; §14 q.20). The rule applies to every value that reaches the engine, not only to literals: a non-constant `rise`/`fall` prints as `(e==0 ? <transres> : e)`, and an alter or sweep value of 0 becomes transres. VACASK reads `fall` ≤ 0 as "no fall", so the pulse stays at val1, and stops the run on `rise` ≤ 0 [E25, E37]; Xyce makes either an ideal step [E25]. An explicit `period` ≤ rise+fall+width is an error (existing check). |
| `type=sine sinedc ampl freq sinephase damp delay` | `sin vo va freq td theta phase` | `sinedc` defaults to `dc`; `freq` defaults to 0, which gives a constant (folded into dc when `damp`=0, else an error); `ampl2 freq2 sinephase2`, `fmmod*`, `ammod*` → error |
| `type=exp val0 val1 td1 tau1 td2 tau2 delay` | `exp v1 v2 td1' tau1 td2' tau2` | `td1' = delay+td1`, `td2' = delay+td2` [I, §14 q.19]; a missing `tau1`/`tau2` is an error (Spectre's defaults are not given) |
| `type=pwl wave=[t v …] offset scale stretch delay`, or `file=` | `pwl` with `points` (t·stretch, v·scale+offset), `td=delay` | `file=` holds two columns without scale factors [M UG p.74] and is searched in the cwd, then the `-I` directories; `pwlperiod`/`twidth` → error; `allbrkpts` is ignored |
| `mag`, `phase` | `Source.ac` | |
| `xfmag` | — | ≠ 1 while an `xf` runs → error |
| `pacmag pacphase fundname` | — | silent: only `pac`/`pdisto`/`envlp` use them, and none runs |
| `noisefile noisevec` | — | error while a `noise` analysis runs; otherwise silent |
| `tc1 tc2 tnom` | — | ≠ 0 → error |
| `m` | multiplier | existing rules |

### 3.9 Models and model groups
- `model name master params` becomes `Model`, with `Model.prim` = the Spectre master (§4.1):
  - kind: `mos*`/`bsim*` with `type=n|p` → `nmos`/`pmos` (default n); `bjt` with `type=npn|pnp` (default
    npn); `jfet` with `type=n|p` (default n) [M ref5 pp.51, 188, 386, 410]; `diode` → `d`;
    resistor/capacitor/inductor cards → `r c l` (existing);
  - level, from the master: `mos1`→1, `mos2`→2, `mos3`→3, `bsim3v3`→49, `bsim4`→54, `diode`→1, `bjt`→1,
    `jfet`→1, so `tables.DISPATCH` picks the target module and Xyce level unchanged (§4.4). A Spectre diode
    `level` other than 1 is an error: Spectre's diode levels are 1 junction, 2 Fowler–Nordheim and 3 junction
    plus metal and poly capacitance [M ref5 p.303], not HSPICE's;
  - SPICE-mode cards get `Model.prim` too (§4.3), so every card of a Spectre netlist, from either language,
    takes the Spectre path below.
- **Cards are never rewritten to HSPICE's defaults.** Since revision 2 was written, `tables.model_params` (which both
  emitters call for every card, `vacask.py:374`, `xyce.py:519`) writes HSPICE's model defaults through
  `hspice_card` (`tables.py:377, 681-1063`), strips `STRIP_KEYS`, applies `_wire_card`'s HSPICE R/C rules,
  and both emitters add HSPICE warnings to M instances (`mos_junction_warnings`, `mos_scale_warnings`). A
  Spectre card would print as HSPICE simulates it: a diode `cjo` gets `fc=0.0 vj=0.8`, a level-49 card
  `xpart=1.0 cj=0.000579 cjsw=0.0`, a mos3 card `ld=0.75·xj`, `cgso`/`cgdo` and `eta·8.14/8.15`, a bjt `mjs=0.5`
  [E59]; Spectre's own are diode `vj=1 fc=0.5`, bsim3v3 `cj=5e-4 cjsw=5e-10 capmod=2 xpart=0`, mos3 `phi=0.7
  ld=0 cgso=0`, bjt `mjs=0` [M ref5 pp.54, 195-196, 305, 505-508], and `acm`/`calcacm` are bsim3v3
  parameters [M ref5 p.197]. So `model_params(…, dialect="spectre")` takes the Spectre path (§4.4): no
  `hspice_card`, no `STRIP_KEYS` strip, no HSPICE M-instance warnings; `SPECTRE_MASTERS` instead. The engine
  fixes stay (`_xyce_diode_charge`), and so do `_wire_card`'s engine renames, which see only temperature
  and noise keys once spectre.py has folded the geometry (§3.8).
- **`SPECTRE_MASTERS`** (phase-0 data, §4.4) lists, per master, every ref5 card parameter with one
  disposition: folded into instances, passed (with a rename where a target spells it differently),
  written as a Spectre default the card omits, stripped with a note, or an error. It lists every instance
  parameter the same way (`MasterRow.instance`, §10), which checks the Spectre parameters appended to a SPICE
  line (§4.3) and gives the renames of `dev=` targets (§5.2). It is built from ref5 against each target's
  parameter list (VACASK `devices/spice/*.va`, Xyce's device tables), together with one unit test per master
  in `test_spectre_masters.py`, built with the data in phase 0 (S0, §12); a parameter
  an engine does not know still fails loudly (VACASK at elaboration, Xyce through the output scan of every
  deck, §7.2). The **defaults** rows write a Spectre default that differs from a target's own when the card
  omits it, with one note per card (a warning where the row says so). The rows below are those found so
  far; the review of revision 3 found two that the earlier check had missed (`nsub`, `mjsw` [E95]), so the
  set is not called complete until phase 0 has compared every ref5 default of every master with both
  targets' sources, and its per-master test (`test_spectre_masters.py`) fails on a ref5 parameter with no
  disposition:

| master | written when the card omits it | Spectre [M ref5] | targets [E] |
|---|---|---|---|
| `mos1 mos2 mos3` | `tox=1e-7` | tox 1e-7 m (pp.411, 489, 506) | `sp_mos1` has no tox by default, so no oxide capacitance: no `kp` from `uo`·cox and no Meyer charge (`mos1.va:741-747`); `mos2`/`mos3` default 1e-7 |
| `mos1 mos2 mos3` | `nsub=1.13e16`; with it `phi=0.7`, `gamma=0` and `vto=0`, each where the card omits it, so that the targets derive nothing from the written `nsub`. The card's note names the applied default ("`nsub=1.13e16` applied, Spectre's documented default"), so a user comparing against Spectre sees it [M ref5 p.410/489/505; I, §14 q.32: whether Spectre's models use that default in the depletion-width and channel-length-modulation terms as the targets do] | nsub 1.13e16 cm⁻³ (pp.410, 489, 505); phi 0.7 V, gamma 0, vto 0 (pp.410, 488, 505). The documented phi is SPICE's derivation from that nsub, 2·Vt·ln(1.13e16/1.45e10) ≈ 0.70 V | nsub 0, read as "not given" (`mos3.va:149, 873-897`; Xyce `N_DEV_MOSFET3.C:309`), so no depletion-width coefficient: `mos3`, and `mos2` with its default `lambda=0`, get no channel-length modulation. A card without `nsub` gave 422.5 µA flat at vds = 1.5, 3 and 5 V on both engines; with `nsub=1.13e16 gamma=0`, 437.3/468.7/494.4 µA (mos3) and 511.1/533.4/567.4 µA (mos2) [E95]. phi 0.6 (`mos1.va:117`, `mos2.va:120`, `mos3.va:123`) |
| `mos1 mos2 mos3` | `mjsw=1/3` | mjsw 1/3 (pp.413, 492, 508) | 0.5 in `sp_mos1` and Xyce's MOSFET1 and MOSFET2, 0.33 in `sp_mos2`/`sp_mos3` and MOSFET3 (`mos1.va:132`, `mos2.va:135`, `mos3.va:137`; `N_DEV_MOSFET1.C:269`, `N_DEV_MOSFET2.C:272`, `N_DEV_MOSFET3.C:273`): at 1 V reverse bias with pb = 0.8, 2.25^-1/2 = 0.667 against 2.25^-1/3 = 0.763, 13 % less sidewall capacitance |
| `mos1` | — (`kp` follows from `tox` above: `uo`·cox = 2.0718e-5) | kp 2.0718e-5 (p.410) | `kp=2e-5` without tox (`mos1.va:115, 701-703`) |
| `mos3` | `badmos3=1`, with a warning per card (an approximation, §0) | its channel-length modulation is undocumented ("kappa=0.2 Saturation field factor", p.505; §14 q.31) | `badmos3=0` default: VACASK and Xyce 0.31-0.66 % apart in saturation, 0.003 % with `badmos3=1` [E69]. That is agreement between the engines, not with Spectre: with `vmax`=0 Xyce uses SPICE2's formula whatever BADMOS3 is (`N_DEV_MOSFET3.C:2204`, line510; BADMOS3 is read only when vmax > 0, :2222), so `badmos3=1` moves VACASK onto it (`mos3.va:1329-1335`) |
| `bsim3v3` | `capmod=2` | capmod 2 (p.195) | `sp_bsim3v3` capmod 3 (`bsim3v3.va:140`) |
| `bjt` | `fc=0.5` | fc 0.5 (p.54) | `sp_bjt` fc 0 (`bjt.va:145`) |
| `diode` | `eg=1.124481` | eg at 27 °C (p.306) | 1.11 in `sp_diode` and Xyce: 4.8 mV apart at 1 mA and 127 °C [E34]. Spectre's default is temperature dependent, so a run that simulates such a card at a temperature other than its `tnom` gets a warning naming it (§14 q.27) |
| `diode` | `fcs=<fc>`, when the card gives `fc` but not `fcs` | fcs = fc (p.305) | `fcs=0.5` (`diode.va:168`); both targets compute the sidewall charge from FCS since VACASK 3becb73d and xyce 7cd78110 |
| `resistor` | `af=2`, when the card gives `kf` | af 2, and flicker exponents `wdexp ldexp weexp leexp fexp` (p.631) | `sp_resistor` af 1 (`resistor.va:115`); a card with `kf` that relies on the geometry exponents is phase-0's to classify |

  Further dispositions of the same table: `mos1`/`mos2`/`mos3` `capmod`: absent or `bsim` (Spectre's
  default [M ref5 pp.411, 490, 507]) → a warning when an `ac`, `noise`, `xf` or `tran` runs, because both targets
  have only Meyer's charge (§0); `meyer` → silent [I, §14 q.45]; `none`, `yang` → error. A
  `mos1`/`mos2`/`mos3` card that gives `nsub` but not `gamma`, `phi` or `vto` → a warning naming what the
  targets derive as SPICE3 does (`mos1.va:748-767`; Spectre lists `gamma=0` as the default [M ref5 p.410], and
  its derivation is undocumented, §14 q.32). Equal defaults are not written: diode `vj=1 fc=0.5 mjsw=0.33`,
  bsim3v3 `xpart=0 cj=5e-4 cjsw=5e-10`, mos `fc=0.5` (`diode.va:129, 167, 139`, `bsim3v3.va:204, 231, 234`,
  `mos1.va:138`). The diode's `hcomp` (1 selects HSPICE's junction equations) and `dcap` [M ref5 p.303] are
  Spectre parameters, never HSPICE's DCAP (which `_junctions` would read them as): `hcomp` ≠ 0 → error in v1.
  A row whose message depends on the run (the `capmod` warning when an `ac`, `noise`, `xf` or `tran` runs;
  the diode `eg` warning at a temperature other than the card's `tnom`) is decided by `plan.build`, which
  knows the analyses and the temperatures of every step; `model_params` only prints the card and its
  per-card notes and warnings. §10's `ParamRule` encodes each row: a value condition (`match`) for the
  `capmod` and `hcomp` rows, a run condition (`warn`) for these two.
- **Default MOS geometry.** A MOS instance without `w`/`l` takes the card's `w`/`l`, else the master's default
  (mos1, mos2, mos3: 3e-6; bsim3v3, bsim4: 5e-6 [M ref5, "Default channel width/length"]). The value is
  written on the instance with a note, and `w`/`l` are removed from the card (rule action `fold`), so a
  `mod=` sweep or alter of the card's `w` or `l` is refused (§5.2); the instance's own `w`/`l` stay
  sweepable. A model-group instance without
  `w`/`l` uses the master's default; a group whose cards give `w`/`l` for such an instance is an error in v1.
  Model-group selection uses the filled values. The engines' own default (1e-4, `$simparam("defl")` in
  VACASK's `sp_mos1`) is never reached: there, `w=30u` alone gave 25.35 µA where `l=3u` gives 845 µA [E42].
- **Verilog-A masters.** A model whose master is a Verilog-A module gives `y` instances with the card's
  parameters merged in (the `spice.py` rule). Parameter names are matched against the module's declarations,
  as `va_modules`' preprocessor collects them (§3.1; a module whose list is unknown passes its names
  unchecked, with a note), case-insensitively (two declared names that differ only in case are an error),
  and printed lower-cased on VACASK: openvaf-r's OSDI names are lower case and VACASK matches exactly
  ("Parameter 'R' not found." for a module declaring `R` [E64]); Xyce is case-insensitive [E64]. On Xyce a
  module's card parameters are refused (§0, §4.5 item 13).
- **Model groups:** `model base master { k: params … }` gives Models `base.k` (with `base`, `bin_index` and
  `bin_rule="spectre"`). Selection is `lmin <= l < lmax and wmin <= w < wmax`, the first matching entry in
  group order winning, and Spectre names the chosen card `base.k` [M UG p.108]. Bounds are exact (no 1e-15
  tolerance); a bound a card omits takes the master's default (`lmin=wmin=0`, `lmax=wmax=1` m [M ref5 p.200],
  where `tables.bin_bounds` raises today, `tables.py:1321-1323`). Group order is kept: `tables.Scope` sorts
  HSPICE bins by name in Xyce's order (`nch.1, nch.10, nch.2`, `tables.py:1554-1558`), which would reorder
  a Spectre group. `w` is the instance's total `w`, also with `nf` > 1 (§14 q.8).

### 3.10 Control statements
- `options` (global, read with the circuit [M ref19 p.187]; inside a subckt, §3.6). Keys are matched
  case-insensitively (the UG itself writes `o1 options TEMP=55` [M UG p.93; I as a general rule, §14 q.43]) and stored
  lower-cased as Spectre spells them (`vabstol`, `iabstol`, …) in `Netlist.options`; the written spelling
  goes to `Netlist.spelling`. The emitters do not call `tables.solver_options` for `Netlist.dialect ==
  "spectre"`, whose tolerance notes are HSPICE's (`tables.py:1440-1465`); the plan's `options` actions carry
  these dispositions (§4.5 item 5). SPICE-mode `.option` keys are mapped to these first (§4.3). Keys:

| key | disposition |
|---|---|
| `reltol`, `vabstol`, `iabstol`, `chargeabstol` | VACASK `reltol`, `vntol`, `abstol`, `chgtol` (the same SPICE-style meaning); Xyce: note |
| `temp`, `tnom` | `netlist/spectre.py` always sets `Netlist.temp` and `Netlist.tnom`: 27.0 unless an `options` statement sets them [M ref19 p.187]. The emitters' fallback for None (25 °C, the HSPICE default) is never reached |
| `gmin` | mapped on both engines |
| `scale` | the IR's `scale` (HSPICE rule); ≠ 1 → warning [I, §14 q.43] |
| `scalem` | ≠ 1 → error |
| `rawfmt`, `rawfile`, `precision` | output settings (§2.2, §2.5, §8.3) |
| `save`, `nestlvl`, `currents`, `subcktprobelvl`, `pwr`, `useprobes`, `useterms`, `saveahdlvars`, `subcktiprobes`, `savefilter`, `saveports`, `wfdebug` | §6.1 |
| `maxwarns`, `maxnotes` | message limits |
| `tempeffects` | `all` silent; any other value → error |
| `rabsshort rcut ccut cgnd cmax cclamp lshort kcut dcut qcut` (element reduction) | the default is silent; any other value → warning: not applied |
| `homotopy newton limit maxdeltav gmin_start gmin_converge try_fast_op dcmaxiters pivrel pivabs rforce residualtol digits cols checklimitdest sensfile psfversion topcheck compatible approx icversion` | note |
| `multithread` | ignored |
| anything else | warning |

- `set`: positional options (§5.1).
- `ic` / `nodeset`: node or hierarchical-node assignments; values are expressions (§4.1), evaluated by
  `plan.build` with `Netlist.values`, and one that reads a swept or altered parameter is an error in v1.
  `signals.py` resolves their names, and the names in `readic`/`readns` files, against the flattened IR
  like save items; an unmatched name is a warning naming it. VACASK drops it silently, and Xyce only warns
  and ignores it, also for a hierarchical name it spells differently (`.ic v(I2:MID)` with the node named
  `XI2:MID`, rc 0 [E84]), so this check is the only guard (§4.5 item 15). Wildcards → error (v1).
- `alter`: the targets of §5.3; `annotate=` is ignored.

### 3.11 Analyses: parameter dispositions [M ref19 pp.43-47, 64-69, 181-186, 406-410, 416-427, 438-441]

| analysis | parameters |
|---|---|
| `dc` | `start stop center span step lin dec log values valuesfile` and `dev mod sub param` (§5.2); no sweep target → operating point; `readns` (mapped), `write`/`writefinal` (mapped), `useprevic` (VACASK mapped, Xyce warning), `save`/`nestlvl` (§6), `print=yes` (mapped: the values go to the log), `oppoint`≠no (note), `force`/`readforce`≠none (error), `restart` and `swpuseprevic` (mapped to VACASK's sweep `continuation`, §5.2), `homotopy newton maxsteps swp1stpointic` (note), `maxiters` (VACASK `op_itl`), `hysteresis=yes` (error), `check annotate title emir*` (ignored) |
| `ac` | as `dc`, plus `freq` (the fixed frequency of a parameter sweep) and `prevoppoint=yes` (VACASK mapped, Xyce warning); `skipdc=yes` and `perturbation`≠linear (with `out1 out2 contriblist rf* flin_out fim_out maxharm_nonlin`) → error |
| `noise` | as `ac`. Output: `p n`, or `oprobe=`: a two-terminal component → its terminal pair (an infinite resistor included, §3.8); a controlled source with `oportv=1` or absent → its output port (p, n); any other port number → error; a current output through a vsource or iprobe → error. Input: `iprobe=` a vsource or isource (a `port` → error), `iportv` 1 or absent. It is optional [M ref19 p.181]: without it §5.6 adds an input, and `in`/`gain` are not written. `separatenoise` (note) |
| `xf` | as `ac`; output `p n` or `probe=` (two-terminal, an infinite resistor included; a current probe → error); `stimuli=sources` (`nodes_and_terminals` → error); `--vamos-analog=xyce` → error |
| `tran` | `stop` (required); `start`≠0 → error (§14 q.24); `outputstart` (mapped); `step` (VACASK `step`); `maxstep` (§5.4); `minstep` (note); `istep pstep` (ignored); `tpoints readtime` (error); `ic`, `skipdc` (§5.4); `readic` (and `read`, its 5.1 name [M UG p.204]) `readns write writefinal` (mapped); `useprevic` (VACASK mapped, Xyce warning); `linearic=yes` and `rampup*` (error); `oscfreq` (ignored); `cmin`≠0 (warning); `method errpreset relref lteratio` (§5.4); `maxstepratio reltolratio` (custom errpreset [M ref19 p.427]: error); `maxiters` (VACASK `tran_itl`); `transres` (feeds the pulse edge policy); `restart` (§5.2); `skipstart skipstop skipcount` (mapped: output filtering); `strobe*` (warning); `infonames infotimes infotime_pair acnames actimes actime_pair` (warning: not run); `ckptperiod saveperiod saveclock savetime savefile recover` (note); `circuitage`≠0 (error); `noisefmax`>0 (error); `param paramset param_vec param_file sub` (error); `annotate* title progress_* compression comp* complvl flush* fastbreak d2a* fastcross lteminstep ltethstep vref* iref* emir* autostop dcmaxiters` (ignored or noted) |
| `sweep` | the sweep parameters of `dc` plus `sub`; `paramset` and `faults*` (error); `distribute numprocesses savedatainseparatedir annotate title` (ignored) |
| `montecarlo` | error in v1 |

Analysis parameters are expressions [M ref19 p.474] (`stop=(p1+p2)*50e-6`), stored as §4.1 says and
evaluated by `plan.build`; one that reads a swept or altered parameter is an error in v1.

### 3.12 sweep, montecarlo and statistics blocks
- `name sweep params {` must end its line with the opening brace [M ref19 p.406]. Its children are analyses,
  nested sweeps, `options` statements (global options [M UG p.47]), or `alter`/`set`/`info` statements (§5.3
  restricts them). The block closes with `}`.
- `name montecarlo params { … }` holds children and `export` lines. It is parsed in v1 and is an error at
  plan time (§12 phase 3 maps it to VACASK's `mc` loop).
- `statistics { process { vary p dist=gauss|unif|lnorm std= [N=] [percent=yes] } mismatch { … } correlate
  param=[…] cc=… ; correlate dev=[…] param=[…] cc=… ; truncate tr=… }` becomes `Netlist.statistics`: one
  `StatBlock` per `process`, `mismatch` and block-level (`statistics`) part, with `Vary` and `Correlate`
  entries (§4.1) [M ref19 pp.175-179; UG]. `std`, `N`, `cc` and `tr` may be expressions; `truncate` appears
  at all three levels (default 4 σ); `correlate dev=[…]` accepts `*` patterns and an optional parameter list
  [M ref19 pp.176-179]. A netlist without a `montecarlo` analysis simulates at nominal values, as Spectre
  does, so PDK libraries that carry statistics blocks parse and run. The XDM fixture
  `OTHER_PARSING/statistics_line.cir.spectre` is a parse test (§11 T3a).

### 3.13 SPICE-mode text
- SPICE sections and SPICE-mode files go through `spice.parse_fragment` with dialect `spectre-spice` and a
  resolver (§4.3). Elements, models, subckts and `.param` follow `spice.py`'s rules with the differences
  §4.3 lists: lower-cased names and spellings, the SPICE number table of §3.3, Spectre expression semantics
  (§3.5), node names as strings and the §3.4 ground rule, Spectre masters on every card (§3.9), Spectre
  instance parameters appended to a line, and SPICE sources filled in Spectre names. Names defined outside
  the fragment, in either language and in either order, are resolved through the resolver, after every file
  has been declared (§4.3).
- Every dot statement has one row in §4.3's table: spice.py reads the definitions, spectre.py the file
  statements and the controls (Spectre maps SPICE statements to Spectre ones [M UG p.54]). The analyses are
  `.op` → `opBegin`, `.dc` → `srcSweep`, `.ac` → `frequencySweep`, `.tran` → `timeSweep` [M UG p.232]; HSPICE's
  `.tran` interval syntax, RMAX/DELMAX, 25 °C and DEFL/DEFW never apply [E46, E58].
- Spectre parameters appended to a SPICE line [M UG p.54] are Spectre instance parameters of the element's
  master (§4.3); an unknown one is an error. Today spice.py keeps them without a check and both emitters print
  them (`isnoisy={no}` on Xyce) [E60].
- A comment whose text starts with `spectre:` (after the `*` and optional blanks) carries Spectre text hidden
  from SPICE, and `*spectre: + …` continues the previous SPICE statement [M UG pp.54-55]. v1 rejects it with an
  error naming the line, so the element or parameter it carries is never lost silently: spice.py drops every
  `*` line today (`spice.py:427`) [E58]. (v2: read it as a Spectre statement.)
- `.hdl "f.va"` is `ahdl_include`, with a note [I, §14 q.28: the manuals on the box never mention `.hdl`].
- **The CMC benchmark decks** (`xyce/utils/ADMS/examples/**/*.sp`) are HSPICE decks with HSPICE G-2012.06
  listings at 25 °C, not Cadence-dialect ones [E91]: their `par'…'`, `.probe name=expr`, `deriv()` and
  `.option post ingold` are HSPICE output syntax. Spectre's SPICE Reader is SPICE2G/SPICE3e plus "common
  extensions", and the full reader is off unless `spectrespp`/`+spp` is used [M UG pp.41-42, 51]; whether it
  takes these decks is §14 q.28. They are spectre-spice parse fixtures (§11 T3c). They are not vcs-ams gold:
  vcs-ams cannot run them, since SPICE-top designs are not in AMS v1 (`VAMOS_GUIDE.md` §10).

## 4. IR and package extensions

Every change is additive: defaults keep today's behaviour, and today's positional call sites bind as before
(§4.1). **The merge gate is the whole `tests/vamos` suite, unchanged, on both legs** (Cygwin Python 3.9.16 and
WSL Python 3.14.4) and on the round-6 engine builds that `test_r6_N.py` needs (§0 [E] row). Besides
`test_netlist_*` and `test_ams_*`, the round-6 and repair modules `test_r6_C`, `test_r6_N`, `test_r6_R`,
`test_repair_netlist`, `test_repair_units` and `test_vamos_run` import `vamos.netlist`. "Unchanged" is
literal: no existing test file changes. spectre's tests are new files (§11), and §10's optable extension keeps
`VamosOpt.where` a string with a new defaulted `also` field, because a tuple `where` makes
`test_help_texts_list_every_option_and_point_at_the_guide`'s `o.where != "simv"` always true, and the test
then fails on a spectre-only option left out of vcs's help (`--vamos-psf-names`) [E94]. Because phase 0 moves
code out of `backends/nvc.py` and `backends/cosim.py`, which every simv run uses, the verifier phase also runs
the ivtest/hazard3 regression gate (§12).

Shared fixes that also reach the HSPICE path, each deliberate and each subject to the gate. Each also gets an
HSPICE-route regression test that fails without it (vcs-ams's rule, `VAMOS_AMS_DESIGN.md` §10 phase 5),
written by the agent that changes the file:
- the Verilog-A module scan follows `` `include`` (§3.1), where today's HSPICE route errors [E61]: S1, through
  `spice.parse` (hspice dialect) on a two-file Verilog-A tree, `Netlist.va_modules` filled;
- the Xyce emitter prints an `xyceModelGroup` module as its letter device (§4.5 item 13), where today's Y form
  fails outright [E64]: S3, on a vcs-ams-style Xyce deck with such a module. The guard against binding a
  colliding built-in model goes with it: `xyce.smoke`'s `-norun` step, which vcs-ams's compile-time check runs,
  applies `device_summary_problems` (§4.5 item 13, §10), so the shared route never reaches vcs-ams unguarded;
- `rawfile.scan`/`fix_points` stream (§4.6): S0, through cosim's re-exports (`scan_raw`, `_fix_points`) and
  the existing rawfile tests;
- `Netlist.left_out` becomes a real field and `tables.scale_source` keeps new `Source` fields (§4.1): S0,
  `left_out` surviving `dataclasses.replace` and `Source.spectre` surviving `scale_source`;
- `VACASK_CONSTANTS` gains `M_DEGPERRAD` (§4.2): S0, an HSPICE `.param M_DEGPERRAD=2` read with
  `case="sensitive"` printed as `vamos_M_DEGPERRAD` on VACASK (`param_ident`, `expr.py:837`);
- optable's `also` contexts (§10); `proc.py`, `engines.choose_engine`, `engines.tool_rows` and
  `engines.env_for(nvc_libdir=None)` move or widen code without changing what their callers get (§10): S0,
  guarded by the existing tests (the help and effects tests, `test_vamos_run.py`'s `TestStreamSignals`) and a
  vcs-ams provenance header that stays byte-identical.

### 4.1 `ir.py` (frozen; additive)

The exact phase-0 block is in §10. **Rule: each new field is appended after its class's current last field
and has a default; the new classes are defined above `Item`.** Revision 2's field list, transcribed onto
today's classes, failed to define on both Pythons ("non-default argument 'nodes' follows default argument
'sweep'" for `Analysis`, "mutable default <class 'dict'> for field spectre is not allowed" for `Source`,
"non-default argument 'saves' follows default argument 'notes'" for `Netlist`) [E49]. Appended, every
positional call site binds as before: `Subckt` with 8 arguments (`spice.py:3010`; round 6 appended
`Subckt.spelling` after `origin`, `ir.py:104-106`), `Model` with 5 (`spice.py:1544`), `Param` 3, `Analysis` 3,
and 75 `Instance` calls with 3 [E49]. `test_spectre_contract.py` pins the `dataclasses.fields` order on both
Pythons. What the additions mean:

1. **`Analysis`** gains the kinds `noise xf sweep montecarlo alter altergroup info set`; `op dc ac tran` now
   carry full Spectre arguments. Appended after `origin`: `name` (the analysis instance name; SPICE mode
   `opBegin` …), `sweep: Optional[SweepSpec]` (the analysis's own swept variable), `nodes` (the output node
   pair of noise or xf), `children` (the body of a sweep or montecarlo, in order) and `spice: bool` (written
   in SPICE syntax; it decides the output naming). For Spectre kinds, `args` holds numbers as Expr (folded to
   `Num` when constant), enumerations and file names as `Str`, and vectors as lists of Expr [M ref19 p.474];
   the keys are §3.11's normalized names, documented in the module docstring. `Netlist.tran()` returns the
   first top-level `tran` only and misses one inside a sweep block [E56]; Spectre code uses `flat_analyses`.
2. **`SweepSpec`** (new): `target` (`dev mod sub param freq`; a temperature sweep is `param` with `param`
   `temp`), `name`, `param`, `mode` (`lin dec log step values`), `start`, `stop`, `step` (each
   `Optional[Expr]`), `count: Optional[int]`, `values: List[Expr]`, `origin`. Names are IR names, resolved by
   the parser (§3.1). The parser keeps the sweep as written (`center`/`span` become start/stop, a `valuesfile`
   is read into `values`); `plan.build` evaluates it and expands every grid that is not engine-native (§5.2).
3. **`SaveSpec`** (new): `items` (raw tokens, as written), `depth`, `sigtype`, `devtype`, `subckt`, `exclude`,
   `probelvl`, `time_window` (a vector of interval pairs, not one interval), `ports`, `filter`, `origin`
   [M ref19 p.517]; `compression=` is noted, not kept. `Netlist.saves: List[SaveSpec]`. HSPICE probes stay in
   `Netlist.probes`.
4. **`Cond`** (new): `branches: List[Tuple[Expr, List[BranchItem]]]`, `default: List[BranchItem]`, `origin`.
   `BranchItem = Union[Instance, Cond, ParamTest]`; an else-if chain nests a `Cond` in `default`. A model,
   parameter or subckt cannot be in a branch (§3.6), so `tables.Scope` keys every card and subckt as it does
   today: a card defined in two branches would otherwise make the last one win for every branch
   (`tables.py:1545-1553`). **`ParamTest`** (new): `name`, `tests: List[Tuple[str, Expr]]`
   (`printif`/`warnif`/`errorif`), `message`, `severity`, `origin`; the emitters print nothing for it.
   `Item` gains both. `ITEM_TYPES = (Instance, Model, Param, Subckt, Cond, ParamTest)` is the tuple a walk
   checks before it raises on anything else. **`flat_items(items)`** yields each item in order; for a `Cond`
   it yields the `Cond` itself, then the items of each branch and of the default, recursively; it never
   enters a `Subckt` body (walks open a `Scope` per subckt, as today). **`flat_analyses(analyses)`** also
   yields the children of nested sweeps and Monte Carlo blocks. `Netlist.instances()` and `models()` are
   documented as not descending into `Cond`. A prototype over the UG p.110 structure gives `Cond,
   Instance(npn10x10), Cond, Instance(npn20x20), Instance(npn_default), Model, ParamTest` [E56].
5. `Subckt.inline: bool = False`, after `spelling`.
6. `Model.bin_rule: Optional[str] = None`. `"spectre"` means exact bounds, group order and total `w`.
7. `Instance.prim: str = ""`: the Spectre primitive master, used for output terminal names, to mark iprobes,
   and by allpub and xf (§6.1, §6.4). It is set on every instance of a Spectre netlist from either language:
   `spice.parse_fragment` sets it on every SPICE element (V → `vsource`, I → `isource`, R → `resistor`, C →
   `capacitor`, L → `inductor`, E/G/H/F → `vcvs`/`vccs`/`ccvs`/`cccs`, K → `mutual_inductor`, D/Q/J/M → the
   card's `Model.prim`, a Verilog-A instance → its module), so SPICE-mode source currents and xf traces are
   not lost.
8. **`Netlist.statistics: List[StatBlock]`** (parse-only in v1). `StatBlock(kind, varies, correlates,
   truncate, origin)`, `kind` one of `process`, `mismatch` or `statistics` (the block's own level);
   `Vary(param, dist="gauss", std, n, percent=False, origin)`; `Correlate(params, devs, cc, origin)`, `devs`
   possibly `*` patterns; `std`, `n`, `cc` and `truncate` are Expr [M ref19 pp.176-179].
9. **`Netlist.dialect: str = "hspice"`.** `spice.parse` leaves it for both of its dialects; only
   `spectre.parse` sets `"spectre"`. The emitters branch on it (§4.4, §4.5).
10. `ParseOpts.dialect` documents `"spectre-spice"`, which only `spice.parse_fragment` accepts; `spice.parse`
    keeps refusing it (`spice.py:3023-3024`). Nothing branches on `"spice"` today (`ams/config.py:25`), so
    `spectre-spice` is the first dialect with behaviour. vcs-ams's `-nspice` does set `"spice"`
    (`ams/initfile.py:670`, `ams/flow.py:253`), which is not an `expr` dialect: spice.py maps it to `hspice`
    before every call into `expr` (§4.2), and `expr` refuses any name outside `expr.DIALECTS`.
11. **`Source.spectre: Dict[str, Value]`**, `field(default_factory=dict)`: every parameter written on a
    vsource/isource, in Spectre names, whatever its `type` (§3.8.1). `Value = Union[Expr, List[Expr]]`;
    enumerations and file names are `Str`.
12. `Model.prim: str = ""`: the Spectre master, set on every card of a Spectre netlist from either language
    (§3.9, §4.3); `plan.py` applies its `SPECTRE_MASTERS` renames to `mod=` sweep and alter parameters (§5.2).
13. **`Netlist.left_out: Dict[str, Note]`**, a real field. Today the subckts left out live in a dynamic
    attribute, `nl._vamos_left_out` (`spice.py:3037`), read back by `spice.left_out` (`spice.py:341-343`),
    which `dataclasses.replace` loses (`VAMOS_AMS_DESIGN.md` §4.2). spice.py stores and reads the field;
    `spice.left_out(nl)` is unchanged for its callers.
14. `Netlist.ics` and `nodesets` are annotated `Dict[str, Union[float, Expr]]` (widened): Spectre values are
    expressions [M UG p.86], and freezing them at parse time would freeze a swept parameter at its nominal
    value. The HSPICE path still stores floats; the emitters already evaluate an Expr there
    (`vacask._number`, `vacask.py:820-829`).
15. `Instance.folded: List[str]`, `field(default_factory=list)`: the instance parameters that fed a value
    spectre.py computed at parse time (a resistor's `w`/`l`, a capacitor's `w l area perim`, §3.8). `plan.build`
    refuses a sweep or alter of them (§5.2), as it refuses one of a card parameter whose rule folds or strips it.
16. **`Netlist.va_modules: Dict[str, VaModule]`**, `field(default_factory=dict)`: every Verilog-A module of
    `Netlist.hdl`, by lower-cased name, as `spice.va_modules` reads it (§3.1): the declared parameters and the
    header attributes `xyceModelGroup`, `xyceLevelNumber`, `xyceTypeVariable` and `xycePTypeValue`. The Xyce
    emitter needs them at render time (the letter device or the Y form, the printed name that `names_for` and
    `printed_path` produce, §4.5 item 13), and so do phase 2's Device Count Summary check and VACASK's
    lower-casing (§4.5 item 12); the IR otherwise carries only the `.va` paths (`ir.py:132`), and re-scanning
    at render time would need the `-I` and `CDS_VLOGA_INCLUDE` lists. `spectre.parse` and `spice.parse` both
    fill it (the scan is a shared fix, §4). `VaModule` is new in ir.py (§10): today spice.py has no such class,
    only `_Parser.va_modules: Dict[str, str]` (`spice.py:781`) [E94]; `spice.va_modules` fills
    `Netlist.va_modules` with it.

**IR copies** are made with `copy.deepcopy`, never `dataclasses.replace`. `tables.scale_source` rebuilds a
`Source` field by field today (`tables.py:1426-1435`), which would drop `Source.spectre`: it uses
`dataclasses.replace`.

**`Netlist.values` in the render copy.** The emitters fold `Netlist.values` (every evaluated top-level
parameter) into what they print: VACASK into subckt defaults (`vacask.py:348`), bin guards and conditions;
both use it to decide bins and branches per path (`xyce.py:181, 223, 280`, `tables.constant`,
`bin_bounds`). A top-level parameter that reads a swept one is in `Netlist.values` too, so excluding only the
swept parameter freezes its dependents: with `.param rval=1k rdep='rval*2'`, VACASK prints `rdep=rval*2.0` at
the top but `parameters r=2000.0` inside the subckt, where Xyce keeps `r={rdep}` [E53]. So `plan.build`
records in `RunPlan.dependents` every plan variable and, transitively, every top-level `Param` that reads one
(`expr.names` over the top-level `Param`s of `Netlist.body`), and `signals.resolve`, which makes the render
copy, only reads it. In the **VACASK** copy it removes them from `Netlist.values`, so the emitter's folding
code needs no change and a subckt default, bin guard or condition that reads one stays symbolic (VACASK
evaluates `@if` at each point [E39]). The **Xyce** copy keeps the nominal values: the Xyce emitter prints
parameters symbolically and uses the values only to choose a branch or a bin per path, which `plan.build`
has shown no planned value changes (§3.7). Both compute the per-path values with the one walker,
`tables.path_envs` (§3.5, §4.4), so the paths the plan checked are the paths the deck prints.

**Options.** A Spectre netlist stores option keys lower-cased as Spectre spells them (`vabstol`, `iabstol`,
…) in `Netlist.options`, which is shared with the HSPICE path; for `Netlist.dialect == "spectre"` the
emitters do not call `tables.solver_options` (§3.10).

**No IR change is needed for:**
- user functions: inlined by `expr.parse(…, funcs=…)` (§3.5);
- `ahdl_include`, beyond `Netlist.va_modules` (item 16): `Netlist.hdl` and `y` instances;
- `global`: `Netlist.globals`, with the ground folded to `0` (§3.4);
- `inline` naming and terminal names: the output layer reads `Subckt.inline` and `Instance.prim`;
- a derived source `dc`: `Source.dc` is None (§3.8.1);
- case-insensitive definitions: the parser resolves every reference to an IR name (§3.1).

The IR objects are mutable and unhashable (`eq=True`), so walks key them by `id()`; the `expr_ast` classes
are frozen and hashable; the layouts are identical on 3.9.16 and 3.14.4; and no IR object already has an
attribute named `prim`, `inline`, `bin_rule`, `spectre`, `dialect`, `saves`, `statistics`, `children` or
`sweep` [E49], nor `folded` or `va_modules` (`spice._Parser.va_modules`, a parser attribute, is not on the
IR) [E94].

### 4.2 `expr_ast.py` / `expr.py`
Today's signatures take a defaulted `dialect` keyword without breaking a caller, and the tests pin the
HSPICE behaviour (`test_netlist_expr.py:81` `a^b == a**b`, `:94` `-2**2 = -4`, `:199` `0**0 = 0`), so the
Spectre dialects are opt-in [E49]. Every function that takes a `dialect` raises `ValueError` for a name
outside `DIALECTS = ("hspice", "spectre", "spectre-spice")`; spice.py maps its own `ParseOpts.dialect` to an
`expr` dialect first (`hspice` and vcs-ams's `spice` → `hspice`, `spectre-spice` → `spectre-spice`, §4.1 item
10), so `-nspice` compiles keep working and a misspelt dialect fails at once.
- **`number(s, dialect="hspice", warn=None)`**: §3.3. `warn(severity, message)`; `dialect="hspice"` is
  today's reader, `D` exponent included.
- **`parse(text, case="lower", dialect="hspice", funcs=None, warn=None)`**: dialects `hspice` (today's, byte
  for byte), `spectre` and `spectre-spice` (§3.5). The binding powers become per-dialect attributes of
  `_Parser` (today the module constants `_LBP`, `_UNARY_RBP` and `_TPREC`, `expr.py:311, 313, 550`, whose
  levels leave no room between `&&` and `==`). In the Spectre dialects: constants become `Num`; `temp` and
  `tnom` become `Name("temper")` and `Name("$tnom")`, and `ident` maps no other `RESERVED` name (`time`,
  `hertz`, and in `spectre` `temper`, are ExprErrors, §3.5); `**` and `pow` become `Call("cpow")`; `^~` parses to
  `~^`; a call must name a user function (`funcs`, inlined as it is parsed) or a `SPECTRE_FUNCS` entry with
  its arity; there are no node-access tokens; function names keep their case; `%` and `_` are scale factors
  only directly after a number; `spectre-spice` adds the `par'…'` grouping. The HSPICE dialect rejects calls
  to `cpow`, `hypot` and `fmod` as unknown functions, so HSPICE text gives the same error as today, only
  earlier.
- **`evaluate(ast, scope, dialect="hspice")`**: in every dialect, `EXTRA_FUNCS` and the bitwise operators
  are evaluated: `cpow` is `math.pow` (EvalError where §3.5 says), `hypot`, `fmod` is C's (the sign of x;
  y = 0 raises EvalError), and the bitwise folding of §3.5. HSPICE text cannot produce any of them (the
  HSPICE parser rejects the calls and the operators), so the HSPICE path is unchanged. `dialect="spectre"`
  adds only the domain rules of §3.5: `log ln log10` raise EvalError for x ≤ 0 and `sqrt` for x < 0; `exp`
  for x ≥ 80; `&&` and `||` evaluate both operands. `fold`, and every evaluation in the emitters and
  `tables` (`vacask.py:357, 826`; `xyce.py:285, 369`; `tables.py:1309, 1325, 1629`: subckt defaults,
  `_number`, `child_env`, `bin_bounds`, `constant`, `run_temps`), keep the default dialect: on a Spectre
  expression they differ from `spectre` only outside the domain, which plan.build has already refused
  (§3.5). Without this rule a constant `Call('cpow', (2, 3))` raised "unknown function cpow()" in the
  strict fold every printer runs before printing, and a Spectre `r=2**3` could not print on either engine
  [E94].
- **`to_text(ast, dialect="hspice")`**: knows `<< >> & | ~^` at the dialect's levels (today `_TPREC[e.op]`
  raises `KeyError` for them while an error message is built, and the `KeyError` escapes the callers'
  handlers, `vacask.py:827` [E50]); in `spectre` it spells `cpow` as `**`, so messages quote Spectre syntax
  (`to_text(-(a**2))` is `-a**2.0`, which Spectre reads as `(-a)**2` [E50]).
- **Data.** `SPECTRE_FUNCS` (name → arity, §3.5); `SPECTRE_CONSTANTS` (the 22 names and values of ref19
  pp.466-467); `FUNCS` stays HSPICE's table, and `EXTRA_FUNCS = {"cpow": (2, 2), "hypot": (2, 2), "fmod": (2,
  2)}` serves the evaluator (in every dialect) and the printers; `RESERVED` gains `"$tnom"`; `VACASK_CONSTANTS` gains
  `M_DEGPERRAD` (VACASK defines it [E51]; `expr.py:821-824` lacks it, so a parameter of that name would
  shadow it).
- **Printers** (`_Printer`, `_VacaskPrinter` and `_XycePrinter`, `expr.py:869-1253`; S1 owns them with the
  rest of expr.py, §12). `cpow` as §3.5. `hypot`: `hypot(x,y)` on VACASK (native, `lib/context.cpp:72`), `sqrt(x*x+y*y)`
  on Xyce, which has none ("Netlist error" [E51]). `fmod(x,y)` on both (VACASK `lib/context.cpp:75`, Xyce
  `ExpressionLexer.l:573`; `fmod(-7,3)` = −1 on both [E51]). The bitwise operators: PrintError. `$tnom`:
  VACASK prints `$tnom` (`param_ident` leaves it); Xyce raises PrintError unless the emitter substituted it
  (§3.5).
- `expr_ast.py` is frozen: the `Binary.op` docstring gains `<< >> ~^` (`& |` are already listed,
  `expr_ast.py:42`); a docstring change only.
- `vacask_quote` quotes an all-digit name with a leading zero (§4.5 item 9); it returns `007` bare today, and
  the HSPICE path never reaches it, because spice.py folds `007` to `7` [E49].

### 4.3 `spice.py`
**Base.** `parse_fragment(lines, cwd, opts, resolver) -> Fragment` is `_Parser.read([], lines)`
(`spice.py:843-850`) with `opts.dialect == "spectre-spice"`, `case="lower"`, `parhier_local=True` and the
switches below. That reader exists since vcs-ams's `netlist_commands` fragment: it reads `(line, origin)` pairs
without a title line and resolves each line's references against its origin file's directory
(`origin_dir`, `spice.py:899-904`) [E60]. `spice.parse` keeps refusing `spectre-spice` (`spice.py:3023-3024`);
the `hspice`/`spice` paths and the vcs-ams goldens are unchanged. Lines carry `file:line` origins (§3.1).

**Two phases.** In spice.py a name decides how the rest of a line is read: an R/C/L model-or-value field
(`spice.py:1722-1732`), three or four Q/J nodes (`three_or_four`, 2042-2053), the M bulk check (2075-2077), an
X master as subckt, Verilog-A module, VA card or model (2130-2168), a `.model` type as a VA module (1503-1509).
A name resolved later could not re-read the statement. So spectre.py first declares every subckt, model (with
its master), Verilog-A module and parameter name of every file in both languages; the resolver (`subckt`,
`model`, `param`, `va_module`) is complete before `parse_fragment` reads an element. To reach every file, the
SPICE side of the first phase, `declare_fragment(lines, opts) -> (List[Decl], List[FileRef])` (§10), also
returns the region's file statements in order (`.include`/`.inc`/`.incl`, both `.lib` forms, `.hdl`: the
path text, the section, the enclosing `.subckt`, the origin), from the same lexing (`_logical`), so spectre.py
never re-lexes SPICE text. A field that names
nothing is read by Spectre's documented defaults: an R/C/L field is a model name [M UG p.45]; the fourth field
of a Q line is a terminal unless a model has that name or it is the last field [M UG p.52]. `Fragment.pending`
holds only names that no file defines; spectre.py reports each as an error. Today an undefined `rmod` on `r2
a 0 rmod` is read as a parameter value, and `q3 c b 0 nch mybjt` gets four nodes [E58].

**Files** belong to spectre.py (§3.1); spice.py reads no file in this dialect. Before the second phase,
spectre.py replaces each SPICE-language file statement of a region by the lines of the file (or of the `.lib`
section, up to its `.endl`), each line keeping its own `file:line` origin, so `parse_fragment` reads the file
at its position and in the open scope, inside a `.subckt` too, as `spice.py`'s `d_include`/`expand`/`run`
do today (`spice.py:993-1090`, the expansion key holding `id(self.scope)`); a repeat of `(realpath,
section)` in the same scope is a note. A Spectre-language file named by a SPICE statement splits the region
there at the top level and is an error inside an open `.subckt` (§3.1). `.hdl` stays as a control (its
modules were declared in the first phase), and `.end` ends the file. A statement cannot continue across a
file boundary [M UG p.73], so in this dialect `_logical` never joins a `+` (or `\`) continuation to a
statement that started in another file: it is an error naming both origins. A region may not end inside a
subckt body (§3.1).

**Lexical.** Lower case outside quotes [M UG p.59]; leading blanks ignored (`spice.py:426`) [M UG p.56];
comments as today, except a `* spectre:` comment (§3.13). Node names are strings: no leading-zero folding,
and only `0` is ground (`00`, `007`, `gnd`, `gnd!` and `ground` are ordinary nodes) [M UG p.56; §3.4]; VACASK
quoting then applies to them (§4.5 item 9). The spellings recorded (`spell`, `spice.py:815-819`, and
`Subckt.spelling`) are the lower-case names [M UG p.59], so outputs name SPICE-mode nodes and instances as
Spectre does.

**Numbers and expressions**: §3.3's SPICE table, and §3.5's `spectre-spice` dialect (Spectre semantics,
`par'…'` grouping), through `expr.number(s, dialect)` and `expr.parse(…, dialect)` (`spice.py:712, 1394,
1405`), with the dialect mapped first (§4.2). `_Parser.expr`'s own handling switches too (the switch points
below): its fast paths, its function check, its user functions and its parse-time constant fold, which today
folds `r1 a 0 'sqrt(-4)'` to `Num(-2.0)` and `'-2**2+10'` to `Num(6.0)` with HSPICE's evaluator [E94], so
that `plan.build` would never see the out-of-domain value (§3.5).

**Parameters.** `.param` and `.subckt`-header parameters as today, PARHIER local: "The SPICE Reader treats all
parameter values specified on any instance including subcircuits as the final value instead of allowing a
global to override it" [M UG p.56]. They are returned raw (`Param` with its origin, in source order; header
parameters on their `Subckt`); spectre.py applies §3.7 (duplicates, sort, evaluation) once, over both
languages (spice.py's own pass, `spice.py:2264-2383`, is bypassed). An X-line override must name a parameter
on the target `.subckt` line, or exactly a parameter of a Spectre subckt: "Parameters that are to be passed
into a subcircuit from an instance statement must be specified on the SPICE subcircuit line", and
"Parameter names must be lowercase if you want to instantiate components from SPICE mode" [M UG pp.55, 97].
Any other name (a body `.param`, an undeclared name) is an error naming the instance and the parameter; today
both are accepted (`spice.py:2116-2129`) [E58, E60].

**Models.** Each card gets `Model.prim`: `nmos`/`pmos` level 1, 2, 3 → `mos1`, `mos2`, `mos3`; 49 and 53 →
`bsim3v3` (`.MODEL NCH NMOS LEVEL=49` becomes `model nch bsim3v3 type=n` [M UG pp.46-47]); 54 → `bsim4`; `d`
(level 1 or none) → `diode`, another level → error [M ref5 p.303]; `npn`/`pnp` → `bjt` [M UG pp.48-49];
`njf`/`pjf` → `jfet`; `r`, `c`, `l` → `resistor`, `capacitor`, `inductor`; a Verilog-A module → that module
(`y`). Any other level is an error naming it. Cards go through `SPECTRE_MASTERS` (§3.9), never
`hspice_card`. `x.N` bins form a Spectre model group, `bin_rule="spectre"` [I, §14 q.8]; today they are
HSPICE bins with `BIN_TOL` and per-finger W (`tables.py:278, 1340-1361`) [E58]. Until q.8 is answered,
`plan.build` also selects each SPICE-mode binned instance's card by HSPICE's rule (`tables.select_bin`, on
the same per-path values) and, where the two cards differ (an `nf` > 1 instance of an HSPICE-style binned PDK
card, an edge within `BIN_TOL`), warns naming the instance and both cards; the Spectre rule's card is used.

**Elements.** As spice.py, except:
- every element gets `Instance.prim`, its Spectre master (§4.1 item 7);
- an M line without `l`/`w` keeps them absent, for §3.9 to fill: HSPICE's DEFL/DEFW (1e-4 m,
  `spice.py:2084-2093`) never apply [E58];
- a keyed parameter that the SPICE element does not define is a Spectre instance parameter of the element's
  master [M UG p.54: such parameters are "passed directly to Spectre as is"], looked up in the master's
  `SPECTRE_MASTERS` instance rules (`MasterRow.instance`, §10) and handled as in Spectre mode (`isnoisy=no` →
  §4.5 item 7); one the master does not have is an error naming
  the element and the parameter (today spice.py keeps every keyed parameter, `spice.py:1747, 1759, 2026-2030`,
  and both emitters print it [E60]);
- `scale=` on R, C, L, E, F, G, H is an error in v1: HSPICE multiplies the value by it (`spice.py:1762-1765,
  1933, 1967, 2004`), while Spectre's resistor `scale` scales `w` and `l` only (§3.8).

**Sources.** A V or I line fills `Source.spectre` in Spectre names, because the Reader converts the netlist
to Spectre syntax [M UG p.42]: `DC` → `dc`; `AC mag phase` → `mag`, `phase`; `PULSE v1 v2 td tr tf pw per` →
`type=pulse val0 val1 delay rise fall width period`; `SIN vo va freq td theta phase` → `type=sine sinedc ampl
freq delay damp sinephase`; `EXP v1 v2 td1 tau1 td2 tau2` → `type=exp val0 val1 td1 tau1 td2 tau2`; `PWL t v …
TD=` → `type=pwl wave=[…] delay`. `spectre.resolve_source` then applies §3.8.1. A SIN without a frequency is
an error naming both readings (SPICE3: 1/TSTOP; a converted `sine`: 0, a constant [I, §14 q.38]); pulse edges
follow §3.8.1's transres rule and warning; an EXP without `tau1`/`tau2` is §3.8.1's error. `PWL R=`, `SFFM`,
`AM` and `PAT` → error. `synth_step`/`synth_stop` are not used: today spice.py fills HSPICE's defaults from
TSTEP/TSTOP (`source`, `spice.py:2593-2691`: `pulse(0 1)` gets `tr=tf=1e-9 pw=1e-6`, `sin(0 1)` `freq=1e6`)
[E58].

**Dot statements.** spice.py reads the definitions; spectre.py reads the rest from `Fragment.controls`
(keyword, spice.py's `(key, text)` fields, origin), unevaluated:

| statement | disposition |
|---|---|
| `.param`/`.parameter(s)`, `.model`, `.subckt`/`.macro`, `.ends`/`.eom` | spice.py (above) |
| `.global` | spice.py: `Netlist.globals`, never ground [I, §14 q.46] |
| `.include .inc .incl`, `.lib` (both forms), `.endl` | spectre.py, spliced in by the first phase (§3.1, Files above) |
| `.end` | ends the file [M UG p.50] |
| `.hdl "f.va"` | `ahdl_include`, with a note [I, §14 q.28] |
| `.title` | note (§3.1's title rule holds) |
| `.protect .prot .unprotect .unprot` | note (§3.1) |
| `.op` | `dc` named `opBegin` [M UG p.232] |
| `.dc s1 a b c [s2 d e f]` | `dc dev=s1` named `srcSweep`; a second source is an enclosing sweep [M UG pp.46-47: `.DC VDR 0 5 0.1 VG 0 2 0.1` converts to an outer `sweep … dev=vg` around `dc … dev=vdr`]; `temp` → `param=temp`; a parameter → `param=`; `LIN DEC OCT POI DATA=` → error |
| `.ac dec\|lin n f1 f2` | `ac` named `frequencySweep`; `oct`, `poi` → error |
| `.tran tstep tstop [tstart [tmax]] [uic]` | `tran` named `timeSweep`: `step=tstep`, `stop=tstop`, `outputstart=tstart`, `maxstep=tmax`, `uic` → `skipdc=yes` [I, §14 q.9 and q.46]. The fields are SPICE2G's, the language the SPICE Reader takes [M UG p.51: "The SPICE Version 2G User's Guide … is the reference for the SPICE2 input language"], where the third field is TSTART, the time before which nothing is output: Spectre's `outputstart`. A `tstart` other than 0 gets a warning naming that reading. The UG's oscillator pair (pp.48-49), where `.tran 1us 80us 10ns` sits beside `tran stop=80us maxstep=10ns`, is a hand-written comparison ("Here is the Spectre version of the same netlist": it renames the nodes and the analysis and lacks the `insensitive=yes` the Reader inserts, UG pp.49, 52), not Reader output, so it does not decide the field. Today spice.py reads HSPICE's start and adds HSPICE's maxstep (`.tran 1n 1u 10n`: start 1e-8, maxstep 5e-9), and refuses `.tran 1n 1u 0 10n` [E58] |
| a second analysis of a type | `<type><n>` (`ac2`) [M UG p.232; §14 q.9 for the others] |
| `.temp t` | `options temp=t`; `.temp t1 t2 …` → error in v1 (Spectre makes a temp sweep of it [M UG pp.46-47]; today spice.py simulates the first value with a warning [E58]); `.temp = t` → error [M UG p.55] |
| `.ic`, `.nodeset`: `v(n)=x`, and HSPICE's `n=x` | §3.10 (`n=x` [I, §14 q.28]) |
| `.option(s)` | the map below [M UG p.50: "Options that are analysis specific are found on the global .options card in SPICE. In Spectre, options pertaining to an analysis are listed on the analysis card"] |
| `.print`, `.probe` `[analysis] items` | the item table below |
| `.measure`, `.meas` | warning: not run |
| `.alter` | error (Spectre's reader has it [M UG p.56]; v1 does not) |
| `.noise v(n[,m]) src [pts]` | error in v1 (v2: a `noise` over the preceding `.ac` sweep with `iprobe=src`) |
| `.four .disto .pz .sens .data .if` and any other | error naming it |

`.option(s)` keys (spectre.py stores in `Netlist.options` only keys with a Spectre meaning, §3.10):

| SPICE key | disposition |
|---|---|
| `reltol` | `reltol` |
| `abstol`, `vntol`, `chgtol` | `iabstol`, `vabstol`, `chargeabstol` [I, §14 q.46 for the renames] |
| `gmin`, `temp`, `tnom`, `scale` | §3.10 |
| `method=trap\|gear` | every `tran`'s `method`: `trap` / `gear2` [I, §14 q.46: UG p.50 places analysis options on the analysis; the hand-written pair of UG pp.48-49 shows `.option method=trap` beside `tran … method=trap`] |
| `post ingold probe list node opts numdgt` and other output or listing keys | note |
| `wl defl defw defad defas defpd defps defnrd defnrs parhier search dcap spice aspec scalm geoshrink` | error: they change how the netlist is read (spice.py consumes them while it parses, `spice.py:1114-1213, 2084-2093, 2912-2978`) |
| `delmax rmax dvdt lvltim accurate itl1`…`itl6` | warning: not applied |
| any other | warning |

`.print`/`.probe` items (today spice.py keeps only `v(n)`, `v(a,b)`, `i(x)` and `v(*)`, `spice.py:2846-2895`
[E58]):

| item | disposition |
|---|---|
| `v(n)`, `v(a,b)` | a save of the node or nodes |
| `vm vp vr vi vdb (n)` | a save of the node (the PSF holds the complex value); a note for the derived form |
| `i(x)` | the current of x (§6.3) |
| `v(*)` | `save=allpub` |
| `name=expr`, `par(…)`, `par'…'`, `deriv()`, any other function | warning: output expression not written (§0) |

A SPICE-mode netlist also saves allpub, as the converted decks do (`sppSaveOptions options save=allpub`
[M UG p.47]; [I, §14 q.46] that `.print` adds to it).

**cadence2xyce.pl's rules in `spectre-spice`** (the HSPICE-dialect preprocessor for the CMC decks; each rule
against today's spice.py [E57, E61]):

| cadence2xyce.pl (lines, at 6e81e73f) | spectre-spice |
|---|---|
| `+` continuation (47-59) | as spice.py (`spice.py:404-442`) |
| `.option post/ingold` dropped (138) | note (`.option` map) |
| `abstol`/`reltol` dropped (139-140) | mapped (`.option` map) |
| `.probe` turned into a comment (149-152) | saves or warnings (item table) |
| `.print` with `par`/`{}` turned into `V(*) I(*)` (291-297); a `.PRINT` inserted (299-320) | warnings per item; allpub is saved anyway |
| `dc=`/`ac=` on sources (174-175) | as spice.py |
| `par'expr'` → `{expr}` (177-178) | a grouping (§3.5) |
| `.temp` → `.OPTIONS DEVICE TEMP` (180-183; Xyce ignores `.temp` [E91]) | the `.temp` row |
| `.hdl` module scan with one level of `` `include`` (81-116, 390-444) | §3.1's scan, recursive |
| `../code` search (85-96, 342-352) | `-I` directories |
| stem alias (103-112) | not adopted (§3.1) |
| Verilog-A `` `include`` inlining (154-171, 354-388) | not needed: both engines resolve a local include relative to the `.va` [E75] |
| `.model <va-module>` → letter type and `level`, included modelcards rewritten in place (185-236) | a Verilog-A card gives `y` instances (`spice.py:1505-1540, 2156-2164`); on Xyce §4.5 item 13; vamos never writes its inputs |
| X lines → M/D/Q/Y (238-282) | kind `y`; Xyce letter devices for `xyceModelGroup` modules (§4.5 item 13) |
| `key = val` (243) | as spice.py |

**`Fragment`** (phase 0, §10): `items` (raw `Param`s, `Model`s with `prim`, `Subckt`s, `Instance`s, in source
order); `controls: List[Tuple[str, List[Tuple[Optional[str], str]], str]]` (keyword, spice.py's `(key,
text)` fields from `_lex`, `spice.py:462-529`, origin), so the `key=value` structure of `.ic v(b)=0.5` or
`.option reltol=1e-3` is never lexed twice; `notes`; `left_out: Dict[str, Note]` (the reason a subckt is
left out, as `spice.left_out` returns it); `pending: List[Tuple[str, str, str]]` (kind, name, origin).

**Switch points in spice.py**, each gated on `opts.dialect == "spectre-spice"`: numbers (`spice.py:712`;
`expr.py:152, 214`); comments (427); `_logical`'s continuations (404-442: never across a file boundary,
above); nodes (272-277, 1296-1324, 1442-1457); `resolve` (906-930) and the include statements (1040-1090),
which spectre.py splices in (above); `d_hdl` (1153-1179: the include-following scan, every dialect); options
(1114-1213, 2912-2978); `e_rcl` (1722-1765); `device` (2017-2032); `e_m` (2084-2093); `e_x` (2116-2168);
`three_or_four` (2042-2053); `source` (2593-2691); `tran`/`max_step` (2416-2579); `parameters` (2264-2383);
`controls_pass` (2771-2895, bypassed). In `_Parser.expr` (1380-1441), each through the dialect: the number
fast path (1392-1396); the identifier fast path (1397-1402), whose `RESERVED` check returns a bare name as
`Name(…)` without `expr.parse`, so `temp` would stay a plain parameter and `temper` would read the
temperature (§3.5); the `FUNCS` check (1421-1424), which accepts `SPECTRE_FUNCS` and `EXTRA_FUNCS` with
their arities; user functions through `expr.parse(funcs=…)`, not `expr.inline` (1409-1412, §3.5); and the
parse-time constant fold (1435-1439), which uses `evaluate(…, dialect="spectre")` and turns an EvalError into
an error naming the element. Also the function-definition check (1345: a name in `SPECTRE_FUNCS` is a user
function that wins, with a warning, §3.5) and `const` (1459-1469, `dialect="spectre"`). The colon-clash check
(`_colon_clashes`, `spice.py:289-338`, run by `spice.parse` only) runs on the merged Spectre IR; it walks
bodies with `ir.flat_items`, so a net or an internal node used only inside an `if` branch is compared too
(§4.5 item 1).

### 4.4 `tables.py`
- **`model_params(model, engine, binned=False, options=None, dialect="hspice")`.** For `"spectre"` every card
  must have `Model.prim` (a `TableError` otherwise); it does not call `hspice_card` and does not strip
  `STRIP_KEYS` (they are Spectre parameters [M ref5 p.197]); it applies `SPECTRE_MASTERS` (renames, strips,
  the defaults rows of §3.9) and keeps the type/level/version/bin handling, `_xyce_diode_charge` (an engine
  fix) and `_wire_card` (§3.9). Both emitters pass `nl.dialect` (`vacask.py:374`, `xyce.py:519`) and skip
  `mos_junction_warnings` and `mos_scale_warnings` for it (`vacask.py:579, 585`; `xyce.py` `i_m`).
  `DISPATCH` stays the module/level table, unchanged.
- **`SPECTRE_MASTERS: Dict[str, MasterRow]`** (data, frozen in phase 0, §10): master → IR element kind, card
  kind by polarity, level, polarity key, terminal names, default instance geometry, default bin bounds, one
  disposition per ref5 card parameter and one per instance parameter (§3.9). `model_params` (S3) applies the
  card rules; `plan.build` (S2) decides every rule whose message depends on the run (`ParamRule.warn`, §10),
  and refuses a sweep or alter of a parameter whose rule folds or strips it (§5.2).
- **`path_envs(nl, values=None, strict=False, dialect="hspice") -> Iterator[PathEnv]`** (phase 0, §10): every
  instance path, the top level first, depth first in body order, with its subckt, `Scope` and parameter
  values, computed by the rule `xyce._Deck.child_env` applies today (`xyce.py:276-290`: the top-level values,
  then the subckt's own parameters in order, an X-line override evaluated where the X line is), and with each
  `Cond` resolved on the path (only the taken branch's items are walked; a condition that is not a number on
  its path raises). Non-strict, a parameter that does not evaluate is hidden, as today; strict, it raises an
  EvalError naming the path and the expression. `plan.build` walks it strict, with `dialect="spectre"`, once
  per parameter state; `xyce._Deck.elaborate` is rebuilt on it (non-strict, nominal values; byte-identical
  decks for every HSPICE golden), so the branches and bins the plan checked are the ones the Xyce deck prints.
  `path_counts(nl)`, built on it, counts the instance paths that reach each subckt, for §5.3's per-path rule
  on Xyce.
- `bin_bounds_spectre` (fills a bound the card omits from `SPECTRE_MASTERS`: `lmin=wmin=0`, `lmax=wmax=1` m [M
  ref5 p.200]), `bin_guard_spectre` and `select_bin_spectre` implement `bin_rule="spectre"`: exact bounds,
  group order, total `w`.
- `Scope` keeps `bin_rule="spectre"` groups in `bin_index` order; the name sort stays for HSPICE bins
  (`tables.py:1554-1558`).
- **Walks over `Cond`** (§4.5 item 1): `reachable`, `all_subckts`, `smoke_netlist`; `Scope.__init__`
  (`tables.py:1535-1558`), which collects the inductors that K elements in branches name; `item_exprs`
  (`tables.py:1189`: a `Cond` yields its conditions, a `ParamTest` its tests); `enclosing_reads` (1212),
  `element_at` (1152) and `current_ref_errors` (1168). Today each of them skips a `Cond` [E56].
- `solver_options` is not called for `Netlist.dialect == "spectre"` (§3.10).
- `scale_source` uses `dataclasses.replace` (§4.1).

### 4.5 `vacask.py` / `xyce.py`
1. **Every walk sees `Cond`.** The walks that skip non-`Instance` items today use `ir.flat_items` or handle
   `Cond` explicitly: in `tables.py` those of §4.4; in `vacask.py` the override scan in `_Deck.__init__`,
   `_names`, `_Deck.items` and `_resolve`; in `xyce.py` `elaborate`/`body_of`, `check_scope`, `printed_path`,
   `_multiplied`, `_models`, `_Deck.items` and `render`'s model-name collision list (`xyce.py:406-407`); in
   `spice.py` `_colon_clashes` (`spice.py:289-338`: `xs_of`, `internal` and `visit` look only at the
   instances directly in a body, so a net `x1:mid` or an internal node used only in a branch would never be
   compared, and the silent node merge it guards against would go undetected; S1 owns it, §12). On
   Xyce, conditions are resolved per instance path (item 2). A walk that meets an item outside
   `ir.ITEM_TYPES` raises; it never skips. Today the `npn_mod` subckt of UG p.110 renders empty on both
   engines, because its cards are used only inside branches, and an X line with `m=` in a branch is missed by
   `_multiplied` [E44, E56].
2. **`Cond`.**
   - VACASK prints `@if c` / `@elseif c` / `@else` / `@end`, around instances and nested blocks [VACASK
     `cir-conditional.md`]. Verified [E9]: an `@if` on a subckt parameter selects per instance, with the same
     instance name in both branches.
   - Xyce resolves conditions per instance path in the elaboration walk, which runs on `tables.path_envs`
     (§4.4). The `__vb<k>` binning-variant mechanism is generalized to variants keyed by bin bindings and
     branch choices. A condition that is not a number on its path is an error.
   - Name-uniqueness checks run on the resolved branch (§3.7).
3. **`bin_rule="spectre"`.** VACASK prints an `@if` chain in group order, with exact guards on total `w`.
   Xyce always binds the instance to its card per path (no native binning).
4. **Variables and overridable subckt parameters.** `variables` (from the plan): a top-level parameter that a
   sweep or alter targets.
   - VACASK prints it as `parameters p=vamos_v_p`, with `var vamos_v_p=<value>` in the control block [E].
   - Xyce keeps `.param p=<nominal>` and sweeps it with `.STEP`/`.DC` [E].
   - Such a parameter and its dependents are never folded into subckt defaults, bin guards or conditions on
     VACASK (§4.1, `Netlist.values` in the render copy [E53]). VACASK's `NOT_GIVEN` scheme keeps those
     defaults overridable.
   - VACASK can sweep or alter only a primary subckt parameter: a dependent one fails ("parameter 'l' of
     instance 'x1' not found"; an alter stops the run) [E24]. A subckt parameter that a `sub=` sweep or alter
     targets is therefore declared overridable exactly like one an X line overrides (`p=NOT_GIVEN` plus
     `p__v`): `RunPlan.overridden` adds these targets to the emitter's overridden set. `NOT_GIVEN` under a
     sweep and an alter was re-checked [E77].
5. **Plan rendering.** `plan.py` and `signals.py` use IR names only (`.` paths, IR card names, IR parameter
   names); the emitters own every engine spelling.
   - `vacask.render(nl, analysis_name, osdi, notes, op=False, plan=None, sigmap=None, names=None)` prints the
     plan's control block (§7.1) in place of the single analysis. It translates every IR name in the actions,
     prints each step's saves from `sigmap.per_step[step.id]` with the directive each analysis type reads
     (§5.5), treats every `sub=X param=p` target as an override of p (item 4), and takes its `options` lines
     from the plan instead of `tables.solver_options` (§3.10): the global ones (temp, tnom, gmin, reltol,
     vntol, abstol, chgtol, `strictsave=2`) from the `options` actions, and each tran's settings (§5.4) from
     its `AnalysisStep.args`, printed before the step's sweep statements and restored after it (§7.1). It
     names each sweep statement by its `SweepLevel.id` (§5.2).
   - `xyce.render(nl, osdi, notes, op=False, step=None, sigmap=None, names=None)` prints one step (§7.2) and a
     `.PRINT` with exactly that step's SignalMap entries, in order; its options are temp, tnom and gmin, with
     the tolerance note of §0.
   - With `plan`/`step` None the output is byte-identical to today.
6. **Name map.** `render()` fills `names: Dict[str, Dict[Ref, str]]` (step id → Ref → engine column) for
   every Ref it printed; `results.py` reads columns only through it, with `Raw.exact` (§4.6, §6.4). The engine
   names of instances, model cards (`m_<name>` and its suffixes) and quoted nodes stay inside the emitter,
   which applies them to the plan's actions and saves. Each emitter also exports `names_for(nl, plan,
   sigmap)`, which computes the same map without rendering. These signatures are phase 0 (§10).
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
     recomputed in every state-replay deck. Xyce's operating point for a waveform source printed without
     `DC` is 0, not its t = 0 value [E70], and today's `xyce.py` prints no `DC` for a waveform source whose
     `Source.dc` is None (`xyce.py:799-802`), so the rule is necessary.
9. **Quoting.** An all-digit node name with a leading zero is printed quoted on VACASK (`'007'`): bare, VACASK
   rejects it, and quoted, `'007'`, `7` and `'00'` are distinct nodes, `'00'` not ground [E35]. Xyce keeps
   them distinct unquoted [E35]. (The HSPICE path never reached this: `spice.py` strips leading zeros.)
10. **Zero resistors.** `r` = 0 prints on VACASK as a 0 V `vsource`, a short whose flow is the resistor
    current: VACASK's `resistor` aborts every analysis with "NaN found", and `sp_resistor` clamps to 1e-12 Ω
    with a 0.08 % current error [E22]. Xyce takes R=0 [E22].
11. **Nested subckts on VACASK** (`Netlist.dialect == "spectre"`). VACASK resolves a name in a nested
    definition in that definition and at top level only [E65]. `vacask.render` lifts each nested definition
    `s` that reads parameters P of its enclosing subckts (`tables.enclosing_reads`, which walks `Cond`) to top
    level as a fresh `<outer>__<s>`, declaring P as its own parameters; every X line that instantiates `s`
    inside the enclosing body gets `p=p` for each p in P that it does not override itself. Deeper nesting is
    lifted recursively. Instance paths do not change (only the definition moves), so `names_for` is
    unaffected. Xyce prints the nesting unchanged. Today's refusal (`vacask.py:310-319`) stays for the HSPICE
    path.
12. **Verilog-A parameter names on VACASK** are printed lower-cased: openvaf-r's OSDI names are lower case and
    VACASK matches them exactly ("Parameter 'R' not found." for a module declaring `R`; `r=3k` works) [E64].
    spectre.py has matched them against the module's declarations case-insensitively (§3.9). VACASK's
    per-instance card (`vacask.py:648-653`) also takes instance-typed parameters [E64].
13. **Verilog-A on Xyce.** `spice.va_modules` (§10) reads each module's header attributes `xyceModelGroup`,
    `xyceLevelNumber`, `xyceTypeVariable` and `xycePTypeValue`, as cadence2xyce.pl's `scan_va_modules` does
    (28 of the 54 ADMS example `.va` files carry them, every CMC model among them [E64]). The parse keeps
    them in `Netlist.va_modules` (§4.1 item 16), where `xyce.render`, `names_for`, `printed`/`printed_path`
    (the letter-device name) and phase 2's checks read them.
    - A module with `xyceModelGroup` in {MOSFET, DIODE, BJT, Resistor, Capacitor} and an `xyceLevelNumber`
      prints as that letter device (M/D/Q/R/C) on a `.model <card> nmos|pmos|d|npn|pnp|r|c level=<n>` per
      Spectre card (one per module for instances that name the module), with the instance's parameters on the
      device line. The polarity comes from the card's value of the `xyceTypeVariable` parameter against
      `xycePTypeValue`; that parameter is consumed. PyMS registers such a module as that device, not as a Y
      device: today's Y form fails for it with "Unrecognized parameter A for device YRESISTOR!R1", with or
      without parameters, and the letter form with `R=3k` on the device line gives 0.25 V as it should [E64].
    - Any other card parameter is refused (an error naming it, as `xyce.py:752-757` does for Y devices today):
      on a letter-device card PyMS gave a singular matrix and V(A) = 1.01e9 with rc 0 for a toy diode [E64].
      The CMC decks are no evidence for lifting the refusal: their runs bound Xyce's built-in ADMS devices,
      not the PyMS modules (below) [E99]. Lifting it is phase 3's, on a route that avoids the colliding levels
      (§12).
    - A level that collides with a built-in Xyce model binds the built-in silently: a toy module tagged DIODE
      level 2002 ran as Xyce's `DIODE_CMC 2.0.0` [E63], and every CMC deck that reached its Device Count
      Summary lists a built-in ADMS device (`M level 77 (BSIM6)`, `M level 107`, `108` or `110 (BSIM-CMG FINFET
      v1xx.0.0)`), never its module: each CMC module's level is that of a compiled-in ADMS device
      (`N_DEV_RegisterADMSDevices.C:89-93`; `N_DEV_ADMSbsim6.h:3335-3336`), while PyMS names a device after its
      module (`PyMS/vae/xyce_device_gen.py:513-514`) [E99]. So the Device Count Summary of the `-norun` check
      must list every such module under its own name (`RESISTOR (resistor)`, not `R level 6 (…)`) [E64]; a
      built-in entry for the module's letter and level is an error, so every CMC model is refused on Xyce in
      v1. `xyce.device_summary_problems(text, nl)` (§10, S3) implements the check. `xyce.smoke`'s `-norun`
      step applies it, so vcs-ams's Xyce path, which shares the letter-device route (§4), is guarded too, and
      spectre's phase 2 applies it to its first deck (§7.2).
    - Any other module keeps today's Y form and its refusal of card and instance parameters, because PyMS
      ignores a card parameter on a Y device silently (`I(V1)` = −1.000e-3 A, the module default, with no
      message [E63]).
14. **Cards.** `model_params(…, dialect=nl.dialect)` and no HSPICE M-instance warnings for Spectre (§4.4).
15. **Xyce spellings of hierarchical names.** Xyce element names start with their letter, so the emitter
    prints a subckt instance whose IR name does not start with `x` as `X<name>` (`printed`, `printed_path`,
    `xyce.py:932-959`), and its internal nodes become `XI2:MID` [E84]. Today `prints()` and the `.IC`/`.NODESET`
    `pairs()` spell hierarchical nodes with a plain `replace(".", ":")` (`xyce.py:899, 911`), which is right only
    when every instance starts with `x`, as HSPICE guarantees. For Spectre (ADE names instances `I0`, `I2`, …
    [S]) every `.PRINT` item, every `.IC`/`.NODESET` node and every `DNO()` is spelled through `printed_path`:
    `.print v(I2:MID)` makes Xyce abort, and `.ic v(I2:MID)` is only warned about and ignored, rc 0 [E84].

### 4.6 `rawfile.py`
- **`Raw.exact(name) -> int`**: the column whose name equals `name` exactly (case-sensitive, no aliasing);
  `KeyError` otherwise. `Raw.index` (`rawfile.py:105-118`), which `column`, `at` and `crossings` use, compares
  lower-cased names and aliases `.`/`:`, `v()`/`i()`, `<inst>.i` and `I(B…)`; VACASK keeps case in column
  names, so a Spectre netlist with nodes `A` and `a` gets columns `A` and `a`, and `index('A')` raises
  "matches several variables: A, a" [E84]. Spectre outputs use `exact` only (§6.4); vcs-ams keeps `index`.
- **`scan(path) -> List[RawPlot]`** and **`fix_scanned(path, plots) -> bool`**: moved from
  `backends/cosim.py` (`RawPlot` and `scan_raw`, `cosim.py:378-416`; `_fix_points(path, plots)`, 582-617),
  which re-exports them under their old names (`RawPlot`, `scan_raw`, `_fix_points`), because
  `test_vamos_run.py:625-665` calls `cosim.scan_raw(a)` and `cosim._fix_points(a, plots)` and compares the
  result with `rawfile.fix_points(b)`; `check_raw` stays in cosim. `scan` gives per plot the point count, the
  declared count and the first and last time, through `mmap`.
- **`fix_points(path) -> bool`** (existing, `rawfile.py:406-445`) keeps its signature and result, and becomes
  `fix_scanned(path, scan(path))`, so it streams: today it reads the whole file, and `read`, `read_all` box
  every value in per-point tuples: 161.6 MB of rawfile took 1259 MB of RSS with `read` and 17 MB with `scan`
  [E85]. `test_netlist_rawfile.py`'s `fix_points` cases (`:196-289`) pin the result unchanged.
- **`iter_rows(path, plot, columns) -> Iterator[tuple]`**: the given columns of one plot, row by row, through
  `mmap` (binary rows with `struct.unpack_from`, ASCII through the cosim token walk). `Raw.exact`, `scan`,
  `fix_scanned` and `iter_rows` are implemented in phase 0, not only declared: S3, S4 and phase 2 all read
  through them (§12).
- **`read_prn(path) -> List[Raw]`**: Xyce standard-format `.PRINT` tables (noise), split per `.STEP` (§7.2);
  S4 implements it.

## 5. The run plan – `netlist/plan.py`

### 5.1 Ordering
- Spectre runs analyses in netlist order, and there is no default analysis [M UG pp.66-68].
- `options` statements are global: they are set while the circuit is read [M ref19 p.187]. `set`, `alter` and
  `info` are positional.
- `plan.build` walks `Netlist.analyses` and produces an ordered list of **actions**. Their `args` are a
  phase-0 contract (§10): render (S3), the results (S4) and phase 2's Xyce state replay read exactly these
  keys. Names are IR names (`.` paths, IR card names, IR parameter names), which `render` translates (§4.5
  item 5); values are evaluated: a `float`, a `str` for an enumeration or a file name (%-expanded and
  resolved), a `List[float]` for a vector.

| `Action.op` | `Action.args` |
|---|---|
| `options` | `{<key>: value}` for each option that changes: §3.10's keys with an engine meaning, as Spectre spells them (`temp tnom gmin reltol vabstol iabstol chargeabstol`). The first action holds the whole global set (argv ⊕ options ⊕ defaults and `+paramdefault`, §2.2); each `set` statement gives one more |
| `var` | `{<top-level parameter>: float}`: the plan variables (§4.5 item 4) at their nominal values; one action, before the first analysis |
| `alter` | `{"target": Target, "value": float or str}`, plus `"source": Source` when the target is a field of an independent source: the source re-resolved by `spectre.resolve_source` with the new value (§5.3) |
| `source` | `{"path": <IR path of the source>, "type": "dc" or the waveform type, "source": Source}`: a VACASK source-state switch (§5.3) |
| `analysis` | `{}`; the step is `Action.step` |
| `note` | `{"note": Note}`: reported by flow in netlist order (an `info` statement, a skipped construct); render prints nothing |

  `Target` is always a 3-tuple (§10): `("instance", <path>, <param>)` (`dev=`, `sub=`, and a source field),
  `("model", <card, or a group's base name>, <param>)`, `("option", "temp" | "tnom", "")`,
  `("variable", <top-level parameter>, "")`, and `("freq", "", "")` for the frequency axis of `ac`, `noise`
  and `xf`. An alter of temp or tnom is an `alter` action with an `option` target, not an `options` action.
  The settings of each analysis are in `AnalysisStep.args`, not in actions: §3.11's parameter names as
  keys, evaluated as above, a key absent when it is neither given nor defaulted; for a `tran`, errpreset
  is applied (§5.4): `reltol`, `relref`, `method`, `lteratio` and `maxstep` hold the effective values and
  `errpreset` the preset used. `AnalysisStep.options` holds the global options in force when the step runs
  (every earlier `options` and `option`-target `alter` applied), under the same keys: what S4 writes into
  the PSF headers (§8.3) and what the Xyce state replay prints (§7.2). A netlist without analyses runs
  nothing; it exits 0 with a notice and an empty `logFile`.
- **Evaluation.** `plan.build` also evaluates, on every instance path:
  - every expression of every reachable item, with the Spectre evaluator, for every parameter state the plan
    sets (§3.5): an out-of-domain value is an error naming the expression and the path, so none reaches an
    engine;
  - the `ParamTest`s (§3.6) and the condition/bin rule (§3.7);
  - the analysis parameters, `ic` and `nodeset` values with `Netlist.values` (§3.10, §3.11); one that reads a
    plan variable or one of its dependents is an error in v1;
  - the run-dependent card rules of §3.9 (`ParamRule.warn`: the `capmod` warning when an `ac`, `noise`, `xf`
    or `tran` runs, the diode `eg` warning at a temperature other than the card's `tnom`), over the cards
    that some instance path uses;
  - for a SPICE-mode binned instance, the HSPICE rule's card beside the Spectre rule's, with §4.3's warning
    where they differ.

  The paths and their values come from `tables.path_envs(…, strict=True, dialect="spectre")` (§4.4). It
  fills `RunPlan.dependents`: the variables and, transitively, every top-level `Param` that reads one (§4.1);
  `signals.resolve` only reads it.

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

- start ≤ 0 without a step parameter is linear [I, §14 q.11]. `center`/`span` with a log sweep is an error
  [I, §14 q.11]. The open points of `dec`, `center`/`span` and `log` are §14 q.11. Sweep bounds and values
  are expressions (`SweepSpec`), evaluated by `plan.build`.
- **The grid is plan.build's.** Every sweep a step runs under is a `SweepLevel` of the step (§10): the
  enclosing Spectre sweeps outermost first (`AnalysisStep.context`), then the step's own (`own`: a dc
  target, the parameter of an `ac`/`noise`/`xf` at a fixed `freq`, or their frequency axis). Its `spec` is
  evaluated and engine-ready: every Expr field of it is a folded `Num` after `plan.build` (the `SweepLevel`
  invariant of §10, which S3 may rely on), and `mode` is `lin`, `step`, `values`, or `dec` only when
  N·log10(stop/start) is an integer; a non-integral `dec`, a `log=N`, a sweep with no step parameter and `center`/`span` are expanded
  by `plan.build` into `values` (or `lin`), so the "computed by vamos" grids in the table are plan.build's
  (§11's Plan test of the dec grid), and render only prints them. `plan.build` also names each level's
  VACASK sweep statement (`SweepLevel.id` = `vamos_w<k>`, numbered over the plan; the rawfile's sweep column
  carries it, §6.4, §7.1) and records its `continuation` (below), its PSF sweep variable (`label`: `"dc"`,
  `"temp"`, the parameter or `"freq"` in a leaf, `"R1:r"` in a parent), the variable as descriptions write it
  (`desc`: `"Vin:dc"`) and that variable's `units` (§8.3), so the results need no IR.

**Targets** (IR names in the action, always a 3-tuple `Target`; the engine forms come from render):
- `dev=X param=p` → `("instance", "<path>", p')`, where p' applies the instance master's `SPECTRE_MASTERS`
  instance renames (`MasterRow.instance`), as on the cards. VACASK `instance="x1:r1" parameter="p"`
  (hierarchical `X1.R1` → `"x1:r1"`) [E]; Xyce `.DC R1:R …` (§7.2) [E20]. A field of an independent source
  (`dc`, `val0`, `ampl`, …) follows §5.3's source rules.
- `mod=M param=p` → `("model", "<IR card>", p')`, where p' applies the card's `Model.prim` renames from
  `SPECTRE_MASTERS`. VACASK `model="<card>"` [E]; Xyce `.DC M:P …` [E20]. A model group cannot be swept this
  way (a VACASK sweep names one model): error in v1.
- `sub=X1 param=p` → `("instance", "x1", p)`, with p made overridable on VACASK (§4.5 item 4) [E24]; Xyce
  sweeps a generated `.param` (§7.2) [E20].
- `param=temp` → `("option", "temp", "")`: VACASK `option="temp"` [E]; Xyce `.DC TEMP` [E].
- `param=<top-level parameter>` → `("variable", p, "")`: VACASK `variable="vamos_v_p"` [E] (a same-named
  `var` does not override a `parameters` entry [E2], hence the hook); Xyce `.DC p` [E].
- `dc` with no target is an operating point.
- A target that a condition or a bin guard reads: §3.7.
- **A parameter vamos folded or stripped is never a target.** A `mod=` target whose `SPECTRE_MASTERS` rule
  folds or strips it (a resistor card's `r rsh l w etch etchl`, a capacitor card's `c w l etch cj cjsw`, a
  MOS card's `w`/`l`, §3.8, §3.9), and a `dev=` target listed in the instance's `Instance.folded` (the `w`/`l`
  of a resistor, the `w l area perim` of a capacitor, that fed a computed `r` or `c`), is an error naming the
  target: the engines print `r` and `c` explicitly, so such a sweep or alter would run with no effect on
  either engine. Re-folding per point is v2. The same check applies to alters (§5.3).

**Restore.** Spectre returns the swept parameter to its original value after the analysis [M ref19 pp.43, 64,
406]. VACASK restores a swept instance, model, option or variable after the sweep [E1, E4], although
`cmd-sweep.md:55` still says the parameter is "left at its last value". vamos relies on the observed behaviour
and pins it with the engine-fact test `engine_facts/E01` and `E04` (§11 T1), so an engine change that
follows the documentation fails loudly.

**Continuation.** Spectre's analyses restart their DC solution from scratch when a condition has changed
(`restart=yes`, the default [M ref19 pp.66, 423]); a dc sweep starts each point from the previous one
(`swpuseprevic=yes` [M ref19 p.67]). VACASK continues from the previous point by default (`continuation=1`,
`cmd-sweep.md`). So the sweep statements generated for a Spectre `sweep` block carry `continuation=0`, unless
the child sets `restart=no`; a dc analysis's own sweep keeps the default, unless it sets `swpuseprevic=no`.
On a bistable circuit the two settings give 1.83 V and 0 V at the same point [E29]. `plan.build` records the
value per level (`SweepLevel.continuation`).

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
  - **Per path on Xyce.** The IR holds one `Instance` per subckt body, shared by every instance of that
    subckt (`ir.py:95-106`), and the Xyce emitter's only per-path mechanism is the `__vb<k>` variant, keyed
    by bin bindings and branch choices (`xyce.py:255-274`). So on Xyce an alter whose target lies inside a
    subckt that more than one instance path reaches (a hierarchical `dev=x1.r1` when `x2` is another
    instance of the same subckt; a `sub=` target whose X line sits in such a body; a source field there) is
    an error in v1 naming the paths: changing the shared item would also change `x2.r1`, where Spectre and
    VACASK change only the path. The path counts come from `tables.path_envs` (§4.4). A uniquified copy of
    the definitions along the changed path is v2. The same rule covers the Xyce sweep hooks of §7.2.
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
  - A value that changes a condition or a bin: §3.7. A parameter vamos folded or stripped: §5.2's error.
- **Source states on VACASK.** VACASK uses `dc` only when `type="dc"` [E17], so a waveform source whose `dc` is
  given is printed `type="dc"` with its waveform fields (§4.5 item 8), and `plan.build` switches it for every
  `tran`: `alter instance("<v>") type="<wave>"` before the tran action (ahead of its options and sweep
  lines, §7.1) and `alter instance("<v>") type="dc"` after it, one `alter` per source [E17]. A source whose
  `dc` is derived stays in its waveform type [E38]. An alter of a derived source's `dc` makes it given from
  then on (`alter instance("<v>") type="dc" dc=<v>`); a sweep of it brackets that one analysis with
  `type="dc"` before and `type="<wave>"` after. Xyce needs no switching: its decks print `DC` and the
  waveform together (§4.5 item 8).
- **`set`** → an `options` action. Option changes between analyses are accepted by VACASK [E7].

### 5.4 Transient settings
- **errpreset.** The value is the `+aps=`/`++aps=` value, which overrides every tran [M ref19 p.35], else the
  tran's own `errpreset`, else `+errpreset=` [I, §14 q.25], else `moderate`:

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
    values after it [E]. `relref=pointlocal` → `relrefsol=relrefres=relreflte="pointlocal"`. Every name and
    value used here exists in VACASK's documentation (`cmd-options-relref.md`, `cmd-options-tran.md`) [E].
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
  - `skipdc=yes` → VACASK `icmode="uic"`, Xyce `UIC` [I, §14 q.47]; both engines start a `uic` tran from the given
    state (in(0) = 0, out(0) = 0.3 [E78]).
- **prevoppoint / useprevic** on VACASK [E11]:
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
VACASK save directives are specific to the analysis type and accumulate across the control block until
`clear saves`, and an analysis silently ignores a directive it does not read [VACASK `cmd-save.md`]: `v()` and
`i()` select signals for op, dc sweeps and tran; `ac` reads `dv()`/`di()`; `noise` reads `n()`/`nc()`; `acxf`
writes every `tf()`/`zin()` with no selection. After `save v(out) v('x1:mid') i(v1)` an ac still writes every
node and a noise every `n()` [E67]. So the deck's saves carry, per analysis type, the directives that analysis
reads (§7.1); one `save` statement may carry all of them, and `names` maps only the requested columns. An
analysis-level `save=`/`nestlvl=`, a noise analysis's `nc()` contributions and a state file's full solution
are bracketed: `clear saves` plus that analysis's saves before it, and the global saves again after it. On
Xyce each deck has its own `.PRINT`.

### 5.6 Noise without an input probe
Spectre's `iprobe` is optional [M ref19 p.181], but VACASK's `noise` needs `in=` (without it: "Instance ''
not found.", and the analysis aborts) and Xyce's `.NOISE` names a source [E19]. Without `iprobe`, `plan.build`
names an input `vamos_nin<k>` in the step, and `signals.resolve` adds `vamos_nin<k> (p n) isource dc=0`
across the output pair (for `oprobe`, its terminals) to its IR copy. A 0 A current source is an open circuit
with no noise, so `out` and every contribution are unchanged [E19]. `in` and `gain` are not written.
`vamos_nin<k>` is a vamos probe (§6.4): never an output, and acxf drops its `tf()`.

## 6. Signals and names – `netlist/signals.py`

### 6.1 What is saved [M ref19 pp.188-191]
- `options save=` defaults to `selected`: the user's saves, or `allpub` when no node voltage is saved.
  `selected` with only current saves is allpub plus every saved item: "if no node voltage is saved, then
  allpub is used" [M ref19 p.188], and items are saved "in addition to any outputs specified by the save
  parameter" [M ref19 p.518]; VACASK takes `save default` followed by the extra directives (`save default p(r1,
  i)` [E84]).
- **allpub** is computed from the original IR, before the insertions of §5.6 and §6.3. It holds every net at
  every level (down to `nestlvl` for `lvlpub`), and the currents of IR instances of kind `v` (`prim` vsource
  or iprobe) and kind `l`, at every level. It never holds vcvs/ccvs flows, `vamos_*` instances or nets, `r` = 0
  shorts (§4.5 item 10) or device-internal unknowns: VACASK's `save default` writes all of these
  (`d1:a_int`, `d1:implicit_equation_0`, `e1:flow(br)`, `I2:vx:flow(br)`) [E84], and they are ignored by
  construction (§6.4). `all`/`lvl` would add device-internal nodes; in v1 they are not written (note).
- `none`: one node voltage, the first in sorted order (note). `nooutput`: no data files.
- `currents=selected` is the default. `all` or `nonlinear` → warning: only the explicitly saved currents are
  written. `subcktprobelvl>0` and `pwr≠none` → warning.
- The other output options [M ref19 pp.189-191]: `useprobes=yes` → note (vamos measures currents with its
  own probes either way); `useterms` → note (it names only the automatic `currents=all`/`subcktprobelvl`
  saves, which are warnings; "Does not apply to individual save statements"); `saveahdlvars` ≠ `selected` →
  warning: AHDL variables are not written; `subcktiprobes` → ignored; `savefilter=rc` → warning: RC-only
  nodes are kept; `saveports=yes` → mapped: wildcard saves with `subckt=` also match the ports; `wfdebug` →
  ignored.

### 6.2 save statement items [M ref19 pp.517-519]
- Items: a node (`out`, `OpAmp1.comp`, integer names), `X:term` (a terminal name of §3.8 or its 1-based
  index [M ref19 p.518]), `X:currents` (all terminals; a two-terminal component gives only the first [M]),
  `X:oppoint`, `X:<opvar>`, `X:pwr` (both warnings: not written), a bare component name (its currents and, as
  a warning, its operating point), and the wildcards `*` and `?` with `depth`, `sigtype`, `devtype`,
  `subckt` and `exclude`.
- An individual item is written in the form the user wrote it (`L1:1` stays `L1:1`, `M1:d` stays `M1:d`)
  [I, §14 q.37]. `X:currents` expansions use names for device terminals and 1-based indices for subckt
  terminals (`useterms=default` [M ref19 p.190]).
- The statement's own options: `ports=yes` → mapped (as `saveports`, §6.1); `filter=rc` → warning (§6.1);
  `compression=` → note; `time_window=[t1 t2 t3 t4 …]` filters the written points to those intervals.
- Items are resolved against the flattened IR. When a name is both a node and an instance, the node wins
  [M UG p.61]. An item that matches nothing gets a warning and is not passed on: Xyce aborts on an unknown
  node, and VACASK fails every analysis but exits 0 [E28]. A save that fails to bind is never written as a
  constant: VACASK's `strictsave=0` would bind it to 0 (column `nosuch` all zero, rc 0), so vamos pins
  `strictsave=2`, which reports every failed binding and still lets an analysis ignore the directives of
  another (an `nc()` before an `op`) [E84].

### 6.3 Currents
- vsource `V1:p` → VACASK `i(v1)` (column `v1:flow(br)`) / Xyce `I(V1)`. The sign matches Spectre's "positive
  into the terminal" [M ref19 p.518]: −1 mA for 1 V across 1 kΩ [research E], and −0.5 mA for 1 V across
  2 kΩ on both engines [E]. `V1:n` is the negation.
- inductor `L1:1` → `i(l1)` (column `l1:flow(br)`) / `I(L1)`; iprobe `I1:in` → `i(i1)` / `I(VI1)`: +1 mA for
  1 mA into terminal 1 or `in`, on both engines [E36]. `L1:2` and `I1:out` are the negation.
- **Native currents.** A resistor printed as a resistor: `R1:1` → VACASK `p(r1, i)` (column `r1.i`, which
  `vacask._Deck.isave` already saves for `resistor` and `sp_resistor`, `vacask.py:802-813`) / Xyce
  `I(R1)`, both positive into terminal 1 [E84]; `R1:2` is the negation. The output port of a vcvs or ccvs
  (`E1:p`) → `i(e1)` / `I(E1)` [E84]. The `r` = 0 short (§4.5 item 10) gives its own current, an infinite
  resistor 0 (§3.8), and Xyce's `G` form of `isnoisy=no` a probe [I, §14 q.47].
- **Every other terminal** (capacitor, diode, BJT, MOS, JFET, a source's `sink`/`src`, a subckt-instance
  port, a Verilog-A port): `signals.py` inserts a 0 V probe source `vamos_tp<k>` in series with the terminal,
  in a copy of the IR. The terminal moves to a new node `vamos_tn<k>`, and the source runs from the original
  node to the new one. Its branch current is the current into the terminal, Spectre's sign, on both engines
  alike. Fewer probes mean fewer added nodes; `names_for` records `<path>.i`, `I(<printed>)` or the probe's
  flow.

### 6.4 Forward name map
`signals.resolve` lists, per step, the requested signals as `(Ref, Spectre name, psf type)`; `render` records
each Ref's engine column in `names` (§4.5 item 6). Columns outside the map, such as VACASK's
`d1:implicit_equation_0` [E] or device-internal nodes, are never written. The psf type is the PSF TRACE type
name (§8.3, §8.4): `V` for a node voltage; `I` for a current; `V/sqrt(Hz)` for noise `out`, and for `in`
with a vsource input (`A/sqrt(Hz)` with an isource input); `V/V` or `V/A` for `gain`, by the input's kind
[I, §14 q.48]; and for a per-instance noise Ref the instance's STRUCT type name (its model, else its master,
a collision suffixed, §8.4). `signals.resolve` computes them from the IR, so `results.py` needs no IR (§10).

| quantity | Ref | VACASK column | Xyce `.PRINT` column | Spectre name |
|---|---|---|---|---|
| top-level node `out` | `('v', 'out')` | `out` [E] | `V(OUT)` [E] | `out` |
| node `mid` in instance `x1` | `('v', 'x1.mid')` | `x1:mid` | `V(X1:MID)` [E] | `x1.mid` (original case) [S] |
| node `Mid` in instance `I2` | `('v', 'I2.Mid')` | `I2:Mid` [E84] | `V(XI2:MID)` [E84] | `I2.Mid` |
| vsource current | `('i', 'v1')` | `v1:flow(br)` [E] | `I(V1)` [E] | `V1:p` |
| inductor, iprobe current | `('i', 'l1')`, `('i', 'i1')` | `l1:flow(br)`, `i1:flow(br)` [E36] | `I(L1)`, `I(VI1)` [E36] | `L1:1`, `I1:in` |
| resistor current | `('i', 'r1')` | `r1.i` [E84] | `I(R1)` [E84] | `R1:1` |
| probed terminal | `('i', 'vamos_tp<k>')` | `vamos_tp<k>:flow(br)` | `I(VAMOS_TP<K>)` | `<inst path>:<term>`, e.g. `I2.Idiffpair:in` [S] |
| sweep variable | — | the sweep's name, its `SweepLevel.id` (§5.2) [E] | the `sweep` column / one plot per `.STEP` [E] | `SweepLevel.label` (§8.3) |
| AC frequency | — | `frequency` | `FREQUENCY` | `freq` [S] |
| noise total | `('onoise',)` | `onoise`, V²/Hz [E] | `ONOISE`, V²/Hz [E] | `out` = √, V/sqrt(Hz) [S] |
| noise gain, input noise | `('gain',)`, `('inoise',)` | `gain` [E] | `INOISE` [E] | `gain`, `in` (§8.4) |
| per-instance noise | `('noise', 'r1')` | `n(r1)`, `n(r1,thermal)` [E] | `DNO(R1)`, `DNO(XI2:R3)` [E73, E84] | STRUCT `R1` with `rn`, `total` [S] |
| xf | `('tf', 'v1')` | `tf(v1)` [E] | — | `V1:p` for a vsource [M UG p.113]; an isource: §14 q.15 |

- The hierarchy separator is `.`, and the case is the original (Spectre is case sensitive [M]).
- An inline subckt's component named like the subckt takes the instance's name (`q1`, not `q1.npn_mod`) [M UG
  p.111].
- With `+escchars`, characters outside `[A-Za-z0-9_.:!]` are backslash-escaped by the PSF string encoder
  (§8.3), never in the name beforehand (§14 q.12).
- **Columns come only from `names`, by exact name.** `results.py` resolves every column with `Raw.exact` on the
  text `names_for` recorded (§4.6); Xyce `.PRINT` columns may also be taken by position, since they follow
  the printed order after `TIME`/`FREQUENCY`/the sweep. `Raw.index`, `column`, `at` and `crossings`
  (case-folding, aliasing) are never used for Spectre outputs: with nodes `A` and `a`, VACASK writes both and
  `index` cannot tell them apart [E84]. On Xyce such a pair is the existing `collide()` error (`xyce.py:387-393`).
- **Xyce column texts are built exactly** (§4.5 item 15): net `a.b.n` → `V(<printed_path(a.b)>:<N>)`
  upper-cased, an element current → `I(<printed_path(path)>)`, noise → `DNO(<printed_path(path)>)`; IR `I2.Mid`
  is `V(XI2:MID)` [E84]. For allpub (`SignalMap.allpub`), where listing every node would make huge `.PRINT`
  lines, the decks print `save default` (VACASK) or `V(*)` plus the allpub currents (Xyce), and `names_for`
  lists the expected column of every allpub signal; the reader takes those with `Raw.exact`. An expected
  column that is absent is a warning naming the signal. Columns no signal claims (device internals, probe
  nodes, VACASK's extra default flows) are ignored.
- **Order.** The SignalMap order is the PSF order; engine column order never reaches the PSF. Explicit items
  come in statement order (duplicates dropped, the first wins); allpub signals are sorted case-insensitively
  by Spectre name [I, §14 q.48: the one sample, `rushikesh` (19.1), was converted from binary PSF by
  Cadence's tools (§0), so its order may be the converter's; the native `joop-banaan.tran` lists explicit
  saves unsorted, E101]; noise STRUCTs come in IR instance order, then `out`,
  `gain` and `in`. VACASK sorts `save default` columns bytewise and keeps explicit saves in order, and Xyce's
  `V(*)` has its own order [E84].
- vamos's own probe sources (§5.6, §6.3) are never outputs. **xf** writes one trace per IR independent source:
  kind `v` with `prim` vsource, and kind `i`, at every level. VACASK's acxf writes `tf()`/`zin()` for every
  vsource and isource in the deck, a 0 V iprobe stand-in, subckt vsources and `r` = 0 shorts included, and it
  does give isource transfer functions (`tf(i1)` = 500 for 1 kΩ‖1 kΩ) [E84]; xf never writes iprobes, shorts,
  `vamos_*` sources, `zin` or `yin`.

## 7. Engines

### 7.1 VACASK: one deck, one run
- `vacask.render(nl, …, plan=…, sigmap=…, names=…)` writes `<run>/vamos.sim`, run in the run directory as
  `engines.vacask_bin()` `vamos.sim`, with `engines.env_for("vacask", None, dict(os.environ))` (§10:
  `SIM_OPENVAF` and `SIM_MODULE_PATH`; no co-simulation bridge directory). The Verilog-A `.osdi` files come from
  §1 step 10, compiled by `engines.openvaf()` with the same environment. `env_for` sets `SIM_OPENVAF` to the
  right openvaf-r even when the caller inherited `/opt/openvaf-r/openvaf-r`, whose OSDI descriptor (328 bytes)
  VACASK refuses [E68]. Every engine run, the `-norun` check and every Verilog-A compile go through
  `engines.vacask_bin()`/`xyce_bin()`/`openvaf()` and `env_for` (§0's [E] row, E68 and E92: the device libraries
  and the Xyce library those resolve to).
- Analysis names in the deck are `vamos_a<k>` (`AnalysisStep.id`) and sweep names `vamos_w<k>`
  (`SweepLevel.id`), both given by `plan.build` (§5.2). That avoids quoting, reserved words and file-name
  collisions on case-insensitive filesystems. The plan maps them back.

Control block of the worked example in §7.3:

```
control
  abort except analysis
  options rawfile="binary" strictoutput=0 strictsave=2 tran_lteimplicit=0 temp=27.0 tnom=27.0 reltol=0.001 vntol=1e-06 abstol=1e-12 gmin=1e-12
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
error ("expecting sweep or analysis") [E16], so per-analysis `options` lines and the source-state `alter`s of
§5.3 go before the sweep statements, and the restoring ones after the analysis [E].

| Spectre | VACASK |
|---|---|
| `dc` (no target) | `analysis a op` |
| `dc` with a target | `sweep … ` + `analysis a op` (a nested sweep for an enclosing block) |
| `ac` frequency sweep | `analysis a ac from= to= mode= points= \| step= \| values=` (§5.2) |
| `ac` parameter sweep at `freq` | `sweep …` + `analysis a ac values=[freq]` |
| `noise` | `analysis a noise out="p" \| out=["p","n"] in="<src>" …` [E], `in` being `vamos_nin<k>` when the netlist gives none (§5.6); per-instance contributions from `save nc(<inst>)` for every noisy IR instance (hierarchical paths quoted, `nc('I2:r3')`), bracketed by `clear saves` (§5.5): `nc` gives exactly `n(inst)` and its contributions, where `save full` gives every instance's and widens every later analysis [E84] |
| `xf` | `analysis a acxf out=…` [E] |
| `tran` | `analysis a tran step= stop= [start=<outputstart>] maxstep= [icmode=] [ic=] [nodeset=]` |
| `sweep {…}` | sweep statements with `continuation=0` (§5.2) before every child |
| `alter`, `set`, `options` | `alter instance(…)`, `alter model(…)`, `var`, `options` |
| `ic` / `nodeset` | `ic={"n", v, …}` on `tran` only; `nodeset={…}` on every analysis that solves an operating point (op, swept op, ac, noise, acxf, tran) |
| saves | `save default` (allpub, with the extra directives of §6.1); otherwise per analysis type, from the SignalMap: `v()`/`i()`/`p(r, i)` for op, dc sweeps and tran; `dv()`/`di()` for ac; `nc(inst)` for noise (§5.5) [E67] |

**Failures.**
- An analysis succeeded only if all of these hold:
  - its rawfile `vamos_a<k>.raw` exists;
  - its `No. Points:` field is filled and equals the rows read (`rawfile.scan`: the declared count is not None
    and equals the count). VACASK fills the field only when the analysis completes, and leaves it blank after an
    abort, a failed sweep point or a signal [E27, E40, E66];
  - it holds the planned groups (for a swept analysis, the product of the outer counts);
  - for a tran, the last time of every group equals `stop` (relative 1e-9).

  Otherwise the analysis failed (exit status 1), whatever VACASK printed or returned [E28, E66]. Under
  `strictoutput=0`, which vamos sets, VACASK leaves a rawfile for every failed analysis: a failed op, ac or
  noise leaves a header-only file, and a tran aborted part-way leaves its rows up to the abort, one group, rc
  0 [E66]; revision 2's rule (the rawfile exists and holds the planned groups) would have accepted that
  truncated tran. The complete leaves of a failed sweep are still written (E27).
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
- `rawfile.scan` counts points and groups, and `rawfile.iter_rows` streams the columns that `names` maps,
  each found with `Raw.exact` (§4.6, §6.4); after a signal, `rawfile.fix_points` (streamed) first repairs the
  count (§2.7).
- A swept analysis is one plot: the sweep columns come first and the points are concatenated, with no
  `Dimensions` header [E5]. Consecutive sweep values may be equal (`values=[1k 1k 2k]`) [E5], so the split never
  goes by value. It uses the known number of outer points and the restart of the inner axis: time and
  frequency increase within a point, and an `op` contributes one row per point. It checks that the number of
  groups equals the product of the outer counts.

### 7.2 Xyce: one deck per analysis
- Xyce runs one analysis type per netlist [research E: "Analysis type TRAN and print type AC are
  inconsistent"].
- Each `analysis` action becomes `<run>/vamos_a<k>.cir`. It is rendered from a copy of the IR with the
  alters and options up to that point applied (state replay: the `alter` actions' targets and values, and the
  step's `AnalysisStep.options`, §5.1), with `xyce.render(…, step=…)`, and run sequentially as `<xyce>
  vamos_a<k>.cir`, where `<xyce>` is `engines.xyce_bin()` (`VAMOS_XYCE`, else
  `/usr/local/src/xyce-build/src/Xyce` when executable), else `tools.find_real("Xyce")`, always with
  `engines.env_for("xyce", None, dict(os.environ))`; the provenance row names the same path (§1). Run without
  that environment, `/usr/local/bin/Xyce` loads a 2026-09-29 `libxyce.so` that lacks `BADMOS3` ("No model
  parameter BADMOS3 found …, parameter ignored", rc 0); with it, both launchers load the 2026-10-03 library
  and know it [E68, E96]. A smoke test asserts that the Xyce in use knows `BADMOS3` (§11 T1).
- **Parameter check.** Xyce ignores an unknown model parameter with only a warning ("No model parameter …
  found"), and a plain run exits 0; only a `.STEP` of it is an error [E43]. Before the first run, `Xyce -norun`
  on the first deck checks the netlist and its Device Count Summary; the output of every deck run is then
  scanned for the same patterns (`No model parameter … found`, `Unrecognized parameter`; binned-card bounds
  exempt). A match is exit status 2, naming the alter or sweep that introduced it. The patterns live today in
  `xyce.py`'s private helpers (`_messages`, `_bad_param`, `_UNREC`, `_NOPARAM`, `_nobin`, `xyce.py:984-1092`),
  and `smoke()` renders and runs its own deck, so S3 exports them as `xyce.output_problems(text, nl)` and
  `xyce.device_summary_problems(text, nl)` (every `xyceModelGroup` module listed under its own name, §4.5
  item 13) (§10), which `smoke()` and phase 2 both call.

| Spectre | Xyce |
|---|---|
| op | `.OP` + `.PRINT DC FORMAT=RAW FILE=vamos_a<k>.raw <columns>` [E]. Under two or more `.STEP`s: `.param vamos_op=0` + `.DC vamos_op LIST 0`, because `.OP` there stops with "Analysis mode 4 is not available" and a segfault, while one `.STEP` with `.OP` works [E20] |
| `dc dev=V param=dc` | `.DC V start stop step` / `.DC V LIST …` [E20] / `.DC DEC V …` (integer spans only, §5.2) [E21] |
| `dc param=temp` | `.DC TEMP …` [E] |
| `dc param=<top-level param>` | `.DC p …` [E] |
| `dc dev=R param=r`, `mod=M param=x` | `.DC R1:R …` / `.DC M:X …` (hierarchical `X2:R1:R` too): one DC plot, like every other dc sweep [E20] |
| `dc sub=X1 param=w` | X1's override becomes `w={vamos_x_x1_w}`, with `.param vamos_x_x1_w=<nominal>`, swept by `.DC vamos_x_x1_w …`: an X-line override follows a swept `.param` [E20]. The `.param` is global, so an X line inside a subckt that more than one instance path reaches would sweep every path: an error in v1 (§5.3) |
| `dev=` a field of an independent source | the field prints through a generated `.param vamos_x_<src>_<field>` (as for `sub=`), which a derived `DC` expression reads too; `.DC` and `.STEP` of it work, and so does `.STEP V1:V1` [E71]. A source inside a subckt that more than one instance path reaches: an error in v1, as for `sub=` (§5.3) |
| `ac` | `.AC DEC n f1 f2` (integer spans, §5.2) / `.AC LIN N+1 f1 f2` / `.AC DATA=<t>` with `.DATA` [E] |
| `ac` parameter sweep at `freq` | `.STEP …` + `.AC DATA=<t>` with one frequency [E] |
| `noise` | `.NOISE V(p,n) <src> …` (frequencies per §5.2; `DATA=` works [E21]) + `.PRINT NOISE FILE=vamos_a<k>.prn ONOISE INOISE DNO(<printed path>)…` (hierarchical `DNO(X1:R1)` works [E73]); the input source prints as `AC 1 0` (§8.4) [E19]. Xyce refuses RAW for noise ("Noise output cannot be written in PROBE, RAW or Touchstone") and writes a standard table [E12], read by `rawfile.read_prn`. Under `.STEP` the table's Index column restarts at 0 for every step, with no label or separator [E33], so `read_prn` splits it at each reset and takes the step values from the deck's `.STEP` order |
| `xf` | error |
| `tran` | `.TRAN step stop [outputstart [maxstep]] [UIC]`; `.OPTIONS TIMEINT METHOD=TRAP\|GEAR [MAXORD=n]` (§5.4) [E30] |
| sweep blocks | `.STEP` lines innermost first: Xyce's first `.STEP` is the fastest loop, the reverse of VACASK's order [E20]; the leaves are decoded in that order |
| options `temp tnom gmin` | existing emitter rules (temp and tnom are always set, §3.10); `$tnom` substituted (§3.5) |
| `ic` statements, `readic` | `.IC` only in a tran deck whose `ic` is `all` or `node`: Xyce applies `.IC` to every DC solve [E18], and Spectre's `ic` is a transient initial condition [M UG p.200]; nodes spelled through `printed_path` (§4.5 item 15) |
| `nodeset`, `readns` | `.NODESET` in every other deck; a tran deck with `.IC` drops it with a note, because Xyce aborts on both [E18] |

- **Output.** `.PRINT … FORMAT=RAW FILE=` writes exactly the printed columns (`V(OUT)`, `I(V1)`), where `-r`
  writes every node [E13]. Each `.STEP` point is one plot, with the step value in its plot name [E12].
- **Failures.** A non-zero exit [E: a singular `.OP` exits 1], or a print file whose `No. Points` is blank or
  short, means the analysis failed (exit status 1): a tran that fails part-way exits 1 with a header-only
  print file [E66]. "Netlist error" or a parse error in the output means exit status 2. Either way the decks
  that finished are converted (§2.7).

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
- **Reference values** [E74, both engines]: `out(5 ms)` = 0.98173 for `rval` = 1k (VACASK 0.981735, Xyce
  0.981725) and 0.86469 for 2k; `out(1 Hz)` = 0.99996 − j0.00628 (identical on both); 61 AC points ending
  exactly at 1e6.

## 8. Outputs

### 8.1 Results directory and file names

| analysis | modern (default) [S 23.1] | legacy (`--vamos-psf-names=legacy`) [M UG pp.230-232] |
|---|---|---|
| `dc` (op or sweep), name n | `n.dc` | `n.dc` |
| `ac` / `noise` / `xf` | `n.ac` / `n.noise` / `n.xf` | same |
| `tran` | `n.tran.tran` | `n.tran` |
| child c of sweep s, point i | `s-00i_c.<ext>` | `s_00i_c.<ext>` |
| sweep parent (one per child) | `s_c.sweep` | — [I, §14 q.48] |
| nested sweep t in s | `s-00i_t-00j_c.<ext>`; parent `s-00i_t_c.sweep` | — |
| SPICE mode | `opBegin.dc`, `srcSweep.dc`, `frequencySweep.ac`, `timeSweep.tran.tran` [I, §14 q.9] | `timeSweep.tran` [M] |

The point index has 3 digits [S] (more for ≥ 1000 points [I, §14 q.48]). The psf format family is irrelevant to the
names: psfascii is always what is written. `--vamos-psf-names` is a spectre-only vamos option (§10 optable).

### 8.2 `logFile` [S]
The layout, field per line, is that of a real 23.1 `logFile` [S: psf-parser `tests/data` `logFile`]. It is
ASCII even in a psfbin run [S]:
```
HEADER
"PSFversion" "1.00"
"Log Generator" "drlLog rev. 1.0"
"Log Time Stamp" "<time.asctime()>"
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
"mysweep-000_ac1-ac" "analysisInst" (
"ac"
"mysweep-000_ac1.ac"
"PSF"
"mysweep_ac1-sweep"
("freq")
"AC Analysis `mysweep-000_ac1': freq = (1 Hz -> 1 MHz)"
) PROP(
"data_type" "swept_scalar"
"sweep_tree_type" "leafNode"
"R1:r" 1000.00
)

END
```
- Each entry: key, `"analysisInst"`, `(`, one field per line, `) PROP(`, one property per line, `)` and a blank
  line. Monte Carlo parents have no PROP and no blank line [S].
- **Keys:**
  - `<name>-<type>`: `myop-dc`, `mytran-tran`; the type is `tran` although the file is `.tran.tran`;
  - sweep parents `<s>_<c>-sweep`, leaves `<s>-NNN_<c>-<type>`;
  - Monte Carlo (phase 3): nominal run `<c>-<type>`, then parents `<m>_<c>-montecarlo` without a PROP, then
    leaves with `"iteration" k`.
- **Sweep trees** [S]: one top-level parent per leaf analysis, `s_c-sweep` (file `s_c.sweep`, sweepVariable the
  outer sweep variable, PROP `sweepNode` only). For a nested sweep t inside s, per outer point i a nested parent
  `s-00i_t_c-sweep`, whose parent is `s_c-sweep` and whose PROP is `sweepNode` plus the outer value (`"R1:r"
  1000.00`); its leaves `s-00i_t-00j_c-<type>` have that parent, and their PROP holds only the innermost value
  (`"C1:c" 1.00000e-06`), not the outer one again.
- **PROP:**
  - `data_type` is `scalar` for `dc` (op and sweep alike), `swept_scalar` for `ac`, `tran` and `xf` [I, §14
    q.48 for `xf`], `swept_struct` for `noise`, and `struct` for `info`; parents carry none;
  - parents carry `"sweep_tree_type" "sweepNode"`;
  - leaves carry `data_type`, then `"sweep_tree_type" "leafNode"`, then the swept value.
- **Order:** execution order, with sweeps point-major as Spectre runs them: all parent entries of a sweep,
  then per point its leaves and nested parents [S]. vamos re-orders VACASK's analysis-major results.
- **Writing:** `logFile` is written once at the end, after every exit status (§2.7). It lists the analyses
  that finished, and after status 3 the interrupted one. It is always PSF ASCII. No `logFile` is written
  for the nutmeg formats (§8.5).
- **Date and time stamp.** `"date"` is built from fixed English tables, never `strftime` (padded and
  locale-dependent: `%I:%M:%S %p, %a %b %d, %Y` gives `02:56:17 PM, Thu Oct 01, 2026` [E86]): `"%d:%02d:%02d %s, %s %s
  %d, %d"`, the hour on a 12-hour clock unpadded, AM/PM, the day name Sun…Sat, the month Jan…Dec, the day
  unpadded (`1:07:28 PM, Tue Feb 2, 2021` in the native 19.1 `joop-banaan.dc`; all 20 dated samples [E86,
  E101]; one 21.1 file has `Thur`, §14 q.34).
  `"Log Time Stamp"` is `time.asctime()`, whose names do not depend on the locale.
- **Format fields.** `"simulator" "spectre"`, `"Log Generator"`, `"simMode"` and `"signalNameType"` are
  data-format fields that readers may key on, so they keep Spectre's values. They are constants of
  `output/psf.py`, not banner keys, so a banner profile (`none` included) never changes them. `"version"` is
  always vamos's, never a Spectre version string (`VAMOS_PLAN.md` §4b).

### 8.3 PSF ASCII data files [S]
- **Grammar:** `HEADER` named values, `TYPE`, `SWEEP`, `TRACE`, `VALUE`, `END` (the psf_utils grammar,
  matching every sample).
- **Strings.** One encoder, `psf_string(s, escchars)`, writes every PSF string from the unescaped name or text.
  It puts a backslash before `"` and `\`; under `+escchars`, also before every character outside
  `[A-Za-z0-9_.:!]`. `+escchars` is never applied to a name beforehand, which would double the backslash
  before `<`. Readers decode backslash-x as x. A title containing `"` written unescaped would make the file
  unparsable. Tags: [I, §14 q.12] for the quote and backslash escapes (the 19.1 `"design"` sample revision 2 cited is a
  hand-edited psf_utils fixture [E86]); [S: `dan-zilla`, 19.1, Cadence-converted, `"net4\<0\>"`] for `<` and
  `>`.
- **Header**, per analysis kind [S 23.1.0.242 unless marked]:

| kind | keys |
|---|---|
| every file | `PSFversion`, `simulator`, `version`, `date`, `design`, `analysis type`, `analysis name`, `analysis description`, `xVecSorted`, `tolerance.relative` |
| `dc` (op and sweep) | + `reltol`, `abstol(V)`, `abstol(I)`, `temp`, `tnom`, `tempeffects`, `gmindc` |
| `ac`, `xf` | + `start`, `stop`, `"operating point producer"` (the analysis's own name in every 23.1 file; every engine analysis solves its own op) |
| `noise` | + `"operating point producer"`, `"output" "pair of nodes"`, `"ground" "0"`, `"positive output signal"`, `"negative output signal"` (the `oprobe=` form: §14 q.35) |
| `tran` | + `start`, `outputstart`, `stop`, `step`, `istep` (1e-3 in every sample), `maxstep`, `ic`, `useprevic`, `skipdc`, `reltol`, `abstol(V)`, `abstol(I)`, `temp`, `tnom`, `tempeffects`, `errpreset`, `method`, `lteratio`, `relref`, `cmin`, `gmin` (19.1 adds `rabsshort` when set) |
| sweep and Monte Carlo parents | the common keys only |

  `xVecSorted` is `ascending` for ac, noise, xf, tran and Monte Carlo parents, `unsorted` for dc, `unknown`
  for sweep parents and info. `psfcheck` enforces this table (§11).
- **TYPE:** the fixed 23.1 list for the analysis family, copied verbatim from the samples (`tb_myop.dc`;
  `myac.ac` for the COMPLEX variants; `tb_mysweep_ac1.sweep` for parents, without `LogicV`): `sweep`, `LogicV`,
  `V`, `I`, `MMF`, `Wb`, `Temp`, `Pwr`, `U`, `V/Sec`, `I/Sec`, `1/Sec`, `HOD`, `Q`, each with its PROP (`"V"
  FLOAT|COMPLEX DOUBLE PROP("units" "V" "key" "node" "tolerance" …)`, `"I" … "branch"`, `"sweep" FLOAT DOUBLE
  PROP("key" "sweep")`), plus the types the file needs (noise STRUCTs, §8.4). Readers resolve types by name,
  and both third-party readers accept a file with only the used types [E83]; the full list costs nothing and
  removes any dependence of a reader on fixed type names [I, §14 q.48].
- **SWEEP:** `"time"` (s), `"freq"` (Hz), or the dc sweep variable, each with `PROP("sweep_direction" 0 "units"
  … "plot" 0 "grid" g)`. `grid` is 1 for a linear sweep and 3 for a logarithmic one [S]; `sweep_direction` is
  0 in every sample [S] (a descending sweep: §14 q.36). The dc sweep variable is the parameter name: `"dc"` for
  a source [S], `"temp"` and the netlist parameter name [I, §14 q.48]. Parents use `"<dev>:<param>"` (`"R1:r"`,
  units `"Ohm"`) [S], and Monte Carlo parents `"iteration"` (units `"real"`) [S]. `plan.build` records each
  sweep's variable name and units in its `SweepLevel` (`label`, `units`, §5.2), which needs the swept
  device's primitive; `results.py` reads them there and gets no IR (§10).
- **Units:** time `s`; freq `Hz`; a vsource's `dc val0 val1 ampl sinedc`: `V` [S for `dc`]; the same isource
  fields: `A`; resistor `r`: `Ohm`, capacitor `c`: `F` [S]; inductor `l`: `H`; `w` and `l` on any device:
  `m`; `temp`: `C`; netlist and model parameters: `""` [I, §14 q.48]; `iteration`: `real` [S].
- **TRACE:** a current trace is `"V1:p" "I" PROP("units" "A")` [S 19.1; the 15.1 files omit the PROP].
- **VALUE:**
  - swept: the sweep value, then one `"sig" v` per trace, repeated per point. Within each point the values
    follow TRACE order exactly: position-based readers such as psf_utils assign values by position [E83];
  - op: `"sig" "V" v`, and for currents `"V1:p" "I" v PROP("units" "A")` [S];
  - complex values are `(re im)`.
- **Numbers.** VALUE, header and TYPE PROP reals are `%.15e` [S 23.1.0.242; the header and TYPE PROP reals
  are `%#g` up to 23.1.0.063]; `logFile` PROP reals are `%#g` even in 23.1.0.242 (`"R1:r" 1000.00`,
  `"C1:c" 1.00000e-06`) [S]; integer properties (`sweep_direction`, `plot`, `grid`, `measdgt`, `ingold`) are
  integers. `options precision="<C conversion>"` applies to VALUE reals only. It is accepted when it matches
  `%[-+ #0]*\d*(\.\d+)?[eEgG]`, with `#` added for `g`/`G` so that a `.` or an exponent always appears
  (psf-parser rejects an integer token where FLOAT is declared [E83]); anything else gets a warning and
  `%.15e` is used. The manual's default `"%g"` (6 digits) [M ref19 p.192] is not followed, because every
  sample says otherwise [S]. A non-finite value is written `nan`, `inf` or `-inf`, with a warning naming the
  signal [I, §14 q.48].
- **Descriptions** [S]: `` DC Analysis `myop' ``, `` DC Analysis `mydc': Vin:dc = (0 V -> 2 V) ``,
  `` AC Analysis `myac': freq = (1 Hz -> 1 MHz) ``, `` Transient Analysis `mytran': time = (0 s -> 500 ms) ``,
  `` Noise Analysis `noiva': freq = (4 Hz -> 4.19 MHz) ``, `Sweep parent`, `Monte Carlo parent`. Numbers are
  `%g` with an SI prefix and the units above (`SweepLevel.units`). The variable is `<inst>:<param>` for
  `dev=`, `temp`, `<param>` for a netlist parameter and `<model>:<param>` for `mod=` [I, §14 q.48]
  (`SweepLevel.desc`).

### 8.4 Noise and xf transforms
- **Noise [S layout, E values]:**
  - `out` = √onoise, in V/sqrt(Hz). A 1 kΩ/1 kΩ divider gives onoise = 8.288e-18 V²/Hz (VACASK) and 8.2879e-18
    (Xyce) [E8, E12], so out = 2.879e-9.
  - Types [S `noiva`]: `out` has type `"V/sqrt(Hz)"`, declared `FLOAT DOUBLE PROP("units" "V/sqrt(Hz)" "key"
    "noise")`, and `"V^2/Hz"` is declared likewise; STRUCT members carry only `PROP("units" "V^2/Hz")`. A
    STRUCT value is its name, `(`, one member per line, `)`. `total` is the sum of the members, and `out` =
    √(sum of the totals) [S: 2.5e-11 + 1.6576e-22 = 2.500000000016576e-11, out = 5.000000000016576e-06].
  - Per instance there is one STRUCT (V^2/Hz) with members from `n(inst,contrib)` plus `total`. The resistor
    contribution `thermal` is renamed `rn` [S]; a resistor card with flicker noise adds `fn` [S]; a Verilog-A
    module keeps its own contribution names (`flicker`, `thermal`) [S]. Other devices keep VACASK's
    contribution names (a diode's are `rs id flicker rsw idsw flickersw` [E84]; note; §14 q.14). The STRUCT
    trace names are Spectre instance paths (`I2.r3`) [I, §14 q.48]; TRACE lists the STRUCTs in IR instance
    order, then `out`, `gain` and `in` [S `noiva`: the STRUCTs first, `out` last; I, §14 q.48 for `gain` and
    `in`]. Each distinct member set is a STRUCT type named after the instance's model, or its master when it
    has none, with `PROP("key" "inst" "master" "<master>")`: `"rref"` for a resistor with model `rref`,
    `"resistor"` for one without, `"res_va"` for a Verilog-A module [S: 15.1 `noiva`]; a name equal to a fixed
    type name (`V`, `I`, `sweep`, …) or to another member set's type gets a suffix `_<k>`, with a note [I, §14
    q.48]. `signals.resolve` computes these type names from the IR and records them as the noise Refs' psf
    types in the SignalMap (§6.4). On Xyce there is only the `total` member (from `DNO`), with a note.
  - With an input probe: `gain` = √(power gain) and `in` = out/gain = √(input-referred PSD) [M: IRN =
    sqrt(No²/G²)] [I, §14 q.48 for the units and type names of `gain`/`in`, §6.4]. On VACASK the power gain
    is its `gain`, which does not depend on the source's `mag` [E19]. On Xyce, gain = √(ONOISE/INOISE) and
    in = √INOISE, with the input source printed `AC 1 0` whatever its netlist `mag`/`phase` (noise does not
    use them): Xyce divides
    INOISE by that source's AC magnitude squared, so Spectre's default `mag=0` would give INOISE = 8.3e2 V²/Hz
    for the divider [E19]. `F` and `NF` need a noisy input `port`, which is not in v1.
- **xf:** one complex trace per IR independent source (§6.4), `tf(<src>)`, named `<src>:p` for a vsource
  ("closed-loop gain, Av = Vdif:p" [M UG p.113]); isource names are §14 q.15. `zin`/`yin` are not written.

### 8.5 nutascii / nutbin
"In the Nutmeg format, the Spectre simulator writes output data to a raw file … In the other formats, the
Spectre simulator creates and writes output to a directory" [M UG p.229]: one rawfile at the results path,
and no `logFile` and no parent plots. One plot per analysis (sweep leaves included) in execution order; the
variables carry the §6.4 names and the types `time`, `frequency`, `voltage` and `current`; a noise plot has
the columns `frequency`, `out` and `<inst>:<member>` for every STRUCT member [I, §14 q.23]; a plot's name is
the PSF analysis description [I, §14 q.23]. If the path is an existing directory, the file is `<dir>/%C:r:t.raw`
(note). It is written by `output/nutmeg.py`, streaming its values (§10); the tests read it back with
independent readers (§11).

### 8.6 State files [M UG pp.203-204]
- **Write** (`write=` takes the first point of the analysis and `writefinal=` the last, from the
  full-solution saves of §5.4): the two-line `#` header of the UG's example, "# State file generated by
  Spectre from circuit file '<%C>'" and "# during '<analysis>' at <time>, <mon> <d>, <yyyy>." [M UG p.204:
  `# during 'stepresponse' at 5:39:38 PM, jan 21, 1992.`]: the time as in §8.2, no day name, the month in
  lower case, the day unpadded. Then `<Spectre name> <value>` per line, nets first (hierarchical with `.`),
  then vsource currents (`V1:p`). The value has 15 significant digits, `%.15g`, as in the example
  (`1.17406247989272`, `14.9900516233357`), but keeps the leading zero the example drops (`.588793510612534`);
  the UG's example (dated 1992) is the only sample, so the modern date and number layout is [I, §14 q.49].
- **Read** (`readns`, `readic`, and `read=`, the 5.1 name of `readic` [M UG p.204]): `#` starts a comment;
  node entries become nodesets or ics per §3.10. Current entries are dropped, with one note for `readns` or
  one warning for `readic` ("<n> current entries not applied"); §3.10's name checks cover node names only.

### 8.7 The `+log` file and the screen [M UG pp.32-33, 270, 274]
Every line a spectre run prints goes through `SpectreLog`, to stdout and/or the `+log`/`=log` file per
`log_mode` (§2.5): the provenance header, vamos's notes, warnings and errors as Spectre messages, the
unmapped-option report, `--vamos-verbose` command echoes, the run-directory note, the SIGUSR1 line and the
trailer. Nothing goes to stderr except `cli.py`'s usage errors before dispatch. `SpectreLog` does not use
`Console`, which always prints to the terminal (`console.py:15-26`), and the vcs-ams paths that print to
stderr (`optable.report_unmapped`'s lines, `cosim._cleanup`'s run-directory note, the verbose echo) are
routed through it, so `=log` stays file-only. `optable.unmapped_notes(job) -> List[Note]` returns the texts
`report_unmapped` prints today; `report_unmapped` becomes a thin printer over it for vcs and simv, and
`SpectreLog` formats the same notes as Spectre messages, so both personalities share one wording. The layout
comes from the banner profile `banners/spectre.json`:
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
<prog> completes with 0 errors, 0 warnings, and 0 notices
```
- **Messages** use the 5.1.41 form: `Error from <prog> during <phase>.`, `Warning from <prog> …`, `Notice from
  <prog> …`, each followed by the indented text [M UG pp.270, 274].
- **Counts** are vamos's notes plus the engine's failures and warnings. For `-maxwarns`/`-maxnotes` a message's
  id is its text with every number replaced by `#` and every quoted name by a single placeholder, so two
  warnings that differ only in a node name share an id [I, §14 q.40]; `SpectreLog` counts per (id, analysis)
  and, at the limit, prints one notice that further ones are suppressed [I, §14 q.40]. The limits are
  argv ⊕ defaults until the netlist is parsed, then the merged `Settings` (`SpectreLog.limits`, §2.2).
- **The last line** is `<prog> completes with N errors, M warnings, and K notices` after exit status 0 or 1,
  without a final period [M UG p.33], and `<prog> terminated prematurely due to fatal error.` after exit
  status 2 [M UG p.270]. After exit status 3 it is the latter as well [I, §14 q.18]. The modern form is §14 q.18.
- **Built-in text for every key.** `Banner.text` returns None for `none` or a missing key and an error string
  for an invalid template (`banner.py:72-84`), and `load_profile` returns `{}` when no profile exists
  (`banner.py:62-64`) [E79]. So `log.py` has built-in text for every key. A profile may change the layout of
  `version`, `subversion`, `error_block`, `warning_block`, `notice_block`, `trailer_ok` and `trailer_fatal`,
  but can never suppress them: under `none`, for a missing key, or for an invalid template (which also gets
  one note), the built-in text is used, and scripts that grep the trailer still find it. `none` drops only
  `run_start`, `inventory`, `analysis_banner`, `analysis_done` and `audit` (`VAMOS_PLAN.md` §4b: `none` prints
  only the provenance header). The built-in `-V`/`-W` text is `vamos <VERSION> (spectre personality)`.
- The profile is `load_profile("spectre", opts.get("banner"))` whatever the invoked name, with `{prog}` as a
  placeholder: `<prog>` is the invoked name (§2.1). That is the program the user ran, not vendor dialogue, and
  scripts grep that line.
- **Not printed:** VACASK's and Xyce's own banners, and Spectre's version banner (`VAMOS_PLAN.md` §4b).

## 9. ADE compatibility
- **A typical ADE command line** (cited verbatim by the research from a Cadence command-line tutorial):
  `spectre -64 butterworth.scs +escchars +log ./butterworth/psf/spectre.out -format psfascii -raw
  ./butterworth/psf +lqtimeout 900 -maxw 5 -maxn 5 +logstatus`. Every token has a disposition in §2.3, and
  the §2.3 table as `Opt` entries scans it with no unknown option and the right netlist [E80]. An APS variant
  adds `++aps +mt=4 +lqsleep 30 +lsusp -env ade` and `-format psfxl` (psfascii is written, note); 5.x ADE's
  "Use SPICE Netlist Reader (spp)" setting passes `+spp` (ignored, §2.3).
- **`input.scs` features** [S: Xyce_Regression `XDM/SPECTRE`, ten ADE netlists "Generated for: spectre",
  ic-5.1.41usr5; and the BAM control file [research V]]:

| feature | disposition |
|---|---|
| `simulator lang=spectre`, `global 0`, `include "…" section=tt` | mapped |
| `simulatorOptions options temp tnom scale=1.0 scalem=1.0 reltol vabstol iabstol gmin rforce maxnotes maxwarns digits pivrel checklimitdest psfversion sensfile cols` | §3.10 (mapped or noted) |
| `tran tran stop=… write="spectre.ic" writefinal="spectre.fc" annotate=status maxiters=5` | mapped (state files written from full saves; `tran_itl=5` on VACASK) |
| `finalTimeOP info what=oppoint where=rawfile`, `modelParameter info what=models …`, `element info what=inst …`, `outputParameter info what=output …`, `designParamVals info what=parameters …`, `primitives`/`subckts` | note: the `.info` files are not written; the `logFile` does not list them |
| `saveOptions options save=allpub` (after the analyses) | mapped (§6.1) |
| `wave_out options rawfmt=sst2` | psfascii written (note) |
| instance names `I0`, `I2`; bus nets `net\<3\>` | escapes unescaped (§3.2); written back escaped under `+escchars` (§8.3); Xyce spells `I2`'s nodes `XI2:…` (§4.5 item 15) |
| vpulse/vpwl cells with a separate "DC voltage" | `dc` given: the DC-type analyses use it (§3.8.1, §4.5 item 8) |
| `parameters` continued with `+` lines; case-distinct `r0`/`R0`; bare subckt ports; hierarchical terminal saves (`I2.R1:1`); `m=` on R/C/L and a subckt; sources with `wave=` and `file=`; a `statistics` block; a `bsimsoi` card | mapped, except `bsimsoi` (error, §0) and the statistics block (parsed, nominal run) |

- **Not vamos's job:** ADE itself writes `runObjFile`, `artistLogFile`, `variables_file` and `simRunData` [I, §14 q.17].
- **Open:** whether ADE probes `-V`/`-W` output (§14 q.16), accepts psfascii where it asked for psfxl (q.17),
  and parses `spectre.out` beyond the trailer (q.18).

## 10. Modules and APIs

Python rules as in `VAMOS_AMS_DESIGN.md` §2.3: `from __future__ import annotations`, Python 3.9, stdlib only,
no module run as a script. Dataclasses are valid on 3.9 when a field with no default never follows one with a
default (there is no `kw_only`) [E48]; every dataclass below defines and constructs on Cygwin Python 3.9.16 and
WSL Python 3.14.4, with the additions of the review of revision 3 (`Instance.folded`, `Netlist.va_modules`)
and every positional call site [E49]; the committed check is `tests/vamos/test_spectre_contract.py` (§11 T0,
§12 phase 0). Everything marked phase 0 is frozen before phase 1 (§12); "existing" names today's code.

```python
# vamos/netlist/ir.py  (phase 0; §4.1: each new field appended after its class's current last field,
# with a default; the new classes defined above Item; test_spectre_contract.py pins the field order)
Value = Union[Expr, List[Expr]]
#   Model    (after bin_index): bin_rule: Optional[str] = None; prim: str = ""
#   Source   (after code_uri):  spectre: Dict[str, Value] = field(default_factory=dict)
#   Instance (after origin):    prim: str = ""; folded: List[str] = field(default_factory=list)   # §4.1 15
#   Subckt   (after spelling):  inline: bool = False
#   Analysis (after origin):    name: str = ""; sweep: Optional[SweepSpec] = None;
#                               nodes: List[str] = field(default_factory=list);
#                               children: List["Analysis"] = field(default_factory=list); spice: bool = False
#   Netlist  (after notes):     dialect: str = "hspice"; saves: List[SaveSpec] = field(default_factory=list);
#                               statistics: List[StatBlock] = field(default_factory=list);
#                               left_out: Dict[str, Note] = field(default_factory=dict);
#                               va_modules: Dict[str, VaModule] = field(default_factory=dict)   # §4.1 16
#   Netlist.ics, Netlist.nodesets: annotation widened to Dict[str, Union[float, Expr]]
@dataclass
class VaModule:                                   # one Verilog-A module (§3.1, §4.5 items 12-13); new in ir.py:
    name: str                                     # spice.va_modules fills Netlist.va_modules with it (§4.1 16)
    path: str                                     # the file that declares it, possibly an `included one
    params: Optional[List[str]] = None            # declared parameter names, as written, from va_modules'
                                                  # preprocessor (§3.1); None: unknown (passed unchecked)
    attrs: Dict[str, str] = field(default_factory=dict)   # xyceModelGroup xyceLevelNumber xyceTypeVariable xycePTypeValue
@dataclass
class SweepSpec:
    target: str                                   # dev | mod | sub | param | freq (temperature: param "temp")
    name: str = ""                                # IR name of the instance, card or subckt instance
    param: str = ""
    mode: str = ""                                # lin | dec | log | step | values
    start: Optional[Expr] = None
    stop: Optional[Expr] = None
    step: Optional[Expr] = None
    count: Optional[int] = None
    values: List[Expr] = field(default_factory=list)
    origin: str = ""
@dataclass
class SaveSpec:
    items: List[str]                              # raw tokens, as written
    depth: Optional[int] = None
    sigtype: str = "node"
    devtype: Optional[str] = None
    subckt: Optional[str] = None
    exclude: List[str] = field(default_factory=list)
    probelvl: Optional[int] = None
    time_window: List[float] = field(default_factory=list)   # t1 t2 t3 t4 ...: interval pairs
    ports: bool = False
    filter: str = "none"                          # none | rc
    origin: str = ""
@dataclass
class ParamTest:
    name: str
    tests: List[Tuple[str, Expr]] = field(default_factory=list)   # (printif | warnif | errorif, condition)
    message: str = ""
    severity: str = ""
    origin: str = ""
@dataclass
class Cond:
    branches: List[Tuple[Expr, List["BranchItem"]]]
    default: List["BranchItem"] = field(default_factory=list)      # an else-if chain nests a Cond here
    origin: str = ""
@dataclass
class Vary:
    param: str
    dist: str = "gauss"                           # gauss | lnorm | unif
    std: Optional[Expr] = None
    n: Optional[Expr] = None
    percent: bool = False
    origin: str = ""
@dataclass
class Correlate:
    params: List[str]
    devs: List[str] = field(default_factory=list) # may hold * patterns
    cc: Optional[Expr] = None
    origin: str = ""
@dataclass
class StatBlock:
    kind: str                                     # process | mismatch | statistics
    varies: List[Vary] = field(default_factory=list)
    correlates: List[Correlate] = field(default_factory=list)
    truncate: Optional[Expr] = None
    origin: str = ""
Item = Union[Instance, Model, Param, Subckt, Cond, ParamTest]
BranchItem = Union[Instance, Cond, ParamTest]
ITEM_TYPES = (Instance, Model, Param, Subckt, Cond, ParamTest)
def flat_items(items: Sequence[Item]) -> Iterator[Item]                  # §4.1 item 4
def flat_analyses(analyses: Sequence[Analysis]) -> Iterator[Analysis]    # nested sweep/montecarlo children too

# vamos/netlist/expr.py  (phase 0: the keywords and tables, as stubs that keep today's behaviour for
# dialect="hspice"; S1 implements all of it, the Spectre dialects and the printers' new forms; §4.2)
DIALECTS = ("hspice", "spectre", "spectre-spice")   # any other name: ValueError (spice.py maps "spice" first)
Warn = Callable[[str, str], None]                 # (severity, message); spectre.py adds the origin
def number(s: str, dialect: str = "hspice", warn: Optional[Warn] = None) -> float
def parse(text: str, case: str = "lower", dialect: str = "hspice",
          funcs: Optional[Mapping[str, Tuple[Sequence[str], Expr]]] = None,
          warn: Optional[Warn] = None) -> Expr
def evaluate(ast: Expr, scope: Mapping[str, float], dialect: str = "hspice") -> float
def to_text(ast: Expr, dialect: str = "hspice") -> str
SPECTRE_FUNCS: Dict[str, Tuple[int, int]]           # §3.5, ref19 pp.476-477
SPECTRE_CONSTANTS: Dict[str, float]                 # ref19 pp.466-467 (22 names)
EXTRA_FUNCS: Dict[str, Tuple[int, Optional[int]]]   # cpow hypot fmod: evaluated in every dialect (as are the
                                                    # bitwise operators); printed; never parsed from HSPICE text
RESERVED = ("time", "temper", "hertz", "$tnom")     # existing tuple, "$tnom" added; the Spectre dialects'
                                                    # ident maps only temp and tnom (§3.5)
# VACASK_CONSTANTS (existing) gains "M_DEGPERRAD"

# vamos/netlist/tables.py  (phase 0: the keyword, the row types, the data; §3.9, §4.4)
def model_params(model: Model, engine: str, binned: bool = False,
                 options: Optional[Mapping[str, Expr]] = None,
                 dialect: str = "hspice") -> Tuple[List[Tuple[str, Expr]], List[Note]]   # existing + dialect
@dataclass(frozen=True)
class ParamRule:
    name: str                                     # the parameter, lower case
    action: str                                   # fold | pass | rename | default | strip | error
    value: str = ""                               # rename target, or the default written ("=fc" copies fc)
    when: str = ""                                # "" | "absent:<p>" | "given:<p>"; several joined by "," (all hold),
                                                  # read on the card as written
    match: Tuple[str, ...] = ()                   # the rule applies only to these values of the parameter: an
                                                  # enumeration value, "absent", "0" or "nonzero"; () = any value
    warn: str = ""                                # a warning besides the action: "card" (model_params, per card) |
                                                  # "analyses=ac,noise,xf,tran" | "temp!=tnom" (plan.build, per run)
    cite: str = ""                                # "ref5 p.410"
# Several rules may share a name; the first whose `when` and `match` hold applies. Examples (§3.9):
#   ParamRule("capmod", "strip", match=("absent", "bsim"), warn="analyses=ac,noise,xf,tran")
#   ParamRule("capmod", "strip", match=("meyer",)); ParamRule("capmod", "error", match=("none", "yang"))
#   ParamRule("hcomp", "error", match=("nonzero",))
#   ParamRule("eg", "default", "1.124481", "absent:eg", warn="temp!=tnom")
#   ParamRule("nsub", "default", "1.13e16", "absent:nsub"); ParamRule("phi", "default", "0.7", "absent:phi,absent:nsub")
#   ParamRule("badmos3", "default", "1", "absent:badmos3", warn="card")
@dataclass(frozen=True)
class MasterRow:
    master: str                                   # resistor capacitor inductor vsource ... diode bjt jfet mos1 ... bsim4
    element: str                                  # IR element kind: r c l v i e g h f k d q j m
    level: Optional[int] = None                   # the tables.DISPATCH level
    polarity_key: str = ""                        # "type" for mos*/bsim*/bjt/jfet
    kinds: Tuple[Tuple[str, str], ...] = ()       # polarity value -> IR card kind, first = default
    terminals: Tuple[str, ...] = ()
    geometry: Tuple[Tuple[str, float], ...] = ()  # default instance w/l; default lmin lmax wmin wmax
    params: Tuple[ParamRule, ...] = ()            # card parameters
    instance: Tuple[ParamRule, ...] = ()          # instance parameters: dev= renames (§5.2), the SPICE-appended
                                                  # check (§4.3), folded inputs (§3.8)
SPECTRE_MASTERS: Dict[str, MasterRow]
def bin_bounds_spectre(model: Model, values: Mapping[str, float]) -> Tuple[float, float, float, float]
def bin_guard_spectre(bounds: Sequence[float], l: Expr, w: Expr, s: float) -> Expr
def select_bin_spectre(bins: Sequence[Tuple[Model, Tuple[float, float, float, float]]], l: float,
                       w: float, s: float) -> Optional[Model]
@dataclass
class PathEnv:                                    # one instance path (§3.5, §4.4; phase 0, implemented by S0)
    path: str                                     # "" at the top level, else the X-instance path "x1.x2" (IR names)
    subckt: Optional[Subckt]                      # None at the top level
    scope: Scope
    env: Mapping[str, float]                      # the parameter values on this path (child_env's rule)
    items: List[Instance] = field(default_factory=list)   # the body's instances on this path, Cond resolved
    hidden: Set[str] = field(default_factory=set) # parameters that did not evaluate (non-strict walks only)
def path_envs(nl: Netlist, values: Optional[Mapping[str, float]] = None, strict: bool = False,
              dialect: str = "hspice") -> Iterator[PathEnv]
    # every path, the top first, depth first in body order; strict: EvalError naming the path and expression;
    # plan.build walks it strict with dialect="spectre"; xyce._Deck.elaborate is rebuilt on it (non-strict)
def path_counts(nl: Netlist) -> Dict[str, int]    # subckt name -> number of instance paths that reach it (§5.3)

# vamos/netlist/spice.py  (additions, §4.3; Decl, FileRef, Fragment and Resolver are phase-0 stubs that S0
# adds, with Netlist.left_out; VaModule is ir.py's, imported here; from phase 1 on, S1 owns every spice.py change)
@dataclass
class Decl:                                       # a name the first phase declares (§4.3, two phases)
    kind: str                                     # subckt | model | param
    name: str                                     # IR name
    master: str = ""                              # model: the Spectre master (Model.prim)
    ports: Optional[int] = None                   # subckt: the port count
    params: List[str] = field(default_factory=list)   # subckt: the header parameter names
    origin: str = ""
@dataclass
class FileRef:                                    # a SPICE file statement the first phase found (§3.1, §4.3 Files)
    keyword: str                                  # .include | .inc | .incl | .lib | .hdl
    path: str                                     # as written
    section: Optional[str] = None                 # .lib's section
    subckt: str = ""                              # the enclosing .subckt's IR name; "" at the top level
    origin: str = ""
class Resolver(Protocol):
    def subckt(self, name: str) -> Optional[Decl]: ...
    def model(self, name: str) -> Optional[Decl]: ...
    def param(self, name: str) -> Optional[Decl]: ...
    def va_module(self, name: str) -> Optional[VaModule]: ...
@dataclass
class Fragment:
    items: List[Item] = field(default_factory=list)   # raw Params, Models with prim, Subckts, Instances; source order
    controls: List[Tuple[str, List[Tuple[Optional[str], str]], str]] = field(default_factory=list)
                                                  # (keyword, spice.py's (key, text) fields, origin)
    notes: List[Note] = field(default_factory=list)
    left_out: Dict[str, Note] = field(default_factory=dict)
    pending: List[Tuple[str, str, str]] = field(default_factory=list)   # (kind, name, origin): defined nowhere
def declare_fragment(lines: List[Tuple[str, str]], opts: ParseOpts) -> Tuple[List[Decl], List[FileRef]]
    # the first phase, SPICE side: the names, and the file statements in order (spectre.py splices them, §4.3)
def parse_fragment(lines: List[Tuple[str, str]], cwd: str, opts: ParseOpts, resolver: Resolver) -> Fragment
    # sets Instance.prim on every element (§4.1 item 7)
def va_modules(path: str, search: Sequence[str] = (), defines: Sequence[str] = ()
               ) -> Tuple[Dict[str, VaModule], List[str]]
    # modules by lower-cased name, and every file read; follows `include and runs the declaration
    # preprocessor (§3.1); spice.d_hdl uses it too, and both parses store the result in Netlist.va_modules

# vamos/netlist/rawfile.py  (additions, §4.6; implemented in phase 0, except read_prn: S4)
class Raw:
    def exact(self, name: str) -> int                 # case-sensitive, no aliasing; KeyError otherwise
class RawPlot: ...                                    # moved from backends/cosim.py, unchanged
def scan(path: str) -> List[RawPlot]                  # cosim.scan_raw, moved; cosim re-exports it as scan_raw
def fix_scanned(path: str, plots: List[RawPlot]) -> bool   # cosim._fix_points(path, plots), moved; re-exported
def fix_points(path: str) -> bool                     # existing signature and result: fix_scanned(path, scan(path))
def iter_rows(path: str, plot: int, columns: Sequence[int]) -> Iterator[tuple]
def read_prn(path: str) -> List[Raw]                  # Xyce standard-format .PRINT tables, split per .STEP (S4)

# vamos/netlist/spectre.py
@dataclass
class SpectreParseOpts:
    search: List[str] = field(default_factory=list)          # the -I directories
    percent: Mapping[str, str] = field(default_factory=dict) # %-codes for quoted strings (§2.4)
    pre: List[str] = field(default_factory=list)             # +pre_config fragment files
    post: List[str] = field(default_factory=list)            # +config fragment files
    cpp_markers: bool = False   # the input is cpp output: origins, include directory and language per
                                # '# <line> "<file>" [flags]' marker, resolved against the cwd (§2.6)
    title: Optional[str] = None # line 1 of the original file when cpp ran
    mts: bool = True            # False under -mts: options inside subckts are global (§3.6)
    va_include: List[str] = field(default_factory=list)      # CDS_VLOGA_INCLUDE (§2.8)
    va_defines: List[str] = field(default_factory=list)      # -va,define, for va_modules' preprocessor (§3.1)
    top_dir: Optional[str] = None   # the directory the top file's relative includes resolve against
                                    # (None: the file's own; the cwd for a stdin copy, §1)
    top_name: Optional[str] = None  # the name the top file's origins carry ("stdin"; None: its path)
@dataclass
class Stmt:                     # phase 0: the statement layer, the CST oracle's unit (§3.2, §11 T0)
    kind: str                   # instance model parameters subckt if analysis save ic nodeset include ...
    name: str = ""
    nodes: List[str] = field(default_factory=list)
    master: str = ""            # instance master, model master or analysis keyword
    params: List[Tuple[str, str]] = field(default_factory=list)       # (name, value text as written)
    children: List["Stmt"] = field(default_factory=list)              # subckt, sweep, montecarlo, group body
    branches: List[Tuple[str, List["Stmt"]]] = field(default_factory=list)   # if: (condition text, body)
    origin: str = ""
    span: Tuple[int, int] = (0, 0)                                    # character offsets in the text
def statements(text: str, origin: str, title: bool = True) -> List[Stmt]   # no file access, no lowering;
                                                                           # NoteError on a syntax error
def parse(path: str, cwd: str, opts: SpectreParseOpts) -> Netlist   # built on statements(); NoteError with
                                                                    # every note on error
def resolve_source(params: Mapping[str, Value], type: str, tran_stops: Sequence[float],
                   notes: List[Note]) -> Source          # §3.8.1; plan.py reuses it for alters (§5.3)
number = functools.partial(expr.number, dialect="spectre")   # §3.3

# vamos/netlist/plan.py  (dataclasses frozen in phase 0; IR names only, §5.1; S2 implements build)
Target = Tuple[str, str, str]   # always three: ("instance", "x1.r1", "r") | ("model", "nch", "vth0")
                                # | ("option", "temp" | "tnom", "") | ("variable", "rval", "") | ("freq", "", "")
Setting = Union[float, str, List[float]]   # an evaluated value: a number, an enumeration or file name, a vector
@dataclass
class SweepLevel:               # one sweep a step runs under (§5.2)
    name: str                   # the Spectre sweep's name (an enclosing sweep block); "" for the step's own sweep
    id: str                     # vamos_w<k>: the VACASK sweep statement and its rawfile column (§7.1)
    target: Target
    spec: SweepSpec             # evaluated, engine-ready: mode lin | step | values, or dec for an integral span.
                                # Invariant after plan.build: every Expr field of it (start, stop, step, each
                                # element of values) is a folded Num; S3 may rely on it. S2's tests pin it;
                                # test_spectre_contract.py need not
    continuation: int = 1       # VACASK continuation (§5.2)
    label: str = ""             # the PSF SWEEP variable: the parameter in a leaf ("dc" for a source, "temp", "r",
                                # "<param>", "freq"), "<dev>:<param>" in a parent ("R1:r") (§8.3)
    desc: str = ""              # the variable as descriptions write it: "Vin:dc", "temp", "<param>", "<model>:<param>"
    units: str = ""             # its PSF units: "V", "Ohm", "C", "", "Hz" … (§8.3)
@dataclass
class AnalysisStep:
    id: str                       # vamos_a<k>
    name: str                     # Spectre name (child name inside sweeps)
    kind: str                     # op dc ac noise xf tran
    context: List[SweepLevel] = field(default_factory=list)   # enclosing Spectre sweeps, outermost first
    own: Optional[SweepLevel] = None   # a dc target; an ac/noise/xf parameter at a fixed freq, or their frequency axis
    args: Dict[str, Setting] = field(default_factory=dict)    # §3.11's names, evaluated; tran: errpreset applied (§5.1)
    options: Dict[str, Setting] = field(default_factory=dict) # the global options in force at this step (§5.1)
    saves: Optional[List[SaveSpec]] = None
    full_solution: bool = False   # write=/writefinal=: save every node (§5.4)
    noise_input: Optional[str] = None   # IR name of the input source; vamos_nin<k> when the netlist gives none (§5.6)
    stores: Optional[str] = None  # prevoppoint/useprevic stored-solution names
    uses: Optional[str] = None
@dataclass
class Action:
    op: str                       # options | var | alter | source | analysis | note
    args: Dict[str, object] = field(default_factory=dict)     # the keys of §5.1's table, per op:
        # options:  {<option key>: Setting}            var:    {<top-level parameter>: float}
        # alter:    {"target": Target, "value": Setting, "source": Source (a source field only)}
        # source:   {"path": str, "type": str, "source": Source}     analysis: {}     note: {"note": Note}
    step: Optional[AnalysisStep] = None
    origin: str = ""
@dataclass
class RunPlan:
    actions: List[Action] = field(default_factory=list)
    variables: Dict[str, float] = field(default_factory=dict)
    overridden: Dict[str, Set[str]] = field(default_factory=dict)  # subckt → parameters sub= targets make overridable
    options: Dict[str, object] = field(default_factory=dict)       # the global set, as the first options action has it
    notes: List[Note] = field(default_factory=list)
    dependents: Set[str] = field(default_factory=set)   # filled by build: variables and every top-level Param that
                                                        # reads one (§4.1); signals.resolve only reads it
    engine: str = "vacask"
def build(nl: Netlist, engine: str, settings: Settings) -> RunPlan

# vamos/netlist/signals.py  (dataclasses frozen in phase 0)
Ref = Tuple[str, ...]   # ('v', node path) | ('i', element path) | ('noise', instance path) | ('onoise',)
                        # | ('inoise',) | ('gain',) | ('tf', source path); IR names, '.' hierarchy
@dataclass
class SignalMap:
    per_step: Dict[str, List[Tuple[Ref, str, str]]] = field(default_factory=dict)   # step id → (Ref, Spectre
                                                                                     # name, psf type), PSF order
    allpub: Set[str] = field(default_factory=set)       # step ids printed as save default / V(*) (§6.4)
# psf type (§6.4): "V" | "I" | "V/sqrt(Hz)" (noise out; in with a vsource input) | "A/sqrt(Hz)" (in with an
# isource input) | "V/V" | "V/A" (gain) | a noise instance's STRUCT type name (its model, else its master, §8.4)
def resolve(nl: Netlist, plan: RunPlan) -> Tuple[Netlist, SignalMap, List[Note]]
    # a copy.deepcopy of nl with the probes, Netlist.values reduced per engine from plan.dependents (§4.1)

# vamos/netlist/vacask.py, xyce.py  (additions; with plan/step None the output is byte-identical to today)
def render(nl, analysis_name="vamos_tran", osdi=(), notes=None, op=False,           # vacask.py
           plan: Optional[RunPlan] = None, sigmap: Optional[SignalMap] = None,
           names: Optional[Dict[str, Dict[Ref, str]]] = None) -> str
def render(nl, osdi=(), notes=None, op=False,                                      # xyce.py: one step
           step: Optional[AnalysisStep] = None, sigmap: Optional[SignalMap] = None,
           names: Optional[Dict[str, Dict[Ref, str]]] = None) -> str
def names_for(nl: Netlist, plan: RunPlan, sigmap: SignalMap) -> Dict[str, Dict[Ref, str]]   # both; no rendering
# xyce.py (S3): the smoke patterns and the summary check as public functions (§4.5 item 13, §7.2)
def output_problems(text: str, nl: Netlist) -> List[Note]           # unknown model parameters (binned-card bounds
                                                                    # exempt), unrecognized parameters, netlist errors
def device_summary_problems(text: str, nl: Netlist) -> List[Note]   # a built-in entry where a Netlist.va_modules
                                                                    # module should be listed under its own name

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
                                                               # scalar fields above hold argv's (None: not given)
    def note(self, option: str, disposition: str, note: str = "") -> None
def settings(job: SpectreJob, nl: Netlist) -> Settings  # §2.2: argv ⊕ the netlist's options ⊕ job.defaults (S5)
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
    values: list = field(default_factory=list)                  # an op's single values only (swept: rows)
    members: Optional[List[Tuple[str, list]]] = None
@dataclass
class AnalysisResult:
    key: str
    atype: str
    file: str
    parent: str = ""
    tree: str = ""                                               # "", "leafNode" or "sweepNode"
    sweep: Optional[Tuple[str, str, int, List[float]]] = None    # (name, units, grid, values); values for
                                                                 # parents only, a leaf's x comes from rows
    signals: List[Signal] = field(default_factory=list)          # the traces, in TRACE order
    header: Dict[str, object] = field(default_factory=dict)
    description: str = ""
    swept: Dict[str, float] = field(default_factory=dict)
    data_type: str = ""
    status: str = "ok"                                           # ok | failed | interrupted
    rows: Optional[Callable[[], Iterator[Tuple[object, List[object]]]]] = None   # swept: (x, values in
                                                                 # TRACE order), streamed (§4.6)
def collect(plan: RunPlan, sigmap: SignalMap, er: EngineResult, naming: str) -> Iterator[AnalysisResult]
    # needs no IR: sweep variables, units and descriptions come from each step's SweepLevels (§5.2, §8.3), trace
    # types and noise STRUCT types from the SignalMap (§6.4), header values from args and options (§5.1)

# vamos/spectre/run_vacask.py, run_xyce.py
def run(nl: Netlist, plan: RunPlan, sigmap: SignalMap, run_dir: str, job: SpectreJob, log: SpectreLog) -> EngineResult

# vamos/output/psf.py
SIMULATOR = "spectre"; LOG_GENERATOR = "drlLog rev. 1.0"; SIM_MODE = "Spectre"; SIGNAL_NAME_TYPE = "spectre"   # §8.2
TYPES: Dict[str, List[str]]                             # the fixed 23.1 TYPE lists per family (§8.3)
@dataclass
class PsfHead:                                          # phase 0
    version: str = ""
    date: str = ""
    design: str = ""
    simulator: str = SIMULATOR
    psfversion: str = "1.4.0"
    precision: str = "%.15e"                            # VALUE reals (options precision, §8.3)
    prop_precision: str = "%#g"                         # logFile PROP reals
def psf_string(s: str, escchars: bool = False) -> str   # §8.3
def psf_date(t: datetime) -> str                        # §8.2: fixed English tables
def write_analysis(dirpath: str, res: AnalysisResult, head: PsfHead, escchars: bool = False) -> None
def write_logfile(dirpath: str, results: Iterable[AnalysisResult], head: PsfHead) -> None
# vamos/output/nutmeg.py
def write(path: str, results: Iterable[AnalysisResult], binary: bool) -> None   # streams; §8.5
# vamos/output/statefile.py
def write(path: str, nodes: Mapping[str, float], currents: Mapping[str, float], circuit: str,
          analysis: str, date: str) -> None
def read(path: str) -> Tuple[Dict[str, float], Dict[str, float]]   # (node entries, current entries), §8.6

# vamos/spectre/args.py
def defaults_tokens(env: Mapping[str, str], prog: str) -> Tuple[Optional[str], List[str]]   # (variable, tokens), §2.2
def prescan(tokens: List[str], job: SpectreJob) -> List[str]   # takes +%X s / -%X and the help options (§2.1)
# vamos/spectre/percent.py
def codes(job: SpectreJob, start: datetime, analysis: str = "") -> Dict[str, str]
def expand(text: str, codes: Mapping[str, str]) -> str  # %X, colon modifiers, %%
# vamos/spectre/cpp.py
def preprocess(path: str, job: SpectreJob, run_dir: str) -> Tuple[str, str, List[Note]]   # (cpp.out, title, notes)

# vamos/spectre/log.py
class SpectreLog:   # stdout/file routing per log_mode, classes, maxwarns/maxnotes per (id, analysis), counts,
                    # the trailer; built-in text for every banner key (§8.7)
    def __init__(self, job: SpectreJob, banner: Banner, start: datetime) -> None   # limits: argv ⊕ defaults
    def limits(self, settings: Settings) -> None        # the merged maxwarns/maxnotes (and to-log) from step 6 (§2.2)
    def message(self, note: Note, phase: str) -> None
    def line(self, text: str) -> None                   # provenance, verbose echoes, the run-directory note
    def analysis_banner(self, res_or_step) -> None
    def status_line(self) -> None                       # SIGUSR1, from the main loop (§2.7)
    def finish(self, status: int) -> None
# vamos/spectre/flow.py
def run(job: SpectreJob, opts: dict) -> int             # the §1 pipeline and §2.7's signals and status mapping

# vamos/personalities/spectre.py
OPTIONS: List[Opt]; TABLE: Table                       # §2.3
def build_job(argv: List[str], env: Mapping[str, str], cwd: str, prog: str) -> SpectreJob
def main(args: List[str], opts: dict) -> int            # cli entry; the invoked name is tools.invoked (§2.1);
                                                        # returns the Spectre exit status; never raises (§2.7)
def help_text(topic: Optional[str] = None) -> str       # spectre -h [topic]; ≤ 100 columns, ends with the
                                                        # absolute path of docs/VAMOS_GUIDE.md (§2.1)

# vamos/proc.py  (phase 0: moved from backends/nvc.py, lines 612-728, which imports them back; nvc.py keeps its
# own INTERRUPT_GRACE (line 55) and passes it at each construction, §2.7)
INTERRUPT_GRACE = 5.0                                   # the default for callers that pass none
class Interrupts:   # as nvc's _Interrupts, parameterised; nvc: handled INT TERM HUP (+TSTP), forward SIGINT
    def __init__(self, handled: Sequence[int], forward: int, flags: Sequence[int] = (),
                 grace: float = INTERRUPT_GRACE) -> None   # flags: USR1, USR2 (set a flag, never forwarded)
    def __enter__(self) -> "Interrupts"                 # installs the handlers (main thread only; an ignored signal
    def __exit__(self, *exc) -> None                    # stays ignored), restores them on exit
    def attach(self, proc: subprocess.Popen) -> None
    signum: Optional[int]                               # the first handled signal, None if none came
    killed: bool                                        # the engine outlived the grace or a second signal: SIGKILL
    def take_flags(self) -> List[int]                   # the flag signals since the last call, in order (§2.7)
def child_setup() -> Optional[Callable[[], None]]       # own process group; PR_SET_PDEATHSIG
def signal_name(signum: int) -> str
# Gate: test_vamos_run.py's TestStreamSignals (test_grace_period_kills sets nvc.INTERRUPT_GRACE) and every
# existing test, unchanged

# vamos/optable.py  (phase 0)
def scan(table: Table, args: List[str], job: Job, positional: Callable[[Job, str], None],
         unknown: Callable[[Job, str, List[str], int], int], option_chars: str = "-+") -> None
class VamosOpt(NamedTuple):       # existing fields unchanged: where stays a str (any | ams | run | simv, and now
    ...                           # spectre); one defaulted field is appended:
    also: Tuple[str, ...] = ()    # further contexts where it has an effect
# analog: where "ams", also ("spectre",); keep: where "run", also ("spectre",); the new psf_names: where
# "spectre", modern|legacy, removed from PLANNED_KEYS. _WHERE_TEXT gains "spectre": "spectre". A tuple `where`
# would break test_help_texts_list_every_option_and_point_at_the_guide (`o.where != "simv"` always true) [E94]:
# vcs's help keeps listing every option whose where is not simv, so it lists psf_names marked [spectre], and the
# existing test passes unchanged.
def unmapped_notes(job: Job) -> List[Note]              # the texts report_unmapped prints; it prints over it
# vamos_option_effects(opts, "spectre"): analog_stop, analog_maxstep, parhier, no_deck_check → "an AMS compile
# option: vcs-ams or vcs -ad"; daidir, append_log → "a ./simv option"; recorded as INAPPLICABLE entries in
# SpectreJob.unmapped (as vcs._note_vamos_options does), so they reach the log and the strict check.
# vamos_option_effects(opts, "vcs"…) reports psf_names as "a spectre option".
# vamos_options_help("spectre") lists the options whose where or also is any or spectre.

# vamos/ams/engines.py  (phase 0)
def choose_engine(opts: dict, configured: Optional[str] = None) -> str   # --vamos-analog > VAMOS_ANALOG >
                    # configured > vacask; ValueError with vcs-ams's text; ams/flow.choose_engine wraps it (AmsError)
def env_for(engine: str, nvc_libdir: Optional[str] = None, base: Optional[Dict[str, str]] = None) -> Dict[str, str]
                    # nvc_libdir None: no bridge directory (today '/nonexistent' puts '/' first [E68])
def tool_rows(engine: str, xyce: Optional[str] = None) -> List[Tuple[str, str]]
                    # the provenance rows: [("VACASK", vacask_bin()), ("OpenVAF-r", openvaf())] or [("Xyce", xyce)],
                    # xyce being the path the caller runs; ams/flow.compile_tools calls it with
                    # engines.xyce_bin() or "Xyce" (unchanged output), the spectre flow with its own lookup (§1, §7.2)
```

Also:
- `cli.py`: `PERSONALITIES["spectre"] = spectre.main` and `_ROLES["spectre"]`; the dispatch rule of §2.1
  (`personality_of(name)`), `tools.invoked`; the effects check skips `spectre` as it skips vcs and vcs-ams
  (`cli.py:104`), because the personality records them itself.
- `tools.SHIM_NAMES` gains `spectre`, so a shim directory holding only `spectre` is removed from the PATH vamos
  gives the tools it runs; today such a directory is neither detected nor scrubbed [E79]. The lock-out needs no
  change: it uses `VAMOS_STACK` and `find_real`, and the stack holds `"spectre"` (`tools.py:206-222`). Alias
  links (`VAMOS_SPECTRE_NAMES`) are not probed, since shim detection probes only fixed names
  (`tools.py:62-64`): put them in a directory that also holds a listed name, or list the directory in
  `VAMOS_REDIRECT`.
- `tables.SPECTRE_MASTERS`: data, phase 0 (§4.4).
- `shims/spectre`.
- `vamos/banners/spectre.json` with the keys `version`, `subversion`, `run_start`, `inventory`,
  `analysis_banner`, `analysis_done`, `audit`, `error_block`, `warning_block`, `notice_block`, `trailer_ok`,
  `trailer_fatal`, plus `brand` and a `_comment` listing the five configuration layers and the placeholders
  (`{prog}` among them), as every shipped profile has (`vamos/banners/vcs.json:2-3`). The existing
  `test_layers_in_the_comments` checks only `vamos/licenses.json` and `vamos/banners/vcs.json`
  (`test_vamos_driver.py:335-339`), so the spectre tests (S5, a new file) check `spectre.json`'s layers and
  keys themselves; the existing test is not changed (§4). No key holds licence or copyright text, and no key
  holds a PSF format field (§8.2).
- `vamos/licenses.json` gains `cpp` (§1).
- `docs/VAMOS_GUIDE.md` gets a spectre section with what the guide gives every personality, under its rule
  that every command shown was run and the output shown is that run's (`VAMOS_GUIDE.md:4-6`):
  - setup: the drop-in name and its aliases (`VAMOS_SPECTRE_NAMES`), the shim, the warning that
    `shims/spectre` shadows a real Spectre when `shims/` is first on PATH, and how to make ADE call vamos
    (the shim first on the PATH that ADE inherits, or an alias name);
  - a quick start (§7.3's netlist run on both engines, its outputs listed and one PSF file shown);
  - the outputs: the results directory, `logFile`, the PSF files and their names (modern and legacy),
    nutmeg, `+log`, state files, the run directory; no `vamos.tools.json` for spectre; how to read the PSF
    results (psf-parser, psf_utils; nutmeg through ngspice);
  - how a run ends and stopping a run: exit status 0/1/2/3, and exit 3 on SIGINT where simv gives 130
    (§2.7);
  - the options: the dispositions of §2.3, including the *error* rows (`+top`, `+interactive`,
    `-format uwi`), since the guide's §4 says "No VCS option is a usage error" for vcs; the `where` column
    of the vamos options table; the environment variables of §2.8;
  - engine choice and what Xyce lacks (`xf`, Verilog-A card parameters, the tolerance settings, §0);
  - the dispositions of the netlist constructs (§0's lists), the banner keys of `spectre.json` and which
    ones a profile cannot suppress (§8.7);
  - troubleshooting entries; the limitations (§0's not-in-v1 list); the tool table's `cpp` row; the guide's
    §10 ("no spectre yet") and §11 ("a design; not built") rows updated.

  S5 writes the parts that need no run (setup, options, environment, banner keys) in phase 1; the
  integrator writes the run-dependent ones (quick start, outputs, how a run ends, troubleshooting messages)
  from real runs in phase 2; a guide trial follows (§12).

## 11. Tests

Four tiers. Each runs from `tests/vamos` with `python3 -m unittest`, and every skip prints its reason. The WSL
gate run requires T0, T1, T2 and T3a; the merge gate is §4's. Every spectre test is in a new file; no existing
test file changes (§4).

| tier | runs on | what |
|---|---|---|
| **T0** unit | both legs, no engine, no Rust | the lists below; the CST oracle; the SPICE-corpus acceptance; the PSF writer fed from rawfile goldens, checked by `psfcheck`; the document check |
| **T1** engine facts | WSL | `fixtures/spectre/engine_facts/E<nn>/`: the engine facts this design rests on, the T1 rows of §13 (listed in its introduction: E1-E13, E15-E43, E45's engine half, E47, E51, E63-E75, E77, E81, E82, E84, E95, E96), one deck and `expect.json` each, run through `engines.*` and `env_for`, so an engine upgrade that changes a fact fails and names the rule that depends on it; the smoke test that the Xyce in use knows `BADMOS3` (§7.2); the sweep-restore pin (§5.2). §13's observations of today's code and its external-tool rows are not T1 (§13) |
| **T2** end-to-end | WSL; both engines unless marked | `fixtures/spectre/e2e/<nn>_<slug>/`: the list below; values read with `psfcheck`'s reader, and every PSF also parsed by the vendored psf-parser; cross-engine agreement within ~1 %, analytic checks tighter |
| **T3** external | WSL | (a) the XDM decks against their Xyce golds; (b) the Cadnip engine oracle, optional; (c) the CMC decks, opt-in |

**Gates** (`spectre_testlib.py`): `needs_vacask`, `needs_xyce` (existing `vamos_testlib` helpers); `needs_engines`
(an engine without nvc: `ams_e2e_lib.needs_ams` also requires nvc and iverilog, `ams_e2e_lib.py:30-31`);
`needs_cpp` (WSL `/usr/bin/cpp`); `needs_psf_parser` (Python ≥ 3.10; the vendored copy by default,
`VAMOS_PSF_PARSER` overrides); `needs_psf_utils` (`VAMOS_PSF_UTILS` and `VAMOS_PSF_UTILS_DEPS`); `needs_ngspice`;
`needs_cadnip` (`VAMOS_CADNIP_VACASK` and `SIM_MODULE_PATH`); `needs_pyms` (the Xyce vamos runs,
`engines.xyce_bin()` with `env_for`, registers an `.hdl` module: both launchers carry PyMS and, under
`env_for`, load the same library [E96], so no binary has to be selected); `needs_cmc` (`VAMOS_CMC=1`,
`VAMOS_CMC_EXAMPLES`); `needs_vacask_src` (`VAMOS_VACASK_SRC`, VACASK's source tree, with no built-in default: the gate skips when it
is unset; its PSP103 tree and ring benchmark for e2e 29); `needs_vacask_rawread`
(`needs_vacask_src` plus numpy: VACASK's `python/rawfile.py`, e2e 13's second nutbin reader).

**Layout.**
```
tests/vamos/spectre_testlib.py      SpectreCase: runs bin/vamos -spectre, loops over engines, isolates tmp and
                                    PYMS_CACHE, loads and compares expect.json; the gates; signal tests use reap
tests/vamos/psfcheck.py             the strict PSF ASCII reader and checker (below); stdlib, Python 3.9
tests/vamos/third_party/psf_parser/ psf-parser 05021e6 (MIT, with its LICENSE)
tests/vamos/fixtures/spectre/
  README                            provenance and licences of everything below
  lang/                             vamos-authored inputs; one per CST gap and per manual construct the corpus
                                    files left out below cover, with a .cst where the CST parses it
  cst/corpus/<cat>/*.scs, cst/expected/<cat>/*.txt   NetlistParse.rs d565fd3, byte-identical (MIT), without
                                    the files that quote or cite a manual until §14 q.41 is answered (below)
  cst/DIVERGENCES, cst/GAPS, cst/regen.sh
  spice/                            NetlistParse.rs tests/corpus/*.sp and their expected dumps
  emit/, plan/                      goldens
  psf/golden/, psf/real/ (+ PROVENANCE), psf/bad/
  e2e/<nn>_<slug>/{deck.scs, expect.json, engine_expect.json}   expect.json: the Spectre-level checks (below);
                                    engine_expect.json: the same checks in engine terms, for the phase-1b chain
                                    without flow.py. S6 writes the deck, expect.json and engine_expect.json
                                    for e2e 1-10 in phase 1b; phase 2 writes 11-30 (§12)
  xdm/<CATEGORY>/                   Xyce_Regression XDM/SPECTRE d4685581: .cir.spectre, gold .cir, aux files
  cadnip/{*.scs, *.cst, expect.json, BUILD}   the .cst dumps made by cst/regen.sh and committed, so T0 checks
                                    their tiling and T3b needs no spectre_dump at test time
  engine_facts/E<nn>/{deck, expect.json}   the T1 rows of §13 only
  cmc/<deck>.json                   expectations; the decks are read in place from VAMOS_CMC_EXAMPLES
tests/vamos/test_spectre_docs.py    the document check: every inferred tag of this design names an existing §14 q.N
```
`expect.json`: `{argv, engines, status, files, values: [{file, signal, at, value, rel, abs}], cross_engine:
{rel}, messages: [[severity, substring]]}`.

**Licences.** sv2ghdl is GPL-3.0-or-later (`LICENSE`), so every source below is compatible. `LICENSE`'s
third-party fixtures clause and `fixtures/README` (whose "Third-party files" section is the precedent) are
extended with: the NetlistParse.rs corpus and dumps (MIT, "Copyright (c) JuliaHub, Inc. and other
contributors", in a new `LICENSES/MIT.txt`; the repository URL and commit d565fd3). Whether to commit the
corpus files that quote or cite a Spectre manual is the user's decision (§14 q.41), so phase 0 does not wait
for it: it vendors the corpus without the 20 files whose comments cite or quote a manual (E101: the
`arrays_langswitch` files `array_expressions`, `array_mixed_names`, `array_params`, `array_sweep`,
`err_array_binary`, `err_array_unterminated`, `simulator_lang_spectre`, `simulator_lang_swap`; the
`control_named` files `alter`, `altergroup`, `check`, `checklimit`, `info`, `options`, `paramtest`, `set`,
`shell`; `funcdecl/comment_body`, `funcdecl/two_args`; `include_global/include_multiple`), and without
`julia_parse_tests/var_reference.scs`, which carries the verbatim manual line too [E88]. vamos-authored
`lang/` fixtures cover their constructs (vector elements, `**` chains, a mid-file `simulator lang` switch,
the control statements), and `cst/DIVERGENCES` points at those; the left-out files are added, with their
dumps, only after the user answers q.41. psf-parser's code and
its real PSF files (MIT, commit 05021e6); psf_utils' sample files (GPL-3.0-or-later, commit 6797146; its code is
never vendored); the XDM decks (GPL-3.0-or-later, Copyright 2019 NTESS, `Xyce_Regression/README.md:62-78`).

### 11.1 T0: unit (Cygwin Python 3.9 and WSL Python 3.14)
- **CLI:**
  - every §2.3 row through `build_job` (the `=` family, abbreviations, `+mt` against `+mt=4` against
    `+mtmode`, `-D` against `-debug`); an *error* row appends to `SpectreJob.notes` and stops before the run
    directory;
  - `+%E opamp1` and `-%E`;
  - defaults: `spectre_DEFAULTS` over `SPECTRE_DEFAULTS` over nothing; a `specsim` alias reading
    `SPECSIM_DEFAULTS`; `spectre_DEFAULTS=""` hiding `SPECTRE_DEFAULTS`; argv over both; an argv `-config`
    against a defaults `+config`; a `--vamos-*` token in `SPECTRE_DEFAULTS` recorded UNSUPPORTED (the
    counterpart of `test_vamos_options_in_option_files_are_reported`); a non-option token in the defaults;
    `-help`, `+interactive`, `-note` and `+mpssession` in `SPECTRE_DEFAULTS` ignored with the "does not
    read" note (a run still simulates and exits 0), a † option (`+log`) and a ref19-only one (`+aps`) applied;
    a `maxwarns` from an `options` statement beating a defaults `-maxw` and losing to an argv `-maxw`
    (`settings()` and `SpectreLog.limits`);
  - `+config`/`=config`/`-config`/`+pre_config`/`-pre_config` dropping rules; `+aps=` against a tran's
    `errpreset` against `+errpreset=`; `-mts`;
  - the invoked name: `spectre231`, `spectre-23.1` and a `VAMOS_SPECTRE_NAMES` alias give the right `%S`,
    `<prog>_DEFAULTS` and trailer; `spectrespp` gives exit 2; `vamos -spectre`;
  - `spectre -h`, `spectre -h resistor`, `spectre -hsf tran` (exit 0, nothing simulated);
  - `-f psfascii` is not an option file; unknown options, and `--vamos-strict` → exit status 2;
  - `--vamos-psf-names=legacy` accepted under spectre; `vamos_option_effects` and `vamos_options_help` for
    spectre; `psf_names` listed in vcs's help as `[spectre]` and reported by vcs's effects as a spectre
    option; `test_vamos_driver.py` itself unchanged and green (§4, §10 optable);
  - `spectre -h`'s last line is the guide's absolute path, every line ≤ 100 columns; `spectre.json`'s
    `_comment` names the five layers, and its keys are §10's;
  - exit status mapping: `VAMOS_CPP=/nonexistent` with `-E` gives 2; `spectre x.scs -raw` gives 2; an injected
    exception gives 2, writes the trailer, and prints no traceback on the screen;
  - the ADE command lines of §9 give no unknowns [E80].
- **Percent codes:** every row of the UG pp.266-268 table, `:h` without a slash giving `.`, `::`, no
  recursion, `%C` = `stdin`; codes inside netlist strings (`readns="%C:r.dc"`) and `include` names.
- **Lexer and numbers:**
  - `//` after a blank and not after a word; `*` lines; `\` and `+` continuation, also inside braces;
  - escaped names; `007` ≠ `7` in both modes; several node groups (`(1 2)(3 4)`);
  - the 38 literals of E52 against UG p.84 in both modes, and `0.22u` == 2.2e-07; `.01`, `.148p`, `-.5u`,
    `1E-14`; `1.234E-3p` (note); `50Ohms`, `1P`, `1x`, `1D3` (errors); `50_Ohms`; `5%`; `1meg` (warning, 1e-3);
    in SPICE mode `1x` and `1a` (warning, 1), `5V` (silent), `1e-3p` (note), `1.0D+3` (error).
- **Statements and the CST oracle** (`test_spectre_cst_oracle.py`, no Rust at test time). For every committed
  `fixtures/spectre/**/*.scs` with a committed dump: the CST must tile the file unless it is an `err_*` file
  (the CST drops text silently: of 126 corpus files 13 are untiled, all `err_*`, and the check flags a
  truncated node group [E93]); `statements(text, origin, title=False)` (corpus files are include files: 7 have
  a statement on line 1 [E88]) is compared with the CST through a kind map on the fields both have: statement
  kind; name as spelled; node texts in order; master, model master or analysis keyword; parameter names in
  order and value text, whitespace-normalized; operator structure, except `**` chains and vector splits;
  subckt ports, the inline flag and the body; `if` conditions and bodies; save signals and modifiers; `ic`
  and `nodeset` pairs; include file and section; `simulator lang`/`insensitive`; global nodes; function
  name, arguments and body text; reject parity for `err_*` files (NoteError, exit 2). Not compared: numeric
  values (the CST keeps text), semantics, comments and the title, SPICE sub-trees. The CST is a differential
  signal, not ground truth: `cst/DIVERGENCES` lists every file or statement where vamos follows the manual,
  with the [M] cite, and there the oracle asserts vamos's reading: vector elements (ref19 p.477) and `**`
  chains (right associative, ref19 p.475), on the vamos-authored `lang/` fixtures that stand for the
  left-out `array_sweep.scs` and `var_reference.scs` until q.41 is answered (§11 Licences);
  `err_braceless.scs` (the brace-less `if` is legal, UG p.109); the first line after a mid-file switch is a
  statement, not a title (UG p.53), on `lang_swap_spice_start.cir` and on the `lang/` stand-in for the
  left-out `simulator_lang_swap.scs`; `base_specifiers.scs` and `reduction_nor_nand.scs` (not in the
  manuals: errors), `lenient_name_only.scs` and `lenient_section_empty.scs` (errors) [E55, E88]. The
  `cadnip/*.cst` dumps are tiled the same way.
  `cst/GAPS` lists the 13 constructs the CST cannot parse (model groups, statistics, sweep bodies, montecarlo
  bodies, bare instance node lists, a title line, a line-1 `*` comment, the escaped name `r\2`, the brace-less
  `if`, library/section, prot/unprot, save modifiers such as `depth=`, one-line `real` functions) and the node
  groups it truncates, each with the `lang/` golden that covers it [E88].
  `cst/regen.sh` (WSL, cargo) rebuilds `spectre_dump` at the pinned commit and dumps any new vamos fixture. The
  CST agrees with the manual on unary vs `**`, `* /`, `+ -`, `<< >>`, `==` over `&`, `& | ~^ ^~`, `&& ||` and
  right-associative `?:` [E55]; `**` chains, `%`, `^`, `!` and number values are checked against the manual
  and the engines instead (E51's one-source-per-expression VACASK deck).
- **Parser:**
  - the title rule; language by extension, by first line (a non-`.scs` included file, and a top-level one,
    starting with `simulator lang=spectre`) and by `simulator lang`, with the mode restored after an include;
    a `.scs` named by a SPICE `.include` read as Spectre; a SPICE `.lib` naming a Spectre file (error);
    `simulator lang=` inside a subckt body or an `if` (error);
  - `insensitive=yes`: an exact match first, then a marked lower-case one; a Spectre-mode `M1 … NCH` using a
    SPICE-mode `.model NCH nmos`; two marked definitions differing in case (error); another `simulator`
    parameter (error);
  - include path order (the including file's directory, then `-I`; never the cwd first), `~`/`${VAR}`/%-codes;
    `section=`; a stdin netlist's relative include resolved against the cwd, with `stdin:<line>` origins;
    `ahdl_include` and `.hdl` with a module in an `` `include``d file (a two-file Verilog-A tree), and a
    vamos-authored module that declares its parameters through a `` `define`` macro in an included file,
    behind an attribute (`(*desc="…"*) parameter`), with an `` `ifdef`` that `-va,define` selects: the
    declared names found; a module whose macro is undefined: names passed unchecked with the note;
    parameters with shadowing, across both languages, a cross-language reference warned; user functions
    (body reading only its arguments, defined before use, a name like a built-in warned);
  - `inline subckt`; model groups; `if`/`else if`/`else` with the one-statement form; duplicate names under
    ref19 p.491's conditions and outside them (error); a model inside an `if` (error);
  - `global gnd vdd` and node `0` (warning); `ic`/`nodeset` with expression values; `save` variants and
    modifiers; `options` merge, `options` in a subckt (error; global with a note under `-mts`) and in a sweep
    block; `prot`/`unprot` in any case;
  - reserved words: a keyword as an instance name (error) and as a node or parameter name (accepted); `M_PI`
    as a node name (error); a parameter named `temp`, `tnom`, `temper`, `time` or `hertz` (error); `temper`
    in a Spectre expression (ExprError) and in a SPICE-mode one (the temperature);
  - every statement of §3.6, including the error rows; a `statistics` block from a PDK-style file and the XDM
    `statistics_line.cir.spectre`;
  - SPICE mode: `.op`/`.ac`/`.tran` naming; `.tran 1n 1u 10n 5n` (step 1e-9, stop 1e-6, outputstart 1e-8,
    maxstep 5e-9, a warning); a `.include` inside an open `.subckt` read into that subckt; a `+` line that
    would continue a statement of another file (error); a `.include` of a Spectre-language file inside a
    `.subckt` (error); `Instance.prim` on every V/I/R/C/L/E/G/H/F/K/D/Q/J/M line; `r1 a 0 'sqrt(-4)'` (an
    error naming the element, not `Num(-2.0)`) and a bare `temp` value (`Name('temper')`);
    `* spectre:` lines (error); appended `isnoisy=no` (mapped) and an unknown appended parameter (error);
    `scale=` on R (error); `00`/`007`/`7` distinct; an R/C/L field naming nothing defined (a model name, then
    `pending`); a Q line's fourth field; an X-line override of a body `.param` (error); a SPICE section that
    uses a Spectre subckt defined later; the `.option` map, the `.print` item table and every row of the dot
    statement table; `.param a='pow(2,1.5)'` evaluates to 2.8284271247461903 and `r0='1k*pow(x,1.5)'` prints
    `pow(x, 1.5)` [E54]; `par'…'` grouping.
- **SPICE-mode acceptance** (`fixtures/spectre/spice/`): every NetlistParse.rs `tests/corpus/*.sp` file parses
  through `parse_fragment` or fails with a named error (today's `spice.parse`: 25 parse, 37 fail, every error
  named [E62]); the element names, nodes and parameters of the parsed ones match the expected CST dumps; the
  two lang-switch files split into the expected regions.
- **Sources and models:** `resolve_source` for every type, from Spectre and from SPICE fields; `Source.spectre`
  keeping the fields of a `type=dc` source; the edge policy on literal, non-constant and altered edges; R/C/L
  card folding with `rsh`/`etch`/`etchl` and `cj`/`cjsw` geometry, the stripped card and `Instance.folded`; one
  test per `SPECTRE_MASTERS` row (each default written or not, per its condition: a card without `nsub` gets
  `nsub=1.13e16 phi=0.7 gamma=0 vto=0`, one with `nsub` gets none of them and the derivation warning; `mjsw=1/3`;
  `badmos3=1` with its per-card warning; the `capmod` and `hcomp` value rules; the instance rules), and
  `test_spectre_masters.py` (S0, built with the data in phase 0, §12): one test per master, which fails on a
  ref5 parameter of that master with no disposition row; Spectre `mos3`, `bsim3v3`, `diode` and `bjt` cards
  printed with no HSPICE-written key and no HSPICE note on both engines (the E59 cards); diode `level` ≠ 1
  (error); MOS default geometry from
  the card and from the master, and its use in bin selection; the resistor `scale` and `r` = 0 rules; a
  Verilog-A instance's parameters matched case-insensitively and printed lower-cased on VACASK.
- **Expression dialect:** the binding-power table (`-2**2` = 4, `2**3**2` = 512, `2**-1` = 0.5, `a || b && c`);
  `pow(2,1.5)` = 2.828…; `0**0` = 1; `(-8)**(1/3)`, `0**-1` and `exp(80)` raise EvalError; `log(-1)` and
  `sqrt(-1)` raise EvalError; `^` is an error; bitwise folding, `~^` an EvalError, every printer a PrintError,
  and no `KeyError` from any message; `evaluate` and the strict `fold` of `cpow(2,3)`, `hypot(3,4)`,
  `fmod(-7,3)` and `3 & 1` under the default dialect (8, 5, −1, 1), so a constant `r=2**3` prints on both
  engines; a dialect outside `DIALECTS` (ValueError) and spice.py's `spice` mapped to `hspice` (an
  `-nspice` netlist parses as today); `hypot` printed as `sqrt(x*x+y*y)` on Xyce and `fmod` on both; `db`,
  `agauss` and a one-argument `sign` rejected; the constants (`M_DEGPERRAD` too); `temp`/`tnom` as `$temp`/
  `$tnom` on VACASK and `$tnom` substituted on Xyce; user functions named `vt` and `MyF`; `V(out)` in a
  parameter (error); vectors `[0.5 1 +p2 (sqrt(p2*p2))]` (four elements) and `[1+p2]` (two); `to_text` in
  both dialects.
- **Plan:**
  - each §5.2 sweep form and target, with IR names only in the actions and every `Target` a 3-tuple; every
    `Action.args` and `AnalysisStep.args`/`options` key per §5.1's table; `mod=` renames per `Model.prim`;
    `SweepLevel` ids, continuation, label, desc and units;
  - the dec grid (plan.build's): 4 Hz to 4.19 MHz at dec=20 gives 121 points ending exactly at 4.19e6, as
    `mode="values"`; integer spans keep `mode="dec"`/`DEC`;
  - a sweep or alter of a folded or stripped parameter (`mod=rmod param=rsh`, `dev=R1 param=w` on a resistor
    whose `r` was computed, a MOS card's `w`) is an error; `dev=M1 param=w` runs;
  - on Xyce, a `dev=x1.r1` alter, a source-field sweep and a `sub=` hook inside a subckt reached by two paths
    (error), and the same with one path (runs);
  - the run-dependent card warnings: `capmod` only when an `ac`/`noise`/`xf`/`tran` runs, the diode `eg`
    warning only at temp ≠ tnom; a SPICE-mode `nf=2` instance of `x.N` bins whose HSPICE and Spectre cards
    differ (the warning, both cards named);
  - `tables.path_envs`: the envs `xyce._Deck` computes today for every HSPICE golden (non-strict), a `Cond`
    resolved per path, a strict walk's EvalError naming the path; `path_counts`;
  - errpreset rows (liberal: trapgear2 → `"trap"` with a note), reltol scaling (options reltol 1e-3: liberal
    1e-2, conservative 1e-4), maxstep = min(user, bound), `maxstepratio` (error);
  - sweep blocks expanded (outer first) with `continuation=0`, and `restart=no` keeping 1; the order-dependent
    child constructs rejected;
  - the condition and bin rule: a sweep inside one branch or bin runs, a value that crosses is an error;
  - every reachable expression evaluated per path and state: an out-of-domain value at one sweep point is an
    error naming the path; `ParamTest` per instance path, at nominal and swept values, with every severity;
  - `RunPlan.dependents` and the render copies: the E53 pattern (`rdep='rval*2'` read by a subckt default)
    keeps `rdep` symbolic on VACASK and nominal on Xyce;
  - an analysis parameter or `ic` value that reads a swept parameter (error);
  - alter lowering per engine: complete source alters, never `type` alone; source-state switching around each
    tran; variables never folded; `sub=` targets in `overridden`;
  - the noise input of §5.6; `write`/`writefinal` full saves; prevoppoint `store`/`opsolve` pairing.
- **Signals:** allpub/selected/lvlpub/nestlvl, and allpub membership (no `d1:a_int`, no `e1` flow, no
  `vamos_*` nets, an `r` = 0 short is not a current; vsource, iprobe and inductor currents present;
  `save=selected` with only currents = allpub + the currents); wildcards with `depth`/`exclude`/`devtype`/
  `subckt`; the node-over-instance rule; terminal names and indices (`D1:a` = `D1:1`, `I1:sink`), each written
  as given; probe insertion (a copy, never the input IR) and the native R and E currents; `ic`/`nodeset`/
  `readns` names resolved, unmatched ones warned; the SignalMap order; inline collapse and `+escchars`; nodes
  `A` and `a` through `Raw.exact`; Xyce printed paths (`I2.Mid` → `V(XI2:MID)`) for explicit saves, allpub,
  `ic`/`nodeset` and `DNO`; xf traces only for IR sources.
- **Outputs** (each file checked by `psfcheck` on both legs):
  - PSF goldens for op, dc sweep, ac, noise, xf, tran, sweep parent, nested parent and leaves, and `logFile`
    after status 0, 1, 2 and 3; also converted from the engine rawfile goldens of `fixtures/netlist`
    (`raw_vacask_{op,ac,tran}`, `raw_xyce_{ac,tran}`, binary and ASCII) through `results.collect` and
    `psf.write_analysis` (op to `.dc`, ac to `.ac`, tran to `.tran.tran`), no engine needed;
  - formats: `%.15e` VALUE and header reals, `%#g` `logFile` PROP reals, `precision` accepted and rejected, the
    date under a non-C `LC_ALL`, `nan`/`inf`; strings with and without `+escchars` (`net<3>`, a quote, a
    backslash);
  - the per-kind header table, the full TYPE lists, the current TRACE PROP, the `logFile` sweep-tree PROP rules
    and multi-line layout, the units, the descriptions (all from the plan's `SweepLevel`s and the SignalMap,
    with no IR passed to `collect`);
  - the legacy names (`--vamos-psf-names=legacy`): `n.tran`, `s_00i_c.<ext>`, `timeSweep.tran`, no sweep
    parent; the `tran` output filters (`outputstart`, `skipstart`/`skipstop`/`skipcount`) and a save's
    `time_window`; `dc print=yes` (the values in the log); `options save=none` (one node, a note) and
    `nooutput` (no data file);
  - noise STRUCT naming (`rref`, `resistor`, a Verilog-A module, a collision with `V`), the `nc()` saves and a
    subckt instance's STRUCT;
  - streaming: a 10^6-point synthetic rawfile converts to PSF and to nutbin under a fixed peak RSS (200 MB)
    [E85];
  - nutmeg: no `logFile`; read back by `rawfile.read_all` here, and in T2 by ngspice;
  - state files: the two-line header in the UG p.204 layout (`at 5:39:38 PM, jan 21, 1992.`), 15 significant
    digits, nodes then `V1:p`, read back with the current entries dropped and noted, `read=`;
  - the Verilog-A cache (code: the phase-2 integrator's `spectre/flow.py` and `run_vacask.py`, §1 step 10,
    §12): `-ahdllibdir`, then `CDS_AHDLCMI_SIMDB_DIR` (an error when missing or not writable), then
    `%C:r:t.ahdlSimDB` under `-outdir`, then the cwd (§2.5); two concurrent runs filling one cache, each `.osdi`
    complete (written to a temporary file and moved); a non-default `CDS_AHDL_*` (a warning, §2.8);
  - the trailer without a period, the fatal form, and the built-in text under the `none` profile, a missing key
    and an invalid template (code: S5's `spectre/log.py`).
- **psfcheck** (`tests/vamos/psfcheck.py`, both legs). A strict stdlib PSF ASCII reader written from §8 and the
  real samples by an agent other than S4, which never imports `output/psf.py`. It checks: the section order
  `HEADER [TYPE] [SWEEP] [TRACE] VALUE END` and nothing after `END`; strings on one line, decoded, with only
  §8.3's escapes; FLOAT values carrying `.` or an exponent, COMPLEX as `(re im)`, STRUCT and ARRAY arity; every
  type reference declared and names unique per section; in a swept file every point is the sweep value
  followed by each trace exactly once in TRACE order, and the last point complete; `xVecSorted` ascending ⇒ a
  non-decreasing sweep; §8.3's header keys per kind; in `logFile`, unique keys, every `dataFile` and parent
  existing, each leaf's PROP value equal to its parent's value at the leaf's index, and the counts agreeing. It
  must accept `psf/real/` (psf-parser's `tests/data/ascii` set; psf_utils' `pnoise.raw/{noiva,noiref}.noise`,
  `logFile`, `aclin.ac` and `joop-banaan.{dc,tran}`; with `PROVENANCE`) and reject each file of `psf/bad/`
  (ragged, no `END`, swapped values, a descending axis under `ascending`, an INT where FLOAT is declared, an
  unescaped quote, an undeclared type, a bad escape). Its prototype passes every native sample and rejects every
  malformed one [E83].
- **Emitter extensions:** golden decks for `Cond` (the UG p.110 `npn_mod` netlist renders its three cards on
  both engines) and the E56 walk cases (`Scope` with a K pair in a branch, `item_exprs(Cond)`, `element_at`,
  `_multiplied` with `m=` in a branch); Spectre bins in group order (a 12-entry group in which entries 2 and 10
  overlap selects entry 2 on both engines) and default bounds; variables and overridable `sub=` targets; plan
  control blocks with per-type saves and `strictsave=2`; source prints (a given-dc waveform as `type="dc"` on
  VACASK; a derived dc as an expression on Xyce, and a `DC` field on every Spectre waveform source there);
  `'007'` quoting; `r` = 0; the `G` element for `isnoisy=no`; the noise input `AC 1 0`; `.IC` only in tran
  decks; the Xyce `.STEP` order and an op under two `.STEP`s; `read_prn` with `.STEP`; the nested-subckt lift
  (UG p.86's inheritance example) on VACASK; Verilog-A on Xyce: the letter form with instance parameters, the
  card-parameter refusal, the Y form for an unattributed module, `device_summary_problems` on a summary that
  lists a built-in at a module's level (`M level 77 (BSIM6)`) and on one that lists the module (`RESISTOR
  (resistor)`), `output_problems` on the smoke patterns; an options-free netlist prints temp=27 tnom=27 in both
  decks; goldens for `vccs`, `ccvs` and `cccs` with `probe=` on a vsource and on an iprobe, `mutual_inductor`,
  `jfet` and `mos1`/`mos2` cards on both engines. Every existing `test_netlist_*` golden is unchanged.
- **HSPICE-route regressions** (§4; each fails without its shared fix): `spice.parse` (hspice) finds a module
  in an `` `include``d file and fills `Netlist.va_modules`; a vcs-ams-style Xyce deck prints an
  `xyceModelGroup` module as its letter device, and `xyce.smoke` refuses a built-in binding; `Netlist.left_out`
  survives `dataclasses.replace`; `scale_source` keeps `Source.spectre`; `.param M_DEGPERRAD=2` (case
  sensitive) prints as `vamos_M_DEGPERRAD` on VACASK; `cosim.scan_raw`/`_fix_points` are the moved functions.
- **Document** (`test_spectre_docs.py`, the vcs-ams `TestDocs` pattern): every inferred tag of this design
  before §13 (`[I…]`, and the `I …` part of a combined tag such as `[M …; I …]`), outside §0's evidence table
  and code spans, names a `§14 q.N` that exists (63 such tags at this revision, every one naming a question).
- **CLI and cpp** (WSL only: Cygwin has no cpp): `-E -D -U -I` with and without `-disableCPP`; origins, title
  and language per file from the markers; a relative `-Iinc` marker resolved against the cwd; the
  `<built-in>`/`<command-line>` markers skipped; cpp's stderr captured as notes; a `#include`d SPICE file; a
  `//` title kept; an apostrophe in a SPICE `*` comment gives no message (`-traditional-cpp`, E82); `#` lines
  without cpp (error); a macro name inside a SPICE `'…'` expression (warning).
- **Fragments:** `+config`, `+pre_config` and `+paramdefault`, with `file:line` origins and the `+config:`
  prefix in messages.
- **Log:** `+log`/`=log`/`-log` routing (`=log`: nothing on stdout; stderr empty in every mode), the message
  classes, `-maxwarns`/`-maxnotes` with their to-log variants and the message id.
- **Signals** (WSL): `nohup` keeps SIGHUP ignored; Ctrl-Z stops the engine with vamos; SIGUSR1 during a long
  VACASK tran prints the status line and leaves the engine running; after SIGKILL to vamos no engine remains
  (`vamos_testlib.reap` finds nothing); an engine killed by someone else gives status 3.

### 11.2 T2: end-to-end (WSL; both engines unless marked)
Values asserted in the PSF, `logFile` contents and exit status.
1. The §7.3 RC example: `out(5 ms)` = 0.98173 (2k: 0.86469) and `out(1 Hz)` = 0.99996 − j0.00628 [E74]; sweep
   parent and leaves.
2. dc sweeps of a source, `temp`, a top-level parameter, a model parameter (one renamed by `SPECTRE_MASTERS`)
   and a subckt-instance parameter, primary and dependent (`X2.R1`); the swept value restored for the next
   analysis.
3. A sweep block with dc and ac children and a nested sweep; repeated sweep values (`values=[1k 1k 2k]`);
   the nested leaves in the right order on Xyce; an op child under two sweeps.
4. Noise of a 1 kΩ/1 kΩ divider: `out` = 2.879e-9 V/sqrt(Hz) at 27 °C; a `R1` STRUCT with `rn` and `total`;
   `in`/`gain` with `iprobe=V1` at V1's default `mag=0`; the same `out` without `iprobe` (§5.6); a resistor
   inside a subckt (`I2.r3`).
5. `xf` (VACASK): the transfer function 0.5, named `V1:p`; no trace for an iprobe or a short; on Xyce the run
   is rejected (exit status 2).
6. `alter dev=Vdif param=type value=pulse` between an op and a tran, then back to dc (the UG op-amp pattern:
   `type=dc dc=0 val0=0 val1=2 width=1u delay=10ns`): the source's node is 2 V at 0.5 µs and 0 V at 1.5 µs
   on both engines, and the op before and after it sees dc = 0.
7. A SPICE-mode file (`.op .ac .tran`): `opBegin.dc`, `frequencySweep.ac`, `timeSweep.tran.tran`; a
   `simulator lang=spice` model section inside a `.scs` file.
8. `include … section=` with a library file; `ahdl_include` of a Verilog-A resistor declaring `R`, given
   `R=3k` (VACASK); on Xyce an unparameterized plain module and a module carrying `xyceModelGroup` attributes
   with an instance parameter (the ADMS `toys/resistor.va` pattern [E64]).
9. A model group (BSIM4 bins split in L and W): every device on its group entry; an edge geometry exactly on
   `lmin`; a MOS without `l`, taking the card's and the master's default, equal on both engines.
10. An inline subckt binning by `area` with same-named branches (the UG `npn_mod` structure): the outputs name
    the devices `q1 q2 q3`; a sweep of `area` inside one branch runs, one that crosses it is an error.
11. Exit status: one failing analysis among good ones (status 1, the others written); a failing sweep point
    (the leaves before it written, status 1); a tran that aborts mid-run (status 1, not 0 [E66]); a VACASK bind
    failure (status 1, not 0); a parse error (status 2, the `terminated prematurely` trailer); SIGINT during a
    long tran (status 3: the finished analyses and the partial tran written, the trailer); SIGUSR1 (a status
    line, the run continues).
12. The §9 ADE command line end to end: `psf/` holds `logFile` and the data files, `spectre.out` ends with
    the trailer, and `spectre.ic`/`spectre.fc` hold every node.
13. `-format nutascii` and `nutbin`, loaded by ngspice (`ngspice -b` with `load` and `print`, which keeps
    vectors `A` and `a` apart [E87]; `needs_ngspice`) and, for nutbin, VACASK's `python/rawfile.py` `rawread`
    (`needs_vacask_rawread`); no `logFile`.
14. `SPECTRE_DEFAULTS="+log %C:r.out"` writes `x.out`; `-outdir`; a netlist on stdin.
15. A fixture modelled on the UG 5.1.41 fully differential op-amp (pp.112-115: mos3, diodes, library
    sections, infinite probe resistors, `xf`, `noise` with `oprobe=Edif oportv=1`, `alter`, two `tran`s with
    `errpreset=conservative`), run with `-E` (it starts with `#define`). The engines agree within ~1 % (`xf`
    on VACASK only; `badmos3=1` written with its warning, §3.9).
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
    at 127 °C (the warning); the NetlistParse.rs `model/ternary.scs` card (`vto=(temp >= 27_C) ? 0.90 : 0.75`)
    at temp 25 and 50.
23. Nested subckts: UG p.86's inheritance example on both engines (VACASK through the lift, §4.5 item 11).
24. A diode with `cjsw` and `fc=0.3`: C(v) above fcs·vjsw follows Spectre's depletion formula with `fcs=fc`
    on both engines.
25. An ADE-style netlist with subckt instance `I2`: an explicit save of `I2.Mid`, allpub, an `ic` on `I2.Mid`
    and noise of `I2.r3`, on Xyce (`XI2:…`) and VACASK.
26. A parameter read by a subckt default under `sweep param=rval values=[1k 2k]` (the E53 pattern) follows rval
    on both engines.
27. Nodes `A` and `a`: VACASK writes both, distinct; Xyce stops with the case-collision error.
28. **A real Spectre run, rebuilt.** psf-parser's `tests/data` is one complete Spectre 23.1.0.242 run of one
    netlist (§0 [S]), which `psf/real/` vendors; until now only `psfcheck`'s acceptance and the format facts
    used it. The deck reconstructs that netlist from its outputs [E101]: title `#RC Low-Pass Filter`; `Vin (in
    0) vsource type=sine sinedc=1 ampl=1 freq=1k mag=1`; `R1 (in out) resistor r=1k`; `C1 (out 0) capacitor
    c=1u`; `myop dc`; `mydc dc dev=Vin param=dc start=0 stop=2 step=0.1`; `myac ac start=1 stop=1M` (the
    points per decade read from `myac.ac`); `mytran tran stop=0.5 step=5e-4` (moderate: maxstep 0.01, as its
    header says); `mysweep sweep dev=R1 param=r values=[1k 2k 3k] { dc1 dc …; ac1 ac …; mynestedsweep sweep
    dev=C1 param=c values=[1u 2u 3u] { ac2 ac … } }`, without `mymonte` (an error in v1; with it go its
    nominal `dc2` and `tran1`) and the `myinfo` statements (a note, no file). On both engines the results
    directory is compared with `psf/real/`, leaving out the entries and files of those omitted statements:
    exactly, the set of files and their names; `logFile`'s keys, parents, PROPs and order; each file's header
    keys, TYPE list, TRACE names and order, SWEEP variable, units and grid, and descriptions, except
    `version` and `date`; within the cross-engine tolerance, the values (`in` = `out` = 1 V at the op;
    `in(t)` = 1 + sin(2π·1 kHz·t)). S4 owns the structural assertions, the phase-2 integrator the run.
29. **A production Verilog-A model on the default engine** (VACASK, `needs_vacask_src`). VACASK's own PSP103
    tree compiles with openvaf-r 20260616 (rc 0, 3 s [E98]); its module body comes from an `` `include`` and
    its 413 parameters from macros (§3.1). The deck is a Spectre transcription of VACASK's ring benchmark
    (`benchmark/ring/vacask/runme.sim`, a 9-stage PSP103 ring oscillator: `ahdl_include` of the tree read in
    place, never vendored; `model psp103n psp103va type=1 …`, exercising §4.5 item 12's lower-casing), compared
    with VACASK's native run of `runme.sim` at a tight tolerance (the same engine and model). On Xyce the
    expected v1 refusal of the card parameters (exit status 2).
30. **Controlled sources and the remaining masters** on both engines: `vccs` (sink/src order), `ccvs` and
    `cccs` with `probe=` naming a vsource and an iprobe, `mutual_inductor` (`coupling ind1 ind2`), a `jfet`,
    `mos1` and `mos2` cards; a `mos2` card without `nsub`, whose drain current rises with vds (the written
    `nsub`, §3.9 [E95]) equally on both engines. The XDM decks hold only resistors, sources, inductors,
    capacitors and diodes, with dc and tran analyses (E100), so T3a cannot cover these.

### 11.3 T3: external decks
- **(a) XDM** (`test_spectre_xdm.py`, written by S6 in phase 1b, §12): the 12 Spectre decks of Xyce_Regression `Netlists/XDM/SPECTRE`
  (d4685581), ten of them real ADE netlists, with D1N3940.scs and two PWL data files. Their hand-written Xyce
  golds run on Xyce with rc 0 in 0.11-0.21 s each [E90]. vamos spectre on Xyce is compared with the gold run
  on Xyce at XDM's tolerances (absTol 1e-5, relTol 1e-3, zeroTol 1e-10), and VACASK with Xyce within 1 %; the
  name map: `RR1`, `VV1`, `XX1:RR1` are `R1`, `V1`, `X1.R1`. Expected dispositions: `bsimsoi_translation`
  exits 2 naming `bsimsoi`; `statistics_line` runs at nominal; the info statements, `sensfile`,
  `checklimitdest`, `rforce`, `digits`, `cols` and `pivrel` give notes. A disagreement with a gold is settled
  against [M]: the golds are XDM's own reading (for example the isource with no `ampl`).
- **(b) Cadnip engine oracle** (optional, `VAMOS_CADNIP_VACASK`; skipped otherwise). Inputs:
  `fixtures/spectre/cadnip/*.scs` in the subset Spectre and VACASK share: no `type=` sources, no integer
  division, no `% _ c meg x` suffixes, no node groups or SPICE sections, primitives R/C/L/vsource/isource with
  `dc` only. Harness: (1) the file's committed dump (`cadnip/<file>.cst`, made by `cst/regen.sh`) must tile it
  with no Error node, otherwise the case errors, because the build drops statements silently [E76]; T0 checks
  the tiling, so no `spectre_dump` is needed at test time; (2) line 1 is stripped; (3) `top.sim` = `ground 0` + a
  `model <p> <p>` line per primitive master + `include "body.scs" lang=spectre` + vamos's rendered control
  block; (4) `SIM_MODULE_PATH` as the `needs_cadnip` gate reads it from the environment (the Cadnip build's
  device directory); (5) every rawfile column is compared by name
  with vamos→VACASK (rel 1e-9; the same engine). It checks hierarchy, parameter scoping and overrides, inline
  subckts and conditionals independently of vamos's emitter; a pilot deck matched exactly [E76]. It is not a
  fidelity oracle. VACASK's own `test/spectre_*.scs` (VACASK dialect: quoted enums, `model vsrc vsource`, in no
  manual) are smoke tests of the harness only, never vamos inputs. The build recipe is kept in
  `cadnip/BUILD`.
- **(c) CMC** (opt-in, `VAMOS_CMC=1`). The 48 CMC benchmark decks are HSPICE decks with HSPICE G-2012.06
  listings at 25 °C (§3.13) [E91]: (1) each deck parses as a SPICE-mode top file with `-I ../code` and renders
  for both engines, or fails naming the construct. The expected failures, recorded per deck in
  `cmc/<deck>.json`: `.alter` (18 decks), `.noise` (3) [E91], and the 26 BSIMCMG108 and BSIMCMG110 decks,
  whose cards name the type `bsimcmg` while the `.hdl` tree (`bsimcmg.va` → `bsimcmg_main.va`) declares
  `bsimcmg_108` or `bsimcmg_110` [E100]: without cadence2xyce.pl's stem alias, which vamos does not adopt
  (§3.1), the card binds no module, and the error names the module the tree declares. The decks are read in
  place, unmodified. (2) On Xyce every CMC deck is an expected refusal in v1 (§4.5 item 13): each CMC
  module's level collides with a compiled-in ADMS device. The PyMS reference that revision 3 counted as
  32 of 48 decks within the `.lis` (`run_adms_tests.sh`'s steps with cadence2xyce.pl) [E91] ran Xyce's
  built-in ADMS models, not the PyMS modules (every Device Count Summary lists `M level 77 (BSIM6)` or
  `M level 1xx (BSIM-CMG FINFET …)`), on `/usr/local/bin/Xyce` without `env_for`, the 2026-09-29 library that
  vamos never runs; under `env_for` the same BSIM6 `idvg_nmos` deck ignores `GEOMOD` and ends in a
  segmentation fault, rc 139 [E99]. So that reference is no evidence for lifting the card-parameter refusal.
  The tier pins `engines.xyce_bin()` with `env_for` (the library vamos runs), applies §4.5 item 13's Device
  Count Summary rule (`device_summary_problems`) in the harness, and asserts the refusal; a lifting needs a
  route that avoids the colliding levels (phase 3). A spectre run is never compared with a `.lis` unless
  temp = tnom = 25. (3) VACASK is a reported skip while openvaf-r rejects the CMC Verilog-A files [E91]; the
  default engine's production-model coverage is e2e 29 (PSP103). The decks are not vcs-ams gold (§3.13).

**Third-party PSF consumers** (WSL, reported skips when absent): the vendored psf-parser parses every golden,
compared after decoding each backslash-x as x (it returns escapes raw [E83]); psf_utils (`VAMOS_PSF_UTILS`, its
dependency path in `VAMOS_PSF_UTILS_DEPS`: ply at tag 3.10, inform, arrow, six, quantiphy; numpy from the system;
never vendored) reads the data files (not `logFile` or parents), and its position-based values must equal
`psfcheck`'s [E83].

**Review:** an adversarial review of the whole diff before commit, as for vcs-ams, then fix rounds, a
verifier and a guide trial (§12, phases 2b-2f).

## 12. Work breakdown

vcs-ams is merged (13e71da; round 6 at 5e0f967), so nothing orders this work against it: phase 0 starts
from 5e0f967, and every phase keeps the merge gate of §4. Agents that touch `spice.py`, `expr.py`,
`tables.py`, `vacask.py`, `xyce.py`, `rawfile.py`, `optable.py`, `cli.py`, `backends/` or `ams/` keep every
existing test green, unchanged.

**Phase 0 (contracts and oracles, frozen afterwards; owner S0):**
- `ir.py`: the §10 block (the appended fields, `Instance.folded` and `Netlist.va_modules` among them,
  `Value`, `VaModule` (new, §4.1 item 16), `SweepSpec`, `SaveSpec`, `ParamTest`, `Cond`, `Vary`,
  `Correlate`, `StatBlock`, `Item`/`BranchItem`/`ITEM_TYPES`, `flat_items`, `flat_analyses`, the widened
  `ics`/`nodesets`), and `Netlist.left_out` used by `spice.py:343, 3037`;
- `expr.py`: the keyword signatures and the data tables of §10 as stubs that keep today's behaviour for
  `dialect="hspice"`; `RESERVED` and `VACASK_CONSTANTS` as §4.2 says;
- `tables.py`: the `dialect` keyword of `model_params` (raising for `"spectre"` until S3), `ParamRule` and
  `MasterRow` (with `match`, `warn` and `instance`), `SPECTRE_MASTERS` as data (built from ref5 against each
  target's parameter list, every ref5 default of every master compared with both targets' sources, §3.9; S1
  builds models from it, S2 decides its run-dependent rules, S3 prints from it) together with
  `test_spectre_masters.py`, one test per master, which fails on a ref5 parameter of that master with no
  disposition row (S0 writes the data and the test together), the `*_spectre` bin
  signatures, `scale_source` with `dataclasses.replace`, and `PathEnv`/`path_envs`/`path_counts` implemented
  (§4.4; tested against `xyce._Deck`'s envs on every HSPICE golden);
- `spice.py`: `Decl`, `FileRef`, the `Resolver` protocol, `Fragment`, and the `declare_fragment`,
  `parse_fragment` and `va_modules` signatures as stubs;
- `spectre.py`: `SpectreParseOpts`, `Stmt`, and the `statements`, `parse` and `resolve_source` signatures;
- the dataclasses of `plan.py` (`Target`, `Setting`, `SweepLevel`, `AnalysisStep`, `Action` with §5.1's key
  table in its docstring, `RunPlan`), `signals.py` (`Ref`, `SignalMap`, the psf type values) and
  `spectre/results.py` (`EngineResult`, `Signal`, `AnalysisResult`);
- `rawfile.py`, implemented (S3, S4 and phase 2 read through them): `Raw.exact`, `iter_rows`, and
  `scan`/`fix_scanned` moved from `backends/cosim.py`, which re-exports them as `scan_raw` and `_fix_points`,
  with `fix_points` rebuilt on them (§4.6); the `read_prn` signature (S4 implements it);
- `output/psf.py`'s `PsfHead` and format constants, `psf_string` and `psf_date` signatures;
- the emitter signatures (the `render` keywords, `names_for`, `xyce.output_problems` and
  `device_summary_problems`), as stubs;
- `spectre/job.py`: `SpectreJob` (with `notes` and `defaults`), `Settings` and the `settings` signature;
- `optable.py`: `scan(option_chars=)`, `VamosOpt.also`, the `spectre` context, `psf_names`, `unmapped_notes`,
  the spectre branches of `vamos_option_effects` and `vamos_options_help`; `test_vamos_driver.py` is not
  changed (§4, §10);
- `vamos/proc.py` (moved from `backends/nvc.py`, which imports it back and keeps its own `INTERRUPT_GRACE`,
  §2.7); the guard is `test_vamos_run.py`'s `TestStreamSignals`, with every other existing test;
- `ams/engines.py`: `choose_engine` (moved; `ams/flow.py` wraps it), `env_for(nvc_libdir=None)` and
  `tool_rows` (`ams/flow.compile_tools` rebuilt on it, its output unchanged);
- `cli.py` (the dispatch rule, `PERSONALITIES["spectre"]` as a stub, `_ROLES`), `tools.invoked`,
  `tools.SHIM_NAMES`; the `vamos/banners/spectre.json` skeleton; `vamos/licenses.json`'s `cpp`;
- tests: `test_spectre_contract.py` (constructs every phase-0 dataclass on the Cygwin 3.9 leg and pins the
  `ir.py` field order on both), `test_spectre_masters.py` (above), `spectre_testlib.py` with the gates, `psfcheck.py` with `psf/real/` and
  `psf/bad/`, `test_spectre_docs.py`, and the HSPICE-route regressions of phase 0's own shared fixes (§4);
- the fixture directories of §11 and their third-party copies (the NetlistParse.rs corpus and dumps without
  the manual-citing files, §11 Licences; psf-parser vendored; the PSF samples; the XDM decks; the committed
  `cadnip/*.cst`), with `LICENSES/MIT.txt` and the `LICENSE`/`fixtures/README` provenance;
  `engine_facts/E<nn>/` harvested from the T1 rows of §13 (E1-E16 rewritten where their decks were not
  kept), with `test_spectre_engine_facts.py`.

Done when: every phase-0 dataclass constructs on both legs (`test_spectre_contract.py`, and E49's check of
the §10 block); every ref5 parameter of every v1 master has a disposition row in `SPECTRE_MASTERS` and
`test_spectre_masters.py` fails on a missing one; the moved and widened code passes its existing tests
unchanged plus the new phase-0 tests; and the merge gate (§4) is green on both legs.

**Phase 1** (parallel; each agent owns its files and tests; nobody commits):

| agent | files |
|---|---|
| S1 language | `netlist/spectre.py` (`statements`, `parse`, `resolve_source`, the two-phase reading and the SPICE include splice, `insensitive`, `Cond`/`ParamTest`/statistics, `Instance.folded`); all of `expr.py`: the dialects (number, parse, evaluate, user functions, constants, `to_text`) and the printers' new forms (`cpow`, `hypot`, `fmod`, the bitwise PrintErrors, `$tnom`), since the printers live in `expr.py` (`expr.py:869-1253`); from phase 1 on every `spice.py` change (S0 added the phase-0 stubs): `declare_fragment`, `parse_fragment`, the `spectre-spice` switch points (§4.3), `va_modules` with its preprocessor, `d_hdl`, `_colon_clashes` over `Cond`; `test_spectre_cst_oracle.py`, `cst/DIVERGENCES`, `cst/GAPS`, `lang/`; the SPICE-corpus acceptance; the HSPICE-route regressions of S1's shared fixes (§4) |
| S2 plan and signals | `netlist/plan.py` (per-path and per-state evaluation on `path_envs`, paramtests, the condition/bin rule, the run-dependent card rules, the folded-target and Xyce per-path checks, the SPICE bin comparison, source states, `SweepLevel`s and the grids, continuation, options actions, `dependents`), `netlist/signals.py` (probes and native currents, the noise input, allpub, SignalMap order and psf types, the render copies) |
| S3 emitters and tables | the `netlist/{vacask,xyce,tables}.py` extensions of §4.4 and §4.5: walks over `Cond`, the Spectre card path and `SPECTRE_MASTERS` printing, bins, `render` and `names_for`, `xyce._Deck` on `path_envs`, sources, quoting, zero resistors, the nested-subckt lift, Verilog-A names and the Xyce Verilog-A route with `output_problems` and `device_summary_problems` (also in `xyce.smoke`), Xyce printed paths, per-type saves, `strictsave`, the options lines; goldens and engine tests of every §7 mapping row; the HSPICE-route regressions of S3's shared fixes (§4) |
| S4 outputs | `output/{psf,nutmeg,statefile}.py`, `spectre/results.py` (streaming, sweep splitting, noise/xf transforms), `rawfile.read_prn`; tests against `psfcheck` (both legs) and the vendored psf-parser (WSL); the rawfile-golden conversions; e2e 28's structural assertions. S4 writes the PSF writer from §8: signal names come from the SignalMap (§6.4), never from a column-name heuristic, and the date from §8.2's fixed tables, never `strftime` [E86] |
| S5 command line and log | `personalities/spectre.py`, `spectre/{args,percent,cpp,log}.py`, `spectre/job.settings`, `vamos/banners/spectre.json`, `cli.py`'s dispatch behaviour, `shims/spectre`, the spectre CLI tests (a new file, `test_spectre_cli.py`), and the parts of the guide's spectre section that need no run (§10) |

Done when (per agent): its T0 lists are green on both legs, the phase-0 contracts are unchanged
(`test_spectre_contract.py`), and the merge gate is green.

**Phase 1b (S6 netlist integration, after S1–S3):** `spectre.parse` → `plan.build` → `signals.resolve` →
`render` on both engines for e2e 1–10, without `flow.py`, and the T3a XDM decks through the same chain
(`test_spectre_xdm.py`, which S6 writes). The results directory does not exist yet, so S6 first writes those
decks with both expectation files: the Spectre-level `expect.json` (§11's layout) and, beside it, an
`engine_expect.json` that states the same checks in engine terms: the engine columns `names_for` gives,
values at points in the engine's raw output, the exit status of the engine run. S6 owns the seam fixes in
`netlist/`, keeping every agent's tests green (vcs-ams needed its N4 integration agent for this, and it found
eight seams). Done when: e2e 1-10 and the T3a decks match their engine-level expectations on both engines,
and the merge gate is green.

**Phase 2 (integrator):** `spectre/{flow,run_vacask,run_xyce}.py`, with §2.7's signal handling through
`vamos/proc.py` and its exit-status mapping; the remaining e2e decks (11-30) and their `expect.json`; the T2
tests and T3 tiers (the Cadnip harness of T3b, the CMC tier of T3c); the run-dependent parts of the guide's
spectre section, from real runs (§10). Done when: the WSL gate run (T0, T1, T2, T3a) and the merge gate are
green on both legs.

**Phases 2b-2f (to commit), as vcs-ams's phases 3-5 and repair rounds were run (`VAMOS_AMS_DESIGN.md` §10):**
- 2b, review: an adversarial review of the whole diff (several lenses), every finding re-checked by an
  independent skeptic;
- 2c, fix rounds: the owners of phase 1 fix the confirmed findings in their files, each with a regression test
  that fails without its fix;
- 2d, verifier: the whole `tests/vamos` suite on both legs, and an ivtest/hazard3 regression gate run
  (`regress/`), because phase 0 moved code out of `backends/nvc.py` and `backends/cosim.py`, which every simv
  run uses;
- 2e, guide trial: a fresh agent follows the guide's spectre section literally, and what it finds is fixed;
- 2f, commit, when the user approves it, and the `VAMOS_PLAN.md` status update.

**Phase 3 (after v1):**
- **montecarlo on VACASK** through its native `mc … endmc` loop [VACASK `cmd-analysis-mc.md`: samples, seed,
  Latin hypercube, a distribution function per call site, nominal re-elaboration after the loop]:
  - a process `vary` becomes a top-level distribution-function call site, mismatch a per-subckt-instance one
    (`parameters q_l=agauss(q, s, 1)` inside each subckt that reads q; VACASK draws per call site and
    instance);
  - the process value is the mismatch mean [M ref19 p.175];
  - `correlate` is built from independent normals (Cholesky);
  - `lnorm` is p·exp(agauss(0,s,1));
  - truncation: Spectre truncates every gaussian, and the one underlying `lnorm`, at 4 σ by default,
    rejecting and redrawing a value outside it [M ref19 p.179], while VACASK's `gauss`/`agauss` draws are not
    clipped (`docs/cmd-analysis-mc.md:47`), so every mapped Monte Carlo run would draw from a different
    distribution, not only a run with a `truncate` statement. Phase 3 either draws the samples itself, with
    Spectre's truncation (4 σ, or the `truncate` factor), and runs them through VACASK as a sweep over the
    drawn values, or adds a truncation to VACASK's distribution functions; until one of them exists, every
    mapped Monte Carlo run gets a warning naming the untruncated tails, and `truncate` stays an error;
  - the outputs: nominal files under the child names, `<m>_<c>.montecarlo` parents, `<m>-NNN_<c>` leaves
    [S];
  - a note that the draws differ from Spectre's random sequence;
- `stb` → VACASK `acstb`, `sp` → `acsp`, `tran noisefmax>0` → VACASK transient noise, `pwlperiod` → `pwl
  period=`, AM/FM sources → `type="am"`/`"fm"`, all VACASK only [VACASK `cmd-analysis-acstb.md`,
  `cmd-analysis-acsp.md`, `cmd-analysis-trannoise.md`, `dev-builtin-src.md`];
- Verilog-A card parameters on Xyce, per module, on a route that avoids the levels of Xyce's compiled-in
  ADMS devices (a unique PyMS level per module, or, as a separate disposition, Spectre's built-in
  `bsimcmg`/`bsim6`-family masters mapped onto Xyce's own ADMS devices), once T3c shows that route matches the
  `.lis` under `env_for` (§4.5 item 13, §11 T3c; the revision-3 reference ran the built-ins [E99]); the CMC
  Verilog-A models on VACASK once openvaf-r compiles them;
- Spectre's built-in `psp103` and `vbic` masters, and `bsimbulk`, mapped onto the OSDI modules VACASK ships
  (`psp103v4.osdi`, `vbic_1p3{,_4t,_5t}.osdi`, `bsimbulk106.osdi`; also `bsim4v8.osdi`) [E98], after a check of
  their parameter names against ref5/ref19; e2e 29 already runs PSP103 through `ahdl_include`;
- `altergroup` (trivial on Xyce through state replay; a model-parameter diff on VACASK);
- `info` (`what=parameters`, then `what=oppoint` with per-device output-variable maps);
- `xf` on Xyce; `bsource`; `pss`/`pac`/`pnoise`/`hb` on VACASK; a psfbin writer.

## 13. Verification record

Manual checks [M] are cited where they are used. Real Spectre output files [S] are cited the same way. The
experiments [E] were run in WSL (and on Cygwin Python 3.9.16 where a row says so) in throw-away directories.
The script and deck names the rows give (`spdesign/exp/…`, `rv_engmap/…`, `sfid/…`, `rv_impl/…`,
`sp3/<area>/…`) name the design sessions' scratch directories, which are not in the repository: they record
which experiment produced a fact, and the T1 rows are harvested into
`tests/vamos/fixtures/spectre/engine_facts/E<nn>/` in phase 0 (§11 T1, §12), where each deck and its
expectation are kept. E1-E16 come from the design (`spdesign/exp`: VACASK `e1*.sim`, Xyce
`x1.cir`-`x10.cir`); E17-E48 from the review of revision 1 (`rv_engmap/t1`-`t22`, `sfid/`, `rv_impl/`: E21
`sfid/dec*`, E34 `rv_engmap/t14/deg.sim`, E44 `rv_impl/cond_walk.py`, E45 `powtest.py`, E46 `trtest.py`, E47
`cpp.sh`, E48 `dc_job.py`); E49-E93 from the re-validation of 2026-10-04 (`sp3/<area>/`; each row names its
decks); E94-E101 from the review of revision 3 the same day (`sp3/IMPL/`, `sp3/FID/w/`, `sp3/COMP/`, re-run
by `sp3/FIX/v1.sh`-`v4.sh` with their outputs `v1.out`-`v4.out`). On 2026-10-04 every one of E1-E48 was
re-run on the builds of §0's [E] row, with unchanged results [E78]; rows whose details moved say so.

**Tiers.** The rows are of three kinds, and only the first is harvested into
`tests/vamos/fixtures/spectre/engine_facts/E<nn>/` (§11 T1; E1-E16's decks rewritten where they were not
kept), so that an engine upgrade that changes a fact fails and names the rule that rests on it:
- **engine facts → T1**: E1-E13, E15-E43, E45's engine half (`pow(0,0)` on both engines), E47 and E82
  (`needs_cpp`), E51, E63-E75 (E63 and E64 under `needs_pyms`, run through `env_for`), E77, E81, E84, E95 and
  E96;
- **observations of today's code → not asserted as facts**: E44, E45's `expr` half, E46, E48-E50, E52-E54,
  E56-E62, E79, E80, E85 and E94 record what the code did before this design changes it. Each becomes the
  inverted regression test of the fix that changes it (it fails before the fix), in the owning agent's T0
  list, or, for E48/E49, `test_spectre_contract.py`;
- **external-tool and corpus facts → their own tier and gate**: E14 and E83 (the PSF consumers,
  `needs_psf_parser`/`needs_psf_utils`), E55, E88 and E93 (the CST oracle on committed dumps, T0), E76
  (T3b), E86 and E101 (the samples: `psfcheck`'s acceptance, e2e 28), E87 (e2e 13), E89 (an evaluation of an
  alternative, not asserted), E90 and E100 (T3a), E91, E97-E99 (T3c, T0's preprocessor fixture, e2e 29), E92
  (a build record, not asserted) and E78 (the re-runs, which their rows' tiers cover).

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
| E14 | psf-parser (MIT) on the real Spectre noise file, a `logFile` and an info file | all parse, so it can be the test oracle. Re-checked 2026-10-04: it parses 59 of 65 real samples, among them the noise, `logFile` and info files, but also accepts ragged, unterminated and reordered files, so it is a consumer check and `psfcheck` is the oracle (§11) [E83] |
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
| E34 | VACASK `sp_diode` at 1 mA with `eg` 1.11 and 1.124481, at 127 °C and −40 °C | 4.8 mV and 3.2 mV apart. Re-checked 2026-10-04 on the round-6 `sp_diode` (3becb73d): 4.82 mV and 3.23 mV [E78] |
| E35 | VACASK nodes `007`, `7`, `00`; Xyce the same | bare `007` is a parse error; quoted `'007'` (2.0 V) and `7` (1.0 V) are distinct, and `'00'` (−1.5 V) is not ground; Xyce keeps them distinct unquoted |
| E36 | inductor and iprobe currents, 1 mA into terminal 1 / `in` | VACASK `l1:flow(br)`, `ip:flow(br)`; Xyce `I(L1)`, `I(VIP)`; all +1 mA |
| E37 | VACASK: a dc source with `val0=0 val1=2 width=1u delay=10n`; `alter instance(…) type="pulse"` alone; then with the fields printed but no `fall` | alone: VACASK's defaults, a 0→1 V step at about 1 ns that stays high for the whole 3 µs; with the fields: 2 V, still 2 V at 1.5 µs (`fall=0` is "no fall"); both exit 0 |
| E38 | VACASK: pulse sources with and without `dc`, an alter and a sweep of `val0` | the op follows `val0` in both (0.7, then 0.2 / 0.4): a derived dc follows the fields, and an explicit `dc` is ignored (E17) |
| E39 | VACASK: `@if area < 2` in a subckt, a sweep of `area` over [1, 1.5] and over [1, 3] | inside the branch: 0.5 / 0.4; across it VACASK re-evaluates the condition: 0.5 / 0.0909 (the 10 kΩ branch) |
| E40 | VACASK: SIGINT and SIGTERM during a long tran | the interrupted analysis's rawfile is left with a blank `No. Points:`; `rawfile.fix_points` plus `read` recover 467,960 points up to t = 46.8 ms. Re-checked 2026-10-04: rc 130/143, 1.65 M rows recovered [E78] |
| E41 | VACASK: a binned MOS (w < 10 µm: vto 0.5; w ≥ 10 µm: vto 1.0), `sweep instance="m1" parameter="w" values=[5u, 20u]` | 2.25 mA at 20 µm, bin 1's current (bin 2's card gives 1.00 mA); exit 0 |
| E42 | VACASK `sp_mos1`: `m1 (d g 0 0) nch w=30e-6` without `l`, and with `l=3e-6` | 25.35 µA (VACASK's default l = 1e-4) against 845 µA; exit 0 |
| E43 | Xyce: a diode card with an unknown parameter, plain and under `.STEP DMOD:BOGUSPARAM` | plain: "No model parameter BOGUSPARAM found for model DMOD" as a warning, exit 0; under `.STEP`: an error, exit 1 |
| E44 | `tables.reachable` and today's renders on the IR of UG p.110's `npn_mod` (cards used only inside `Cond` branches) | neither card is reachable; the subckt renders empty. Re-checked 2026-10-04 on 5e0f967: unchanged, and the later walks skip a `Cond` too [E56] |
| E45 | `expr` printing and evaluation of `x**0.5`, `x**0`, `0**0`, `(−2)**0.5`; pow(0,0) on the engines | `(x>0.0 ? pow(x, 0.5) : (x<0.0 ? 1.0 : 0.0))` and an `x==0` guard; 0\*\*0 = 0 and (−2)\*\*0.5 = 1 in the evaluator; Xyce pow(0,0) = 1e50, VACASK refuses it. Re-checked 2026-10-04 on 5e0f967 with VACASK 0.3.4-91 and Xyce DEVELOPMENT-202609292231: unchanged; the guard `(y==0 ? 1 : pow(x,y))` gives 1 on both engines, and HSPICE-dialect `pow` truncates y (`pow(2,1.5)` = 2) [E50, E51] |
| E46 | `spice.py` on `.tran 1n 1u 0 10n` and `.tran 1n 1u` | "tstop values must increase"; HSPICE's RMAX maxstep 5e-9 and temp = tnom = 25; source defaults from synth_step/synth_stop (1e-11 s / 3600 s). Re-checked 2026-10-04: unchanged [E58] |
| E47 | `cpp -nostdinc -undef` on a netlist whose line 1 is a `//` comment | the title line comes out empty. Re-checked 2026-10-04 with GCC cpp 15.2.0: unchanged in ISO mode; `-traditional-cpp` keeps the line [E82] |
| E48 | revision 1's `SpectreJob` on Cygwin Python 3.9.16 | "TypeError: non-default argument 'percent' follows default argument" |
| E49 | dataclass layouts on Cygwin Python 3.9.16 and WSL 3.14.4 (`sp3/IR/layout.py`, `rev2_literal.py`, `rev3_proto.py`, `posargs.py`, `sec10_literal.py`, `lz.py`; `sp3/CLI/sec10_contract.py`; re-run for this revision by `sp3/SYNTH/check_ir.py`, which applies §10's `ir.py` block to a copy of today's `ir.py`, and `check_dc.py`, which defines every §10 dataclass; re-checked after the review of revision 3 with its additions by `sp3/FIX/check_dc4.py` and `check_ir4.py`, output `v5.out`) | revision 2's §4.1 transcribed literally: `Analysis` "non-default argument 'nodes' follows default argument 'sweep'", `Source` "mutable default <class 'dict'> for field spectre is not allowed", `Netlist` "non-default argument 'saves' follows default argument 'notes'". §10's block: today's positional call sites bind (`Subckt` 8 at `spice.py:3010`, `Model` 5 at `spice.py:1544`, `Param` 3, `Analysis` 3, 75 `Instance` calls with 3) and all 24 §10 dataclasses define, on both Pythons; after the review of revision 3, 27 of 27 §10 dataclasses define and construct, and the appended `Instance.folded` and `Netlist.va_modules` bind at every positional call site, on both Pythons. The IR layouts are identical on both; IR objects are unhashable, `expr_ast` ones frozen; no IR class has an attribute named `prim inline bin_rule spectre dialect saves statistics children sweep`; `vacask_quote('007')` returns `007`, and spice.py folds `007` to `7` |
| E50 | today's `expr` on Spectre text (`sp3/IR/expr_today.py`, `temper.py`, `keyerr.py`) | `evaluate(hypot(3,4))`: "unknown function hypot()"; `to_xyce(fmod(a,3))`: PrintError; `db(10)` = 20, `agauss(1,0.1,3)` = 1, `if(1,2,3)` = 2, `nint(2.5)` = 3 accepted; HSPICE `pow(2,1.5)` = 2.0, `pow(-8,1/3)` = 1.0; `temp` → `Name('temp')`, printed `temp` (VACASK) and `vamos_temp` (Xyce); `tnom` → `Name('tnom')`; `evaluate(temper+1, {temper: 27})`: "temper is not a constant"; `M_PI` printed `vamos_M_PI` (VACASK) and `M_PI` (Xyce); `M_DEGPERRAD` not in `VACASK_CONSTANTS`; `vt(300)` a node call that `inline` leaves unexpanded; `MyF` lower-cased; `inline(f(1))` keeps a free `p`; `to_vacask(sqrt(-4))` = `(-2.0)`; `-2**2` = −4; `to_text(-(a**2))` = `-a**2.0`; `evaluate`, `fold` and both printers on `&`, `<<`, `~^`: `KeyError` at `expr.py:573`; `par'-i(vdrain)'`: "unexpected \"'\""; `deriv(ids)` an unknown call |
| E51 | the engines on expression edge cases (`sp3/IR/eng/run.sh`, `run2.sh`, `run3.sh`; Xyce DEVELOPMENT-202609292231, VACASK 0.3.4-91) | Xyce: `(y==0 ? 1 : pow(x,y))` at x = −8, y = 1/3 gives 1.0; `pow(-8,0.5)` 1.73e-16 and `pow(-8,0.25)` 1.1892 (the real part), rc 0, no warning; `pow(0,0)` 1e50, the guarded form 1.0; `pow(-2,3)` −7.999999999999998; `{fmod(-7,3)}` −1; `{hypot(3,4)}` "Netlist error"; `{-2**2}` −4. VACASK: the x = −8 case "NaN found in vector … Analysis 'op1' aborted.", rc 0; `pow(0,0)` "Zero raised to negative or zero power.", rc 1 (`rpnfunctor.h` `FwPower::ok`); the guarded form 1.0; `pow(2,1.5)` 2.828427124746190; `hypot(3,4)` 5, `fmod(-7,3)` −1; `-2**2` 4, `-x2**y2` 4, `2**3**2` 512; under `options temp=50 tnom=27`, `$tnom` 27 and `$temp` 50; `M_DEGPERRAD` 57.29577951308232 |
| E52 | a number reader built from UG p.84 against today's `expr.number`, 38 literals (`sp3/IR/rev3_proto.py`, both Pythons) | 12 differ (Spectre table vs today): `1M` 1e6 vs 0.001; `0.3MHz` 3e5 vs 3e-4; `1c` 0.01 vs 1; `5%` 0.05 vs an error; `1.234E-3p` 1.234e-3 (a note) vs 1.234e-15; `1meg` 1e-3 (a warning) vs 1e6; `1mil` 1e-3 (a warning) vs 2.54e-5; `50Ohms` an error vs 50; `1P`, `1x`, `1F` errors vs 1e-12, 1e6, 1e-15; `1D3` an error vs 1000. The other 26 agree (`5u 3.26k 2.65e3 1E-14 23pf 6.3ns 27_C 50_Ohms .148p 0.22u` …); `0.22u` == 2.2e-07 exactly |
| E53 | `spice.parse` + `vacask.render` of `.param rval=1k rdep='rval*2'` with `.subckt rsec a b r=rdep` (`sp3/IR/fold_dep.py`) | `Netlist.values` `{'rval': 1000.0, 'rdep': 2000.0}`; the top level prints `parameters rdep=rval*2.0`, the subckt `parameters r=2000.0`, so a sweep of rval cannot reach x1's r; Xyce keeps `r={rdep}` |
| E54 | today's `spice.parse` of SPICE-text `pow` (`sp3/IR/spicepow.py`) | `.param a='pow(2, 1.5)' b='2**1.5' c='(-8)**(1/3)' x=0.5 r0='1k*pow(x, 1.5)'`: a 2.0, b 2.828, c 1.0, r0 500, printed `pow(x, 1.0)` (C pow: 2.828, a domain error, 353.6) |
| E55 | NetlistParse.rs `spectre_dump` on precedence probes (`sp3/IR/nprs_oracle.scs`, `nprs_oracle2.scs`; `sp3/ORC/s57_prec.sh`) | agrees with ref19 p.475 on unary vs `**` (`UnaryOp(-2) ** 2`), `* /`, `+ -`, `<< >>`, `==` over `&`, `& \| ~^ ^~`, `&& \|\|` and right-associative `?:`; differs on `2**3**2` (`BinaryExpression(BinaryExpression(2 ** 3) ** 2)`), `5%` (a modulo that swallows the next parameter, `5% a4`, and loses the rest of the line), `^` (accepted as XOR), `!` (an Error node); `1D3` and `1x` are one literal each; `M_PI`, `hypot`, `fmod` are built-ins, `ln log10 atan2 sgn sign M_1_PI` are not, `nint` and `truncate` are (`spectre_keywords.rs`) |
| E56 | `Cond` through today's walks: E44 on 5e0f967, a stand-in `Cond`, and the `flat_items` prototype (`rv_impl/cond_walk.py`, `sp3/ENG/walks.py`, `sp3/IR/rev3_proto.py`) | `reachable`: `npn10x10` and `npn_default` False; `Scope` over a subckt whose branch holds a card and a K pair: models [], coupled []; `item_exprs(Cond)` []; `enclosing_reads` []; `element_at('l1')` None; `xyce._multiplied` [] with an `m=` X line in a branch; both decks print empty subckts. `flat_items`: `Cond, Instance(npn10x10), Cond, Instance(npn20x20), Instance(npn_default), Model, ParamTest`; `flat_analyses` `(sweep swp), (tran tr)`, where `nl.tran()` is None |
| E57 | cadence2xyce.pl's rules against today's spice.py (`sp3/SPICE/exp1.py`, r01-r10) | continuation, `.option post/ingold` (a note), `dc=`/`ac=`, `.temp`, `key = val`: handled; `abstol`/`reltol` kept with a note; `.probe` warned; `par'…'` in a value an error; a module in an `` `include``d file: "myres.va declares no Verilog-A module"; the stem alias: "model type MYRES is not supported"; Verilog-A cards merged into `y` instances |
| E58 | today's spice.py on SPICE text whose reading the `spectre-spice` dialect changes (`sp3/SPICE/exp2.py`, n01-n19) | `r1 007 00 1k` → nodes `7`, `0`; `.tran 1n 1u 10n` → start 1e-8, HSPICE's maxstep 5e-9, temp = tnom = 25; `.tran 1n 1u 0 10n`: "tstop values must increase"; `* spectre:` lines dropped, no note; `rload a 0 1k isnoisy=no` kept, no note; `simulator lang=spectre` read as an S element; `m1 d d 0 0 nch`: "L not given; the HSPICE default DEFL=0.0001 m is used"; `pulse(0 1)` → tr=tf=1e-9 pw=1e-6, `sin(0 1)` → freq 1e6, `exp(0 1)` → tau1=tau2=td2=1e-9; `.option method=gear itl4=50` stored, itl4 a note; `vm(b)`, `vp(b)`, `vdb(b)` ignored with a warning; `.temp = 125` an error; a `.temp` list: the first value, a warning; `r2 a 0 rmod` with rmod undefined read as a value; `q3 c b 0 nch mybjt` four nodes, nch a node; `x1 n 0 cell k=3` with k a body `.param` accepted; `nch.1`/`nch.2` chosen by per-finger W |
| E59 | `tables.model_params` on Spectre-shaped cards, both engines (`sp3/SPICE/exp3.py`; `sp3/ENG/cards.py`, `r_cards.log`) | diode `cjo` → `fc=0.0 vj=0.8` ("written as HSPICE simulates it"); level 49 → `xpart=1.0 cj=0.000579 cjsw=0.0` and an ACM warning; level 1 `{vto tox cj cjsw}` → `nsub=1e15 phi=0.576037 mjsw=0.33 fc=0`, KP and CAPOP warnings; level 3 → `eta=0.0998773` (×8.14/8.15), `ld=1.5e-07` (0.75·xj), `cj=1.0185e-4`, `cgso`/`cgdo` 2.59e-10, `badmos3=1`, a CAPOP warning; npn `cjs` → `fc=0 mjs=0.5`; njf → `fc=0 pb=0.8` |
| E60 | appended parameters, include order and fragments (`sp3/SPICE/exp4.py`, `exp5.py`, `exp6.py`) | `rload a 0 1k isnoisy=no` printed `rload (a 0) vamos_sp_resistor r=1000.0 isnoisy=no` (VACASK) and `rload a 0 1000.0 isnoisy={no}` (Xyce); `sub/a.sp` including `b.sp` used `./b.sp` (r1 = 1k) over `sub/b.sp` (2k) with only a note; `.include 'models.scs'` holding `simulator lang=spectre`: "an S-parameter element (HSPICE S) is not supported", "model needs 4 nodes and a model"; an undeclared override `zz=3` accepted; `spice.parse([], lines)` found `lib/models.sp` from a `lib/top.scs:4` origin; `.param p=q*2` with q in Spectre text: "parameter p uses q, which is not defined at top level"; "X1: subckt spectre_cell not found" |
| E61 | the 48 CMC decks through today's `spice.parse` (`sp3/SPICE/cmc_parse.py`, `cmc_parse2.py`) | 8/48 parse with `search=['../code']`, 0/48 without; the 13 `bsimcmg_107` and 13 `BSIMCMG108` decks stop at "bsimcmg.va declares no Verilog-A module", 108/110 at "model type BSIMCMG is not supported"; with a module scan that follows `` `include``, the stem alias and the decks cut at `.alter`: 44/48, the other 4 stop at `.ic 1=1` ("write v(node)=value, not 1"; HSPICE accepted it) |
| E62 | NetlistParse.rs's SPICE corpus (62 `tests/corpus/*.sp`) through today's `spice.parse` (`sp3/SPICE/nprs_parse.py`) | 25 parse, 37 fail, every failure named (POLY/TABLE/`.data`/`.if`, S/W/N/Y elements, missing include files, the deliberate `err_*.sp`); `b1_dots.sp` and `ex_tran.sp` stop on SPICE3 `.tran` ("tstop values must increase") |
| E63 | Xyce PyMS, `/usr/local/bin/Xyce` (`sp3/SPICE/xy1.sh`, `xy2.sh`; logs in `sp3/SPICE/out/`) | a module in a file the `.hdl` file includes compiles and runs; `.model rm myres r=2k` + `ymyres x1 a 0 rm`: I(V1) = −1.00000000e-03 (the module default r = 1k), no message; a module tagged `DIODE` level 2002 with `.model dm d level=2002 r=2k`: "No model parameter R found for model DM of type D, parameter ignored", I(V1) = −3.395e-7, and the Device Count Summary lists `D level 2002 (DIODE_CMC 2.0.0)`: the level is Xyce's built-in DIODE_CMC, so the card bound the built-in model, not the module. These ran without `env_for`, on the 2026-09-29 library; the card-parameter fact was re-run under `env_for` on both launchers with the same result (E96); T1 re-runs the rest there |
| E64 | Verilog-A on Xyce PyMS and VACASK (`sp3/ENG/r_va.sh`, `r_va2.sh` with the ADMS `toys/resistor.va` and `diode.va`; the Device Count Summaries in `sp3/ENG/va/x5.xlog`, `x7.xlog`) | Xyce: vamos's Y form for an `xyceModelGroup` module, with or without parameters (x1-x4, x6, x8): "Unrecognized parameter A for device YRESISTOR!R1", rc 1; `.model rmod r level=6` + `R1 a b rmod R=3k` (x5): V(B) = 0.25, the summary `RESISTOR (resistor)`; `.model dm d level=1002001 Is=1e-14 Rs=10` + `D1 a 0 dm` (x7): rc 0, "Numerically singular matrix found by Amesos, returning zero", V(A) = 1.01e9; an unattributed module (default r = 2000): no parameters 0.3333, `.model rc vres r=3000` still 0.3333 with no warning, `yvres r1 a b vres__vamos r=3000` "Unrecognized parameter R"; Xyce accepts `R=3k` and `r=3k`. VACASK: `model r1__va resistor R=3k`: "Parameter 'R' not found.", rc 1; `r=3k`: 0.25; `model d1__va diode_simple Is=1e-14 Rs=10`: "Parameter 'Is' not found."; an instance-typed parameter on the per-instance card works. 28 of the 54 ADMS example `.va` files carry `xyceModelGroup`/`xyceLevelNumber`, every CMC model among them (`bsimcmg_111`: MOSFET, 111, `TYPE`, 0). These Xyce runs used `env_for`'s library path (`sp3/ENG/env.sh` sets it; launcher `/usr/local/src/xyce-build/src/Xyce`) |
| E65 | a nested subckt reading its enclosing subckt's `rr` (`sp3/ENG/r_new.sh` N5) | VACASK: "Variable or constant 'rr' not defined."; Xyce: V(OUT) = 0.5, rr = 1k inherited from the X line |
| E66 | failed analyses under `strictoutput=0` (`sp3/ENG/r_fail.sh`, `r_new.sh` N2) | two parallel sources: "Analysis 'op1' aborted." …; `op1.raw`, `ac1.raw`, `nz1.raw` exist with a blank `No. Points:` and 0 rows, rc 0; a Verilog-A source that goes NaN at 1 µs: "Timestep too small. Transient analysis aborted.", "Analysis 'tr1' aborted.", rc 0, `tr1.raw` with a blank `No. Points:` and 50 rows, the last at 1.0e-6 s of stop 3e-6 s; every completed analysis fills the field. Xyce: a tran that fails part-way exits 1 ("Time step too small … Exiting transient loop", or "Maximum number of failures … Xyce Abort") and leaves a header-only RAW print file |
| E67 | VACASK save directives per analysis type (`sp3/ENG/r_save.sh` S2) | after `save v(out) v('x1:mid') i(v1)`: op1 `[out, x1:mid, v1:flow(br)]`, but ac1 `[in, out, v1:flow(br), x1:mid]` and nz1 every `n()`; `save dv(out) dv('x1:mid') di(v1)`: ac2 `[out, x1:mid, v1:flow(br)]`; `save n(r3) n('x1:r1')`: nz2 `[n(r3), n(x1:r1)]`; the column names are the same in every case |
| E68 | engine builds and environment (`sp3/ENG/v0.sh`, `v1.sh`, `r_bad3.sh`, `r_ovaf.sh`; `sp3/SYNTH/builds.sh`) | `env -u LD_LIBRARY_PATH /usr/local/bin/Xyce m3.cir`: "No model parameter BADMOS3 found for model NCH of type NMOS, parameter ignored", rc 0; with `engines.env_for`'s `LD_LIBRARY_PATH` both launchers list BADMOS3. `/usr/local/bin/Xyce` (2026-09-29, `/usr/local/lib/libxyce.so` of the same day) is DEVELOPMENT-202609292231 and `/usr/local/src/xyce-build/src/Xyce` DEVELOPMENT-202609292309, both from 1c36edca; `/usr/local/src/xyce-build/src/libxyce.so` rebuilt 2026-10-03. An OSDI file from `/opt/openvaf-r/openvaf-r` (openvaf 23.5.0): "OSDI descriptor structure size (328) is smaller than expected (352). Failed to open OSDI file"; `engines.openvaf()` gives `/opt/openvaf-r-20260616/openvaf-r` (OpenVAF-reloaded 20260616-3-g0e83f1ed), and `env_for('vacask', …, {'SIM_OPENVAF': '/opt/openvaf-r/openvaf-r'})` replaces it; `env_for('xyce', '/nonexistent', {})` gives an `LD_LIBRARY_PATH` that starts with `/:` before the Xyce library directory |
| E69 | mos3 channel-length modulation (`sp3/ENG/r_m3.sh`; vto=0.7 kp=1e-4 kappa=0.2 nsub=1e16 tox=2e-8, w=l=3u, vgs=2 V) | `badmos3=0`: VACASK −7.973e-5 A vs Xyce −7.921e-5 A at vds = 1.5 V (0.66 %), 0.31 % at 3 V, 0.32 % at 5 V; `badmos3=1`: 0.003-0.004 % apart |
| E70 | Xyce `.OP` of waveform sources printed without `DC` (`sp3/ENG/r_xdc.sh`) | `PULSE(0.3 …)`, `SIN(0.5 1 1k 1u 0 90)`, `EXP(0.2 …)`, `PWL 0 0.7 …`, `PWL 1u 0.6 …`: 0/0/0/0/0; the same sources on VACASK: 0.3/1.5/0.2/0.7/0.6 |
| E71 | Xyce sweeps of a source field (`sp3/ENG/r_new.sh` N3) | `DC {p} PULSE({p} 1.8 …)` with `.DC vamos_x_v1_val0 LIST 0.2 0.4`: 0.2 and 0.4; a `.STEP` of it with `.TRAN`: a(0) = 0.2 and 0.4, a(1.5u) = 1.8; `.STEP V1:V1 LIST 0.2 0.4`: the same |
| E72 | Xyce interrupted (`sp3/ENG/r_xsig.sh`) | a long `.TRAN` killed after 4 s: rc 130 (SIGINT), 143 (SIGTERM); `long.raw` has a blank `No. Points`, and `rawfile.read` gives 190,464 rows up to t = 19.05 ms (SIGTERM: 198,656); the standard `.prn` holds its partial table (165,441 lines) |
| E73 | Xyce `PULSE` width 1e30 and hierarchical `DNO` (`sp3/ENG/r_pw.sh`, `r_dno.sh`) | `PULSE(0 1 1u 1n 1n 1e+30)` with `.TRAN 10n 10u`: a(0.5u) = 0, a(2u) = a(5u) = a(9.9u) = 1; `.PRINT NOISE … DNO(X1:R1) DNO(R2)`: 4.1439e-18 each |
| E74 | the §7.3 RC on both engines (`rv_engmap/t1/rc.sim`, `sp3/ENG/r_engA.sh`; `r_new.sh` N1) | VACASK: out(5 ms) 0.981735 (2k: 0.864690), out(1 Hz) 0.999961 − j0.00628294, 61 AC points ending exactly at 1e6; Xyce (`.OP`; `.AC DEC 10 1 1e6`; `.STEP rval LIST 1000 2000` + `.TRAN 5e-06 0.005 0 0.0001`, V1 `DC 0 PULSE(…)`): 0.981725 and 0.864685, the AC value identical |
| E75 | openvaf-r and Verilog-A includes (`sp3/ENG/r_ovaf.sh`, `r_inc.sh`) | openvaf-r 20260616 `-D HALF` selects r = 500 (i = −2e-3); `ires.va`, including `ires_defs.include` from its own directory and given by absolute path from another directory: −2.5e-4 A (r = 4000 from the include) on Xyce `.hdl` and VACASK `load` |
| E76 | the Cadnip VACASK, 0.3.4-94-gcdf13d22 with `CADNIP_PARSERS=ON`, NetlistParse.rs d565fd3 (`sp3/ENG/r_cadnip.sh`; `sp3/ORC/s10`-`s16`, `s42`, `s48`-`s50`) | `/opt`'s build: "include of SPICE/Spectre file … requires a build with -DCADNIP_PARSERS=ON"; without `SIM_MODULE_PATH`: "File capacitor.osdi not found."; Spectre primitives without native model lines: "Master 'vsource' not found." (VACASK's own `test/spectre_rc.scs` declares `model res resistor`, `model vsrc vsource`); numbers verbatim (`1/2` → 0, `1x` → 1e6, `1meg` → 1e6, `-2**2` → 4); `50_Ohms`, `10%`, `2c` parse errors; "Variable or constant sine/pulse not defined"; "Parameter tc1 not found"; "netlistrs adapter ignoring 2 analysis command(s)", "does not yet transcribe 3 save / 4 ic directive(s)"; `Gm (2 0)(1 0) vccs gm=1m` and the next `r2` vanished, rc 0; after a mid-file `simulator lang=spice` a `.model` line became the title, the op aborted with NaN, rc 0; VACASK's `test_spectre_rc`, `include` and `subckt` print True and `bad_include` gives rc 1; `subckt/nested.scs` runs with native primitives (b = 0.5), `subckt_binning.scs` gives "Circuit has no unknowns"; the engine-oracle pilot (an inline subckt with a structural `if`, a nested subckt, an override) gave the same columns and values as a vamos-style native deck: in=1 n1=0.933333 n2=0.266667 v1:flow(br)=−6.66667e-05 x3:m=0.133333 |
| E77 | VACASK `NOT_GIVEN` under a sweep and an alter (`sp3/ENG/r_ng.sh`) | a sweep of r over 1000/4000 gives −1e-3/−2.5e-4 and restores r = 2w; a sweep of w is followed; `alter r=5000` gives −2e-4, then back |
| E78 | revision 2's engine experiments re-run on today's builds (`sp3/ENG/r_spd.sh`, `r_spdx.sh`, `r_engA.sh`-`r_engD.sh`, `r_walks.sh`, `r_ng.sh`, `r_tests*.sh`; `sp3/ORC/s46_e34_rerun.sh`) | unchanged: E1 0.5/0.25, restored to 0.5; E4 temp, `x1:rr` and `dm:is` swept and restored; E5 21/134/6 rows; E6 "Analysis 'bad1' aborted.", rc 0, "Sweep aborted @ 1/2" and "@ 2/3", no rawfile under strictoutput=2; E8 onoise 8.28804e-18, gain 0.25, tf 0.5, zin 2k; E11 0.0189733 vs 0.999961; E12/E13 the singular op rc 1; E16 "expecting sweep or analysis or newline"; E17 VACASK op 0/0/0.2/0.3, AC 1 vs Xyce 0.0851129, with `type="dc"` 0.9, sweep 0.6/0.8, AC 0.0851202, tran 0 → 1.8; E18 `.IC` op 0.2, the `.NODESET` conflict; E19 "Instance '' not found", INOISE 3.3152e-17 / 8.2879e-18 / 8.2879e+02; E20 the step order, "Analysis mode 4 is not available" then rc 139, `.DC R1:R` 0.498812/0.25; E21 25 points ending at 500 vs 24 at 399.052; E27 a blank `No. Points`, 1 group / 71 rows; E28 the three bind failures, no rawfile, rc 0; E29 1.8314 vs 0; E34 4.82 and 3.23 mV on the round-6 `sp_diode`; E35, E36 +1 mA on all; E39 0.5/0.4 and 0.5/0.0909; E40 rc 130/143, `fix_points` + `read` recover 1.65 M rows; E41 2.25 mA; E42 25.35 µA vs 845 µA. A `uic` tran starts at in(0) = 0, out(0) = 0.3 on both engines (`r_engB.sh`). The netlist suites (`test_netlist_emit`, `_rawfile`, `_expr`, `_integ`) on a private copy of 5e0f967: 156 OK on three runs (one failure on the first run did not recur) |
| E79 | the cli, shims, vamos options, banners and provenance on Cygwin Python 3.9.16 (`sp3/CLI/exp_cli.py`) | `VAMOS_ARGV0=spectre`, `spectre231`, `specsim`, and `vamos -spectre x.scs`: rc 2, "usage: vamos -<personality> [tool arguments...]" (PERSONALITIES: nvc simv vcs vcs-ams); `check_vamos_opts({'psf_names': 'legacy'})`: "--vamos-psf-names=legacy is planned (docs/VAMOS_PLAN.md) but not implemented yet"; `vamos_option_effects({analog, keep, strict, banner}, 'spectre')`: each "the spectre personality hands its command line to the real spectre unchanged", and with a stub personality `--vamos-strict` makes the cli exit 1; a personality raising `tools.ToolError('VAMOS_CPP=/nonexistent: …')` makes the cli exit 1; `optable.scan` takes `=log` and `x.out` as positionals; `'spectre' in SHIM_NAMES` False, and a directory holding only a `spectre` link to vamos stays on the scrubbed PATH; `banner.load_profile('spectre')` `{}` and `Banner({}, 'spectre').text('trailer_ok')` None; `provenance(…('cpp', '13.2.0', '/usr/bin/cpp')…, 'spectre')` prints `cpp 13.2.0 licence unknown`; Cygwin has no cpp |
| E80 | §2.3 as 191 `Opt` entries with `option_chars` `"-+="` (`sp3/CLI/ade_scan.py`) | the ADE line: netlist `butterworth.scs`, no unknowns; the ADE-APS line: `input.scs`, no unknowns; `+param range.lmts +log %C:r.o -E -format wsfbin =log %C:r.log -f psfbin`: no positional, no unknowns; `-h resistor` makes `resistor` the netlist; `+spp -sppbin ./spp opamp.sp`: unknown `+spp` and `-sppbin`, positionals `./spp` and `opamp.sp` |
| E81 | the engines under signals (`sp3/CLI/sigdrive.py`, `wsl_sig.sh`, `wsl_sig2.sh`; VACASK `/opt/build.VACASK/Release`, Xyce `/usr/local/bin/Xyce`) | each engine in its own session, signalled after 3 s (VACASK) or 6 s (Xyce): SIGINT, TERM, HUP, QUIT, USR1, USR2 end it within 0.00-0.01 s (rc −2, −15, −1, −3, −10, −12); after `fix_points` the rawfiles hold 1,564,664-1,655,032 (VACASK) and 285,184-304,128 (Xyce) points; a VACASK started with `&` from a non-interactive bash (SIGINT inherited as `SIG_IGN`) still ran 5 min 14 s after `kill -INT`; under `nohup`, `signal.getsignal(SIGHUP)` is `SIG_IGN` (1), without it 0 |
| E82 | GCC cpp 15.2.0 (WSL) on a Spectre netlist with `-Iinc` (`sp3/CLI/wsl_cpp.sh`, `wsl_cpp.out`) | ISO mode: markers `# 0 "<built-in>"`, `# 0 "<command-line>"`, `# 1 "inc/sub.scs" 1`; an empty title line; `.param rr='VDD*1k'` with `VDD` unexpanded; stderr "top.scs:13:42: warning: missing terminating ' character", rc 0. `-traditional-cpp` keeps `// opamp test bench, title line` and writes nothing to stderr; `VDD` is not expanded there either |
| E83 | PSF readers on real and malformed files (`sp3/OUT/pp_run.sh`, `neg_run.sh` with `neg/*.tran`, `psfcheck.py`, `pu_run3.sh`; `sp3/ORC/psfp_test.py`) | psf-parser 05021e6 (WSL 3.14.4): 59 of 65 real ASCII files, every native one (noise, `logFile`, info; all 38 of its own `tests/data/ascii`), failing on the Cadence-converted GROUP (`dan-zilla`, `fracpole.ac`, `rushikesh`) and SINGLE (`fracpole.spac`, `.spdc`) layouts and `phaseMargin.stb`; on Cygwin 3.9.16 the import fails ("unsupported operand type(s) for \|: 'type' and 'type'"); it accepts a trace missing at the last point (`traces={'a': [1.0, 3.0], 'b': [2.0]}`), a missing `END`, swapped values and a descending axis under `ascending`, rejects an INT where FLOAT is declared ("Expected FLOAT, got Token(kind='INT' …)"), and returns escapes raw. psf_utils 6797146 with ply 3.10, inform, arrow, six and quantiphy: 27 of 30, failing on `logFile` and sweep/Monte Carlo parents; it assigns values by position (the swapped file read as a = [1, 4]), drops an incomplete point and deletes every backslash (`parse.py:366`). The `psfcheck` prototype passes every native sample on both Pythons and rejects each malformed file ("neg/missing.tran:37: point 2: expected trace 'b', got None") |
| E84 | VACASK and Xyce columns for Spectre-style netlists (`sp3/OUT/x1`-`x7`: `names.sim`, `op.cir`, `s0`-`s2`, `p1.cir`, `p2.cir`, `xf.sim`; `idx.sh`) | VACASK keeps case: `op2.raw` has `A` and `a`, and `rawfile.read(…).index('A')`: "'A' matches several variables: A, a"; `save default` (bytewise sorted) writes `d1:a_int`, `d1:implicit_equation_0`, `e1:flow(br)`, `I2:vx:flow(br)`, `IPRB:flow(br)` (+4.8125e-07 into `in`); `save full` adds `d1:implicit_equation_1`, `d1:qp_int`, `d1:sw_int`; `save default p(r1, i)` gives the defaults plus `r1.i` = 5e-4 A into terminal 1; noise `n()` in engine order, `nc(r1) nc('I2:r3')` exactly those with their contributions, a diode's `rs id flicker rsw idsw flickersw`; acxf writes `tf()`/`zin()` of `IPRB`, `v1` and `I2:vx`, and `tf(i1)` = 500 for 1 kΩ‖1 kΩ; `strictsave=0`: a `nosuch` column of zeros, rc 0; 1 and 2: "Node 'nosuch' not found. Failed to bind analysis outputs.", no rawfile; under 2 an `nc()` before an op is still ignored. Xyce: `V(*)` names `V(XI2:MID)` and no device internal; `DNO(XI2:R3)`; `I(R1)` 0.0005187502 A into terminal 1, `I(E1)` −0.0009625; `.print dc … v(I2:MID)`: "There was 1 undefined symbol in .PRINT command: node I2:MID", rc 1; `.ic v(I2:MID)=0.3`: "Ignoring nodes: I2:MID", rc 0, V(XI2:MID) = 1.0 at t = 0 |
| E85 | rawfile memory (`sp3/OUT/mem.py`; a 161.6 MB synthetic binary rawfile, 200,000 points × 101 variables) | `cosim.scan_raw`: 0.0 s, 17.1 MB peak RSS; `rawfile.read`: 1.2 s, 1259.3 MB |
| E86 | the PSF samples (`sp3/OUT/dates.sh`, `esc.sh`) | dates unpadded in all 20 dated samples (the native `joop-banaan.dc`, 19.1: `1:07:28 PM, Tue Feb 2, 2021`; revision 3 quoted `bus_chevrons.tran`'s, a hand-edited fixture [E101]), `Thur` once (`empty.pnoise`, 21.1.0.303.isr5); `escaped-strings.dc` differs from `joop-banaan.dc` in the `design` line only, and `bus_chevrons.tran` carries "mod by circuitmuggle@3Dec25" with hand-written values; `dan-zilla` (19.1.0.455.isr11, converted) has `net4\<0\>`; header and TYPE PROP reals are `%#g` from 15.1 to 23.1.0.063 and `%.15e` in 23.1.0.242, `logFile` PROP reals `%#g` throughout; a `strftime` date (`%I:%M:%S %p, %a %b %d, %Y`) is padded and locale-dependent (`02:56:17 PM, Thu Oct 01, 2026`), unlike every sample, and a writer that names signals from the engine's column names (every non-flow column a node, every flow `:p`) cannot produce Spectre's names: they come from the SignalMap (§6.4) |
| E87 | ngspice-45.2 on vamos-style rawfiles (`sp3/OUT/x3/load.cir`) | `ngspice -b` loads two concatenated ASCII plots (op2, ac1): "List of plots available: Current ac1 … op1 …", with `A` and `a` separate vectors |
| E88 | NetlistParse.rs d565fd3 (`sp3/ORC/s01`-`s09`, `s35`, `s39`, `s41`, `s50`, `s57`, `s60`, `s65`; `crates/netlist-py/src/lib.rs:106-128`, `crates/netlist-cxx/src/lib.rs:214-219`) | the Python module registers only `parse_spice`; Spectre parsing is reachable from Rust and the C++ bridge only. The Spectre dumps of all 126 corpus files are byte-identical to `tests/expected` (the Julia parser's). Incomplete/Error nodes on manual-valid input: a model group (6), statistics (17), `sweep {…}` (5), `montecarlo {…}` (7), `r1 a b resistor` (4), a title line, a line-1 `*`, `r\2`, the brace-less `if` (5), `library` (8), `prot` (4), `save … depth=2` (5), one-line `real` functions; clean: `ends name`, `+` continuation, alter, paramtest, an inline subckt with `if`, set/options, a `simulator lang=spice` section, `write=`/`writefinal=`. Against the manuals: `[0.5 1 +p2 (sqrt(p2*p2))]` read as 3 elements (`+p2(…)` a call of p2), `2**3**2` left associative, `err_braceless.scs` an error, the first line after a mid-file `simulator lang=spice` a Title (`r1 1 0 1k` lost in `simulator_lang_swap.scs` and `lang_swap_spice_start.cir`), based literals `8'hFF` and `~& ~\|` accepted, `Gm (1 2)(3 4) vccs gm=.01` as the last statement ending at byte 8 with no Error. Tiling: 13 of 126 untiled, all `err_*`; 7 files with a statement on line 1; 12 of 13 XDM ADE files clean once their title line is stripped (`statistics_line` fails at `statistics {`). MIT ("Copyright (c) JuliaHub, Inc. and other contributors"); 127 corpus files (22,945 bytes) and 127 dumps (147,930 bytes); 20 cite manual lines, one calls itself a verbatim manual example; 30 `err_*`, 2 `lenient_*` |
| E89 | XDM 2.7.0 on the XDM SPECTRE decks and a probe (`sp3/ORC/s32_xdm_run.sh`, `s33_xdm_warn.sh`, `s34_xdm_names.sh`) | `diodeClipper`: 0 errors, 14 warnings ("Unsupported parameter in spectre tran statement: errpreset\|write\|writefinal\|annotate\|maxiters", "Unsupported type: simulatorOptions\|finalTimeOP\|…\|saveOptions"); `dcOp dc` and `tran1 tran stop=1u` kept only as comments; `save V1:p` → `.PRINT AC V(b) V(VV1:p)`; devices renamed `RR1`, `VV1`, `XX1:RR1` |
| E90 | the XDM golds on Xyce (`sp3/ORC/s44_xdm_gold_run.sh`; Xyce_Regression d4685581) | all 12 gold `.cir` files run, rc 0, 0.11-0.21 s each |
| E91 | the CMC benchmark decks (`sp3/ORC/s17`-`s26`, `s43`, `s61`, `s64`, `s68`; `cmc_pyms_report.csv`) | 48 of 48 have a same-name `.lis` from "HSPICE -- G-2012.06 32-BIT" (`hspice -hdlpath ../code -i idvg_nmos.sp`); `.hdl` 48, `.option post ingold` 48, `par'…'` 25, `.probe` 24, two-source `.dc` 23, `deriv(` 19, `.alter` 18, `.meas` 4, `.noise` 3; ref19, the UG and ref5 never mention `.hdl`, `par'` or `deriv(`. `run_adms_tests.sh` points at `/usr/local/src/Xyce-8/…` as shipped; its logic with this box's paths on `/usr/local/bin/Xyce` (without `env_for`, so on the 2026-09-29 library; and every run that printed a Device Count Summary ran Xyce's built-in ADMS device, not the PyMS module [E99]): 32 OK, 16 FAIL in 18-28 s per deck (`ac.sp` ×3 `dc myvdd`; `idvg[n\|p]mos` ×5 `i(X1.d)` left after the rename; noise ×3 "Analysis type AC and print type NOISE are inconsistent"; `rdsgeo` ×3 "Unrecognized parameter SDTERM"; BSIM6 `idvg_pmos` "-1.3.0"; a bsimcmg107 ringosc lost to a concurrently deleted `/tmp/pyms_hdl_cache`); PyMS honours `PYMS_CACHE` (`N_DEV_PyMS.C:544-550`) and compiles a non-inlined `bsimcmg.va`; Xyce ignores `.temp` ("Unrecognized dot line will be ignored"). Against the `.lis`, `idvg_nmos` over 1044 points: worst 8.0 % at 27 °C, 0.12 % with temperature 25 °C; the inverter 7.9155e-3 against 7.913e-3. openvaf-r 20260616 rejects `BSIM6.1.1.va` (651 errors, the `(* xyceModelGroup=… *)` attribute after the port list among them) and bsimcmg 107 (1334, the attribute removed); 108 and 110 fail (rc 65) |
| E92 | VACASK's device libraries (`sp3/ORC/s45_vacask_dev.sh`; `sp3/SYNTH/builds.sh`) | `lib/vacask/mod/spice/diode.osdi` and `mos3.osdi` replaced 2026-10-03 15:25 (sha1 da0cc2e90ce8, adff42fbc900; the `*.pre-r6b` copies are ef47a311…, 302b7f76…); the others are of 2026-10-01 (bjt 5dae5f1d12a8, bsim3v3 48aba32551ba, bsim4v8 f3d20bd4b1ee, capacitor 9bc38d62d79a, inductor 59b8c30ca168, jfet1 ac3edfaa35e6, mos1 634e8ec20d1a, mos2 753cd6d99a9a, resistor 6d24d151af84); the source is at 3becb73d, and the binary still reports 0.3.4-91-g64489cf7 |
| E93 | the statement layer against the CST (`sp3/ORC/cst2stmts.py`, `cst_tiling.py`, `s35_cst_oracle.sh`, `s60_tiling.sh`) | a stdlib prototype (Python 3.9 grammar) builds `{kind, name, nodes, master, params [name, raw text, expression shape], children/branches, span}` from (source, dump): 291 statements over the corpus (Parameters 57, Instance 42, Analysis 24, Simulator 24, Save 22, Subckt 13, Include 12, Model 11, FunctionDecl 10, ConditionalBlock 6, Info 4, Alter/Options/Ic/Global/CheckLimit 3 each, …) plus 37 Incomplete/Error nodes; the tiling check finds the 13 untiled `err_*` files and a truncated node group ("14 byte(s) outside every statement, first at 8: (3 4) vccs gm=.01") |
| E94 | today's code at the seams the review of revision 3 checked (Cygwin Python 3.9.16: `sp3/IMPL/spice_fold.py`, re-run as `sp3/FIX/cyg/spice_fold.py` on 5e0f967; `sp3/IMPL/cpow_fold.py`; `sp3/IMPL/patch_where.py` applied to the copy `sp3/IMPL/sv`, then `python3 -m unittest test_vamos_driver.TestVamosOptions`, re-run) | `spice.parse` of `r1 a 0 'sqrt(-4)'` gives `value = Num(value=-2.0)` and of `r2 a 0 '-2**2+10'` `Num(value=6.0)`: HSPICE's evaluator folds them at parse time (`spice.py:1435-1439`); `fold(Call('cpow', (2, 3)), strict=True)`: "EvalError: unknown function cpow()", and `to_vacask`/`to_xyce` of it "PrintError: unknown function cpow()", because `fold` evaluates with the default dialect (`expr.py:756-803, 882-887`); revision 3's optable change (a tuple `where`, `psf_names` spectre-only and left out of vcs's help): "FAIL: test_help_texts_list_every_option_and_point_at_the_guide … '--vamos-psf-names' not found in "usage: vcs …"" (`test_vamos_driver.py:151-160`); no IR class has an attribute `folded` or `va_modules` (`spice._Parser.va_modules` is a parser dict, `spice.py:781`) |
| E95 | `mos1`/`mos2`/`mos3` with and without Spectre's `nsub` default, both engines (`sp3/FIX/v1.sh`, decks in `sp3/FIX/nsub/`; first run by the fidelity lens, `sp3/FID/w/e1_nsub.sh`): vto=0.7 kp=1e-4 tox=1e-7 phi=0.7 (mos1 `lambda=0.02`, mos3 `badmos3=1`), w=10u l=2u, vgs = 2 V, vds = 1.5, 3, 5 V | without `nsub`: mos2 and mos3 422.50 µA at every vds on both engines (no channel-length modulation); with `nsub=1.13e16 gamma=0`: mos3 437.29/468.73/494.44 µA on both, mos2 511.15/533.43/567.36 µA (Xyce 511.14/533.43/567.73); mos1 435.18/447.85/464.75 µA either way; adding `mjsw=1/3` changes no dc value. Targets: `nsub` 0 (`mos3.va:149`; Xyce `NSUB` 0.0, `N_DEV_MOSFET1.C:305`, `N_DEV_MOSFET2.C:308`, `N_DEV_MOSFET3.C:309`), `mjsw` 0.5 (`mos1.va:132`, `N_DEV_MOSFET1.C:269`, `N_DEV_MOSFET2.C:272`) or 0.33 (`mos2.va:135`, `mos3.va:137`, `N_DEV_MOSFET3.C:273`); ref5: `nsub=1.13e16` (pp.410, 489, 505) and `mjsw=1/3` (pp.413, 492, 508) for all three masters |
| E96 | PyMS on both Xyce launchers, plain and under `env_for` (`sp3/FIX/v1.sh`, decks in `sp3/FIX/pyms/`; first run by the fidelity lens, `sp3/FID/w/e2_engines.sh`) | all four runs print "Netlist warning: .HDL: compiled and registered dres from dres.va" and give I(V1) = −1.000000001e-3 for `.model dresm dres r=2k` (the card ignored, the module's default 1k, as in E63); `ldd`: `/usr/local/bin/Xyce` loads `/usr/local/bin/../lib/libxyce.so` (2026-09-29 22:33) plain and `/usr/local/src/xyce-build/src/libxyce.so` (2026-10-03 15:27) under `env_for`; `/usr/local/src/xyce-build/src/Xyce` loads the 2026-10-03 library either way |
| E97 | Verilog-A parameter declarations in real compact models (`sp3/FIX/v1.sh`; first counted by the completeness lens, `sp3/COMP/w6.sh`) | lines starting with `parameter` / with a `` `MPR…``/`` `IPR…`` macro: VACASK `psp103v4/PSP103_module.include` 0/413, ADMS `BSIM6.1.1/code/BSIM6.1.1.va` 0/870, VACASK `vbic/vbic_1p3.va` 0/129, `bsimbulk106.va` 0/950; `Common103_macrodefs.include:140`: `` `define MPRoo(nam,def,uni,lwr,upr,des) (*units=uni, desc=des*) parameter real nam=def from(lwr:upr);``; ADMS `toys/resistor.va:11`: `(*desc="Resistance", type="instance"*) parameter real R=1 from (0:inf);`, so no line of it starts with `parameter` either |
| E98 | VACASK's PSP103 tree, its shipped OSDI and openvaf-r's options (`sp3/FIX/v1.sh`, `v2.sh`, `sp3/FIX/psp/`; first run by the completeness lens, `sp3/COMP/w5.sh`, `w8.sh`) | `/opt/openvaf-r-20260616/openvaf-r /usr/local/src/VACASK/devices/psp103v4/psp103.va`: rc 0, "Finished building psp103.va in 2.99s", 729,272 bytes; `psp103.va` is four `` `include``s of macro files, `module PSP103VA(D, G, S, B);`, `` `include "PSP103_module.include"`` and `endmodule`; `/opt/build.VACASK/Release/lib/vacask/mod` holds `psp103v4.osdi`, `bsimbulk106.osdi`, `vbic_1p3.osdi`, `vbic_1p3_4t.osdi`, `vbic_1p3_5t.osdi`, `bsim4v8.osdi` and `bsim3v3.osdi`; `benchmark/ring/vacask/runme.sim` loads `psp103v4.osdi` and includes `models.inc` (`model psp103n psp103va (`, line 13); the ADMS examples' `psp103.va`, `diode_cmc.va`, `hicumL0V1p32.va` and `bjt504.va` give openvaf-r rc 65 (the completeness lens's run, consistent with E91); `openvaf-r --help` lists `-D <MACRO[=VALUE]>` and `-I, --include <DIR>` ("Search directory for include files") |
| E99 | the CMC decks on Xyce: which device runs (`sp3/FIX/v1.sh` re-running the completeness lens's `sp3/COMP/w12.sh` on the engine lens's cadence2xyce'd BSIM6 `idvg_nmos.cir`, logs `sp3/FIX/b6/{plain,envfor}/run.log`; `sp3/FIX/v4.sh` over `sp3/ORC/p3all/*/run.log`, the logs of E91's `s26_cmc_all.sh`) | the deck holds `.hdl "BSIM6.1.1_inlined.va"` and `.model nmos nmos level=77`. `/usr/local/bin/Xyce` in a plain environment: "compiled and registered bsim6", the Device Count Summary lists `M level 77 (BSIM6) 1`, "End of Xyce", rc 0. Under `env_for`'s library path: "No model parameter GEOMOD found for model NMOS of type NMOS", no M entry in the summary, rc 139 (a segmentation fault). In the 50 CMC run logs, every summary that lists the device lists a built-in ADMS device, `M level 77 (BSIM6)` (the 9 BSIM6 decks) or `M level 107`, `108` or `110 (BSIM-CMG FINFET v107.0.0`, `v108.0.0`, `v110.0.0)`, never a module name; these are the built-ins' names (`N_DEV_ADMSbsim6.h:3335-3336`: "BSIM6", "M level 77"; registered at `N_DEV_RegisterADMSDevices.C:89-93`), while PyMS names a device after its module (`PyMS/vae/xyce_device_gen.py:513-514`). `s26_cmc_all.sh` runs `/usr/local/bin/Xyce` and sets no `LD_LIBRARY_PATH` |
| E100 | the BSIM-CMG decks and the XDM decks (`sp3/FIX/v1.sh`; first examined by the completeness lens, `sp3/COMP/w7.sh`, `w9.sh`, `w10.sh`) | BSIMCMG108.0.0_20140822: 13 decks, each with `.hdl "bsimcmg.va"`, the modelcards' `.model` type `bsimcmg` (7 cards); `bsimcmg.va:48` is `` `include "bsimcmg_main.va"``, which declares `module bsimcmg_108`; `module bsimcmg` is only in `bsimcmg_nqsmod3.va`, which the tree does not include. BSIMCMG110.0.0_20160101: 13 decks, `.hdl "bsimcmg.va"`, type `bsimcmg`, module `bsimcmg_110`. The XDM SPECTRE decks (Xyce_Regression d4685581): masters `resistor` 46, `vsource` 22, `isource` 9, `inductor` 5, `capacitor` 4, `nfet`/`pfet` 1 each, two diodes; analyses `dc` 1, `tran` 11, `info` 70; no `ac`, `noise`, controlled source, mutual inductor or jfet |
| E101 | the real samples and the corpus (`sp3/FIX/v1.sh`-`v3.sh`, outputs `v1.out`-`v3.out`) | psf-parser `tests/data` (Spectre 23.1.0.242): `"design" "#RC Low-Pass Filter"` in every file; `myop.dc` in = out = 1 V; `mydc.dc` "Vin:dc = (0 V -> 2 V)", SWEEP `"dc"` (V), step 0.1; `myac.ac` 1 Hz → 1 MHz, grid 3; `mytran.tran.tran` stop 0.5, step 5e-4, maxstep 0.01, errpreset moderate, 6002 points, `in` = 1.0 at t = 0 and 1.5 at t = 8.3333e-5 s (1 + sin(2π·1 kHz·t)); the `mysweep_*.sweep` parents `"R1:r"` (Ohm) 1000/2000/3000, `mysweep-00i_mynestedsweep_ac2.sweep` `"C1:c"` (F) 1e-6/2e-6/3e-6; `binary/logFile` (ASCII) lists the `myinfo_*-info` entries, `myop-dc`, `mydc-dc`, `myac-ac`, `mytran-tran`, the three `mysweep_*-sweep` parents, per point `dc1`, `ac1`, the nested parent and its three `ac2` leaves, then `dc2-dc`, `tran1-tran` (stop 5 ms) and the `mymonte` entries. Dates: `joop-banaan.dc` (19.1, native) `1:07:28 PM, Tue Feb 2, 2021`, `fracpole.dc` (20.1) `11:50:23 AM, Tue Feb 2, 2021`; `bus_chevrons.tran`'s `1:35:51 AM, Mon Dec 1, 2025` sits beside "circuitmuggle". allpub order: `rushikesh-dhanaji-phadtare.tran` (`"BINPSF creation time"`, 19.1.0.063) TRACE `"group" GROUP 5`: `net1 net2 V0:p V1:p vdd!`; the native `joop-banaan.tran` (19.1.0.373.isr7): `V1:p vinp vref_o I2.pup2 I2.pup_b …` (explicit saves, unsorted). NetlistParse.rs `tests/corpus/spectre`: 20 of 126 files cite or quote a manual (`spectre_reference.txt:<line>`, "manual"; the list is in §11), `array_sweep.scs` among them as a "verbatim manual example" |

## 14. Open questions
1. The UG's `/tmp%C:t:r.raw` example against its `:t` definition (§2.4). vamos follows the definition.
2. `-outdir` with a netlist path containing `/` (§2.5): the literal reading is used.
3. Where `write=`/`writefinal=` state files go (the cwd, `-outdir` or the raw directory); where `ahdlSimDB` goes
   under `-outdir` alone (§2.5).
4. The exact cpp flags that reproduce Spectre's preprocessing (§2.6). GCC cpp 15.2.0 in ISO mode empties `//`
   lines and warns on an apostrophe in a SPICE `*` comment, and `-traditional-cpp` keeps both and warns about
   nothing [E82]: vamos uses `-traditional-cpp` (§2.6) unless a real Spectre sample shows ISO behaviour.
   Neither mode expands a macro inside a SPICE `'…'` expression [E82].
5. A `.scs` file whose line 1 is `simulator lang=spice`: vamos selects SPICE mode with a note, the mirror of
   §3.1's `lang=spectre` rule.
6. Spectre-mode `1meg`/`1mil` (read as 1e-3, warned); whether newer versions add `P`/`E` suffixes; whether
   the SPICE Reader takes HSPICE's `D` exponent (`1.0D+3`; v1: an error, §3.3).
7. Whether node `0` is also ground when `global gnd …` names another ground node.
8. Model-group selection with `nf` > 1: total or per-finger W; and whether SPICE-mode `x.N` bins become a
   Spectre model group with Spectre's selection (§4.3; until this is answered, plan.build warns where
   HSPICE's rule would choose another card for an instance).
9. SPICE-mode names for a second `.op`/`.dc`/`.tran`; the modern name of `timeSweep` (`.tran` or
   `.tran.tran`); how the Reader reads `.tran`'s third field and `uic`. vamos reads the third field as
   SPICE2G's TSTART, Spectre's `outputstart`, with a warning, because the UG names the SPICE 2G User's Guide as
   the reference for the SPICE2 input language [M UG p.51]; the UG's oscillator pair, where `.tran 1us 80us
   10ns` sits beside `tran stop=80us maxstep=10ns` (pp.48-49), is a hand-written comparison, not Reader
   output. `uic` is read as `skipdc=yes` (§4.3). `spp -convert` names its analyses `analysisOP1`,
   `analysisDCswp1`, `swp_tmp1` and writes `.op` as `dc oppoint=logfile` [M UG pp.46-47], unlike UG p.232's
   `opBegin`: the names depend on the reader.
10. Whether the built-in SPICE reader of later versions marks every SPICE-mode model and subckt
    case-insensitive, as the 5.1 Reader does by inserting `simulator lang=spectre insensitive=yes` [M UG pp.49,
    52] (§3.1).
11. `dec` placement: floor or round of the interval count (one real sample: 120.4 → 120); `center`/`span` with
    log sweeps (v1: an error); `log=N` endpoint inclusion; a start ≤ 0 with no step parameter (v1: linear)
    (§5.2).
12. `+escchars` semantics: `<` and `>` are escaped in a 19.1 Cadence-converted file [S `dan-zilla`]; the quote
    and backslash escapes are inferred, the 19.1 sample revision 2 cited being a hand-edited fixture [E86]
    (§8.3).
13. A sweep that changes a condition or a bin: UG p.111 says Spectre stops with an error, UG p.115 that
    instances keep the model chosen at input. vamos refuses either change (§3.7).
14. Spectre's noise contribution names for diodes, BJTs and MOSFETs (the resistor's `rn`/`fn` and Verilog-A
    names are known [S]; VACASK's diode names are `rs id flicker rsw idsw flickersw` [E84]).
15. xf trace names for an isource (a vsource's is `<src>:p` [M UG p.113]); VACASK gives isource transfer
    functions [E84].
16. Whether ADE parses `spectre -V`/`-W` output, and in which format; whether Spectre exits 0 after `-V` with
    no netlist (with one, the run continues [M UG p.234]).
17. Whether ADE accepts psfascii results when it asked for psfxl (it reads `logFile`'s `dataFile` and
    `format`); whether ADE, not Spectre, writes `runObjFile`, `artistLogFile`, `variables_file` and
    `simRunData` (v1: ADE does, §9).
18. Which `spectre.out` lines ADE parses beyond the trailer; the modern message format (`ERROR (SFE-…)`)
    against the 5.1.41 one used here; whether the modern `completes with` line ends with a period (the 5.1.41
    one does not [M UG p.33]); the trailer after a user's interrupt (v1: the `terminated prematurely` form,
    §8.7).
19. Spectre's `exp` waveform: are `td1`/`td2` measured after `delay`; the default `tau1`/`tau2`.
20. Spectre's default pulse rise and fall (vamos uses transres, with a warning).
21. The allpub names of inductor and iprobe currents.
22. errpreset's LTE check (liberal: capacitor and inductor states; moderate and conservative: node voltages
    [M ref19 p.431]) against VACASK's `tran_lteimplicit=0`, used for every preset (§5.4).
23. The nutmeg layout Spectre writes: one rawfile and no `logFile` [M UG p.229]; the plot and variable names, and
    a noise plot's columns (§8.5).
24. Tran `start` ≠ 0 semantics (refused in v1).
25. Whether a tran's own `errpreset` beats `+errpreset=` (assumed; `+aps=` beats both [M ref19 p.35]).
26. Whether Xyce's noise `.prn` table is readable after an interrupt; its `FORMAT=RAW` print files are [E72]
    (§2.7).
27. Spectre's temperature law for the default diode `eg` (§3.9); with it, the warning could go.
28. Whether Spectre's SPICE Reader accepts `.hdl` (vamos: `ahdl_include`, with a note), `.probe name=par(…)`,
    `deriv()` and HSPICE's `.ic n=v` (vamos: accepted) (§3.13, §4.3). The manuals on the box never mention
    `.hdl`, `par'` or `deriv(` [E91].
29. Whether a netlist parameter named `temp` or `tnom` shadows the predefined one (v1: an error; NetlistParse.rs's
    corpus defines `parameters temp=300` and reads it as `vt=P_K*temp/P_Q`) (§3.5); how Spectre reads the
    reserved words `temper`, `time` and `hertz` in an expression and as parameter names (v1: a parameter of
    those names is an error; `temper` is an ExprError in Spectre mode and the temperature in SPICE mode;
    `time` and `hertz` are ExprErrors, §3.5).
30. `^` in a SPICE-mode expression: an error (v1) or HSPICE's power (§3.5).
31. Which channel-length modulation Spectre's `mos3` uses (SPICE2, SPICE3f or its own); vamos writes
    `badmos3=1`, with a warning per card (§3.9). E69 shows that the two engines agree on it, not that Spectre
    does: with `vmax`=0 Xyce uses SPICE2's formula whatever BADMOS3 is, and `badmos3=1` moves VACASK onto it.
32. Spectre's `mos1`/`mos2`/`mos3` derivations: whether `gamma`, `phi` and `vto` follow from a given `nsub`, and
    `kp` from a given `tox`, as in SPICE3 (ref5 lists `gamma=0`, `phi=0.7` and `kp=2.0718e-5` as defaults)
    (§3.9; with the answer, the nsub warning could go); and whether Spectre's `mos2`/`mos3` use the default
    `nsub` (1.13e16) for the depletion width, hence channel-length modulation, as the targets do once vamos
    writes it (§3.9 [E95]; the documented `phi=0.7` is SPICE's derivation from that `nsub`). vamos applies the
    documented default, and the card's note names it (§3.9), so a user comparing against Spectre can see what
    was written.
33. Spectre's behaviour for `exp(x)` with x ≥ 80, outside its documented domain: an error, a clamp or an
    overflow (v1: an error, §3.5).
34. The day names of the PSF `date` (`Thur` in one 21.1 file [E86]) (§8.2).
35. The PSF header of a `noise` analysis with `oprobe=` (§8.3).
36. `sweep_direction` of a descending sweep (every sample has 0) (§8.3).
37. How an indexed terminal save (`L1:1`) is named in outputs (v1: as written, §6.2).
38. What a converted SPICE `SIN` without a frequency simulates: SPICE3's 1/TSTOP or a Spectre `sine` with
    `freq` 0 (v1: an error naming both, §4.3).
39. The order in which `<prog>_DEFAULTS` and `<PROG>_DEFAULTS` are tried for an alias (§2.2); which of ref19's
    additions (the options ref5 does not list, whose † marks the ref19 text does not carry) Spectre reads from
    the defaults variable (v1: all of them).
40. What Spectre counts as one message for `-maxwarns`/`-maxnotes` (v1: the text with numbers and quoted names
    normalized, §8.7), and what it prints when a limit is reached (v1: one notice).
41. **A decision for the user:** whether to commit the NetlistParse.rs corpus files that quote Spectre manual
    examples (20 files cite manual lines; `arrays_langswitch/array_sweep.scs` calls itself a verbatim manual
    example, and `julia_parse_tests/var_reference.scs` carries the same line) (§11) [E88, E101]. Until the
    answer, phase 0 vendors the corpus without them, and vamos-authored `lang/` fixtures cover their
    constructs (§11 Licences).
42. Whether Spectre searches the `-I` directories for `ahdl_include` and Verilog-A `` `include`` (v1: yes, after
    `CDS_VLOGA_INCLUDE`, §2.3, §3.1): the manuals name `-I` for `include`, `#include` and PWL `file=` only [M UG
    pp.73-74], and `CDS_VLOGA_INCLUDE` for the Verilog-A include path [M ref19 p.551]. (openvaf-r 20260616
    takes include directories, `-I` [E98].)
43. Netlist reading (§2-§3): that `+pre_config`'s fragment comes after the title line (§2.3); that a plain
    `include` of a file that defines sections is an error (§3.1); that a hierarchical node on an instance
    terminal would become a new top-level node if printed (§3.4, v1: an error); that `options` keys are
    case-insensitive in general (§3.10); that the `scale` option scales instance geometry as HSPICE's does
    (§3.10).
44. Expressions (§3.5): `~^`, whose value needs an integer width Spectre does not document (v1: an EvalError);
    `sqrt`'s domain, x ≥ 0 (C's) where the table writes x > 0; SPICE-mode constants matched case-insensitively;
    that the SPICE Reader converts SPICE-mode expressions to Spectre semantics, so that they get Spectre's
    evaluator.
45. Sources and models (§3.8-§3.9): that a `tran` starts from the t = 0 waveform value of a source whose `dc` is
    given (both engines do); that `capmod=meyer` is the targets' Meyer gate charge.
46. SPICE-mode mappings (§4.3): `.global` names never ground; `uic` → `skipdc=yes`; `abstol`/`vntol`/`chgtol` →
    `iabstol`/`vabstol`/`chargeabstol`; `.option method=trap|gear` → every `tran`'s `method` `trap`/`gear2`
    (UG p.50 puts analysis options on the analysis, and the hand-written pair of pp.48-49 shows `trap` there);
    that a `.print` adds to allpub.
47. Engine mappings (§5.4, §6.3): `skipdc=yes` as both engines' `uic`; the current of Xyce's `G` form of
    `isnoisy=no` measured through a probe.
48. Outputs (§6.4, §8): no sweep parent in the legacy names, and the point index beyond 999; `data_type`
    `swept_scalar` for `xf`; that writing the full TYPE list costs readers nothing; the dc SWEEP name of a
    `temp` or netlist-parameter sweep; units `""` for netlist and model parameters; `nan`/`inf` written with a
    warning; the description variables (`<inst>:<param>`, `<model>:<param>`, `temp`, `<param>`); noise STRUCT
    trace names (Spectre instance paths), the order and the units and type names of `gain` and `in` (`V/V` or
    `V/A`; `V/sqrt(Hz)` or `A/sqrt(Hz)`, by the input's kind), and the suffix of a colliding STRUCT type name;
    the order of allpub signals, which rests on one Cadence-converted sample (`rushikesh`) [E101].
49. State files (§8.6): the modern header and number layout. The only sample, UG p.204's (dated 1992), writes
    `# during 'stepresponse' at 5:39:38 PM, jan 21, 1992.` (no day name, the month in lower case) and values
    with 15 significant digits and no leading zero (`.588793510612534`); vamos follows the header and keeps
    the leading zero.

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

## 16. Revision 3 changes

Revision 3 re-validates revision 2 against sv2ghdl 5e0f967 and the engine builds of §0, after six finders
checked it (expressions and IR, SPICE mode and cards, engines, command line, outputs, reuse and oracles). Each
finding whose evidence held is applied; where two findings conflicted, or the evidence held only in part, the
resolution is listed at the end. New experiments are E49-E93; E1-E48 were re-run (E78).

- **Expressions and the IR (IR-01…IR-20).** SPICE-mode text gets Spectre semantics through the
  `spectre-spice` expression dialect, not HSPICE's truncating `pow` (§3.5, §4.3; E54). §4.1's prose became an
  exact phase-0 block whose fields are appended with defaults, because revision 2's list failed to define on
  both Pythons and would have broken positional call sites (§4.1, §10; E49). `cpow` is evaluated by
  `plan.build` on every path and parameter state, so no out-of-domain value reaches an engine (Xyce returns a
  silent real part) (§3.5, §5.1; E51). The Spectre function table is closed, with `hypot`/`fmod` added
  (`EXTRA_FUNCS`) (§3.5, §4.2). `temp`/`tnom` are mapped to `temper`/`$tnom` (§3.5). One number reader serves
  statements and expressions, with its warnings (§3.3; E52). Netlist variables and their dependents leave
  `Netlist.values` in the VACASK render copy (§4.1; E53). `Cond` holds only instances, ifs and paramtests, and
  `flat_items`/`flat_analyses`/`ITEM_TYPES` are defined (§3.6, §4.1). The bitwise operators no longer crash
  messages (§3.5). The precedence table is per dialect (§3.5). User functions are inlined at parse time, and
  there are no node-access tokens (§3.5). `Value`, expression-valued analysis arguments, vectors, `ic`s and
  nodesets are added (§3.5, §4.1). The statistics and save types are given (§4.1). Constants are folded at
  parse time, and `M_DEGPERRAD` added (§3.5, §4.2). Option keys keep Spectre's spelling, without HSPICE's
  tolerance notes (§3.10). `Netlist.left_out` becomes a real field, and copies use deepcopy (§4.1). The oracle
  scope of NetlistParse.rs is set (§11). `par'…'` grouping is added (§3.5). Confirmed: E45 and the additive
  premise (§13).
- **SPICE mode and cards (SPICE-01…SPICE-31).** Spectre cards bypass `hspice_card` (§3.9, §4.4; E59).
  `SPECTRE_MASTERS` gains its defaults rows (§3.9). spectre.py owns every file (§3.1, §4.3). SPICE parameters
  are returned raw and resolved once over both languages (§3.7, §4.3). Names are declared in a first phase
  (§4.3, `Decl`, `declare_fragment`). `* spectre:` lines are an error (§3.13). Appended Spectre parameters
  (§4.3). Node names are strings (§3.4). SPICE-mode numbers (§3.3). X-line overrides are checked (§4.3).
  SPICE sources are filled in Spectre names (§4.3). SPICE cards get `Model.prim` (§4.3). `.tran`'s third field
  (§4.3, §14 q.9). The `.option` map (§4.3). The Verilog-A module scan follows `` `include`` (§3.1; E61). The
  dot statements are assigned (§4.3). `.dc`, `.temp`, `.ic` and `.noise` (§4.3). The `.print`/`.probe` items
  (§4.3). `parse_fragment` is based on `_Parser.read` (§4.3). The `Fragment` types (§10). The post-design walks
  (§4.4). Spectre bins keep group order (§3.9, §4.4). Lower-case spellings (§4.3). `simulator lang=` inside a
  subckt (§3.1). The CMC decks are HSPICE decks (§3.13, §11 T3c). The NetlistParse.rs SPICE corpus is an
  acceptance set (§11). The merge gate (§4). The evidence tags of q.10, the type defaults and PARHIER (§3.1,
  §3.9, §4.3). The rule-by-rule `spectre-spice` text, with cadence2xyce.pl's rules (§4.3).
- **Engines (ENG-01…ENG-25).** No HSPICE defaults on Spectre cards, with the Spectre-default rows (§3.9,
  §4.4). Nested subckts are lifted on VACASK (§3.7, §4.5 item 11; E65). Verilog-A on Xyce: letter devices for
  `xyceModelGroup` modules (§4.5 item 13; E64). Verilog-A parameter names are lower-cased on VACASK (§4.5
  item 12). Failure means an unfilled `No. Points`, a short tran or missing groups (§7.1, §7.2; E66). Every
  walk sees `Cond` (§4.4, §4.5 item 1; E56). Bins: group order and default bounds (§3.9). Per-type save
  directives (§5.5, §7.1; E67). Builds are identified, and every run goes through `engines.*` with `env_for`
  (§0, §7; E68, E92). The `pow` row is corrected (§3.5). `badmos3=1` (§3.9; E69). Diode `fcs=fc` (§3.9). New
  evidence replaces [I] tags or research claims: E70 (§4.5 item 8), E71 (§7.2), E72 (§2.7; q.26 narrowed),
  E73 (§3.8.1, §6.4), E74 (§7.3), E75 (§1, §2.3); E1-E48 re-run (E78).
  Tolerances are taken from the plan's options (§3.10, §4.5 item 5). `env_for(nvc_libdir=None)` (§10).
  Phase 3 maps to VACASK's `mc`, `acstb`, `acsp`, transient noise, `pwl period` and `am`/`fm` (§12). The Cadnip
  VACASK is not a fidelity oracle (§3). R/C card geometry is folded and stripped (§3.8, §3.9). The VACASK
  option names are confirmed, with the sweep-restore pin (§5.2, §5.4).
- **Command line, log and signals (CLI-01…CLI-22).** The invoked-name dispatch, `tools.invoked` and the
  aliases (§2.1, §10; E79). The defaults variable order, with an empty variable meaning none (§2.2). vamos
  options are read from the command line only (§1). optable's `where` contexts, `psf_names` and the spectre
  effects and help (§10). No exception escapes to `cli.py`; vamos failures give 2, and foreign kills 3
  (§2.7). Signals reuse `proc.py` (moved from `nvc.py`); USR1/USR2 are never forwarded; nohup and Ctrl-Z
  handled (§2.7; E81). `SpectreLog` routes everything, nothing goes to stderr, and `unmapped_notes` is added
  (§8.7). Built-in banner text for every key (§8.7). The help topic is read in the prescan (§2.1). `+spp`,
  `-spp`, `-sppbin` and `-env` get rows, and the checkpoint row is split (§2.3). Layer rules for the defaults
  (§2.2). The run directory follows the vcs-ams convention, and a stale `logFile` is removed (§1). cpp runs in
  the cwd, and its markers and stderr are handled (§2.6; E82). The Verilog-A location and the new §2.8
  Environment. Provenance, the `cpp` licence, `choose_engine`, and the Xyce lookup (§1, §7.2, §10). Phase 0
  and S5's files (§12). Message ids (§8.7). `SpectreJob.notes` holds the error rows (§2.3, §10). Confirmed:
  the `scan` signature, `SHIM_NAMES`, the §10 dataclasses and the `-V` [M].
- **Outputs (OUT-01…OUT-25).** `psfcheck` is the required oracle on both legs, and the third-party readers
  are consumer checks (§11; E83). psf_utils is an optional consumer, and VALUE order equals TRACE order
  (§8.3). Columns are found by exact name (`Raw.exact`, §4.6, §6.4; E84). Xyce printed paths for saves,
  allpub, ic/nodeset and noise (§4.5 item 15, §6.4). allpub membership (§6.1). Streaming rawfile access and
  results (§4.6, §10; E85). One string encoder, with corrected [S] tags (§0, §8.3; E86). Number formats,
  `precision` and the date (§8.2, §8.3). The header table, the full TYPE lists, the current TRACE PROP, the
  `logFile` layout and sweep-tree PROPs, the units (§8.2, §8.3). Noise STRUCTs, `nc()` saves and naming
  (§7.1, §8.4). The remaining output options (§6.1). Save items are written as given, with the statement
  options (§6.2). xf writes only IR sources (§6.4). SignalMap order (§6.4). `strictsave=2` (§6.2, §7.1).
  State files (§8.6). Nutmeg has no `logFile` and is read by ngspice and VACASK (§8.5, §11; E87). The trailer
  has no period (§8.7). Native R and E currents (§6.3). S4 writes the PSF writer from §8 (§12).
  The §11 test lists. Confirmed: the PSF naming, the `logFile` keys and the column facts (OUT-24).
- **Reuse and oracles (ORC-01…ORC-20).** The rejected-alternatives sentence is restated with the re-verified
  reasons and how each is used instead (§3; E88, E89, E76). The statement layer (`Stmt`, `statements()`) is a
  phase-0 contract (§3.2, §10; E93). The CST is a differential signal with `DIVERGENCES` and `GAPS` (§11
  T0). The CST oracle with tiling (§11 T0). The corpus is committed with its MIT notice (§11; §14 q.41). The
  optional Cadnip engine oracle (§11 T3b). The CMC decks: HSPICE decks with 25 °C listings, never compared at
  27 °C (§3.13, §11 T3c; E91). `.hdl` (§3.13, §14 q.28). The opt-in PyMS tier with `PYMS_CACHE` (§11 T3c).
  Module discovery follows `` `include`` (§3.1). The XDM decks against their golds (§11 T3a; E90). The merge
  gate is the whole suite on both legs (§4). The engine facts are harvested with the binaries named (§11 T1,
  §13; E92). The Xyce binary is named per row (§0, §13). psf-parser is vendored (§11). Rawfile goldens feed
  the PSF writer (§11). VACASK's own Spectre tests serve only as harness smoke tests (§11 T3b). The test
  tiers, gates, layout, `expect.json` and licences (§11).
- **Resolved differently from a finding:**
  - *IR-06 against SPICE-09:* in SPICE mode an unknown suffix reads as 1 with a warning only when it starts
    with `x` or `a`; `5V` stays silent. SPICE-09's [M] exponent-with-factor note is adopted.
  - *IR-07:* the Xyce copy keeps the nominal values, because the Xyce emitter needs numbers to choose
    branches and bins per path (`xyce.py:181, 223, 280`); `RunPlan.dependents` and `RunPlan.engine` are
    added.
  - *IR-04:* `FUNCS` stays HSPICE's table, and `EXTRA_FUNCS` holds `cpow hypot fmod`, rejected in HSPICE text,
    so the HSPICE path gives the same error as today.
  - *SPICE-01 against ENG-01:* the gate is the `dialect` keyword (ENG-01), and every Spectre card must carry
    `prim` (SPICE-01), so a card without one fails loudly. `_wire_card` stays for its engine renames, because
    spectre.py strips the geometry it would act on (ENG-22).
  - *SPICE-02 and ENG-01's rows:* checked against ref5 and the targets' sources by this revision. `phi=0.7` is
    written only when the card gives neither `phi` nor `nsub`; `tox=1e-7` is written, so `kp` derives as ref5's
    default (`sp_mos1` has no tox by default, `mos1.va:741-747`); the `capmod=bsim` warning applies only when a
    capacitance-dependent analysis runs. New from ref5: the `nsub` derivation warning, the resistor `af` row,
    and diode `hcomp`.
  - *SPICE-05:* the resolver returns declarations (`Decl`, `VaModule`), not revision 2's IR objects, which
    cannot exist before the fragments are parsed; `declare_fragment` is the SPICE side of the first phase.
  - *SPICE-11:* a SIN without a frequency is an error naming both readings, not a warning: which one the
    Reader applies is unknown, and SPICE-13 treats the ambiguous `.tran` field the same way.
  - *SPICE-25:* the CMC decks are not VACASK end-to-end oracles, because openvaf-r rejects their Verilog-A
    files (E91); they are parse fixtures and the opt-in PyMS tier.
  - *SPICE-26 and ENG-03:* ENG-03's letter-device route is adopted for instance parameters, but card
    parameters stay refused on Xyce: ENG-03's own x7 run returned V(A) = 1.01e9 with rc 0 (E64), and the CMC
    decks' success outside vamos (E91) is per module. SPICE-26's level-2002 test bound Xyce's built-in
    DIODE_CMC (its own log), so it shows a level-collision risk, not a dropped card parameter; the Device Count
    Summary check guards against that risk (§4.5 item 13).
  - *ENG-06:* a card inside a branch is excluded by IR-08's rule (ref19 p.491), so `Scope` collects only the
    K-coupled inductors from branches.
  - *ENG-15 against CLI-06:* q.26 is narrowed to the noise `.prn`, not removed.
  - *ENG-21 against ORC-08:* the Cadnip VACASK is not a fidelity oracle, but it is an optional structural
    oracle, with the primitives declared by the harness.
  - *ORC-10:* SPICE-mode `.hdl` is `ahdl_include` with a note, not an error: the manuals do not say Spectre
    rejects it, and the mapping is unambiguous. The question stays open (q.28).
  - *ORC-17/ORC-20 against OUT-01:* `psfcheck` reads the end-to-end values and is required on both legs. The
    vendored psf-parser is a consumer check, because it accepts malformed files (E83).
  - *ORC-15:* E1-E48 get no deck column. The §13 intro names their scratch areas, every new row names its
    decks, and phase 0 harvests them all into `engine_facts/E<nn>/`.
  - *SPICE-28 against ORC-14:* the gate is the whole suite (ORC-14), on the round-6 builds (SPICE-28).
  - *SPICE-15, ORC-12 and ENG-03:* the module scan and the Xyce letter-device route reach the HSPICE path too,
    listed in §4 as deliberate shared fixes under the merge gate.
  - *CLI-15:* the standalone Xyce lookup falls back to `tools.find_real` in the spectre code
    (`engines.xyce_bin() or find_real`), leaving `engines.xyce_bin` unchanged for vcs-ams.
  - *ENG-02:* the lift, its primary proposal, is adopted rather than a deferral. Today's refusal stays for
    the HSPICE path.
  - *Synthesis checks:* ref19 p.491's conditions for duplicate names in `if` branches are added (§3.7), and
    the vector element grammar is read from ref19 p.477 ("not mandatory" spaces) (§3.5).
- **Questions.** Narrowed: q.3, q.4, q.8, q.9, q.10, q.12, q.14, q.15, q.16, q.18, q.23 and q.26. New: q.28-q.42.
  None is closed outright: q.26 was answered for RAW print files but stays open for the noise `.prn`.

### The review of revision 3 (2026-10-04)

Three adversarial lenses then checked revision 3: implementability (IMPL-01…IMPL-25), fidelity to Spectre
(FID-01…FID-14) and the completeness of the tests and the plan (COMP-01…COMP-12). Each finding was checked
again against today's code, the manuals, or a re-run of its experiment (E94-E101). The evidence
of every finding held (FID-11's in part), so all 51 are applied; where a finding offered options, or its
evidence held only in part, the choice is listed at the end.

- **Implementability (IMPL-01…IMPL-25).** The plan's seam is typed: §5.1's table freezes `Action.args` per
  op, `AnalysisStep.args` holds §3.11's evaluated names with errpreset applied and `AnalysisStep.options` the
  options in force, `Target` is a 3-tuple everywhere (with `freq`), and a `SweepLevel` carries each sweep's
  VACASK id, continuation, PSF label, description variable and units, with every grid that is not
  engine-native expanded by `plan.build` (§5.1, §5.2, §10). All of `expr.py`, printers included, and, from
  phase 1 on, every `spice.py` change are S1's (S0 adds the phase-0 stubs); `Raw.exact`, `iter_rows` and the moved `scan`/`fix_scanned` are implemented in
  phase 0, `read_prn` is S4's (§12). The evaluator knows `cpow`, `hypot`, `fmod` and the bitwise operators in
  every dialect, so the printers' strict fold and the emitters' evaluations work on Spectre expressions
  (§4.2; E94). One walker, `tables.path_envs`, gives `plan.build` and the Xyce emitter the same per-path
  values (§3.5, §4.4). `SpectreJob.defaults`, `job.settings()` and `SpectreLog.limits` resolve the settings
  an `options` statement can change (§2.2, §10). The first phase returns the SPICE file statements
  (`FileRef`), spectre.py splices SPICE includes into their scope, inside an open `.subckt` too, and a
  continuation never crosses a file (§3.1, §4.3). `Netlist.va_modules` takes the module attributes to render
  time (§4.1). On Xyce, an alter or sweep hook inside a subckt that more than one path reaches is an error
  (§5.3, §7.2). `ParamRule` gains `match` and `warn`, `MasterRow` gains `instance`, and `plan.build` decides
  the run-dependent rules (§3.9, §10). A folded or stripped parameter is never a sweep or alter target
  (`Instance.folded`, §3.8, §5.2). `collect` needs no IR: the `SweepLevel`s and the SignalMap's listed psf
  types carry units and STRUCT names (§6.4, §8.3, §10). optable keeps `where` a string and adds `also`, so no
  existing test changes (§4, §10; E94). The manual-citing corpus files wait for q.41 behind `lang/` stand-ins
  (§11). `_Parser.expr`'s fast paths, function check, user functions and parse-time fold are switch points
  (§4.3; E94). The Spectre dialects map only `temp` and `tnom`, and parameters named `temper`, `time` or
  `hertz` are errors (§3.2, §3.5). spice.py maps `spice` to `hspice`, and `expr` refuses an unknown dialect
  (§4.1, §4.2). `plan.build` fills `RunPlan.dependents` (§4.1, §5.1). `fix_points(path)` keeps its signature
  over `fix_scanned`, which cosim re-exports (§4.6). `proc.Interrupts` takes the grace and documents its
  protocol, and nvc keeps `INTERRUPT_GRACE` (§2.7, §10). The provenance rows come from `engines.tool_rows`,
  with the Xyce the run executes (§1, §10). `SpectreParseOpts.top_dir`/`top_name` for a stdin netlist (§1,
  §10). Every SPICE element gets `Instance.prim` (§4.1, §4.3). `xyce.output_problems` and
  `device_summary_problems` are public (§7.2, §10). The banner and licence paths are under `vamos/`, and a new
  test checks `spectre.json` (§10). `_colon_clashes` walks `Cond` (§4.3, §4.5 item 1).
- **Fidelity (FID-01…FID-14).** A `mos1`/`mos2`/`mos3` card without `nsub` gets Spectre's documented
  `nsub=1.13e16`, with `phi=0.7`, `gamma=0` and `vto=0` where omitted: without it the targets drop `mos2` and
  `mos3`'s channel-length modulation (§3.9; E95). `mjsw=1/3` is written (§3.9), and the defaults set is not
  called complete before phase 0 has audited every ref5 default against both targets (§3.9, §12). `badmos3=1`
  carries a warning per card, and q.31 records that E69 is the engines' agreement, not Spectre's (§0, §3.9,
  §14). An option ref5 lists without † is ignored when it comes from the defaults variable, `+mpssession` and
  `+mpshost` added (§2.2). `.tran`'s third field is SPICE2G's TSTART, mapped to `outputstart` with a warning;
  the UG's oscillator pair is described as the hand-written comparison it is, and `.option method=` is
  [I] (§0, §4.3, q.9, q.46). Four page citations are corrected (UG pp.50, 97; ref5 pp.630, 631), and the
  §8.2 date example comes from the native `joop-banaan.dc` (§3.8, §3.9, §4.3, §8.2; E101). The state-file
  header follows UG p.204, its layout open as q.49 (§8.6). The Verilog-A part of `-I` is [I, q.42], after
  `CDS_VLOGA_INCLUDE`; openvaf-r takes `-I` (§2.3, §2.8, §3.1; E98). Every inferred tag names its §14
  question (q.43-q.49 new), and a parameter reference across language sections quotes UG pp.47-48 and warns
  (§3.7, §14). The allpub order is [I], resting on one converted sample (§6.4). Both Xyce launchers carry
  PyMS, E63's card-parameter fact holds under `env_for`, and `needs_pyms` selects no binary (§0, §11, §13;
  E96). Phase 3 handles Spectre's 4 σ Monte Carlo truncation, or warns (§12). `-traditional-cpp` is settled
  (§2.6). SPICE-mode `x.N` bins are compared with HSPICE's rule and warned on (§0, §4.3, §5.1).
- **Completeness (COMP-01…COMP-12).** T3c is restated: the CMC "PyMS reference" ran Xyce's built-in ADMS
  devices on the 2026-09-29 library, and under `env_for` BSIM6 segfaults, so every CMC model is an expected
  refusal on Xyce, and lifting the card-parameter refusal needs a route without colliding levels (§4.5 item
  13, §11 T3c, §12; E99; E91 restated). e2e 28 rebuilds psf-parser's real Spectre run and compares the
  results directory with it (§11; E101). `va_modules` gets a declaration preprocessor, a fallback for an
  unknown list and a macro-declared T0 fixture (§3.1, §3.9, §10; E97). e2e 29 runs VACASK's PSP103 ring
  through `ahdl_include`, and phase 3 maps `psp103`, `vbic` and `bsimbulk` onto VACASK's OSDI (§11, §12;
  E98). Each shared fix gets an HSPICE-route regression test from its owner, the built-in-binding guard joins
  `xyce.smoke`, and the CMC decks are no longer called HSPICE-route gold (§3.13, §4, §11). The guide plan
  covers what every personality's section has, help texts end with the guide's path, the run-dependent parts
  move to phase 2, and a guide trial is scheduled (§2.1, §10, §12). Every phase has a "done when", phase 1b
  engine-level expectations and an owner for its decks, and phases 2b-2f add the review, fix rounds, a
  verifier with the ivtest/hazard3 gate, the guide trial and the commit (§4, §12). §13's rows are tiered, and
  T1 lists its rows (§11, §13). The 26 BSIMCMG108/110 decks are expected dispositions (§11; E100). e2e 30
  and new T0 assertions cover the controlled sources, `mutual_inductor`, `jfet`, `mos1`/`mos2`, the `tran`
  output filters, `time_window`, the legacy names, the `ahdlSimDB` rules with a shared cache, `print=yes`,
  `save=none`/`nooutput` and `CDS_AHDL_*` (§11; E100). The Cadnip dumps are committed, and
  `needs_vacask_src`/`needs_vacask_rawread` gate e2e 29 and e2e 13 (§11). A document check makes every
  inferred tag name its question (§0, §11 T0).
- **Choices among a finding's options, and partial evidence:**
  - *IMPL-03:* the evaluator handles the new functions in every dialect (the finding's first option), and
    the bitwise operators too, which a constant bitwise expression needs in the printers' strict fold.
  - *IMPL-08:* a per-path change on Xyce inside a multiply-reached subckt is an error in v1; uniquified
    definitions per path are v2. *IMPL-10:* such targets are refused; re-folding per point is v2.
  - *IMPL-11:* the IR facts travel in the plan (`SweepLevel`) and the SignalMap, not as an `nl` argument to
    `collect`. *IMPL-12:* `where` stays a string with an appended `also` (the first option); no test changes.
  - *IMPL-13:* `var_reference.scs` and `simulator_lang_swap.scs`, which also carry manual text, wait with the
    20 citing files. *IMPL-15:* only `temp` and `tnom` map; `temper` is an ExprError in Spectre mode and the
    temperature in SPICE mode [I, q.29], and parameters named `temper`, `time` or `hertz` are errors.
    *IMPL-16:* both: spice.py maps `spice` and `expr` validates.
  - *FID-01:* the documented `nsub` default is written as a defaults row (a note), with the derivations kept
    off; its use in the depletion width is q.32. *FID-03:* `badmos3=1` stays, with a warning per card.
    *FID-05:* the third field is mapped per SPICE2G with a warning, not refused. *FID-07:* the header follows
    UG p.204; the leading zero is kept [I, q.49]. *FID-08:* `-I` is still searched for Verilog-A includes,
    after `CDS_VLOGA_INCLUDE`, tagged [I, q.42]. *FID-11:* the evidence held in part: E63 ran without
    `env_for`, but E64 ran with its library path (E64); the fix applies to both. *FID-12:* drawn
    samples or a VACASK truncation, with a warning until one exists. *FID-14:* a warning, the Spectre rule's
    card used.
  - *COMP-03:* the preprocessor, not the compiled OSDI descriptor. *COMP-09:* the mismatch is an expected
    disposition; the decks stay unmodified. *COMP-11:* committed dumps, plus a `needs_vacask_rawread` gate.
- **Questions.** New: q.43-q.49. Extended: q.4, q.6, q.8, q.9, q.11, q.17, q.18, q.29, q.31, q.32, q.39, q.40
  and q.41. q.42's openvaf-r half is answered (it takes `-I` [E98]), and the question now asks how Spectre
  uses `-I` for Verilog-A. None is closed.
- **Skeptic pass (2026-10-04).** An independent skeptic audited revision 3 before its commit; its items are
  applied here. *Publish hygiene:* every private path, and every instruction that pointed at a file readers
  cannot have, is gone: the Cadnip build is named by VACASK's `CADNIP_PARSERS` option and the committed
  `cadnip/BUILD` recipe (§3), the §10 and §11 checks are the committed `test_spectre_contract.py` and
  `test_spectre_docs.py`, `VAMOS_VACASK_SRC` and T3b's `SIM_MODULE_PATH` have no baked-in paths (§11), S4
  writes the PSF writer from §8 with E86's date and escaping facts kept (§8.2, §12, §13), and §13's intro says
  once that its script names are the design sessions' scratch directories, harvested into `engine_facts/` in
  phase 0. *Sources:* §0 gains a Sources paragraph with the origin, URL and commit or version of every reused
  asset, and the later mentions follow it (psf_utils `6797146`; cadence2xyce.pl is kev-cam/xyce's, its line
  numbers pinned to 6e81e73f). *Ownership and wording:* S0 adds the phase-0 spice.py stubs and S1 owns
  spice.py from phase 1 on (§10, §12); `VaModule` is new in ir.py, filled by `spice.va_modules` (§4.1, §10,
  §12); `engine_expect.json` joins the e2e layout, and S6 writes both expectation files for e2e 1-10 (§11,
  §12); `test_spectre_masters.py` (S0, phase 0, with its done-when) and `test_spectre_xdm.py` (S6, phase 1b)
  have owners (§3.9, §11, §12); the `SweepLevel.spec` folded-`Num` invariant is stated (§5.2, §10); q.20 and
  q.24 are referenced from §0, §3.8.1 and §3.11; the `nsub` row is tagged `[M …; I, §14 q.32]` and its note
  names the applied default (§3.9, §14); the Verilog-A cache and trailer tests of §11.1 name their code
  owners.
