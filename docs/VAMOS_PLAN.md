# vamos: a drop-in simulator command line

**Status:** plan, not yet implemented (2026-09-27). Manual links: `docs/vamos-manuals.html`.

`vamos` is one Python driver that accepts the command lines of commercial and open
simulators and runs them on our stack: sv2ghdl → nvc for digital, nvc cosim → Xyce for
analog, and stat-sim for variability. There are two ways to call it:

```
vamos -vcs [vcs args...]        # explicit personality
ln -s vamos vcs; vcs [args...]  # personality taken from argv[0]
```

The goal is that existing Makefiles, vendor-generated scripts (Quartus, Vivado, cocotb,
UVM regressions) and CI jobs run unchanged. VCS comes first.

---

## 1. What exists today and what we reuse

| Piece | Where | Role in vamos |
|---|---|---|
| `bin/iverilog-sv2ghdl`, `bin/vvp-sv2ghdl` | bash | iverilog personality. Keep as is until the Python frontend matches them, then make them thin shims |
| `bin/verilator-sv2ghdl` | bash | Verilator personality. It already has the "drop an `obj_dir/Vsim` runner + no-op makefile" pattern that `simv` will copy. Same migration plan |
| `shims/{iverilog,vvp,verilator}` | symlinks | `vcs`, `vlogan`, `vhdlan`, `xrun`… get added here |
| `nvc -a x.v x.sv …` | nvc.c:329 | Translates all Verilog sources together through sv2ghdl, forwarding `+incdir`/`+define`. **vamos should call this path, not re-implement translation** |
| `nvc --load=lib.so` + plusargs | nvc.c:941, 1115 | VPI (`-P`/`-load`) and `+plusargs` at run time |
| `nvc --xyce-netlist/--xyce-config` | nvc.c:948, cosim.c | Backend for the AMS personalities (`vcs -ad`, `xrun -ams`) |
| `xyce/utils/simetrix_cosim.pl`, bfit, PyMS | xyce, sv2ghdl/bfit | Verilog-A/AMS and behavioral models on the analog side |
| stat-sim (ensemble, sky130 mismatch) | /usr/local/src/stat-sim | Monte Carlo and seeds exposed as vamos extensions |

nvc library-path discovery and the "prefer build-area binaries" rule are copied three
times in the bash scripts. They move into one Python module, `vamos/tools.py`.

## 2. Layout (in sv2ghdl)

```
sv2ghdl/
  bin/vamos                  # tiny launcher: locates the package relative to realpath($0) (source or installed)
  bin/vamos-install          # --user / --system / --prefix / --remote / --uninstall (§2a, §2b)
  bin/use-vamos              # wrapper + shell macros (bash/zsh/fish/tcsh) (§2c)
  vamos/
    __main__.py              # personality dispatch: argv[0] basename, or leading -vcs/-xrun/…
    job.py                   # Job IR (see §3)
    argscan.py               # shared tokenizer: -f/-F/-file expansion, $ENV expansion, +a+b+ lists,
                             #   -opt=value, -opt value, comments in filelists
    optable.py               # declarative option-spec machinery + unmapped-option accounting
    sites.py                 # ssh/jump/scheduler site config, rsync staging, remote simv stub (§2b)
    redirect.py              # use-vamos: fake tool homes, modulefiles, namespace overlay, --audit (§2c)
    tools.py                 # locate nvc / iverilog / xyce / sv2ghdl; NVC_LIBDIR; WSL-vs-native;
                             #   find_real() with PATH scrubbing + VAMOS_ACTIVE lock-out (§4a)
    banner.py                # banner profiles + provenance header (§4b)
    licenses.json            # tool -> SPDX licence + URL (site-overridable)
    banners/                 # shipped default profiles, brand = vamos
    backends/
      nvc.py                 # analyse (nvc -a, which routes .v/.sv through sv2ghdl), elaborate, run
      cosim.py               # nvc + Xyce: builds the netlist and config from the analog partition
      statsim.py             # MC / corner sweeps driving the nvc/Xyce backends
      native.py              # optional fall-through to the real tool (like verilator-sv2ghdl)
    personalities/
      vcs.py                 # vcs compile/elab (2-step and 3-step elab)
      vlogan.py, vhdlan.py   # VCS MX analysis steps + synopsys_sim.setup
      simv.py                # runtime personality for the generated ./simv
      vcs_ams.py             # -ad / vcsAD.init / analog files layered on vcs.py
      ncverilog.py, xrun.py  # Cadence (xrun is a superset of irun/ncverilog; + and - aliases)
      questa.py              # vlib/vmap/vlog/vcom/vopt/vsim
      iverilog.py, vvp.py, verilator.py, ghdl.py, nvc.py
    runtime/simv_template.py # the script written as ./simv
  shims/vcs, vlogan, vhdlan, xrun, irun, ncverilog, vlog, vcom, vsim, ghdl, nvc … -> ../bin/vamos
  tests/vamos/               # unittest suites (see §7)
```

