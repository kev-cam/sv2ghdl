#!/usr/bin/env python
"""Layout-optimised two-phase evaluation of the same systolic array (sa.sv) - identical port behaviour to nki_sa.py.

Layout change: partition = PE COLUMN c, free = PE ROW r.  Then
  - the partial-sum flow (row r-1 -> r) is a free-dimension offset: evaluated in place by one tensor_tensor on
    shifted views, no data movement at all (no cross-partition DMA);
  - the activation registers are a pure delay line fed only by the primary input (a_q(r,c) at cycle s equals the
    left-edge input of row r at cycle s-c), so their value is an index into the stimulus: a per-column time skew.
    The skewed stimulus is prepared once (here on the host) and read with a free-dimension offset each cycle.
  - weights are stationary after the load phase, which has no observable output: W is DMA'd in transposed once.
Per simulated cycle: 1 multiply, 1 add, 2 column copies - all vector-engine work, no DMA, no cross-engine sync.
Bank switching as before: two P tiles, loop unrolled by two.

Usage: nki_sa2.py [M] [N]   -> CPU-simulator run checked word-for-word against nki_sa.golden (the RTL model).
"""
import os, sys, time
import numpy as np

os.environ.setdefault("NEURON_PLATFORM_TARGET_OVERRIDE", "trn2")
os.environ.setdefault("NEURON_LOGICAL_NC_CONFIG", "1")
import nki
import nki.language as nl
import nki.isa as nisa
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nki_sa                       # golden model + stimulus conventions

M, N = 256, 128
if __name__ == "__main__":
    M = int(sys.argv[1]) if len(sys.argv) > 1 else M
    N = int(sys.argv[2]) if len(sys.argv) > 2 else N
T = M + 2 * N


@nki.jit
def sa2_kernel(skew, wT):
    """skew: [N, T*N] int8, partition c, element t*N + r = a_in of row r at cycle t-c (0 for t < c).
       wT:   [N, N]   int8, partition c, element r = W[r][c].
       returns outT: [N, T] int32, partition c, element s = bottom-row p_out of column c after stream cycle s."""
    n = wT.shape[0]
    t_stream = skew.shape[1] // n
    outT = nl.ndarray((n, t_stream), dtype=nl.int32, buffer=nl.shared_hbm)

    sk = nl.ndarray((n, t_stream * n), dtype=nl.int8, buffer=nl.sbuf)
    nisa.dma_copy(dst=sk, src=skew)
    w = nl.ndarray((n, n), dtype=nl.int8, buffer=nl.sbuf)
    nisa.dma_copy(dst=w, src=wT)
    P0 = nl.zeros((n, n), dtype=nl.int32, buffer=nl.sbuf)
    P1 = nl.zeros((n, n), dtype=nl.int32, buffer=nl.sbuf)
    prod = nl.ndarray((n, n), dtype=nl.int32, buffer=nl.sbuf)
    ob = nl.ndarray((n, t_stream), dtype=nl.int32, buffer=nl.sbuf)

    def cycle(Pcur, Pnxt, s):
        nisa.tensor_tensor(dst=prod, data1=sk[:, s * n:(s + 1) * n], data2=w, op=nl.multiply)   # a_in * w_q
        nisa.tensor_tensor(dst=Pnxt[:, 1:n], data1=Pcur[:, 0:n - 1], data2=prod[:, 1:n], op=nl.add)  # p_q <= p_in + a*w
        nisa.tensor_copy(dst=Pnxt[:, 0:1], src=prod[:, 0:1])                                      # top row: p_in = 0
        nisa.tensor_copy(dst=ob[:, s:s + 1], src=Pnxt[:, n - 1:n])                                 # bottom-row output

    for i in nl.sequential_range(t_stream // 2):
        cycle(P0, P1, 2 * i)
        cycle(P1, P0, 2 * i + 1)
    nisa.dma_copy(dst=outT, src=ob)
    return outT


def make_inputs(A, W):
    """Host-side stimulus preparation (same A, W conventions as tb_sa.cpp / nki_sa.py)."""
    stim_a = np.zeros((T, N), np.int64)
    for s in range(T):
        for k in range(N):
            if 0 <= s - k < M:
                stim_a[s, k] = A[s - k, k]
    skew = np.zeros((N, T, N), np.int8)          # [c, t, r] = stim_a[t - c, r]
    for c in range(N):
        skew[c, c:, :] = stim_a[:T - c, :].astype(np.int8)
    return stim_a, skew.reshape(N, T * N), np.ascontiguousarray(W.T.astype(np.int8))


if __name__ == "__main__":
    assert T % 2 == 0
    nki_sa.N, nki_sa.T = N, T
    rng = np.random.default_rng(1)
    A = rng.integers(-128, 128, size=(M, N), dtype=np.int64)
    W = rng.integers(-128, 128, size=(N, N), dtype=np.int64)
    stim_a, skew, wT = make_inputs(A, W)
    stim_w = np.array([W[N - 1 - t] for t in range(N)])
    t0 = time.time(); gold = nki_sa.golden(stim_a, stim_w); tg = time.time() - t0
    t0 = time.time(); res = nki.simulate(sa2_kernel)(skew, wT); ts = time.time() - t0
    res = np.asarray(res).astype(np.int64).T            # [T, N]
    mism = int(np.sum(res != gold))
    C = A @ W
    bad = checked = 0
    for s in range(T):
        for c in range(N):
            m = s - N - c + 1
            if 0 <= m < M:
                checked += 1
                bad += int(res[s, c] != C[m, c])
    print(f"sa2 N={N} M={M} T={T}: golden {tg:.2f}s, NKI CPU-sim {ts:.2f}s")
    print(f"stream words compared={T * N} mismatches={mism}; de-skewed C=AxW checked={checked} wrong={bad}")
    sys.exit(0 if (mism == 0 and bad == 0) else 1)
