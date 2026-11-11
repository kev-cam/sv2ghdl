# `--accel`: compiled single-cycle acceleration in nvc

How RTL acceleration works in the sv2ghdl / nvc toolchain, how to drive it, and where it is
measured. Status as of 2026-11; the June 2026 revision of this file is summarised under
"History" at the end.

## What it does

A normal nvc simulation interprets the design. With `--accel`, nvc walks the elaborated hierarchy
after reset, picks subtrees that pass an admission gate, synthesizes each one with yosys into a
**single-cycle state machine in C** (`sv2ghdl/yosys/gen_statemachine.cpp`), compiles that to a
shared object, and **installs it into the running model** in place of the interpreted processes
of that subtree. The testbench and everything the gate declines stay interpreted. A subtree that
fails anywhere in the pipeline is left alone: the fidelity gates are designed so that a wrong but
parseable model never silently replaces the interpreter.

## How to trigger it

| Trigger | Notes |
|---------|-------|
| `nvc -r --accel <top>` | direct; `--accel` takes no argument |
| `NVC_ACCEL=<anything but 0 or empty>` in the environment | the equivalent of `--accel`, so unmodified scripts and test suites opt in; `NVC_ACCEL=auto` and `NVC_ACCEL=1` mean the same thing |
| vamos: `NVC_ACCEL=auto ./simv …`, `NVC_ACCEL=auto xsim …` | the `vcs` and `xsim` personalities pass the environment through to nvc (VAMOS_GUIDE.md §13) |
| `vvp --accel` through the `vvp` wrapper | `bin/vvp-sv2ghdl` forwards `--accel` and lifts its 30 s test-suite timeout |

The older `vvp --accel=<module>` and `NVC_ACCEL=<module>` forms still enable acceleration, but
the module name is not consumed: the wrapper exports it as `NVC_ACCEL_TOP`, which nvc does not
read. Scope selection is done with nvc's own knobs (`NVC_ACCEL_ONLY`, `NVC_ACCEL_SKIP`,
`NVC_ACCEL_SKIP_SCOPE`, `NVC_ACCEL_SKIP_TREE`, `NVC_ACCEL_EXCLUDE`; see `src/rt/model.c` in the
nvc fork for their exact matching).

## The pipeline

```
elaborated design (nvc -r --accel)
   │  accel_auto()            nvc/src/rt/model.c, called from nvc.c after reset
   │  accel_scan_scope()      walk the hierarchy top-down; admit or decline each subtree
   ▼
synthesizable Verilog for the admitted subtree
   │  source A: the original Verilog, found through the `nvc_verilog_src` attribute
   │            that iverilog's VHDL backend puts on every translated unit
   │  source B: NVC_ACCEL_FROM_VHDL=1, regenerate Verilog from the elaborated VHDL
   │            with vhdl2vlog (nvc/src/vhdl2vlog.c); any unhandled construct declines
   ▼
yosys (libyosys) -> gen_statemachine -> aj_<chunk>.c      one C function per clock cycle
   │  NVC_ACCEL_CC (default gcc, -O3) -> aj_<chunk>.so, in the accel cache directory
   ▼
accel_install_subtree()      dlopen the .so, bind its boundary pins to nvc nets, swap the
                             subtree's evaluation for the compiled step
```

With `NVC_ACCEL_MERGE=1` (together with `NVC_ACCEL_FROM_VHDL=1`) the scan first collects the
admitted candidates and then installs **one merged chunk per clock-domain group**
(`aj_try_merge_install`), falling back to per-chunk installation on any failure. This is the mode
the fused VeeR-EH2 runs use.

