# GPU simulation: status and to-do

**Active again since 2026-11-15** (parked 2026-11-13 to 15). The current work is our own two-phase
sLUT engine, programmed from yosys, running on CPU threads and on the GPU, with the 3D-Logic uncertain
plane (section 1.6). The aim is maximum speed for **one** simulation of one design on a large card.
As of 2026-11-16 one simulation of a CPU-core-sized design on an RTX 4090 is still 1.5x to 7x slower
than one CPU thread of the development box. This file records what was established, the decisions taken, and what is left to
do, so that nobody has to re-derive it or repeat the mistake that started the reassessment.

Scope: the GPU instance farm (`bfit/benchmarks/vhdl/gpu/`, `gpubuild/`, `bfit/benchmarks/gpu_farm.md`),
nvc `--accel` (`Accel.md`), and the planned design-as-data ("sLUT") evaluator with 3D-Logic.

## 1. Where things stand

Measured on 2026-11-12/13 on one box in one session (Threadripper PRO 5955WX, 16 cores / 32 threads,
NVIDIA T1000 with 14 SMs, WSL2, Verilator 5.032) unless a line says otherwise. The reports with the
raw logs and scripts are local to that box (section 5), not in this repository. Each report was
checked within the task that produced it (certified checksums, spot checks, known-answer tests);
**a separate independent check of the numbers was planned and not run.**

### 1.1 The earlier farm numbers

- The GPU-side measurements in `gpu_farm.md` and the Hazard3 kit README are genuine. The kit's
  generators reproduce the tables from the committed logs and `check_h3.py` certifies 119 of 119
  Hazard3 FARM lines.
- The multipliers are the problem. Every large "x CPU" figure divides the **aggregate** rate of
  16 thousand to 8 million independent instances on a card (kernel time) by the rate of **one** CPU
  thread. That thread is, depending on the row, an nvc-class VHDL engine, Verilator on a 5-vCPU cloud
  VM that left no logs, a third-party dashboard figure for a different workload, or Verilator running
  a different job. No document has a measured CPU baseline wider than 8 threads.
- Nothing recorded shows **one** simulation finishing faster on a GPU than under Verilator on
  comparable hardware.
- nvc `--accel` on the CPU has not been recorded at or above Verilator: 6x to 30x behind on the
  synthetic shapes (12.2x geomean), while 4.1x geomean ahead of the interpreter.

### 1.2 Like for like: Hazard3 farm SoC, workload `thr` (95,038 cycles, all runs certified)

| what | rate |
| :-- | --: |
| Verilator, one thread, kit build / best build (LTO + PGO) | 3.29e6 / 4.47e6 cycles/s |
| Verilator, 32 processes on all hardware threads, kit / best | 5.85e7 / 7.42e7 instance-cycles/s |
| compiled C model (gen_statemachine), 1 thread / 32 threads | 2.57e6 cycles/s / 3.84e7 instance-cycles/s |
| T1000 farm, one instance | 1.008e5 cycles/s (kernel time) |
| T1000 farm, plateau (3,584 instances or more) | 1.046e8 instance-cycles/s |
| whole 1024x1024 image: T1000 / Verilator 32 processes / C model 32 threads | 59.3 s / 66.3 to 86.9 s / 132.9 s |

- The T1000 at its plateau delivers **1.41x (best Verilator build) to 1.79x (kit build)** of what
  Verilator does on the whole CPU of the same box.
- One instance on a T1000 thread is **33x to 44x slower** than one Verilator thread.
- RTX 4090, rented host, 2026-10-03 (cross-machine: card and CPU were not measured together):
  plateau 5.36e9 instance-cycles/s, i.e. **72x to 92x** this box's all-core Verilator; one instance
  2.70e5 cycles/s, i.e. 12x to 17x slower than one Verilator thread here; whole image 1.38 s by the
  wall time of the command.

### 1.3 What limits one simulation on a GPU

Message passing on the T1000, cost per simulated cycle that is not the chunk's own work (own CUDA
benchmark, every run checked bit for bit against a sequential C reference):

| scheme | cost per cycle |
| :-- | --: |
| kernel launch per cycle | about 7.5 us (38 to 61 us if the host synchronises after every launch) |
| card-wide barrier (`grid.sync()`) | 0.67 to 2.1 us |
| block barrier (`__syncthreads()`), one SM only | 46 to 490 ns |
| barrier-free neighbour handoff between blocks (global memory) | 0.23 to 2.5 us; one hop 177 ns |

- Barrier-free handoff is 0 to 32 % faster than the card-wide barrier up to 224 warps and no better
  when the card is full. One GPU thread executes a dependent integer operation in about 2.6 ns.
- For scale, Verilator's whole cycle takes 0.22 to 0.30 us for Hazard3, 6.2 us for VeeR-EH1 and
  30 to 40 us for VeeR-EH2.
