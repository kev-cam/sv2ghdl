# hazard3: the Verijit Hazard3 Mandelbrot test case

This directory turns the test case published by the Verijit project,
[verilator-hazard3-mandelbrot-testbench](https://github.com/verijit/verilator-hazard3-mandelbrot-testbench),
into a regression suite that Verilator, Icarus Verilog and vamos (sv2ghdl + nvc) all run the same
way, and adds an opt-in block that runs the original benchmark under Verilator.

## What the test case is

Upstream is a small SoC: Luke Wren's [Hazard3](https://github.com/Wren6991/Hazard3) RISC-V core
(RV32IMAC, the `Hazard3` submodule at v1.1.1) with two AHB-Lite SRAMs, one for instruction fetch and
one for loads and stores, each 1 << 24 words. The firmware, `sw/mandelbrot.c`, computes a 1024x1024
fixed-point (8.24) Mandelbrot image with up to 256 iterations per pixel into a local array on the
stack, then executes EBREAK. `sw/rv32.c` supplies `_start` (which calls `main` without setting up a
stack pointer: sp starts at 0 and the stack wraps to the top of the data RAM), `memset`, and an
empty `used_image()`.

`load_elf.py` (pyelftools) splits the ELF file into the two `$readmemh` preload images and fills
the three `${...}` placeholders of `soc.tmpl.v` to make `mandelbrot_10.v`. The harness,
`main_verilator.cpp`, toggles the clock until the SoC's `finished` output rises, prints the cycle
count and MCycles/s, and writes `output.ppm` from the data RAM (word 15728639 on). `finished` is
set from a hierarchical reference into the core (`core.core.fd_cir`, the current instruction
register, equal to EBREAK), with a non-Verilator `(* keep, hierconn *)` declaration of it.

[Verijit](https://verijit.com) is a Verilog simulator built on just-in-time compilation, by Can
Joshua Lehmann and CF Bolz-Tereick; its site claims up to 100x over existing simulators on the same
hardware, cycle-accurate. It is not available to run: on 2026-10-03 the site offered no download,
sources or licence (it invites processor design teams and investors to get in touch), and the
`verijit` GitHub organisation held only this test case. Upstream's README reports, on an AMD
Ryzen 7 PRO 7840U with 32 GB RAM running Ubuntu 24.04:

| Simulator | MCycles/s | max RAM used in MB |
|-----------|----------:|-------------------:|
| verilator |       3.6 |                133 |
| verijit   |     766.8 |                449 |
| ratio     |      213x |              0.30x |

(quoted from upstream's README.md at commit 9e76830, by the verilator-hazard3-mandelbrot-testbench
authors.)

## Why a portable variant

The upstream harness runs only on Verilator: it is C++, it reads the image out of Verilator's
internal memory array, and the SoC's `finished` depends on a hierarchical reference whose
declaration, `wire [31:0] core.core.fd_cir;`, Icarus rejects ("'core' has already been declared
in this scope"). Two 2^24-word RAMs are also far too big for a 4-state or translated simulation,
and upstream relies on Verilator's `--x-initial fast` zeroing the register file (sp = 0). So the
suite builds a variant of the same design and software that every engine runs identically, with
no hierarchical references and no C++:

- **SoC** (`gen_soc.py`, run on upstream's `soc.tmpl.v` at test time; only the fragments it
  matches are stored here). The changes, each checked against the pinned template and recorded in
  the header of the generated `soc_portable.v`:
  1. new outputs `tohost_valid` and `tohost_data`;
  2. `${reset_vector}` filled with the ELF entry point, as `load_elf.py` does;
  3. `RESET_REGFILE` 0 -> 1, so a 4-state simulator starts with sp = 0 as Verilator does;
  4. instruction RAM depth 1 << 24 -> the variant's (2048 words);
  5. data RAM depth 1 << 24 -> the variant's (2048 to 4096 words: the ELF-header words at the
     bottom, the TOHOST word, and the stack, which wraps from address 0 to the top);
  6. `${i_ram_preload}` filled with this directory's `variants/<v>/i_ram.hex`;
  7. `${d_ram_preload}` filled with `variants/<v>/d_ram.hex`;
  8. the hierarchical `finished` logic removed (`finished` stays a port, tied low);
  9. a TOHOST snooper: an AHB-Lite monitor on the data port. A write to `0x00001000` is noted in
     its address phase; in its data phase (the next cycle with `d_hready` high) `d_hwdata` is
     captured into `tohost_data` and `tohost_valid` pulses for one cycle. The RAM still performs
     the write (it decodes only the low address bits; nothing else uses that word).

  Every engine compiles the SoC with `SIM` defined, so `sram_sync.v` zero-fills both RAMs before
  the preload, as Verilator's memories start at zero.
- **Firmware runtime** (`fw/rv32_tohost.c`, in place of `sw/rv32.c`). `mandelbrot.c` is compiled
  unchanged from the upstream checkout. The runtime is upstream's `rv32.c` (same `_start`, EBREAK,
  types, `memset`) except that `used_image()`, called once the image is complete, streams it to
  TOHOST:

  | words | content |
  |---|---|
  | 2 | header: SIZE, MAX_ITERS |
  | SIZE x SIZE | the pixels, row-major (`0x00rrggbb`, each below 2^24) |
  | 1 | checksum: FNV-1a over the pixel words, bit 31 cleared |
  | 1 | DONE marker `0xffffffff`, the only word with bit 31 set |

- **Preload images** (`elf2hex.py`): `load_elf.py` re-implemented with the standard library (no
  pyelftools). It loads every program header as `load_elf.py` does (segments with PF_X to the
  instruction image, all others to the data image, bytes OR-ed into little-endian words, written
  densely from word 0) and has the same template mode. Its output was checked byte for byte
  against `load_elf.py` with pyelftools 0.33, on upstream's committed firmware and on every
  variant.
- **Testbench** (`tb/tb_hazard3.v`): drives the clock in two phases per cycle (low, high) as
  `main_verilator.cpp` does, counts rising edges, prints each captured word as
  `TOHOST <8 hex digits>`, prints `HAZARD3 DONE cycles=<n> words=<n>` and calls `$finish` at the
  DONE marker, and gives up with `HAZARD3 FAIL cycle cap ...` after `CYCLE_CAP` cycles.
- **Golden outputs** (`variants/<v>/golden.tohost`): the same `mandelbrot.c`, compiled natively by
  gcc with `fw/native_tohost.c` (upstream's `sw/native.c` with a `used_image()` that prints the
  same TOHOST lines), at -O0 and at -O2 (the two must agree), and run on the host. No simulator
  is involved.

A test passes only when the engine's `TOHOST` lines equal the golden file exactly and the cycle
count equals the variant's `ref_cycles` in `variants.json` (the count Verilator and Icarus agree
on; a run that gets the image right in a different number of cycles has mis-simulated the
pipeline).

