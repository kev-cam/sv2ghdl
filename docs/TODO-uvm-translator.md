# TODO: UVM(-AMS) → Python/cocotb translator for NVC

Status: scoping. Not started.

## Goal

Translate SystemVerilog UVM testbenches into Python running against NVC via the
cocotb bridge in `nvc/contrib/cocotb`, so that existing SV-UVM verification IP —
including UVM-AMS bridge/core style analog UVCs — can drive VHDL/VHDL-AMS DUTs
without a SystemVerilog simulator in the loop.

Non-goal: running UVM. We are not porting `uvm_pkg`. See "Runtime split" below.

## Runtime split (decide this first, everything else depends on it)

`uvm_pkg` is ~50k lines of SV OOP. Translating it is the wrong move: the output
would be unreadable, unmaintainable, and slow. Instead:

- **Hand-write a Python runtime** implementing the UVM subset that testbenches
  actually touch. Call it the VVM layer. Estimated a few thousand lines.
- **Translate only user code** — the agents, drivers, monitors, sequences,
  transactions, env, and test. These are the parts that differ per project.

### VVM runtime scope — in

- `uvm_object` / `uvm_component`, the component tree, `get_full_name()`
- Phasing: `build`, `connect`, `end_of_elaboration`, `run`; objections
- `uvm_config_db#(T)::set/get` — keyed on `(context, inst_name, field, type)`
- Factory: `type_id::create()`, type and instance overrides
- Sequencer / sequence / driver handshake: `start_item`, `finish_item`,
  `get_next_item`, `item_done`, `execute_item`
- TLM: analysis ports, `uvm_analysis_imp`, blocking put/get
- Reporting: `uvm_info/warning/error/fatal`, verbosity, the report server,
  and a table printer that formats reals as `%g` (see fixes note below)

### VVM runtime scope — out (for now)

RAL / registers, functional coverage, callbacks, comparer, packer, heartbeat,
`uvm_event_pool`, the sequence library/arbitration beyond FIFO.

## Dependencies on `nvc/contrib/cocotb` (blockers)

These are bridge-side work items, not translator work items, but the translator
cannot be validated without them.