- Handoff correctness is not free: the usual sketch (two slots by cycle parity plus a counter) ended
  in a wrong state in 130 of 204 runs, and separate value and counter words without memory fences in
  29 of 132. One aligned 64-bit word carrying {cycle tag, value}, with back-pressure, was bit-exact
  in all 1,826 timed runs.

Structure of the two cores (own tools, validated on hand-made designs with known answers):

| | Hazard3 `thr` | VeeR-EH1 |
| :-- | --: | --: |
| flip-flop bits in the largest feedback component | 93.1 % | 95.2 % |
| combinational cells on a register-to-register cycle | 99.4 % | 99.3 % |
| logic that only observes (no loop depends on it) | 0.5 % of cells | 0.5 % of cells |
| iteration bound / longest register-to-register path, word-level cells | 69 / 70 | 88 / 108 |
| the same in AND levels | 533 / 538 | 92 / 169 |
| largest single register cone, share of all cells | 36.5 % | 18.8 % |
| most parallelism that chunking by register cones can give | 2.74x | 5.32x |
| fewest registers on a cycle through two chunks | 2 | 2 |

- There is almost no feed-forward pipeline to decouple, no slack between chunks, and the inputs of
  the core's chunks change in nearly every cycle. The parallelism that exists is **width inside one
  clock cycle** (for VeeR-EH1 thousands of gates per level), which a design-as-data evaluator uses.
- Idle blocks are the exception. Hazard3's 32x unrolled divider holds 44 % of the gates and every
  level above 74, sets the iteration bound, and is never started by `thr`. In VeeR-EH1 the debug and
  DMA controllers' state never changes during the measured slice.
- Not measured: a design made of several loosely coupled blocks (several cores, peripherals behind
  bus bridges). That is where chunk-level decoupling could have room.

### 1.4 Logic depth is mostly a synthesis choice

| | Hazard3 `thr` | VeeR-EH1 | VeeR-EH2 |
| :-- | --: | --: | --: |
| AND nodes / levels, plain structural hashing | 34,161 / 538 | 202,825 / 169 | 587,051 / 672 |
| after a depth-oriented ABC script | 51,142 / 367 | 186,813 / 40 | 530,046 / 55 |
| with GEM's published yosys recipe | 30,482 / 2,208 | 210,357 / 128 | not run |
| Verilator here, 1 thread / `--threads 4` | (section 1.2) | 161 / 112 kHz | 25.1 / 33.2 kHz |

Hazard3 with a sequential divider (`MULDIV_UNROLL = 1`, a different configuration, sized only) is
21,310 / 74, and 20,386 / 44 after the depth script.

### 1.5 GEM as a yardstick (stopped)

NVlabs GEM (DAC 2025, Apache-2.0) is the published design-as-data GPU simulator: yosys to an
and-inverter graph, interpreted as data, every gate evaluated every cycle. Its paper reports 17,889
to 65,385 cycles/s on an RTX 3090 / A100 for designs of 0.35 to 1.8 million gates.

- GEM at commit 9e913f9b builds without source changes and runs on the T1000.
- Hazard3 `thr` through its published recipe (30,482 gates, 2,208 levels, one partition of about 172
  layers): **5,316 cycles/s** (median of 3 runs of 100,001 cycles). Output equal to the Verilator
  reference (260 words, end marker at cycle 95,038, checksum 47E5C3CA649F77AD) once the power-on
  reset was turned into an input port; GEM's own CPU cross-check passes.
- Limitations met or documented: registers start at 0 and initial values are ignored; 2-state;
  replays a recorded VCD, so no test bench in the loop; bit level only.
- Not done: the depth-oriented netlist, VeeR-EH1 / EH2, any large card.

### 1.6 Our own sLUT engine (2026-11-15/16)

A prototype, **not yet in any repository** (section 5). The design is data: `slutgen.py` turns the
yosys JSON netlist into a program of typed word-level operations plus a name map; the engine settles
the combinational levels and then latches (two phases, double-buffered registers). Every step
executes one operation type. Hazard3 `thr` reproduces the Verilator reference in every configuration
below; VeeR-EH1 (an idle core, no real workload yet) is checked GPU against CPU engine bit for bit.

What exists:

- **Uncertain plane** beside the value plane (about 2x the work). Power-on options: the declared
  default, all certain zero, all certain ones, per name prefix; **report-and-clear**: list what is
  still uncertain at a given cycle, then make it certain.
