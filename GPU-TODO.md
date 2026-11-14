# GPU simulation: status and to-do

**Parked on 2026-11-13.** Work resumes when the machine that holds the sLUT prototype (clevo-lx) is
back. This file records what was established before parking, the decisions taken, and what is left
to do, so that nobody has to re-derive it or repeat the mistake that started the reassessment.

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

## 2. Decisions

- 2026-10-03: the default type for logic simulation is the 3D family (`logic3d`), 2-state on request.
  The stack is an accuracy-capable framework first; fast digital runs can be federated to Verilator.
- 2026-11-13: the target form for GPU simulation is design as data (sLUT: the netlist is data, one
  common operation per warp, not restricted to single-bit logic). Any restructuring of the RTL must
  keep the behaviour at the external ports exactly; internal timing may change.
- 2026-11-13: GEM is not used directly and the yardstick runs are abandoned. The evaluator is to be
  our own, through `--accel` / yosys, so that it does 3D-Logic.
- 2026-11-13: parked until clevo-lx is back.

## 3. To do

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
- [ ] The sLUT prototype on clevo-lx, measured under the rules of section 4.
- [ ] A large card: nothing was rented in November.
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