## Variants

`build_firmware.py` builds the variants listed in its `VARIANTS` table. The firmware spends about
49 cycles per Mandelbrot iteration and 42 per pixel, so MAX_ITERS is kept low to keep runs short
under Icarus and vamos:

| variant | SIZE | MAX_ITERS | TOHOST words | cycles (ref) | i_ram / d_ram words |
|---|---:|---:|---:|---:|---|
| `mandel8_i16` | 8 | 16 | 68 | 35720 | 2048 / 2048 |
| `mandel16_i8` | 16 | 8 | 260 | 95038 | 2048 / 2048 |
| `mandel32_i4` | 32 | 4 | 1028 | 247044 | 2048 / 4096 |

SIZE_LOG2 = 6 is not included: at about 0.6 to 1.2 million cycles it would take 5 to 10 minutes
under vamos (about 2000 cycles/s here) for little extra coverage.

The firmware is linked at address 0 (`-Wl,--image-base=0`) instead of lld's default 0x10000, so
the dense preload images stay at about 1200 lines; the code is otherwise what upstream's
`sw/Makefile` builds (same clang flags).

## Setting up

The suite needs the upstream checkout, pinned in `UPSTREAM` (upstream 9e76830, Hazard3 8af99293).
`setup.sh` makes it, as upstream's README describes:

```sh
sh tests/hazard3_mandelbrot/setup.sh [DEST]
#   git clone --depth 1 https://github.com/verijit/verilator-hazard3-mandelbrot-testbench DEST
#   git -C DEST submodule update --init --depth 1 Hazard3      (then checks both commits)
```

DEST defaults to `$HAZARD3_MANDELBROT_DIR`, else `<src root>/verilator-hazard3-mandelbrot-testbench`
when `<src root>` (`$SV2GHDL_SRC_ROOT`, default `/usr/local/src`) is writable, else
`$HOME/verilator-hazard3-mandelbrot-testbench`. The regression harness looks in the same places
(`Regress::Tools::hazard3_mandelbrot_dir`) and runs `setup.sh` itself when it finds no checkout.
A checkout at other commits is an error.

Tools: python3 (3.9+, standard library only); for the blocks, Verilator 5 (`--binary --timing`),
Icarus Verilog, or vamos with the build-area nvc and iverilog. Rebuilding the firmware also needs
clang and ld.lld with the RISC-V target (clang-19/ld.lld-19 are looked for first) and gcc.

## Running

With the regression harness (`regress/`, from WSL):

```sh
cd regress
./regress run hazard3/verilator hazard3/iverilog hazard3/vamos --notes "hazard3 ..."
./regress run hazard3/iverilog --filter mandel8          # variants whose name contains mandel8
                                                          # (comma-separated; none matching = error)
./regress run hazard3/bench-verilator --notes "hazard3 bench"   # opt-in: ~21 min
```

| block | engine | what it runs |
|---|---|---|
| `hazard3/verilator` | Verilator | `verilator --binary --timing -O3 -DSIM -DCYCLE_CAP=<n> --timescale 1ns/1ps --top-module tb`, then `obj/Vtb` |
| `hazard3/iverilog` | Icarus | `iverilog -g2012 -DSIM -DCYCLE_CAP=<n> -s tb`, then `vvp -n` |
| `hazard3/vamos` | vamos | `vcs -full64 -sverilog -timescale=1ns/1ps +define+SIM +define+CYCLE_CAP=<n> -top tb -o simv` (through `shims/vcs`), then `./simv` |
| `hazard3/bench-verilator` | Verilator | opt-in; upstream's own benchmark, below |

Each portable block runs every variant as one test. The message of each result gives the cycle
count, the compile and run wall times and the simulated cycles per second (for Verilator the short
variants mostly measure start-up; the benchmark block measures throughput). The cycle cap
(`CYCLE_CAP`) is twice the reference plus 100000, or the manifest's worst-case `cycle_cap` when no
reference is recorded. Work directories are `regress/out/run-<n>/hazard3/<block>/<variant>`, or
under `$HAZARD3_WORKDIR/run-<n>/` when that is set (a local disk speeds up the Verilator builds).
`regress run` with no block names runs the three portable blocks (with every other ready block)
but not the benchmark.

