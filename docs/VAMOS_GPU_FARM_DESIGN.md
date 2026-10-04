# vamos on GPU farms: the `gpu-farm` engine and binary-only GPU sites (Vast.ai)

**Status:** design, rev 1 (2026-10-03). Nothing in this document is built yet. It extends
docs/VAMOS_PLAN.md §2b (sites) and follows docs/VAMOS_GUIDE.md for the user-facing conventions
(`--vamos-*` options, `./simv` behaviour, exit status, provenance header).

**Licence of this document:** part of sv2ghdl, GPL-3.0-or-later. It describes the bfit GPU-farm kit
(`bfit/benchmarks/vhdl/gpu/`, PolyForm-Noncommercial-1.0.0) **only by its command-line interface**:
no kit code is reproduced here, and nothing below asks vamos to import or copy kit files (§7).

**Inputs read for this design:** `bfit/benchmarks/gpu_farm.md` (method, rules 1-5, certification,
measured results and costs, 2026-09-14/15); `gpubuild/` (README.md, index.html, `gpu-cc.sh`,
`build_farm.sh`, `make_device_model.sh`, `ship_run.sh`, `vast_run.sh`); the kit's scripts and their
committed logs (`results/run_*.out`); `yosys/gen_statemachine.cpp`; `tests/hazard3_mandelbrot/`;
the vastai CLI 1.8.3 installed in WSL (`/home/claude/vastai-venv`); vamos's own code (`job.py`,
`cli.py`, `optable.py`, `personalities/{vcs,simv}.py`, `backends/nvc.py`, `tools.py`).