Use the Python 3.9 standard library only. That is what Cygwin `/usr/bin/python3` has, and
WSL has no pip. Don't use argparse for the personalities: VCS-style command lines (`+incdir+a+b`,
`-Mdir=x`, single-dash long options, plusargs mixed with files, `-f` nesting) do not fit it.
Hand-scan with a declarative table instead (§4). `vamos`'s own options use a `--vamos-*`
prefix so they can never collide with a vendor option.

## 2a. Installation: personal (`~`) and system (`/usr/local`)

vamos installs from the containerized build (`docker/build_stack.sh`, then `docker/export.sh`) in one of two
ways, from the **same exported tree**:

```
vamos-install --user              # PREFIX=~/.local       (no root)
vamos-install --system            # PREFIX=/usr/local     (sudo)
vamos-install --prefix=/opt/eda   # anything else (site/NFS installs)
vamos-install --uninstall [--user|--system|--prefix=…]
```

`vamos-install` ships in the export tree. It replaces the current "`sudo rsync -a $DEST/ /usr/local/`"
advice in `export.sh`. It copies the tree, writes a manifest (`share/vamos/manifest.txt`) that
uninstall uses, and runs a `vamos --vamos-selfcheck` smoke test at the end.

**Installed layout** (the same under every PREFIX):
```
PREFIX/bin/vamos                  # launcher; finds its package relative to realpath($0)
PREFIX/lib/vamos/                 # the Python package
PREFIX/share/vamos/{banners/,licenses.json,manifest.txt}
PREFIX/libexec/vamos/shims/       # vcs, vlogan, xrun, … symlinks -> ../../../bin/vamos
PREFIX/etc/vamos/                 # system-level config (only used for --system/--prefix)
PREFIX/bin/{nvc,iverilog,Xyce,…}  # the rest of the stack, as build_stack.sh already installs it
```

**Requirements this puts on the build:**
- **Relocatable, with no baked-in prefix.** The container builds into
  `/home/claude/sv2ghdl-stack/usr` and then installs elsewhere, so nothing may depend on the
  configure prefix:
  - nvc: already fine, because it finds its lib dir relative to the exe (`util.c:get_relative_prefix`).
  - Xyce/Trilinos/iverilog `.so` files: link with `-Wl,-rpath,'$ORIGIN/../lib'` in `build_stack.sh`.
    There is no rpath handling there today, so check with `readelf -d` and add a test that runs
    Xyce from a moved tree.
  - The `/usr/local/src/sv2ghdl/bin/iverilog-sv2ghdl` fallback that is hard-coded in nvc
    (`common.c:2636`, `nvc.c:341`) must stay a *last* resort, behind the PATH lookup and a sibling
    lookup (`dirname(nvc)/iverilog-sv2ghdl`).
  - vamos must never read paths from build areas (`/usr/local/src/...-build`) unless it is in
    dev mode (running from a source checkout, or `VAMOS_DEV=1`). The build-area-first rule
    that the bash wrappers use today becomes dev-mode-only.
- **Tool resolution is prefix-first.** `tools.py` resolves in this order:
  1. `$VAMOS_NVC`-style overrides
  2. vamos's *own* PREFIX/bin
  3. PATH, after scrubbing (§4a)
  4. dev-mode build areas

  So a `~/.local` vamos uses its `~/.local` nvc, even when an older `/usr/local` stack exists,
  and one job never mixes versions. The provenance header (§4b) shows the path of every tool
  it used, so any mix-up is visible.
