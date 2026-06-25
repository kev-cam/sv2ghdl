#!/usr/bin/env python3
"""gen_amp.py -- generate an N-stage common-emitter BJT amplifier cascade.

A scaling benchmark circuit for the bfit perf table: every stage is the exact
CE pattern that bfit's recognizer substitutes (Q + Rc[vcc-c] + Rb[vcc-b] +
Re[e-0] + Ce[e-0] + coupling cap), so the same circuit exercises plain engines
and the bfit macromodel path. Device count scales linearly with N.

Dialect is the portable subset accepted by ngspice, Xyce and QSPICE.

Usage: gen_amp.py N [> ampN.cir]
"""
import sys

def gen(n):
    L = ["* %d-stage common-emitter BJT amplifier (bfit scaling benchmark)" % n,
         "* %d transistors, ~%d unknowns" % (n, 6 * n + 4),
         "Vcc vcc 0 12",
         "Vin in 0 SIN(0 0.005 10k)"]
    prev = "in"
    for k in range(1, n + 1):
        b, c, e = "b%d" % k, "c%d" % k, "e%d" % k
        cc = "Cin" if k == 1 else "Cc%d" % (k - 1)
        L += ["%s %s %s 1u" % (cc, prev, b),
              "Rc%d vcc %s 4.7k" % (k, c),
              "Rb%d vcc %s 100k" % (k, b),
              "Re%d %s 0 470" % (k, e),
              "Ce%d %s 0 10u" % (k, e),
              "Q%d %s %s %s QN" % (k, c, b, e)]
        prev = c
    L += ["Cout %s out 1u" % prev,
          "Rload out 0 10k",
          ".model QN NPN(BF=200 IS=1e-14 VAF=100 RB=10 RC=1 RE=0.5 "
          "CJC=3p CJE=8p TF=0.4n TR=10n)",
          "* forced fine step: stresses per-step device load (where bfit wins)",
          ".tran 20n 2m 0 20n",
          ".end", ""]
    return "\n".join(L)

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    sys.stdout.write(gen(n))
