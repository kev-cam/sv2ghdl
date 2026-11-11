# sv2ghdl

**SystemVerilog to VHDL translation, and the federated simulation stack built on it**

sv2ghdl started as a SystemVerilog-to-VHDL translator for the GHDL and NVC simulators. Today the
repository holds the whole stack that grew around that idea:

- **The translation path.** SystemVerilog goes through the kev-cam fork of Icarus Verilog, whose
  VHDL backend (`iverilog -tvhdl`) emits VHDL over this project's `sv2vhdl` library (3-state
  `logic3d` types, strengths, `$display`, analog and math packages), and that VHDL runs on the
  kev-cam fork of nvc. `sv2ghdl.pl`, the original Perl translator, is the per-module fallback.
- **vamos.** Drop-in vendor command lines on that stack: `vcs`/`./simv`, `vcs-ams`, `spectre`, and
  the Vivado simulator's `xvlog`/`xvhdl`/`xelab`/`xsim`. Digital designs run on nvc or Verilator;
  analog cells run on VACASK or Xyce, co-simulated by nvc.
- **VHDL extensions in the nvc fork** for bidirectional and switch-level modelling: `'driver`,
  `'other` and `'receiver` attributes and a blocking assignment to a driver.
- **`--accel`**, nvc's compiled single-cycle acceleration of synthesizable subtrees (`Accel.md`).
- **bfit**, a simulator-agnostic analog accelerator (`bfit/`, separately licensed), with its
  cross-engine benchmarks and a GPU instance farm.
- **A regression harness** (`regress/`) that runs the external suites and our own tests across
  every engine and keeps the results in a database.

## vamos: vendor command lines on this stack

vamos is a drop-in replacement for a simulator's command line: your `vcs ...` compile and
`./simv ...` run lines compile and simulate the design through iverilog's VHDL backend and nvc,
with no vendor tool or licence. `vcs-ams` adds HSPICE-dialect SPICE cells, simulated on VACASK
(the default) or Xyce (`--vamos-analog=xyce`) and co-simulated by nvc. The `spectre` personality
runs a Spectre netlist (Spectre or SPICE mode) on the same analog engines and writes what Spectre
writes (the results directory, PSF, the log). The Vivado simulator's command lines run on the same
stack, and `--vamos-engine=verilator` puts Verilator behind any digital compile.

```sh
export SV2GHDL=/usr/local/src/sv2ghdl               # your sv2ghdl checkout
export PATH=$SV2GHDL/shims:$PATH                    # the drop-in names, see the note below
vcs -sverilog tb.sv rtl/counter.v -R                # compile and run
./simv +CYCLES=9                                    # run again, with plusargs
vcs-ams -sverilog tb.sv                             # digital on nvc; the SPICE cells vcsAD.init chooses, on VACASK
spectre opamp.scs                                   # a Spectre deck, VACASK underneath
xvlog --sv tb.sv rtl/counter.v && xelab -s snap tb  # the Vivado simulator's flow, same stack
xsim snap -R --testplusarg CYCLES=9
xelab --vamos-engine=verilator -s fast tb && xsim fast -R   # same command lines, Verilator as the engine
```

`shims/` holds the vamos personalities `vcs`, `vcs-ams`, `spectre`, `nvc` (a pass-through),
`xvlog`, `xvhdl`, `xelab` and `xsim`, and also the older `iverilog`, `vvp` and `verilator`
wrappers (`bin/*-sv2ghdl`). With the whole directory first on PATH, every one of those names,
including `spectre` and `iverilog`, is shadowed. If you want only some of them, make your own
directory of links to `bin/vamos`, as the guide shows.

Start with **[docs/VAMOS_GUIDE.md](docs/VAMOS_GUIDE.md)**: §2 set-up (including the three AMS
parts that are built by hand), §3 digital quick start, §5 AMS quick start, §10 limitations,
§12 the spectre personality, §13 the Vivado personalities. The designs behind it are
`docs/VAMOS_PLAN.md`, `docs/VAMOS_AMS_DESIGN.md` and `docs/VAMOS_SPECTRE_DESIGN.md`.

## Building the stack

