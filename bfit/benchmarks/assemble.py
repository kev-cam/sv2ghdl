#!/usr/bin/env python3
"""Generate benchmarks/perf.md from the measured CSVs. Deterministic: every
×multiplier and green/blue dot is computed here from the seconds, not by hand.

  python3 assemble.py [open.csv] [commercial.csv] > perf.md

  open.csv        from model_bench.sh (Linux engines: native + bfit bal/fast + MPI)
  commercial.csv  from win_models.sh  (model,qspice,ltspice); defaults to the
                  committed file next to this script if omitted.

Multiplier reference: base-engine × is vs the row's SLOWEST native engine; the
+bfit and Xyce-MPI × are vs that engine's OWN native run. Dots: green = fastest
cell in the row; blue = an open engine/mode beating BOTH commercial tools."""
import sys, os, csv, math

NTX = {'rectifier':0,'inv_chain':200,'ring_osc':102,'ota_5t':5,'bjt_amp':3,'opamp':8,'breaker':3000}
LABEL = {'rectifier':'Bridge rectifier (4 diodes)','inv_chain':'CMOS inverter chain ×100',
 'ring_osc':'CMOS ring oscillator ×51','ota_5t':'5T OTA (diff pair + mirror)',
 'bjt_amp':'BJT 3-stage CE amp ‡','opamp':'2-stage Miller op-amp','breaker':'BJT cascade ×3000 (breaker)'}
ROWS = ['rectifier','inv_chain','ring_osc','ota_5t','bjt_amp','opamp','breaker']

HERE = os.path.dirname(os.path.abspath(__file__))
OPEN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get('MODELS','.'), 'open.csv')
COMM = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, 'commercial.csv')

def num(x):
    if x is None: return None
    x = str(x).strip()
    if x in ('', 'brk', 'na', '-', '—', 'FAIL', '?'): return None
    try: return float(x)
    except ValueError: return None

def rows_by_model(path):
    d = {}
    if not os.path.exists(path): return d
    for r in csv.DictReader(open(path)):
        d[r['model']] = r
    return d

op = rows_by_model(OPEN)
cm = rows_by_model(COMM)

def mult(ref, x):
    return None if (ref is None or x is None or x <= 0) else ref / x

def db(a):  # rel-L2 % -> signal-to-error ratio in dB (higher = better).
    v = num(str(a).rstrip('%')) if a is not None else None
    if v is None: return '?'
    if v <= 0: return '∞ dB'
    d = -20 * math.log10(v / 100.0)
    return ('%+.0f dB' % d) if abs(d) >= 0.5 else '0 dB'

def fmt(sec, mu, extra='', dot=''):
    if sec is None: return 'brk'
    s = ('%.0f' % sec) if sec >= 10 else ('%.2g' % sec)
    out = s + ('' if mu is None else ' ×%.1f' % mu) + extra
    return (dot + ' ' if dot else '') + out

HDR = ['Model','# Tx','QSPICE','LTspice','ngspice','Xyce','Xyce-MPI',
       'ng+bfit bal','ng+bfit fast','xy+bfit bal','xy+bfit fast']
lines = ['| ' + ' | '.join(HDR) + ' |', '| :-- | --: ' + '| --: '*9 + '|']
for m in ROWS:
    o = op.get(m, {}); c = cm.get(m, {})
    qs, lt = num(c.get('qspice')), num(c.get('ltspice'))
    ngb, xyb = num(o.get('ng_base')), num(o.get('xy_base'))
    nbal, nfast = num(o.get('ng_bal')), num(o.get('ng_fast'))
    xbal, xfast = num(o.get('xy_bal')), num(o.get('xy_fast'))
    mpi, mnp = num(o.get('mpi_best')), o.get('mpi_np')
    ref = max([b for b in (qs, lt, ngb, xyb) if b is not None], default=None)  # slowest native
    comm_min = min([x for x in (qs, lt) if x is not None], default=None)
    cells = {'qspice':qs,'ltspice':lt,'ngspice':ngb,'xyce':xyb,'mpi':mpi,
             'nbal':nbal,'nfast':nfast,'xbal':xbal,'xfast':xfast}
    fin = {k:v for k,v in cells.items() if v is not None}
    green = min(fin, key=fin.get) if fin else None
    openk = {'ngspice','xyce','mpi','nbal','nfast','xbal','xfast'}
    if comm_min is not None:
        blue = {k for k in fin if k in openk and fin[k] < comm_min and k != green}
    else:                                  # no commercial finishes -> any open finisher beats them
        blue = {k for k in fin if k in openk and k != green}
    dot = lambda k: '🟢' if k == green else ('🔵' if k in blue else '')
    row = [LABEL[m], str(NTX[m]),
           fmt(qs, mult(ref, qs), dot=dot('qspice')), fmt(lt, mult(ref, lt), dot=dot('ltspice')),
           fmt(ngb, mult(ref, ngb), dot=dot('ngspice')), fmt(xyb, mult(ref, xyb), dot=dot('xyce'))]
    row.append(fmt(mpi, mult(xyb, mpi), ' (np %s)' % mnp if mpi else '', dot('mpi')) if mpi else '—')
    row += [fmt(nbal, mult(ngb, nbal), ' (%s)' % db(o.get('ng_bal_acc')), dot('nbal')) if nbal else '—',
            fmt(nfast, mult(ngb, nfast), ' (%s)' % db(o.get('ng_fast_acc')), dot('nfast')) if nfast else '—',
            fmt(xbal, mult(xyb, xbal), ' (%s)' % db(o.get('xy_bal_acc')), dot('xbal')) if xbal else '—',
            fmt(xfast, mult(xyb, xfast), ' (%s)' % db(o.get('xy_fast_acc')), dot('xfast')) if xfast else '—']
    lines.append('| ' + ' | '.join(row) + ' |')
