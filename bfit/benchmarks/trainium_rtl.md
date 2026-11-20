# RTL simulation on Trainium: a 128×128 systolic array stepped in SBUF, 23× Verilator

*Measured 2026-11-15 on a trn1.2xlarge (one Trainium1 chip, one of its two
NeuronCores used) in AWS us-east-1. Kit: `trainium/` (RTL, Verilator twin,
the three NKI kernels, the device-clock harness, launch and run scripts,
raw logs). Rental spend for the hardware run: $0.13; for the whole
Trainium campaign including three failed SageMaker attempts: about $2.22.*

The GPU farm (`gpu_farm.md`) buys throughput by breadth: thousands of
independent instances of a design, each stepped by one thread. This
document is about depth: one instance of one synchronous block, stepped as
fast as the hardware allows. The question was whether the two-phase
(bank-swapped) evaluation that the mailbox FPGA array and the GPU farm use
maps onto a Trainium NeuronCore well enough that a single simulation beats
Verilator. It does, by 22–23× against one Verilator thread and 14–15×
against eight, with every output word bit-exact against the RTL.

## The testcase

`trainium/sa.sv`: an N×N weight-stationary systolic MAC array, int8 × int8
→ int32. Each PE holds 48 bits of state (`a_q`, `w_q`, `p_q`); weights
shift down the columns while `load_w` is high, activations flow right
along the rows, partial sums flow down and leave at the bottom edge.
N = 128 gives 16,384 PEs and 786,432 state bits. Ports: `a_in` (128 × 8),
`w_in` (128 × 8), `load_w`, `p_out` (128 × 32).

The stimulus is the natural one: load W over N cycles, stream M activation
rows skewed by column (row k gets A[m][k] at cycle m + k), T = M + 2N
stream cycles, then de-skew the bottom outputs and check C = A·W. The same
A, W and skew conventions are used by the Verilator bench, the numpy golden
model and the NKI kernels, so every output word can be compared, not just
the final checksum.

Why this design: it is the largest regular synchronous block whose state
fits one NeuronCore's SBUF with room for stimulus (24 MB: 128 partitions ×
192 KB), it has no control flow, and it is the kind of block AWS's own
hardware people recognise.

## Method

**Verilator twin.** `trainium/tb_sa.cpp`, Verilator 5.032, `-O3 --x-assign
fast --x-initial fast --noassert`, C++ `-O2`, on P620A (Threadripper PRO
5955WX, WSL). Only the stream is timed, M = 20,000. The `--threads 8` build
carries Verilator's `UNOPTTHREADS` warning (its scheduler could not supply
the parallelism asked for), so the 8-thread figure is a soft bound, not
Verilator's best possible.

| N | PEs | Verilator 1 thread | 8 threads | PE-cycles/s (1T) |
| --: | --: | --: | --: | --: |
| 16 | 256 | 0.54 µs/cycle | | 4.7e8 |
| 32 | 1,024 | 1.37 µs/cycle | | 7.5e8 |
| 64 | 4,096 | 6.49 µs/cycle | | 6.3e8 |
| 128 | 16,384 | 28.61 µs/cycle (34,954 cycles/s) | 18.35 µs/cycle (54,501 cycles/s) | 5.7e8 (8.9e8 at 8T) |

Verilator's rate of PE evaluations is flat at about 0.6 G/s per thread, so
its time per cycle grows as N².

**Kernels.** Three NKI kernels (nki 0.7.0, neuronx-cc 2.28.7631), all with
port behaviour identical to the RTL. The rule from `GPU-TODO.md` applies:
any restructuring must keep the behaviour at the external ports exactly;
internal timing may change.

- `nki_sa.py` (**sa**): the literal two-phase model. Partition axis = PE
  row, free axis = PE column. `a` flowing right is a free-dimension shift
  (one `tensor_copy`); `p` flowing down crosses partitions, so it is an
  SBUF→SBUF DMA each cycle; W is shifted in by DMA over the 128 load
  cycles, which are executed like the RTL does. Two tiles per state array,
  loop unrolled by two with the roles swapped: that is the bank switch.
- `nki_sa2.py` (**sa2**): the layout turned 90°. Partition = PE column,
  free = PE row. The partial-sum flow is now a free-dimension offset,
  evaluated in place by one `tensor_tensor` on shifted views, with no data
  movement. The activation registers are a pure delay line fed only by the
  primary input, so their contents are an index into a per-column
  time-skewed copy of the stimulus prepared once on the host. Weights are
  stationary after the load phase, which has no observable output, so W is
  DMA'd in transposed once. Per simulated cycle: one multiply, one add, two
  column copies, all on wide 128×128 tiles, no DMA. The whole skewed
  stimulus is resident (T × N bytes per partition), which limits M to a few
  hundred.