Contents: [0 In one page](#0-in-one-page) · [1 What exists](#1-what-exists) ·
[2 What a farm means for a VCS user](#2-what-a-farm-means-for-a-vcs-user) ·
[3 The farm spec](#3-the-farm-spec-the-testbench-contract) ·
[4 What a gsm model cannot provide](#4-what-a-gsm-model-cannot-provide-refuse-or-warn) ·
[5 Files and options](#5-vamos-side-files-options-and-records) · [6 The local build](#6-the-local-build-nothing-leaves-the-machine) ·
[7 Licence boundary and kit contract](#7-the-licence-boundary-and-the-kit-contract) · [8 Sites](#8-sites) ·
[9 The Vast.ai session](#9-the-vastai-session-step-by-step) · [10 Cost guards](#10-cost-guards) ·
[11 Secrets](#11-secrets) · [12 Certification](#12-certification) ·
[13 Hazard3 Mandelbrot on the farm](#13-hazard3-mandelbrot-on-the-farm-the-users-experiment) ·
[14 Phases](#14-phases-and-exit-criteria) · [15 Tests](#15-tests) ·
[16 Findings in the existing flow](#16-findings-in-the-existing-flow) · [17 Open questions](#17-open-questions-for-the-user)

---

## 0. In one page

What the user gets:

```
vcs -sverilog -timescale=1ns/1ps +define+SIM -top tb $SRC \
    --vamos-engine=gpu-farm --vamos-farm-spec=farm.json      # compile: nvc build + gsm model + farm binaries
./simv                                                       # one instance, compiled C model on this CPU
./simv --vamos-farm=65536 --vamos-site=vast-4090             # 65,536 instances on a rented RTX 4090
```

- The compile builds everything **locally**: the gsm cycle model of the DUT (`gen_statemachine`),
  the kit's harness around it, a CPU farm binary and a GPU fat binary (nvcc in the digest-pinned
  podman image, `--network=none`). It certifies the model against vamos's normal nvc build of the
  same command line before it writes `./simv`.
- A farm run ships **only binaries and a parameter table** to the GPU host, runs them, brings back
  per-instance results, certifies them (instance 0, random spot checks replayed locally, population
  hash, golden when there is one), destroys the instance, and prints instance 0's `$display` lines
  rebuilt from the run (§2.3), as `./simv` would have printed them.
- Every rental is bounded: a price cap per hour, a per-run budget turned into a hard deadline, a
  cumulative ledger cap (the user's $20 for this experiment), confirmation above the budget, and
  four independent destroy paths (§10). The Vast.ai API key is read only by the vastai CLI, from
  its key file or the environment, and never appears on any command line (§11).

What the farm is not (from `gpu_farm.md`, rule 4): **it never speeds up one simulation.** One GPU
thread runs a cycle model 2.5x slower than one CPU core for small designs and 10-30x slower for
core-sized ones. The farm multiplies runs: seeds, parameter points, or one workload split into
independent pieces. The model is 2-state and cycle-based, and the testbench is not simulated: a
**farm spec** (§3) stands in for it, and vamos refuses what the spec cannot express (§4).

"Verijit claims 100x Verilator, what can we get?" Verijit's 766.8 MCycles/s is one simulation on one
core. The farm cannot match that for one run. For one run, the best this stack has is the gsm model
compiled for one CPU core: **1.6-2.6 MCycles/s measured** on the hazard3 SoC (the kit port's CPU
build, `mandel16_i8`, cycle-exact against `ref_cycles`), about 1,000x vamos/nvc's 2.1 kCycles/s,
0.45-0.7x Verilator's 3.6 MCycles/s on the original SoC, and 0.8x a Verilator twin of the same farm
SoC. For the Mandelbrot *image*, split into independent per-tile
simulations, the projection in §13 is about 0.9-4.5 x 10^9 aggregate instance-cycles/s per RTX 4090
(260-1,230x one Verilator thread), so one card could produce the 1024x1024 image in roughly 1.3-7 s
of kernel time, against Verilator's 1,274 s for the single run here and Verijit's claimed 6 s; this
machine's 16 CPU cores would take about 2 minutes. That is a projection from rules 1-2 and the kit
port's model (1,253 comb cells, 112 registers); the measurement is the experiment in §13.

## 1. What exists

| piece | licence | what it does | how vamos uses it |
|---|---|---|---|
| `gpubuild/gpu-cc.sh` | GPL-3.0-or-later (sv2ghdl) | `nvcc` inside `docker.io/nvidia/cuda` pinned by digest, `--network=none`, `$PWD` mounted at `/w` | runs it as the GPU compiler (through the kit's build entry, §7) |
| `gpubuild/build_farm.sh`, `make_device_model.sh` | GPL | model → `__device__`-qualified model → fat binary (`-DMODEL_C`, `GENC=`) | `make_device_model.sh` directly; `build_farm.sh` only for self-contained harnesses |
| `gpubuild/ship_run.sh`, `vast_run.sh` | GPL | ssh/scp a command to a host; binary-only vast.ai session | protocol reference; vamos implements the session itself (findings in §16) |
| `yosys/gen_statemachine.cpp`, `yosys/make_soa_model.py` | GPL (sv2ghdl, not bfit) | Verilog → 2-phase cycle model C (`state_t`/`inputs_t`/`outputs_t`, `sm_reset`/`sm_comb`/`sm_clock`); AoS → SoA | runs `gen_statemachine` as a program (§6) |
| the farm kit, `bfit/benchmarks/vhdl/gpu/` | **PolyForm-Noncommercial-1.0.0** | harnesses (`farm.cu`, the hazard3 port in `hazard3/`), stimulus generators, strip/SoA prep, CPU cert, sweeps, rental scripts, reports | **subprocess only**, through the contract in §7 |
| nvc + sv2ghdl (vamos's normal build) | GPL | the reference simulation of the same `vcs` command line | oracle for the model (§12) and the replay engine (§2.5) |
| Verilator | LGPL-3.0/Artistic-2.0 | the kit's "twin testbench" oracle for LFSR harnesses | optional oracle |
| vastai CLI 1.8.3 | MIT | the Vast.ai API client (`search offers`, `create/show/destroy instance`, `attach ssh`) | subprocess, never with the key on argv (§11) |
| podman 5.7.0 (WSL) | Apache-2.0 | container engine for `gpu-cc.sh` | indirectly |

Facts checked on this machine (2026-10-03): the local `docker.io/nvidia/cuda:12.4.1-devel-ubuntu22.04`
image carries the digest `gpu-cc.sh` pins (`sha256:da679129...` is in its RepoDigests), so GPU builds
run offline; WSL2 sees a local **NVIDIA T1000** (compute capability 7.5, 4 GB, driver 596.59), so a
free local GPU pre-flight is possible before any rental; `gen_statemachine` is not installed with the
stack (copies under `/home/claude`, no Makefile target); `nvcc` is not installed (the container is
the only compiler); the vastai CLI is in a venv, not on PATH, and no key is configured.

**The rules this design must respect** (`gpu_farm.md`; measured on rented cards, every cell certified):

| rule | statement | consequence here |
|---|---|---|
| 1 | aggregate instance-cycles/s ≈ K_card / cells (K ≈ 4-5.6e12 on an RTX 4090, 2.8-4.3e12 H100 SXM, 1.6-2.5e12 A100 40GB / RTX 3090) | the estimate in §10 and the projection in §13 |
| 2 | once per-thread state spills, throughput is cache-bound (3-5x fewer cell-evals/s), and the optimum N moves lower; a frame too big for the card's resident-thread reservation cannot launch | resource gate G4 (§12); `--vamos-farm-layout` (SoA above ~200 KB/instance) |
| 3 | 90 % of plateau at 16k-262k instances per card (≈ SMs x resident threads) | default N per card; refuse to split below `cards x N_sat` |
| 4 | breadth, never depth: one instance on a GPU thread is slower than on one CPU core | `./simv` with N = 1 runs on the CPU, never on a GPU (§2.2) |
| 5 | cards multiply linearly (96-99 % to 8 cards) when N ≥ cards x N_sat | multi-GPU nodes and multi-node fan-out split contiguous gid slices (§9, phase F4) |
| cert | instance 0 equals the reference checksum at the full cycle count; the population hash `AGG` is equal across cards and splits for equal (N, cycles) | kept, and extended with spot checks and goldens (§12) |

## 2. What a farm means for a VCS user

### 2.1 Compile once

The engine is chosen at **compile** time, because the model and binaries take minutes to build and
`./simv` must only run:

```
vcs [vcs options and sources] --vamos-engine=gpu-farm --vamos-farm-spec=farm.json
```

The compile does, in order (each step's log under `simv.daidir/farm/`, §5.3):

1. **The normal vamos build** (sv2ghdl → nvc), exactly as today. It is the reference: the model is
   certified against it, and any farm instance can be replayed on it (§2.5). If sv2ghdl cannot
   translate the design, the compile warns and continues only when the spec names another oracle
   (a golden file, or the kit's Verilator twin); `--vamos-strict` makes it an error.
2. **The model:** `gen_statemachine` on vamos's preprocessed sources (`simv.daidir/pp/pp.v`, so
   `+incdir+`, `+define+` and `-v` apply exactly as for nvc) with the spec's DUT module as top
   and the testbench instance's parameter overrides (§6).
3. **The harness** (kit), **the CPU farm binary** (kit build), **certification** against step 1
   (gate G1, §12), then **the GPU binary** (kit build through `gpu-cc.sh`, SASS for the configured
   archs) and its resource report (gate G4).
4. `./simv` is written last, as today: a farm compile that fails anywhere leaves the previous
   `./simv` disabled (VAMOS_GUIDE §2 behaviour).

The compile prints one summary line per stage, e.g.
`vamos: farm: model soc: 1253 comb cells, 112 registers, 650 B state/instance (AoS)` and
`vamos: farm: gpu binary sm_75+sm_89 SASS, <size>, <n> registers, <n> B stack, <n> B spill`.

### 2.2 Run: `./simv` is one instance, `--vamos-farm=N` is N

| command | what runs | where |
|---|---|---|
| `./simv [plusargs]` | **one** instance, plusargs as given | the CPU farm binary on this machine (rule 4: a single run is fastest on a CPU core) |
| `./simv --vamos-engine=nvc [plusargs]` | the reference nvc run of the same build | this machine |
| `./simv --vamos-farm=N [plusargs] [--vamos-farm-plusargs=F]` | N instances | `--vamos-site=` / `VAMOS_SITE` (default `local`: the CPU farm, OpenMP over the cores) |
| `./simv --vamos-farm=N --vamos-site=vast-4090 --vamos-dry-run` | nothing; prints the plan (offer, estimate, files to ship, deadline) | - |

Each instance is **one `./simv` run with its own plusargs**. Instance `i` (0 ≤ i < N, the *gid*)
gets, in this order (later wins):

1. the plusargs on the `./simv` command line;
2. the per-instance template expansion: in any plusarg, `{i}` is the gid, `{n}` is N,
   `{seed}` is the instance seed (below);
3. line `i` of `--vamos-farm-plusargs=F` (one line of `+a=b +c` per instance; fewer lines than N is
   an error).

The spec (§3) maps plusargs onto what the model can see: **constant DUT input ports** (the
"parameter port" convention: the testbench drives the port from `$value$plusargs`, the harness
drives it from the table), **the stimulus seed** of LFSR-driven inputs, and **the cycle cap**. A
plusarg the spec does not map is a warning, `vamos: warning: +X: the testbench is not simulated in
the gpu-farm engine and the farm spec maps nothing to it` (an error under `--vamos-strict`).

- **Seeds.** `+ntb_random_seed=S` sets the base seed. Instance 0 uses S unchanged, so
  `./simv +ntb_random_seed=S` replays it; instance i > 0 uses a hash of (S, i), the kit's
  decorrelation (`seed_for(gid)` in `farm.cu`) with S folded in. `+ntb_random_seed_automatic` draws S
  from `os.urandom` and prints it. Seeds reach **only** the spec's LFSR inputs: SystemVerilog
  `$urandom` in the testbench is not simulated, and a seed given to a spec with no LFSR input is a
  warning.
- **Split workloads.** A spec `split` names the input that carries the piece index (`"from":
  "gid"`, plus an optional constant `add`) and the piece count. With `--vamos-farm=N` the instances
  compute the N pieces of one job, and the spec's `merge` hook (§3) assembles the outputs, for
  example the Mandelbrot tiles into one image (§13). N must equal the piece count; vamos refuses any
  other N. The hazard3 tile firmware reads its tile size from the tile word, so another tiling
  changes only vamos's parameter table, not the binary: `--vamos-farm-split=<count>:<add>` overrides
  the spec's count and `add` for one run (m = 0, 2, 4 are `1048576:0`, `262144:0x02000000`,
  `65536:0x04000000`).
- **`+vcs+finish+<time>`** becomes a cycle cap: time / the spec's clock period, refused when it is
  not a whole number of cycles.

### 2.3 What comes back

The console (and `-l <file>`) gets, after the provenance header:

```
vamos: farm: 65536 instances of soc (1253 cells, 650 B/instance, AoS) on vast-4090
vamos: farm: plan: RTX_4090 x1, offer 43681499, $0.36/h (cap $0.60/h), est. 6 min / $0.04;
             budget $2.00, ledger $1.20 of $20.00; deadline 25 min
vamos: farm: instance 51058267 running after 46 s (ssh8.vast.ai:18266); shipped 3 files, 6.4 MB, 4 s
TOHOST 04000000                       <- instance 0's lines, rebuilt from its returned stream (§3)
...
HAZARD3 DONE cycles=70423 words=18
vamos: farm: 65536 done, 0 capped, 0 fatal; kernel 9.8 s, 4.7e8 instance-cycles/s; balance 0.52
vamos: farm: cert: instance 0 MATCH, 16/16 spot checks MATCH (CPU replay), AGG 9F3C..., golden image MATCH
vamos: farm: instance 51058267 destroyed after 6 min 12 s; cost $0.04 (ledger $1.24 of $20.00)
           V A M O S   S i m u l a t i o n   R e p o r t
Time: 70423 cycles (instance 0)
```

(The numbers are illustrative.) The run directory, `simv.farm/<run-id>/` next to `./simv`
(`<run-id>` = UTC time + 6 hex), holds:

| file | contents |
|---|---|
| `run.json` | the request (N, plusargs, template, site, spec hash, binary sha256s, toolchain digest), the offer, the estimate, the deadline, timings, cost, exit status |
| `params.bin` / `params.tsv` | the per-instance parameter table that was shipped (binary) and its readable form |
| `session.log` | the remote session transcript (redacted, §11) and every FARM line |
| `results/` | per-instance records and streams as returned (§7.3), with the remote sha256 manifest |
| `instances.tsv` | gid, parameters, status (done / capped / fatal / overflow), done cycle, stream words, CHK |
| `logs/<gid>.log` | rebuilt `$display` lines for the instances chosen by `--vamos-farm-logs=` (default `0`; also `all`, `none`, `failed`, or a list) |
| `merged/` | the output of the spec's merge hook |
| `cert.json` | every certification check and its result (§12) |

Instances whose logs were not rebuilt still have their CHK, status and done cycle; any of them can be
replayed in full (§2.5).

### 2.4 Exit status

VCS semantics first: VCS exits 0 after `$finish`, even after `$error` or a testbench-detected
failure (VAMOS_GUIDE §3). A farm instance that reaches a spec finish condition (`done`, or the cap
when the spec marks the cap line as the testbench's own `$finish`) counts as finished.

| exit | meaning |
|---|---|
| 0 | every instance finished, and every certification check passed |
| 1 | a design failure: an instance reached a spec `fatal` condition (the farm's `$fatal`), or, with spec `"exit": "strict"`, did not reach `done`; or the farm build failed |
| 2 | usage error (vamos convention: a bad `--vamos-*` option, a spec error) |
| 3 | **certification failed**: the results are not trusted (instance 0, a spot check, AGG, or the golden differs); results are kept for diagnosis, nothing is merged |
| 4 | infrastructure: no offer under the price cap, the instance never ran, ssh never opened, a transfer failed, a device error on the host (`FARM-ERROR`), or the deadline was reached (the instance is destroyed either way) |
| 5 | spend refused: the estimate is above the budget and was not confirmed; nothing was rented |
| signal | interrupted (Ctrl-C, SIGTERM, SIGHUP): after the instance is destroyed, vamos ends with that same signal, so the calling shell sees 128+N, as `./simv` does today for nvc runs |

### 2.5 Replay one instance

`./simv --vamos-farm-replay=<run-id>:<gid>` re-runs that instance's plusargs locally on the CPU farm
binary and compares its CHK, done cycle and stream with the returned record; with
`--vamos-engine=nvc` it runs the reference nvc build instead, prints the testbench's own
`$display` output, and compares the TOHOST-style lines. This is how a VCS user debugs one failing
seed of a farm regression: it is the same `./simv +plusargs` they would have run.

## 3. The farm spec: the testbench contract

The gsm model covers the DUT only. Everything the testbench does must be expressible as a harness the
kit can generate, and the spec is where the user says what that is. It is a JSON file (stdlib
`json`; unknown keys are an error, as in `Job.from_json`), copied normalised into
`simv.daidir/farm/spec.json`.

| key | meaning |
|---|---|
| `farm_spec` | format version, `1` |
| `dut.module`, `dut.instance` | the module compiled to the model, and its instance path in the testbench (`tb.dut`); vamos takes the instance's parameter overrides from the elaborated nvc design, or from `dut.parameters` |
| `clock.port`, `clock.period` | the one clock input, driven in two phases per cycle (low, then high), and its period (for `+vcs+finish+` and the footer) |
| `reset` | `null` (the DUT resets itself, as the hazard3 SoC does), or `{"ports": [...], "active": 0/1, "cycles": n}` |
| `inputs.<port>` | every other DUT input: `{"const": v}`, `{"plusarg": "NAME", "default": v}` (a parameter port), `{"from": "gid"}` or `{"from": "n"}` (optionally `"add": v`, a constant added to it), or `{"lfsr": true}` (seeded stimulus, the kit's LFSR convention); an input the spec does not list is an error. `plusarg` and `from` together: a farm run takes the gid (or N), a single `./simv` run and a replay take the plusarg, so `./simv +TILE_ID=<the word of instance 17>` is instance 17 |
| `stimulus` | `"lfsr"` for the kit's RTLMeter-style harness (every free input LFSR-driven, output fold, periodic reset, Verilator twin as oracle); omitted for stream harnesses |
| `stream` | `{"valid": port, "data": port, "max_words": n, "line": "TOHOST %h"}`: a word the DUT presents on `data` while `valid` is high at a rising edge is captured, counted, folded into the instance checksum, and, when the instance's log is rebuilt, printed with the testbench's own `$display` format |
| `done` | the finish condition: `{"stream_value": "0xffffffff"}` or `{"port": p, "value": v}`, plus the `line` the testbench prints there and its `args` (`cycles`, `words`, a port) |
| `cycle_cap` | `{"plusarg": "CYCLE_CAP", "default": n, "line": ..., "args": [...], "finish": true}`: the testbench's give-up; `finish: true` says the testbench calls `$finish` there (exit 0 under VCS semantics) |
| `fatal` | optional conditions that the testbench treats as `$fatal` (a stream value, a port value) |
| `checksum` | `"stream"` (FNV-1a-64 over the stream words, the hazard3 convention) or `"outputs"` (the kit's per-cycle output fold) |
| `split` | `{"index": "<input>", "count": n}`: the inputs that make a split workload (§2.2) |
| `golden` | optional: `{"stream_file": f}`, `{"chk": hex, "cycles": n}`, or `{"merged_md5": hex}` |
| `merge` | optional: a command run locally on the returned streams, `["python3", "merge.py", "{results}", "{merged}"]` (vamos substitutes the two directories) |
| `exit` | `"vcs"` (default, §2.4) or `"strict"` |
| `ship_ok` | must be `true` before any third-party site gets a binary of this design (§11.4) |

The hazard3 split variant as a spec. The kit's farm SoC adds a `tile_id` input, which the tile
firmware reads at `0x1004`. Its tile word is `index | m << 24`: tile `index` renders the 2^m pixels
`p = index + k x NT` (k < 2^m, NT = 2^(20-m) tiles at 1024x1024), so one firmware image serves every
tile count; below, m = 4: 65,536 tiles of 16 pixels (m = 0, a million one-pixel tiles, balances
better on a GPU: §13.2). The reference testbench for vamos is
`tb_hazard3.v` plus one line that drives `tile_id` from `$value$plusargs("TILE_ID=%d", ...)` (a
farm counterpart that does not exist yet):

```json
{
  "farm_spec": 1,
  "dut": {"module": "soc", "instance": "tb.dut"},
  "clock": {"port": "clock", "period": "10ns"},
  "reset": null,
  "inputs": {"tile_id": {"plusarg": "TILE_ID", "default": 0, "from": "gid", "add": "0x04000000"}},
  "split": {"index": "tile_id", "count": 65536},
  "stream": {"valid": "tohost_valid", "data": "tohost_data", "max_words": 18, "line": "TOHOST %h"},
  "done": {"stream_value": "0xffffffff", "line": "HAZARD3 DONE cycles=%0d words=%0d",
           "args": ["cycles", "words"]},
  "cycle_cap": {"plusarg": "CYCLE_CAP", "default": 50000000, "finish": true,
                "line": "HAZARD3 FAIL cycle cap %0d reached after %0d TOHOST words (no DONE marker)",
                "args": ["cycles", "words"]},
  "checksum": "stream",
  "golden": {"merged_md5": "693d2391e979a114a82af00b3e64e54c"},
  "merge": ["python3", "tiles2ppm.py", "{results}", "{merged}/output.ppm"],
  "ship_ok": true
}
```

**How vamos checks a spec against the testbench.** It cannot prove that the spec describes the
testbench, so it compares behaviour: at compile time (gate G1, §12) the reference nvc run and the
CPU farm binary run the same plusargs with the cap lowered to a prefix vamos can afford on nvc
(about 2,000 cycles/s for hazard3: 35,720 cycles of `mandel8_i16` take 17 s), and their printed
lines must be identical. A spec that leaves out something the testbench does shows up as a
difference, and the compile stops with the first differing line from each side.

**Specs vamos could derive itself (v2, not in v1):** testbenches that are only a clock generator,
a reset sequence, constant or plusarg-driven inputs and `always @(posedge clk) if (v) $display(fmt, d)`
monitors. v1 always takes an explicit spec.

## 4. What a gsm model cannot provide: refuse or warn

| VCS / Verilog semantic | in the gsm farm | vamos |
|---|---|---|
| 4-state values (x, z) | 2-state; `x` and undriven nets read 0; flops without reset or init start at 0 (as Verilator `--x-initial 0`); init values (`reg r = 1`) and `$readmemh` at time 0 are honoured | one-line note per compile; the oracle (G1) runs the same prefix on nvc, so any dependence on x shows up there |
| `#` delays, `specify`, timing checks in the DUT | dropped by synthesis | warning with file:line (yosys reports them); error under `--vamos-strict` |
| more than one free-running clock; `negedge` flops; latches other than clock-gate cells | one 2-phase clock; extra clocks held at 0 (`sm_clock_masked`); `GSM_ICG2EN` rewrites latch+AND clock gates; other latches decline | refuse (compile error) when the testbench drives a second clock or `gen_statemachine` declines; note when extra clocks are held |
| non-synthesizable DUT code (`$display`, `$finish`, `$random`, `fork`, events, `real`, classes) | not representable | refuse, with yosys's file:line |
| **the testbench** (initial blocks, scoreboards, `$display`, `$value$plusargs`, `$urandom`, file I/O) | not simulated: the spec's harness replaces it | refuse without a spec; the G1 comparison guards the spec |
| `$display` text | none on the device; the spec's stream and finish lines are rebuilt on the host | only spec lines appear; `%t`/`$time` print cycles x period |
| `$time`, `+vcs+finish+<time>` | cycles | converted with `clock.period`; refused when not a whole number of cycles |
| run-time `$readmemh`, `$fopen`, `$fwrite` | none | refuse (DUT); not simulated (testbench) |
| plusargs | only those the spec maps (ports, seeds, cap) | warning for the rest (§2.2) |
| `+ntb_random_seed` | seeds the spec's LFSR inputs only | note when the spec has none |
| waves (`-debug_access`, `$dumpvars`), coverage (`-cm`), UCLI, VPI/PLI/DPI, `-xprop` | none | error at compile when given with `--vamos-engine=gpu-farm` (they would silently do nothing) |
| AMS (`vcs-ams`, `-ad`) | none | error: the gpu-farm engine is digital only |
| exit status | from the spec's finish conditions | §2.4 |

## 5. vamos-side files, options and records

### 5.1 New and changed files (all GPL-3.0-or-later)

| file | role |
|---|---|
| `vamos/farm/spec.py` | the farm spec: parse, validate (unknown keys refused), normalise, hash |
| `vamos/farm/model.py` | runs `gen_statemachine` on `pp.v` (or nvc's accel export, §6), reads the model's port structs and statistics into `model.json` |
| `vamos/farm/kit.py` | **the only module that knows where the kit is**; runs kit entry points as subprocesses, checks the contract version, parses their JSON outputs; never imports kit code |
| `vamos/farm/params.py` | plusargs + template + plusarg file → the per-instance table (`params.bin`, `params.tsv`) |
| `vamos/farm/results.py` | readers for the returned records and streams; AGG and CAGG recomputation; status counts; balance |
| `vamos/farm/logs.py` | rebuilds `$display` lines from a stream with the spec's formats |
| `vamos/farm/cert.py` | gates G1-G5 and the post-run checks (§12) |
| `vamos/farm/run.py` | one farm run on one site: plan → confirm → execute → collect → certify → report |
| `vamos/backends/farm.py` | the `./simv` branch for `job.engine == "gpu-farm"` (beside `cosim.run`) |
| `vamos/sites/config.py` | `sites.json` in the configuration layers (package, `etc/vamos/`, `~/.config/vamos/`, `~/.vamos/`, `./.vamos/`; later wins, site by site) |
| `vamos/sites/local.py`, `local_gpu.py`, `gpu_ssh.py`, `vast.py` | the four site types (§8) |
| `vamos/sites/ledger.py` | the spend ledger (§10) |
| `vamos/sites/watchdog.py` | the detached destroy-at-deadline process (§10) |
| `vamos/sites/secrets.py` | child environments without the key, key-file mode check, log redaction (§11) |
| `vamos/personalities/vcs.py`, `simv.py` | the engine and farm options; the farm compile stage; dispatch |
| `vamos/optable.py` | the new `--vamos-*` keys (§5.2); `site`, `sites` leave `PLANNED_KEYS` |
| `vamos/job.py` | `engine` (`"nvc"` or `"gpu-farm"`), `farm_spec` (the parse-time request), `farm` (the compile-to-run record, like `ams`); `SCHEMA` + 1 (2 today), so an older vamos refuses a farm daidir |
| `vamos/licenses.json` | entries for `bfit-farm-kit` (PolyForm-NC), `gen_statemachine` (GPL, sv2ghdl), `CUDA toolkit` (NVIDIA CUDA EULA; cudart linked statically), `vastai` (MIT), `podman` (Apache-2.0) |
| `tests/vamos/test_farm_*.py`, `tests/vamos/fakes/` | §15 |

### 5.2 Options

| option | where | effect |
|---|---|---|
| `--vamos-engine=nvc\|gpu-farm` | compile; `./simv` | compile: build the farm as well (default `nvc`); `./simv --vamos-engine=nvc`: run the reference build of a farm compile |
| `--vamos-farm-spec=<file>` | farm compile | the spec (required with `gpu-farm`) |
| `--vamos-farm-arch=<sm_xx,...>` | farm compile | SASS targets; default: the local GPU's arch plus the archs of the configured GPU sites; **no PTX** unless `--vamos-farm-ptx` (§11.4) |
| `--vamos-farm-layout=auto\|aos\|soa` | farm compile | per-instance state layout (auto: SoA above 200 KB/instance, `gpu_farm.md`) |
| `--vamos-farm-u32` | farm compile | 32-bit carriers (`GSM_U32=1`; 22-27 % faster on the Yuri benchmark); off by default, as in `gen_statemachine` |
| `--vamos-farm-cert-cycles=<n>` | farm compile | the G1 prefix (default: the spec cap, at most 200,000) |
| `--vamos-farm=<N>` | farm `./simv` | number of instances (default 1) |
| `--vamos-site=<name>` | farm `./simv` | `local`, `local-gpu`, or a configured site (also `VAMOS_SITE`) |
| `--vamos-farm-plusargs=<file>` | farm `./simv` | per-instance plusarg lines |
| `--vamos-farm-split=<count>:<add>` | farm `./simv` | override the spec's split count and index offset for this run (§2.2) |
| `--vamos-farm-logs=0\|all\|none\|failed\|<list>` | farm `./simv` | whose `$display` lines are rebuilt |
| `--vamos-farm-spot=<K>` | farm `./simv` | spot checks after the run (default 16) |
| `--vamos-farm-replay=<run>:<gid>` | farm `./simv` | §2.5 |
| `--vamos-budget=<usd>` | farm `./simv` | this run's budget (default: the site's) |
| `--vamos-confirm-spend=<usd>` | farm `./simv` | non-interactive approval of an estimate up to this amount |
| `--vamos-max-dph=<usd>` | farm `./simv` | price cap per hour for the offer search (default: the site's) |
| `--vamos-farm-minutes=<m>` | farm `./simv` | hard deadline for the rental (default from the estimate, never above the site's `max_minutes`) |
| `--vamos-dry-run` | farm `./simv` | plan only: offer, estimate, deadline, files to ship; rents nothing |
| `--vamos-sites` | any | probe the configured sites and exit, offline (vast: CLI found, key resolvable, key-file modes) |
| `--vamos-sites-online` | any | `--vamos-sites` plus one read-only API call per vast site (`vastai show user --raw`) to prove the key works |
| `--vamos-farm-reap` | any | list vamos-labelled instances on the account; destroy, after asking, those no live lease owns |
| `--vamos-spend` | any | print the ledger: per site, per day, total against the cap |

The run directory is always kept (it holds the results), so `--vamos-keep` has no effect on a farm
run (the usual "has no effect" warning).

Environment: `VAMOS_SITE`, `VAMOS_FARMKIT` (the kit directory; default `<sv2ghdl>/bfit/benchmarks/vhdl/gpu`
in dev mode, `PREFIX/libexec/bfit/farmkit` installed), `VAMOS_GEN_STATEMACHINE`, `VAMOS_GPU_CC`
(default `<sv2ghdl>/gpubuild/gpu-cc.sh`), `VAMOS_VASTAI` (the vastai executable; checked like
`VAMOS_NVC`). `VAST_API_KEY` is the vastai CLI's own variable: vamos only tests whether it is set (§11).

### 5.3 Records on disk

```
simv.daidir/
  vamos.job.json  vamos.tools.json  pp/  nvc/          (as today: the reference build)
  farm/
    spec.json                 normalised spec + its sha256
    model/   model.c  model_s.c  model_s_dev.c  gsm.log  model.json   (cells, registers, memories, ports, state bytes)
    kit/     prep/ ...  harness/ ...  build.json   (kit outputs; PolyForm-NC by derivation, §7.1)
    bin/     farm_cpu  farm_gpu  sha256sums  resources.json  (registers, spill, stack, local memory per arch)
    cert/    g1_nvc.log  g1_farm.log  g3_localgpu.log  cert.json
simv.farm/<run-id>/           §2.3
~/.config/vamos/sites.json    site definitions (§8)
~/.local/state/vamos/vast-ledger.jsonl   spend ledger (XDG_STATE_HOME), §10
~/.local/state/vamos/leases/<run-id>.json   live rentals, read by the watchdog and --vamos-farm-reap
```

## 6. The local build: nothing leaves the machine

1. **Model.** `gen_statemachine <pp.v> [name=value ...] <dut.module> model.c`, run in
   `farm/model/` with `GSM_ICG2EN=1` (and `GSM_U32=1` with `--vamos-farm-u32`). `pp.v` is vamos's
   preprocessed source set, so the tool needs neither `-I` nor `-D` (today's CLI has neither: it runs
   plain `read_verilog [-sv]` on each file). For VHDL or mixed designs the alternative source is
   nvc's accel export (`NVC_ACCEL=1` with the gsm library, the route `build_models.sh` used for the
   ITC'99 designs); it is phase F4.
   First build of the hazard3 farm SoC (kit port, 2026-10-03): 10.5 s, 121 MB RSS, 1,409 comb cells,
   144 registers, 5.5 MB of C; 1,253 comb cells and 112 registers with the register file as a memory
   (the port's build, §13.2).
2. **Prep** (kit): strip the clock-variant bodies the farm never calls; move memories the design
   never writes into one read-only table shared by all instances (the hazard3 instruction RAM: the
   kit port's `mem_prep.py` turns a write into a trap under its original guard, so the transform
   cannot silently diverge); store narrow memories as 32-bit words instead of 64-bit; SoA when the
   layout says so. Then `gpubuild/make_device_model.sh` (GPL) for the device copy.
3. **Harness:** the kit generates it from the spec and the model (§7.2).
4. **CPU build** (kit): `g++ -O2 -fopenmp`, the same harness source as the GPU build.
5. **Gates G1-G3** (§12): nvc prefix vs CPU farm, CPU determinism, local GPU vs CPU.
6. **GPU build** (kit, through `gpu-cc.sh`): SASS only for the configured archs, e.g.
   `-gencode arch=compute_75,code=sm_75 -gencode arch=compute_89,code=sm_89` (T1000 pre-flight +
   RTX 4090/L40S); `cudart` static, so the run host needs only `libcuda.so.1`. Models over 20 MB of C
   get `-Xcicc -O1` (the kit's field note). Then the resource report for each arch (registers, spill
   stores, stack frame, local memory).
7. **Hashes:** sha256 of the binaries, the harness and the model, plus the container image digest,
   into `farm/bin/sha256sums` and `vamos.tools.json`. A run ships exactly these files and checks them
   again on the host (§9 step 7).

## 7. The licence boundary and the kit contract

### 7.1 The boundary

- vamos runs kit programs with arguments and files and reads their outputs. It never imports a kit
  module, never copies a kit file (not into its package, its tests, or a build directory: when a kit
  file must sit inside the container mount, the kit's own build entry puts it there), and never
  generates code that reproduces a kit harness.
- `vamos/farm/kit.py` is the only place that knows the kit's location and entry points. Tests that
  need the kit run it as a subprocess and skip when it is absent.
- Kit outputs (generated harness, binaries containing the harness) are build products of the user's
  design, under the kit's licence by derivation. vamos stores them in the daidir like any other
  build product and ships them only to the sites the user configured.
- The provenance header lists the kit on every farm compile and run:
  `bfit-farm-kit <rev> PolyForm-Noncommercial-1.0.0 <path>`, and the first farm compile in a project
  prints `vamos: note: the gpu-farm engine runs the bfit farm kit (PolyForm-Noncommercial-1.0.0); its
  harness is compiled into the farm binaries; commercial use needs a licence from the copyright holder`.
- A gpu-farm compile without the kit is an error naming `VAMOS_FARMKIT`; the nvc engine never needs it.

### 7.2 Contract v1: exactly what vamos calls

Today's scripts work for the ITC'99 and RTLMeter flows but cannot be called from vamos as they are
(§16: absolute paths to missing tools, no NVCC override, outputs written to the current directory,
no machine-readable summaries). The contract below is what the kit owner adds; vamos calls nothing
else. Every entry prints one JSON object on stdout and exits non-zero on failure.

| call | inputs | outputs |
|---|---|---|
| `farmkit version` | - | `{"kit": "<rev>", "contract": 1, "license": "PolyForm-Noncommercial-1.0.0"}` |
| `farmkit prep --model M.c --out D [--rom <regex>] [--layout aos\|soa\|auto]` | the gsm model | `D/model_p.c`; `{"removed": [...], "rom": [...], "narrowed": [...], "layout": "aos", "state_bytes": n}` (strip, memory transforms, SoA: `strip_model.py`, `hazard3/mem_prep.py`, `soa_prep.py` today) |
| `farmkit harness --spec S.json --model D/model_p.c --out D` | spec (§3), prepared model | `D/harness/` (a self-contained harness source and headers; for `stimulus: lfsr` also the Verilator twin), `{"inputs": [...], "stream": ..., "params": [names in table order], "registered_stream": bool}` |
| `farmkit build --dir D --target cpu` | harness + model | `D/farm_cpu`; `{"binary", "bytes", "seconds"}` |
| `farmkit build --dir D --target gpu --genc "<-gencode ...>" --nvcc <gpu-cc.sh> [--layout aos\|soa] [--u32]` | harness + model | `D/farm_gpu`; `{"binary", "bytes", "seconds", "resources": {"sm_89": {"registers", "spill_stores", "stack_frame", "local_bytes"}}}` (from `cuobjdump --dump-resource-usage` in the same container, not a second compile) |
| `farmkit expect --spec S.json --stream golden.tohost` | a golden stream | `{"chk": hex, "words": n, "done": bool}` (the host-side checksum; `h3chk.py` today) |
| `farm_cpu` / `farm_gpu` | §7.3 | §7.3 |
| `farmkit bench --binary B --expect E` (on the host; optional) | a binary, expected checksums | the adaptive N sweep (`sweep_rtlm.sh` today), FARM lines |

What each entry wraps today, and what it lacks (the reason vamos does not call these scripts directly):

| v1 entry | today's script and interface | gap |
|---|---|---|
| `prep` | `strip_model.py model.c > model_s.c`; `hazard3/mem_prep.py model_s.c [--rom <regex>] > model_m.c`; `soa_prep.py model.c stim.h` (writes beside the model, runs `make_soa_model.py` by absolute path) | one entry, outputs under `--out`, a JSON summary |
| `harness` | `gen_stim_v.py model.c <name> <clk> [<resets>] > stim.h` (writes `tb_<name>.cpp` into the current directory; requires a reset port); hand-written `rtlm/yuri/yuri.cu`, `hazard3/h3farm.cu` | a generator driven by the spec |
| `build --target cpu` | `g++ -O2 -fopenmp -x c++ -DFARM_CPU -DMODEL_C=... [-DSTIM_H=...]` on the harness (inside `rtlm_build.sh`, `cert_cpu.sh`) | an entry point that hides the harness's macros |
| `build --target gpu` | `rtlm_build.sh <dir> <name>` (`NVCC` fixed to `/home/claude/tools/cuda-redist/bin/nvcc`, absent here; passes the harness by absolute path, which `gpu-cc.sh`'s `$PWD` mount cannot see); `build_gpu.sh` (fixed design list) | `--nvcc`, staging inside the mount, the resource report from the built binary |
| `expect` | `hazard3/h3chk.py golden.tohost` → `CHK=<hex> words=<n> done=<0\|1>` | JSON, any spec |
| binary | `farm.cu`: `<cycles> <N> [block] [reps] [ngpu]`, the FARM line, `FARM-ERROR` and exit 3; `h3farm.cu` adds `H3_GID0`, `H3_DUMP`/`H3_WMAX`, `DONE0`, `CAGG` | `FARM_OUT`, `FARM_PARAMS`, one naming for every harness |
| `bench` | `sweep_rtlm.sh` (reads `expect.txt`: `name cycles CHK [N-list] [FARM_SMEM]`) | none needed |

### 7.3 The farm binary contract (runs on the GPU host)

Kept from `farm.cu` and the hazard3 port, so existing logs stay parseable:

```
farm_gpu <cycles> <N> [block=128] [reps=1] [ngpu=1]
  env FARM_GID0=<g>      first global instance id (default 0)       (hazard3 port: H3_GID0)
  env FARM_PARAMS=<file> per-instance input values, table order     (new)
  env FARM_OUT=<file>    per-instance result records                 (new; vamos always sets it)
  env FARM_DUMP=<file>   per-instance stream words, FARM_WMAX each   (hazard3 port: H3_DUMP, H3_WMAX)
  env FARM_SMEM=<bytes>  residency throttle                          (exists)
stdout:  FARM contract=1 design=<s> dev="<g>x <name>" N=<n> gid0=<g> cycles=<cap> block=<b> secs=<kernel s>
         agg_inst_cyc_per_s=<f> per_inst_cyc_per_s=<f> CHK0=<16 hex> AGG=<16 hex> CAGG=<16 hex>
         done=<n> capped=<n> fatal=<n> overflow=<n>
         FARM-ERROR design=<s> CUDA="<message>" ...        (then exit 3)
exit:    0 ran (whatever the instances did); 2 usage; 3 device, launch or allocation error
```

`<cycles>` is the cap; an instance stops at its `done` cycle (stream harnesses) or runs exactly
`<cycles>` (LFSR harnesses). `secs` is kernel time, best of `reps`, as in `gpu_farm.md`. Records
(`FARM_OUT`, little-endian): a header (`VFR1`, N, gid0, record size), then per instance
`chk:u64, done_cycle:u64 (0 = none), words:u32, status:u32` (bits: 1 done, 2 capped, 4 stream overflow,
8 fatal). `FARM_PARAMS`: a header (`VFP1`, N, K, the K parameter names, which must equal
`harness.params`), then N x K `u64`. `AGG` = FNV-1a-64 over the CHK vector bytes in gid order and
`CAGG` the same over the done cycles (the hazard3 port's cycle-exact population hash). vamos
recomputes both from the records, which is what lets it join slices from several GPUs or hosts
(rule 5) and still compare one population hash.

### 7.4 What vamos needs from `gen_statemachine` and the kit for arbitrary designs

From `gen_statemachine` (GPL, sv2ghdl):
1. An installed, versioned build: a Makefile/`build_stack.sh` target, `gen_statemachine --version`
   (git revision + source sha256) for the provenance header. Today there are several copies under
   `/home/claude` and `build_models.sh`'s `yosys/libgsm.so` does not exist.
2. A JSON sidecar with what a consumer has to scrape from the generated C today (the kit's
   `gen_stim_v.py` parses `// <n> bits` member comments): cells, registers, memories (name, width,
   depth, init source), ports (name, direction, width, C member, registered or not), extra clocks,
   `state_t`/`inputs_t`/`outputs_t` sizes, declines with file:line.
3. Declines with source locations, so vamos can say "refused: latch at hazard3_foo.v:120" instead of
   a bare exit 1.
4. Stable C member names across builds (vamos maps spec ports onto them).
5. Optional: `-I`/`-D` for users who run it without vamos.

From the kit (beyond §7.2):
1. **A spec-driven stream harness.** Today's harnesses are `farm.cu` (LFSR drive + output fold,
   needs a reset port: `gen_stim_v.py` asserts one) and hand-written ones (`rtlm/yuri/yuri.cu`,
   `hazard3/h3farm.cu`). vamos needs one generator for: parameter ports from a table, a
   valid/data stream with `max_words`, done/cap/fatal conditions, stream or output checksums,
   reading registered stream ports from the state (the hazard3 port's optimisation).
2. **Per-instance results out** (`FARM_OUT`), not only `CHK0` and `AGG` (today `farm.cu` keeps the
   CHK vector in memory).
3. **`FARM_GID0` in every harness, SoA included** (`farm.cu`'s SoA mode is one GPU only and starts
   at gid 0).
4. **No hard-coded tool paths** (`NVCC`, `make_soa_model.py`, nvc, vastai, ITC'99 sources): every
   tool from an argument or variable, every output under `--out`.

## 8. Sites

docs/VAMOS_PLAN.md §2b defines *sites* as ssh-reachable farms where vamos itself is installed and the
whole command line re-runs remotely. GPU farm sites are a different kind: **binary-only**. vamos
stays on the laptop; the remote side runs only the farm binary and a short run script vamos writes
(§9 step 7). Four types:

| type | where | lifetime | ships |
|---|---|---|---|
| `local` (built in) | this machine's CPU, OpenMP | - | nothing |
| `local-gpu` (built in) | this machine's GPU (WSL2 `/usr/lib/wsl/lib/libcuda.so.1`, or native) | - | nothing |
| `gpu-ssh` | an owned GPU host over ssh (the `ship_run.sh` protocol; jump hosts as in §2b) | persistent | binaries + params |
| `vast` | a Vast.ai instance rented for this run | created and destroyed by the run | binaries + params |

`sites.json` (merged by name across the configuration layers):

```json
{"sites": [
  {"name": "vast-4090", "type": "vast",
   "gpu_name": "RTX_4090", "num_gpus": 1, "max_dph": 0.60,
   "filters": "verified=true reliability>0.98 inet_down>100 disk_space>=16 rentable=true",
   "image": "nvidia/cuda:12.4.1-runtime-ubuntu22.04", "disk_gb": 16,
   "budget_usd": 2.00, "max_minutes": 45, "provision_timeout_s": 600,
   "exclude_machines": [103058], "spend_cap_usd": 20.00, "third_party": true},
  {"name": "vast-4090x8", "type": "vast", "gpu_name": "RTX_4090", "num_gpus": 8,
   "max_dph": 4.50, "budget_usd": 3.00, "max_minutes": 30, "spend_cap_usd": 20.00, "third_party": true},
  {"name": "lab-a6000", "type": "gpu-ssh", "ssh": "kc@gpu-lab", "jump": ["bastion"], "arch": "sm_86",
   "workdir": "/scratch/$USER/vamos-farm", "third_party": false}
]}
```

`spend_cap_usd` is shared by every `vast` site (one ledger per account). `exclude_machines` starts
from the kit's `results/bad_machines.txt`. `vamos --vamos-sites` probes each site without renting:
for `vast`, the CLI is found and runnable, a key is resolvable (env or key file present, file mode
0600, never read by vamos), and the offer filter parses; for `gpu-ssh`, `ssh -o BatchMode=yes`
through the jump chain and `nvidia-smi` on the host.

## 9. The Vast.ai session, step by step

vamos implements the session in `vamos/sites/vast.py` (stdlib only: `subprocess` to `vastai`, `ssh`,
`scp`, `ssh-keygen`), following `vast_run.sh`'s protocol (poll, attach a key, scp prebuilt binaries
within the auth window, one held-open run) but not calling it (its exit status and key handling are
in §16). Every `vastai` call passes `--raw` and parses JSON; none passes `--api-key`, `--curl` or
`--explain` (§11).

1. **Preconditions, all local and free:** the farm build exists and passed G1-G3 for the archs this
   site needs; `ship_ok` is set in the spec when the site is `third_party`; the key is resolvable;
   no live lease for this daidir and site (one rental per run).
2. **Plan:** `vastai search offers '<gpu_name> <num_gpus> <filters> cuda_vers>=12.4' -o dph --raw`;
   keep offers with `dph_total` ≤ the price cap, a `compute_cap` covered by a built SASS arch (same
   major version, minor version ≤ the card's: sm_86 SASS runs on an sm_89 card, sm_89 SASS cannot
   run on an sm_86 card, and without PTX nothing crosses a major version), `cuda_max_good` ≥ the
   toolkit, `machine_id` not excluded; take the cheapest. Estimate the cost (§10). `--vamos-dry-run` prints the plan and exits 0.
3. **Confirm:** an estimate within the budget proceeds; above it, an interactive terminal asks
   `Estimated $X exceeds the budget $Y (ledger $L of $C). Rent? [y/N]`; a non-interactive run
   needs `--vamos-confirm-spend=` ≥ the estimate, or exits 5 having rented nothing.
4. **Create:** write the lease file first (run-id, site, deadline), then
   `vastai create instance <offer> --image <runtime image> --ssh --direct --disk <gb>
   --label vamos-<run-id> --cancel-unavail --raw` (`--cancel-unavail`: fail instead of leaving a
   stopped instance that still bills storage). A `create` that fails because the offer was taken
   moves to the next offer, three times at most. Record the instance id in the lease and the
   ledger, then start the watchdog (§10).
5. **Wait for running:** poll `vastai show instance <id> --raw` every 10 s for `actual_status ==
   running` and `ssh_host`/`ssh_port`, up to `provision_timeout_s`. On timeout: destroy, add the
   machine to the run's exclusions, try the next offer once if the budget still allows, else exit 4.
   Measured: 46-72 s from create to running (`results/run_RTX_4090.out`, `run_H100_PCIEx8.out`).
6. **Key:** `ssh-keygen -t ed25519 -N '' -f <run>/id` makes a key for this run only;
   `vastai attach ssh <id> "<contents of id.pub>"` (a public key on argv is harmless). ssh and scp use
   `-i <run>/id -o IdentitiesOnly=yes -o UserKnownHostsFile=<run>/known_hosts
   -o StrictHostKeyChecking=accept-new -o ForwardAgent=no -o ForwardX11=no -o BatchMode=yes
   -o ConnectTimeout=10 -o ServerAliveInterval=15`. Vast reuses proxy ports, so a run-local
   known_hosts keeps `~/.ssh/known_hosts` clean, and the user's own keys are never attached to
   a third-party instance.
7. **Ship:** scp `farm_gpu`, `params.bin`, `run.sh` and `MANIFEST.in` (sha256 of the three) into
   `/root/vamos-<run-id>/`, retrying for up to 5 minutes (auth can take a while to settle; measured
   33-108 s from running to shipped). `run.sh` is vamos-generated and does only this: check the
   manifest (`sha256sum -c`), print `nvidia-smi --query-gpu=index,name,driver_version,compute_cap,
   memory.total,clocks.max.sm --format=csv,noheader`, run `FARM_GID0=0 FARM_PARAMS=params.bin
   FARM_OUT=out.bin [FARM_DUMP=dump.bin FARM_WMAX=w] timeout <s> ./farm_gpu <cap> <N> <block> 1 <gpus>`,
   write `MANIFEST.out` (sha256 of the outputs), and print `VAMOS-REMOTE rc=<rc>`.
8. **Run:** one ssh session, `sh /root/vamos-<run-id>/run.sh`, under a local timeout equal to the
   time left before the deadline minus the teardown margin; stdout streams into `session.log`
   (redacted) and FARM lines are parsed as they arrive. The remote exit code comes from
   `VAMOS-REMOTE rc=`, never from a pipeline (§16). Remote text reaches the terminal with control
   characters escaped (no escape-sequence injection from the host).
9. **Collect:** scp `out.bin`, `dump.bin`, `MANIFEST.out`; check the hashes (transfer integrity),
   recompute AGG/CAGG from `out.bin` and compare with the FARM line. Everything returned is
   untrusted input: file sizes must match N and the record layout before anything is parsed,
   nothing from the host is executed or used as a path, and rebuilt log lines come from vamos's
   own formats (§3), the host supplying only numbers.
10. **Destroy:** `vastai destroy instance <id> -y`, then poll `show instance` until it is gone
    (retries with backoff for 2 minutes; on failure a loud error with the id and the exact command,
    and the watchdog keeps trying). Close the lease, write the ledger entry (lifetime, `dph_total`,
    cost). Destroy comes **before** certification: billing stops as soon as the data is home.
11. **Certify and report** (local, §12), rebuild logs, run the merge hook, print the summary, exit
    per §2.4.

Signals (Ctrl-C, SIGTERM, SIGHUP) anywhere in steps 4-10 go to the destroy path first; then vamos
ends with the same signal (as `./simv` does for nvc runs today). Multi-GPU nodes pass `<gpus>` to the binary
(contiguous gid slices per card, `farm.cu`'s scheme); multi-node fan-out (phase F4) rents K
instances, gives each a `FARM_GID0` slice, and joins the records.

## 10. Cost guards

| guard | how |
|---|---|
| price cap | offers above `max_dph` (site, or `--vamos-max-dph`) are never considered |
| per-run budget | estimate = `dph_total` x (provision p90 + ship + 1.5 x run estimate + teardown) + the offer's transfer prices (`inet_up_cost`, `inet_down_cost`) x the bytes shipped and returned, compared with `budget_usd` / `--vamos-budget` before renting |
| run estimate | from a calibration record for (model sha256, card class) when one exists (every run writes one), else from rules 1-2 with the model's cells and the resource report; with neither, `--vamos-farm-minutes` is required |
| confirmation | above the budget: interactive y/N, or `--vamos-confirm-spend=<usd>`; else exit 5, nothing rented |
| hard deadline | `min(site max_minutes, --vamos-farm-minutes, budget / dph_total)`: the budget becomes a time limit, so the worst case is `dph_total x deadline ≤ budget` whatever the estimate said |
| four destroy paths | (1) vamos's own `finally` and signal handlers; (2) a **watchdog**: `vamos --vamos-farm-watchdog=<lease>` started with `setsid` (outlives the terminal and a `kill -9` of vamos), sleeps to the deadline, destroys and verifies, exits early when the lease is closed; (3) `timeout` on the remote run; (4) **reap on start**: every vamos invocation first looks at the lease directory and destroys the instance of any lease whose deadline has passed. Vast.ai has no instance lifetime of its own (the 1.8.3 CLI's `create instance` has none), so the guard has to be client-side |
| WSL lifetime | on a Windows laptop the watchdog lives in the WSL VM, and WSL processes die when the last `wsl.exe` session exits (`gpubuild`'s own field note). A vast run therefore warns when it cannot keep a session (it starts the watchdog through a detached Windows-side `wsl.exe` when it can), and path (4) catches what is left at the next vamos command |
| orphans | every instance is labelled `vamos-<run-id>`; `vamos --vamos-farm-reap` lists labelled instances on the account and destroys, after asking, those no live lease owns |
| ledger | `~/.local/state/vamos/vast-ledger.jsonl`, appended under a file lock (concurrent runs): one line per rental (instance, offer, `dph_total`, created, destroyed, cost, run-id, exit). Before renting: ledger total + estimate ≤ `spend_cap_usd` (the user's $20 for this experiment), else the same confirmation as the budget. `vamos --vamos-spend` prints it |
| storage | `--cancel-unavail` at create; destroy (not stop) at the end |
| card fit | gate G4 refuses a card the binary cannot launch on (frame x resident threads > VRAM, the EH2 lesson) before renting it |

The estimate's provisioning part comes from the ledger's p90 once there are five rentals; until then
3 minutes (the committed logs show 1.3-3 minutes from create to shipped).

## 11. Secrets

### 11.1 Where the key lives

vamos never reads the Vast.ai API key. The vastai CLI 1.8.3 resolves it itself, in this order
(`vastai/cli/main.py`, `vastai/cli/util.py`): `--api-key` (vamos never passes it), the `VAST_API_KEY`
environment variable, a 2FA session key `~/.config/vastai/vast_tfa_key` when one exists, then
`~/.config/vastai/vast_api_key` (`$XDG_CONFIG_HOME/vastai/` when set; a legacy `~/.vast_api_key` is
copied there on first use). The recommended setup keeps the key off every command line and out of
shell history:

```
(umask 077; mkdir -p ~/.config/vastai; cat > ~/.config/vastai/vast_api_key)   # paste the key, then Ctrl-D
```

`vastai set api-key <KEY>` is the CLI's own way, but it puts the key on argv (visible in `ps` to
other users while it runs, and saved in shell history), and it writes the file with the process
umask, which is 0022 here: a world-readable key file. `vamos --vamos-sites` and every vast run check
the modes of both key files with `stat` (without reading them) and refuse a group- or world-readable
one, as ssh does for private keys.

### 11.2 Rules for vamos's own processes

1. No command line vamos runs ever carries the key: `vastai` gets no `--api-key`; nothing is run
   through `bash -c "VAST_API_KEY=..."` (that puts the key in bash's argv, readable in
   `/proc/<pid>/cmdline` by every user, and in the Windows `wsl.exe` command line when launched from
   Windows); the Windows launchers (plan §2b) never forward `VAST_API_KEY` through `WSLENV`.
2. Only `vastai` children inherit `VAST_API_KEY`, and only if the user set it. Every other child
   (ssh, scp, ssh-keygen, podman, the kit, the farm binaries, the merge hook) gets an environment
   with it removed (`sites/secrets.child_env`).
3. `--curl` and `--explain` are never passed: the 1.8.3 CLI's `--curl` prints the request with the
   full `Authorization: Bearer <key>` header (`vastai/api/client.py`, `as_curl_command`).
4. `session.log` and every saved stderr go through a redaction filter (bearer tokens, 64-hex strings)
   as a second line of defence. The CLI itself prints only the key's last characters in its
   auth-failure hint.
5. The key never goes to the instance: it is not in the shipped files, the run script or the remote
   environment, and the instance has no way to call the API.

### 11.3 `vast_run.sh` and the kit's rental scripts (checked)

No command line in `vast_run.sh` carries the key: its `vastai show instance <id> --raw` and
`vastai attach ssh <id> "<public key>"` calls take it from the environment. But:
- it **requires** `VAST_API_KEY` in the environment (`: "${VAST_API_KEY:?...}"`), although the CLI
  would read the key file. The documented invocation, `VAST_API_KEY=... ./vast_run.sh` (gpubuild
  README, `gpu_farm.md` "Reproduce", the headers of `vast_bench.sh` and `vast_build_run.sh`), saves
  the key in shell history, and run as `wsl bash -lc "VAST_API_KEY=... ..."` (how Linux tools are
  started from Windows here) it is in bash's argv and the `wsl.exe` command line;
- it `export`s the key, so every child inherits it: ssh, scp, timeout, tee, python3. The stock
  `ssh_config` here sends only `LANG LC_* COLORTERM NO_COLOR` (and `~/.ssh/config` has no
  `SendEnv`), so it does not leave the machine today; but a `SendEnv` pattern matching it in any ssh
  configuration would transmit it to the rented host's sshd (a stock sshd drops variables outside
  `AcceptEnv`, but the host's owner controls that sshd), contrary to "never copied to the instance";
- `vast_bench.sh` and `vast_build_run.sh` do the same `export` (their `search`, `create` and
  `destroy` calls carry no key on argv either).

### 11.4 What ships, and to whom

A farm binary contains the DUT netlist as machine code and every memory image the design preloads
(the hazard3 binary contains the firmware). `gpubuild`'s security model applies: SASS only for
third-party hosts (PTX is much easier to reverse), certification instead of trust. vamos adds:
`ship_ok: true` in the spec before any `third_party` site gets a binary of this design, and a plan
line listing what ships (`farm_gpu: model of soc, 1253 cells; memory images: i_ram (128 words)`).

## 12. Certification

A cloud host can read and alter anything it runs, so results count only when checks it could not
predict pass (`gpubuild/index.html`, "Security model").

**Before renting (local, free):**

| gate | check |
|---|---|
| G1 model vs oracle | the reference nvc run and the CPU farm binary, same plusargs, cap lowered to the G1 prefix: identical rebuilt lines (stream, done/cap line, cycle count); plus the golden when the spec has one. For hazard3 the portable variants' `golden.tohost` and `ref_cycles` make this cycle-exact |
| G2 CPU determinism | the CPU farm at N = 256 with `OMP_NUM_THREADS=1` and with 8 threads: identical AGG and CAGG |
| G3 local GPU vs CPU | when a local GPU exists (the T1000 here): the GPU binary at N = 256 and N = 4,096 gives the CPU's AGG, CAGG and records. This tests the exact binary that ships (the sm_75 SASS here, while the host runs sm_89: the on-host checks cover that) |
| G4 resources | registers, spill, stack frame per arch; refuse a card where frame x resident threads exceeds VRAM; warn when spill puts the design in rule 2's regime |
| G5 offer fit | the card's `compute_cap` covered by a built SASS arch (§9 step 2); driver CUDA ≥ toolkit |

**After the run (local, after the instance is destroyed):**

1. **Instance 0**: its CHK and done cycle equal the CPU farm's for the same parameters (computed
   locally before the run), and the golden's where there is one (`farmkit expect`). This is
   `gpu_farm.md`'s rule.
2. **Spot checks**: K gids (default 16) drawn with `secrets.SystemRandom` **after** the results are
   home, replayed on the local CPU farm binary; CHK, done cycle and status must match. The host cannot
   know which instances will be checked. Cost for hazard3: a few seconds of CPU per gid.
3. **Population**: AGG and CAGG recomputed from the records equal the FARM line's (or the joined
   lines'); for a run that repeats an earlier (N, cycles, parameters) on another card class or split,
   they must equal the earlier ones (`gpu_farm.md`'s cross-card rule; the calibration records keep
   them).
4. **Golden end to end** when the spec has one, e.g. the merged image's md5.
5. **Transfer integrity**: `MANIFEST.out` hashes (truncation, not tampering: checks 1-4 catch that).

Any failure: exit 3, results kept in the run directory, nothing merged, the ledger entry marked.

## 13. Hazard3 Mandelbrot on the farm: the user's experiment

### 13.1 The workloads, and what compares with what

| workload | cycles | what it measures |
|---|---|---|
| portable variants `mandel8_i16`, `mandel16_i8`, `mandel32_i4` (`tests/hazard3_mandelbrot`) | 35,720 / 95,038 / 247,044 | certification: TOHOST stream = `golden.tohost`, done cycle = `ref_cycles` (Verilator, Icarus and vamos/nvc agree on both) |
| the original benchmark: 1024x1024, 256 iterations, one SoC | 4,637,655,132 | **one simulation**: Verilator 5.032 3.5-3.7 MCycles/s here (1,240-1,274 s); vvp ~3-4 kCycles/s; vamos/nvc ~2.1 kCycles/s; Verijit claims 766.8 MCycles/s (≈ 6.0 s) |
| the same image as a **split render**: the kit's farm SoC (`tile_id` input read at `0x1004`) and tile firmware (no image buffer, so no 4 MB memset), one instance per tile | 5.06-5.12e9 in total (§13.2: per-pixel tile bookkeeping costs more than the memset saves) | **aggregate**: N independent simulations whose merged output must equal the native golden image (md5 `693d2391e979a114a82af00b3e64e54c`) |

The split render answers "how fast can the stack produce this image, cycle-accurately per tile". It
does **not** answer "how fast does one Hazard3 run 4.6 billion cycles": for that, rule 4 says the
fastest engine in this stack is the gsm model on one CPU core, and no GPU helps. Reports must keep the
two apart, as `gpu_farm.md`'s Yuri section does ("aggregate over seeds, not latency of one run").

### 13.2 Projection (not a measurement)

**Measured inputs** (2026-10-03, this machine, the kit port's builds). The model: 1,409 comb cells and
144 registers as first generated (`RESET_REGFILE=1`); 1,253 comb cells and 112 registers with the
register file as a memory (`RESET_REGFILE=0`, which a 2-state model may use: its registers start at
0 either way, and both give the same checksums and done cycles). One CPU core (`taskset`, best of 2):

| model variant (128-word instruction RAM, 512-word data RAM, `mandel16_i8`) | state per instance | cycles/s |
|---|---:|---:|
| as generated (memories in 64-bit words) | 6,336 B | 1.59e6 |
| kit memory prep (instruction RAM a shared ROM, data RAM in 32-bit words) | 3,200 B | 1.64e6 |
| + 32-bit carriers (`GSM_U32=1`) | 2,632 B | 1.97e6 |
| memory prep, register file as a memory | 3,072 B | 2.22e6 |
| the same + 32-bit carriers (the kit port's build) | 2,632 B | 2.56e6 |

The kit port's final CPU farm runs both workloads at the same rate: 2.49e6 cycles/s on one core for
the throughput workload (every instance `mandel16_i8`: checksum = `golden.tohost`'s, done cycle
95,038 = `ref_cycles`) and 2.55e6 for the split render (16-pixel tiles; tile 0 matches the native
golden and the Verilator twin's done cycle, 15,614). The port's own certification log
(`hazard3/results/cert_cpu.log`) has the Verilator twin of the same farm SoC at 3.0e6 cycles/s on one
core, so the compiled gsm model runs at about 0.8x Verilator here.

**The split render's total and balance.** An independent count of the image's Mandelbrot iterations
(not kit code) gives 108,623,046, the figure measured on the original run. A cost model fitted to the
Verilator twin's done cycles for 192 tiles (`58.6 + 72.8 x pixels + 45.8 x iterations` per tile,
largest error 0.04 %) gives the split render's total, 5.06-5.12e9 cycles: about 10 % more than the
single run's 4.64e9, because each pixel now carries tile bookkeeping and a TOHOST store. A kit tile
is a column of 2^m pixels; the slowest tile runs 2.2-2.4x the mean for every m (mean/max = 0.41 at
m = 0 and 2, 0.43 at m = 4, 0.45 at m = 6-10). The kernel waits for its slowest instance, so:
- with every tile resident at once (m = 4: 65,536 tiles), the kernel takes the slowest tile's time,
  and only 43 % of the card's instance-cycles do useful work;
- with many more tiles than resident threads (m = 0: 1,048,576 one-pixel tiles, ≥ 5 per resident
  thread on a 4090), the block scheduler hands new tiles to free slots and the tail is one short
  tile: the projection takes 80-90 % for this case. That is the recommended split.

**Two regimes from `gpu_farm.md` bracket the GPU rate** for 1,253 comb cells:
- **A, rule 1 (register-resident):** aggregate = K_card / 1,253. Servant (466 cells, 8.7 KB of AoS
  state, frame 0) and b22 (1,000 cells) sit here.
- **B, rule 2 (spill):** b17 (2,517 cells, 3.4 KB spill) ran 3-5x below the constant; its measured
  cell-evals/s per card, divided by 1,253.

Which applies depends on the state an instance touches every cycle. The split build has no image
buffer (16-word data RAM; the instruction RAM is the shared ROM), so its state is nearly all
registers: about 650 B per instance (the 2,632 B variant less 2,048 B of data RAM, plus 64 B). At
65,536 resident instances that is 42 MB, inside the 4090's 72 MB L2, which favours regime A; at
full occupancy (197k resident threads) it is 128 MB, beyond the L2, which pushes toward regime B and
an optimum below saturation, as b17 showed (best at 16k). G4's ptxas numbers decide; the sweep
measures.

| card | $/h paid (2026-09) | A: inst-cyc/s | B: inst-cyc/s | split render (m = 0), kernel only | vs Verilator 1 thread (aggregate) |
|---|---:|---:|---:|---|---|
| RTX 4090 | 0.36 | 3.2e9-4.5e9 | 9.4e8 | A 1.3-2.0 s · B 6.1-6.8 s | A ≈ 880-1,230x · B ≈ 260x |
| H100 SXM | 2.94 | 2.2e9-3.4e9 | 5.4e8 | A 1.7-2.9 s · B 11-12 s | A ≈ 610-940x · B ≈ 150x |
| A100 40GB | 0.67 | 1.3e9-2.0e9 | 3.5e8 | A 2.8-5.0 s · B 16-18 s | A ≈ 350-550x · B ≈ 97x |
| RTX 3090 | 0.11 | 1.3e9-2.0e9 | 2.3e8 | A 2.8-5.0 s · B 25-28 s | A ≈ 350-550x · B ≈ 62x |
| T1000 (local, 4 GB) | 0 | ≈ 1.7e8 | ≈ 3.6e7 | A 34-38 s · B 2.6-2.9 min | A ≈ 46x · B ≈ 10x |
| this CPU, 16 cores / 32 threads | 0 | 4.1e7-5.1e7 | | about 1.7-2.1 min | ≈ 11-14x |

Arithmetic: A = K_card / 1,253 with K from rule 1 (4090 4-5.6e12, H100 SXM 2.8-4.3e12, A100/3090
1.6-2.5e12); B = b17's plateau x 2,517 / 1,253 (b17's plateaus: 4090 4.67e8, H100 SXM 2.68e8, A100
1.76e8, 3090 1.13e8 inst-cyc/s; e.g. 4.67e8 x 2,517 / 1,253 = 9.4e8); T1000 = its `gpu_farm.md`
column, measured at N = 4,096 (b22 2.10e8 x 1,000 and b17 1.81e7 x 2,517 cell-evals/s) / 1,253; CPU
= 2.55e6 per core x 16-20 effective cores (SMT adds little), OpenMP's dynamic schedule balancing the
tiles. Kernel time = 5.12e9 / (rate x 0.8-0.9). Verilator: 3.64e6 cycles/s here (1,274 s for the
single run). Verijit's claimed 766.8 MCycles/s for one run is just below regime B's aggregate for one
4090: comparable numbers for different kinds of run.

End to end, provisioning dominates: create to running 46-72 s and running to shipped 33-108 s in the
committed logs, against seconds of kernel. More cards shorten only the kernel part, and only while
N ≥ cards x N_sat (rule 5). For **one** image the local CPU farm, about two minutes and no rental, is
the yardstick a rental has to beat once provisioning is counted. The GPU wins clearly for repeated or
larger work (many images, parameter sweeps, seeds), which is what the farm is for, and the
throughput workload (N copies of `mandel16_i8`, 95,038 cycles each) measures exactly that.

### 13.3 The experiment under the $20 budget

All of it fits far inside $20; the plan still runs under the guards of §10, and asks before any step
that would pass the cap. Until phase F3 exists, these steps run through the kit's own hazard3 scripts
with the same guards applied by hand (price cap, deadline, destroy, ledger); after F3 each step is a
`./simv --vamos-farm=<N> --vamos-site=<site>` line.

| step | where | what | expected cost |
|---|---|---|---|
| 0 | this machine | models and CPU farms for both workloads (the kit port: certified against `golden.tohost`/`ref_cycles`, the native tile goldens and its Verilator twin; 2.5e6 cycles/s per core), G2, G3 on the T1000 (the sm_75 SASS), local sweep N ≤ 16k; the split render once on the CPU farm (about 2 minutes) as the yardstick | $0 |
| 1 | 1x RTX 4090, cap $0.60/h, deadline 30 min | ship the two `farm_gpu` binaries (throughput, split; sm_75+sm_89 SASS); throughput workload: certify (CHK0 = `golden.tohost`'s fold, done cycle 95,038), then N sweep {4k, 16k, 32k, 65k, 131k, 262k} for the plateau; split render at m = 0, 2, 4 (1,048,576 / 262,144 / 65,536 tiles) with `FARM_DUMP`; certify (instance 0 and AGG against the native goldens the port computed, spot checks, CAGG); merge; md5 = `693d2391e979a114a82af00b3e64e54c` | ≈ $0.10-0.20 (worst case $0.30 at the deadline) |
| 2 (optional, maximum speed) | the largest 4090 node offered within $4.50/h (8x, else 4x), deadline 20 min | the split render at m = 0 across all cards (contiguous gid slices); AGG/CAGG equal to step 1's | ≈ $0.80-1.50 (worst case $1.50) |
| total | | | ≈ $1-2; ledger cap $20 |

H100s are not in the plan: on this integer, no-FP workload the 4090 is faster per card and an order
of magnitude cheaper per instance-cycle (`gpu_farm.md`, cost section).

## 14. Phases and exit criteria

| phase | deliverable | exit criterion |
|---|---|---|
| F0 kit contract | (kit owner) `farmkit` entries and the binary contract of §7.2-7.3; (sv2ghdl) installed `gen_statemachine` with `--version` and the JSON sidecar | `farmkit version` reports contract 1; the hazard3 harness builds through `farmkit build --target gpu --nvcc gpubuild/gpu-cc.sh` with no absolute paths |
| F1 local farm | spec, model, kit calls, CPU build, G1/G2, `./simv` and `--vamos-farm=N` on `local`, replay, logs, exit codes | hazard3 `mandel8_i16`/`mandel16_i8`/`mandel32_i4`: instance 0's rebuilt lines equal `golden.tohost` and the nvc run's, done cycle = `ref_cycles`; AGG equal for 1 and 8 threads |
| F2 GPU build + local GPU | GPU build via `gpu-cc.sh`, resource report, G3/G4 on `local-gpu` | the T1000 reproduces the CPU's AGG/CAGG/records at N = 4,096 |
| F3 vast | `sites/` (config, vast, ledger, watchdog, secrets), cost guards, `--vamos-dry-run`, `--vamos-sites`, `--vamos-farm-reap`, `--vamos-spend` | the offline suite (§15) passes with fake `vastai`/`ssh`; then, with the user's approval, one real step-1 run of §13.3 under a $1 budget: certified, destroyed, ledger correct |
| F4 scale | multi-GPU nodes, multi-node fan-out by `FARM_GID0` slices, calibration records, `gpu-ssh` sites, nvc-accel model source for VHDL | step 2 of §13.3: AGG/CAGG equal across 1 and g cards |
| F5 spec derivation | derive specs from simple testbenches (§3) | the hazard3 portable testbench yields the hand-written spec |

## 15. Tests

All offline; nothing in the suite rents, and nothing needs a key. Fakes are small executables in
`tests/vamos/fakes/` put first on PATH (`vastai`, `ssh`, `scp`, `ssh-keygen` where needed): each
appends its argv and the names (not values) of its environment variables to a journal and answers
from canned JSON; the fake `ssh` runs the "remote" command in a temporary directory with the CPU
farm binary standing in for `farm_gpu`.

| test file | what it pins |
|---|---|
| `test_farm_spec.py` | spec parsing: every key, unknown keys, missing inputs, bad formats, `split.count` vs N, `ship_ok` |
| `test_farm_params.py` | plusarg precedence (command line, template, file), `{i}`/`{n}`/`{seed}`, `+ntb_random_seed[_automatic]`, unmapped-plusarg warnings, `+vcs+finish+` to cycles |
| `test_farm_results.py` | record/stream readers, AGG/CAGG recomputation, joining gid slices, balance, status counts; hostile input: truncated, oversized and mis-headed files are refused before parsing, control characters in remote text are escaped |
| `test_farm_logs.py` | line rebuilding from streams against `tb_hazard3.v`'s formats |
| `test_farm_exit.py` | the exit table of §2.4, including 128+N after destroy |
| `test_vast_plan.py` | offer filtering (price cap, `compute_cap`, `cuda_max_good`, exclusions), estimates, budget and ledger refusal (exit 5 and **no `create` in the journal**), `--vamos-dry-run` |
| `test_vast_lifecycle.py` | destroy on success, on certification failure, on remote failure, on SIGINT, on deadline; destroy before certify; lease and ledger entries; `--cancel-unavail` and `--label` present |
| `test_vast_watchdog.py` | `kill -9` of vamos mid-run: the watchdog destroys at the deadline; a closed lease stops it; with the watchdog killed too, the next vamos invocation reaps the expired lease |
| `test_vast_secrets.py` | with `VAST_API_KEY=<sentinel>` set: the sentinel is in no child's argv, in no non-vastai child's environment, in no file under the run directory; `--curl`/`--explain`/`--api-key` never appear; a 0644 key file is refused (mode check without reading) |
| `test_vast_reap.py` | labelled orphans listed, destroy only after confirmation, live leases left alone |
| `test_farm_e2e_cpu.py` (skips without the kit) | F1's exit criterion on the three variants |
| `test_farm_e2e_gpu.py` (skips without podman, the kit or a local GPU) | F2's exit criterion |

Plus a `regress/` block, `hazard3/vamos-farm`, beside `hazard3/vamos`: the three variants through
`vcs --vamos-engine=gpu-farm` and `./simv`, compared with the goldens as the other blocks are.

## 16. Findings in the existing flow

Found while reading for this design; none is fixed here (no changes in the shared repos).

1. **`vast_run.sh` reports every remote run as successful.** `echo "$(date +%T) RUN-EXIT=$?"`
   follows `timeout ... ssh ... | tee`, so `$?` is the pipeline's last status, `tee`'s (no
   `pipefail`); a remote exit 7 logs `RUN-EXIT=0` (reproduced in WSL), a `RUN_TIMEOUT` expiry too,
   and the script then `exit 0`s. Fix: `set -o pipefail` or `${PIPESTATUS[0]}`, and exit with it.
2. `vast_run.sh` requires `VAST_API_KEY` in the environment and exports it to every child (§11.3);
   it attaches `~/.ssh/id_rsa.pub` only (fails for ed25519-only users) and uses the global
   known_hosts with `accept-new` on reused proxy ports.
3. `vast_run.sh` never destroys the instance (only the kit's `vast_bench.sh`/`vast_build_run.sh` do,
   from an EXIT trap that a `kill -9` or a closed WSL session skips).
4. `vastai set api-key` writes the key file with the umask (0644 here) and takes the key on argv (§11.1).
5. The kit's scripts hard-code tool paths, several of which do not exist on this machine:
   `NVCC=/home/claude/tools/cuda-redist/bin/nvcc` (`build_gpu.sh`, `rtlm_build.sh`, not
   overridable), `V=~/.local/bin/vastai` (`vast_bench.sh`, `vast_build_run.sh`; the CLI is in
   `/home/claude/vastai-venv`), `/usr/local/src/nvc/build/bin/nvc` and `yosys/libgsm.so`
   (`build_models.sh`); `soa_prep.py` runs `/usr/local/src/sv2ghdl/yosys/make_soa_model.py` by
   absolute path (present, but tied to this layout). `rtlm_build.sh` passes the kit's harness by
   absolute path, which `gpu-cc.sh` cannot see (it mounts only `$PWD`).
6. `gpubuild/build_farm.sh` passes only `-DMODEL_C`, so it builds self-contained harnesses (`yuri.cu`,
   the hazard3 port) but not `farm.cu`, which also needs `-DSTIM_H`; it has no hook for extra nvcc
   flags (`-Xcicc -O1` for big models), and its default `GENC` ships `compute_86` PTX, which the
   security model advises against for third-party hosts. `gpu-cc.sh` runs only `nvcc`, so a
   resource report needs a second compile instead of `cuobjdump` on the built binary.
7. `farm.cu` returns only CHK0 and AGG (the CHK vector stays in memory), its SoA mode is one GPU
   only, and it has no gid offset for multi-host slices (the hazard3 port adds `H3_GID0`).
8. `gen_stim_v.py` asserts a reset port, so self-resetting designs (the hazard3 SoC) need a hand-written harness.
9. `gpu_farm.md`'s rule 1 table has its header and no rows: `report_rules.py` reads
   `results/vast_*.log`, but the committed logs are `results/run_*.out` (`vast_*.log` is
   gitignored), so the table cannot be regenerated from the repository. The Yuri section cites raw logs
   (`vast_RTX_4090x1_51099583.log`, `..._51098213.log`) that are not committed either.
10. `gen_statemachine` is not built by any sv2ghdl target; several copies live under `/home/claude`.

## 17. Open questions for the user

**Decided 2026-10-03** (after the first Hazard3 run, `bfit/benchmarks/gpu_farm.md`):
- **Q1/Q2, licensing:** anything not inherited as GPL is PolyForm (Noncommercial 1.0.0), as the
  kit already is. So there is no GPL duplicate of the kit's harness: whatever vamos adds on the farm
  side is PolyForm, and the contract (§7.2-7.3) lives with the kit. vamos itself stays
  GPL-3.0-or-later and runs the kit only as a subprocess, through that contract (confirmed).
- **Q8, the default model:** the default for logic simulation is the 3D-logic family; 2-state only on
  request. So `./simv` after a farm compile runs the nvc 3D reference, and the 2-state gsm model (CPU
  or GPU farm) runs only on an explicit request (`--vamos-farm=N`, or a 2-state option).
- **Q4, budget:** a $10/day limit on GPU spend for now, as a safety cap (replacing the $2/run, $20
  ledger defaults above).
- **Q9, single-run speed:** not pursued here. vamos/sv2ghdl aims to be an accuracy-capable framework,
  not a fast digital simulator; fast single runs come from federation with Verilator and Verijit.

The remaining questions (Q3, Q5-Q7, Q10, Q11) are still open.

1. **Who writes contract v1?** The kit is PolyForm-NC and yours; vamos needs §7.2-7.3 on the kit
   side (the hazard3 port already has `H3_GID0`/`H3_DUMP`, close to it). Or would you rather vamos
   carry its own GPL harness generator, so commercial vamos users need no bfit licence? That
   duplicates the kit, so this design assumes the kit.
2. **Commercial users.** Should the first gpu-farm compile require an explicit acknowledgement of
   PolyForm-NC (a config key), or is the provenance note enough?
3. **Exit status.** Is the §2.4 table right, in particular VCS semantics (0) for instances that hit
   a cap the testbench handles with `$finish`, and the separate codes 3/4/5?
4. **Budget defaults.** Per-run $2 and a $20 ledger cap per account, both overridable; confirmation
   above them. Should the $20 be a hard stop (no confirmation possible) instead?
5. **Which cards to allow by default:** 4090-class only (best $/instance-cycle here), or any card
   that passes G4/G5 under the price cap?
6. **`ship_ok`.** Default deny for third-party sites, even for public designs like hazard3: one
   line in the spec per design. Acceptable?
7. **Spot-check size.** 16 instances replayed locally (seconds for hazard3; minutes for core-sized
   designs at ~10^4 cycles/s per core). More, fewer, or proportional to N?
8. **`./simv` default for a farm compile:** one instance on the CPU model (fast, 2-state), as
   proposed, or the nvc reference (slow, VCS-faithful) with the farm only on `--vamos-farm=`?
9. **The single-run question.** For the unmodified 4.6e9-cycle benchmark the best this stack can
   offer is the gsm model on one CPU core: at the measured 2.5e6 cycles/s that is about 31 minutes,
   against Verilator's 21 here (it needs the original SoC's 2^24-word RAMs in the model, about
   128 MB of state for one instance). Do you want that run and reported next to Verilator as its own
   row (it is not a farm result)?
10. **Fan-out across several rentals** (phase F4) multiplies provisioning risk and ledger entries.
    Wanted for this experiment, or only multi-GPU single nodes?
11. **A last-resort stop from the instance itself.** Every destroy path above runs on your machine,
    because the instance must never hold the key. Ending the container at the deadline from the run
    script would stop GPU billing even if this machine were off, if Vast.ai stops GPU billing for an
    exited container (to be checked, cheaply, on the first real run). Worth adding?
