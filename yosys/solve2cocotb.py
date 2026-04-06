#!/usr/bin/env python3
"""Convert cover_solve output to cocotb test stimulus.

Reads the solver's REACHABLE output (stdin or file) and generates
a cocotb test function that replays the input sequence.

Usage:
    cover_solve ... | python3 solve2cocotb.py > test_sat.py
    cover_solve ... > solutions.txt && python3 solve2cocotb.py solutions.txt
"""

import sys
import re
from collections import defaultdict

def parse_solutions(lines):
    """Parse cover_solve output into structured solutions."""
    solutions = []
    current = None

    for line in lines:
        line = line.rstrip()
        m = re.match(r'REACHABLE: (\S+)=(\d+) at cycle (\d+)', line)
        if m:
            if current:
                solutions.append(current)
            current = {
                'signal': m.group(1),
                'value': int(m.group(2)),
                'cycle': int(m.group(3)),
                'inputs': defaultdict(dict),  # cycle -> {signal: value}
            }
            continue

        if current and line.startswith('    cycle '):
            m2 = re.match(r'\s+cycle (\d+): (\S+) = (0x[0-9a-fA-F]+)', line)
            if m2:
                cyc = int(m2.group(1))
                sig = m2.group(2)
                val = int(m2.group(3), 16)
                current['inputs'][cyc][sig] = val

    if current:
        solutions.append(current)
    return solutions


# Signal name to cocotb DUT attribute mapping
SIGNAL_MAP = {
    '_tl_a_valid_i': 'tl_a_valid_i',
    '_tl_a_opcode_i': 'tl_a_opcode_i',
    '_tl_a_address_i': 'tl_a_address_i',
    '_tl_a_data_i': 'tl_a_data_i',
    '_tl_a_mask_i': 'tl_a_mask_i',
    '_tl_a_source_i': 'tl_a_source_i',
    '_tl_a_size_i': 'tl_a_size_i',
    '_tl_d_ready_i': 'tl_d_ready_i',
    '_uart_rx_i': 'uart_rx_i',
    '_gpio_i': 'gpio_i',
    '_spi_miso_i': 'spi_miso_i',
    '_sda_i': 'sda_i',
    '_scl_i': 'scl_i',
}

def sig_to_cocotb(sig_name):
    """Map solver signal name to cocotb DUT attribute."""
    return SIGNAL_MAP.get(sig_name, sig_name.lstrip('_'))


def gen_cocotb_test(sol, test_name=None):
    """Generate a cocotb test function from a solution."""
    sig = sol['signal']
    val = sol['value']
    cycle = sol['cycle']
    if not test_name:
        test_name = f"test_sat_{sig.lstrip('_')}_{val}"

    lines = []
    lines.append(f'@cocotb.test()')
    lines.append(f'async def {test_name}(dut):')
    lines.append(f'    """SAT-generated test: reach {sig}={val} at cycle {cycle}."""')
    lines.append(f'    cocotb.start_soon(Clock(dut.clk_i, 10, units="ns").start())')
    lines.append(f'    # Reset')
    lines.append(f'    dut.rst_ni.value = 0')
    lines.append(f'    for _ in range(5):')
    lines.append(f'        await RisingEdge(dut.clk_i)')
    lines.append(f'    dut.rst_ni.value = 1')
    lines.append(f'    await RisingEdge(dut.clk_i)')
    lines.append(f'')

    # Sort cycles
    max_cycle = max(sol['inputs'].keys()) if sol['inputs'] else 0
    for c in range(max_cycle + 1):
        if c in sol['inputs']:
            lines.append(f'    # Cycle {c}')
            for sig_name, value in sorted(sol['inputs'][c].items()):
                cocotb_sig = sig_to_cocotb(sig_name)
                lines.append(f'    dut.{cocotb_sig}.value = 0x{value:x}')
        lines.append(f'    await RisingEdge(dut.clk_i)')

    lines.append(f'    dut._log.info("{test_name} completed (target: {sig}={val})")')
    lines.append(f'')
    return '\n'.join(lines)


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            input_lines = f.readlines()
    else:
        input_lines = sys.stdin.readlines()

    solutions = parse_solutions(input_lines)

    if not solutions:
        print("# No solutions found in input", file=sys.stderr)
        sys.exit(1)

    # Header
    print('"""SAT-generated coverage closure tests.')
    print(f'Generated from {len(solutions)} cover_solve solutions."""')
    print()
    print('import cocotb')
    print('from cocotb.clock import Clock')
    print('from cocotb.triggers import RisingEdge')
    print()

    for sol in solutions:
        print(gen_cocotb_test(sol))
        print()

    print(f'# Generated {len(solutions)} tests')


if __name__ == '__main__':
    main()
