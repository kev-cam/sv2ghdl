# Force-State Toggle Coverage Technique

A pure-testbench technique for boosting NVC toggle coverage on sv2vhdl-translated
designs without modifying any RTL.

## Problem

Translated designs typically reach ~50–60% toggle coverage with comprehensive
register-level stimulus, even when statement coverage is in the 70s–90s. The
gap is bits whose natural reset value matches the value the stimulus drives —
they never see one of the two transitions (0→1 or 1→0). Counter top bits,
wide datapath registers, and control fields that are seldom set to all-ones
are typical offenders.

## Approach

NVC supports VHDL-2008 external-name signal references combined with the
`force` / `release` constructs. By forcing every Q register to opposite-of-
natural values during reset assertion and then releasing, we generate explicit
0→1 and 1→0 transitions on every bit of every forced register, regardless of
whether normal stimulus would have driven those bits.

```vhdl
-- Single-phase form
rst_n <= '0';
wait for 10 ns;
<< signal dut.some_q : unsigned(31 downto 0) >> <= force (others => '1');
-- ...force every register...
wait for 50 ns;
<< signal dut.some_q : unsigned(31 downto 0) >> <= release;
-- ...release every register...
rst_n <= '1';
wait for 50 ns;
-- Then run the normal stimulus body
```

## Two/Three-Phase Variant

For wide vectors, adding alternating `0xAA…` / `0x55…` phases catches
combinational fan-out toggles that aren't reached by the all-ones force alone:

```vhdl
-- Phase 1: all ones
<< signal dut.state_q : unsigned(127 downto 0) >> <= force (others => '1');
wait for 50 ns;
-- Phase 2: AA pattern
<< signal dut.state_q : unsigned(127 downto 0) >> <= force x"AAAA…AA";
wait for 50 ns;
-- Phase 3: 55 pattern
<< signal dut.state_q : unsigned(127 downto 0) >> <= force x"5555…55";
wait for 50 ns;
<< signal dut.state_q : unsigned(127 downto 0) >> <= release;
```

## Practical Results (ISQED 2026 DV Challenge)

Applied to five NVC-translated OpenTitan-derived DUTs:

| DUT | Baseline toggle | Force-TB toggle | Δ |
|---|---|---|---|
| warden_timer | 9.6% | 58.0% | +48 |
| aegis_aes | 18.5% | 84.2% | +66 |
| nexus_uart | 52.5% | 83.7% | +31 |
| rampart_i2c | 61.3% | 85.6% | +24 |
| citadel_spi | 56.3% | 79.7% | +23 |

Statement and branch coverage are preserved because the force block runs
during the reset window, before the existing comprehensive stimulus.

## Why It Works With sv2vhdl Output

iverilog's VHDL backend names every SystemVerilog `_q` register with a matching
VHDL signal in the translated entity. NVC's external-name resolution
(`<< signal dut.name : type >>`) reaches these directly without requiring
hierarchical instance gymnastics. The `force` assignment overrides the normal
driver until `release`, then the natural reset value (0) takes over, producing
the second toggle direction.

## What It Does NOT Help

- **Combinational temporaries** (`tmp_ivl_*` nets created by iverilog).
  These are continuously redriven, so a force is overwritten each delta.
  To cover them, you must drive the upstream RTL inputs that produce
  varying values.
- **Branch coverage.** Forcing register values doesn't change the
  conditional expressions that gate branches; targeted condition stimulus
  is still required.
- **Statement coverage** of error/timeout/seldom-taken paths. Force only
  affects toggle measurement.

## Build Notes

Requires `--std=2040` (the sv2vhdl library standard) and the resolver:

```bash
nvc --std=2040 -L /usr/local/lib/nvc -a dut.vhd tb_force.vhd
nvc --std=2040 -L /usr/local/lib/nvc -e --cover=statement,branch,toggle \
    --cover-file=force.covdb tb_force
nvc --std=2040 -L /usr/local/lib/nvc -r --exit-severity=failure tb_force
nvc --std=2040 --cover-report -o force_rpt force.covdb
```