### What `docker/build_stack.sh` builds

`build_stack.sh` clones the kev-cam forks at the head of their default branches and builds, from
source, into a prefix (`~/sv2ghdl-stack/usr` by default):

| Mode      | Builds                                                                 |
|-----------|------------------------------------------------------------------------|
| `digital` | iverilog (with the VHDL backend), nvc, ghdl, yosys (with `libyosys`), the sv2ghdl wrappers and vamos |
| `analog`  | Trilinos and Xyce                                                      |
| `full`    | both (the default); adds roughly 30 to 90 minutes and several GB for Trilinos + Xyce |
| `verify`  | clones the Nuitka source for verification work (install step deferred) |
| `-mpi`    | (flag) distributed-parallel Trilinos + Xyce (`Xyce_PARALLEL_MPI`) instead of serial, for large-circuit partitioning and cloud scale-out; needs an MPI toolchain |

`RUN_TESTS=1` runs a Xyce_Regression subset after an analog or full build.

**What it does not build.** The AMS personalities need three more parts, which VAMOS_GUIDE.md §2
describes building by hand: VACASK with its Verilog-A compiler openvaf-r (the default analog
engine of `vcs-ams` and `spectre`), nvc's co-simulation bridge `libcosim_bridge.so`, and Xyce's C
interface `libxycecinterface.so`. A stack from `build_stack.sh` alone runs the digital flows and
standalone Xyce, not `vcs-ams` or `spectre`.

### Clean build in a container

The stack can be built from clean upstream sources inside a small container and rsync'd onto a
host `/usr/local`. Works with docker or podman:

```sh
docker build -t sv2ghdl-base -f docker/Dockerfile .
docker run -d --name sv2ghdl -p 2222:22 -p 8080:80 sv2ghdl-base
docker/export.sh /tmp/sv2ghdl-export
sudo rsync -a /tmp/sv2ghdl-export/ /usr/local/
```

Or with podman (rootless, no daemon): no clone needed, fetch the Containerfile for your distro and
build directly. Available variants:

| Distro                  | Containerfile             |
|-------------------------|---------------------------|
| Ubuntu 22.04 (default)  | `Dockerfile.ubuntu22`     |
| Debian                  | `Dockerfile.debian`       |
| Fedora                  | `Dockerfile.fedora`       |
| Arch                    | `Dockerfile.arch`         |
| openSUSE                | `Dockerfile.suse`         |

Pick automatically from `/etc/os-release`:

```sh
case "$(. /etc/os-release; echo $ID)" in
  fedora|rhel|centos|rocky) F=Dockerfile.fedora ;;
  arch|manjaro)             F=Dockerfile.arch ;;
  opensuse*|sles)           F=Dockerfile.suse ;;
  debian)                   F=Dockerfile.debian ;;
  *)                        F=Dockerfile.ubuntu22 ;;
esac
curl -fLO https://raw.githubusercontent.com/kev-cam/sv2ghdl/main/docker/$F
podman build -t sv2ghdl-base -f $F .
podman run -d --name sv2ghdl -p 2222:22 -p 8080:80 sv2ghdl-base
ssh -p 2222 root@localhost /opt/sv2ghdl/docker/build_stack.sh
rsync -av -e 'ssh -p 2222' root@localhost:/home/claude/sv2ghdl-stack/usr/ /tmp/sv2ghdl-export/
sudo rsync -a /tmp/sv2ghdl-export/ /usr/local/
```

Binary compatibility: an Ubuntu 22.04 build exports cleanly onto most current glibc-based hosts.
Build on the variant matching your host distro if you hit glibc-version mismatches.

Visit <http://localhost:8080> after starting the container for instructions. The default SSH
password is `sv2ghdl`; change it before exposing the port.

### Windows (WSL2, no container)

WSL2 gives you a real Linux environment, so the easiest path on Windows is to skip containers:

```powershell
wsl --install -d Ubuntu-22.04
```

Then inside the new Ubuntu shell:

```sh
sudo apt-get update && sudo apt-get install -y curl
curl -fL https://raw.githubusercontent.com/kev-cam/sv2ghdl/main/docker/build_stack.sh \
    | bash
```