Under `regress run` the blocks run inside the smak dispatcher, which exports `MAKE=smak` and its
job-server variables. The adapter starts every step with those (and GNU make's `MAKEFLAGS`)
removed and `MAKE` pointing at GNU make: `verilator --binary` builds with `$MAKE`, smak cannot
build Verilator's generated makefiles, and a recursive smak inside the dispatcher hangs.

Environment: `HAZARD3_MANDELBROT_DIR` (the upstream checkout), `HAZARD3_WORKDIR` (work directory
root), `HAZARD3_BENCH_CXX` (the benchmark's C++ compiler), `HAZARD3_SUITE_DIR` (this directory, by
default found next to the harness), `VAMOS_VCS` (default `<src root>/sv2ghdl/shims/vcs`), `PYTHON3`,
and the harness's usual `VERILATOR`, `IVERILOG`, `VVP`, `NVC` and `SV2GHDL_SRC_ROOT`. The vamos
block pins vamos to the same nvc and iverilog (`VAMOS_NVC`, `VAMOS_IVERILOG`).

By hand, for one variant (`U` = the upstream checkout, `H` = this directory):

```sh
python3 $H/gen_soc.py --template $U/soc.tmpl.v --variant mandel16_i8 -o soc_portable.v
# upstream's HAZARD3_FILES list (the SRAM models and the Hazard3 sources)
F=$(awk -v U=$U '/^HAZARD3_FILES :=/ {on=1; next} on && NF == 0 {exit} on {print U "/" $1}' $U/Makefile)
SRC="$H/tb/tb_hazard3.v soc_portable.v $F"
iverilog -g2012 -DSIM -s tb -I$U/Hazard3 -I$U/Hazard3/hdl -o sim.vvp $SRC && vvp -n sim.vvp > run.log
grep '^TOHOST ' run.log | cmp - $H/variants/mandel16_i8/golden.tohost && grep DONE run.log
```

The other engines take the same files and defines: `verilator --binary --timing -DSIM
--timescale 1ns/1ps -Wno-fatal --top-module tb -I... $SRC`, or `vcs -sverilog -timescale=1ns/1ps
+define+SIM +incdir+... -top tb $SRC` and `./simv`.

## The benchmark block

`hazard3/bench-verilator` builds upstream's `main_verilator` as its Makefile does, in the block's
work directory, from upstream's committed firmware `sw/bin/mandelbrot_rv32imac`:

1. `elf2hex.py --template soc.tmpl.v sw/bin/mandelbrot_rv32imac mandelbrot_10.v` (what the
   Makefile's `load_elf.py` step does);
2. `verilator --cc --exe -j 1 -O3 --x-assign fast --x-initial fast --no-assert --compiler clang
   main_verilator.cpp -IHazard3/ -IHazard3/hdl/ mandelbrot_10.v <HAZARD3_FILES>`;
3. in `obj_dir`: `make OPT_FAST="-O3 -march=native --std=c++20" -f Vmandelbrot_10.mk`, with
   `CXX` and `LINK` set to the compiler `Regress::Tools::hazard3_bench_cxx` picks
   (`$HAZARD3_BENCH_CXX`, else clang++-19, clang++, g++; with g++ Verilator gets `--compiler gcc`).
   Debian's `verilated.mk` defaults to g++, so upstream's Makefile run unchanged here would compile
   with g++ despite `--compiler clang`;
4. runs `obj_dir/Vmandelbrot_10` (unchanged `main_verilator.cpp`, the original SoC: the
   hierarchical `finished`, RESET_REGFILE 0, 2^24-word RAMs).

The differences from running upstream's Makefile are only: absolute paths, the work directory,
the explicit C++ compiler and `make -j4`. PASS means the run finishes, `output.ppm` has the md5
of the native golden image (`variants.json` `bench.ppm_md5`: `mandelbrot.c` with its defaults,
compiled natively, written as `main_verilator.cpp` writes the P6 file), and the cycle count equals
`bench.ref_cycles` when that is recorded. The message records the cycle count, upstream's
MCycles/s figure and the wall time.

## Measured here

AMD Ryzen Threadripper PRO 5955WX under WSL2, other jobs running; Verilator 5.032, Icarus
Verilog 13.0 (devel), nvc 1.19-devel, vamos 0.1.0 (sv2ghdl 13e71da); 2026-10-03, regress runs
108 to 110. Every variant passed on every engine, with the same cycle count. Compile and run are
wall times; the work directories were on the Windows file system (`/mnt/c`), which roughly
doubles the Verilator and vamos compile times against a Linux disk.

| variant | cycles | Verilator compile / run | Icarus compile / run (cycles/s) | vamos compile / run (cycles/s) |
|---|---:|---|---|---|
| `mandel8_i16` | 35720 | 15.0 s / 0.12 s | 0.2 s / 8.5 s (4.2 k) | 14.9 s / 16.8 s (2.1 k) |
| `mandel16_i8` | 95038 | 15.0 s / 0.12 s | 0.2 s / 23.3 s (4.1 k) | 14.6 s / 42.7 s (2.2 k) |
| `mandel32_i4` | 247044 | 15.1 s / 0.22 s | 0.2 s / 59.5 s (4.2 k) | 14.0 s / 116.6 s (2.1 k) |

Upstream's 1024x1024 benchmark (`hazard3/bench-verilator`, verilator --compiler clang with
clang++-19, OPT_FAST -O3 -march=native): it finished at cycle 4637655132 in 1274 s, 3.64 MCycles/s
by upstream's own count, and wrote the native golden image (md5 693d2391e979a114a82af00b3e64e54c).
That matches upstream's Verilator figure of 3.6 MCycles/s on its Ryzen 7 PRO 7840U; Verijit's
766.8 MCycles/s could not be checked. The cycle count is recorded as `bench.ref_cycles`.
Simulation speed on this design, then: Verilator about 3.6 million cycles/s (this benchmark),
Icarus about 4.1 thousand and vamos about 2.1 thousand (the portable variants).

## Rebuilding the firmware and goldens

```sh
python3 tests/hazard3_mandelbrot/build_firmware.py [--upstream DIR] [--keep DIR]
```

compiles each variant (the unchanged upstream `sw/mandelbrot.c` with `fw/rv32_tohost.c`
force-included, `-DSIZE_LOG2`, `-DMAX_ITERS`, upstream's `sw/Makefile` flags, `--image-base=0`),
writes `variants/<v>/{i_ram,d_ram}.hex` with `elf2hex.py`, makes the golden output natively, and
rewrites `variants.json` (RAM depths, reset vector, cycle cap, compilers, SHA-256 of each file,
and the benchmark's golden md5). A variant's `ref_cycles` is kept while its images are unchanged;
when they change, run the verilator and iverilog blocks and record the count they agree on:

```sh
python3 tests/hazard3_mandelbrot/build_firmware.py --set-ref mandel16_i8=95038
```

The images in this directory were built with Ubuntu clang 19.1.7 / LLD 19.1.7 and gcc 15.2.0
(`variants.json` records the exact versions).

## Files and licences

| file | what | origin and licence |
|---|---|---|
| `UPSTREAM` | pinned upstream commits | sv2ghdl, GPL-3.0-or-later |
| `setup.sh` | clones the upstream checkout | sv2ghdl, GPL-3.0-or-later |
| `build_firmware.py` | rebuilds `variants/` and `variants.json` | sv2ghdl, GPL-3.0-or-later |
| `variants.json` | the variants' manifest (generated) | sv2ghdl, GPL-3.0-or-later |
| `gen_soc.py` | generates the portable SoC from `soc.tmpl.v` | Apache-2.0; embeds fragments of upstream's `soc.tmpl.v` |
| `elf2hex.py` | standard-library `load_elf.py` | Apache-2.0; derived from upstream's `load_elf.py` (Copyright (c) 2025 Can Joshua Lehmann) |
| `fw/rv32_tohost.c` | firmware runtime | Apache-2.0; derived from upstream's `sw/rv32.c` |
| `fw/native_tohost.c` | native golden runtime | Apache-2.0; derived from upstream's `sw/native.c` |
| `tb/tb_hazard3.v` | testbench | Apache-2.0; follows upstream's `main_verilator.cpp` |
| `variants/*/*` | preload images, golden outputs (generated) | Apache-2.0; built from upstream's `sw/mandelbrot.c` (Copyright 2026 Can Joshua Lehmann), see `variants/README` |

Upstream is `https://github.com/verijit/verilator-hazard3-mandelbrot-testbench` at commit
9e768306a3cb03b9894aee609545fe3ed829d6be, licensed under the Apache License 2.0; each derived file
names the upstream file it comes from and says what changed. The Apache-2.0 text is
`LICENSES/Apache-2.0.txt` at the root of this repository, and the repository's `LICENSE` lists
these files as an exception to its GPL-3.0-or-later terms. Nothing of Hazard3 (Apache-2.0,
Copyright Luke Wren) or of upstream's SRAM models (`ahb_sync_sram.v`, `sram_sync.v`: WTFPL,
Copyright Luke Wren) is stored here: the harness compiles them from the upstream checkout.
