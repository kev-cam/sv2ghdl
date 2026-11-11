# Enhanced Logic Package (enh_logic_1164)

This package provides an alternative to IEEE's `std_logic_1164` package, using the name `enh_logic` instead of `std_logic` throughout. It is the type library behind `sv2ghdl.pl --mode=enhanced`.

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

Both files depend on `ieee.std_logic_1164`, for the conversions below.

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

### Conversions to and from the IEEE types

There is no numeric package for `enh_logic_vector`. Arithmetic goes through
`ieee.numeric_std` on the IEEE side of these conversions, which map the two
enumerations position for position (they have the same order):

- `to_std_logic(enh_ulogic)`, `to_enh_logic(std_ulogic)`
- `to_std_logic_vector(enh_logic_vector)`, `to_enh_logic_vector(std_logic_vector)`

`sv2ghdl.pl --mode=enhanced` emits, for `count <= count + 1`:

```vhdl
count <= to_enh_logic_vector(std_logic_vector(unsigned(to_std_logic_vector(count)) + 1));
```

and uses `to_std_logic_vector` to hand vectors to the `sv2vhdl` display helpers.

### Edge Detection
- `rising_edge()`, `falling_edge()`

### Test Functions
- `Is_X()` - Check for unknown/uninitialized values

### String Conversion
- `to_string()` - Convert to human-readable format

## Differences from std_logic_1164

Apart from the IEEE conversions above, the only differences are naming:
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

Analyse the declaration before the body, into `work`, before any design that uses it. With the
nvc fork of this stack (the translated designs also need its `sv2vhdl` library, built at
standard 2040):

```bash
nvc --std=2040 -L <nvc prefix>/lib -a enh_logic_1164.vhd enh_logic_1164-body.vhd design.vhd
nvc --std=2040 -L <nvc prefix>/lib -e design
```

With GHDL:

```bash
ghdl -a enh_logic_1164.vhd
ghdl -a enh_logic_1164-body.vhd
```

## License

This package is based on the IEEE std_logic_1164 standard and follows the same license terms.