Pass a mode via `bash -s --`, e.g. `… | bash -s -- digital` to skip the analog build, or
`… | bash -s -- analog -mpi` for the distributed Xyce. The build runs directly in WSL and
installs into `~/sv2ghdl-stack/usr` (run as root, the script builds as the user `claude`, in
`/home/claude/sv2ghdl-stack/usr`); copy onto the WSL `/usr/local` with
`sudo rsync -a ~/sv2ghdl-stack/usr/ /usr/local/`.

### Which build do I need?

- **Digital only** (RTL simulation through iverilog, nvc, ghdl, yosys and vamos's digital
  personalities): the base image (`Dockerfile`, `Dockerfile.debian`, …) or `build_stack.sh digital`.
  Lean, fast, no Trilinos.
- **Analog or mixed-signal with Xyce**: the AMS image, which bakes the distributed-parallel (MPI)
  Trilinos + Xyce into the image so it is ready to run (no Trilinos compile on first use):

  ```sh
  docker build -t sv2ghdl-ams -f docker/Dockerfile.ams .            # nvc + Xyce-MPI
  docker build -t sv2ghdl-ams --build-arg BUILD_MODE=analog \
               -f docker/Dockerfile.ams .                           # Xyce-MPI only
  docker run -d --name sv2ghdl-ams -p 2222:22 -p 8080:80 sv2ghdl-ams
  docker/export.sh /tmp/sv2ghdl-export   # then rsync onto the host /usr/local
  ```

  `Dockerfile.ams` is Debian Trixie based (GCC 14, the toolchain Trilinos 14.4 builds cleanly
  with) and runs `build_stack.sh <mode> -mpi` at image-build time. It does not add the three
  hand-built AMS parts above. A serial single-node Xyce is faster on small circuits; MPI pays off
  on large designs and distributed (laptop to cloud) runs, not on small-circuit speed.

## Vision: HDLs for the AI Era

IEEE standards are a **portable floor, not a ceiling**. They ensure baseline code works everywhere, but there's no reason you can't build on top. Commercial tools have always added proprietary extensions; open-source tools like GHDL and NVC can do the same - transparently.

We aim to provide HDL capabilities suited to modern design challenges:

- **Dynamically reconfigurable** - runtime adaptation, not just static netlists
- **Parallel processing** - native support for massively parallel architectures
- **Mixed-abstraction** - RTL, behavioral, analog, and system-level in one simulation
- **Proper C++ interaction** - OO interfaces in VHDL/SV that map directly to C++ classes

The translated VHDL core remains portable (standard IEEE), while extensions enable capabilities the standards committees couldn't agree on or didn't anticipate.

## Why Translate SV to VHDL?

SystemVerilog is widely used for RTL design, but its simulation semantics have limitations:

- **X-propagation problems**: SV's `logic` type handles unknown states poorly, leading to optimistic simulation that masks real hardware bugs
- **No multi-UDN wires**: SV lacks support for user-defined net types with multiple resolution functions
- **Limited bidirectional modeling**: Bidirectional components (analog switches, transmission gates) are awkward in SV

By translating to VHDL and using extended simulators (GHDL, NVC), we gain:

- **Better UDN types**: Replace SV `logic` with resolution functions that properly propagate unknowns
- **Multi-UDN wire support**: Model complex interconnect with multiple resolution semantics
- **True bidirectional components**: Native support in VHDL's signal resolution model
- **Federated simulation**: Mix VHDL with other simulation engines seamlessly

## Approach

### Target Scope

The focus is **RTL-style SystemVerilog** - the synthesizable subset used for actual hardware design - plus the testbench constructs the external suites (ivtest, sv-tests, rtlmeter) exercise. Post-synthesis gate-level netlists are covered by the ATPG flow in `tests-atpg/` and `tests-RTL/`.

### Federated Simulation Strategy

Rather than attempting to translate the full horror of SystemVerilog (classes, constraints, coverage, UVM, etc.), we use **federated simulation**:

```
┌─────────────────────┐     ┌─────────────────────┐
│   SV Testbench      │     │     VHDL DUT        │
│   (Verilator/etc)   │◄───►│   (GHDL/NVC)        │
│                     │     │                     │
│ - UVM/classes       │     │ - Better X-prop     │
│ - Constraints       │     │ - Multi-UDN wires   │
│ - Coverage          │     │ - Bidirectional     │
└─────────────────────┘     └─────────────────────┘
```

This lets existing SV testbenches drive the translated VHDL design-under-test (DUT), gaining VHDL's simulation advantages without reimplementing SV's verification features. The full complexity of SV verification constructs doesn't need to be addressed - possibly ever.

What runs today: a SystemVerilog testbench and DUT go through iverilog's VHDL backend to nvc as one
design (or to Verilator, through vamos), and the analog side of a mixed-signal design runs in
VACASK or Xyce, co-simulated with nvc through `libcosim_bridge.so` (docs/VAMOS_AMS_DESIGN.md).

### Discipline and Nature Model for Wires

VHDL lacks a true "wire" concept. Its `signal` is a value-over-time abstraction with resolution bolted onto the type system. Verilog has `wire` but SystemVerilog confuses it as a type rather than connectivity. VHDL-AMS's `terminal` doesn't quite fit either.

We adopt **Verilog-AMS's discipline/nature terminology** for GHDL/NVC extensions:

```
           discipline electrical
wire: ─────────┬──────────────────
               │
    ┌──────────┴──────────┐
    │   driver            │
    │   internal: logic   │  ← type (0,1,X,Z representation)
    │   kind: potential   │  ← drives voltage ('1'→Vdd, '0'→Gnd)
    └─────────────────────┘
```

**Key distinctions:**

| Concept | What it is | Examples |
|---------|-----------|----------|
| **Discipline** | Physical nature of a wire | `electrical`, `thermal`, `mechanical` |
| **Nature** | Quantities in a discipline | voltage/current, temperature/heat flow |
| **Driver kind** | Potential (across) or flow (through) | voltage source vs current source |
| **Internal type** | Model's representation | `logic`, `real`, `std_logic` |

Digital logic drivers are **potential drivers** on an **electrical discipline**. They present voltage to the wire; current is consequential. The internal `logic` type (0,1,X,Z) maps to voltage levels. This is true even for "digital" simulation - the wire is always physical.

**Two-phase elaboration:**

1. **Connectivity phase** - Ports connect if disciplines match. Build the wire graph. Internal types, driver kinds don't matter yet.

2. **Resolution analysis** - After connectivity is complete, examine each wire:
   - Gather all connected drivers/receivers
   - Determine resolution strategy based on participants:
     - All potential drivers → voltage arbitration (std_logic-like)
     - All flow drivers → current summation (KCL)
     - Mixed → full electrical solve or defined priority

This separates the **connectivity problem** (discipline matching) from the **resolution problem** (worked out post-elaboration based on actual participants). PWL (piecewise-linear) signals are discrete representations - just `(time, value)` pairs - that can be native VHDL types. The continuous DAE relationships live inside component models, not in interconnect semantics.

This model is a design, not yet an nvc feature: the shipped co-simulation exchanges PWL
boundary values between nvc and the analog engine (the `.boundary` files of docs/VAMOS_AMS_DESIGN.md)
rather than resolving disciplines inside nvc.

### PWL Signals and the Federation Library

PWL (piecewise-linear) waveforms are fundamental to analog/mixed-signal simulation. Unlike VHDL-AMS which lacks clean PWL syntax, we define PWL tables as native HDL data:

**VHDL:**
```vhdl
type pwl_point is record
  t : real;
  v : real;
end record;
type pwl_table is array (natural range <>) of pwl_point;

constant vdd_ramp : pwl_table := (
  (0.0,    0.0),
  (1.0e-9, 3.3),
  (5.0e-6, 3.3),
  (5.1e-6, 0.0)
);
```

**SystemVerilog (testbench side):**
```systemverilog
real vdd_ramp[][] = '{
  '{0.0,    0.0},
  '{1.0e-9, 3.3},
  '{5.0e-6, 3.3},
  '{5.1e-6, 0.0}
};
```