- **Shims are opt-in, especially on system installs.** A `/usr/local/bin/vcs` would shadow a
  real VCS for *every* user. So shims go under `libexec/vamos/shims/`, and there are two ways
  to enable them:
  - The user adds that directory to PATH (`vamos --vamos-env` prints the line to add).
  - `vamos-install --shims=vcs,vlogan,…` links the chosen names into `PREFIX/bin`.

  A personal install can default to `--shims=all`. A system install defaults to none.
- **PATH scrubbing (§4a) covers both prefixes.** It identifies shims by
  `realpath → */bin/vamos`, not by a fixed directory. It therefore removes `~/.local/libexec/vamos/shims`,
  `/usr/local/bin/vcs → vamos`, and any other vamos, whichever copy is running.
- **Layered config.** Settings are read in this order, with later layers overriding earlier ones:
  1. Shipped defaults: `PREFIX/share/vamos/`
  2. System: `PREFIX/etc/vamos/` (e.g. a site banner profile, or `licenses.json` overrides)
  3. User: `~/.config/vamos/` (or `~/.vamos/`)
  4. Project: `./.vamos/`
  5. Environment, then the command line

  This applies to banners (§4b), fall-through policy and tool overrides. `vamos --vamos-config`
  prints the effective settings and which layer each came from.
- **Platform.** The stack is Linux ELF. On a laptop, "personal install" means the WSL home
  (`~/.local` inside WSL). See §2b for how Windows reaches it and how it reaches farm hosts.

## 2b. Deployment model: Windows laptop → WSL → corporate farms over ssh

The usual corporate setup is a company Windows laptop per engineer, with the real compute on
farms. Those farms are reachable only over ssh, and often only a few hosts per silo are
reachable. vamos should treat that as the *normal* case:

1. **Windows entry.** `vamos-install --user` inside WSL also drops Windows-side launchers
   (`vamos.cmd`, plus `.cmd` shims for the enabled personalities) into a user-writable Windows dir
   such as `%LOCALAPPDATA%\vamos\bin`, and offers to add it to the user PATH. Each launcher runs
   `wsl -d <distro> -- ~/.local/bin/vamos -<personality> …` and converts Windows paths in
   arguments (`C:\…` to `/mnt/c/…`). It passes arguments as a vector, never through a shell
   string, so no `wsl bash -lc` quoting problems (the [[wsl-cygwin-gotchas]] lesson). Cygwin/MSYS
   users get the same through a sh launcher.
2. **Local first.** Jobs run in WSL by default. That covers most unit-level RTL and small AMS runs
   entirely on the laptop.
3. **Farm targets ("sites").** Each site is a config entry (user or project layer, §2a):
   ```json
   { "name": "farm-a", "ssh": "login-a.corp", "jump": ["bastion.corp"],
     "scheduler": "lsf|slurm|sge|none", "submit": "bsub -q regr -n {cpus}",
     "prefix": "~/.local", "workdir": "/proj/scratch/$USER/vamos", "fs": "shared|rsync" }
   ```
   `vamos --vamos-site=farm-a vcs …` (or `VAMOS_SITE`) runs the *same command line* there:
   - `fs=rsync`: the job's sources and filelists are resolved locally (the `Job` IR already has
     the full file set) and rsynced to `workdir`. The job runs through the scheduler, and results
     and logs are rsynced back. `./simv` becomes a local stub that re-runs remotely.
   - `fs=shared`: the paths are already visible on the farm, so no copying is needed.
   - Reachability is checked per site (`vamos --vamos-sites` probes with `ssh -o BatchMode=yes`
     through the jump chain) and is never assumed. With siloed access, a site that works from one
     laptop may not work from another.
   - All ssh use goes through the user's own `~/.ssh/config` and agent. vamos never stores
     credentials, and routing is limited to hops the user can already reach.
4. **Getting vamos onto a farm.** `vamos-install --remote=farm-a` pushes the relocatable export
   tree (§2a) to `prefix` on the site: a `--user` install in the farm home, no root needed.
   Farm nodes are usually RHEL/Rocky, so the container export must target the oldest glibc in use.
   Add a `Dockerfile.rocky8` (or a manylinux-style base) next to the existing distro Dockerfiles.
   The version on each site is checked against the local one before a job runs.
5. **Provenance crosses the hop.** The §4b tool/licence header records which site and host ran
   each stage.

## 2c. `use-vamos`: redirecting tools buried inside scripts

