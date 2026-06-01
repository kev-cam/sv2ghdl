# BUG: --std=2040 rejects Verilog-instantiates-VHDL (cross-instantiation)

**Reported:** 2026-05-28 (Claude working on ldx RTL-sim / Yuri FIFO baseline)
**Owner:** whoever is on the iverilog/nvc/sv2ghdl toolchain.

## Summary

`--std=2040` lax mode is supposed to permit a Verilog module to instantiate a
VHDL entity (and vice-versa). Today it does not: elaboration fails with

```
** Error: (init): unit WORK.LEAF is not a Verilog module
```

when a native-Verilog module instantiates a unit that landed in the work
library as a VHDL entity.

## Minimal repro (no sv2ghdl/iverilog quirks needed)

`leaf.vhd`:
```vhdl
library ieee; use ieee.std_logic_1164.all;
entity leaf is
  port (d : in std_logic; q : out std_logic);
end entity;
architecture rtl of leaf is begin q <= d; end architecture;
```

`top.v`:
```verilog
module top;
  reg d = 1'b0; wire q;
  leaf u (.d(d), .q(q));        // Verilog instantiating a VHDL entity
  initial begin #1 d = 1'b1; #1 $display("q=%b", q); $finish; end
endmodule
```

```sh
nvc --std=2040 --work=work -a leaf.vhd     # ok
nvc --std=2040 --work=work -a top.v        # ok (falls back to native Verilog parser)
nvc --std=2040 --work=work -e top          # FAILS: "unit WORK.LEAF is not a Verilog module"
```

Expected (2040 lax): elaboration binds the Verilog instance `u` to the VHDL
entity `LEAF` and runs, printing `q=1`.

## How it bites the real flow (the motivating case)

Baselining the Yuri wrapped-FIFO benchmark
(`ldx/examples/rtl-sim/yuri_challenge/{flip_flop_fifo,ff_fifo_wrapped_in_valid_ready,a_plus_b_using_wrapped_fifos}.sv`)
through `nvc --std=2040 -a *.sv tb`:

- nvc invokes `iverilog-sv2ghdl` **per file**.
- `flip_flop_fifo` translates cleanly → lands as a **VHDL entity**.
- `ff_fifo_wrapped_in_valid_ready` and `a_plus_b_using_wrapped_fifos` get
  "no modules translated" (per-file: their submodules aren't present in that
  invocation) → fall back to **native Verilog**.
- Result: a Verilog module (`ff_fifo...`) instantiates a VHDL entity
  (`flip_flop_fifo`) → the cross-instantiation rejection above. Blocks the
  nvc and nvc --accel baseline numbers.

Two independent things to consider:
1. **The 2040 lax-mode cross-instantiation** (this bug) — the clean fix.
2. The **per-file translation** in nvc's sv2ghdl hook: translating each file in
   isolation guarantees parents fail (submodules absent), so the success/failure
   split that creates the mixed VHDL/Verilog work lib is itself fragile.
   Translating the file set together (as `iverilog -tvhdl` on all three at once
   does succeed) would keep the lib single-language.

## Not touched

Left the toolchain unmodified pending coordination. No edits under
`/usr/local/src/{nvc,iverilog,sv2ghdl}` except this note.