1. **ReadWrite phase / delta-cycle semantics.** Currently the bridge forces
   `COCOTB_TRUST_INERTIAL_WRITES=1` and has known failures where a write does
   not propagate before a read ("needs more delta cycles between write and
   read"). Every UVM driver handshake is write-then-read-back. This must be
   correct before any translated driver can be trusted. Tracked as Next Steps
   item 1 in the cocotb README.

2. **Call-into-model primitive.** The bridge API is signal-oriented
   (`nvcb_get_signal_val_real`, `nvcb_set_signal_val_real`, handle navigation,
   callbacks). There is no way to *call a subprogram* in the model. The UVM-AMS
   proxy pattern depends on exactly that (`i_core.setRout(r, tr, tf)`).
   Two options — pick one:
   - (a) Add `nvcb_call_subprogram(hdl, args...)` to the bridge.
   - (b) Re-express the instrument API as signal deposits: the core exposes
     `rout`, `rout_tr`, `rout_tf` as signals and reacts to deposits on them.
   (b) is less faithful to the SV original but avoids bridge surgery and sits
   better with arena resolution. Leaning (b); revisit if a customer TB needs (a).

3. **Analog quantity access and threshold callbacks.** Confirm whether
   `nvcb_get_signal_val_real` reaches VHDL-AMS quantities or only signals. We
   need the equivalent of Verilog-AMS `above()` / `absdelta()`: register a
   threshold/break callback so Python can block until an analog crossing occurs.
   This is what replaces the polled `*_eot` handshake — see AMS section.

4. **Nuitka pipeline live.** `build_nuitka.sh` and `postprocess_c.py` exist but
   are not wired up; the interpreted path goes through ctypes at roughly
   microseconds per call. Do not benchmark anything until post-processing
   replaces ctypes with direct C calls, or we will get a misleadingly bad number
   and talk ourselves into a C++ backend for the wrong reason.

5. **Nice-to-have:** Next Steps item 3 (cocotb tasks become NVC processes,
   triggers become NVC `wait` statements). If that lands, Python stops being a
   runtime and becomes pure codegen, and the GIL leaves the hot path. Design the
   translator output so it survives that change — emit blocking calls, never
   touch the cocotb scheduler directly.

## Translator work items

### T1. SV class front-end

Check what `pp` / sv2ghdl currently does with class bodies; likely needs
extending. Required coverage:

- `class` / `extends` / `virtual` methods / `super.`
- Parameterised classes (`uvm_sequence #(txn)`) — specialise at translation time
- `static` members and methods
- `typedef`, `enum`, `struct`
- `constraint` blocks (see T4)
- Deliberately unsupported, error cleanly: `covergroup`, `bind`, `program`

### T2. Macro handling

`uvm_*_utils`, `uvm_info/warning/error/fatal`, `uvm_do*`, `uvm_field_*`.
These are preprocessor macros, so they are ours to define. Map them to VVM
runtime calls rather than expanding the UVM definitions:

```
`uvm_object_utils(vdriver_txn)    ->  @vvm_object                (decorator)
`uvm_component_utils(foo)         ->  @vvm_component
`uvm_info("TAG", msg, UVM_MEDIUM) ->  self.info("TAG", msg, MEDIUM)
```

`uvm_field_*` automation is the awkward one — it drives print/copy/compare.
Simplest route: emit an explicit field list on the class and have the runtime
introspect it.

### T3. Type mapping

| SV | Python |
|---|---|
| `real` | `float` |
| `int`, `int unsigned`, `longint` | `int` |
| `string` | `str` |
| `enum` | `IntEnum` |
| `bit`/`logic` vectors | decide: `int` + width, or a 4-state class |
| associative array | `dict` |
| queue, dynamic array | `list` |
| class handle | reference |
| `time`, `$realtime` | bridge sim-time call |

4-state is the open one. The TB layer here is mostly `real`/`int`/`string`, and
the 4-state traffic lives at the DUT boundary which stays in VHDL — so a plain
`int` plus width is probably enough for v1. Revisit if a digital UVC needs X.

### T4. Concurrency and randomisation

- Blocking tasks → blocking bridge calls. Reuse the `translate_cocotb.py` AST
  transform approach; it already does `await Timer(...)` →
  `_nvcb_wait_time(...)`, `await RisingEdge(s)` → `_nvcb_wait_edge(s, RISING)`.
  Our equivalents: `#delay`, `@(posedge clk)`, `wait(expr)`.
- `fork`/`join`, `join_none`, `join_any` — no decision yet. Options: map to NVC
  processes (best, depends on bridge item 5), or Python threads (GIL, ugly), or
  restrict v1 to the sequential subset and error on `fork`. **Start by erroring
  on `fork`** and see how far real testbenches get; the Dialog example does not
  use it in the UVC layer.
- `rand` / `constraint` / `randomize() with {...}` → PyVSC. It implements
  SV-style constraint semantics over Z3 and is a reasonably direct mapping.
  Defer until a TB needs it; emit a clear error meanwhile.

### T5. UVM-AMS specifics

The reference material is the Dialog Semiconductor UVM-(A)MS example (Grove /
Holloway, 2021) — see `doc/refs/uvm_ms/`. Its structure is the thing to
preserve:

- **UVC layer** (pure UVM, no analog) → translates to Python.
- **MS bridge** (SV wrapper module holding the proxy, instantiating the core)
  → becomes VHDL, or disappears entirely if we go with bridge option (b).
- **Analog resource core** (`.vams` / `.snps.sv` / `.snps.va` variants, selected
  by filelist) → **do not translate.** Hand-write once in VHDL-AMS per net
  abstraction. `transition()` → slew via quantity/break; `above()` → `'above`;
  `absdelta()` → threshold break. This is the part NVC already does natively.

Two things from the reference NOT to carry over:

- **The `*_eot` polling handshake.** The original mirrors `i_core.vdc_eot`
  into the proxy with `always begin ... @(...) end` and has the driver
  `wait()` on it, because there is no way to express "block until the analog
  solver has converged past this point" across the domain boundary. We have
  bridge item 3 for that — block on a threshold callback directly.
- **The logic strength → resistance path** (`logic_decode`, the VPI
  `vpiStrengthVal` extraction, the 8-entry strength/resistance table, the
  X-region debounce). All of it is scar tissue from SV having no discipline
  resolution and connect modules being Verilog-AMS-only. Under arena
  resolution the strength table *is* the resolution function, at the receiver.
  Do not port it.

### T6. Report-server fidelity

The reference carries a `uvm_ams_fixes_pkg` working around two UVM bugs:
report-server timestamps at the wrong precision (MANTIS 5807) and the table
printer mangling reals. Since we are writing the runtime, just get both right
first time — `%g` for reals, full-precision sim time from the bridge.

## Validation

Acceptance target: the Dialog example, end to end.

1. Rebuild `dut.vams` + `test_env` in VHDL-AMS. Note the original DUT is three
   1e12-ohm GMIN stubs — it exists only to force the nodes electrical. The real
   test is the UVCs exercising each other, so this is cheap.
2. Hand-write the `vdriver`, `cap` and `logic` analog cores in VHDL-AMS.
3. Translate the UVC layer (`vdriver_agent`, `cap_agent`, `ldriver_agent`,
   `uvm_ams_env`, the test) with the tool.
4. Run: Vdd=3V, 100 random logic toggles, measure cap, set rseries=1k, measure
   again, sawtooth. Compare the message stream and the measured values against
   the original where we can get a reference run.

Secondary target: a purely digital UVM TB with a sequencer/driver/monitor and
a scoreboard, to shake out T1–T4 without analog in the way.

## Open questions

- **"VVM" naming/shape.** Does the Python layer present a UVM-shaped API
  (easiest translation target, familiar to the SV people we are trying to
  attract), or map onto UVVM/OSVVM idioms (friendlier to the existing VHDL
  verification community)? These pull in opposite directions and the answer
  affects T2 heavily. Decide before writing the runtime.
- Do we translate SV classes *at all*, or hand-port the UVC layer once and only
  build the translator when a customer arrives with their own VIP? The
  customisation-services model argues for hand-porting first and generalising
  from real engagements.
- Licensing: the reference example is Dialog's. Check terms before anything
  derived from it ships in a repo.
