#!/usr/bin/env python3
"""Parse Verilator coverage annotations and generate cover_solve targets.

Reads the annotated coverage file (verilator_coverage --annotate output)
and identifies signals with zero toggle coverage, producing --target flags
for cover_solve.

Usage:
    python3 cov2targets.py <annotated_file.sv> [--max N]
"""

import re
import sys


def parse_annotated(filename, max_targets=20):
    """Extract uncovered toggle targets from Verilator annotation."""
    targets = []

    with open(filename) as f:
        for line in f:
            # %000000 means zero hits — completely uncovered toggle
            # Look for signal declarations with %000000
            m = re.match(r'\s*%0{6}\s+(?:input|output|inout)?\s*(?:logic|wire|reg)\s+'
                         r'(?:\[[\d:]+\]\s+)?(\w+)', line)
            if m:
                sig_name = m.group(1)
                # Generate toggle target: signal=1 (make it go high)
                targets.append(f'--target=_{sig_name}=1')
                if len(targets) >= max_targets:
                    break

            # Also catch internal signals
            m2 = re.match(r'\s*%0{6}\s+logic\s+(?:\[[\d:]+\]\s+)?(\w+)', line)
            if m2 and not m:
                sig_name = m2.group(1)
                targets.append(f'--target=_{sig_name}=1')
                if len(targets) >= max_targets:
                    break

    return targets


def main():
    if len(sys.argv) < 2:
        print("Usage: cov2targets.py <annotated.sv> [--max N]", file=sys.stderr)
        sys.exit(1)

    filename = sys.argv[1]
    max_t = 20
    for i, arg in enumerate(sys.argv):
        if arg == '--max' and i+1 < len(sys.argv):
            max_t = int(sys.argv[i+1])

    targets = parse_annotated(filename, max_t)

    if targets:
        print(f"# {len(targets)} uncovered toggle targets from {filename}")
        print(' '.join(targets))
    else:
        print("# No uncovered targets found")


if __name__ == '__main__':
    main()