Real flows rarely call `vcs` directly. They run `make regress`, or a Perl/Python regression
runner that calls a vendor tool five layers down. `use-vamos` makes those calls land on vamos
without editing the scripts:

```
use-vamos make regress                  # wrapper: run one command with vamos in front
use-vamos -p vcs,vlogan runsim.pl …     # only redirect these personalities
source <(vamos --vamos-shell=bash)      # shell macro: `use-vamos on|off|status` in the current shell
                                        #   (bash/zsh/fish; plus a tcsh/csh version — common on farms)
```

What it does, in increasing strength:
1. **PATH:** prepends the shim dir, for all personalities or only the ones chosen with `-p`.
2. **Tool-home variables:** many scripts call `$VCS_HOME/bin/vcs` or `$CDS_INST_DIR/tools/bin/xrun`
   directly. use-vamos points `VCS_HOME`, `VCS_MX_HOME`, `XCELIUM_HOME`/`CDS_INST_DIR`,
   `MTI_HOME`/`QUESTA_HOME` and similar at a generated **fake tool tree**
   (`~/.cache/vamos/homes/vcs/bin/vcs` → vamos) that has the directory layout the scripts expect.
   It also covers the files those scripts read, such as `$VCS_HOME/etc/uvm` or a stub `synopsys_sim.setup`.
3. **Environment Modules / Lmod:** farm scripts often run `module load vcs/2023.03`, which
   prepends the *real* tool again. use-vamos puts a vamos modulefile directory first in
   `MODULEPATH`, so `module load vcs[/any-version]` resolves to a modulefile that sets up the vamos
   tree instead.
4. **Hard-coded absolute paths** (`/tools/synopsys/vcs/2023.03/bin/vcs`): these are opt-in via
   `use-vamos --overlay`. It runs the command in an unprivileged user and mount namespace (`bwrap`
   or `unshare -rm`, which works under WSL2 and most farm kernels) with the vamos tree bind-mounted
   over the listed vendor prefixes. If namespaces are unavailable, it reports the paths it could not
   redirect rather than doing nothing silently.

Interaction with §4a: use-vamos exports `VAMOS_REDIRECT=<list>`. This tells the PATH scrubbing and
lock-out code that shim dirs, fake homes and modulefiles are all vamos-owned. They are removed
before any *real* tool is run, including when `VAMOS_FALLTHROUGH` hands off to the vendor tool.
`use-vamos --audit make regress` runs in record-only mode: every vendor-tool call is logged (argv,
cwd, the script that called it) and still passed through to the real tool. This measures what a flow
needs before switching it over, and the argv lines it collects feed the §7 invocation corpus.

## 3. The Job IR

Every personality turns its argv into a `Job`. Backends only read `Job`s, so
adding a personality never touches a backend:

- `sources`: ordered list of `(path, lang, library)`. lang ∈ {verilog, sv, vhdl, vams, spice, c}.
  Language comes from the extension and personality flags (`-sverilog`, `+systemverilogext+`).
- `libs`: `-v file`, `-y dir`, `+libext+`, plus the logical-library → directory map (synopsys_sim.setup, vmap)
- `incdirs`, `defines`, `undefines`, `timescale`, `std` (v2001/v2005/sv2012/sv2017/vhdl93/08/19)
- `top` (one or more), `generics/parameters` (`-pvalue+`, `-gv`, `-G`)
- `outputs`: executable name (`-o simv`), work dir (`-Mdir`), log (`-l`), `daidir`
- `stages`: which of analyse / elaborate / run this call does (vlogan = analyse only;
  `vcs -R` = all three; `xrun` = all three by default; `vsim` = run)
- `run`: plusargs, runtime log, seed, stop time, `-ucli -do script`, `+vcs+finish`
- `debug`: waves requested (`-debug_access`, `+vcs+vcdpluson`, `-kdb`, `-access +rwc`) → nvc `--wave`
- `coverage`: `-cm line+cond+fsm+tgl` → nvc `--cover=` mapping (partial)
- `foreign`: VPI libs (`-P tab`, `-load`), DPI C sources and `.so` files, CFLAGS/LDFLAGS
- `analog`: partition spec (vcsAD.init / amscf.scs) and analog netlists → cosim backend
- `variability`: vamos extension (`--vamos-mc N`, `--vamos-corner`) → statsim backend
- `unmapped`: every option seen but not implemented, each with a disposition (§4)

