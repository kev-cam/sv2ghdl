# SystemVerilog simulation performance: nvc vs Verilator

Companion to `vhdl_perf.md`, which covers the VHDL cross-simulator comparison
(our-nvc / stock-nvc / ghdl). **This doc covers the SystemVerilog path**, where
the competition is Verilator.

The two engines do genuinely different work, and the comparison is only fair if
that is stated up front:

| | our flow | Verilator |
| :-- | :-- | :-- |
| front end | SV → `sv2vhdl` → VHDL → nvc | SV compiled directly to C++ |
| value model | 4-state + Z + strength (3D-Logic), multi-driver resolution | 2-state (`--x-assign` approximates) |
| scheduling | event-driven, delta cycles | levelised, whole-design eval per cycle |
| accel path | `--accel`: yosys front end → generated C, per-chunk | n/a — always compiled |

Verilator is the speed reference *because* it discards what we keep. The
[north-star](../../..) position is that we beat it on 4-state values, Z,
multi-driver resolution and low-activity event-driven work — not on raw
gate-toggle throughput. Nothing here contradicts that; this doc measures the
axis where we are behind, because that is the axis with a number on it.

## The one fully-valid measured row

**VeeR-EH2, `hello`, 3000 ns window.** Both arms `TEST_PASSED cycles=2519`, so
the workloads are provably identical.

| engine | cycles/s | vs Verilator | wall (best of 3) |
| :-- | --: | --: | --: |
| Verilator | **30,195** | 1.00x | — |
| our-nvc, interpreted | **12.65** | **0.00042x** (2,387x slower) | 199.132 s |
| our-nvc `--accel` | **12.58** | 0.00042x | 200.280 s |

Method: 3 interleaved reps per arm, alternating so neither engine owns the
colder slot; the machine was shared throughout (other agents plus a 98%-CPU
daemon), which is exactly why the arms were interleaved rather than run in
blocks — contention becomes a common-mode error. Runs were **not** pinned; never
whole-process `taskset` an nvc run. Raw walls: interp 202.452 / 200.639 /
199.132, accel 203.681 / 203.278 / 200.280. Arithmetic: 2519/199.132 = 12.650,
2519/200.280 = 12.577, ratio 0.994x.

This reproduces earlier independent measurements (12.53 / 12.32) within noise,
on a different binary and a different day.

## Why `--accel` buys nothing here

**Because the chunks that install are all cold.** The five ACTIVE chunks in
every accel run above are `dbg`, `dma_ctrl`, `exu_div_ctl`, `exu_mul_ctl` and
`pic_ctrl`. None is on the hot path of a `hello` workload — so the accelerated
fraction of the design is nearly all idle, and 0.994x is the expected result
rather than a disappointing one.

The hot blocks — `eh2_dec` and `eh2_ifu` — do not install. As of 2026-07-27 the
reason is understood and it is **not** what it appeared to be:

* They used to decline at synthesis because of a dangling-pointer bug in the
  translator: `vid()` returns one of 8 ROTATING STATIC buffers, and
  `emit_function` borrowed that pointer across a body emission issuing far more
  than 8 `vid()` calls, so the trailing `return` printed whatever identifier
  last landed in the slot. **22 of 22 emitted functions had the wrong return
  target.**
* Fixing that is two lines and it does unblock synthesis: `eh2_dec` (16511 comb
  cells / 346 registers) and `eh2_ifu` (30680 / 1949) then emit, synthesise,
  compile and install.
* **And they are functionally wrong.** `eh2_dec` derails at cycle 77 — 125
  retires under the interpreter against 42 under accel, identical for the first
  16 retires, then `0x2E000000` garbage and a repeating illegal-instruction
  trap. `NVC_ACCEL_VERIFY` (which drives the chunk from the interpreter's own
  inputs, so this is compute, not a boundary race) localises the first
  divergence at `215ns+101 DEC_I0_BR_IMMED_D`. `eh2_ifu` installs and then
  SIGSEGVs on an 8.33 MiB `sm_comb` stack frame against an 8 MiB limit.

So the hot blocks were never *correct but declined* — **they were wrong and
failing safe.**

