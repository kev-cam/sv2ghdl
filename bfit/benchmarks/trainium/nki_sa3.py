#!/usr/bin/env python
"""sa3 = sa2 (column layout, no DMA in the cycle loop) with the stimulus and the output STREAMED in chunks.

sa2 loaded the whole skewed stimulus [N, T*N] int8 into SBUF (T*N bytes per partition): fine for T=512 (64 KB) but
T=8448 needs 1.06 MB per partition and trn1 has 192 KB (the M=8192 compile on hardware failed with
"Allocated memory out of bound ... SB<0,0>(128x1081344)").  sa3 keeps two CH-cycle stimulus buffers and two CH-cycle
output buffers in SBUF (CH=256: 2x32 KB + 2x1 KB per partition) and DMAs chunk k+1 in / chunk k-1 out while chunk k
is evaluated; the code is fully unrolled, so the compiler overlaps those DMAs with the vector work by itself.
Port behaviour is identical to sa2/sa (checked word-for-word against nki_sa.golden, the RTL model).

Usage: nki_sa3.py [M] [N]   -> CPU-simulator run checked against the golden model and against A x W.
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
import nki_sa2                      # make_inputs

M, N = 256, 128
CH = 256                            # stream cycles per chunk (even; T % CH == 0)
if __name__ == "__main__":
    M = int(sys.argv[1]) if len(sys.argv) > 1 else M
    N = int(sys.argv[2]) if len(sys.argv) > 2 else N
T = M + 2 * N


@nki.jit
def sa3_kernel(skew, wT):
    """skew: [N, T*N] int8 (as sa2), wT: [N, N] int8 (as sa2); returns outT: [N, T] int32 (as sa2)."""
    n = wT.shape[0]
    t_stream = skew.shape[1] // n
    ch = CH
    nchunk = t_stream // ch
    outT = nl.ndarray((n, t_stream), dtype=nl.int32, buffer=nl.shared_hbm)

    w = nl.ndarray((n, n), dtype=nl.int8, buffer=nl.sbuf)
    nisa.dma_copy(dst=w, src=wT)
    P0 = nl.zeros((n, n), dtype=nl.int32, buffer=nl.sbuf)
    P1 = nl.zeros((n, n), dtype=nl.int32, buffer=nl.sbuf)
    prod = nl.ndarray((n, n), dtype=nl.int32, buffer=nl.sbuf)
    sk0 = nl.ndarray((n, ch * n), dtype=nl.int8, buffer=nl.sbuf)
    sk1 = nl.ndarray((n, ch * n), dtype=nl.int8, buffer=nl.sbuf)
    ob0 = nl.ndarray((n, ch), dtype=nl.int32, buffer=nl.sbuf)
    ob1 = nl.ndarray((n, ch), dtype=nl.int32, buffer=nl.sbuf)

    def cycle(Pcur, Pnxt, sk, ob, j):
        nisa.tensor_tensor(dst=prod, data1=sk[:, j * n:(j + 1) * n], data2=w, op=nl.multiply)      # a_in * w_q
        nisa.tensor_tensor(dst=Pnxt[:, 1:n], data1=Pcur[:, 0:n - 1], data2=prod[:, 1:n], op=nl.add)  # p_q <= p_in + a*w
        nisa.tensor_copy(dst=Pnxt[:, 0:1], src=prod[:, 0:1])                                      # top row: p_in = 0
        nisa.tensor_copy(dst=ob[:, j:j + 1], src=Pnxt[:, n - 1:n])                                 # bottom-row output

    for k in range(nchunk):
        sk = sk0 if k % 2 == 0 else sk1
        ob = ob0 if k % 2 == 0 else ob1
        nisa.dma_copy(dst=sk, src=skew[:, k * ch * n:(k + 1) * ch * n])
        for i in range(ch // 2):
            cycle(P0, P1, sk, ob, 2 * i)
            cycle(P1, P0, sk, ob, 2 * i + 1)
        nisa.dma_copy(dst=outT[:, k * ch:(k + 1) * ch], src=ob)
    return outT


if __name__ == "__main__":
    assert T % CH == 0 and CH % 2 == 0
    nki_sa.N, nki_sa.T = N, T
    nki_sa2.M, nki_sa2.N, nki_sa2.T = M, N, T
    rng = np.random.default_rng(1)
    A = rng.integers(-128, 128, size=(M, N), dtype=np.int64)
    W = rng.integers(-128, 128, size=(N, N), dtype=np.int64)
    stim_a, skew, wT = nki_sa2.make_inputs(A, W)
    stim_w = np.array([W[N - 1 - t] for t in range(N)])
    t0 = time.time(); gold = nki_sa.golden(stim_a, stim_w); tg = time.time() - t0
    t0 = time.time(); res = nki.simulate(sa3_kernel)(skew, wT); ts = time.time() - t0
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
    print(f"sa3 N={N} M={M} T={T} CH={CH} chunks={T // CH}: golden {tg:.2f}s, NKI CPU-sim {ts:.2f}s")
    print(f"stream words compared={T * N} mismatches={mism}; de-skewed C=AxW checked={checked} wrong={bad}")
    sys.exit(0 if (mism == 0 and bad == 0) else 1)