The `Job` gets serialised to `<daidir>/vamos.job.json`. That lets the three-step flow and the
generated `./simv` pick up where the compile left off, and it doubles as the debugging artefact
("what did vamos think I asked for?").

## 4. Option tables and "accept everything"

"Accepts all the arguments vcs does" means **no VCS option causes a usage error**. It does not mean
every option is implemented. Each personality has a data table:

```python
Opt("-timescale", arity="eq",   act=set_("timescale"))
Opt("+incdir+",   arity="plus", act=extend("incdirs"))
Opt("-debug_access", arity="suffix", act=waves)          # -debug_access, -debug_access+all, ...
Opt("-full64",    act=IGNORE)                             # meaningless here, silent
Opt("-lca",       act=IGNORE)
Opt("-kdb",       act=NOTE("Verdi KDB not produced; use --vamos-wave=fst"))
Opt("-cm",        arity="next", act=coverage)
Opt("-ntb_opts",  arity="next", act=ntb_opts)             # uvm → §6 gap
Opt("-xprop",     arity="eq_opt", act=UNSUPPORTED)        # accepted, recorded, warned
```

There are four dispositions: **mapped**, **ignored** (no effect in our world, e.g. `-full64`, `-lca`,
licence options), **noted** (accepted, prints a one-line note, e.g. Verdi options), and
**unsupported** (accepted, warns, and logs to `vamos.unmapped.log`). Unknown options get a warning.
The run carries on. `--vamos-strict` turns unsupported and unknown options into errors, for CI.
Walking the manual's option index is how we populate these tables. The unmapped log is how we
set priorities: collect it across the test corpus and implement the most common options first.

Real Makefiles rely on some tokenizer behaviour, so it must be exact:
- `-f file` paths are relative to the cwd, and `-F file` paths are relative to the file (VCS semantics).
  Nested files, `//` and `#` comments, and `$VAR`/`${VAR}` expansion all have to work
- Both `+define+A=1+B` and `-define A=1` forms
- Source vs. plusarg: a `+foo` that no table claims is a compile-time plusarg. VCS then warns
  and ignores it, and so do we
- VCS takes `-o name`, while Cadence xrun accepts `+`/`-` duplicates of most options

## 4a. Calling other tools: PATH scrubbing and re-entry lock-out

- **PATH scrubbing.** Whenever vamos execs a real tool (nvc, iverilog, Xyce, or a real vendor
  tool on fall-through), the child's `PATH` has every vamos shim directory removed.
  It also drops any directory whose entry for that tool name resolves to vamos
  (`os.path.realpath` equals `bin/vamos`). The same applies to the child's own sub-calls, so a real
  `vcs` that runs `gcc`, or an `xrun` that runs `xmvlog`, can never land back in our shims.
  `tools.find_real(name)` does the lookup with the scrubbed PATH and never returns vamos.
- **Lock-out.** vamos exports `VAMOS_ACTIVE=<pid>:<personality>`, plus `VAMOS_DEPTH`, which goes up by
  one on each entry. If a shim is entered while `VAMOS_ACTIVE` is set for the same personality,
  it does not dispatch again. It execs `find_real(name)` straight away, or errors out if no real tool exists.
  `VAMOS_DEPTH > 4` is a hard error that shows the call chain. Two things get this
  protection: the generated `./simv`, which re-enters vamos, and shims that share a real tool's
  name (`nvc`, `ghdl`, `iverilog`). **Result: an `nvc` symlink is safe**, and the shim
  list now includes it.

## 4b. Banners, product text and tool/licence provenance

- **No vendor product dialogue.** vamos never copies vendor copyright, licence-checkout or
  licence-server text (`Licensed to …`, `Compiler version …`, `This program is proprietary …`,
  and the `+vcs+lic+wait` chatter). Licence-related options are still accepted, but they are ignored.