- `nki_sa3.py` (**sa3**): sa2 with the stimulus streamed in 256-cycle
  chunks and double-buffered (2 × 32 KB in, 2 × 1 KB out, per partition),
  so SBUF use is constant in M. This is the production form; the source is
  in the appendix.

**Golden model.** `nki_sa.golden`: a numpy step model of `sa.sv` with int32
wrap. All T × N output words are compared, then the de-skewed matrix is
checked against A·W. sa, sa2 and sa3 were developed and verified on the
NKI CPU simulator (`nki.simulate`) with no hardware at all; sa3 was checked
there at M = 256 and M = 2048. On hardware the check was repeated at
M = 256 for all three kernels.

**Timing.** `nki_sa_hw2.py` compiles each kernel once, loads the NEFF once,
runs it once and checks the output, then calls `CompiledKernel.benchmark`
(nrtpy 2.35.33, device-side trace on the NeuronCore clock), warm-up 2,
5 iterations. Min and max are within 0.1 % of the mean in every row.

A trap worth recording: in nki 0.7 a `@nki.jit` kernel called on numpy
arrays re-traces, re-runs neuronx-cc, reloads the NEFF and re-allocates
device tensors on **every call**. The first hardware attempt timed that
path and reported 5–22 ms per simulated cycle, which is host overhead, not
the kernel. The compiler's own post-schedule latency estimate is not a
substitute either: measured time was 0.85–1.9 ns per estimate unit
depending on the kernel, so it ranks kernels but does not predict them.

## Results

trn1.2xlarge, one NeuronCore, `NEURON_LOGICAL_NC_CONFIG=1`.

| kernel | M | RTL cycles | device time | µs per RTL cycle | PE-cycles/s | vs Verilator 1T | vs 8T | check |
| :-- | --: | --: | --: | --: | --: | --: | --: | :-- |
| sa (row layout, DMA shift) | 256 | 640 | 1.024 ms | 1.600 | 1.02e10 | 17.9× | 11.5× | bit-exact on hw |
| sa2 (column layout) | 256 | 512 | 0.668 ms | 1.304 | 1.26e10 | 21.9× | 14.1× | bit-exact on hw |
| sa3 (column, streamed) | 256 | 512 | 0.654 ms | 1.277 | 1.28e10 | 22.4× | 14.4× | bit-exact on hw |
| sa3 | 8192 | 8448 | 10.360 ms | 1.226 | 1.34e10 | 23.3× | 15.0× | bit-exact at M=2048 on the CPU simulator; hw output not re-checked (golden too slow) |

sa's cycle count includes the 128 load cycles that the RTL executes; sa2
and sa3 skip them because they have no observable output.

Instruction counts from the compiler's metric store, per kernel and per
simulated cycle:

| kernel | M | Vector engine | Pool/GpSimd engine | Sync engine | per cycle |
| :-- | --: | --: | --: | --: | :-- |
| sa | 256 | 2,054 | 2,294 | 1,533 | 3.2 + 3.6 + 2.4, plus 88 MB of SBUF→SBUF DMA for the partition shifts |
| sa2 | 256 | 1,538 | 516 | 0 | 3 + 1 |
| sa3 | 256 | 1,539 | 519 | 0 | 3 + 1 |
| sa3 | 8192 | 25,378 | 8,519 | 0 | 3 + 1 |

The compiler put the two `tensor_tensor` ops and one copy on the Vector
engine and the other copy on the Pool engine. 1.23 µs for three Vector ops
on 128×128 int32 tiles is about 0.4 µs per op, which is the floor of this
formulation on one engine.

Artifacts of the run (NEFFs, per-kernel compile logs and metric stores)
are in `trainium/logs/`; the raw run log is `trainium/logs/run2_20261115_120308.log`.

## What it says