The cache directory is `NVC_ACCEL_CACHE_DIR`, else `$XDG_CACHE_HOME/nvc/accel`, else
`~/.cache/nvc/accel`, else a private `mkdtemp` under `/tmp` (never a shared world-writable path,
because the directory is dlopen'ed from). `NVC_ACCEL_NO_CACHE=1` rebuilds every chunk.

## Admission and the design rules

Two counting gates decide whether a subtree is worth compiling, both keyed by
`NVC_ACCEL_MIN_MODULES` (default 8): the subtree needs at least that many instance scopes and at
least that many distinct `(entity, generics)` variants. `NVC_ACCEL_PER_INSTANCE=1` chunks per
instance instead (set `NVC_ACCEL_MIN_MODULES=1` with it); it is known to be wrong for parallel
chunk topologies (`regress/accel/README.md`, bug 2).

What the VHDL-source path declines, from `regress/accel/README.md`: `for … generate` scopes,
component instantiation (direct `entity work.X` only), ports that are not `std_logic` or
`std_logic_vector`, `assert`/`report`/procedure calls/PSL inside the chunk, more than 4096 bits per
boundary port or 4096 boundary pins. The `regress/accel/opt_asserts.sh` guards (ck52 to ck65)
pin down constructs that once produced a wrong but parseable model and must keep declining or
keep translating correctly.

## Knobs

The ones the scripts and documents in this repository use:

| Variable | Meaning |
|---|---|
| `NVC_ACCEL` | enable (any non-empty value other than `0`) |
| `NVC_ACCEL_FROM_VHDL` | allow the vhdl2vlog source mode (native VHDL designs) |
| `NVC_ACCEL_MERGE` | one merged chunk per clock-domain group |
| `NVC_ACCEL_PER_INSTANCE`, `NVC_ACCEL_MIN_MODULES` | admission, above |
| `NVC_ACCEL_CC` | the C compiler for the generated code (the benchmark suite's -O0 mistake was made here) |
| `NVC_ACCEL_CACHE_DIR`, `NVC_ACCEL_NO_CACHE` | the chunk cache |
| `NVC_ACCEL_VERIFY`, `NVC_ACCEL_VERIFY_X`, `NVC_ACCEL_VERIFY_TRACK` | run the compiled chunk against the interpreter and report divergence |
| `NVC_ACCEL_JIT_DEBUG`, `NVC_ACCEL_SMDUMP`, `NVC_ACCEL_RTLIL` | keep or dump the intermediate C, state machine and RTLIL |

The fork reads about ninety `NVC_ACCEL_*` variables in all (`grep -o 'getenv("NVC_ACCEL[A-Z_0-9]*")' src/rt/model.c`
lists them); the rest are diagnostics and experiment switches for specific mechanisms (clock
gating, X-demotion, seeding, snap-in, capture census) and are documented next to their code.
`NVC_ACCEL_JIT`, which `regress/accel/run.sh` still exports, is no longer read.

## Where it stands

- **Correctness gates.** `regress/accel-gate.sh` runs the accel benchmark designs and the
  translated-design check (`regress/accel/translated.sh`) and must be green before a change is
  merged (`regress/COORD-GATE.md`). The `nvc/regr-accel` block re-runs every passing nvc
  regression test under `NVC_ACCEL=1` and logs accel-only regressions separately
  (`regress/docs/accel.md`).
- **Measured speed, synthetic shapes.** `regress/accel/README.md`: at nvc `3370154`, 19 cases
  over six shapes, 4.1x geomean over the interpreter (worst 1.52x, nothing loses) and 12.2x
  behind Verilator (best 6.2x, worst 30.0x). The same file records why two earlier tables were
  wrong, and that per-chunk cost, not cone depth, is the bottleneck: the compiled cone is nearly
  free, the scheduler plus one bridge crossing per chunk per cycle is what remains.
- **Real cores.** The whole-core VeeR-EH2 run that was 70x slower than the interpreter in July
  2026 (1,366 chunks, most of them single flip-flops) led to the merged-chunk mode. By 2026-08-07
  the fused VeeR-EH2 hello (14 merged chunks) was byte-exact against the interpreter through the
  reference window, with its suite 12/12 (`regress/docs/accel-veer-status.md`); its speed
  against the interpreter and Verilator is the pending parity measurement
  (`regress/verilator_parity_roadmap.md`). The Hazard3 case under vamos
  (`tests/hazard3_mandelbrot/`) runs interpreted unless `NVC_ACCEL` is set.
- **Known shape result.** Accel does a fixed amount of work per cycle regardless of activity
  (the `act_lo`/`act_hi` pair in the benchmark suite), so low-activity designs gain least.

## gen_statemachine beyond nvc

The same yosys-to-C generator produces the models of the GPU instance farm
(`bfit/benchmarks/vhdl/gpu/`, `gpubuild/`): there every GPU thread runs one complete instance of
the compiled design (the Hazard3 SoC at 1,253 cells is the worked case), and the model is
qualified for nvcc by `gpubuild/make_device_model.sh`. `make gen_sm` builds the generator
against `libyosys`.

## History

The June 2026 revision of this file described the prototype: `sm_<design>.so` built by hand,
loaded through the `libresolver.so` plugin (`nvc -r --load`), searched for in `./_accel/`, with
the bet DUT not yet accelerating because the scan only tried leaf scopes through vhdl2vlog and the
Verilog-source mode was not wired. All of that is superseded by the in-process flow above:
`accel_auto` in `model.c` scans top-down, both source modes exist, chunks are cached as `aj_*.so`
in the cache directory, and the benchmark suite measures the result. The SV dynamic-queue
translation that lets real testbenches reach nvc lives in the iverilog fork's `tgt-vhdl`; the
yosys `write_cxxrtl` route remains the robust alternative
(`ldx/examples/rtl-sim/mesh_flow/bet_cxxrtl.sh`).
