# sv2ghdl

**SystemVerilog to VHDL Translator for Federated Simulation**

sv2ghdl translates SystemVerilog RTL into VHDL for use with GHDL and NVC simulators. The generated VHDL leverages simulator extensions that go beyond what SystemVerilog offers, including multi-UDN wires, bidirectional components, and improved unknown-state handling.

## Vision: HDLs for the AI Era

IEEE standards are a **portable floor, not a ceiling**. They ensure baseline code works everywhere, but there's no reason you can't build on top. Commercial tools have always added proprietary extensions; open-source tools like GHDL and NVC can do the same - transparently.

We aim to provide HDL capabilities suited to modern design challenges:

- **Dynamically reconfigurable** - runtime adaptation, not just static netlists
- **Parallel processing** - native support for massively parallel architectures
- **Mixed-abstraction** - RTL, behavioral, analog, and system-level in one simulation
- **Proper C++ interaction** - OO interfaces in VHDL/SV that map directly to C++ classes

The translated VHDL core remains portable (standard IEEE), while extensions enable capabilities the standards committees couldn't agree on or didn't anticipate.

## Why Translate SV to VHDL?

SystemVerilog is widely used for RTL design, but its simulation semantics have limitations:

- **X-propagation problems**: SV's `logic` type handles unknown states poorly, leading to optimistic simulation that masks real hardware bugs
- **No multi-UDN wires**: SV lacks support for user-defined net types with multiple resolution functions
- **Limited bidirectional modeling**: Bidirectional components (analog switches, transmission gates) are awkward in SV

By translating to VHDL and using extended simulators (GHDL, NVC), we gain:

- **Better UDN types**: Replace SV `logic` with resolution functions that properly propagate unknowns
- **Multi-UDN wire support**: Model complex interconnect with multiple resolution semantics
- **True bidirectional components**: Native support in VHDL's signal resolution model
- **Federated simulation**: Mix VHDL with other simulation engines seamlessly

## Approach

### Target Scope

The initial focus is **RTL-style SystemVerilog** - the synthesizable subset used for actual hardware design. This covers:

- Module declarations with ports
- Always blocks (combinational and sequential)
- Continuous assignments
- Basic operators and expressions
- Module instantiation

The scope will expand to **post-synthesis gate-level netlists**, enabling translation of synthesis output for gate-level simulation with improved X-handling.

### Federated Simulation Strategy

Rather than attempting to translate the full horror of SystemVerilog (classes, constraints, coverage, UVM, etc.), we use **federated simulation**:

```
┌─────────────────────┐     ┌─────────────────────┐
│   SV Testbench      │     │     VHDL DUT        │
│   (Verilator/etc)   │◄───►│   (GHDL/NVC)        │
│                     │     │                     │
│ - UVM/classes       │     │ - Better X-prop     │
│ - Constraints       │     │ - Multi-UDN wires   │
│ - Coverage          │     │ - Bidirectional     │
└─────────────────────┘     └─────────────────────┘
```

This lets existing SV testbenches drive the translated VHDL design-under-test (DUT), gaining VHDL's simulation advantages without reimplementing SV's verification features. The full complexity of SV verification constructs doesn't need to be addressed - possibly ever.

### Discipline and Nature Model for Wires

VHDL lacks a true "wire" concept. Its `signal` is a value-over-time abstraction with resolution bolted onto the type system. Verilog has `wire` but SystemVerilog confuses it as a type rather than connectivity. VHDL-AMS's `terminal` doesn't quite fit either.

We adopt **Verilog-AMS's discipline/nature terminology** for GHDL/NVC extensions:

```
           discipline electrical
wire: ─────────┬──────────────────
               │
    ┌──────────┴──────────┐
    │   driver            │
    │   internal: logic   │  ← type (0,1,X,Z representation)
    │   kind: potential   │  ← drives voltage ('1'→Vdd, '0'→Gnd)
    └─────────────────────┘
```

**Key distinctions:**

| Concept | What it is | Examples |
|---------|-----------|----------|
| **Discipline** | Physical nature of a wire | `electrical`, `thermal`, `mechanical` |
| **Nature** | Quantities in a discipline | voltage/current, temperature/heat flow |
| **Driver kind** | Potential (across) or flow (through) | voltage source vs current source |
| **Internal type** | Model's representation | `logic`, `real`, `std_logic` |

Digital logic drivers are **potential drivers** on an **electrical discipline**. They present voltage to the wire; current is consequential. The internal `logic` type (0,1,X,Z) maps to voltage levels. This is true even for "digital" simulation - the wire is always physical.

**Two-phase elaboration:**

