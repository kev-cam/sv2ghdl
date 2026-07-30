#!/usr/bin/env python3
"""SYMBOLIC proof of gen_statemachine's worbits single-word peephole.

The C harness prove_worbits_peephole.c checks every (db, sb, w) shape but leans
on an argument about the DATA: both forms are bitwise-linear, so agreeing on the
33 basis patterns implies agreement everywhere.  That argument is sound, but it
is an argument.  This version discharges it instead: for each shape, z3 proves
equivalence over ALL 2^32 source words and ALL 2^32 destination words at once,
by asking for a counterexample and getting `unsat`.

    peephole:  d |= ((s >> sb) & MASK) << db
    worbits:   the while-loop body, which under the guard runs exactly once

Run:  /home/claude/z3env/bin/python prove_worbits_z3.py
"""
import sys

try:
    from z3 import BitVec, BitVecVal, Extract, ZeroExt, LShR, Solver, unsat, Not
except ImportError:
    sys.exit("z3 not importable — see the venv recipe in the commit message")


def worbits_once(d, s, db, sb, w):
    """One iteration of worbits' loop, which is all the guard permits.

    n = 32 - max(db, sb), clamped to w.  Under (db+w<=32 and sb+w<=32) we have
    32-max(db,sb) >= w, so n == w and the loop terminates after one pass.
    """
    n = 32 - max(db, sb)
    if n > w:
        n = w
    assert n == w, "guard should force a single full-width iteration"
    m = 0xFFFFFFFF if n >= 32 else ((1 << n) - 1)
    return d | (((LShR(s, sb)) & BitVecVal(m, 32)) << db)


def folded(d, s, db, sb, w):
    mask = 0xFFFFFFFF if w >= 32 else ((1 << w) - 1)
    return d | ((LShR(s, sb) & BitVecVal(mask, 32)) << db)


def main():
    d = BitVec('d', 32)
    s = BitVec('s', 32)

    shapes = proved = 0
    for db in range(32):
        for sb in range(32):
            for w in range(1, 33):
                if db + w > 32 or sb + w > 32:
                    continue          # outside the guard: emitter keeps the call
                shapes += 1
                sol = Solver()
                sol.add(Not(worbits_once(d, s, db, sb, w) == folded(d, s, db, sb, w)))
                r = sol.check()
                if r != unsat:
                    print("COUNTEREXAMPLE db=%d sb=%d w=%d: %s" % (db, sb, w, sol.model()))
                    return 1
                proved += 1

    print("shapes satisfying the guard : %d" % shapes)
    print("proved unsat (no counterexample over all 2^32 x 2^32 inputs) : %d" % proved)
    print("RESULT: PROVED SYMBOLICALLY — the folded form equals worbits for every"
          " guarded shape and every possible source and destination word")
    return 0


if __name__ == '__main__':
    sys.exit(main())