The **C++ federation library** provides runtime support via VHPI:

```vhdl
-- Foreign function declaration
function pwl_value(source_id : integer) return real;
attribute foreign of pwl_value : function is "VHPI libfederation.so pwl_value";
```

The library handles:
- **Interpolation** - compute value at current time
- **Breakpoint scheduling** - notify simulator of next transition
- **SPICE-like sources** - PWL, SIN, PULSE, EXP, etc.
- **Solver integration** - interface with Xyce or other analog engines

This gives clean separation:
- **Data in HDL** - portable, readable, version-controlled with the design
- **Runtime in C++** - interpolation, scheduling, solver interface

VHDL has limited OO support (protected types provide encapsulation but no inheritance), so complex runtime behavior belongs in the federation library rather than fighting VHDL's limitations. The runtime pieces that exist live in the nvc fork under `lib/sv2vhdl` (the `sv_analog`, `sv_math` and federation bridge sources) and `src/cosim_bridge.cpp`.

### ATPG-Driven Test Generation

The project leverages **Automatic Test Pattern Generation (ATPG)** to create testbenches for translated logic:

- Generate test vectors that exercise the design
- Validate functional equivalence between SV and VHDL
- Target stuck-at and transition fault models
- Provide regression coverage as translation improves

See `tests-atpg/` for ATPG-based test infrastructure.

## The translators

### iverilog's VHDL backend (the main path)

The kev-cam fork of Icarus Verilog carries a VHDL code generator that emits this project's
dialect. vamos and the `iverilog`/`vvp` wrappers drive it; by hand:

```sh
iverilog -g2012 -tvhdl -psv2vhdl=1 -o counter.vhd counter.v
nvc --std=2040 -L <nvc prefix>/lib -a counter.vhd        # the sv2vhdl library is built at standard 2040
```

The output uses the `sv2vhdl` library from the nvc fork (3-state `logic3d` types, strengths,
`$display`, analog and math packages) and carries the Verilog origin of every unit as an
attribute, which `--accel` reads back. For the counter below it produces:

```vhdl
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
library sv2vhdl;
use sv2vhdl.sv_display_pkg.all;
use sv2vhdl.sv_strength_pkg.all;
use sv2vhdl.logic3d_types_pkg.all;
use sv2vhdl.sv_analog_pkg.all;
use sv2vhdl.sv_math_pkg.all;

-- Generated from Verilog module counter (counter.v:1)
entity counter is
  port (
    clk : in logic3d;
    rst : in logic3d;
    count : out logic3d_vector(7 downto 0)
  );
  attribute nvc_verilog_src : string;
  attribute nvc_verilog_src of counter : entity is "counter.v:1";
end entity;

-- architecture from_verilog: the always block becomes a process with
-- Verilog nonblocking-assignment semantics (about 40 lines, omitted)
```

**Input (counter.v):**
```verilog
module counter (
    input clk,
    input rst,
    output reg [7:0] count
);
    always @(posedge clk or posedge rst) begin
        if (rst)
            count <= 8'h00;
        else
            count <= count + 1;
    end
endmodule
```

### sv2ghdl.pl (the fallback)

`sv2ghdl.pl` is the original Perl translator. `bin/iverilog-sv2ghdl` falls back to it per module
when the iverilog backend declines a module. It needs Perl 5.10+ and nothing else:

```bash
./sv2ghdl.pl design.v                     # basic translation
./sv2ghdl.pl design.v -o design.vhd       # named output file
./sv2ghdl.pl --mode=logic3d design.v      # 3-state logic types
./sv2ghdl.pl -find ./rtl -d vhdl_output   # batch translate all .v files in a directory
./sv2ghdl.pl -v design.v                  # verbose
```

| Option | Description |
|--------|-------------|
| `--mode=MODE` | Translation mode: `standard`, `enhanced` or `logic3d` (see below) |
| `-o, --output=FILE` | Output file name |
| `-d, --outdir=DIR` | Output directory for batch translation |
| `-find[=PATH]` | Find and translate all Verilog files |
| `--name=PATTERN` | File pattern for -find (default: `*.v`) |
| `--no-line-directives` | Do not emit `#line` directives |
| `-v, --verbose` | Verbose output |
| `--version` | Show version |
| `-h, --help` | Show help |