1. **Connectivity phase** - Ports connect if disciplines match. Build the wire graph. Internal types, driver kinds don't matter yet.

2. **Resolution analysis** - After connectivity is complete, examine each wire:
   - Gather all connected drivers/receivers
   - Determine resolution strategy based on participants:
     - All potential drivers → voltage arbitration (std_logic-like)
     - All flow drivers → current summation (KCL)
     - Mixed → full electrical solve or defined priority

This separates the **connectivity problem** (discipline matching) from the **resolution problem** (worked out post-elaboration based on actual participants). PWL (piecewise-linear) signals are discrete representations - just `(time, value)` pairs - that can be native VHDL types. The continuous DAE relationships live inside component models, not in interconnect semantics.

### PWL Signals and the Federation Library

PWL (piecewise-linear) waveforms are fundamental to analog/mixed-signal simulation. Unlike VHDL-AMS which lacks clean PWL syntax, we define PWL tables as native HDL data:

**VHDL:**
```vhdl
type pwl_point is record
  t : real;
  v : real;
end record;
type pwl_table is array (natural range <>) of pwl_point;

constant vdd_ramp : pwl_table := (
  (0.0,    0.0),
  (1.0e-9, 3.3),
  (5.0e-6, 3.3),
  (5.1e-6, 0.0)
);
```

**SystemVerilog (testbench side):**
```systemverilog
real vdd_ramp[][] = '{
  '{0.0,    0.0},
  '{1.0e-9, 3.3},
  '{5.0e-6, 3.3},
  '{5.1e-6, 0.0}
};
```

The **C++ federation library** provides runtime support via VHPI:

```vhdl
-- Foreign function declaration
function pwl_value(source_id : integer) return real;
attribute foreign of pwl_value : function is "VHPI libfederation.so pwl_value";
```

The library handles:
- **Interpolation** - compute value at current time
- **Breakpoint scheduling** - notify simulator of next transition
- **SPICE-like sources** - PWL, SIN, PULSE, EXP, etc.
- **Solver integration** - interface with Xyce or other analog engines

This gives clean separation:
- **Data in HDL** - portable, readable, version-controlled with the design
- **Runtime in C++** - interpolation, scheduling, solver interface

VHDL has limited OO support (protected types provide encapsulation but no inheritance), so complex runtime behavior belongs in the federation library rather than fighting VHDL's limitations.

### ATPG-Driven Test Generation

The project leverages **Automatic Test Pattern Generation (ATPG)** to create testbenches for translated logic:

- Generate test vectors that exercise the design
- Validate functional equivalence between SV and VHDL
- Target stuck-at and transition fault models
- Provide regression coverage as translation improves

See `tests-atpg/` for ATPG-based test infrastructure.

## Installation

```bash
# Clone the repository
git clone <repo-url> sv2ghdl
cd sv2ghdl

# Make executable (Perl - no compilation needed)
chmod +x sv2ghdl.pl

# Optional: add to PATH
sudo cp sv2ghdl.pl /usr/local/bin/
```

**Requirements:** Perl 5.10+ (standard on Linux/macOS)

## Usage

```bash
# Basic translation
./sv2ghdl.pl design.v

# Specify output file
./sv2ghdl.pl design.v -o design.vhd

# Use enhanced types (cameron_eda library)
./sv2ghdl.pl --mode=enhanced design.v

# Batch translate all .v files in a directory
./sv2ghdl.pl -find ./rtl -d vhdl_output

# Verbose output
./sv2ghdl.pl -v design.v
```

### Options

| Option | Description |
|--------|-------------|
| `--mode=MODE` | Translation mode: `standard` (IEEE) or `enhanced` (cameron_eda) |
| `-o, --output=FILE` | Output file name |
| `-d, --outdir=DIR` | Output directory for batch translation |
| `-find[=PATH]` | Find and translate all Verilog files |
| `--name=PATTERN` | File pattern for -find (default: `*.v`) |
| `-v, --verbose` | Verbose output |
| `-h, --help` | Show help |

## Translation Modes

### Standard Mode (default)

Uses IEEE std_logic_1164 - compatible with any VHDL simulator:

```vhdl
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
```

### Enhanced Mode

Uses cameron_eda libraries with improved UDN handling:

```vhdl
library cameron_eda;
use cameron_eda.enhanced_logic_1164.all;
use cameron_eda.numeric_std_enhanced.all;
```

The enhanced types provide:
- Proper X-propagation (pessimistic, catches more bugs)
- Multi-value resolution for bidirectional nets
- Compatibility with federated simulation infrastructure

## What Gets Translated

