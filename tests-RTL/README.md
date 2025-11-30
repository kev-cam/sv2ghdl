# RTL to Gate-Level Synthesis Tests

This directory contains simple RTL test cases for synthesis to gate-level netlists.

## Test Cases

- **counter4.v** - 4-bit counter with enable and reset
- **adder4.v** - 4-bit adder with carry in/out
- **mux2to1.v** - 2:1 multiplexer with 4-bit data
- **shifter4.v** - 4-bit shift register with parallel load
- **alu4.v** - Simple 4-bit ALU (ADD, SUB, AND, OR)

## Workflow

The intended workflow is:

1. **RTL** (*.v) → **Yosys synthesis** → **Gate-level netlist** (*_syn.v)
2. **Gate-level netlist** → **ATPG (Atalanta)** → **Test vectors** (*.test)
3. **Test vectors** → **Testbench** → **Verify RTL**

### Synthesis

Run `make all` to synthesize all RTL files to gate-level netlists:

```bash
make all
```

This will create `work/*_syn.v` files containing gate-level implementations.

### ATPG Flow

After synthesis, the gate-level netlists can be used with the ATPG workflow:

```bash
# Convert gate netlist to BENCH format (if verilog2bench.pl available)
verilog2bench.pl work/counter4_syn.v work/counter4.bench

# Run ATPG to generate test vectors
cd work
atalanta counter4.bench

# Create VHDL testbench from test vectors
test2vhdl.pl counter4.test

# Simulate original RTL with generated testbench
ghdl -a counter4.vhd counter4_tb.vhd
ghdl -r counter4_tb
```

## Requirements

- **yosys** - For RTL to gate-level synthesis
- **atalanta** - For ATPG test generation (optional)
- **ghdl** - For VHDL simulation (optional)

## Yosys Synthesis Options

The Makefile uses these Yosys commands:

- `proc` - Process RTL constructs (always blocks)
- `fsm` - Extract and optimize finite state machines
- `memory` - Extract and optimize memory structures
- `techmap` - Technology mapping to basic gates
- `opt` - Optimization passes
- `clean` - Remove unused signals/wires