**UPDATE — the correctness half is fixed and landed (nvc `f251009c0`).** The
wrongness was a second silent mistranslation, independent of the first: Verilog
**self-determined width**. `l3d_bit_read` returns a 1-bit scalar but was emitted
as `((a >> i) & 1'b1)`, which Verilog widths from its *left operand*. The two
Verilog contexts that do not resize their parts — a concatenation element and a
replication operand — were both fed such reads, so `{32 × bit_read(v_w0v,…)}`
with `width(v_w0v)=31` became 992 bits whose low 32 are `0x80000001`: **only bits
0 and 31 of every GPR write survived**, exactly the `accel == interp &
0x80000001` signature. `eh2_dec` now produces **125 RETIRE lines byte-identical
to the interpreter**.

Two blockers remain before a VeeR accel number is publishable: `eh2_ifu` still
SIGSEGVs on its 8.33 MiB `sm_comb` frame, and the 215ns family turned out to be a
*separate* defect — extra-clock-group advance scheduling, not compute, which
collapses `eh2_dec_decode_ctl`'s 62 divergences to 2 under `NVC_ACCEL_CK_LATE=1`.
Two independent defects were being conflated.

## The gap is code shape, not representation — measured

This is the section that answers "why is `--accel` slow", and it overturns the
obvious hypothesis. Our generated C was suspected of being **bit-scalar** where
CXXRTL packs bits. It is not. Measured on the real `eh2_dec_gpr_ctl` netlist
(3017 wires / 1.47 M wire bits / 2122 cells), storage per RTL bit is:

| | ours | CXXRTL |
| :-- | --: | --: |
| 992-bit net | `uint32_t[31]` = 124 B | `value<992>` = 124 B — **identical** |
| 32-bit register | `uint64_t` = 8 B | `wire<32>` = 8 B — **identical** |
| 1-bit port | 8 B | 4 B (ours 2× worse) |
| total persistent | 528 B | 408 B (1.29×) |

The 5.8 M `uint64_t _x = 0;` lines that prompted the hypothesis are **one per
NET**, each holding up to 64 RTL bits — not one per bit. The claimed 75:1
declaration-to-assignment ratio does not reproduce either: ours 1:2.34, CXXRTL
1:2.16.

**What is actually wrong is code shape, and one item is 85% of it.** A 992-bit
result is assigned with `wplace(dst,0,src,992)`, which gcc `-O2` inlines as an
**18-instruction-per-bit read-modify-write loop running 992 times** — 17,856
instructions to move 124 bytes. The `wand()` on the line above does the same 992
bits in **186 instructions**. So our *operators* are already word-parallel; only
the *assignment* is bit-serial, **96× more expensive than it needs to be**. 1,686
such sites execute per eval, giving ~30.1 M of 35.4 M instructions; `perf` agrees
(sm_clock 81.3%, sm_comb 14.5%). **Replacing it with a 31-limb `wcopy` is ~6.7× on
this module from one peephole.** Behind it: 23,613 calls to `worbits_s` — a
while-loop function called to deposit a *single* bit — 9,931 `rep-stos` zeroing
temporaries, and three copies of the comb cone (`sm_comb`/`sm_clock`/
`sm_clock_out`).

**Head to head on one module**, same Verilog, same stimulus, both producing
checksum `0x00000001` so functionally equivalent, and both input-independent to
0.02%:

| engine | insn / cycle | ratio |
| :-- | --: | --: |
| Verilator | 36,731 | 1.0× |
| ours | 35,376,321 | **963×** |
| CXXRTL (same shape, smaller netlist) | — | **2.30× vs ours** |

So we are **~2.3× off CXXRTL, not 2000×** — the earlier framing was wrong.
CXXRTL earns its 2.3× purely because `slice<K>`/`and_uu<992>` are compile-time
template parameters that constant-fold to chunk-parallel code, where ours is a
runtime bit loop. Its storage is not cleverer.

**Verilator is in a different class for an architectural reason**, not a tuning
one: it works from **RTL, not the flattened yosys netlist**. It keeps 32-bit
lanes as `IData` scalars and materialises only **two** `VlWide<31>` objects in the
entire design, with one `VL_ASSIGN_W` in the whole generated file. It never builds
a 992-bit intermediate per cell; our flattened path builds one per cell. On
`.text`: ours 1.66 MB against Verilator's 24,960 B of design code — 66×, and 86×
on the hot path.

Order of attack by measured payoff: (1) the `wplace` peephole, ~6.7× for a
localised change; (2) inline `worbits_s` and kill the single-bit call sites;
(3) stop emitting three copies of the comb cone; (4) the deep one — avoid
materialising full-width intermediates per cell. **Even a perfect (1)–(3) leaves
~144×**, so Verilator parity is not reachable by peepholes on the
flattened-netlist path.