Modes. Every output uses `ieee.numeric_std` for arithmetic and the nvc fork's
`sv2vhdl.sv_display_pkg` for `$display`, so analyse it with `nvc --std=2040 -L <nvc prefix>/lib`
like the iverilog output:

- `standard` (default): `ieee.std_logic_1164` types.
- `enhanced`: `enh_logic`/`enh_logic_vector` from `packages/enhlogic`, a rename of
  std_logic_1164 with conversions to and from the IEEE types. Analyse its two files into `work`
  before the translated design.
- `logic3d`: `logic3d`/`logic3d_vector` from the nvc fork's `sv2vhdl.logic3d_types_pkg`, the
  3-state types the iverilog backend emits. Conditions become `is_one(sig)`, sized literals
  `to_l3d(…)`.

The counter above analyses and elaborates in all three modes. Single-statement `if`/`else`
branches, `else if` chains, one-line `if (c) stmt;` forms and an `else` on the line after a
block's `end` are all closed correctly. The translator is line-based, so a statement split over
several lines or a `case` inside a process is still beyond it; the iverilog backend is the path
that is regression-tested.

### Drop-in wrappers for iverilog / vvp / verilator

Wrapper scripts route `iverilog`, `vvp` and `verilator` invocations through the translator and
nvc, so existing flows can try the nvc path without changing build files. `shims/` holds them
under their real names; `utils/install-svx` installs them under `svx-` names so they do not shadow
the real tools:

```bash
sudo utils/install-svx           # installs to /usr/local/bin
utils/install-svx ~/bin          # or a custom prefix
utils/install-svx --uninstall    # remove
```

| Wrapper | Backing script | Notes |
|---|---|---|
| `svx-iverilog` | `bin/iverilog-sv2ghdl` | `iverilog -tvhdl` per module, falling back to `sv2ghdl.pl`; also translates modules resolved from `-y` library directories |
| `svx-vvp` | `bin/vvp-sv2ghdl` | Elaborates and runs with nvc, filters output to iverilog `$display` format; `--accel` or `NVC_ACCEL` turns on acceleration |
| `svx-verilator` | `bin/verilator-sv2ghdl` | Handles `--binary -o OUT [--top T] sources…`; any unsupported flag or failure falls through to the real `verilator` (override with `VERILATOR=…`) |

Set `SVX_VERBOSE=1` to log fall-through decisions.

## What Gets Translated

| SystemVerilog | VHDL |
|---------------|------|
| `module` | `entity` + `architecture` |
| `input/output` | `port` declarations |
| `wire/reg` | `signal` declarations |
| `always @(posedge clk)` | `process` with `rising_edge()` |
| `always @(*)` | `process(all)` (VHDL-2008) |
| `assign` | Concurrent signal assignment |
| `<=` (non-blocking) | Signal assignment |
| `=` (blocking) | Variable assignment |
| Gate primitives | `sv2vhdl` primitives / Boolean expressions |
| Module instances | Entity instantiation |

The external suites in `regress/` (ivtest, sv-tests, rtlmeter) are the measure of what the
iverilog backend covers; `regress report` shows the current pass counts per block.

## Repository layout