- **Stop and start**: state copies between the CPU engine and the GPU engine by name, both planes.
- **CPU threads** (`slutpar.py`: partitions with duplicated cones, one barrier per stage).
- **GPU engine** (CUDA, persistent kernel; code and values in shared memory where they fit).
- **Depth pass** (`slutgen.py --depth K`): the 137 (Hazard3) and 164 (VeeR-EH1) levels first measured
  were artefacts of the generator: the unrolled divider as a chain, wiring counted as levels,
  unbalanced AND / OR / multiplexer chains. With K-input table operations, a selector operation and
  fused divider steps: **19 levels** (Hazard3; 39 when every internal name is kept) and **31**
  (VeeR-EH1). The depth-reduced programs are slower on the CPU (1.4x to 5.5x); the CPU keeps the
  original ones.
- **Asynchronous islands**: one synchronous block per SM, cycle-tagged channels in global memory, no
  card-wide barrier; survives injected delays bit-exactly.
- **Speculate and select** (`slutgen.py --spec B`): the cone behind up to B late-arriving bits on a
  longest path is copied for every value of those bits and finished by one indexed select. Hazard3
  19 to 12 levels (the shifter's five select stages become 32 wirings and one select); VeeR-EH1 only
  31 to 28, because its remaining long path carries wide late data (load, forward, add, compares).
  The expanded programs are never more uncertain than the unexpanded ones (identical, or a few bits
  less uncertain), and are slower on the CPU.

One simulation, cycles/s, median of 7 runs. CPU rows are the development box (section 1); the 4090
is a rented host, so those rows are cross-machine.

| | Hazard3 `thr` | VeeR-EH1 (idle) |
| :-- | --: | --: |
| Verilator, one thread | 3.3e6 to 4.5e6 | about 1.6e5 |
| sLUT on the CPU, one thread | 5.7e5 (7.6e5 with an activity profile) | 4.0e4 |
| sLUT on the CPU, 8 threads | 1.1x the one-thread figure | 1.59e5 |
| sLUT on the T1000, original program | 1.22e4 | 3.87e3 |
| sLUT on the T1000, depth-reduced | 4.2e4 | 1.38e4 |
| sLUT on an RTX 4090, original program | 2.19e4 | 6.48e3 |
| sLUT on an RTX 4090, depth-reduced | 6.11e4 | 1.68e4 |
| the same with the uncertain plane | 9.5e3 | 1.06e4 |
| RTX 4090, speculate and select, one block | **8.26e4** | 1.82e4 |
| RTX 4090, islands (Hazard3 14, VeeR-EH1 64) | 5.34e4 | **2.69e4** |
| RTX 4090, best with the uncertain plane | 9.9e3 | 1.65e4 (islands) |

- **One simulation still loses on the GPU.** Best 4090 figures against the development box: Hazard3
  6.9x behind one sLUT CPU thread; VeeR-EH1 1.5x behind one thread and 5.9x behind 8 threads or one
  Verilator thread. On the rented host itself (slower cores) VeeR-EH1 on the card is just above one
  CPU thread (2.69e4 against 2.47e4) and 3x behind 8 threads.
- **The two restructurings help different designs** (third 4090 run, 2026-11-16, 66 checks passed):
  speculation gives Hazard3 +33 % and VeeR-EH1 +9 %; islands give VeeR-EH1 +61 % and lose on
  Hazard3; both together on VeeR-EH1 equal islands alone. On the 4090 an island fits one SM's shared
  memory, which it did not on the T1000; free-running islands (no waits) reach 2.94e4, so the channel
  protocol costs about 9 %.
- Fewer levels pay less than proportionally: 19 to 12 levels gave 1.14x on the T1000. Barriers and
  the step loop are 2.5 to 3 us of a 20 to 25 us cycle; the cost model inside one block is not
  settled.
- The time is now levels x (step + barrier), about **1 to 2 us per level**, against 0.1 to 0.6 us for
  a bare step inside one SM. Width is not the limit: the depth-reduced VeeR-EH1 uses about 520 of the
  card's 16,384 lanes per level.
- The card wins on size. Many VeeR-EH1 cores as one program: peak 1.06e10 operations/s at 30.7 million
  operations, about 9 sLUT threads of the development box; the crossover is at 1 to 2 million
  operations.
- Many copies of Hazard3 on the 4090: plateau 4.34e7 instance-cycles/s, below all-core Verilator on
  the development box (5.9e7 to 7.4e7). The interpreting engine is far slower per copy than the
  compiled farm kernel of section 1.2.
- Asynchronous islands, T1000 only, first measurement: on the depth-reduced programs they are 8 to
  11 % **slower** than the best single-block scheme; waiting for neighbours is 1 % (Hazard3) to 11 %
  (VeeR-EH1) of the cycle, so the island's own depth is the bound. On the original VeeR-EH1 program
  they are 1.84x faster. One memory fence per round costs about 1.5 us and cannot be dropped safely.
- Not golden: Hazard3 with every register powered on at certain ones ends in a different checksum;
  not traced.

### 1.7 Phase 2 per engine form, and the FPGA's case (2026-11-16)

Two-phase evaluation keeps the next state apart from the current one until every operation of the
cycle has read it. Two ways avoid a copy: hold the program in two versions, one per register-bank
parity, each reading one bank and latching into the other; or keep one program and select the bank
by address (a pointer, or a parity bit XORed into the address). Where the prototype stands, from the
code in `/opt/gpu-reassess/slut`:

| engine form | phase 2 today |
| :-- | :-- |
| single-thread `slut.c` | memcpy of the d slots onto the q slots (gather and scatter when not contiguous) |
| SMP sections (`slutpar.py`) | two versions of every section, one per parity (`S STAGE THREAD PARITY N`); the latch is `cpne` into the other bank; `par` flips, no copy |
| GPU `k_stream` (farm, one-thread baseline) | per-parity streams for the main, async-reset and phase-2 code; `par` flips each cycle |
| GPU `k_step` (warp programs) | parity-indexed segments, plus an S2B pass copying bank 1 back to bank 0 for every register, with its own grid barrier |
| GPU islands | the latch operation writes q inside the island's shared memory and publishes the token into the ring; the ring, indexed by cycle tag, is the multi-bank |

- The two-version form is the right one for a table engine: a bank pointer there is an XOR or a
  lookup on every register operand (the engine's slot resolver does exactly that, for the mapping
  and memory-write paths only). Compiled x86 code gets a pointer for free through base-plus-
  displacement addressing, so the distinction only bites in the interpreted engine.
- What is left to convert costs little: Hazard3 has 61 register slots, so the single-thread copy is
  488 bytes per plane per cycle, a few percent of a 1.2 us cycle (VeeR-EH1: 1,491 slots, 12 KB per
  plane); `k_step`'s four barriers together are under 1 us of a 119 us Hazard3 cycle, and dropping
  S2B removes one of them. The island kernel needs no change.
- On the FPGA the pointer wins. Code memory is what caps the core count of the ldx mailbox array
  (about 96 cores by URAM, about 128 by LUTs), and a second code image per core halves it. The
  array already has the pointer in hardware: the `addr_mode=1` banked region XORs the cycle parity
  into the address, zero data movement, measured 1.5x (6,158 to 4,115 core cycles per simulated
  cycle). RV32 has only base-plus-12-bit-immediate addressing, so a software pointer would cost an
  add per register access once a core's state passes 4 KB; the address XOR avoids that too. The
  shared-memory experiment in the sLUT report (slots and code in shared memory, 13 to 20 %) ran
  with one code version, which suggests the second version is what did not fit there either.

**Where the FPGA wins: the wiring.** Operations that slice, concatenate, replicate or extend bits
cost a shift and a mask per 64-bit word on a CPU and nothing but wires on an FPGA, at any width;
reductions are a LUT tree. Their share of the programs (operation histograms in `PREFIX.stat`):

| design | operations | slicing (`ext`, `cat*`, `rep`, `sext`) | plus `muxb`, shifts, `copy` | reductions (`anyn`, `rbool`, `rand`, `rxor`, `nonen`) |
| :-- | --: | --: | --: | --: |
| Hazard3 (`blk16`) | 1,244 | 388 (31 %) | 434 (35 %) | 73 (6 %) |
| VeeR-EH1 (`g`) | 29,951 | 5,920 (20 %) | 6,208 (21 %) | 541 (2 %) |

Counts are not time. The profile below ranks the handlers by measured time.

#### The x86 profile (2026-11-16)

Method: a program-counter sampler preloaded into the engine (`pcsamp.c`: a CLOCK_MONOTONIC timer
raises SIGPROF every 100 us, the handler counts the interrupted PC), samples attributed to the
operation handlers through the debug line table of a `gcc -O2 -g -no-pie -march=native -DSLUT_GOTO`
build. One thread pinned to core 8 of the Threadripper PRO 5955WX, gcc 15.2, WSL2; 68k to 76k
samples per Hazard3 configuration (`--reps`), 27k per VeeR-EH1 (`--const 100000`); sampling slows the
run by about 5 %. Every sampled Hazard3 run is golden (CHK0 47E5C3CA649F77AD, DONE0 95038). The
engine's own per-run TSC timer (`-DSLUT_OPTIME`, `--optime`) was run as the cross-check: on EH1 it
agrees with the sampler within 2 to 3 points per type, on Hazard3 it cannot resolve anything,
because a run averages 3 operations (384 runs per 1.8 us cycle, 19 TSC of work per run) and the two
timestamps cost 54 TSC per run. Files: `bench/optime_20261116/` (histograms, logs, `pcs_ranking.md`,
`ranking.md` for the TSC method, scripts).

Share of the cycle by class (the slicing, bit-select and reduction rows are the FPGA's wiring):

| class | Hazard3 all2 | same, uncertain plane | Hazard3 hot (guard blocks) | same, uncertain | VeeR-EH1 | same, uncertain |
| :-- | --: | --: | --: | --: | --: | --: |
| slicing (`ext`, `cat*`, `rep`, `sext`) | 36.6 % | 21.7 % | 31.1 % | 17.8 % | 27.6 % | 23.0 % |
| bit select / shifts (`muxb`, `bmux`, `shl`, `shr`) | 4.7 % | 3.1 % | 1.6 % | 1.3 % | 1.8 % | 1.8 % |
| reductions (`anyn`, `rbool`, `rand`, `rxor`, `nonen`) | 11.7 % | 10.1 % | 10.5 % | 9.1 % | 2.4 % | 2.7 % |
| bitwise (`and`, `or`, `xor`, `not`) | 1.6 % | 1.2 % | 1.7 % | 1.2 % | 44.3 % | 50.0 % |
| compare (`eq`, `ne`, `ult`, `ule`, `sle`) | 10.7 % | 6.7 % | 10.6 % | 8.4 % | 5.3 % | 5.1 % |
| boolean (`land`, `lor`, `lnot`) | 11.7 % | 12.2 % | 11.0 % | 10.2 % | 0.8 % | 0.6 % |
| select (`mux`, `pmux`, `muxf`) | 16.7 % | 12.8 % | 17.4 % | 12.6 % | 13.1 % | 12.8 % |
| arithmetic (`add`, `sub`, `mul`, `neg`) | 3.2 % | 1.9 % | 1.2 % | 0.7 % | 1.2 % | 0.9 % |
| memory read | 1.1 % | 0.7 % | 1.3 % | 0.8 % | 0.8 % | 0.7 % |
| guard-block check (`chk`) | 0.0 % | 0.0 % | 7.0 % | 4.9 % | 0.0 % | 0.0 % |
| phase 2 (latch, memory writes, resets) | 0.9 % | 28.3 % | 5.1 % | 32.1 % | 1.0 % | 0.8 % |
| harness, loader, libc, other | 1.1 % | 1.3 % | 1.5 % | 0.9 % | 1.5 % | 1.6 % |

The twelve most expensive handlers, value plane (share of the cycle; ns per operation from the
share, the plain build's cycle time and the operation count per cycle):

| rank | Hazard3 all2 | share | ops/cycle | ns/op | VeeR-EH1 | share | ops/cycle | ns/op |
| --: | :-- | --: | --: | --: | :-- | --: | --: | --: |
| 1 | `cat` | 15.2 % | 70 | 3.9 | `and` | 25.8 % | 10,092 | 0.64 |
| 2 | `mux` | 10.6 % | 194 | 0.98 | `or` | 13.8 % | 4,710 | 0.74 |
| 3 | `anyn` | 8.2 % | 43 | 3.4 | `mux` | 12.4 % | 3,874 | 0.81 |
| 4 | `cat2` | 7.3 % | 100 | 1.3 | `ext` | 8.7 % | 3,367 | 0.65 |
| 5 | `eq` | 6.6 % | 198 | 0.60 | `cat` | 7.2 % | 323 | 5.6 |
| 6 | `ext` | 6.6 % | 138 | 0.86 | `eq` | 5.1 % | 1,983 | 0.65 |
| 7 | `land` | 5.0 % | 84 | 1.1 | `catf` | 4.9 % | 343 | 3.6 |
| 8 | `lor` | 3.9 % | 58 | 1.2 | `rep` | 3.8 % | 1,298 | 0.74 |
| 9 | `muxb` | 3.7 % | 43 | 1.5 | `not` | 2.6 % | 1,081 | 0.61 |
| 10 | `pmux` | 3.3 % | 17 | 3.5 | `xor` | 2.1 % | 874 | 0.60 |
| 11 | `lnot` | 2.9 % | 78 | 0.67 | `cat2` | 1.6 % | 283 | 1.4 |
| 12 | `add` | 2.8 % | 42 | 1.2 | `cat2f` | 1.2 % | 299 | 1.0 |

What it says:

- **The wiring is the largest class on Hazard3**: slicing, bit select and reductions take 53 % of the
  value-plane cycle (48 % on the guard-block program) from 41 % of the operations. The expensive
  handlers per operation are the multi-piece ones: `cat` 3.9 ns, `anyn` 3.4 ns, `pmux` 3.5 ns against
  0.6 ns for a plain word operation (`eq`, `lnot`, `and`). On EH1 the same class is 32 %, and `cat` /
  `catf` cost 5.6 / 3.6 ns each. These handlers are the ldx candidates: concatenation, extract,
  replicate and reduce-or are wires or a LUT tree in fabric at any width.
- **EH1's time is bitwise `and` / `or` on 64-bit words doing mostly one-bit work**: 44 % of the cycle
  for 10,092 `and` and 4,710 `or` per cycle. The FPGA does those as LUTs at the native width; on a
  CPU the answer is the packing of one-bit nets listed under "considered" in the sLUT report.
- **The uncertain plane's extra cost is one rule, not the latch.** 28 to 32 % of the uncertain Hazard3
  cycle is in `slut_memwr` lines 600 to 605: a memory write port whose address has uncertain bits
  visits every word of the memory and merges uncertainty into each matching one. On this workload
  the stack pointer is uncertain from cycle 14 (section 1.6), so every data-memory write scans the
  memory. The value-plane memory writes cost 0.3 %. Without that scan the uncertain plane costs the
  handlers 1.47x (1.77 us against 2.59 us of handler time per cycle), not the 2.1 to 2.3x reported
  for the whole cycle. Any target needs an O(1) form (mark the memory, or an address range, uncertain
  and resolve on read) or report-and-clear at start-up; a per-word smear is a full scan on BRAM too.
- **Phase 2 proper is under 1 %** of every value-plane cycle; the 5 % on the guard-block program is the
  dirty marking for the blocks (lines 644 to 647), and the block check itself costs 7 %, so gating
  pays about 12 % of the cycle for its 1.33x.

## 2. Decisions

- 2026-10-03: the default type for logic simulation is the 3D family (`logic3d`), 2-state on request.
  The stack is an accuracy-capable framework first; fast digital runs can be federated to Verilator.
- 2026-11-13: the target form for GPU simulation is design as data (sLUT: the netlist is data, one
  common operation per warp, not restricted to single-bit logic). Any restructuring of the RTL must
  keep the behaviour at the external ports exactly; internal timing may change.
- 2026-11-13: GEM is not used directly and the yardstick runs are abandoned. The evaluator is to be
  our own, through `--accel` / yosys, so that it does 3D-Logic.
- 2026-11-13: parked until clevo-lx is back.
- 2026-11-15: restarted. GEM is not run; we take what its code teaches. The engine is two-phase
  evaluation of RTL as sLUT operations programmed from yosys, staying inside the resource limits of
  the chip, and it must run on SMP as well. sLUT shapes are tuned per target (64-bit operations on a
  CPU, shorter on a GPU, extra wide on an FPGA). It is a run-time acceleration option that can be
  stopped and restarted: a mapping between the regular simulation's data and the accelerator's lets
  state be copied back on demand for debug, with no cost while running.
- 2026-11-15: uncertainty at start-up is an option. It varies by type and the default comes from the
  type declaration; all-zero and certain is the common no-uncertainty case; report-and-clear is the
  safer route. Not to be optimised away unless speed falls far behind: the uncertain plane is what
  this stack offers over Verilator / VCS.
- 2026-11-16: the goal on the GPU is maximum speed of a single design, on the largest cards.
- 2026-11-16: blocks stay synchronous inside one SM and talk to each other asynchronously (the RTL
  to asynchronous conversion of the mylex notes, used at block level).
- 2026-11-16: with cores to spare, compute the array of possible results and select one, instead of
  evaluating conditionally (speculate and select).
- 2026-11-16: two-phase evaluation takes the form the target can afford: two versions of the program
  (one per bank parity) on the CPU table engine and the GPU, where code is cheap; one program and a
  bank selected by address on the FPGA, where code memory is the scarce resource (the ldx mailbox's
  cycle-parity XOR). Section 1.7.
- 2026-11-16: the FPGA's case is wide bit-slicing and reductions, done at any width in fabric. The
  routines to move to ldx are chosen from a per-routine time profile of the x86 engine, not from
  operation counts. Section 1.7 (profile taken the same day: concatenation, extract, replicate,
  reduce-or first).

## 3. To do

### 3.0 Next, in order

- [x] **Speculate and select in the generator** (section 1.6).
- [x] Rerun on an RTX 4090 with islands and speculation (section 1.6).
- [ ] Speculate and select at island boundaries: analysed only, not built. No mid-cycle crossing is
      narrow (narrowest 93 bits on Hazard3, 4,948 on VeeR-EH1), and removing every wait gains at
      most 1 % / 11 %. Worth another look only with a partition that has narrow cuts.
- [ ] **VeeR-EH1's long path**: wide late data, where 2^k enumeration does not apply. Needs another
      restructuring (retiming of the compare chain, or speculation on the few values a forward
      select can take).
- [ ] **A partitioner that cuts without duplicating logic**: today's inflates the islands 1.8x and
      gives no usable 128-island partition.
- [ ] A per-step timer in the kernel, to settle where the 12 us of a Hazard3 cycle go.
- [x] **Profile the x86 engine per operation routine** (section 1.7): PC sampler, both designs, both
      planes, cross-checked with the engine's TSC timer on EH1. Wiring and reductions are 53 % of
      Hazard3's cycle, `and` / `or` 44 % of EH1's; the uncertain plane's extra cost is the
      uncertain-address memory write scan, not the latch.
- [ ] **Uncertain write address in O(1)**: mark the memory (or a range) uncertain instead of visiting
      every word (`slut_memwr` lines 600 to 605); 28 to 32 % of the uncertain Hazard3 cycle today, and
      the same scan would hit BRAM on the FPGA.
- [ ] **ldx candidate set from the profile**: `cat` / `cat2` / `catf`, `ext`, `rep`, `anyn` (and `pmux`) as
      CFU instructions on the RV32 cores or as the wide evaluator's first operations; measure them on
      the core (feature matrix, cfu variant) before choosing.
- [ ] Pack one-bit nets on the CPU engine (EH1: 56 % of the operations are `and` / `or` / `not` on
      mostly one-bit values, 44 % of the cycle).
- [ ] Low priority: parity-versioned programs for the single-thread engine and `k_step` (drop the
      memcpy latch and the S2B copy-back with its grid barrier); a few percent at most (section 1.7).
- [ ] Speculation across cycles: an island ahead of its inputs computes the next cycle for both
      values of a one-bit unknown.
- [ ] Cut the per-level cost: one-word packed records, operands gathered in advance, 32-bit lanes.
- [ ] Skip idle blocks inside thread and island sections (the activity profile does this on one
      CPU thread only).
- [ ] `slutgen.py`: shifts wider than 64 bits and `$demux` (these block VeeR-EH2).
- [ ] A real workload and an external reference for VeeR-EH1.
- [ ] Trace the non-golden all-ones power-on run of Hazard3.
- [ ] Review the 17 provisional uncertain-plane rules (arithmetic, uncertain select, memories).
- [ ] Open questions: where the activity profile comes from in a real flow; several clocks in the
      program format; per-type declared defaults from the front end.
- [ ] Bring the prototype into a repository and connect it to nvc (`--accel`, `gen_statemachine`).

### 3.1 Correct the existing documents (independent of everything else)

- [ ] `bfit/benchmarks/gpu_farm.md`, `bfit/benchmarks/vhdl/gpu/hazard3/README.md`,
      `bfit/benchmarks/vhdl_perf.md` (the gpu-farm column) and `docs/VAMOS_GPU_FARM_DESIGN.md`:
      replace every "x one CPU thread" multiplier by the two figures of section 4 (one simulation
      against one thread; the card against the whole CPU), and name the CPU.
- [ ] `gpu_farm.md`: the rule-1 table has a header and no rows; the same numerator carries two
      multipliers (1,450x there, 1,750x in the Hazard3 README); cited raw-log names are not the
      committed file names; "best of 2" against single launches.
- [ ] The remaining items of the audit's list of 16 inconsistencies (section 5), which also touch
      `bfit/benchmarks/sv_perf.md`, `regress/accel/README.md` and `regress/docs/accel-veer-status.md`.

### 3.2 Stage 1: 3D-Logic in the compiled CPU path

Today `--accel` is a value-plane engine: it models 2 states, notes when an uncertain value reaches
a chunk input, and with `NVC_ACCEL_XDEMOTE=1` hands the chunk back to the interpreter.

- [ ] Decide the acceptance rule for the uncertain plane. The value plane can match the interpreter
      exactly. The uncertain plane computed on an optimised netlist is expected not to match bit for
      bit, because logic optimisation changes the structure that uncertainty propagates through. A
      strict match rules out a bit-level netlist.
- [ ] `yosys/gen_statemachine.cpp`: emit the uncertain plane beside the value plane for every cell
      type, with the plane algebra of the nvc fork's `src/rt/l3dplane.h` (AND, OR, XOR, NOT are
      already verified exhaustively against the LUTs); define the rules for arithmetic, multiplexers
      with an uncertain select, and memories with an uncertain address or enable.
- [ ] Bridge in the nvc fork's `src/rt/model.c`: carry the planes across the chunk boundary;
      registers power on uncertain and honour initial values.
- [ ] Verify: extend `NVC_ACCEL_VERIFY` to the planes; `regress/accel`, the fork's `test/accel/l3d*`,
      Hazard3.

### 3.3 Stage 2: GPU evaluator, design as data

Superseded in part by the prototype of section 1.6, which is word-level, not an and-inverter graph,
and already has both planes, the CPU cross-check and a free-running mode. The list is kept for the
integration into `libgsm`.

- [ ] Decide: start from GEM's code (Apache-2.0) or write it inside `libgsm`.
- [ ] And-inverter graph from yosys with a depth-oriented script (section 1.4), registers with initial
      values, RAM blocks.
- [ ] Partitioning and mapping of the graph onto wide bit-parallel layers. This is the one hard
      algorithm; the rest is plumbing.
- [ ] Kernel with two planes. GEM's per-word operation is `(a ^ xa) & ((b ^ xb) | ob)`; the
      uncertain plane is a second formula beside it: `ru = (au | bu) & ~(cert0_a | cert0_b)` with
      `cert0_x = ~xv & ~xu`.
- [ ] Check every netlist bit for bit, both planes, against the stage 1 model.
- [ ] Run modes. `--accel` exchanges pins with nvc every cycle; across the host / GPU boundary that
      costs tens of microseconds per cycle (section 1.3), a ceiling near 20 kHz. The GPU version
      therefore needs a batch or free-running mode, or many instances per card (faults, variability).

### 3.4 Stage 3: coupling and verification on real designs

- [ ] Hazard3 and the VeeR cores end to end against the interpreter, stimulus through nvc.
- [ ] Evaluate idle blocks on demand (Hazard3's divider is the test case).
- [ ] Word-level operations where bit-level depth explodes (the same divider: about 70 word-level
      cells deep, 538 to 2,208 AND levels).

### 3.5 Measurements still missing

- [ ] The independent second check of the 2026-11 reports.
- [x] The per-routine time profile of the x86 engine (section 1.7, 2026-11-16).
- [ ] The earlier sLUT prototype on clevo-lx, measured under the rules of section 4.
- [x] A large card: three RTX 4090 runs on 2026-11-15/16 (section 1.6).
- [ ] Structure analysis (section 1.3) of a design with several loosely coupled blocks, and of VeeR-EH2.

### 3.6 Effort (estimates of 2026-11-13, not measurements)

| stage | from scratch | starting from GEM's code |
| :-- | --: | --: |
| 1: uncertainty rules, second plane in the compiled CPU path | 2 to 4 weeks | the same |
| 2: GPU evaluator | 6 to 10 weeks | 3 to 5 weeks |
| 3: coupling and verification on Hazard3 and VeeR | 3 to 5 weeks | the same |

Basis: GEM's core is about 2,850 lines of mapping code, a 388-line CUDA kernel and a 750-line
driver; its parser, netlist database and VCD code are replaced here by libyosys and nvc. The
two-plane kernel does roughly twice the work of the 2-state one, so expect about half GEM's rate on
the same netlist. The case for building it is 3D-Logic at scale, not beating Verilator on one run.

## 4. Rules for reporting GPU results

1. Never divide a card's many-instance total by the rate of one CPU thread.
2. Report one simulation (cycles/s against one CPU thread) separately from throughput of many copies
   (instance-cycles/s against the whole CPU, all cores, same workload).
3. Same box and same session where possible; otherwise say "cross-machine" next to the number.
4. Say whether a time is kernel time or the wall time of the command.
5. Name the CPU, the build flags of the baseline, and the number of runs; keep the raw logs under
   the names the document cites.
6. Only certified runs (checksum and cycle count equal to the reference) go into a ratio.

## 5. References

- The sLUT engine prototype of section 1.6 (development box, WSL): `/opt/gpu-reassess/slut`.
  `STATE.md` is the step log, `REPORT.md` the full report (sections 11 to 16: power-on, threads, GPU
  engine, depth, islands, speculate and select); `gpu/pkg/` is the self-contained package (`run_all.sh`) with the reference results
  of the T1000 and RTX 4090 runs under `gpu/pkg/reference/`.
- Local reports of 2026-11-12/13 (development box P620A, WSL): `REPORT.md` under
  `/opt/gpu-reassess/audit` (the claims audit with the list of 16 inconsistencies), `baseline`
  (section 1.2), `sizes` (1.4), `msg` and `loops` (1.3); GEM build and notes in
  `/opt/gpu-reassess/gemrun` (`HOWTO.md`, `STATE.md`).
- GEM: <https://research.nvidia.com/publication/2025-06_gem-gpu-accelerated-emulator-inspired-rtl-simulation>,
  <https://github.com/NVlabs/GEM>. A maintained derivative that also ships the paper's benchmark
  netlists: <https://github.com/gpu-eda/Jacquard>.
- NVIDIA native instruction sets (SASS), unofficial: <https://github.com/0xD0GF00D/DocumentSASS>. The
  repository holds a tool and notes; the instruction and latency tables are generated by running the
  tool against `nvdisasm` (architectures up to sm_86 according to its notes). It links
  "Dissecting the NVidia Turing T4 GPU via Microbenchmarking" (arXiv 1903.07486), the T1000's
  generation.
- In this repository: `Accel.md`, `yosys/gen_statemachine.cpp`, `gpubuild/`,
  `bfit/benchmarks/vhdl/gpu/hazard3/`. In the nvc fork: `src/rt/l3dplane.h`,
  `lib/sv2vhdl/logic3d_types_pkg.vhd`, `lib/sv2vhdl/logic3dw_pkg.vhd`, `src/rt/model.c`.
- Asynchronous / pipeline translation notes: `GPU-SIM.md`, `PIPES.md`, `ASYNC-PLAN.md` in the mylex
  repository.
