#!/usr/bin/env python
"""sa3u = sa3 with the UNCERTAINTY plane (3D logic): every word carries a value and an uncertain flag.

Rule used (the conservative arithmetic rule of the sLUT engine): a result word is uncertain when any input word
is uncertain.  For the PE,  p_q <= p_in + a_in * w_q :
    U(p_q) = U(p_in) | U(a_in) | U(w_q)          (top row: p_in is the constant 0, certain)
The value plane is computed exactly as in sa3; where a word is uncertain its value is "don't care".
The P registers power on UNCERTAIN (no reset is applied in this run), so the first N output rows are uncertain and
the uncertainty flushes out of the array as certain data shifts down; uncertain activations and one uncertain
weight are injected to check propagation.  Per simulated cycle: 3 value ops + 3 flag ops + 2 output copies.

Usage: nki_sa3u.py [M] [N]   -> CPU-simulator run checked against a two-plane numpy model of the RTL.
"""
import os, sys, time
import numpy as np

os.environ.setdefault("NEURON_PLATFORM_TARGET_OVERRIDE", "trn2")
os.environ.setdefault("NEURON_LOGICAL_NC_CONFIG", "1")
import nki
import nki.language as nl
import nki.isa as nisa
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nki_sa, nki_sa2

M, N = 256, 128
CH = 256
if __name__ == "__main__":
    M = int(sys.argv[1]) if len(sys.argv) > 1 else M
    N = int(sys.argv[2]) if len(sys.argv) > 2 else N
T = M + 2 * N


