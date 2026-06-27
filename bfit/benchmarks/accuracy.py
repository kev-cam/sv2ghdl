"""Accuracy metrics for the bfit league table: THD% (distortion fidelity) and
golden-referenced waveform error. Compares a bfit-macromodel output against the
full-device 'golden' output of the same circuit.

usage: accuracy.py <f0_Hz> <golden.dat> <bfit.dat>
  .dat = whitespace 'time value' (e.g. ngspice wrdata, Xyce .prn col-pair)
"""
import sys, numpy as np

def load(path):
    t, v = [], []
    for ln in open(path):
        p = ln.split()
        if len(p) < 2: continue
        try: tt, vv = float(p[0]), float(p[-1])
        except ValueError: continue
        t.append(tt); v.append(vv)
    return np.array(t), np.array(v)

def _resample(t, v, n, frac=0.5):
    t0 = t[0] + frac*(t[-1]-t[0])          # drop startup transient
    m = t >= t0
    tu = np.linspace(t[m][0], t[-1], n)
    return tu, np.interp(tu, t[m], v[m])

def thd(t, v, f0, n=8192, nharm=10):
    tu, vu = _resample(t, v, n)
    vu = vu - vu.mean()
    V = np.abs(np.fft.rfft(vu*np.hanning(n)))
    freqs = np.fft.rfftfreq(n, tu[1]-tu[0])
    k = lambda f: int(np.argmin(np.abs(freqs - f)))
    fund = V[k(f0)]
    harm = np.sqrt(sum(V[k(h*f0)]**2 for h in range(2, nharm+1)))
    return 100.0*harm/fund, fund

def main():
    f0 = float(sys.argv[1]); gf, bf = sys.argv[2], sys.argv[3]
    tg, vg = load(gf); tb, vb = load(bf)
    tu, vgi = _resample(tg, vg, 8192)             # common steady-state grid
    vbi = np.interp(tu, tb, vb)
    # universal: relative L2 error vs golden (works AC or DC)
    relL2 = 100*np.sqrt(np.mean((vbi-vgi)**2))/np.sqrt(np.mean(vgi**2))
    # DC level (rectifier/PSU rows): mean output error
    dcg, dcb = vgi.mean(), vbi.mean()
    dcerr = 100*abs(dcb-dcg)/abs(dcg) if dcg else float('nan')
    # AC swing gain (signal-path rows): peak-to-peak ratio (phase-insensitive)
    ppg, ppb = vgi.max()-vgi.min(), vbi.max()-vbi.min()
    gain = ppb/ppg if ppg else float('nan')
    print("  rel-L2 err  %.2f%% (of golden RMS)   DC-level err %.2f%%   pk-pk ratio %.4f"
          % (relL2, dcerr, gain))
    if f0 > 0:                                     # THD only meaningful for a tone-driven output
        thd_g, _ = thd(tg, vg, f0); thd_b, _ = thd(tb, vb, f0)
        print("  THD(golden) %.3f%%   THD(bfit) %.3f%%   |dTHD| %.3f pts" % (thd_g, thd_b, abs(thd_g-thd_b)))

if __name__ == "__main__":
    main()
