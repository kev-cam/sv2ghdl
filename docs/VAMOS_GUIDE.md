# vamos user guide

How to run your VCS and VCS AMS command lines and scripts on the open stack.
Covers vamos 0.1.0 as of 2026-10-03. Every command below was run under WSL2 with Python 3.14, and the
output shown is that run's output, shortened where marked `...`, with the working directory shown as
`/work/ams`, the sv2ghdl checkout as `/usr/local/src/sv2ghdl` and its commit as `git-<commit>`.

Contents: [1 What vamos is](#1-what-vamos-is) · [2 Set up](#2-set-up) ·
[3 Quick start: digital](#3-quick-start-digital) · [4 Options vamos does not implement](#4-options-vamos-does-not-implement) ·
[5 Quick start: AMS](#5-quick-start-ams) · [6 The control file in brief](#6-the-control-file-in-brief) ·
[7 vamos options and environment variables](#7-vamos-options-and-environment-variables) ·
[8 Banners, provenance and licences](#8-banners-provenance-and-licences) · [9 Troubleshooting](#9-troubleshooting) ·
[10 Limitations](#10-limitations) · [11 Other documents](#11-other-documents)

## 1. What vamos is

vamos is a drop-in replacement for a simulator's command line. You keep your `vcs ...` compile line and your
`./simv ...` run line, and vamos runs the job on open-source tools:

- **digital** (Verilog, SystemVerilog): iverilog's VHDL back end and the sv2ghdl scripts translate the design
  to VHDL, and nvc simulates it;
- **AMS** (`vcs-ams`, `vcs -ad`): the digital side as above; HSPICE-dialect SPICE cells on VACASK (the
  default) or Xyce; nvc couples the two.

vamos never runs a vendor tool, and needs no vendor licence.

**Working today:** the `vcs` personality (the two-step flow: `vcs`, then `./simv`), `vcs-ams`, the `simv`
runtime, and an `nvc` pass-through. **Planned:** the VCS MX three-step flow (`vlogan`/`vhdlan`), Cadence
`xrun`/`irun`/`ncverilog` and Questa (docs/VAMOS_PLAN.md §8), `spectre` (docs/VAMOS_SPECTRE_DESIGN.md), an
installer and `use-vamos` (docs/VAMOS_PLAN.md §2a, §2c).

The tools a job runs:

| tool | role | licence |
|---|---|---|
| vamos, sv2ghdl | this driver; the translation scripts | GPL-3.0-or-later |
| iverilog (kev-cam fork) | preprocessing; Verilog to VHDL (`tgt-vhdl`) | GPL-2.0-or-later |
| nvc (kev-cam fork) | VHDL simulation; runs the co-simulation | GPL-3.0-or-later, see below |
| VACASK (kev-cam fork) | default analog engine | AGPL-3.0-only |
| OpenVAF-r | compiles Verilog-A for VACASK | GPL-3.0-only |
| Xyce (kev-cam fork) | alternative analog engine | GPL-3.0-or-later |

Each compile and each run prints the tools it actually used (§8). `vamos --vamos-licenses` prints the whole
licence table, with the source each tool is built from (the kev-cam forks, and the project each fork comes
from); it also names tools that planned personalities will run.

The kev-cam nvc fork adds a notice to `nvc --version`: "This build contains modifications covered by
patents ... Commercial use requires a separate license. See https://github.com/kev-cam/nvc for details."
Read those terms before you use the stack commercially.

## 2. Set up

### Requirements

- Linux, or WSL2 on Windows: the stack is Linux ELF, so run everything inside WSL.
- Python 3.9 or newer, standard library only. `VAMOS_PYTHON` selects another interpreter.
- An sv2ghdl checkout. Keep `bin/vamos`, `bin/iverilog-sv2ghdl` and the `vamos/` package together: the
  launcher finds the package from its own real path, so link to `bin/vamos` rather than copying it.
- nvc and iverilog from the kev-cam forks, built with the vamos translator patches. vamos looks for them in
  `VAMOS_NVC`/`VAMOS_IVERILOG`, then in its own directory, then on PATH, then, when it runs from a source
  checkout, in `/usr/local/src/nvc-build/bin/nvc` and `/usr/local/src/iverilog/_install/bin/iverilog`. An
  override must name an executable file (a bare name is looked up on PATH); a bad one is an error, never a
  fallback to another copy.
- For AMS: VACASK with openvaf-r, Xyce, or both, plus nvc's `libcosim_bridge.so`, all built with the vamos
  co-simulation patches. vamos refuses to run with unpatched engines (the "rebuild" message in §9). Where
  vamos looks for the engines is in §7.

**Getting the stack.** vamos, `vcs-ams` included, and this guide are on sv2ghdl's `main` branch; the
engine and translator patches vamos needs (docs/VAMOS_AMS_DESIGN.md §7) are on the default branches of
the kev-cam forks: iverilog and VACASK (`main`), nvc and xyce (`master`). `docker/build_stack.sh`
(README.md) clones each fork at the head of its default branch; it builds iverilog and nvc (mode
`digital`, with ghdl and yosys) and Trilinos and Xyce (mode `analog`; `full`, the default, builds both).
It skips a tool that is already installed in its prefix, so remove an older build first. Three parts of
the AMS stack are built by hand:

- nvc's `libcosim_bridge.so`, which nvc's own build does not make: in the nvc source tree,
  `c++ -O2 -shared -fPIC -o <nvc prefix>/lib/libcosim_bridge.so src/cosim_bridge.cpp`;
- Xyce's C interface, `libxycecinterface.so`, which the default target of Xyce's build leaves out: build
  the `xycecinterface` target in the Xyce build directory;
- VACASK, with OpenVAF-r, its Verilog-A compiler: build the kev-cam/VACASK fork as its README.md describes
  ("Building VACASK", which also says where to get `openvaf-r`).

§7 lists the variables that point vamos at builds outside its default paths. build_stack.sh also installs
vamos into its prefix (`bin/vamos` with the package in `lib/vamos`), so `<prefix>/bin/vamos` works as well
as the checkout's `bin/vamos` (build_stack.sh clones sv2ghdl to `~/sv2ghdl`); the drop-in shims are in the
checkout's `shims/`.

### Put vamos on PATH

```sh
export SV2GHDL=/usr/local/src/sv2ghdl        # your checkout
export PATH=$SV2GHDL/bin:$PATH
vamos --vamos-version                        # vamos 0.1.0
vamos -h                                     # the personalities and every vamos option
```

### The drop-in names

vamos takes its personality from the name it was run as. You can put the shipped shim directory first on
PATH:

```sh
export PATH=$SV2GHDL/shims:$PATH             # vcs, vcs-ams, nvc
vcs -h                                       # usage of the vcs personality (vcs-ams -h has its own)
```

Every help text ends with the path of this guide.

Or you can make your own directory that holds only the names you want:

```sh
mkdir -p ~/vamos-shims
ln -sf $SV2GHDL/bin/vamos ~/vamos-shims/vcs
ln -sf $SV2GHDL/bin/vamos ~/vamos-shims/vcs-ams
export PATH=~/vamos-shims:$PATH
```

`shims/` also holds `iverilog`, `vvp` and `verilator`. These are the older sv2ghdl wrappers
(`bin/*-sv2ghdl`), not vamos. With the whole directory on PATH, the `iverilog` command in your shell runs
the wrapper. If you only want `vcs`, use your own directory.

You can also run a personality without a link: `vamos -vcs <args>` is the same as `vcs <args>`, and
`vamos -vcs-ams <args>` is the same as `vcs-ams <args>`.

What "drop-in" means here:

- `vcs` writes `./simv` (a small shell script that runs vamos again) and `simv.daidir/`. Makefiles and
  scripts that call `vcs ...` and then `./simv ...` run unchanged, as long as `vcs` is found on PATH. vamos
  does not yet redirect a script that calls an absolute path such as `$VCS_HOME/bin/vcs`.
  `use-vamos`, which will handle that case, is planned (docs/VAMOS_PLAN.md §2c).
- `vcs-ams` is `vcs` with `-ad` implied.
- vamos never calls a vendor tool. Every process it starts gets a PATH with the shim directories removed.
  If a tool calls a shim name again, a lock-out hands the call to the real tool instead (`VAMOS_STACK`). So
  an `nvc` link to vamos is safe: the `nvc` personality passes its command line unchanged to the real nvc.
- `./simv` holds the path of vamos and runs the `simv.daidir` next to itself, as VCS's simv does. Move or
  copy `simv` and `simv.daidir` together, and the copy runs its own build, an AMS build on either engine
  included (the VACASK deck loads its compiled Verilog-A relative to itself); a symlink to `simv` alone runs
  the daidir beside its target.
- A compile disables `./simv` when it starts and writes the real one only when it succeeds. After a failed
  or interrupted compile, `./simv` prints `vamos: error: ./simv: the last compile into simv.daidir failed or
  was interrupted, so there is nothing to run; compile again` and exits 1. A first compile that fails leaves
  no `./simv`, as VCS does. A command-line error (a file that cannot be opened, a bad option) stops before
  that and leaves the previous build runnable.

### Check the install

`vcs --vamos-version` prints `vamos 0.1.0` when the `vcs` name reaches vamos. The quick starts are the real
check: §3 for digital, §5 for AMS. Each compile and each run lists the tools it used ("tools used"), with
their versions and paths; a `?` as a version means the tool printed no version: it could not run, or it
printed an error (§9).

## 3. Quick start: digital

In an empty directory, run `mkdir rtl` and make these two files:

```verilog
// rtl/counter.v
`timescale 1ns/1ps
module counter #(parameter W = 4) (input clk, input rst, output reg [W-1:0] q);
  always @(posedge clk)
    if (rst) q <= 0;
    else     q <= q + 1;
endmodule
```

```verilog
// tb.sv
`timescale 1ns/1ps
module tb;
  reg clk = 0, rst = 1;
  wire [3:0] q;
  integer cycles = 5;
  counter dut (.clk(clk), .rst(rst), .q(q));
  always #5 clk = ~clk;
  initial begin
    if ($value$plusargs("CYCLES=%d", cycles)) $display("running %0d cycles", cycles);
    #12 rst = 0;
    repeat (cycles) @(posedge clk);
    #1 $display("q=%0d at %0t", q, $time);
    if ($test$plusargs("verbose")) $display("verbose mode");
    $finish;
  end
endmodule
```

Compile, and with `-R` run straight away:

```
$ vcs -full64 -sverilog tb.sv rtl/counter.v -R

VAMOS compile (vcs personality), vamos 0.1.0

vamos 0.1.0 (vcs personality) - tools used:
  vamos     git-<commit>   GPL-3.0-or-later           /usr/local/src/sv2ghdl/bin/vamos
  sv2ghdl   git-<commit>   GPL-3.0-or-later           /usr/local/src/sv2ghdl/bin/iverilog-sv2ghdl
  iverilog  13.0           GPL-2.0-or-later           /usr/local/src/iverilog/_install/bin/iverilog
  nvc       1.19-devel     GPL-3.0-or-later           /usr/local/src/nvc-build/bin/nvc
Top Level Modules:
       tb
Vamos: ./simv is up to date
CPU time: 0.260 seconds to compile + 0.040 seconds to elab
vamos 0.1.0 (simv personality) - tools used:
  vamos     git-<commit>   GPL-3.0-or-later           /usr/local/src/sv2ghdl/bin/vamos
  nvc       1.19-devel     GPL-3.0-or-later           /usr/local/src/nvc-build/bin/nvc
q=5 at 56000
FINISH called
           V A M O S   S i m u l a t i o n   R e p o r t 
Time: 56ns
CPU Time:      0.060 seconds;
```

Run again with plusargs. Every `+plusarg` reaches `$test$plusargs` and `$value$plusargs`:

```
$ ./simv +CYCLES=9 +verbose
vamos 0.1.0 (simv personality) - tools used:
...
running 9 cycles
q=9 at 96000
verbose mode
FINISH called
           V A M O S   S i m u l a t i o n   R e p o r t 
Time: 96ns
CPU Time:      0.060 seconds;
```

Option files, a different executable name, and logs. Put the options in a file, `files.f`:

```
// option file: paths relative to the directory vcs runs in
-sverilog
+incdir+rtl
rtl/counter.v
tb.sv
```

```
$ vcs -f files.f -l comp.log -o sim/tb_simv
...
Vamos: ./sim/tb_simv is up to date
...
$ sim/tb_simv -l run.log +CYCLES=3
```

- `-f file` and `-file file`: the paths inside are relative to the current directory. `-F file`: the paths
  inside are relative to the file's own directory. Option files can nest, and can hold `//`, `/* */` and
  line-start `#` comments, quoted strings, and `$VAR`, `${VAR}` or `$(VAR)` (an unset variable expands to
  nothing). `--vamos-*` options inside an option file are not read (a warning): give them on the command
  line.
- `vcs -l file` logs the compile, and with `-R` the run is appended to the same file. `./simv -l file` logs
  the run. A log gets everything vamos prints, including its notes and warnings.
- Compile-time `+plusargs` that vamos does not know are passed to the `-R` run, as VCS does:
  `vcs -sverilog tb.sv rtl/counter.v -R +CYCLES=2 +verbose`. Without `-R` each gets a note (it has no
  effect; give it to `./simv`). `+plusarg_save` is not supported (a warning).
- `+incdir+<dir>`, `+define+<name>[=<value>]` and `` `include `` work as in VCS. A `-v <file>` supplies only
  the modules the sources do not define (VCS's library rule); when several `-v` files define one, the first
  wins. A module that only `-v` files define is never a top-level module.

What a compile leaves behind (the names follow `-o`):

| path | contents |
|---|---|
| `simv` | the run script |
| `simv.daidir/vamos.job.json` | what vamos understood from the command line, including every option it did not act on (`"unmapped"`); with several top-level modules, `"tops"` starts with `vamos_tops`, followed by the modules |
| `simv.daidir/vamos.tools.json` | the tools, versions and paths used |
| `simv.daidir/vamos.srclines.json` | the preprocessor's line map, through which the run's messages name your file and line |
| `simv.daidir/pp/pp.v` | the preprocessed sources, with the `-v` rule applied: what gets translated |
| `simv.daidir/nvc/` | the translation: `design.vhd`, `iverilog.log` (translator messages), the nvc `work` library; with several top-level modules, `vamos_tops.vhd`, the entity that instantiates them; `_mods_design.vhd` and `_mods_analysis.log` when nvc could not analyse the module-by-module translation (§9) |

Behaviour to know:

- **Exit status:** 0 after `$finish` or `$stop`, and also after `$error` (as VCS does without
  `-exitstatus`). Non-zero after `$fatal` or a runtime error.
- **Time units:** give every file a `` `timescale ``, or use `-timescale=1ns/1ps` (applies to files without
  one) or `-override_timescale=1ns/1ps` (rewrites every file). Every precision of 1 ms or finer runs at its
  true size, 1ns/10ps, 1ns/100ps and 10ns/10ns included. A coarser precision is compressed to 1 ms per
  tick, so with no time unit at all delays count in 1 ms units where VCS uses 1 s: a `#40` run ends at
  `Time: 40ms`.
- **`./simv +vcs+finish+<time>`** stops the run at that time. The forms are N units of the design's time
  precision (with 1ns/1ps, `+vcs+finish+22000` stops at 22 ns), a time with a unit (`+vcs+finish+22ns`,
  `+vcs+finish+9001us`), VCS's `<low>+<high>` form for times of 2^32 units and more, and `0`. N needs a
  recorded precision (a `` `timescale `` or `-timescale`). The footer's `Time:` is the time the run ended at:
  the `$finish`, `$stop` or fatal time; the stop time when events remained (the quick start prints nothing
  before 56 ns, so `./simv +vcs+finish+22000` prints only the footer, with `Time: 22ns`); or the last event
  when the run ran out of events first (a design whose last event is at 10 ns, run with
  `./simv +vcs+finish+100000` or with no bound, shows `Time: 10ns`).
- **Top-level modules:** as in VCS: the `-top` modules (`-top a -top b`, or `-top a+b+`), else every module
  nothing instantiates. `Top Level Modules:` lists them. Several tops run together under a generated entity,
  `vamos_tops`; `%m` and hierarchical names print as in VCS, and an undriven top-level input reads z (with a
  single top it reads 0, with a warning, §9). A `-top` that names no module is an error: `vamos: error: -top
  alhpa: no module alhpa in the Verilog sources (did you mean alpha?); the top-level modules are: alpha,
  beta`.
- **Preprocessing:** the sources are preprocessed first (`iverilog -E`); a preprocessing error is a compile
  error.
- **Output:** `$display` output comes out as written. `%t` without `$timeformat` prints in the finest
  precision of the whole design, as VCS and vvp do. Bare arguments print as vvp prints them: `$time` and
  `$simtime` right-aligned in 20 columns, `$stime` in 10, `$realtime` with the scope's precision digits, a
  real with 6 significant digits (`2.50000`). Messages with a location (`$info`, `$warning`, `$error`,
  `$fatal`, and the file tasks' warnings below) name your file and line: the compile writes the
  preprocessor's line map, `simv.daidir/vamos.srclines.json`, and the run rewrites each location in vamos's
  translated copy through it. nvc's `FINISH called` takes the place of VCS's `$finish` lines (`$finish(0)`
  and `$stop(0)` print none, as under VCS and vvp), and the run footer is branded VAMOS (§8).
- **Scheduling:** as in Verilog, a process runs from one wait to the next without yielding: a blocking
  assignment updates the variable at once, and other processes, VHDL modules and nets computed from it see
  the new value when the process waits. An `always @(...)` waits for its first event, and the assignments an
  `initial` block makes at time 0 are events to it. A declaration initializer (`reg a = 0;`) is no event, as
  in SystemVerilog (IEEE 1800 6.8): vamos translates every source under SystemVerilog's rules, also a `.v`
  file, where IEEE 1364 (and vvp's default) make it an assignment at time 0 that an `always @(a)` sees.
- **File I/O** runs as under vvp: `$fopen` (and `$fopenr`/`$fopenw`/`$fopena`), `$fdisplay`, `$fwrite`,
  `$fstrobe`, `$fmonitor` and their `b`/`h`/`o` forms, `$fflush`, `$fclose`, `$readmemh`, `$readmemb`,
  `$writememh` and `$writememb`. They use vvp's descriptors (`$fopen(name)` returns an MCD, bit 0 is stdout;
  `$fopen(name, mode)` an FD, 32'h8000_0003 first), write the same bytes and print vvp's warnings and errors,
  which name your file and line (`WARNING: tb.v:10: invalid file descriptor (0x80000003) given to
  $fdisplay().`, `... $readmemh(m.hex): Not enough words in the file for the requested range [0:3].`). A
  relative file name is found in the directory you start `./simv` from, as under VCS, in an AMS run too.
  `$fstrobe` writes at the end of the time step; every `$fmonitor` call starts its own monitor, which writes
  when a value it shows changes (for a memory word, that word only) until an `$fclose` of its descriptor
  (`$monitoroff` does not stop it). Differences from vvp: text to FD 2 (stderr) comes out with simv's other
  output; files are text, so `b` changes nothing, `w+`/`a+` write as `w`/`a`, and `r+` is refused with a
  warning (`$fopen` returns 0); lines that several `$fstrobe`/`$fmonitor` statements write in one time step
  come statement by statement, where vvp keeps call order (Verilog leaves that order open); a `$write` line
  still pending when the run runs out of events without `$finish` is lost. The reading tasks (`$fgets`,
  `$fgetc`, `$ungetc`, `$fscanf`, `$sscanf`, `$fread`, `$fseek`, `$ftell`, `$rewind`, `$feof`, `$ferror`) are
  not translated (a compile error, below); `$readmempath`, a `$readmem*`/`$writemem*` of a memory in another
  module or of a real or multi-dimensional memory, and `$fstrobe`/`$fmonitor` inside a function are dropped
  with a warning.
- **Waves:** `$dumpfile` and `$dumpvars` write a VCD. Add the two lines `$dumpfile("counter.vcd");` and
  `$dumpvars(0, tb);` at the start of the quick start's `initial` block and compile again: the compile says
  what the run will write, and `./simv` writes it.

  ```
  $ vcs -sverilog tb.sv rtl/counter.v -R
  ...
  vamos: note: tb.sv:11: $dumpvars: ./simv writes a VCD of tb (every level) to counter.vcd, from time 0 for the whole run
  ...
  $ grep -e scope -e '$var' counter.vcd
  $scope module tb $end
  $var wire 1 ! clk $end
  $var wire 32 " cycles[31:0] $end
  $var wire 4 # q[3:0] $end
  $var wire 1 $ rst $end
  $scope module dut $end
  ...
  ```

  Values are four-state (0, 1, x, z) and modules are module scopes; the translator's own nets are left out.
  `$dumpvars(levels, scope...)` is honoured (instance or variable names; a name relative to a module applies
  to each of its instances). The VCD covers the whole run from time 0: `$dumpoff`, `$dumpon`, `$dumpall`,
  `$dumpflush` and `$dumplimit` get a note saying so, and `$dumpports` and its family are not translated (a
  warning). A call under `if ($test$plusargs("x"))` (or its `else`, or `!`), or `if ($value$plusargs("x=%s",
  f)) $dumpfile(f)`, counts only when ./simv's plusargs pass the test; under any other condition (an `if`, a
  `case`, a loop, a task) the call gets a note and counts as made. The file is the design's `$dumpfile`
  name, else `./simv +vcs+dumpfile+<file>` or `-vcd <file>`, else `verilog.dump` (VCS's default), relative to
  where ./simv runs; one that cannot be written is a warning (`vamos: warning: the run writes no waves:
  <path> cannot be written (the directory is not writable)`), and the run goes on without waves.
  `+vcs+dumparrays` adds memories (without it a note says they are left out, as under VCS). The compile-time
  `vcs +vcs+dumpvars` dumps the whole design, in `vcs-ams` too; beside a `$dumpvars` in the source, the note
  labels the option's part (`... writes a VCD of the whole design (+vcs+dumpvars); tb.dut (1 level) when
  ./simv gets +waves to ...`). `$dumpfile`/`$dumpvars` in the source work in `vcs-ams` as in a plain compile;
  an analog output that no digital code reads keeps its A2D when the waves record it, so the VCD shows its
  digital value, not z. Names follow the translation: a VHDL reserved word gets `_sig` (`bus` is
  `bus_sig`), an `output reg q` also shows its `q_reg`, an `integer` is a 32-bit vector, generate blocks are
  flattened into their module, and several tops are under `vamos_tops.top1`, `top2`, .... The VCD's time
  unit is 1 fs.
- **Random numbers:** `$random`, `$random(seed)`, `$urandom`, `$urandom(seed)` and `$urandom_range` draw the
  same numbers as vvp. An unseeded `$random` uses one seed for the whole design, and `$urandom` and
  `$urandom_range` a second one, as vvp keeps them; a seeded call draws from its seed variable and advances
  it. Two shapes are approximate, each with a warning at your file:line (`vamos: warning: <file>:<line>:
  $random(seed) is not translated faithfully: ...`; an error with `--vamos-strict`): a seeded call in a `?:`
  branch or an `&&`/`||` operand always draws, and a read of the seed earlier in the same statement sees the
  advanced seed. Give the call a statement of its own to avoid both: `if (c) a = $random(seed); else a = 0;`
  for `a = c ? $random(seed) : 0;`.
- **Not translated:** a task the translation drops gets a warning at your file:line (`vamos: warning:
  tb.v:5: system task $dumpports is not translated: the simulation drops it`); `--vamos-strict` makes it an
  error, and an AMS compile refuses it.
- **A top sv2ghdl cannot translate** stops the compile with exit 1: `vamos: error: sv2ghdl could not
  translate top module 'tb': VHDL conversion error: No translation for system function $sformatf (see
  <daidir>/nvc/iverilog.log)`. For a module that is a top only because nothing instantiates it, the message
  adds "give -top to choose the tops".

## 4. Options vamos does not implement

No VCS option is a usage error. Every option gets one of these dispositions:

| disposition | what you see | examples |
|---|---|---|
| mapped | nothing; the option takes effect | `-o -R -l -top +incdir+ +define+ -v -sverilog -timescale -override_timescale -f -F -ad +vcs+dumpvars` |
| ignored | nothing; it has no meaning here | `-full64 -lca -j8 -licqueue +vcs+lic+wait -q +lint=... -debug_region...` |
| noted | `vamos: note: <option>: <why>` | `-kdb -debug_access+all -gui -verdi -y <dir> +notimingcheck` |
| unsupported | `vamos: warning: <option> is not supported yet (<why>)` | `-cm -xprop -pvalue+ -P -load -CFLAGS -ntb_opts uvm`, VHDL, C and `.va` sources |
| unknown | `vamos: warning: unknown option <option> ignored` | anything not in the table |
| inapplicable | `vamos: warning: <option> has no effect: <why>` | a `--vamos-*` option given where it does nothing (§7) |

```
$ vcs -full64 -sverilog -debug_access+all -kdb -lca -cm line+tgl -y libdir +libext+.v -bogus_opt \
      +my_compile_plus tb.sv rtl/counter.v -o opt_simv
...
vamos: note: -debug_access+all: no debug database or VPD/FSDB waves are produced; $dumpvars writes a VCD
vamos: note: -kdb: Verdi KDB is not produced
vamos: warning: -cm line+tgl is not supported yet (coverage mapping onto nvc --cover is planned)
vamos: note: -y libdir: library directories are recorded but modules are not yet pulled from them; list the files or use -v
vamos: warning: unknown option -bogus_opt ignored
vamos: note: +my_compile_plus: a plusarg given to vcs reaches only a -R run, as under VCS; give it to ./simv
Top Level Modules:
       tb
...
```

`--vamos-strict` turns unsupported, unknown and ineffective options into errors (exit 1), and every warning
of a compile, digital or AMS, too. Notes and ignored options do not count:

```
$ vcs -full64 -sverilog -kdb -cm line -bogus_opt tb.sv rtl/counter.v -o strict_simv --vamos-strict
...
vamos: error: --vamos-strict: 2 unsupported/unknown option(s): -cm line -bogus_opt
```

`./simv` works the same way. Every `+plusarg` goes to the simulation. `-l <file>`, `+vcs+finish+<time>`,
`+vcs+dumpfile+<file>`, `-vcd <file>` and `+vcs+dumparrays` are mapped (§3); `./simv -h` lists the runtime
options and exits without simulating. `+ntb_random_seed=<n>` is passed on as a plusarg and seeds nothing
else. `+vcs+lic+wait`, `-licqueue`, `-q` and `-k <file>` are ignored. `+vcs+dumpon+...`, `+vcs+dumpoff+...`,
`+vcs+flush+dump`, `-ad_runopt=`, `-gui`, `-verdi`, `+fsdbfile+`, `+vpdfile+`, `-assert` and
`+notimingcheck` are noted, each with its reason. `-ucli`, `-do`, `+vcs+stop+` and `-cm` are unsupported,
and any other `-option` is unknown. In every compile, `--vamos-strict` also turns every warning (an
approximation vamos had to make, such as an untranslated system task) into an error. The compile-time `-gui`
note says there is no GUI (`$dumpvars writes a VCD for a wave viewer`).

The full tables are `OPTIONS` in `vamos/personalities/vcs.py` and `vamos/personalities/simv.py`.

## 5. Quick start: AMS

A Verilog clock drives an RC cell written in SPICE. The cell's output comes back into a SystemVerilog
`logic`. Make three files:

```verilog
// tb.sv
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  logic seen;
  always #5 clk = ~clk;
  rc_cell u1 (.in(clk), .out(seen));     // rc_cell is a SPICE subckt
  always @(seen) $display("%t seen=%b", $realtime, seen);
  initial #30 $finish;
endmodule
```

```spice
* rc.sp: RC cell; the capacitor node is buffered to the output
.subckt rc_cell in out
r1 in mid 500
c1 mid 0 0.5p
e1 out 0 mid 0 1
.ends
vsup sup 0 1.8
rsup sup 0 1meg
.tran 1p 100n
.end
```

```
// vcsAD.init
choose xa rc.sp;
```

There is no `module rc_cell` in the Verilog. A cell with only a SPICE view binds to its subckt
automatically, and its port directions come from how the Verilog connects it (`port_dir` can declare
them).

```
$ vcs-ams -sverilog tb.sv

VAMOS compile (vcs personality), vamos 0.1.0

vamos 0.1.0 (vcs personality) - tools used:
  vamos      git-<commit>         GPL-3.0-or-later           /usr/local/src/sv2ghdl/bin/vamos
  sv2ghdl    git-<commit>         GPL-3.0-or-later           /usr/local/src/sv2ghdl/bin/iverilog-sv2ghdl
  iverilog   13.0                 GPL-2.0-or-later           /usr/local/src/iverilog/_install/bin/iverilog
  nvc        1.19-devel           GPL-3.0-or-later           /usr/local/src/nvc-build/bin/nvc
  VACASK     0.3.4-91-g64489cf7   AGPL-3.0-only              /opt/build.VACASK/Release/simulator/vacask
  OpenVAF-r  20260616-3-g0e83f1ed GPL-3.0-only               /opt/openvaf-r-20260616/openvaf-r
vamos: note: /work/ams/rc.sp:9: .tran: maximum time step 5e-12 s, HSPICE's bound without .option delmax: min(TSTOP/50, RMAX*TSTEP) = min(2e-09, 5*1e-12) with RMAX=5, its default under DVDT=4 and LVLTIM=1; .option delmax or --vamos-analog-maxstep sets another
vamos: AMS: 1 SPICE instance(s), 2 analog node(s), 3 bridge(s); vacask deck ams/deck/vamos.sim
Top Level Modules:
       tb
Vamos: ./simv is up to date
CPU time: 0.320 seconds to compile + 0.920 seconds to elab
$ ./simv
vamos 0.1.0 (simv personality) - tools used:
...
                   0 seen=x
                   0 seen=0
                5356 seen=1
               10354 seen=0
               15354 seen=1
               20354 seen=0
               25354 seen=1
FINISH called
** Note: co-simulation finished: digital stop at 3e-08 s
           V A M O S   S i m u l a t i o n   R e p o r t 
Time: 30ns
CPU Time:      2.290 seconds;
```

`%t` prints `$realtime` in the design's precision, 1 ps here, so `5356` is 5.356 ns (as in VCS and vvp,
and at any simulation time). `seen` follows each clock edge by about 0.35 ns: the RC (500 Ω, 0.5 pF)
charges through the D2A's 500.7 Ω series resistance up to the A2D threshold at 0.9 V. The `seen=x` line at
time 0 is the co-simulation's start: until the analog side has its operating point, the A2D drives x, and
`always @(seen)` sees that change too, before `seen` settles to 0.

The same compile, spelled other ways: `vcs -sverilog tb.sv -ad`, `vcs ... -ad=<file>`, `vcs ... +ad`,
`vcs ... +ad=<file>`, `vamos -vcs-ams ...`. Add `-R` to run straight away.

**Choosing the engine.** VACASK is the default. To use Xyce, pass `--vamos-analog=xyce`, set
`VAMOS_ANALOG=xyce`, or write `choose xyce ...` in the control file, checked in that order. With Xyce the
example above gives the same output to within a few ps (`10353` and `20351` where VACASK gives `10354` and
`20354`). `--vamos-analog` needs a value (`vacask` or `xyce`; anything else is an error). The engine is
fixed at compile time: given to `./simv`, `--vamos-analog` has no effect (a warning), so compile again to
switch.

**How a run ends.** A run that reaches the analog engine prints exactly one end line. A normal end is a
`** Note: co-simulation finished: ...` line:

- `digital stop at <t> s`: `$finish`, `$stop` or `$fatal` (a `$fatal` run still exits non-zero).
- `analog end at <t> s`: the `.tran` stop time, or `+vcs+finish+<time>`.
- `digital stop at 0 s (before the first analog step)`: the testbench stopped at time 0.

A failed run ends with `** Error: <engine> transient failed at <t> s`, `** Error: co-simulation stalled at
<t> s` or `** Error: co-simulation interrupted at <t> s` instead, followed by a `vamos:` line that names the
cause. A netlist with no `.tran` runs until `$finish`/`$stop` or 3600 s (`--vamos-analog-stop`), and vamos
says so in a note. A run that succeeds exits 0. A failed run exits non-zero, publishes no rawfile, and keeps
its run directory.

Everything your testbench prints reaches the screen and the `-l` log unchanged. vamos filters only the
engines' own output, and judges the run only from nvc's, the bridge's and the engines' lines, never from
your messages.

**Stopping a run.** Ctrl-C (or SIGTERM or SIGHUP) stops the co-simulation the way `$finish` would: the
engine finishes its output, nvc prints `** Error: co-simulation interrupted at <t> s`, and simv prints
`vamos: note: co-simulation interrupted (SIGINT) at <t> s; run directory kept (partial waves): <dir>` and the
footer, publishes nothing, and exits as an interrupted command (status 130 for Ctrl-C). A second Ctrl-C
kills nvc at once. Ctrl-Z suspends the whole run. With the example's `#30` made `#2000000` and its `.tran`
`1p 3m`, a Ctrl-C after 15 s gives:

```
$ ./simv
...
** Error: co-simulation interrupted at 1.9811004988e-05 s
vamos: note: co-simulation interrupted (SIGINT) at 1.9811004988e-05 s; run directory kept (partial waves): /work/ams/vamos_ams.run.6ldgloh4
           V A M O S   S i m u l a t i o n   R e p o r t 
Time: 19811004988fs
CPU Time:     16.570 seconds;
$ echo $?
130
```

A run started in the background of a script (`./simv &`) has SIGINT ignored, as the shell sets it up, so
stop such a run with SIGTERM (`kill <pid>`): the same note says `(SIGTERM)`, and the status is 143.

**Outputs:**

| path | contents |
|---|---|
| `vamos_ams.raw` | the analog waveforms as a SPICE rawfile, written in the directory where `./simv` runs. `choose ... -o <prefix>` names it `<prefix>.raw` (relative paths allowed) |
| `simv.msv/interface_element.rpt` | the interface-element (IE) report, below |
| `simv.daidir/ams/deck/vamos.sim` (VACASK) or `vamos.cir` (Xyce) | the flattened deck that runs: your netlist plus the bridges. Node names in the rawfile are the ones in this deck |
| `simv.daidir/ams/` | also `pp.v` (the preprocessed Verilog), `cut.vhd`, `vamos.boundary` (the bridge list), `ams.json` (the plan) |
| `<prefix>.run.<random>/` | the per-run directory, where the engines run. It is removed after a successful run unless you gave `--vamos-keep` or the run left other files in it |

Each run in a directory overwrites that directory's `<prefix>.raw`. Runs started together in one directory
all finish, and the last to finish leaves its `<prefix>.raw`. To keep the results of several runs, use
separate directories or different prefixes.

The IE report has one entry per analog node, written in control-file syntax (UTF-8 whatever your locale).
You can paste a line back into `vcsAD.init` to select the same node and change its levels. Pasting all its
`d2a`, `a2d` and `map_by_node` lines in place of your control file's rules reproduces every node exactly,
supply nets included: a `supply1`/`supply0` net gets a `d2a powernet hiv=... lov=...` line before its
`// node=...: supply net` comment.

```
$ cat simv.msv/interface_element.rpt
// vamos interface-element report (vacask). Entries are control-file syntax: a line
// pasted into vcsAD.init selects the same node.

d2a hiv=1.8 lov=0.0 rf_time=1e-11 x2v=0 node=tb.u1.in;
// Top-Net tb.clk
// All Boundary Nets tb.u1.in
// host tb.u1.in
// direction: auto→input (variable actual) tb.u1.in
// levels: reference highest deck source vsup
// shunt rsh_n_u1_in 1e12 ohm to ground

a2d loth=0.9 hith=0.9 node=tb.u1.out;
...
```

**Reading the rawfile.** Any SPICE rawfile reader can open `vamos_ams.raw`. With vamos's own reader:

```
$ PYTHONPATH=$SV2GHDL python3 -c 'from vamos.netlist import rawfile; r = rawfile.read("vamos_ams.raw"); print(r.names()); print(r.at("n_u1_out", 20e-9)); print(r.crossings("n_u1_out", 0.9, +1))'
['time', 'n_u1_in', 'n_u1_in_d', 'n_u1_in_e', 'n_u1_out', 'sup', 'vd_n_u1_in:flow(br)', 've_n_u1_in:flow(br)', 'vsup:flow(br)', 'xv_u1:e1:flow(br)', 'xv_u1:mid']
1.7999169826191361
[5.351518980484005e-09, 1.5351235986790824e-08, 2.5351235986790226e-08]
```

`r.at(name, t)` interpolates the value at time `t`; `r.column(name)` returns the whole column as a list
(`v(a,b)` gives the difference of two nodes); `r.crossings(name, level, direction=0)` returns the times at
which the column crosses `level` (`+1` rising only, `-1` falling only, `0` both). Interface nodes are named
`n_<instance path without the top>_<port>` (`n_u1_out`). Nodes inside a cell are under its `xv_<path>`
instance (`xv_u1:mid` on VACASK). The reader accepts either engine's spelling of a name. The rawfile holds
the netlist's `.print`/`.probe` tran probes plus the interface nodes; a netlist with neither gets every node
(as above).

With ngspice, load the file in a `.control` block. Put this in `read.sp`:

```spice
* read a vamos rawfile
.control
load vamos_ams.raw
meas tran tr when v(n_u1_out)=0.9 rise=1
quit
.endc
.end
```

Run it without `-b` (in batch mode ngspice reports an empty netlist and exits 1):

```
$ ngspice read.sp
...
tr                  =  5.351519e-09
```

## 6. The control file in brief

vamos reads `vcsAD.init` from the current directory, or the file named by `-ad=<file>`. Before that it reads
`snps_vcsAD.ini` (from the current directory, else `$HOME`), and a command read later wins; when it reads
both, the compile prints the order in a `[MSV-MC-FRO]` note. Statements end with `;`. `//` and `/* */`
comments are allowed. Here is one control file that uses the common commands; it was compiled and run on
both engines:

```
choose xa spice/cells.sp -o waves/run1;
bus_format <%d>;
use_spice -cell buf_cell;
port_dir -cell dac2 (input code; output out);
`include "common.init"
d2a inst=tb.u2 port=code hiv=1.2 lov=0;
a2d node=tb.u1.y loth=0.6 hith=1.2;
netlist_commands_begin
.temp 85
netlist_commands_end
```

Here `common.init` holds `d2a node=* rf_time=50p;`. `buf_cell` has a Verilog module and a SPICE subckt, and
`use_spice` selects the SPICE one. `dac2` exists only in SPICE, with bus pins `code<1> code<0>`.

<details><summary>A design that this control file fits, to try it</summary>

In an empty directory, run `mkdir spice`, put the control file above in `vcsAD.init` and
`d2a node=* rf_time=50p;` in `common.init`, and make these three files:

```verilog
// buf_cell.v
`timescale 1ns/1ps
module buf_cell (input a, output y);
  assign y = a;
endmodule
```

```verilog
// tb.sv
`timescale 1ns/1ps
module tb;
  reg clk = 0;
  reg [1:0] code = 2'b00;
  logic y, out;
  always #5 clk = ~clk;
  buf_cell u1 (.a(clk), .y(y));
  dac2     u2 (.code(code), .out(out));
  always @(y)   $display("%t y=%b", $realtime, y);
  always @(out) $display("%t out=%b", $realtime, out);
  initial begin
    #12 code = 2'b01;
    #10 code = 2'b10;
    #10 code = 2'b11;
    #10 $finish;
  end
endmodule
```

```spice
* spice/cells.sp
.subckt buf_cell a y
r1 a m 200
c1 m 0 0.2p
e1 y 0 m 0 1.05
.ends
.subckt dac2 code<1> code<0> out
r1 code<1> out 1k
r0 code<0> out 2k
rl out 0 1k
.ends
vdd vdd 0 1.2
rdd vdd 0 1meg
.tran 1p 50n
.end
```

```
$ vcs -sverilog tb.sv buf_cell.v -ad
...
vamos: AMS: 2 SPICE instance(s), 5 analog node(s), 8 bridge(s); vacask deck ams/deck/vamos.sim
...
$ ./simv
...
                5455 y=1
               10130 y=0
...
               32046 out=1
               35455 y=1
               40130 y=0
FINISH called
** Note: co-simulation finished: digital stop at 4.2e-08 s
...
```

The waves are in `waves/run1.raw`, and the IE report shows the levels each rule set. `out` reads 1 only for
code 3: `n_u2_out` reaches 0.23, 0.39 and 0.62 V for codes 1, 2 and 3, and its A2D threshold is 0.6 V (half
the 1.2 V supply).

</details>

| command | what it does |
|---|---|
| `choose <engine> <netlist>... [-o <prefix>] [-skipdc] [-c <xa.cfg>];` | Required. Names the SPICE netlists (a path, or `-n`/`-hspice <file>`). `xa`, `finesim`, `primesim`, `hsim` and `nanosim` all mean the default engine; `vacask` or `xyce` choose one. `-o` sets the rawfile prefix. `-skipdc` starts from initial conditions instead of an operating point. A relative netlist path is tried in the current directory, then next to the control file. `-c <xa.cfg>` is read for `probe_waveform_voltage`/`_current` and `set_sim_case`; `$VAR`, `${VAR}` and `~` are expanded in its path. |
| `use_spice -cell <c>[:<subckt>] [-inst <path>...];` | Simulates cell `c` as SPICE although it has a Verilog view. `c:s` binds it to subckt `s`. A cell with no Verilog view needs no command. `partition -cell` does the same. |
| `bus_format <fmt>;` | How SPICE names bus bits: `[%d]` (the default), `<%d>` or `_%d`. |
| `port_dir -cell <c> (input a; output y; inout z);` | Directions of a SPICE-only cell's ports. Without it, vamos infers each direction from the Verilog connection, and a port it cannot decide becomes `inout` (VCS's default; `port_dir` is faster). |
| `port_connect -cell <c> [-inst <path>] (p => net, q => snps_open);` | Wires a SPICE port to a deck net or leaves it open, instead of connecting it to Verilog. With `-inst`, only that instance (an `-inst` statement beats a cell-level one). `net` is a top-level or `.global` net of the netlist (`tb.<net>` works too), ground, or `<instance>.<port>` of a SPICE port a Verilog net connects; anything else (a typo, a node inside a SPICE instance, a Verilog-only net, `real`) is an error. Leave the port out of the Verilog instance: naming it there, even as `.p()`, is a compile error. |
| `d2a <keys> <selector>;` | Digital-to-analog levels: `hiv lov rf_time rise_time fall_time delay rise_delay fall_delay x2v powernet`. With `vdd=`/`vss=` (or `vdd_port=`/`vss_port=`, a port of the matched cell) the levels must be percentages of that supply, as in VCS. `vss=0`, `vss=gnd` (ground) and a `vss_port=` on a port connected to ground name the 0 V supply. A `vdd_port=`/`vss_port=` with `*` (`vdd_port=vdd*`) is matched against the cell's SPICE ports, as VCS does, and must match exactly one. |
| `a2d <keys> <selector>;` | Analog-to-digital thresholds: `loth hith xband midv_time midv_logic` (`midv_logic=0\|1\|X\|Z`; Z releases the net after `midv_time` in the window). |
| `map_by_node r=<ohms> node=<n>;` | The D2A series resistance at the matching D2A or bidirectional nodes, for every drive strength (default 500.7 Ω). It has no effect on supply nets or `d2a powernet` nodes (a warning). |
| selectors | `node=<name>` (with `*` it matches the report's node names; without `*` it also matches Verilog net names such as `tb.clk`), `inst=<path> port=<p>`, or `cell=<c> port=<p>`. Rules apply in file order, and a later rule overrides an earlier one key by key. |
| `ie_reference_voltage node=<n> [voltage=<v>];` | The reference supply (for default and `%` levels) of every node whose supply trace reaches `<n>`, whether or not the trace would have found a source. The last command on a net wins; a command no trace reaches is a warning. |
| `remove_d2a [dc=<v>] node=<n>;` / `disable_ie node=<n>;` | No D2A on the node (optionally an ideal DC source instead) / no interface element at all. |
| `netlist_commands_begin` ... `netlist_commands_end` | Raw SPICE lines added after the netlists (`.temp`, `.option`, `.param`, ...). |
| `` `include "file" `` | Reads another control file in place. A relative name resolves from the directory `vcs` runs in. |
| `downgrade_to_warn MSV-IE-OPT-TNF;` | Makes "target not found" a warning (§9). |

Without rules, the defaults are VCS's: `hiv` = the supply, `lov` = 0, 10 ps ramps, a 500.7 Ω series
resistance (`map_by_node` changes it), and A2D thresholds at half the supply. vamos finds the supply by
tracing the cell's devices to a voltage source, or else takes the highest constant source in the deck. If
neither exists it falls back to VCS's 3.3 V, with a warning. A supply source vamos cannot evaluate (one
using `temper`) is an error wherever a default level would need it: give the levels with a rule, or name
the supply with `ie_reference_voltage ... voltage=`.

Every other command is accepted with an error, a warning or a note, as listed in docs/VAMOS_AMS_DESIGN.md
§2.2. All the keys are covered in §3, and the IE report format in §3.6.

## 7. vamos options and environment variables

vamos's own options all start with `--vamos-`, so they can never collide with a vendor option. They can go
anywhere on the line, and vamos removes them before the personality reads its arguments.

vamos owns this namespace. An unknown option (`--vamos-analgo=xyce`), a missing value (`--vamos-analog`), a
value given to a flag (`--vamos-strict=0`) or a value outside its choices (`--vamos-parhier=lcoal`) is an
error (exit 2); for an unknown option the message names the nearest one (`unknown vamos option
--vamos-analgo=xyce (did you mean --vamos-analog?)`). An option that has no effect where it is given is a warning,
`vamos: warning: <option> has no effect: <why>`, and `--vamos-strict` makes it an error. Examples: an AMS
option in a digital compile, `--vamos-keep` in a compile without `-R`, a compile-time option given to
`./simv`, `--vamos-analog-stop` beside a `.tran`.

| option | where | effect |
|---|---|---|
| `--vamos-banner=<name\|path\|none>` | all | banner profile (§8); `none` prints only the provenance header |
| `--vamos-strict` | all | unsupported, unknown and ineffective options are errors; so is every warning of a compile |
| `--vamos-verbose` | all | prints each command vamos runs (`vamos: + ...`) |
| `--vamos-licenses` | - | prints the tool/licence table and exits |
| `--vamos-version` | - | prints the version and exits |
| `--vamos-analog=vacask\|xyce` | AMS compile | the analog engine |
| `--vamos-analog-stop=<time>` | AMS compile | end time for a netlist with no `.tran` (default 3600 s; above 9000 s it is clamped); beside a `.tran` it has no effect (a warning) |
| `--vamos-analog-maxstep=<time>` | AMS compile | analog maximum time step; replaces the `.tran`'s (default with no `.tran`: `.option delmax`, else 10 ns); the compile notes the value used |
| `--vamos-no-deck-check` | AMS compile | skips the compile-time check, in which the engine computes an operating point of the deck |
| `--vamos-parhier=local\|global` | AMS compile | `local`: a parameter defined both at top level and in a subckt takes the inner value; `global`, the default: such a collision is an error |
| `--vamos-keep` | AMS `./simv`, or `vcs -R` | keeps the per-run directory |
| `--vamos-append-log` | `simv` | with `-l`, appends to the log (`vcs -R` uses it) |
| `--vamos-daidir=<dir>` | `simv` | used by the generated `./simv`; not for direct use |

Times are SPICE numbers: `20n`, `50p`, `1e-6`.

| variable | effect |
|---|---|
| `VAMOS_NVC`, `VAMOS_IVERILOG` | the real nvc / iverilog. In general, `VAMOS_<TOOL>` names any tool vamos looks up. Checked: a path that is not an executable file, or is vamos itself, is an error (exit 1); a bare name is looked up on PATH |
| `VAMOS_ANALOG` | `vacask` or `xyce`; below `--vamos-analog`, above `choose` |
| `VAMOS_BANNER` | banner profile name or path; below `--vamos-banner` |
| `VAMOS_VERBOSE=1` | the same as `--vamos-verbose` |
| `VAMOS_PYTHON` | the Python interpreter for the launcher (default `python3`) |
| `VAMOS_VACASK_HOME` | the VACASK build (default `/opt/build.VACASK/Release`); the C interface is `<home>/cinterface/libvacaskcinterface.so` |
| `VAMOS_VACASK` | the standalone VACASK used for the compile-time check (default `<home>/simulator/vacask`) |
| `VAMOS_VACASK_MODULE_PATH` | VACASK's device modules (default `<home>/lib/vacask/mod`, else `<home>/devices`) |
| `VAMOS_OPENVAF` | openvaf-r (default: the newest `/opt/openvaf-r-*/openvaf-r`, else `<home>/simulator/openvaf-r`) |
| `VAMOS_XYCE` | the standalone Xyce used for the compile-time check (default `/usr/local/src/xyce-build/src/Xyce`) |
| `VAMOS_XYCE_LIBS` | `:`-separated directories holding `libxycecinterface.so` and `libxyce.so` (default `~/xyce-libs`, `/usr/local/src/xyce-build/utils/XyceCInterface`, `/usr/local/src/xyce-build/src`) |
| `NVC_LIBDIR` | nvc's library directory (default: found from the nvc path) |
| `VAMOS_IVERILOG_TIMEOUT` | seconds allowed for each iverilog preprocess or check run (default 3600) |
| `VAMOS_DEV=1` | dev mode: also look in the source build areas for nvc and iverilog (automatic in a source checkout; `VAMOS_DEV=0` does not turn it off) |
| `VAMOS_REDIRECT` | `:`-separated extra directories to remove from the PATH vamos gives to the tools it runs |
| `XDG_CONFIG_HOME` | where the user configuration layer is (default `~/.config`) |
| `COSIM_TRACE=1` | nvc's: prints the co-simulation bindings and every D2A change (useful in §9) |

The engine variables (`VAMOS_VACASK_HOME`, `VAMOS_VACASK`, `VAMOS_VACASK_MODULE_PATH`, `VAMOS_OPENVAF`,
`VAMOS_XYCE`, `VAMOS_XYCE_LIBS`) are used as given when set, never replaced by the default: a value that is
not an executable (a directory for `_HOME`, `_MODULE_PATH` and each `_LIBS` entry) stops the AMS compile.

vamos sets `VAMOS_STACK`, `VAMOS_DEPTH`, `VAMOS_LAUNCHER` and `VAMOS_ARGV0` for its own use. Do not set them
yourself. For a run's nvc it also sets `NVC_REPORT_END_TIME` (the footer's time), `SV2VHDL_FILE_DIR` (the
directory `./simv` was started in, where the testbench's relative file names resolve) and, unless you set
them, `NVC_RESOLVER_DIR` and `NVC_WORK` (the resolver plugin's cache and work library, kept in the daidir).

## 8. Banners, provenance and licences

- **Provenance.** Every compile and every run starts with the tools it actually ran, each with its version,
  licence and path ("tools used", see §3). This header is printed whatever the banner profile, and it is
  also saved in `<daidir>/vamos.tools.json`. A version shown as `?` means the tool printed no version (it
  could not run, or printed an error). The columns are as wide as their widest entry.
- **No vendor text.** vamos never prints vendor licence or copyright text. Licence options (`-licqueue`,
  `+vcs+lic+wait`) are accepted and ignored.
- **Banner profiles.** The compile banner, the `Top Level Modules:` summary, the `CPU time` line and the run
  footer come from a JSON profile. The default is `vamos/banners/vcs.json`: VCS's layout, branded vamos
  (`V A M O S   S i m u l a t i o n   R e p o r t`). If a log-scraping script expects another brand or
  layout, copy that file and edit it:

  ```
  $ mkdir -p .vamos/banners
  $ sed 's/"brand": "vamos"/"brand": "acme"/' $SV2GHDL/vamos/banners/vcs.json > .vamos/banners/acme.json
  $ vcs -sverilog tb.sv rtl/counter.v -R --vamos-banner=acme

  ACME compile (vcs personality), acme 0.1.0

  vamos 0.1.0 (vcs personality) - tools used:
  ...
  Acme: ./simv is up to date
  ...
             A C M E   S i m u l a t i o n   R e p o r t 
  Time: 56ns
  ```

  Selection order: `--vamos-banner=<name|path|none>`, then `$VAMOS_BANNER`, then the profile named after the
  personality (`vcs`). A name is looked up as `banners/<name>.json` in each configuration layer, in this
  order: the package; a site layer, `etc/vamos/` in the directory above the checkout (for
  `/usr/local/src/sv2ghdl`, that is `/usr/local/src/etc/vamos/`); `~/.config/vamos/`; `~/.vamos/`;
  `./.vamos/`. The last layer that has the file wins, and it replaces the whole profile, so copy every key,
  not just the ones you change. The keys are
  `brand`, `compile_start`, `compile_tops`, `compile_done`, `compile_failed` and `run_footer`. The
  placeholders are listed in the shipped file's `_comment`; `{exe}` is the executable relative to the current
  directory (`Vamos: ./sim/tb_simv is up to date`). No placeholder exists for licence text.
- **Licences.** `vamos --vamos-licenses` prints `vamos/licenses.json`: each tool's licence, the source the
  stack runs (the kev-cam forks of nvc, iverilog, VACASK and Xyce) and the upstream project each fork comes
  from. A site can override entries in a `licenses.json` in any of the layers above (a later layer wins,
  tool by tool).

## 9. Troubleshooting

Setup and tools:

| message | cause and fix |
|---|---|
| `usage: vamos -<personality> ...` (exit 2) | `vamos` ran without a personality. Use `vamos -vcs ...`, or a link named after the tool. |
| `vamos: cannot find the vamos package near <path>` | `bin/vamos` was copied away from its checkout, or into a prefix without `lib/vamos`. Link it instead, or install it as build_stack.sh does (`bin/vamos` plus the package in `lib/vamos`). |
| `cannot find nvc (set VAMOS_NVC, or put nvc on PATH)` | nvc is not on PATH. Also `cannot find iverilog (set VAMOS_IVERILOG, ...)` and `cannot find iverilog-sv2ghdl next to vamos (<path>)` (an incomplete `bin/`). |
| `no real 'nvc' found (PATH has only vamos shims); set VAMOS_NVC` | the `nvc` personality found nothing to hand the command to: no `VAMOS_NVC`, no nvc on PATH, and, in a source checkout, none at `/usr/local/src/nvc-build/bin/nvc`. |
| `VAMOS_NVC=<p>: no such file (it must name the real nvc)` (also `... is a directory`, `... is not executable`, `... is vamos itself`, `no executable '<p>' on PATH`), exit 1 | the override names no usable tool. Fix or unset it. The same for `VAMOS_IVERILOG`. |
| `VAMOS_OPENVAF=<p> is not an executable` (also `VAMOS_VACASK`, `VAMOS_XYCE`), `VAMOS_VACASK_HOME=<p> is not a directory` (also `VAMOS_VACASK_MODULE_PATH`), `VAMOS_XYCE_LIBS=<v>: <d> is not a directory`, then `AMS compile failed at the analog engine` | an AMS engine override names nothing usable. Fix or unset the variable. |
| `?` as a version in "tools used" | the tool printed no version (it could not run, or printed an error). |
| `VACASK not found at <path> (set VAMOS_VACASK or VAMOS_VACASK_HOME)`, `Xyce not found (set VAMOS_XYCE)`, `cannot find openvaf-r (set VAMOS_OPENVAF)` | an AMS compile cannot find an engine tool at its default location. See §7 for the locations. |

Command line and compile:

| message | cause and fix |
|---|---|
| `unknown vamos option --vamos-<x> (did you mean --vamos-<y>?)`, `<option> needs a value: ...`, `<option> takes no value (...)`, `<option>: the value must be ...` (exit 2) | a `--vamos-*` usage error (§7). |
| `warning: <option> has no effect: <why>` | the `--vamos-*` option does nothing where you gave it (§7). `--vamos-strict` makes it an error. |
| `source file '<path>' cannot be opened`, `no source files given` | check the paths. In `-F` files, paths are relative to the file. |
| `cannot open option file '<f>': ...`, `option file '<f>' includes itself`, `option <x> needs a value` | an option-file or argument problem. |
| `warning: unknown option ...`, `... is not supported yet (...)`, `note: ...` | §4. `--vamos-strict` makes the first two errors. |
| `no banner profile named '<name>'` | §8. |
| `-top <m>: no module <m> in the Verilog sources (did you mean <n>?); the top-level modules are: ...` | a misspelt `-top`. |
| `no top-level module: every module is instantiated, or only in a -v library file; give -top <module>` | name the top with `-top`. |
| `top module <m>: input port <p> (<type>) cannot be left unconnected beside other top-level modules; give -top` | several tops run under one wrapper, which ties each top's inputs to Z or 0; this input type cannot be tied off. Give `-top` to choose the tops. |
| `sv2ghdl could not translate top module '<m>': <iverilog's reason> (see <daidir>/nvc/iverilog.log)`, exit 1 | The translator cannot handle a construct, for example `No translation for system function $sformatf`, a SystemVerilog class (`VHDL conversion error: unsupported construct (class) at <file>:<line>: new() of SystemVerilog class C has no VHDL translation`) or a fork (`unsupported construct (fork) at <file>:<line>`). A class nobody uses does not stop the module. A module above a failing one fails too. A back-end run that printed an error but exited 0 (`Error: …`, `VHDL conversion error: …` or `<file>:<line>: error: …`) counts as failed: it would have left the statement out. Rewrite the construct, or guard it with `` `ifdef ``. When the module is a top only because nothing instantiates it, the message adds a `-top` hint. In AMS mode the message is `sv2ghdl could not translate module <m> (see <daidir>/nvc/iverilog.log)`, also for a module the design does not use. |
| `iverilog-sv2ghdl: note: module <m>, which the design does not use, does not translate on its own (nvc: <message>, <file>:<line>); it is left out` (or `... the module-by-module translation of module <m> does not analyse (...); the whole-design translation, which analyses, was used`) | Information: the module-by-module translation analyses every module, including one that only an unused generate branch instantiates; the compile used the whole-design translation, which does not need it. `simv.daidir/nvc/_mods_analysis.log` has nvc's messages. |
| `... disable <scope> has no VHDL translation: only a block, task or function that encloses the disable statement, in the same process, can be disabled`; `... task <t> calls itself (recursion): tasks are inlined in VHDL, so a recursive task call has no translation`; `... automatic task <t> is entered again before its activation ends: that has no VHDL translation` (in the `could not translate` reason) | `disable` (and SV `return`) works for the named block, task or function that encloses it, and an automatic task called from several processes gives each its own variables; disabling another process's block, `disable fork`, a recursive task and an automatic task entered again before it returns have no translation. |
| `... $dist_uniform in a branch of ?:, an operand of && or \|\| or a loop condition has no VHDL translation` (also `$value$plusargs`), `... $random with a seed outside a procedural statement has no ...`, `... $random's seed variable is less than 32 bits ...` | Verilog may skip or repeat such a call, the translation would make it every time: give the call a statement of its own (`if (c) a = $dist_uniform(seed, 0, 9); else a = 0;`; in a loop condition, draw into a variable before the loop and again at the end of its body). A seed must be an integer, time or reg variable of 32 bits or more, as vvp also requires. |
| `... no VHDL translation for a final block: a VHDL process cannot run when the simulation ends (it would run at time 0)` (in the `could not translate` reason) | A `final` block. Move its statements to the end of the test, before `$finish`. A `final` block that holds only `$fclose`/`$fflush` is left out silently: the end of the run does both. |
| `... no VHDL translation for the void function <f>: write it as a task` (in the same reason) | A SystemVerilog `void` function (it used to crash the translation, an assertion in iverilog's `ivl_signal_data_type`). Write it as a task. |
| `... no VHDL translation for an intra-assignment event control on a nonblocking assignment (...)` (in the same reason) | `x <= @(posedge c) v` or `x <= repeat (n) @(posedge c) v`. Write the event control as a statement (`@(posedge c) x <= v;`), capturing `v` first if it must be the value before the wait. |
| `warning: <file>:<line>: $random(seed) is not translated faithfully: ...` | The two approximate shapes of §3 (a seeded call in a `?:` branch or an `&&`/`\|\|` operand; a read of the seed earlier in the statement). Give the call a statement of its own (§3). `--vamos-strict` makes it an error. |
| `warning: <file>:<line>: specify path delays are not simulated: every module path has zero delay (<n> specify blocks with path delays; VCS applies them unless given +nospecify)`; `... timing checks are not run (<checks>; <n> in the design; VCS runs them unless given +notimingcheck)`; `... $sdf_annotate is not simulated: the SDF file's delays are not applied` | The design's specify blocks and `$sdf_annotate`: vamos simulates without them (specparams keep their values). `+nospecify` says that is intended and silences the first two, `+notimingcheck` the second. `--vamos-strict` makes them errors. |
| `warning: <file>:<line>: top-level module <m> has input port(s) <p> that nothing drives: they read 0 in this simulation, where VCS leaves them undriven (z)` | One top-level module with input ports, which nvc elaborates alone. Drive the inputs from a testbench, or expect 0 where VCS shows z or x. |
| `warning: <file>:<line>: system task $x is not translated: the simulation drops it` | a task the translation leaves out: `$dumpports` and its family, `$readmempath`, and the few file-I/O forms §3 lists. design.vhd marks each place (`grep -n Unsupported simv.daidir/nvc/design.vhd`). `--vamos-strict` makes these errors; an AMS compile refuses them. (The `system function $f is not translated: every call returns 0` form is no longer produced: no system function is replaced by a constant.) |
| `note: <file>:<line>: $dumpvars: ./simv writes a VCD of <scopes> to <file>[ when ./simv gets +<plusarg>], from time 0 for the whole run`; `note: ... $dumpoff is not honoured: the VCD records the whole run` (also `$dumpon`, `$dumpall`, `$dumpflush`, `$dumplimit`); `note: ... the call is inside <condition>, which vamos cannot evaluate before the run: ./simv acts as if it runs` | Information: what the run's VCD will hold (§3, Waves). |
| `vamos: warning: the run writes no waves: <path> cannot be written (<why>)` (from `./simv`) | The VCD's directory is missing or not writable, or the path is a directory: the run goes on without waves, as under VCS. |
| `WARNING:`/`ERROR: <file>:<line>: ...` printed by `./simv` from a file task (`invalid file descriptor (0x80000003) given to $fdisplay().`, `$readmemh: Unable to open m.hex for reading.`, `$readmemh(m.hex): Not enough words in the file for the requested range [0:3].`) | vvp's run-time messages: the descriptor, file or range is wrong, and the run goes on. Relative names are looked up in the directory `./simv` runs in. `<file>:<line>` is your source file and line. |
| `warning: the translation fell back to translating module by module ...` | iverilog-sv2ghdl's last resort, which may use the older translator; read `simv.daidir/nvc/iverilog.log`. |
| `warning: <file>:<line>: <vec>(<sel>) is connected one way only: ...` (also `warning: <file>:<line>: inout port ... is connected one way only ...`) | a `tran`/`tranif` on a select of a module port (or an inout operand on an `in`/`out` port) is joined one way only: what the module drives does not reach the vector. A `tran` on a select of a net of the module is joined both ways. Connect a whole net (`wire w; ... .p(w) ...; assign ...`), or avoid the select. `--vamos-strict` makes it an error. |
| `warning: tri1 net <path>: its pull is not translated (...)` | a `tri1`/`tri0` pull on a net made only of ports (a root port); give it an internal net. `--vamos-strict` makes it an error. |
| `VHDL conversion error: <file>:<line>: an input port connection of instance <path> is not translated: ...` (also `input port <p> of instance <path>: its connection is not translated`) | the compile stops: this port connection shape (a type cast into an input the module also drives) is not translated. Connect through a wire of the port's own type (`wire [3:0] w = r; m u (.a(w));`). |
| `warning: unknown option +vcs+finish+<v> ignored (not a time value: ...)` or `+vcs+finish+<v> is not supported yet (the compile recorded no time precision; recompile)` | use one of the §3 forms, and compile with a `` `timescale `` or `-timescale`. |

AMS compile:

| message | cause and fix |
|---|---|
| ``module tb has no time unit (no `timescale, timeunit); give -timescale or -override_timescale with a precision <= 1ms`` | Every module needs a time unit for AMS. Add `-timescale=1ns/1ps` (for files without one) or `-override_timescale=1ns/1ps` (for all files). |
| `module tb: digital precision 1s is coarser than 1 ms; ...` | The precision must be 1 ms or finer: `-override_timescale=1ns/1ps`. |
| `-top: an AMS design has one top module; -top was given <n> times (...)` / `-top: -top X: no module X in the Verilog sources` | Give one `-top`, spelled as the module. |
| `AMS control file <path>/vcsAD.init not found` | Write a `vcsAD.init`, or give `-ad=<file>`. |
| `<file>: the mixed-signal control file must contain at least choose` / `<file>:<line>: unknown command '<x>'` | Fix the control file (§6). |
| `<file>:<line>: [MSV-IE-OPT-TNF] Option Target Not Found: "d2a" command, option target "node=tb.u1.inp" was not found; the command is ignored (nearest interface-element names: tb.u1.in, tb.u1.out)` | A selector matches nothing. Use one of the names it suggests or a name from the IE report, or add `downgrade_to_warn MSV-IE-OPT-TNF;` to make it a warning. |
| `<file>:<line>: d2a: hiv=1.2 cannot be an absolute level in a rule with vdd=: the levels are percentages of that supply (PAMS p190, p207)` | With `vdd=`/`vss=`/`vdd_port=`/`vss_port=`, write the levels as percentages (`hiv=100%`), or drop the supply key. |
| `<file>:<line>: a2d: vdd_port=v* matches 2 ports of tb.u1 (vdd, vss); name one` / `... vdd_port=../vdd*: a wildcard after ../ is not supported; ...` | A wildcard `vdd_port=`/`vss_port=` is matched against the matched instance's subckt ports, as VCS does (PAMS p194): it must match exactly one (none is [MSV-IE-OPT-TNF]). Name the port, or give the supply net with `vdd=`. |
| `<file>:<line>: choose: XA cfg <f>: environment variable <V> is not set` | Set the variable, or write the path out. |
| `--vamos-analog=<x>: the analog engine must be vacask or xyce` | Fix the option or `VAMOS_ANALOG`. |
| `<file>:<line>: system task $dumpports is not translated (it would be dropped from the simulation)` | AMS refuses what the translator drops: `$dumpports` and its family, `$readmempath` and the few file-I/O forms §3 lists. Remove the task or guard it with `` `ifdef ``. `$dumpfile` and `$dumpvars` give waves, as in plain `vcs` (§3). |
| `sv2ghdl could not translate module <m> (see <daidir>/nvc/iverilog.log)` | as for the digital message above. |
| `cell C: subckt S cannot be simulated: <why> (<where>)` | The subckt uses a construct vamos does not support (for example an S-parameter element). Change the netlist, or bind the cell to another subckt. |
| `port_connect -cell C (p => X): no net X in the deck; ...; nearest: ...` | A typo, or a net only Verilog has. Name a top-level or `.global` net of the netlist, ground, or `<instance>.<port>`. |
| `... X is node N inside SPICE instance I; ...` | `port_connect` cannot reach inside a SPICE instance. Make N a port of its subckt and connect it from Verilog, or declare it `.global`. |
| `...: real-number interface elements are not supported in v1` | `real p => net` in `port_connect` is not supported. |
| `port P of subckt S is port_connect'ed only by -inst statements that do not match I (...)` | Add a cell-level `port_connect -cell C`, or an `-inst` for I. |
| `port gnd of subckt S is a ground alias, which is ground inside the subckt, so it cannot be connected to N; ...` | Connect it to ground, or rename the port. |
| `warning: port_connect ...: no voltage source reaches N (port P of I): the port floats` | The net has no supply in the deck. Add one, or connect the port elsewhere. |
| `tb.u1: parameter override G=0.9 on SPICE instance tb.u1 is not passed to subckt <s>` | The subckt takes no Verilog parameters: an override (integer, real or string) would be lost. Remove it, or make the SPICE cell carry the value. Overrides of a multi-view cell's parameters that only size its ports are allowed. |
| `V source S cannot be evaluated (...): the supply trace of <IEs> reaches it, so their levels cannot be set; ...` | Give those interface elements their levels with a rule, or name the supply with `ie_reference_voltage node=<node> voltage=<v>`. |
| `ie_reference_voltage node=N has no voltage= and N is not a constant supply, so the levels of <IEs> would follow it (...)` | Give `voltage=`. |
| `warning: ie_reference_voltage node=N: no interface element's supply trace reaches N, so the command sets no levels ...` | The command selects nothing; the IE report's `levels:` lines show where each trace stopped. |
| `warning: <file>:<line>: map_by_node r=<r> has no effect on <node>: a supply net is an ideal source with no series resistance` | Information: supply nets and `d2a powernet` nodes have no series resistance. |
| `.print/.probe: v(X): the deck has no node X (...)` | Probe a node the deck has: a top-level or `.global` net, or `<X instance>.<node>`. |
| `warning: probe_waveform_voltage P matches no node of the deck, so it saves nothing (...)` | XA cfg patterns are VCS paths: `<Verilog instance path>.<node>` inside a SPICE instance, a netlist net by its own name. |
| `warning: <node>: variable <v> joins SPICE ports in analog; VCS digitises it: ...` | An approximation: two or more SPICE ports connected through one Verilog `reg`/`logic` share one analog node, where VCS gives each port an interface element. Declare it a wire if that is intended, or give each port its own net. |
| `warning: <netlist>:<line>: model <m>: HSPICE's default CAPOP=2 gate capacitance (...) is simulated as SPICE's Meyer model (CAPOP=0); ...` | Neither engine has HSPICE's CAPOP=2 model. Add `capop=0` to the card (or `.option spice`) to make HSPICE use the same model; `--vamos-strict` makes this an error. Cards get a `note: ... written as HSPICE simulates it: ...` listing the HSPICE defaults vamos wrote. |
| `warning: <netlist>:<line>: model <m>: no CJ and no NSUB on a MOS LEVEL <n> card whose instances give AD/AS: HSPICE's default CJ is ambiguous (...); give CJ (F/m^2) or NSUB on the card` | vamos writes HSPICE's default for the default option ASPEC=0, `sqrt(eps_si*q*NSUB/(2*PB))` (with NSUB given, that is the documented default, and there is no warning), but the manual's default column also gives 579.11 µF/m². Give `CJ` or `NSUB` on the LEVEL 1/2/3 card. `--vamos-strict` makes it an error. |
| `warning: ... model <m>: CBD/CBS on a MOS LEVEL <n> card whose instances give AD/AS: HSPICE uses CBD/CBS only when CJ*AD+CJSW*PD is 0 ...`; `... php=<v> differs from PB=<v>: ... simulated with PB`; `... cox=<v> with LEVEL 1: HSPICE invokes the Meyer gate capacitance only when TOX is specified ...`; `... <inst>: sa/sb under .option scale=<s>: both targets read them as written (in meters, unscaled) ...` | HSPICE behaviour neither engine has, or that the manuals leave open (docs/VAMOS_AMS_DESIGN.md §4.3.5–§4.3.6): give `AD=AS=0` or drop CBD/CBS; set PHP equal to PB; give TOX instead of COX; mind that SA/SB/SD are not scaled. `--vamos-strict` makes each an error. |
| `note: <netlist>:<line>: model <m>: cjo=1e-30 written for Xyce, ...` (a diode with CJSW or TT but no CJO) | Information: Xyce computes no junction charge for a diode whose CJO is 0, so vamos writes a negligible value instead. |
| `.option aspec is not supported: ASPEC compatibility mode sets SCALE=SCALM=1e-6, WL, LEVEL=6 and ACM=1 MOS models and the CJ=IS=0 defaults ...` | Remove `.option aspec` (or set it to 0). |
| `node name 'a.b' contains '.', which HSPICE reserves as the hierarchy separator (<subckt>.<node>)`; `node x1:n1: VACASK and Xyce also call the internal node n1 of instance x1 'x1:n1', so the two would be one node (...); rename the node` | Rename the net. A `.` in a net name cannot be probed or given an `.ic` (vamos reads `v(a.b)` as `<subckt>.<node>`); a `<instance>:<node>` name would silently merge with that instance's internal node on both engines. |
| `i(r1) in the expression of e1: the R element has no branch current VACASK or Xyce can read in an expression (V, L, E, H and E VOL= elements have one); put a 0 V source in series with r1 and use i() of it` | As the message says: a 0 V source in series (`vr1 a a1 0`, with `r1` moved to `a1`), and `i(vr1)` in the expression. |
| `model <m>: HSPICE's wire capacitance (CAP: the CRC model of a wire resistor) has no target equivalent; not supported on VACASK` (also `SHRINK`, and on Xyce `DW` other than `DLR`, a model `L`, a `RES`/`CAP` default) | An R or C model parameter neither engine can compute as HSPICE does. The other wire parameters (`RSH DW DLR TC1R TC2R TREF COX CAPSW DEL THICK DI W L`) are mapped. |
| `subckt <s>, defined inside subckt <p>, reads <names> of the enclosing subckt: VACASK does not pass an enclosing subckt's parameters into a nested definition; define <s> at top level and pass <names> on its X lines` | VACASK only (Xyce reads them as HSPICE does): move the nested definition to top level and pass the parameters on its X lines, or use `--vamos-analog=xyce`. |
| `model <m>: DCAP=3 (peak-limited depletion capacitance) has no VACASK or Xyce equivalent; use DCAP=1 or 2` | Use `.option dcap=1` or `2`. |
| `note: <netlist>:<line>: .tran: maximum time step ...`; with no `.tran`, `note: <netlist>:<line>: .option delmax=<v>: the maximum time step of the analysis vamos synthesises (the netlist has no .tran)` | Information: the HSPICE step bound vamos applies. `--vamos-analog-maxstep` (the note then shows that value) or `.option delmax` sets another. With no `.tran`, `.option delmax` is the maximum step of the analysis vamos synthesises; `--vamos-analog-maxstep` still wins. |
| `vamos: error: <path>: cannot write the interface-element report: <reason>`, then `AMS compile failed at the IE report` | `simv.msv` is not writable. |
| an engine message from the compile-time check | The engine refused the deck at an operating point. The log is `simv.daidir/ams/deck/smoke.log`. Fix the netlist; `--vamos-no-deck-check` only postpones the failure to the run. |
| `the cut has no table entry for instance path <p>, which nvc elaborated: an internal error of the cut (...)` | Report it, with `simv.daidir/ams/cut.vhd`. |

AMS run (`./simv`):

| message | cause and fix |
|---|---|
| `vamos: error: <lib> not found (searched <dirs>): set VAMOS_VACASK_HOME ...` (or `VAMOS_XYCE_LIBS`, or `VAMOS_NVC` for the bridge), exit 1 | The engine's library or the bridge is not where vamos and nvc look. Point the variable at the build. |
| `vamos: error: <path> does not export <sym>() (it predates the vamos co-simulation ABI): rebuild ...`, exit 1 | The library was built without the vamos patches: rebuild it. nvc's own form of this check is `** Fatal: co-simulation ABI mismatch: ...`. |
| `vamos: error: co-simulation failed: <line>` | The named line is the cause. For example, `[FAILED: signal not found]` means a boundary path nvc could not resolve: report it, with the kept run directory. |
| `vamos: error: analog output ends early at <t> s (expected <t'> s)` | The rawfile stops before the end the run reported (the check allows 1e-9 relative or 2 fs). Nothing is published and the run directory is kept. Report it, along with the kept directory. |
| `** Error: <engine> transient failed at <t> s` | The analog solver failed (for example "Time step too small"). Exit 1, run directory kept. Check the circuit. Xyce fails this way when a fast D2A edge reaches a transistor-driven node that has no capacitance (level-1 MOS cards have none): add a small capacitance (1 fF) or use VACASK. Purely resistive nodes are fine. |
| `[cosim_bridge] warning: D2A '<name>': a <d> s ramp at <t> s is shorter than <engine> resolves at that time; it takes <d'> s (reported once per signal)` | Late in a long run an engine cannot resolve a very short D2A edge (below 1e-13 of the time: 1 fs from 10 ms on, 10 ps from 100 s on), so the edge is stretched to that length. Use a longer `rf_time` if the edge matters. |
| `vamos: error: the co-simulation ended without an end line (nvc exit status N)` | The co-simulation stopped before it ran, for example with an nvc fatal. Read the lines above it; `COSIM_TRACE=1 ./simv` shows the bindings. |
| `vamos: error: nvc was killed by signal N (NAME)`, exit 128+N | Something killed nvc: out of memory, or a batch system. |
| `vamos: note: co-simulation interrupted (...) at <t> s; run directory kept (partial waves): <dir>` | Ctrl-C or another signal stopped the run (§5). The partial rawfile is in the kept directory. |
| `co-simulation stalled at <t> s`, `the analog engine wrote no rawfile`, `the analog rawfile has no points (...)`, `cannot read the analog rawfile: ...` | Report these, with the kept run directory. |
| `note: run directory kept: <dir>` / `note: run directory kept (testbench output): <dir>` | A failure or `--vamos-keep`; the second form, files the run left there that are not the engines' own. |
| `note: no analog output: the digital stopped at t=0` | A testbench that stops at time 0. Xyce writes no rawfile then; VACASK writes the operating point, or nothing when the `.tran` has a TSTART. Not an error. |
| `note: no analog output: the run ended at <t> s, before the deck's output start <t'> s (.tran TSTART)` | The run ended before the `.tran` start time, so the engines wrote no point. Not an error. |

`./simv` and re-entry:

| message | cause and fix |
|---|---|
| `vamos: error: ./simv: the last compile into simv.daidir failed or was interrupted, so there is nothing to run; compile again` | Fix the compile error and compile again. |
| `<daidir> holds no finished compile (the last compile failed or was interrupted, or the directory is incomplete); compile again` / `<daidir> is not a vamos simulation directory (...)` | The first: the compile into it failed or was interrupted (it removes the job record first and writes it last), for example when a copy of an older `simv` runs it. The second: `vamos -simv --vamos-daidir=` pointed at a directory that does not exist or holds an unreadable record. Compile again. |
| `<daidir>: compiled by a newer vamos (schema N); recompile` | Compile again with this vamos. |
| `-simv needs --vamos-daidir=<dir> (run the generated ./simv instead)` | Run `./simv`, not `vamos -simv`. |
| `vamos re-entered <n> times (...); refusing to recurse`, `'vcs' was called from inside vamos ... and no real 'vcs' exists to hand it to` | Something that vamos ran called `vcs` again. Check the scripts on PATH. |

To see what vamos runs, add `--vamos-verbose`. To see what it understood from the command line, read
`<daidir>/vamos.job.json`.

## 10. Limitations

- Personalities: only `vcs`, `vcs-ams`, `simv` and the `nvc` pass-through. No three-step flow, no VHDL
  sources, no `xrun`/Questa yet (docs/VAMOS_PLAN.md §8), no `spectre` yet (docs/VAMOS_SPECTRE_DESIGN.md).
- Accepted and reported, but not done yet: debug and other wave formats (`-debug_access`, VPD/FSDB, `-kdb`;
  `$dumpvars` writes a VCD, §3), coverage (`-cm`), UCLI (`-ucli`, `-do`), VPI/PLI/DPI (`-P`, `-load`, C
  sources), UVM and SystemVerilog classes (`-ntb_opts uvm`; the route is docs/TODO-uvm-translator.md; a
  class the design uses is a compile error at its file:line),
  parameter overrides (`-pvalue+`, `-parameters`), `-xprop`, and `-y` library search (list the files, or use
  `-v`).
- File I/O runs as under vvp, with the differences in §3; the reading tasks are not translated. Waves: the
  VCD covers the whole run whatever `$dumpoff`/`$dumpon` say, and its names follow the translation (§3).
  Plain `vcs` mode warns about each untranslated system task and stops on a top it cannot translate; AMS
  mode refuses both.
- By design, the translation computes on value bits (docs/VAMOS_AMS_DESIGN.md §7): `===`/`!==` do not tell
  x from z; a vector `case`/`casez`/`casex` selector compares only its value bits (x/z bits read as 0/1),
  while scalar selectors, and anything read from pulls or analog pads, follow Verilog; vector arithmetic
  with an x or z operand gives a number, not x; an index with x/z bits selects by its value bits (all-x is
  word 0), so a store at an x index lands in word 0 and a read returns word 0 (or the bit the value bits
  name), where vvp drops the store and reads x. This differs from vvp and VCS and is kept on purpose.
- A continuous assignment that copies a variable (`wire rw = r;`, `wire m0w = mem[0];`) updates one delta
  after a blocking write to the variable, so a read of `rw` later in the same activation sees the old value,
  where vvp sees the new one. Verilog allows both.
- Translation artefacts, open in docs/VAMOS_AMS_DESIGN.md §7 and §10: a recursive task, an automatic task
  entered again before it returns, `disable` of another process's block and `disable fork` are translation
  errors, and so are `$dist_*` and `$value$plusargs` in a branch of `?:`, an operand of `&&`/`||` or a loop
  condition (Verilog may not evaluate them there; the translation would, every time), a `void` function, a
  `final` block (other than one that only closes files) and an intra-assignment event control on a
  nonblocking assignment (§9); `$sformat` is translated with a literal format, and a non-literal format is a
  translation error; a store to a bit outside a vector, or outside a memory word, is dropped, as vvp drops it
  (also at a constant index iverilog ignores, e.g. `array1[0] = 1` on `reg array1[2:1]`), and a memory read
  outside the memory gives x; constant drivers with strengths on the words of a memory of nets translate,
  but the run stops in nvc's net solver for every word width (ivtest pr1703346; it used to read `xx`
  silently for words of 2 bits or more).
- Not simulated, with a compile warning (§9): specify path delays, timing checks and `$sdf_annotate`
  (specparams stay, a min:typ:max one at its typ value). A single top-level module's undriven input ports
  read 0, not z (warned).
- Time: every precision of 1 ms or finer runs at its true size, in plain and AMS mode. A coarser precision is
  compressed to 1 ms per tick, so with no `` `timescale `` the unit is 1 ms, not 1 s. AMS requires a
  precision of 1 ms or finer.
- AMS v1 scope: Verilog-top designs with SPICE cells (plus Verilog-A through `.hdl`), the two-step flow, and
  the control-file subset in §6. docs/VAMOS_AMS_DESIGN.md §0 lists everything that is not in v1, such as
  SPICE-top designs, the Verilog-AMS flow (`-ams`, `.vams`), real-number interface elements and dynamic
  supplies. Its §10 lists the open items. A SPICE-only cell's bus port on a concatenation of wires works
  without `port_dir`, and real expressions on a cell's real ports (`.vin(r1 + 0.2)`, `.vin(code * 0.1)`)
  and undriven `wire real` nets work.
- Not built yet: `vamos-install`, `use-vamos`, Windows `.cmd` launchers, remote farm "sites", and the
  stat-sim Monte Carlo extensions (docs/VAMOS_PLAN.md §2a-2c, §6).

## 11. Other documents

| document | for |
|---|---|
| docs/VAMOS_PLAN.md | the overall plan, phases and decisions (installation, use-vamos, provenance), as of 2026-09-27. Its AMS parts (§6 and phase 4) predate VACASK, the default engine; they are marked superseded and point to docs/VAMOS_AMS_DESIGN.md and this guide |
| docs/VAMOS_AMS_DESIGN.md | the `vcs-ams` contract: every control-file command, key and disposition; the runtime; the open items |
| docs/VAMOS_SPECTRE_DESIGN.md | the planned `spectre` personality (a design; not built) |
| docs/TODO-uvm-translator.md | the planned UVM route |
| docs/vamos-manuals.html | links to the vendor manuals vamos follows |
| tests/vamos/ | working examples. `test_vamos.py` and `test_vamos_driver.py` cover digital; `test_ams_e2e_*.py` cover AMS end to end; `test_r6_*.py` cover the latest fixes, such as file I/O (`test_r6_F.py`) and waves (`test_r6_W.py`) (each item is described in docs/VAMOS_AMS_DESIGN.md §9). Run them from `tests/vamos`: `python3 -m unittest test_vamos` (about 10 s), `python3 -m unittest test_ams_e2e_basics` (2 to 3 minutes). Tests that need a tool or engine the machine lacks are skipped |
| tests/hazard3_mandelbrot/README.md | a larger working example: the Hazard3 RISC-V Mandelbrot test case (Verijit's), which Verilator, Icarus and vamos run the same way; with the regression harness, `regress/regress run hazard3/vamos` compiles it with `vcs` and runs `./simv` |
