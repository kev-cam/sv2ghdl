# Debugging a compiled-model miscompile with federated simulation

*How `NVC_ACCEL_VERIFY` — a passive-companion differential built on nvc's
resolution/federation scheme — pinpointed and fixed a VeeR-EH1 writeback
miscompile in the `--accel` codegen.*

nvc commit `6253f9952` (the verify mode) · sv2ghdl commit `ed4acd7` (the fix)

---

## 1. The idea: federated simulation as a differential oracle

nvc's resolution scheme exists to let independent simulations meet at a net: a
resolution function sees every driver of a signal and reduces them to one value.
That is normally used to *federate* — to bridge a VHDL core to Xyce, or to splice
a back-annotated block into an RTL sim. But the same machinery is a debugging
instrument: put **two versions of the same design** on the two sides of a net and
let the resolver *compare* instead of merge. Any driver that disagrees with its
companion is a discrepancy, reported with the net name and the exact simulation
time.

We apply that to the `--accel` path. `--accel` replaces a subtree's interpreted
processes with a compiled cycle-C model (`gen_statemachine` → `.so`). When the
compiled model miscomputes, the failure surfaces thousands of cycles later as a
wrong architectural result — useless for root-causing. Federated simulation
collapses that distance: run the interpreter and the compiled model **side by
side on the same stimulus** and report the *first net that disagrees*.

## 2. `NVC_ACCEL_VERIFY` — the mechanism

`NVC_ACCEL_VERIFY=1` turns `--accel` into a passive oracle instead of an
accelerator:

- The accel subtree is **not** rerouted. The interpreter keeps driving the real
  simulation, so the reference stays golden.
- Each compiled `.so` runs as a **passive companion**: advanced once per delta at
  `END_OF_PROCESSES` (state only, so its multi-clock register ordering tracks the
  interpreter delta-for-delta), then at `END_TIME_STEP` its combinational outputs
  are compared, per net, against the interpreter's settled values.
- The **first** divergence per output is reported as
  `accel-verify: <time>+<delta>  <NET>  interp=0x..  accel=0x..`.
