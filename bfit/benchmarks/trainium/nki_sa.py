#!/usr/bin/env python
"""Two-phase (bank-swapped) RTL evaluation of the N x N weight-stationary systolic array (sa.sv) as an NKI kernel.

State arrays (one per PE register, partition = PE row, free = PE column):
    A[r][c]  a_q (int8)   flows right  -> next bank = [a_in | A[:, :-1]]            (free-dim shift: one tensor_copy)
    W[r][c]  w_q (int8)   stationary; during load it shifts down                   (partition shift: SBUF->SBUF DMA)
    P[r][c]  p_q (int32)  flows down   -> next bank = shift_down(P) + a_in * W      (partition shift DMA + mul + add)
Bank switching = two tiles per array and two copies of the eval code (loop unrolled by two with roles swapped).
Semantics follow sa.sv exactly: p_q <= p_in + a_in * w_q, a_q <= a_in, w_q <= load_w ? w_in : w_q.

Usage: nki_sa.py [M rows] [N]      (default M=256 -> T = M + 2N stream cycles; N=128)
Runs the kernel on the NKI CPU simulator and compares every output word with a numpy golden model of the RTL,
then checks the de-skewed result against A x W.
"""
import os, sys, time
import numpy as np

os.environ.setdefault("NEURON_PLATFORM_TARGET_OVERRIDE", "trn2")
os.environ.setdefault("NEURON_LOGICAL_NC_CONFIG", "1")
import nki
import nki.language as nl
import nki.isa as nisa

M, N = 256, 128
if __name__ == "__main__":
    M = int(sys.argv[1]) if len(sys.argv) > 1 else M
    N = int(sys.argv[2]) if len(sys.argv) > 2 else N
T = M + 2 * N          # stream cycles, as in tb_sa.cpp


# ---------------------------------------------------------------- golden model (numpy, int32 wrap) -----------------
def golden(stim_a, stim_w):
    A = np.zeros((N, N), np.int64); Wt = np.zeros((N, N), np.int64); P = np.zeros((N, N), np.int64)
    def step(a_in, w_in, load_w):
        nonlocal A, Wt, P
        a_pe = np.empty_like(A); a_pe[:, 0] = a_in; a_pe[:, 1:] = A[:, :-1]
        w_pe = np.empty_like(Wt); w_pe[0, :] = w_in; w_pe[1:, :] = Wt[:-1, :]
        p_pe = np.zeros_like(P); p_pe[1:, :] = P[:-1, :]
        P_new = p_pe + a_pe * Wt
        A = a_pe
        P = ((P_new + 2**31) % 2**32) - 2**31
        if load_w:
            Wt = w_pe
        return P[N - 1, :].copy()
    for t in range(N):
        step(np.zeros(N, np.int64), stim_w[t], True)
    out = np.empty((T, N), np.int64)
    for s in range(T):
        out[s] = step(stim_a[s], np.zeros(N, np.int64), False)
    return out.astype(np.int32)