- **Provenance header instead.** Every compile and every run starts with the tools that were
  *actually invoked* on this job, each with its resolved path, version (from `--version`) and licence:
  ```
  vamos 0.1 (vcs personality) — tools used:
    sv2ghdl   <rev>    GPL-3.0-or-later /usr/local/src/sv2ghdl  (vamos ships under this too)
    nvc       1.x-dev  GPL-3.0         /usr/local/src/nvc-build/bin/nvc
    iverilog  13.0     GPL-2.0         /usr/local/src/iverilog/_install/bin/iverilog
    Xyce      7.x      GPL-3.0         (only when the job uses cosim)
    bfit      <rev>    PolyForm-NC-1.0.0               (only when behavioural substitution is used)
    stat-sim  <rev>    PolyForm-NC-1.0.0 + commercial   (only when MC is used)
  ```
  The licence data comes from `vamos/licenses.json` (SPDX id and a URL for each tool, which a site
  can override). It is not guessed. The same list is written to `<daidir>/vamos.tools.json` for
  audit and compliance, and `vamos --vamos-licenses` prints it in full. sv2ghdl, and vamos
  inside it, are GPL-3.0-or-later because they are front-end glue: the heavy lifting happens in nvc, Xyce and
  the other listed tools. The README used to say "GPL v2+"; the
  licence files are now in place (`COPYING`, `LICENSE`, `bfit/LICENSE`, added 2026-09-27). **bfit and stat-sim are
  PolyForm-Noncommercial-1.0.0** (§9.5). Each has its own entry in the header, and vamos only
  ever runs them as **separate programs** (subprocess/CLI). It never imports them into the
  GPL-3.0 vamos process, so the two licences are never combined in one work.
- **Programmable banners.** All text that the user sees and that a vendor tool would also print
  (banners, compile summary, run report, `$finish` line, error and warning prefixes) comes from a
  **banner profile**. There is one template file per personality, in JSON (stdlib only), with
  `{brand}`, `{Brand}`, `{BRAND}` and `{brand_spaced}` placeholders, plus timing and counts fields.
  - The shipped default is `brand = vamos`, so where VCS would print
    `V C S   S i m u l a t i o n   R e p o r t` you get `V A M O S   S i m u l a t i o n   R e p o r t`
  - Lookup order: `--vamos-banner=<name|path>`, then `$VAMOS_BANNER`, then `./.vamos/banners/`, then
    `~/.vamos/banners/`, then the shipped defaults. Users can create a profile that matches the
    vendor's layout exactly for log-scraping scripts. Profiles only control format. They cannot bring the
    licence dialogue back, because it has no template slot.
  - `--vamos-banner=none` prints nothing but the provenance header.

## 5. VCS in detail (phase 1)

### 5a. Two-step: `vcs [opts] files…` then `./simv [runtime opts]`
1. Scan argv into a `Job`. Make `csrc/` and `simv.daidir/` (named after `-o`, honour `-Mdir`).
2. Analyse: one `nvc -a --work=… <all .v/.sv/.vhd>` call inside `simv.daidir/`, with incdirs and
   defines forwarded. `-v`/`-y` library resolution: first version pulls in the whole library file
   or directory. Later, resolve only the modules that are actually referenced.
3. Elaborate: `nvc -e <top>`. If `-top` is missing, find the top-level modules (not-instantiated
   modules) from sv2ghdl's `_metadata`, the same way `vvp-sv2ghdl` does.
4. Write `./simv` (from `runtime/simv_template.py`): a small executable script that points at
   `vamos.job.json` and runs `vamos -simv "$@"`. With `-R`, run it right away with the leftover
   runtime arguments.
5. Print banners and summaries through the banner-profile system (§4b). The default profile has
   the same shape as VCS output (`Top Level Modules:`, `CPU time`, the runtime report footer,
   `$finish at simulation time`) but brands it as VAMOS, not the vendor product.

### 5b. Three-step: `vlogan` / `vhdlan` / `vcs top`
- Parse `synopsys_sim.setup` (cwd, then `$HOME`, then `$VCS_HOME/bin`): `WORK > DEFAULT`,
  `lib : ./path`, `OTHERS=` includes. Map each logical library to an nvc library dir, with `-L`/`--map`.
- `vlogan` / `vhdlan` do analysis only, into the mapped lib (`-work lib`). `vcs [-top] cfg|lib.top` elaborates.
- This is also how VHDL/Verilog mixed designs come in. nvc already handles mixed via the
  sv2ghdl translation.