```
sv2ghdl/
├── sv2ghdl.pl               # the Perl fallback translator
├── bin/                     # iverilog-sv2ghdl, vvp-sv2ghdl, verilator-sv2ghdl, vamos, helpers
├── shims/                   # drop-in names: vcs, vcs-ams, spectre, nvc, xvlog, xvhdl, xelab, xsim,
│                            #   and the older iverilog, vvp, verilator wrappers
├── vamos/                   # the vamos driver: personalities/, backends/ (nvc, verilator, cosim),
│                            #   ams/ (vcs-ams), spectre/, netlist/, output/
├── docs/                    # VAMOS_GUIDE.md, VAMOS_PLAN.md, VAMOS_AMS_DESIGN.md,
│                            #   VAMOS_SPECTRE_DESIGN.md, VAMOS_GPU_FARM_DESIGN.md, P1800 proposal
├── docker/                  # build_stack.sh, Dockerfiles per distro, Dockerfile.ams, export.sh
├── utils/                   # install-svx, fetch-repo, fetch-all
├── packages/                # enhlogic (enh_logic_1164), sv_primitives (logic_3d, sv_primitives)
├── yosys/                   # gen_statemachine: yosys netlist -> single-cycle C (accel, GPU farm)
├── regress/                 # the regression harness (regress list / run / report / diff),
│                            #   accel/ (the --accel benchmark suite), docs/, results.db
├── tests/                   # functional tests; tests/vamos (the vamos suites and fixtures);
│                            #   tests/hazard3_mandelbrot (Verijit's Hazard3 case on every engine)
├── tests-atpg/              # ATPG-driven equivalence tests (test2vhdl.pl, test2spice.pl, ...)
├── tests-RTL/               # RTL -> yosys -> gate-level -> ATPG flow
├── bfit/                    # behavioral-fit analog accelerator (PolyForm Noncommercial, bfit/LICENSE)
│   ├── benchmarks/          #   cross-engine perf league (perf.md), merge/table studies,
│   │                        #   vhdl/ (VHDL perf, GPU farm, Hazard3 on the GPU farm)
│   └── tools/               #   stdcell2bfit.py
├── gpubuild/                # CUDA farm binaries built in a pinned container, shipped to GPU hosts
├── Accel.md                 # nvc --accel: what it does, how to drive it, where it is measured
├── SIMULATORS.md            # which commercial simulators the stack displaces, and status
├── PERL_IMPLEMENTATION.md   # sv2ghdl.pl implementation notes
├── Makefile                 # test_misc, tests_atpg, tests_ivl, gen_sm
├── LICENSE, COPYING, LICENSES/
└── README.md                # this file
```

## Tests and regressions

The small suites run through make:

```bash
make all            # test_misc + tests_atpg
make test_misc
make tests_atpg
make tests_ivl      # ivtest through the wrappers (vvp_reg.pl --suffix=-sv2ghdl)
make gen_sm         # build yosys/gen_statemachine
```

Everything else runs through the harness in `regress/`, from Linux or WSL:

```sh
cd regress
./regress list                                   # blocks: suite x engine, and readiness
./regress run                                    # every ready block that is not opt-in
./regress run ivtest/iverilog-nvc nvc/regr-accel hazard3/vamos --notes "..."
./regress report                                 # per-block summary, regressions vs the previous run
./regress diff <runA> <runB>                     # test by test
```

Blocks today: ivtest on iverilog, on the nvc shim and as VHDL; the nvc regression and unit suites,
with `nvc/regr-accel` re-running every passer under `--accel`; sv-tests on Verilator and iverilog;
rtlmeter on Verilator and the nvc shim; Xyce regression, the IHP PDK and PyMS suites; ltz and
QSPICE circuit sets; and the Hazard3 Mandelbrot blocks (`tests/hazard3_mandelbrot/README.md`).
`regress/accel-gate.sh` and `regress/coord-gate.sh` are the gates a change must pass before it
is merged across the forks (`regress/COORD-GATE.md`). The vamos suites are pytest under
`tests/vamos/` (docs/VAMOS_AMS_DESIGN.md §9, docs/VAMOS_SPECTRE_DESIGN.md §11).

## VHDL extensions in the nvc fork

Bidirectional switch modelling needs to write a signal's driver separately from reading its
resolved value, and to read the resolution of every driver but one's own. Standard VHDL has
neither. The kev-cam nvc fork implements them as attributes:

- **`'driver`**: the process's own driver of a resolved signal, as an assignment target.
- **`'other`**: the resolved value of all drivers on the signal except the current process's.
- **`'receiver`**: the local result of the resolution process.
- **Blocking assignment to a driver**, `a'driver := b'other`: immediate, no delta cycle, for
  switches that have no propagation delay (`tran`, `rtran`) and would otherwise pay an event-queue
  round trip per pass-through. The scheduled form `a'driver <= …` remains available.

