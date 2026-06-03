#!/usr/bin/env python3
"""run_my_regressions.py - convenience entry point for the regress harness.

Maps a friendly target name to the relevant set of (suite x engine) blocks and
runs them through ./regress, which records results (per-test status, last-pass
build SHAs + options + run time, fail-since) into results.db.

Usage:
    run_my_regressions.py                 # everything (all ready blocks)
    run_my_regressions.py iverilog        # iverilog: ivtest + sv-tests under Icarus
    run_my_regressions.py nvc             # nvc/VHDL tests
    run_my_regressions.py verilator       # verilator + rtlmeter
    run_my_regressions.py gate --repo iverilog --push   # CI gate locally (FF origin/main if clean)
    run_my_regressions.py --list          # show targets and their blocks
    run_my_regressions.py nvc --seq --notes "checking worker X"   # extra opts pass through to ./regress

Runs the build-area tool binaries (not system installs); see the harness docs.
"""
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REGRESS = os.path.join(HERE, "regress")

# A target maps to a list of blocks. An empty list means "all ready blocks"
# (i.e. let ./regress pick every block that is runnable).
GROUPS = {
    "everything":  [],
    "all":         [],
    # iverilog-driven: native Icarus over ivtest + the sv-tests corpus
    "iverilog":    ["ivtest/iverilog", "sv-tests/iverilog"],
    # nvc as a VHDL simulator: its own regression + the VHDL/shim paths
    "nvc":         ["nvc/regr", "nvc/unit", "ivtest/nvc-vhdl", "ivtest/iverilog-nvc"],
    # just the VHDL-via-nvc tests
    "vhdl":        ["ivtest/nvc-vhdl", "nvc/regr"],
    # verilator native + RTLMeter (incl. the nvc-via-verilator shim)
    "verilator":   ["sv-tests/verilator", "rtlmeter/verilator", "rtlmeter/verilator-nvc"],
    # whole-corpus groupings
    "svtests":     ["sv-tests/verilator", "sv-tests/iverilog"],
    "ivtest":      ["ivtest/iverilog", "ivtest/iverilog-nvc",
                    "ivtest/nvc-vhdl", "ivtest/iverilog-steve"],
    # upstream Icarus A/B reference
    "steve":       ["ivtest/iverilog-steve"],
}
ALIASES = {"icarus": "iverilog", "vl": "verilator", "sv-tests": "svtests"}


def usage(code=0):
    print(__doc__)
    list_targets()
    sys.exit(code)


def list_targets():
    print("targets:")
    for name in sorted(GROUPS):
        blocks = GROUPS[name] or ["<all ready blocks>"]
        print(f"  {name:<12} {', '.join(blocks)}")
    if ALIASES:
        print("aliases: " + ", ".join(f"{a}->{b}" for a, b in sorted(ALIASES.items())))


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        usage(0)
    if argv and argv[0] == "--list":
        list_targets(); return 0

    # first non-option arg is the target; the rest pass through to ./regress
    target, passthru = "everything", []
    if argv and not argv[0].startswith("-"):
        target, passthru = argv[0], argv[1:]
    else:
        passthru = argv

    # `gate` runs the CI gate on this machine (build current local repo HEAD,
    # run its regressions, --push to FF origin/main if clean). Args pass through
    # to `regress gate` (e.g. --repo iverilog --push). To delegate from a laptop
    # instead, use ./delegate-regressions.
    if target == "gate":
        return subprocess.call([REGRESS, "gate"] + passthru)

    target = ALIASES.get(target, target)
    if target not in GROUPS:
        sys.stderr.write(f"run_my_regressions.py: unknown target '{target}'\n\n")
        list_targets()
        return 2

    if not os.access(REGRESS, os.X_OK):
        sys.stderr.write(f"run_my_regressions.py: harness not found/executable: {REGRESS}\n")
        return 1

    blocks = GROUPS[target]
    cmd = [REGRESS, "run"] + blocks + passthru
    label = target if blocks else "everything (all ready blocks)"
    print(f"==> {label}: {' '.join([os.path.basename(REGRESS), 'run'] + blocks + passthru)}")
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
