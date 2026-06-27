"""Run the `bfit table` output: a discrete 4-diode bridge built from 2-node diode
models, solved as a real MNA Newton circuit (no merge). Demonstrates table mode
running the whole circuit from table models vs the accurate exp diode.
Bridge: D1 a->p, D2 b->p, D3 n->a, D4 n->b; n=gnd; Vin across a-b; Rl p-gnd.
Unknowns: Va,Vb,Vp,Iin. DC operating point swept over one input cycle."""
import ctypes, math, time, sys
from ctypes import c_double, POINTER, byref

class VaeState(ctypes.Structure):
    _fields_ = [("V", c_double*16), ("Vt", c_double)]

def load(p):
    lib = ctypes.CDLL(p)
    for fn in (lib.vae_eval, lib.vae_jacobian):
        fn.argtypes = [POINTER(VaeState), POINTER(c_double), POINTER(c_double)]
    return lib

def diode(lib, v0, v1):
    s = VaeState(); s.V[0]=v0; s.V[1]=v1
    F=(c_double*2)(); Q=(c_double*2)(); J=(c_double*4)(); Jq=(c_double*4)()
    lib.vae_eval(byref(s), F, Q); lib.vae_jacobian(byref(s), J, Jq)
    return F[0], J[0], J[1]                 # Id (0->1), dId/dV0, dId/dV1

def solve4(A, b):
    M = [A[i][:] + [b[i]] for i in range(4)]
    for c in range(4):
        p = max(range(c, 4), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        pv = M[c][c] or 1e-30
        for r in range(4):
            if r != c and M[r][c]:
                f = M[r][c]/pv
                for k in range(c, 5): M[r][k] -= f*M[c][k]
    return [M[i][4]/(M[i][i] or 1e-30) for i in range(4)]

def stamp(g, J, n0, n1, Id, d0, d1):
    if n0 >= 0: g[n0] += Id
    if n1 >= 0: g[n1] -= Id
    for rn, sgn in ((n0, 1), (n1, -1)):
        if rn < 0: continue
        for cn, dd in ((n0, d0), (n1, d1)):
            if cn < 0: continue
            J[rn][cn] += sgn*dd

def run(lib, Rl=1e3, Vpk=5.0, npts=400, maxit=100):
    diodes = [(0, 2), (1, 2), (-1, 0), (-1, 1)]     # (n0,n1), -1 = gnd
    x = [0.0, 0.0, 0.0, 0.0]                          # Va,Vb,Vp,Iin
    iters = 0; fail = 0; vout = []
    t0 = time.perf_counter()
    for k in range(npts):
        vin = Vpk*math.sin(2*math.pi*k/npts)
        ok = False
        for it in range(maxit):
            Va, Vb, Vp, Iin = x
            volt = {-1: 0.0, 0: Va, 1: Vb, 2: Vp}
            g = [0.0]*4; J = [[0.0]*4 for _ in range(4)]
            bad = False
            for n0, n1 in diodes:
                Id, d0, d1 = diode(lib, volt[n0], volt[n1])
                if not math.isfinite(Id): bad = True; break
                stamp(g, J, n0, n1, Id, d0, d1)
            if bad: break
            g[2] += Vp/Rl; J[2][2] += 1.0/Rl                 # Rl p-gnd
            g[0] += Iin; g[1] -= Iin; J[0][3] += 1; J[1][3] -= 1   # Vin a-b
            g[3] = Va - Vb - vin; J[3][0] += 1; J[3][1] -= 1
            dx = solve4(J, [-v for v in g]); iters += 1
            for i in range(4):
                if   dx[i] >  0.5: dx[i] =  0.5
                elif dx[i] < -0.5: dx[i] = -0.5
                x[i] += dx[i]
            if max(abs(d) for d in dx) < 1e-9: ok = True; break
        if not ok: fail += 1
        vout.append(x[2])
    dur = time.perf_counter() - t0
    return dict(iters=iters, time=dur, fail=fail, npts=npts,
                vpeak=max(vout), vmin=min(vout)), vout

names = sys.argv[1:]
refs = {}
print("\n  model                  pts   newton-iters   time(s)   Vout_pk   fails")
for path in names:
    r, vout = run(load(path))
    refs[path] = vout
    print("  %-20s %5d   %10d   %7.3f   %7.3f   %5d" % (
        path.split("/")[-1], r["npts"], r["iters"], r["time"], r["vpeak"], r["fail"]))
# accuracy: table vs the clamped-exp reference if both present
keys = list(refs)
if len(keys) >= 2:
    a, b = refs[keys[0]], refs[keys[-1]]
    err = max(abs(x-y) for x, y in zip(a, b))
    print("  max |%s - %s| over the cycle: %.4f V" % (
        keys[0].split("/")[-1], keys[-1].split("/")[-1], err))
