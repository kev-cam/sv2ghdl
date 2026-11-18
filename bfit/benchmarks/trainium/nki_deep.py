#!/usr/bin/env python
"""deep = a SYNTHETIC design with L dependent levels of logic between registers, on one 128x128 int32 tile.

Purpose: measure what a simulated cycle costs on a NeuronCore when the design is deep (a CPU core) instead of
one level deep (the systolic array).  Each cycle evaluates the chain
    x0 = S;  x(i+1) = x(i) OP(i) K(i)        for i = 0..L-1 (every level depends on the one before);   S <= xL
with OP cycling through add / and-mask / xor / xor and K four constant tiles, so nothing can be reordered or
fused away.  L = 19 is Hazard3 after the depth pass, L = 137 is Hazard3 as yosys emits it (GPU-TODO.md 1.4).
It is NOT Hazard3: it has the depth, not the logic.  One column of S is streamed out per cycle (as sa3 does).

Usage: nki_deep.py [L] [CYCLES]   -> CPU-simulator run checked against numpy.
"""
import os, sys, time
import numpy as np

os.environ.setdefault("NEURON_PLATFORM_TARGET_OVERRIDE", "trn2")
os.environ.setdefault("NEURON_LOGICAL_NC_CONFIG", "1")
import nki
import nki.language as nl
import nki.isa as nisa

N = 128
MASK = 0xFFFF
L, CYC = 19, 64
if __name__ == "__main__":
    L = int(sys.argv[1]) if len(sys.argv) > 1 else L
    CYC = int(sys.argv[2]) if len(sys.argv) > 2 else CYC


def make_kernel(levels, cycles):
    assert cycles % 2 == 0 and levels >= 2

    @nki.jit
    def deep_kernel(s0, kc):
        """s0: [N, N] int32 initial state (< 2^16); kc: [N, 4*N] int32 constants K0 | MASK | K1 | K2.
        Returns outT [N, cycles] int32: column N-1 of the state after every cycle."""
        n = s0.shape[0]
        outT = nl.ndarray((n, cycles), dtype=nl.int32, buffer=nl.shared_hbm)
        k = nl.ndarray((n, 4 * n), dtype=nl.int32, buffer=nl.sbuf)
        nisa.dma_copy(dst=k, src=kc)
        S0 = nl.ndarray((n, n), dtype=nl.int32, buffer=nl.sbuf)
        S1 = nl.ndarray((n, n), dtype=nl.int32, buffer=nl.sbuf)
        nisa.dma_copy(dst=S0, src=s0)
        ta = nl.ndarray((n, n), dtype=nl.int32, buffer=nl.sbuf)
        tb = nl.ndarray((n, n), dtype=nl.int32, buffer=nl.sbuf)
        ob = nl.ndarray((n, cycles), dtype=nl.int32, buffer=nl.sbuf)
        ops = [nl.add, nl.bitwise_and, nl.bitwise_xor, nl.bitwise_xor]

        def cycle(Sc, Sn, j):
            src = Sc
            for i in range(levels):
                dst = Sn if i == levels - 1 else (ta if i % 2 == 0 else tb)
                q = i % 4
                nisa.tensor_tensor(dst=dst, data1=src, data2=k[:, q * n:(q + 1) * n], op=ops[q])
                src = dst
            nisa.tensor_copy(dst=ob[:, j:j + 1], src=Sn[:, n - 1:n])

        for i in range(cycles // 2):
            cycle(S0, S1, 2 * i)
            cycle(S1, S0, 2 * i + 1)
        nisa.dma_copy(dst=outT, src=ob)
        return outT

    return deep_kernel


def make_inputs(seed=3):
    rng = np.random.default_rng(seed)
    s0 = rng.integers(0, MASK + 1, size=(N, N), dtype=np.int64)
    k0 = rng.integers(1, 1 << 15, size=(N, N), dtype=np.int64)
    k1 = rng.integers(0, MASK + 1, size=(N, N), dtype=np.int64)
    k2 = rng.integers(0, MASK + 1, size=(N, N), dtype=np.int64)
    kc = np.concatenate([k0, np.full((N, N), MASK, np.int64), k1, k2], axis=1)
    return {"s0": s0.astype(np.int32), "kc": np.ascontiguousarray(kc.astype(np.int32))}


def golden(inputs, levels, cycles):
    s = inputs["s0"].astype(np.int64)
    kc = inputs["kc"].astype(np.int64)
    ks = [kc[:, q * N:(q + 1) * N] for q in range(4)]
    out = np.empty((N, cycles), np.int64)
    for j in range(cycles):
        x = s
        for i in range(levels):
            q = i % 4
            x = x + ks[q] if q == 0 else (x & ks[q] if q == 1 else x ^ ks[q])
        s = x
        out[:, j] = s[:, N - 1]
    return out


if __name__ == "__main__":
    inputs = make_inputs()
    kern = make_kernel(L, CYC)
    gold = golden(inputs, L, CYC)
    t0 = time.time(); res = np.asarray(nki.simulate(kern)(**inputs)).astype(np.int64); ts = time.time() - t0
    bad = int(np.sum(res != gold))
    print(f"deep L={L} cycles={CYC}: NKI CPU-sim {ts:.2f}s, words compared {gold.size}, mismatches {bad}, max value {int(gold.max())}")
    sys.exit(0 if bad == 0 else 1)