### 5c. `simv` runtime personality
- It is a Python script that re-enters vamos under the §4a lock-out.
- Accepts `+plusargs` (nvc passes them through), `-l log`, `+ntb_random_seed=`, `+vcs+finish+N`,
  `+vcs+lic+wait` (ignored silently, §4b), `-ucli -do file` (subset: `run`, `quit`, `dump -add`, `force`),
  `-gui` (note, then open a wave viewer on the dump if one is available), `+fsdbfile`, `+vpdfile`
  (waves go to FST/VCD with the requested basename).
- `$vcdpluson`, `$fsdbDumpvars`, `$dumpfile`: `$dumpfile`/`$dumpvars` already work through sv2ghdl.
  The VPD/FSDB system tasks map to nvc wave dumping (they need sv2ghdl/nvc system-task stubs;
  check which exist).
- Exit status follows VCS: 0 on `$finish`, non-zero on `$fatal` or errors.

### 5d. Known gaps to size up early (they decide what "drop-in" can honestly claim)
- **UVM** (`-ntb_opts uvm`): this needs SystemVerilog classes, constraints and so on through sv2ghdl. It is almost
  certainly the biggest real-world blocker. For now, detect it and fail clearly (or fall through to
  the real tool). Handle it as its own sv2ghdl project.
- **DPI-C** (`.c` sources on the vcs line, `-CFLAGS`, `-LDFLAGS`): compile them to a `.so`. Check
  how far nvc/sv2ghdl get with `import "DPI-C"`.
- **Coverage** (`-cm`, `urg`): map line and toggle coverage onto nvc coverage. The `urg` report is out of scope at first.
- **Partition compile, `-j`, `-lca` features**: ignore.
- **`-xprop`**: nvc's X semantics differ. Record it as a known behaviour difference.

## 6. VCS AMS (phase 2)
- Trigger: `-ad`, `-ad=vcsAD.init`, `-ad_hsopt`, `.sp`/`.spi`/`.scs` analog files, or `vams` sources.
- Parse `vcsAD.init`: `choose <engine> <netlist> …;` (the netlist and analog options go to Xyce;
  `xa`/`finesim`/`hsim`/`nanosim` are all read as "Xyce"), `partition -cell / -inst`,
  `use_spice -cell`, `set bus_format`, `a2d`/`d2a` thresholds and `set rise/fall`.
- Build: the digital side through the vcs flow, the analog partition's subcircuits through
  ltz/Xyce, and the interface elements from the a2d/d2a rules. `./simv` then runs
  `nvc -r --xyce-netlist … --xyce-config …`.
- Verilog-A/AMS modules are routed through bfit/PyMS the way `pyms-not-adms` does (engines are thin wiring).
- The same `cosim` backend later serves `xrun -ams` (amscf.scs `amsd { portmap…; config cell=… use=spice }`)
  and Questa ADMS.
- stat-sim hook: `+vamos_mc=N` / `--vamos-mc N` runs N seeds of the sky130 mismatch models through
  `backends/statsim.py`. This is where the patent tools (probability waveforms, defect binning) surface
  to a user who only knows VCS.

## 7. Testing
- **Tokenizer and table unit tests** (unittest, stdlib only): corpus lines → expected `Job` JSON.
  Include pathological cases: nested `-F`, env vars, `+define+` with `=` and quotes, plusargs mixed with sources.
- **Invocation corpus**: gather real command lines from the manuals' examples, the Intel/Achronix/
  SpinalHDL scripts, EDA Playground and cocotb's `Makefile.vcs` (links in the HTML). Each must
  parse with zero *unknown* options.
- **Equivalence**: run `tests-RTL` and the iverilog suite through `vcs … && ./simv`, and compare
  results against `iverilog-sv2ghdl`/`vvp-sv2ghdl` on the same inputs. They share a backend, so
  any difference is a vamos bug.
- **cocotb acceptance**: run `SIM=vcs make` with our `vcs` shim first on PATH (VPI through `nvc --load`).
  This one test covers the plusargs, VPI, top selection and exit-code contracts.
- **AMS**: `xyce/utils/test_inv_chain` rebuilt as a `vcs -ad` testcase.
- Hook into `regress/` (`delegate-regressions`) so vamos parity gets tracked with the rest.

## 8. Phases