## Where the time goes

`perf record -F 499`, normalised to the sim thread (other tids are LLVM tier-up
workers), on the **dec-installed** configuration:

| | share of sim thread |
| :-- | --: |
| generated accel code (`aj_eh2_dec__e826_*.so`) | 32.4% |
| libnvc.so | 31.8% |
| libc.so.6 | 23.9% |
| JIT'd interpreted design | 5.2% |
| kernel | 3.6% |
| libzstd | 3.0% |

**Read this as structural evidence only, not as a validated measurement.** The
profile comes from the 42-retire trap-loop run — the core is executing, but it
is executing the wrong program. It shows what coverage *would* look like with
the hot blocks installed; it does not show the cost profile of a correct run.

A frequently-quoted "0.09% of sim time" figure for baseline accel coverage
appears in older notes. **It was never actually measured** and should not be
cited as a number; the honest statement is that baseline coverage was small
enough that accel and interp were within noise, which the 0.994x above does
establish.

## Not yet measured

Stated explicitly so absences are not read as zeros:

* **iverilog / vvp** on the same workload — the third engine in the correctness
  differential (`regress/verilator_ref.py`) has no perf row here.
* **VeeR-EH1** — passes under nvc, never timed against Verilator.
* **VeeR-EL2** — blocked in translation, so no row is possible.
* **Any design but VeeR-EH2 `hello`.** One workload is not a benchmark suite. In
  particular a *low-activity* design is where the event-driven model should win,
  and nothing here tests that — so this doc currently measures only our worst
  axis.
* **A correct `--accel` number**, which requires the `eh2_dec` compute
  divergence fixed first.

## How much can low activity actually buy? — an estimate, not a measurement

The event-driven case is that we do work proportional to what *changed*, while
Verilator levelises and evaluates regardless. That is true, and it is worth
knowing how far it goes. Crude model: Verilator costs `c_vl · N` per cycle
(activity-independent), we cost `c_nvc · α · N`. We win when `α < c_vl / c_nvc`.
The measured 2387x gap on VeeR-EH2 fixes that ratio at `α₀ / 2387`, where `α₀`
is VeeR-hello's own activity factor:

| assumed α₀ | crossover α* | = 1 node active in |
| --: | --: | --: |
| 30% | 0.0126% | 7,957 |
| 10% | 0.0042% | 23,870 |
| 2% | 0.0008% | 119,348 |

**So at interpreted speed the low-activity lever cannot close the gap** — it
would need roughly one node in 24,000 toggling per cycle, which is not a regime
real designs under test sit in. The lever that matters is `c_nvc`, the per-unit-
work constant, not `α`. That is exactly what `--accel` attacks, and it moves the
crossover into plausible territory only when it is working:

| if accel is… | effective gap | crossover α* at α₀=10% |
| :-- | --: | --: |
| 5x faster | 477x | 0.021% |
| 20x faster | 119x | 0.084% |
| 50x faster | 48x | 0.209% |

Caveats, because this is arithmetic on one measurement rather than an
experiment: the model assumes our work scales linearly in α and Verilator's not
at all (both are approximations — we carry per-event overhead that does not
scale down, and Verilator is not perfectly activity-blind once caches and
branches are involved); and **α₀ for VeeR hello has not been measured**, so the
table is parameterised rather than solved.

**The honest reading of our existing "we win" evidence.** The demand-driven
prototype's 8-169x and the compiled-pull-cone 26.46x are measured against *our
own forward-push baseline*, not against Verilator. They demonstrate that pull
beats push **within our engine**; they do not establish a win over Verilator,
and this doc should not be read as if they did. Combining them with the constant
above is the open question, and it needs the experiment in "Not yet measured",
not more arithmetic.

None of this touches the capability argument, which is separate and not a speed
claim at all: 4-state values, Z, strength and multi-driver resolution are things
Verilator does not do, at any activity factor.

## Correctness parity is tracked elsewhere

This doc is performance only. For how the SV path compares to Verilator on
*results* rather than speed:

* `regress/verilator_parity_roadmap.md` — the 1112 translation gaps, diagnosed
  and ranked.
* `regress/verilator_ref_findings.md` — the 3-engine differential
  (iverilog / shim / Verilator) findings.

_Numbers dated 2026-07-27. Re-measure on a quiet machine before quoting: every
timing above was taken on a contended box, and while interleaving makes that a
common-mode error for the interp-vs-accel ratio, it does not make the absolute
cycles/s figures precise._