# ---------------------------------------------------------------- NKI kernel ------------------------------------------
@nki.jit
def sa_kernel(stim_aT, stim_w_flat):
    """stim_aT: [N, T] int8, a_in per row (partition) per stream cycle (free).
       stim_w_flat: [1, N*N] int8, w_in vectors for the N load cycles, concatenated (cycle t at [t*N:(t+1)*N]).
       returns out: [T, N] int32, the bottom-row p_out after each stream cycle."""
    n = stim_aT.shape[0]
    t_stream = stim_aT.shape[1]
    out = nl.ndarray((t_stream, n), dtype=nl.int32, buffer=nl.shared_hbm)

    stim = nl.ndarray((n, t_stream), dtype=nl.int8, buffer=nl.sbuf)
    nisa.dma_copy(dst=stim, src=stim_aT)
    wst = nl.ndarray((1, n * n), dtype=nl.int8, buffer=nl.sbuf)
    nisa.dma_copy(dst=wst, src=stim_w_flat)

    A0 = nl.zeros((n, n), dtype=nl.int8, buffer=nl.sbuf);  A1 = nl.zeros((n, n), dtype=nl.int8, buffer=nl.sbuf)
    P0 = nl.zeros((n, n), dtype=nl.int32, buffer=nl.sbuf); P1 = nl.zeros((n, n), dtype=nl.int32, buffer=nl.sbuf)
    W0 = nl.zeros((n, n), dtype=nl.int8, buffer=nl.sbuf);  W1 = nl.zeros((n, n), dtype=nl.int8, buffer=nl.sbuf)
    prod = nl.ndarray((n, n), dtype=nl.int32, buffer=nl.sbuf)
    pin = nl.zeros((n, n), dtype=nl.int32, buffer=nl.sbuf)      # row 0 is the array's top edge: stays zero
    zcol = nl.zeros((n, 1), dtype=nl.int8, buffer=nl.sbuf)       # a_in = 0 during the load phase

    def cycle(Acur, Pcur, Wcur, Anxt, Pnxt, a_col):
        # phase 1: evaluate next bank from current bank
        nisa.tensor_copy(dst=Anxt[:, 1:n], src=Acur[:, 0:n - 1])          # a flows right
        nisa.tensor_copy(dst=Anxt[:, 0:1], src=a_col)                      # left edge input
        nisa.tensor_tensor(dst=prod, data1=Anxt, data2=Wcur, op=nl.multiply)   # a_in * w_q
        nisa.dma_copy(dst=pin[1:n, :], src=Pcur[0:n - 1, :])              # p flows down (crosses partitions)
        nisa.tensor_tensor(dst=Pnxt, data1=pin, data2=prod, op=nl.add)    # p_q <= p_in + a_in*w_q
        # phase 2 (bank switch) is the caller swapping the roles of the two tiles

    def load_step(Wcur, Wnxt, t):
        nisa.dma_copy(dst=Wnxt[1:n, :], src=Wcur[0:n - 1, :])             # w shifts down
        nisa.dma_copy(dst=Wnxt[0:1, :], src=wst[0:1, t * n:(t + 1) * n])   # top edge input

    # load phase: N cycles, load_w = 1, a_in = 0  (A and P are also evaluated, as in the RTL)
    for i in nl.sequential_range(n // 2):
        cycle(A0, P0, W0, A1, P1, zcol); load_step(W0, W1, 2 * i)
        cycle(A1, P1, W1, A0, P0, zcol); load_step(W1, W0, 2 * i + 1)
    # stream phase: T cycles, load_w = 0 (W0 holds the loaded weights since N is even)
    for i in nl.sequential_range(t_stream // 2):
        s0 = 2 * i
        cycle(A0, P0, W0, A1, P1, stim[:, s0:s0 + 1])
        nisa.dma_copy(dst=out[s0:s0 + 1, :], src=P1[n - 1:n, :])
        s1 = 2 * i + 1
        cycle(A1, P1, W0, A0, P0, stim[:, s1:s1 + 1])
        nisa.dma_copy(dst=out[s1:s1 + 1, :], src=P0[n - 1:n, :])
    return out


# ---------------------------------------------------------------- host -----------------------------------------------
if __name__ == "__main__":
    assert N % 2 == 0 and T % 2 == 0
    rng = np.random.default_rng(1)
    A = rng.integers(-128, 128, size=(M, N), dtype=np.int64)
    W = rng.integers(-128, 128, size=(N, N), dtype=np.int64)
    stim_a = np.zeros((T, N), np.int64)
    for s in range(T):
        for k in range(N):
            m = s - k
            if 0 <= m < M:
                stim_a[s, k] = A[m, k]
    stim_w = np.array([W[N - 1 - t] for t in range(N)])      # inject W[N-1-t][c] at load cycle t

    t0 = time.time(); gold = golden(stim_a, stim_w); tg = time.time() - t0
    t0 = time.time()
    res = nki.simulate(sa_kernel)(np.ascontiguousarray(stim_a.T.astype(np.int8)),
                                  np.ascontiguousarray(stim_w.reshape(1, N * N).astype(np.int8)))
    ts = time.time() - t0
    res = np.asarray(res).astype(np.int64)
    mism = int(np.sum(res != gold))
    # de-skew: column c at stream cycle s holds C[m][c] with m = s - N - c + 1
    C = A @ W
    bad = 0; checked = 0
    for s in range(T):
        for c in range(N):
            m = s - N - c + 1
            if 0 <= m < M:
                checked += 1
                if res[s, c] != C[m, c]:
                    bad += 1
    print(f"N={N} M={M} T={T}: golden {tg:.2f}s, NKI CPU-sim {ts:.2f}s ({ts / (T + N) * 1e3:.1f} ms/cycle, timing meaningless)")
    print(f"stream words compared={T * N} mismatches={mism}; de-skewed C=AxW checked={checked} wrong={bad}")
    sys.exit(0 if (mism == 0 and bad == 0) else 1)