| # | Deliverable | Exit criterion |
|---|---|---|
| 0 | Skeleton: dispatcher, `argscan`, `Job`, `tools.py` (PATH scrub + lock-out), `banner.py` + provenance header, nvc backend, `bin/vamos`, shims | `vamos -vcs -R counter.v` prints `$display` output under a VAMOS banner with the tool/licence list; an `nvc` shim on PATH does not recurse |
| 1 | VCS two-step + `simv` runtime + option table populated from the UG index; `vamos-install` + relocatable (`$ORIGIN` rpath) container export | tests-RTL parity with iverilog-sv2ghdl; corpus has 0 unknown options; the same export tree installs with `--user` and `--system` and passes `--vamos-selfcheck` in both, including when both installs coexist |
| 1b | `use-vamos` (PATH + fake homes + modulefiles, `--audit`), Windows `.cmd` launchers | `use-vamos make` on an unmodified vendor-style Makefile runs under vamos; the `--audit` corpus feeds §7 |
| 2 | VCS MX three-step (`vlogan`/`vhdlan`/`synopsys_sim.setup`), mixed VHDL | Intel Quartus-generated VCS script runs unmodified |
| 3 | Waves/debug (`-debug_access`, VPD/FSDB tasks → FST), `-ucli -do` subset, VPI `-P`/`-load` | cocotb `SIM=vcs` smoke passes |
| 4 | VCS AMS (`-ad`, vcsAD.init → nvc+Xyce cosim) + stat-sim MC extension | inv-chain cosim reproduces run_cosim.sh result |
| 4b | Sites: ssh/jump/scheduler execution, `--remote` install, Rocky8-baseline export, `use-vamos --overlay` | a laptop job runs on a reachable farm host through LSF/Slurm with results back locally |
| 5 | Move iverilog/vvp/verilator bash logic into personalities; bash scripts become shims | existing iverilog/verilator regressions unchanged |
| 6 | Cadence: ncverilog, irun, xrun (+ `-ams` via cosim backend) | EDA Playground xrun lines parse; tests-RTL parity |
| 7 | Questa (vlib/vmap/vlog/vcom/vsim), ghdl (`-a/-e/-r` → nvc), nvc pass-through with vamos extensions | cocotb `SIM=questa`/`ghdl` smoke |

Phase 0+1 is the VCS MVP. UVM and DPI (§5d) run as their own sv2ghdl tracks in parallel,
because they decide how much of the real world phase 1 can take on.

## 9. Decisions (settled 2026-09-27)
1. **Calling a real tool:** vamos runs by default, and `VAMOS_FALLTHROUGH=1` hands off to a real vendor tool
   when one exists. Whenever vamos calls a real application, it removes the vamos path from `PATH` (§4a).
   The provenance header records which tool actually ran.
2. **`./simv` and same-name shims:** `./simv` is a Python script that re-enters vamos, and lock-out code
   prevents recursion (§4a). The same guard makes `nvc`/`ghdl`/`iverilog` symlinks safe.
3. **Banners:** they are programmable profiles. The default text says VAMOS/Vamos/vamos, not the
   product name, and users can pick or write a profile that matches the vendor exactly (§4b).
4. **Vendor dialogue:** licensing and copyright text is not copied. vamos prints the list of tools it
   used, each with its own licence (§4b).
5. **Licences:** sv2ghdl and vamos are GPL-3.0 (glue). **bfit is PolyForm-NC-1.0.0**, like
   stat-sim. bfit is self-contained, with no imports in either direction between it and the rest
   of sv2ghdl, and all of its commits are by the owner, so relicensing is clean. Mechanics: add a
   `bfit/LICENSE` (PolyForm-NC), a top-level `COPYING` (GPL-3.0) with a note that `bfit/` is
   excluded, and change the README line. Earlier bfit revisions already published under
   "GPL v2+" stay GPL for anyone who has them. The new licence applies from the relicensing commit
   onward. Consider moving bfit into its own repo later to make the boundary obvious.
6. **Installation:** from the container export, both personal (`~/.local`, no root) and system
   (`/usr/local`) installs are supported, from one relocatable tree (§2a).
7. **Entry point:** a Windows laptop running WSL is the primary environment. Corporate farms are
   reached over ssh as configured "sites" (siloed, probed per site, never assumed) (§2b).
8. **use-vamos:** a wrapper plus shell macros that redirect vendor tools buried in scripts via
   PATH, fake tool homes, modulefiles and, optionally, a namespace overlay (§2c).