TABLE = '\n'.join(lines)

print("""# Cross-engine performance

One run per circuit, **same netlist on every engine**. Each cell is
`seconds ×speedup` (fewer seconds / bigger × is better); **+bfit** cells append
`(signal-to-error ratio in dB vs that engine's own base; higher = better)`.
Transients are sized so
QSPICE solves for **≥3 s** and driven with **multi-tone** inputs so the adaptive
engines can't coast to steady state. 🟢 = fastest cell in the row; 🔵 = an open
engine/mode beating **both** commercial tools. `brk` = aborted (timestep
collapse); `—` = no benefit over that engine's own base.

**The `×` reference.** Base-engine `×` is relative to the **slowest native
engine** in the row (Xyce here → ×1.0). The **+bfit** and **Xyce-MPI** `×` are
relative to **that engine's own native run** — i.e. what the acceleration
actually bought. **bal / fast** are the `bfit front --accuracy` presets
(`balanced` ≈1000 pts + tight LTE; `fast` ≈300 pts + loose LTE); `exact`
(no coarsening, not shown) keeps the engine at reference accuracy.

""" + TABLE + """

**Accuracy = signal-to-error ratio in dB** (`SER = −20·log₁₀(rel-L2)`); higher is
better, +25 dB ≈ 6% error, 0 dB = error equals signal. It is **phase-sensitive**,
so a macromodel that matches amplitude but lags in phase scores low: † the
digital rows (inverter, ring) are dominated by **timing** (edge delay, the
oscillator's frequency), not amplitude; ‡ the BJT amp is an overdriven
**limiter** whose macromodel matches the clipping levels to <1% but sits near
0 dB on phase alone. A delay-aligned SER (removing benign propagation delay) is
the honest fix for the amps — coming next.

**Xyce-MPI.** Domain-decomposition overhead dwarfs the work on small circuits,
so MPI is **slower than serial on every small row** (→ —, killed once it passes
the serial wall-clock). It pays off only at **scale**: the 3000-stage breaker
wins at a *middle* rank count (the cloud / large-circuit lever, not a
single-small-circuit one).

**Behavioral-assist (Xyce column).** Each Xyce cell is the faster of *plain*
Xyce and Xyce with the quiescence-bypass stack (`XYCE_BYPASS=1e-12
XYCE_FROZEN_STATE=1`), chosen per row and verified correct against the plain run.
It wins on the **digital/switching** rows — inverter chain 6.8→6.3 s (−8%), ring
oscillator 21→19 s (−8%) — where most devices sit quiescent between edges; it is
correctly rejected on the **analog** rows (op-amp/OTA: no quiescent set, and
frozen state corrupts slow analog nodes) and is N/A on the diode/BJT rows
(MOSFET1-only). `XYCE_FROZEN_JAC` is excluded — it segfaults when stacked and
adds no speed.

**Reading it.** bfit swaps device stages for smooth macromodels and coarsens the
transient, so the solver strides — every accelerated row beats both commercial
tools. The cleanest win is the **op-amp** (merged diff-pair + current-mirror
legs). The **`--accuracy` knob** trades speed for fidelity (compare each `bal`
vs `fast` cell); the fast multi-tone amps lose more to undersampling. The
**breaker** is the other half: at 3000 stiff stages QSPICE, LTspice and ngspice
all abort — only Xyce solves it, and MPI then nearly halves that.

_Models: `gen_models.py` (+ `gen_amp.py` for the breaker). Open engines:
`model_bench.sh` → `open.csv`. Commercial: `win_models.sh` → `commercial.csv`.
Table: `assemble.py`. Accuracy: `accuracy.py`. Speed/accuracy knob:
`bfit front --accuracy {exact,balanced,fast}` (or raw `--points/--reltol/--abstol`)._

## Cascade-depth stress runs

The N-stage cascade sweep (`run_bench.sh`) is a separate lane and writes
**date-named snapshots** next to this page — `cascade-YYYY-MM-DD.md` — so
each run is preserved rather than overwriting this table:""")

import glob
for f in sorted(glob.glob(os.path.join(HERE, 'cascade-*.md')), reverse=True):
    b = os.path.basename(f)
    print("- [%s](%s)" % (b.replace('cascade-', '').replace('.md', ''), b))