| SystemVerilog | VHDL |
|---------------|------|
| `module` | `entity` + `architecture` |
| `input/output` | `port` declarations |
| `wire/reg` | `signal` declarations |
| `always @(posedge clk)` | `process(clk)` with `rising_edge()` |
| `always @(*)` | `process(all)` (VHDL-2008) |
| `assign` | Concurrent signal assignment |
| `<=` (non-blocking) | Signal assignment |
| `=` (blocking) | Variable assignment |
| Gate primitives | Boolean expressions |
| Module instances | Entity instantiation |

## Example

**Input (counter.v):**
```verilog
module counter (
    input clk,
    input rst,
    output reg [7:0] count
);
    always @(posedge clk or posedge rst) begin
        if (rst)
            count <= 8'h00;
        else
            count <= count + 1;
    end
endmodule
```

**Output (counter.vhd):**
```vhdl
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity counter is
  port (
    clk : in std_logic;
    rst : in std_logic;
    count : inout std_logic_vector(7 downto 0)
  );
end entity;

architecture rtl of counter is
begin
  process(clk, rst)
  begin
    if rst = '1' then
        count <= x"00";
    elsif rising_edge(clk) then
        count <= std_logic_vector(unsigned(count) + 1);
    end if;
  end process;
end architecture;
```

## Project Structure

```
sv2ghdl/
├── sv2ghdl.pl              # Main translator
├── README.md               # This file
├── PERL_IMPLEMENTATION.md  # Implementation notes
├── Makefile                # Test runner
├── packages/
│   └── enhlogic/           # Enhanced VHDL libraries
├── tests/                  # Basic functional tests
├── tests-atpg/             # ATPG-driven equivalence tests
│   ├── *.v                 # Gate-level test cases
│   ├── test2vhdl.pl        # Test harness generator
│   └── compare_results.pl  # SV vs VHDL comparison
└── tests-RTL/              # RTL translation tests
    ├── *.v                 # RTL test cases
    └── synth.ys            # Yosys synthesis script
```

## Running Tests

```bash
# Run all tests
make all

# Run specific test suite
make test_misc
make tests_atpg
```

## Roadmap

### Phase 1: RTL Translation (current)
- [x] Basic module/entity translation
- [x] Ports and signals
- [x] Always blocks (sequential and combinational)
- [x] Gate primitives
- [ ] Case statements
- [ ] Generate blocks
- [ ] Parameterized modules

### Phase 2: Gate-Level Support
- [ ] Post-synthesis netlist translation
- [ ] Standard cell library mapping
- [ ] SDF timing annotation passthrough

### Phase 3: Federated Simulation
- [ ] GHDL/NVC foreign interface integration
- [ ] SV testbench ↔ VHDL DUT bridge
- [ ] Mixed-language co-simulation

### Phase 4: Enhanced Libraries
- [ ] cameron_eda library completion
- [ ] Multi-UDN wire types
- [ ] Improved X-propagation models

### Phase 5: Discipline/Nature Wire Model
- [ ] GHDL/NVC extensions for discipline declarations
- [ ] Electrical discipline with potential/flow drivers
- [ ] Two-phase elaboration: connectivity then resolution analysis
- [ ] PWL signal types for discrete analog representation
- [ ] Mixed potential/flow driver resolution

### Phase 6: Federation Library (C++)
- [ ] VHPI interface for GHDL/NVC
- [ ] PWL source implementation with breakpoint scheduling
- [ ] SPICE-like sources (SIN, PULSE, EXP)
- [ ] Xyce integration for analog solving
- [ ] SV testbench bridge

## Related Projects

- [GHDL](https://github.com/ghdl/ghdl) - Open-source VHDL simulator
- [NVC](https://github.com/nickg/nvc) - VHDL compiler and simulator
- [Verilator](https://github.com/verilator/verilator) - Verilog/SV simulator (for comparison)

## References

- **P1800 Proposals for Mixed-UDN Support** (Kevin Cameron) - IEEE P1800 SystemVerilog proposal for mixed user-defined net types, defining the driver/receiver model, nature/discipline semantics, connect-modules, and resolution strategies that inform this project's architecture. Key concepts:
  - Drivers and receivers as first-class constructs with `.driver[i].value`, `.waveform`, `.mine`, `.find()`
  - Nature-labeled UDN structs (`potential Voltage:`, `flow Current:`)
  - Resolvers as simulation artifacts in "resolver space"
  - No-MAR (per-receiver) resolution for performance
  - Arena resolution for RF/free-space modeling

## License

GPL v2+

## Author

Kevin Cameron

---

*"Leverage VHDL's strengths where SystemVerilog falls short."*
