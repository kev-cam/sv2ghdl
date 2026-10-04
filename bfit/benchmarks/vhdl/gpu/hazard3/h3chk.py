#!/usr/bin/env python3
# h3chk.py - the farm checksum of a TOHOST stream, computed on the host from a
# golden file ("TOHOST xxxxxxxx" lines, as tests/hazard3_mandelbrot's
# golden.tohost and tb_hazard3.v print them).  Same fold as h3farm.cu:
# FNV-1a-64 over the 32-bit words, word-wise.  Part of the bfit GPU-farm kit
# (PolyForm Noncommercial 1.0.0).
#   h3chk.py golden.tohost      -> CHK=<16 hex> words=<n> done=<0|1>
import sys

FNV64_OFF, FNV64_PRIME, M64 = 0xcbf29ce484222325, 0x100000001b3, (1 << 64) - 1


def fold(words):
    h = FNV64_OFF
    for w in words:
        h = ((h ^ (w & 0xffffffff)) * FNV64_PRIME) & M64
    return h


def read_tohost(path):
    out = []
    for ln in open(path):
        p = ln.split()
        if len(p) == 2 and p[0] == 'TOHOST':
            out.append(int(p[1], 16))
    return out


if __name__ == '__main__':
    w = read_tohost(sys.argv[1])
    print('CHK=%016X words=%d done=%d' % (fold(w), len(w), int(bool(w) and w[-1] == 0xffffffff)))