- The comparison is **metavalue-aware** (interpreter `U`/`X`/`Z` bits are
  don't-care — the `.so` is 2-state), and in verify mode the main clock is
  detected by value (`clk && !clk_last`) rather than the rerouted path's
  time-edge, because the harness runs the model on every delta.

Run it with the normal accel environment plus `NVC_ACCEL_VERIFY=1`, and scope it
to one subtree with `NVC_ACCEL_ONLY=<module>`:

```
NVC_ACCEL=1 NVC_ACCEL_JIT=1 NVC_ACCEL_FROM_VHDL=1 NVC_ACCEL_CC=cc \
NVC_ACCEL_VERIFY=1 NVC_ACCEL_ONLY=dec_decode_ctl \
  nvc -M2g -H1g --std=2040 --work=$WORK -r veer_eh1_tb --stop-time=400ns
```

> **Gotcha.** The bridge `.so` cache key is the subtree hash, not the nvc/codegen
> version. After rebuilding nvc *or* `gen_statemachine`, clear `~/.cache/nvc/accel`
> or the stale `.so` is reused.

## 3. The debugging session

### 3.1 Localize (federated verify)

A full-subtree verify run on VeeR-EH1's `hello` reported the first divergence at
**275 ns** on `dec_decode_ctl`'s writeback outputs. Scoping to that leaf with
`NVC_ACCEL_ONLY=dec_decode_ctl` gave a clean, ordered list:

```
275ns  DEC_TLU_PACKET_E4    interp=0x2000600 accel=0x80018
285ns  DEC_CSR_WEN_WB       interp=0x1       accel=0x0
285ns  DEC_CSR_WRADDR_WB    interp=0xb02     accel=0xd81
295ns  DEC_I1_WADDR_WB      interp=0x1       accel=0x0
```

One command reproduced — natively, on real testbench stimulus — an analysis that
had previously required hand-built VCD differencing.

### 3.2 Reproduce offline (xcheck)

`xcheck.py` drives the `.so` and an independent `iverilog` sim of the same
flattened netlist with identical random vectors and compares outputs. It made the
bug **deterministic and stimulus-general**:

```
cyc=46  dec_i1_waddr_wb    iverilog=07  cdriver=00
cyc=46  dec_csr_wraddr_wb  iverilog=e4f cdriver=727
```

### 3.3 Decode the signature

Decoding the divergences bit-by-bit revealed **field-placement offsets**, not
random corruption:

| signal | reference | accel | relation |
|---|---|---|---|
| `dec_csr_wraddr_wb` | `0xe4f` | `0x727` | `accel = ref >> 1` |
| `dec_tlu_packet_e4` | bits {9,10,25} | bits {3,4,19} | every bit −6 |
| `dec_i1_waddr_wb` | `0x7` | `0x0` | dropped |

A field emitted too narrow shifts everything above it down — the fingerprint of a
**concatenation / bit-placement** bug.

### 3.4 Trace to the register D-assembly

The output slices were **correct**:

```c
o->_dec_csr_wraddr_wb = wslice64(_wbff_dff_dffs_dout_reg, 19, 12, 3);  // wbd[30:19]
o->_dec_i1_waddr_wb   = wslice64(_wbff_dff_dffs_dout_reg, 38,  5, 3);  // wbd[42:38]
```

So the **register itself** (`_wbff_dff_dffs_dout_reg`, the 67-bit `wbff` flop)
held a wrong value. Its D net `_wbff_dff_din` was assembled by a wide `$mux`, and
the generated C placed the 54-bit result **contiguously**:

```c
wplace(_wbff_dff_din, 0, _wy, 54);   // <-- packs into din[0:53]
```

### 3.5 Source of truth

The flattened netlist showed the intended assignment is a **non-contiguous
concatenation LHS**:

```verilog
// clean.v
assign { din[61], din[58], din[54:35], din[32:19], din[17:0] }
     = exu_div_finish ? 54'h0
     : { e4ff[61], e4ff[58], e4ff[54:35], e4ff[32:19], e4ff[17:0] };
```

The target slices have **gaps** (bits 18, 33-34, 55-57, 59-60 are driven
elsewhere). Packing the 54-bit result into `din[0:53]` shifts every slice past the
first gap: `e4ff[19:32]` landed at `din[18:31]` instead of `din[19:32]` (→
`dec_csr_wraddr_wb >> 1`), `e4ff[35:54]` at `din[32:51]` instead of `din[35:54]`
(→ `dec_i1_waddr_wb` reads the wrong bits → 0).

### 3.6 Pinpoint the codegen

`gen_statemachine.cpp::emit_wide_cell` stored results with a single contiguous
`wplace(y, yoff, _wy, yw)` using only the **first** Y chunk's offset. The
scalar/narrow path already scattered multi-chunk outputs correctly (the `y_multi`
/ `_yspl` block, mirrored earlier for `dec_tlu_packet_e4` per-slice writes in
commit `e4a39a4`) — but that scatter was **never mirrored into the wide path**.
Any wide cell whose `Y` is a discontiguous concat was therefore miscompiled.

### 3.7 Cross-validate (multi-agent workflow)

Three independent paths were run in parallel and converged on the same cell:

1. **Internal-net differential** — a corrected `net_diff` (fixed VCD↔C name
   mapping and sample phase) exposed the `_wbff_dff_din` cone.
2. **Static cone trace** — walked the C from the wb outputs back to the `$mux`.
3. **In-latch probe** — an `fprintf` inserted directly inside `sm_clock` at the
   flop-latch point read the *true* `_wbff_dff_din` and `e4ff` at the failing
   posedge, and manually packing the same `e4ff` slices contiguously reproduced
   the buggy value **exactly** — proving the mis-placement.

## 4. The fix (`ed4acd7`)

`put_val` now collects the `Y` chunks (LSB-first) and, when there is more than
one, scatters `_wy` across them — each slice RMW-placed at its own wire+offset
(`wplaceb` loop for limb-array wires, masked RMW for scalar wires) with a running
source position. Single-chunk cells are byte-identical to before.

```c
for(int _sb=0;_sb<18;_sb++) wplaceb(_wbff_dff_din, 0+_sb, ...);   // din[0:17]
for(int _sb=0;_sb<14;_sb++) wplaceb(_wbff_dff_din,19+_sb, ...);   // din[19:32]  (was 18)
for(int _sb=0;_sb<20;_sb++) wplaceb(_wbff_dff_din,35+_sb, ...);   // din[35:54]  (was 32)
for(int _sb=0;_sb< 1;_sb++) wplaceb(_wbff_dff_din,58+_sb, ...);   // din[58]
for(int _sb=0;_sb< 1;_sb++) wplaceb(_wbff_dff_din,61+_sb, ...);   // din[61]
```

## 5. Validation

| check | before | after |
|---|---|---|
| `xcheck` on `dec_decode_ctl` (seeds 1/2/3/7, ≤2000 cyc) | 2 mismatches | **NO MISMATCH** |
| verify `dec_csr_wen_wb` / `csr_wraddr_wb` / `i1_waddr_wb` | diverge @285-295ns | **gone** |
| toys `test/accel/run.sh` (rerouted, bit-identical) | 0 fail | **0 fail** |
| `nvc/regr-accel` (fresh cache, fixed codegen) | 1137P/0F | **1137P/0F** |

## 6. Residual — the boot loop is not yet closed

Fixing the `wbff` writeback did **not** end VeeR's fetch boot-loop (0,8,16,24→0).
Full-subtree verify with the fix shows a *distinct* first divergence in the E4
trap path:

```
275ns  DEC_TLU_FLUSH_LOWER_WB  interp=0x0 accel=0x1   (spurious flush)
275ns  DEC_TLU_I0_VALID_E4     interp=0x1 accel=0x0
       DEC_TLU_PACKET_E4       interp=0x2000600 accel=0x80018  (still >>6)
```

Crucially, `xcheck` is **clean** for `dec_decode_ctl` up to 2000 random cycles —
so this is *not* a static codegen bug reachable by random stimulus. It needs a
state the real program reaches (a trap/flush condition), so the next step is to
**capture the real dec input vectors** at ~275 ns and replay them through the
net-level differential. A spurious `dec_tlu_flush_lower_wb` fully explains the
symptom (bad flush → fetch redirect to 0).

## 7. Reproduction

```bash
# artifacts (regenerate after clearing the accel cache so the fixed codegen runs)
SUB=~/.cache/nvc/accel/aj_dec_decode_ctl__06e9_subtree.v
TOP=dec_decode_ctl__06e9
export PATH=/usr/local/src/iverilog/_install/bin:$PATH        # real build-area iverilog
/usr/local/src/yosys-build/yosys -q -p \
  "read_verilog -sv $SUB; hierarchy -top $TOP; proc; flatten; opt; dffunmap; opt_clean; \
   write_verilog -noattr clean.v"
/usr/local/src/sv2ghdl/yosys/gen_statemachine clean.v $TOP synth.c
python3 /usr/local/src/sv2ghdl/yosys/xcheck.py clean.v synth.c $TOP 200 1   # -> NO MISMATCH

# federated verify on the live design (clear the .so cache first!)
rm -rf ~/.cache/nvc/accel
NVC_ACCEL=1 NVC_ACCEL_JIT=1 NVC_ACCEL_FROM_VHDL=1 NVC_ACCEL_CC=cc \
NVC_ACCEL_VERIFY=1 NVC_ACCEL_ONLY=dec_decode_ctl \
  nvc -M2g -H1g --std=2040 --work=$WORK -r veer_eh1_tb --stop-time=400ns
```

## 8. Takeaways

- **Federation is a debugging primitive, not just an integration one.** A
  resolver that compares instead of merges turns "the compiled model is wrong
  somewhere" into "net `X` disagrees at time `T`."
- **A passive companion beats a rerouted one for diagnosis** — the reference stays
  golden, so every divergence is measured against truth, not against another
  possibly-wrong value.
- **Decode the divergence before chasing code.** `accel = ref >> N` said
  "bit-placement," which pointed straight at a concatenation, not at logic.
- **Two oracles are better than one.** The live federated verify (real stimulus,
  finds the *symptom*) and the offline `xcheck`/net-differential (random,
  deterministic, isolates the *cell*) are complementary; a bug that reproduces in
  one but not the other is itself a signal (§6).
