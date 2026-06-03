# `--accel` — single-cycle statemachine acceleration

How RTL acceleration works in the sv2ghdl / nvc toolchain, and how to drive it.

## What it does

A normal nvc simulation interprets the design. `--accel` instead compiles the
hot RTL (the DUT) into **one native C function per clock cycle** (a "single-cycle
statemachine"), builds it into a `.so`, and **hot-patches it into the running
nvc simulation** — the sim starts immediately and swaps in the fast model when
the background compile finishes (it never blocks waiting). The testbench stays
in interpreted nvc (it isn't synthesizable); only the DUT subtree is accelerated.

## How to trigger it

Three equivalent ways — all converge on `nvc -r --accel`:

| Trigger | Notes |
|---------|-------|
| `export NVC_ACCEL=auto` | zero-touch: unmodified build scripts get accel through the shims |
| `export NVC_ACCEL=<module>` | accelerate a named DUT module/instance |
| `vvp --accel` / `vvp --accel=<module>` | command-line, through the `vvp` shim |
| `nvc -r --accel <top>` | direct (no shim) |

The shims (`sv2ghdl/shims/{iverilog,vvp,verilator}` → the `*-sv2ghdl` wrappers)
route a build's `iverilog`/`vvp`/`verilator` calls through nvc. `vvp-sv2ghdl`
reads `--accel`/`NVC_ACCEL` and forwards `--accel` to `nvc -r` (and lifts its
test-suite 30s timeout, since accel runs are real workloads). The simulation
**top** (the testbench) is auto-detected by `iverilog-sv2ghdl` as the RTL root
(the module nothing else instantiates) and stored as `TOP_ENTITY` in the sim
dir's `_metadata`.

## Two source modes (per scope)

For each scope nvc tries to accelerate, it needs **synthesizable Verilog** to
feed yosys/gen_statemachine:

1. **Verilog-source mode** — for designs yosys can handle, go back to the
   **original Verilog source**. The translated output is marked up with it:
   `tgt-vhdl` emits `-- Generated from Verilog module <M> (<orig>.sv:<line>)`
   (`ivl_scope_def_file`, see `iverilog/tgt-vhdl/scope.cc`).
2. **VHDL-fallback mode** — if there's no Verilog (or it fails), regenerate
   synthesizable Verilog **from the elaborated VHDL** via `vhdl2vlog`
   (`nvc/src/vhdl2vlog.c`). This is a *fidelity gate*: any unhandled construct
   makes `vhdl2vlog` decline so a wrong-but-parseable model never silently
   corrupts results — the scope just stays in nvc.

## The pipeline

```
DUT .sv source(s)
   │  gen_statemachine <src...> <top> <out.c>      (sv2ghdl/yosys/gen_statemachine.cpp)
   ▼
<out>.c        single-cycle statemachine: sm_reset(), sm_eval(state,in,out)
<out>_nvc.c    NVC-mapped ABI: sm_eval_mapped/sm_init_mapped/sm_reset_mapped,
   │           sm_n_regs, sm_reg_names[]   (for register name↔nvc-net mapping)
   │  gcc -O2 -shared -fPIC -o sm_<design>.so <out>_nvc.c
   ▼
sm_<design>.so
   │  loaded by the resolver plugin (nvc -r --load=.../libresolver.so)
   ▼
nvc/lib/sv2vhdl/resolver.c  — dlopen the .so, map sm_reg_names[] to nvc nets,
   and (when enough map) swap the scope's eval to the statemachine (vtable swap).
   Looks for sm_<design>.so in ./_accel/ or cwd.
```

gen_statemachine accepts **multiple input files** (`<a.sv> <b.sv> … <top> <out.c>`),
so a multi-module DUT compiles straight from its original sources — no
concatenation. It models SystemVerilog queues, `$size`, push_back/pop_front,
`$urandom`, etc. (see the dynamic-queue work below) so real testbenches translate.

## nvc-side entry points

- `nvc/src/nvc.c` — the `--accel` run option; calls `accel_auto()` after reset.
- `nvc/src/rt/model.c`:
  - `accel_auto()` — walk the elaborated hierarchy.
  - `accel_scan_scope()` — per scope: load a cached `accel-mod_<m>-arch_from_verilog.so`,
    else pick a source (Verilog mode / `vhdl2vlog` fallback) and `accel_bg_compile`.
  - `accel_bg_compile()` — runs `gen_statemachine … && gcc -shared … _nvc.c`,
    via the `smak` build server if present (background), else synchronous.
- `nvc/lib/sv2vhdl/resolver.c` — the `--load` plugin that loads `sm_<design>.so`
  and does the register-mapped hot-swap (`ACCEL_DIR="_accel"`, `g_design_name`).

## Current status (2026-06-02)

WORKING:
- gen_statemachine: segfault fixed, **multi-file input**, correct single-cycle C
  for the bet DUT (76 comb cells, 12 registers); validated 10M transfers.
- Shim trigger: `NVC_ACCEL` env + `--accel`/`--accel=<mod>` in `vvp-sv2ghdl`;
  `iverilog-sv2ghdl` auto-detects the sim top (was wrongly picking a leaf).
- `nvc -r --accel` reaches the scan and runs the two-mode mechanism.

NOT YET WORKING (open):
- **The bet DUT doesn't actually accelerate yet.** `accel_scan_scope` uses the
  *VHDL* loc as the source and only attempts **leaf** scopes via `vhdl2vlog`,
  which **declines on the sv2vhdl logic3d helper primitives** (`sv_and_behavioral`
  …); the DUT `a_plus_b` is a *non-leaf* so it's skipped ("no source"). Verilog-
  source mode (read the markup → original `.sv`) is **not wired** in this path.
- **Named-module path** (`--accel=<module>`): recover the module's original
  source set from the markup and `accel_bg_compile` the whole subtree (the
  gen_statemachine multi-file support is now in place for this).
- **Register mapping**: the resolver maps `sm_reg_names[]`↔nvc nets; historically
  "N/M mapped → not activating". Needs the names to line up to actually swap.
- "Work down from the top, take the first scope that compiles" auto policy
  (current scan is bottom-up leaf-only).

## Run the bet through accel (example)

```bash
export PATH=/usr/local/src/sv2ghdl/shims:/usr/local/bin:$PATH
export SVX_STD=2040 NVC_ACCEL=auto          # or: vvp --accel=a_plus_b_using_wrapped_fifos
cd .../yuri_challenge
iverilog -g2012 -o vsim tb_benchmark.sv a_plus_b_using_wrapped_fifos.sv \
         ff_fifo_wrapped_in_valid_ready.sv flip_flop_fifo.sv
vvp vsim
```

Direct gen_statemachine (no nvc), for reference / debugging:
```bash
cd .../yuri_challenge
gen_statemachine a_plus_b_using_wrapped_fifos.sv ff_fifo_wrapped_in_valid_ready.sv \
                 flip_flop_fifo.sv a_plus_b_using_wrapped_fifos /tmp/sm.c
```

## Related

- SV dynamic-queue translation (so real testbenches reach nvc/accel) lives in
  `iverilog/tgt-vhdl` — see that repo's history (commit `0305993`).
- The robust alternative accel is yosys `write_cxxrtl` — see
  `ldx/examples/rtl-sim/mesh_flow/bet_cxxrtl.sh` (+ `bet_drv.cc`).