The switch primitives in the nvc fork's `lib/sv2vhdl/sv_tran.vhd` use them:

```vhdl
-- sv_tran: always-on bidirectional switch
process (a'other, b'other)
begin
    a'driver := b'other;
    b'driver := a'other;
end process;
```

(Earlier revisions of this README proposed the spelling `'others`; the implemented attribute is
`'other`.) The extended standard is selected with `--std=2040`; `nvc/lib/sv2vhdl/STD_MX_EXTENSIONS.md`
in the fork lists the other language extensions behind that standard.

## Status

Done:

- SystemVerilog to VHDL through the iverilog backend, run on nvc; `sv2ghdl.pl` as the fallback.
- vamos personalities `vcs`/`simv`, `vcs-ams`, `spectre` (phases 0 to 2f of its design) and the
  Vivado simulator's `xvlog`/`xvhdl`/`xelab`/`xsim`; Verilator as an alternative digital engine.
- Mixed-signal co-simulation of nvc with VACASK and with Xyce; PyMS Verilog-A devices in Xyce.
- The `'driver`/`'other`/`'receiver` extensions and the 3-state `logic3d` libraries in the nvc fork.
- `--accel` (Accel.md), measured in `regress/accel/`; the ATPG flows; the regression harness.
- bfit: `front`, `merge` and `table` modes with xyce, ngspice and VACASK drivers; the cross-engine
  perf league; the GPU instance farm with its Hazard3 case measured on rented cards.

Planned or in progress:

- `use-vamos`, redirecting tools called by absolute path inside scripts (docs/VAMOS_PLAN.md §2c).
- The spectre personality's phase 3 (deferred; docs/VAMOS_SPECTRE_DESIGN.md).
- The discipline/nature wire model and the federation library as designed above; today's
  co-simulation exchanges PWL boundary values between engines instead.

## Related Projects

- [NVC](https://github.com/nickg/nvc) and the [kev-cam fork](https://github.com/kev-cam/nvc) this
  stack runs on
- [Icarus Verilog](https://github.com/steveicarus/iverilog) and the
  [kev-cam fork](https://github.com/kev-cam/iverilog) with the VHDL backend
- [GHDL](https://github.com/ghdl/ghdl) ([kev-cam fork](https://github.com/kev-cam/ghdl)), built by
  `build_stack.sh` as the reference VHDL simulator
- [Verilator](https://github.com/verilator/verilator), a vamos engine and the comparison point
- [Yosys](https://github.com/YosysHQ/yosys), behind `gen_statemachine` and `--accel`
- [Xyce](https://xyce.sandia.gov) and the [kev-cam fork](https://github.com/kev-cam/xyce) with the
  co-simulation patches and PyMS
- [VACASK](https://codeberg.org/arpadbuermen/VACASK) and the
  [kev-cam fork](https://github.com/kev-cam/VACASK), the default analog engine, with openvaf-r
- [ngspice](https://ngspice.sourceforge.io), a bfit driver and benchmark engine

## References

- **[P1800 Proposals for Mixed-UDN Support](docs/P1800%20Proposals%20for%20Mixed-UDN%20support.pdf)** (Kevin Cameron) - IEEE P1800 SystemVerilog proposal for mixed user-defined net types, defining the driver/receiver model, nature/discipline semantics, connect-modules, and resolution strategies that inform this project's architecture. Key concepts:
  - Drivers and receivers as first-class constructs with `.driver[i].value`, `.waveform`, `.mine`, `.find()`
  - Nature-labeled UDN structs (`potential Voltage:`, `flow Current:`)
  - Resolvers as simulation artifacts in "resolver space"
  - No-MAR (per-receiver) resolution for performance
  - Arena resolution for RF/free-space modeling

## License

GPL-3.0-or-later (see `COPYING`), **except `bfit/`**, which is under the
PolyForm Noncommercial License 1.0.0 (see `bfit/LICENSE`). `LICENSE` has
the details, including the third-party test fixtures under `tests/` that keep
their own licences. Revisions published before this change were "GPL v2+".

## Author

Kevin Cameron

---

*"Leverage VHDL's strengths where SystemVerilog falls short."*