**1. Layout decides the cost; the RTL does not.** Same design, same port
behaviour, 20 % faster from sa to sa2 by choosing the partition axis so
that the data that moves between PEs every cycle moves along the free
axis. Movement along the free axis is an address offset and costs nothing;
movement across partitions is a DMA and costs the DMA ring every cycle
(sa's 88 MB of copy traffic in 640 cycles). For a general RTL block the
mapping step is: find the register-to-register flows, put the dominant one
on the free axis, and turn pure delay lines fed by primary inputs into
stimulus indexing, as sa2 does with `a_q`.

**2. The per-cycle floor is the instruction count, not the PE count.** One
wide op on a 128 × 128 × int32 tile costs about 0.3–0.4 µs on one engine
whether it covers 16 PEs or 16,384. A block that needs k such ops per
cycle runs at roughly 0.4k µs per cycle until the tile is full. Width is
free, op count is not. This is the opposite of Verilator, whose cost grows
with the design (N² here) at a flat 0.6 G PE evaluations per second per
thread, so the ratio keeps growing with design size up to the tile limit.
(Trainium at smaller N was not measured; the model predicts the same
1.2 µs per cycle down to N = 1.)

**3. Streaming keeps the footprint constant and costs nothing.** sa3's
chunked double buffer changed the per-cycle time by −4 % between M = 256
and M = 8192 while keeping SBUF use fixed; the DMA of the next chunk hides
under the compute of the current one. Cycle count is unbounded in this
form.

**4. Bank switching is free.** Two tiles per state array and two copies of
the cycle code with the roles swapped produce no extra instructions: the
"switch" is which tile the next instruction names. The first phase
(evaluate next from current) is the only work.

**5. Most of the chip is idle.** This used one of the two NeuronCores on a
trn1 chip (16 on a trn1.32xlarge), one of its compute engines for the
arithmetic, and no Tensor engine at all. The next experiments, in order of
expected return: split the four ops across the Vector, GpSimd and Scalar
engines so they overlap; split the array across the two cores along the
column axis, where the only cross-core traffic is one column of partial
sums per cycle, evaluated first as the boundary; and run the same NEFFs on
trn2 (NeuronCore-v3, eight cores per chip), for which the quota was still
zero when this was measured. A `neuron-explorer` capture would show the
engine occupancy; SDK 2.33 removed `neuron-profile`, so none was taken on
this run.

**6. The CPU simulator is enough to get the kernel right.** All three
kernels were written, debugged and verified word-for-word against the RTL
model with `nki.simulate` on a machine with no Neuron device; the hardware
was needed only for the number. Compile on the trn1 host took 2–5 s for
the M = 256 kernels and 18 s for sa3 at M = 8192 (fully unrolled, 0.72 MB
NEFF: `nl.sequential_range` is a plain `range` in nki 0.7).

## Cost and the path to the number

| attempt | route | what happened | cost |
| :-- | :-- | :-- | --: |
| 1 | SageMaker endpoint, ml.trn1.2xlarge us-west-2 | AWS's `pytorch-inference-neuronx` image crash-looped on `import pkg_resources`; fixed with a shim in the model archive | $0.93 |
| 2 | SageMaker endpoint | handler died on a wrong code path; endpoint idle | $0.70 |
| 3 | SageMaker endpoint | kernels ran bit-exact on hardware but the harness timed the recompile-per-call path | $0.46 |
| 4 | EC2 trn1.2xlarge us-east-1, on demand, 45-minute hard cap | the numbers above; billed 5 min 44 s | $0.13 |

The EC2 route needs the "Running On-Demand Trn instances" quota, which was
0 on a fresh account and was granted (8 vCPUs, us-east-1) about ten hours
after a Service Quotas request; SageMaker's `ml.trn1.2xlarge` endpoint
quota of 2 was available immediately, which is why the first three attempts
went that way. On-demand trn1.2xlarge is $1.34375/h in us-east-1; the
SageMaker endpoint is $1.5453/h.

## Addendum (2026-11-17): a tuned Verilator baseline, where the win ends, Trainium2

**Tuned Verilator.** The 8-thread figure above carried Verilator's `UNOPTTHREADS`
warning. A sweep on the same box with `-O3 --x-assign fast --x-initial fast
--noassert --no-timing`, `-CFLAGS "-O3 -march=native"`, threads pinned away from
the first cores, best of three runs (`trainium/logs/verilator_tuned_results.txt`):

| threads | 1 | 2 | 4 | 8 | 16 |
| :-- | --: | --: | --: | --: | --: |
| µs per cycle | 29.94 | 24.74 | 16.26 | 15.35 | 77.26 |

Native code buys nothing at one thread (29.9 against 28.6 µs is noise). The best
multi-threaded number improves from 18.35 to 15.35 µs at 8 threads; 16 threads
oversubscribe the SMT siblings and collapse. Against the tuned baseline the sa3
kernel at 1.23 µs is 24× one Verilator thread and 12.5× the best eight.

**Where the win ends.** The measured floor is 0.3–0.4 µs per wide operation and a
cycle costs one floor per dependent level of the design. The systolic array is one
level deep per cycle. A CPU core is not: Hazard3 is 137 levels as yosys emits it
and 19 after the depth pass of the GPU work (GPU-TODO.md section 1.4); at 0.3 µs a
level that is 40 µs or 6 µs per cycle against Verilator's 0.25 µs. The claim this
report supports is therefore: wide, shallow designs win by an order of magnitude on
Trainium; sequential cores lose by about as much. What is not yet measured: the
uncertainty plane (about 2× the data and the ops, so nearer 10× than 23×), a
CPU-class design on the device, and end-to-end host I/O. The three kernels here are
hand-written for this design; the general path, yosys → NKI through the sLUT
program format with both planes, is not built, and it is the same generator the
FPGA evaluator needs.

**Trainium2.** Not run. Only `trn2.48xlarge` exists (16 chips, 128 NeuronCores,
192 vCPUs; us-west-2 and us-east-2 only). Spot was $8.60/h in us-east-2c on
2026-11-17; the account's Trn quota in those regions is 0, so a 192-vCPU quota
increase precedes any run. The kernels run unchanged on one of its cores. Every
number above is Trainium 1.

## Addendum 2 (2026-11-18): the uncertainty plane and deep logic, measured

A second trn1.2xlarge session (5 minutes, about $0.12; `trainium/nki_hw3.py`, `run3_remote.sh`, log
`trainium/logs/run3_20261118_092234.log`), same method: one NeuronCore, device clock, warm-up 2, 5 iterations,
every output checked against numpy.

| case | cycles | µs per cycle | vs Verilator 1T (29.94) | vs best 8T (15.35) | check |
| :-- | --: | --: | --: | --: | :-- |
| sa3, 2-state (same-session reference) | 512 | 1.277 | 23.5× | 12.0× | OK |
| sa3u, value + uncertain plane | 512 | 1.842 | 16.3× | 8.3× | OK |
| sa3u, value + uncertain plane | 8,448 | 2.025 | 14.8× | 7.6× | OK |
| deep, 4 dependent levels | 1,024 | 2.051 | | | OK |
| deep, 19 dependent levels | 512 | 9.359 | | | OK |
| deep, 137 dependent levels | 128 | 66.761 | | | OK |

- **Full detail costs 1.44× to 1.6×, not 2×.** `nki_sa3u.py` carries an uncertain flag per word beside the value
  (a result is uncertain when any input is; P registers power on uncertain; uncertain activations and one
  uncertain weight injected; flags exact and values exact on every certain word). It doubles the instruction
  count (8 per cycle against 4) but the flag tiles are int8, and the cycle goes from 1.28 to 1.84–2.03 µs. With
  3D logic the kernel is still 15–16× one Verilator thread and about 8× the best eight, and Verilator's number
  is 2-state.
- **A dependent level costs 0.49 µs.** `nki_deep.py` is a synthetic chain (it has a CPU core's depth, not its
  logic): 4, 19 and 137 levels fit `0.10 + 0.487 × levels` µs within 1 %. That is the measured form of the bound
  in the first addendum, a little worse than the 0.3–0.4 µs assumed there. A Hazard3-depth design is 9.4 µs per
  cycle after the depth pass and 67 µs as yosys emits it, against Verilator's 0.25 µs: 37× and 270× behind.
  One NeuronCore wins on width and loses on depth, by about the same factor.

## Reproduce

Everything is in `trainium/`:

| file | role |
| :-- | :-- |
| `sa.sv`, `tb_sa.cpp`, `run_verilator.sh` | the RTL, its Verilator bench and the build+run script (`run_verilator.sh 128 20000 [1\|8]`) |
| `nki_sa.py`, `nki_sa2.py`, `nki_sa3.py` | the kernels; each runs on the CPU simulator and checks itself against the golden model (`python nki_sa3.py 2048`) |
| `nki_sa_hw2.py` | compile-once, device-clock harness (`nki_sa_hw2.py N Mlist repeats --kernels sa,sa2,sa3 --out DIR`; `--local` stops after the compile) |
| `run2_remote.sh` | the exact command sequence that produced the numbers, run on the instance |
| `hw_launch.sh` | launches a trn1.2xlarge from the Neuron DLAMI, runs the harness, fetches the artifacts and terminates (`HW_DRY=1` for a dry run; the user-data `shutdown -h +N` is the cost cap) |
| `logs/` | Verilator results, the hardware run log, NEFFs and compiler metric stores |

Host-side packages that worked together: `nki==0.7.0+37713544100.gfede04bb`,
`neuronx-cc==2.28.7631.0+8f7b0cf5`, `nrtpy==2.35.33+d2725ccb` (from
`pip.repos.neuron.amazonaws.com`); on the DLAMI (SDK 2.33.1, Ubuntu 24.04)
install them into `/opt/aws_neuronx_venv_jax_0_10` and force-reinstall
nrtpy last.

Things that cost time and are easy to avoid:

- The DLAMI venvs do not put `/opt/aws/neuron/lib` on the loader path;
  without `LD_LIBRARY_PATH=/opt/aws/neuron/lib` `nki.runtime` fails with
  `libnrt.so.1: cannot open shared object file` and then claims the
  platform is unsupported. The pinned nrtpy matches the system libnrt
  (2.35.33) exactly.
- The DLAMI's root snapshot needs a volume of at least 100 GB.
- `neuron-profile` is gone in SDK 2.33; use `neuron-explorer capture/view`.
- Time kernels with `CompiledKernel.benchmark`, never with wall clock
  around a numpy call (see Method).
- The whole-stimulus kernel (sa2) overflows SBUF above M ≈ 900
  (`NCC_INLA001`); use sa3.
- The SageMaker image route works but needs the `pkg_resources` shim and a
  handler that starts the benchmark in a thread; the EC2 route is simpler
  once the quota exists.

## Appendix: the sa3 kernel

```python
M, N = 256, 128
CH = 256                            # stream cycles per chunk (even; T % CH == 0)

@nki.jit
def sa3_kernel(skew, wT):
    """skew: [N, T*N] int8, partition c, element t*N + r = a_in of row r at cycle t-c (0 for t < c).
       wT:   [N, N]   int8, partition c, element r = W[r][c].
       returns outT: [N, T] int32, partition c, element s = bottom-row p_out of column c after stream cycle s."""
    n = wT.shape[0]
    t_stream = skew.shape[1] // n
    ch = CH
    nchunk = t_stream // ch
    outT = nl.ndarray((n, t_stream), dtype=nl.int32, buffer=nl.shared_hbm)

    w = nl.ndarray((n, n), dtype=nl.int8, buffer=nl.sbuf)
    nisa.dma_copy(dst=w, src=wT)
    P0 = nl.zeros((n, n), dtype=nl.int32, buffer=nl.sbuf)          # the two banks of p_q
    P1 = nl.zeros((n, n), dtype=nl.int32, buffer=nl.sbuf)
    prod = nl.ndarray((n, n), dtype=nl.int32, buffer=nl.sbuf)
    sk0 = nl.ndarray((n, ch * n), dtype=nl.int8, buffer=nl.sbuf)   # stimulus double buffer
    sk1 = nl.ndarray((n, ch * n), dtype=nl.int8, buffer=nl.sbuf)
    ob0 = nl.ndarray((n, ch), dtype=nl.int32, buffer=nl.sbuf)      # output double buffer
    ob1 = nl.ndarray((n, ch), dtype=nl.int32, buffer=nl.sbuf)

    def cycle(Pcur, Pnxt, sk, ob, j):                                # phase 1: next bank from current bank
        nisa.tensor_tensor(dst=prod, data1=sk[:, j * n:(j + 1) * n], data2=w, op=nl.multiply)        # a_in * w_q
        nisa.tensor_tensor(dst=Pnxt[:, 1:n], data1=Pcur[:, 0:n - 1], data2=prod[:, 1:n], op=nl.add)  # p_q <= p_in + a*w
        nisa.tensor_copy(dst=Pnxt[:, 0:1], src=prod[:, 0:1])                                        # top row: p_in = 0
        nisa.tensor_copy(dst=ob[:, j:j + 1], src=Pnxt[:, n - 1:n])                                   # bottom-row output

    for k in range(nchunk):
        sk = sk0 if k % 2 == 0 else sk1
        ob = ob0 if k % 2 == 0 else ob1
        nisa.dma_copy(dst=sk, src=skew[:, k * ch * n:(k + 1) * ch * n])
        for i in range(ch // 2):                                      # phase 2 (bank switch) = swapped roles
            cycle(P0, P1, sk, ob, 2 * i)
            cycle(P1, P0, sk, ob, 2 * i + 1)
        nisa.dma_copy(dst=outT[:, k * ch:(k + 1) * ch], src=ob)
    return outT
```
