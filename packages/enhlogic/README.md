# Enhanced Logic Package (enh_logic_1164)

This package provides an alternative to IEEE's `std_logic_1164` package, using the name `enh_logic` instead of `std_logic` throughout.

## Overview

The Enhanced Logic package defines:
- `enh_ulogic` - Unresolved logic type (9 states: U, X, 0, 1, Z, W, L, H, -)
- `enh_logic` - Resolved logic type (with resolution function)
- `enh_logic_vector` - Vector of enh_logic
- `enh_ulogic_vector` - Vector of enh_ulogic

## Purpose

This package is functionally equivalent to IEEE's `std_logic_1164` but uses different type names. This allows:
- Custom logic implementations with different semantics
- Type-safe separation from standard std_logic types
- Experimentation with enhanced or modified logic systems

## Files

- **enh_logic_1164.vhd** - Package declaration (types and function signatures)
- **enh_logic_1164-body.vhd** - Package body (implementation)

## Usage

```vhdl
library work;
use work.enh_logic_1164.all;

entity my_design is
    port (
        clk : in enh_logic;
        rst : in enh_logic;
        data_in : in enh_logic_vector(7 downto 0);
        data_out : out enh_logic_vector(7 downto 0)
    );
end entity;
```

## Features

All features from std_logic_1164 are supported:

### Logical Operators
- `and`, `or`, `nand`, `nor`, `xor`, `xnor`, `not`
- Vectorized operations
- Mixed scalar/vector operations

### Conversion Functions
- `To_bit`, `To_bitvector`
- `To_EnhULogic`, `To_EnhLogicVector`, `To_EnhULogicVector`
- `To_X01`, `To_X01Z`, `To_UX01` (strength strippers)

### Edge Detection
- `rising_edge()`, `falling_edge()`

### Test Functions
- `Is_X()` - Check for unknown/uninitialized values

### String Conversion
- `to_string()` - Convert to human-readable format

## Differences from std_logic_1164

The only differences are naming:
- `std_logic` → `enh_logic`
- `std_ulogic` → `enh_ulogic`
- `std_logic_vector` → `enh_logic_vector`
- `std_ulogic_vector` → `enh_ulogic_vector`
- `STD_X01` → `ENH_X01`
- `STD_X01Z` → `ENH_X01Z`
- `STD_UX01` → `ENH_UX01`
- `STD_UX01Z` → `ENH_UX01Z`

All logic tables, resolution functions, and operations are identical to the IEEE standard.

## Compilation

To compile with GHDL:

```bash
ghdl -a enh_logic_1164.vhd
ghdl -a enh_logic_1164-body.vhd
```

Or for other simulators, compile both files in order.

## License

This package is based on the IEEE std_logic_1164 standard and follows the same license terms.
