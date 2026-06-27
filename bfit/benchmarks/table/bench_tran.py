"""Full-wave bridge-rectifier transient through each VAE-ABI .so.
Va=+Vpk/2 sin, Vb=-Vpk/2 sin, Vn=0; unknown V(p) with RC load.
Backward-Euler + damped Newton. Reports Newton iters, time, Vout, convergence."""
import ctypes, math, time, sys
from ctypes import c_double, POINTER, byref

class VaeState(ctypes.Structure):
    _fields_ = [("V", c_double*16), ("Vt", c_double)]

def load(path):
    lib = ctypes.CDLL(path)
    for fn in (lib.vae_eval, lib.vae_jacobian):
        fn.argtypes = [POINTER(VaeState), POINTER(c_double), POINTER(c_double)]
    return lib

def run_tran(lib, Vpk=5.0, f=1e3, Rl=1e3, C=10e-6, dt=1e-6, T=3e-3):
    n = int(T/dt)
    s = VaeState(); F=(c_double*4)(); Q=(c_double*4)(); J=(c_double*16)(); Jq=(c_double*16)()
    Vp = 0.0; iters = 0; peak = -1e9; vmin = 1e9; diverged = None
    t0 = time.perf_counter()
    last = 0
    for k in range(n):
        t = k*dt
        Va = 0.5*Vpk*math.sin(2*math.pi*f*t); Vb = -Va
        Vp_prev = Vp
        for it in range(80):
            s.V[0]=Va; s.V[1]=Vb; s.V[2]=Vp; s.V[3]=0.0
            lib.vae_eval(byref(s), F, Q)
            if not math.isfinite(F[2]):
                diverged = (k, t); break
            g  = -F[2] - C*(Vp-Vp_prev)/dt - Vp/Rl
            lib.vae_jacobian(byref(s), J, Jq)
            gp = -J[2*4+2] - C/dt - 1.0/Rl
            dx = -g/gp
            if dx >  0.5: dx =  0.5         # damping
            if dx < -0.5: dx = -0.5
            Vp += dx; iters += 1
            if abs(dx) < 1e-9: break
        if diverged: break
        last = k
        if t > 1e-3:                         # steady-ish: after first cycle
            peak = max(peak, Vp); vmin = min(vmin, Vp)
    dur = time.perf_counter() - t0
    return dict(iters=iters, time=dur, steps=last+1, total=n,
                vpeak=(peak if peak>-1e9 else 0), ripple=(peak-vmin if vmin<1e9 else 0),
                diverged=diverged)

names = sys.argv[1:]
print("\n  model                         steps     iters    time(s)   Vout_pk  ripple   status")
for path in names:
    r = run_tran(load(path))
    if r["diverged"]:
        st = "DIVERGED at step %d (t=%.2es)" % r["diverged"]
    else:
        st = "converged"
    print("  %-26s %5d/%-5d %7d  %8.3f  %7.3f  %6.3f   %s" % (
        path.split("/")[-1], r["steps"], r["total"], r["iters"], r["time"],
        r["vpeak"], r["ripple"], st))