@nki.jit
def sa3u_kernel(skew, uskew, wT, uwT):
    """skew/uskew: [N, T*N] int8 value / uncertain flag (0 or 1); wT/uwT: [N, N] int8.
    Returns outT [N, T] int32 and uoutT [N, T] int8 (1 = that output word is uncertain)."""
    n = wT.shape[0]
    t_stream = skew.shape[1] // n
    ch = CH
    nchunk = t_stream // ch
    outT = nl.ndarray((n, t_stream), dtype=nl.int32, buffer=nl.shared_hbm)
    uoutT = nl.ndarray((n, t_stream), dtype=nl.int8, buffer=nl.shared_hbm)

    w = nl.ndarray((n, n), dtype=nl.int8, buffer=nl.sbuf)
    uw = nl.ndarray((n, n), dtype=nl.int8, buffer=nl.sbuf)
    nisa.dma_copy(dst=w, src=wT)
    nisa.dma_copy(dst=uw, src=uwT)
    P0 = nl.zeros((n, n), dtype=nl.int32, buffer=nl.sbuf)
    P1 = nl.zeros((n, n), dtype=nl.int32, buffer=nl.sbuf)
    U0 = nl.ndarray((n, n), dtype=nl.int8, buffer=nl.sbuf)
    U1 = nl.ndarray((n, n), dtype=nl.int8, buffer=nl.sbuf)
    nisa.memset(dst=U0, value=1)                       # power-on: every P register uncertain
    nisa.memset(dst=U1, value=1)
    prod = nl.ndarray((n, n), dtype=nl.int32, buffer=nl.sbuf)
    uprod = nl.ndarray((n, n), dtype=nl.int8, buffer=nl.sbuf)
    sk0 = nl.ndarray((n, ch * n), dtype=nl.int8, buffer=nl.sbuf)
    sk1 = nl.ndarray((n, ch * n), dtype=nl.int8, buffer=nl.sbuf)
    us0 = nl.ndarray((n, ch * n), dtype=nl.int8, buffer=nl.sbuf)
    us1 = nl.ndarray((n, ch * n), dtype=nl.int8, buffer=nl.sbuf)
    ob0 = nl.ndarray((n, ch), dtype=nl.int32, buffer=nl.sbuf)
    ob1 = nl.ndarray((n, ch), dtype=nl.int32, buffer=nl.sbuf)
    uo0 = nl.ndarray((n, ch), dtype=nl.int8, buffer=nl.sbuf)
    uo1 = nl.ndarray((n, ch), dtype=nl.int8, buffer=nl.sbuf)

    def cycle(Pc, Pn, Uc, Un, sk, us, ob, uo, j):
        # value plane (as sa3)
        nisa.tensor_tensor(dst=prod, data1=sk[:, j * n:(j + 1) * n], data2=w, op=nl.multiply)
        nisa.tensor_tensor(dst=Pn[:, 1:n], data1=Pc[:, 0:n - 1], data2=prod[:, 1:n], op=nl.add)
        nisa.tensor_copy(dst=Pn[:, 0:1], src=prod[:, 0:1])
        nisa.tensor_copy(dst=ob[:, j:j + 1], src=Pn[:, n - 1:n])
        # uncertain plane
        nisa.tensor_tensor(dst=uprod, data1=us[:, j * n:(j + 1) * n], data2=uw, op=nl.bitwise_or)
        nisa.tensor_tensor(dst=Un[:, 1:n], data1=Uc[:, 0:n - 1], data2=uprod[:, 1:n], op=nl.bitwise_or)
        nisa.tensor_copy(dst=Un[:, 0:1], src=uprod[:, 0:1])
        nisa.tensor_copy(dst=uo[:, j:j + 1], src=Un[:, n - 1:n])

    for k in range(nchunk):
        sk = sk0 if k % 2 == 0 else sk1
        us = us0 if k % 2 == 0 else us1
        ob = ob0 if k % 2 == 0 else ob1
        uo = uo0 if k % 2 == 0 else uo1
        nisa.dma_copy(dst=sk, src=skew[:, k * ch * n:(k + 1) * ch * n])
        nisa.dma_copy(dst=us, src=uskew[:, k * ch * n:(k + 1) * ch * n])
        for i in range(ch // 2):
            cycle(P0, P1, U0, U1, sk, us, ob, uo, 2 * i)
            cycle(P1, P0, U1, U0, sk, us, ob, uo, 2 * i + 1)
        nisa.dma_copy(dst=outT[:, k * ch:(k + 1) * ch], src=ob)
        nisa.dma_copy(dst=uoutT[:, k * ch:(k + 1) * ch], src=uo)
    return outT, uoutT


def skew_of(stim):
    """[T, N] per-cycle row inputs -> the kernel's [N, T*N] column-skewed stream (as nki_sa2.make_inputs)."""
    sk = np.zeros((N, T, N), np.int8)
    for c in range(N):
        sk[c, c:, :] = stim[:T - c, :].astype(np.int8)
    return sk.reshape(N, T * N)


def make_case(seed=1, n_xa=3, x_weight=True):
    """Stimulus with a few uncertain activation rows-entries and (optionally) one uncertain weight."""
    rng = np.random.default_rng(seed)
    A = rng.integers(-128, 128, size=(M, N), dtype=np.int64)
    W = rng.integers(-128, 128, size=(N, N), dtype=np.int64)
    UA = np.zeros((M, N), np.int64)
    for _ in range(n_xa):
        UA[rng.integers(0, M), rng.integers(0, N)] = 1
    UW = np.zeros((N, N), np.int64)                      # UW[k, c]: weight of PE(row k, column c)
    if x_weight:
        UW[rng.integers(0, N), rng.integers(0, N)] = 1
    stim_a = np.zeros((T, N), np.int64); ustim_a = np.zeros((T, N), np.int64)
    for s in range(T):
        for k in range(N):
            if 0 <= s - k < M:
                stim_a[s, k] = A[s - k, k]; ustim_a[s, k] = UA[s - k, k]
    inputs = {"skew": skew_of(stim_a), "uskew": skew_of(ustim_a),
              "wT": np.ascontiguousarray(W.T.astype(np.int8)), "uwT": np.ascontiguousarray(UW.T.astype(np.int8))}
    return inputs, (stim_a, ustim_a, W, UW)


def golden_u(stim_a, ustim_a, W, UW):
    """Two-plane model of the RTL after the weight load: PE(r,c) state a_q, p_q (+ flags); P powers on uncertain."""
    A = np.zeros((N, N), np.int64); UAq = np.zeros((N, N), np.int64)
    P = np.zeros((N, N), np.int64); UP = np.ones((N, N), np.int64)
    out = np.empty((T, N), np.int64); uout = np.empty((T, N), np.int64)
    for s in range(T):
        a_pe = np.empty_like(A); a_pe[:, 0] = stim_a[s]; a_pe[:, 1:] = A[:, :-1]
        ua_pe = np.empty_like(UAq); ua_pe[:, 0] = ustim_a[s]; ua_pe[:, 1:] = UAq[:, :-1]
        p_pe = np.zeros_like(P); p_pe[1:, :] = P[:-1, :]
        up_pe = np.zeros_like(UP); up_pe[1:, :] = UP[:-1, :]
        P = ((p_pe + a_pe * W + 2**31) % 2**32) - 2**31
        UP = up_pe | ua_pe | UW
        A, UAq = a_pe, ua_pe
        out[s] = P[N - 1]; uout[s] = UP[N - 1]
    return out, uout


def check(res, ures, gold, ugold):
    """res/ures: [T, N] from the kernel.  Flags must match exactly; values must match wherever certain."""
    u_bad = int(np.sum(ures.astype(np.int64) != ugold))
    cert = ugold == 0
    v_bad = int(np.sum((res.astype(np.int64) != gold) & cert))
    return u_bad, v_bad, int(cert.sum()), int((~cert).sum())


if __name__ == "__main__":
    assert T % CH == 0 and CH % 2 == 0
    inputs, ref = make_case()
    t0 = time.time(); gold, ugold = golden_u(*ref); tg = time.time() - t0
    t0 = time.time(); r = nki.simulate(sa3u_kernel)(**inputs); ts = time.time() - t0
    res, ures = np.asarray(r[0]).T, np.asarray(r[1]).T
    u_bad, v_bad, ncert, nunc = check(res, ures, gold, ugold)
    print(f"sa3u N={N} M={M} T={T}: golden {tg:.2f}s, NKI CPU-sim {ts:.2f}s; certain words {ncert}, uncertain {nunc}; "
          f"flag mismatches {u_bad}, value mismatches on certain words {v_bad}")
    sys.exit(0 if (u_bad == 0 and v_bad == 0) else 1)
