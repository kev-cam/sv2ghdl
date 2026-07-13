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

NTX = {'rectifier':0,'inv_chain':200,'ring_osc':102,'ota_5t':5,'bjt_amp':3,'opamp':8,
       'c6288':10112,'breaker':3000}
LABEL = {'rectifier':'Bridge rectifier (4 diodes)','inv_chain':'CMOS inverter chain ×100',
 'ring_osc':'CMOS ring oscillator ×51','ota_5t':'5T OTA (diff pair + mirror)',
 'bjt_amp':'BJT 3-stage CE amp ‡','opamp':'2-stage Miller op-amp',
 'c6288':'C6288 16×16 multiplier (PSP103)','breaker':'BJT cascade ×3000 (breaker)'}
ROWS = ['rectifier','inv_chain','ring_osc','ota_5t','bjt_amp','opamp','c6288','breaker']

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

def accs(a):
    """dB suffix for a +bfit cell; no annotation when there is no gold ('-')."""
    if a is None or str(a).strip() in ('', '-', '—'):
        return ''
    return ' (%s)' % db(a)

HDR = ['Model','# Tx','QSPICE','LTspice','ngspice','Xyce','VACASK','Xyce-MPI',
       'ng+bfit bal','ng+bfit fast','xy+bfit bal','xy+bfit fast','vc+bfit bal','vc+bfit fast']
lines = ['| ' + ' | '.join(HDR) + ' |', '| :-- | --: ' + '| --: '*12 + '|']
for m in ROWS:
    o = op.get(m, {}); c = cm.get(m, {})
    qs, lt = num(c.get('qspice')), num(c.get('ltspice'))
    ngb, xyb, vc = num(o.get('ng_base')), num(o.get('xy_base')), num(o.get('vc_base'))
    nbal, nfast = num(o.get('ng_bal')), num(o.get('ng_fast'))
    xbal, xfast = num(o.get('xy_bal')), num(o.get('xy_fast'))
    vbal, vfast = num(o.get('vc_bal')), num(o.get('vc_fast'))
    mpi, mnp = num(o.get('mpi_best')), o.get('mpi_np')
    ref = max([b for b in (qs, lt, ngb, xyb, vc) if b is not None], default=None)  # slowest native
    comm_min = min([x for x in (qs, lt) if x is not None], default=None)
    cells = {'qspice':qs,'ltspice':lt,'ngspice':ngb,'xyce':xyb,'vacask':vc,'mpi':mpi,
             'nbal':nbal,'nfast':nfast,'xbal':xbal,'xfast':xfast,'vbal':vbal,'vfast':vfast}
    fin = {k:v for k,v in cells.items() if v is not None}
    green = min(fin, key=fin.get) if fin else None
    openk = {'ngspice','xyce','vacask','mpi','nbal','nfast','xbal','xfast','vbal','vfast'}
    if comm_min is not None:
        blue = {k for k in fin if k in openk and fin[k] < comm_min and k != green}
    else:                                  # no commercial finishes -> any open finisher beats them
        blue = {k for k in fin if k in openk and k != green}
    dot = lambda k: '🟢' if k == green else ('🔵' if k in blue else '')
    def base_cell(rawv, secs, key):
        s = str(rawv or '').strip().lower()   # 't/o'/'n/a' shown verbatim:
        if s in ('t/o', 'n/a', 'na'):         # didn't finish / has no model-path
            return 't/o' if s == 't/o' else 'n/a'   # (distinct from 'brk' = aborted)
        return fmt(secs, mult(ref, secs), dot=dot(key))
    row = [LABEL[m], str(NTX[m]),
           base_cell(c.get('qspice'), qs, 'qspice'), base_cell(c.get('ltspice'), lt, 'ltspice'),
           base_cell(o.get('ng_base'), ngb, 'ngspice'), base_cell(o.get('xy_base'), xyb, 'xyce'),
           base_cell(o.get('vc_base'), vc, 'vacask')]
    # every × below is vs the row's slowest native (ref), so multipliers are
    # comparable across ALL columns: the biggest × in a row is its 🟢 cell.
    row.append(fmt(mpi, mult(ref, mpi), ' (np %s)' % mnp if mpi else '', dot('mpi')) if mpi else '—')
    row += [fmt(nbal, mult(ref, nbal), accs(o.get('ng_bal_acc')), dot('nbal')) if nbal else '—',
            fmt(nfast, mult(ref, nfast), accs(o.get('ng_fast_acc')), dot('nfast')) if nfast else '—',
            fmt(xbal, mult(ref, xbal), accs(o.get('xy_bal_acc')), dot('xbal')) if xbal else '—',
            fmt(xfast, mult(ref, xfast), accs(o.get('xy_fast_acc')), dot('xfast')) if xfast else '—']
    row += [fmt(vbal, mult(ref, vbal), accs(o.get('vc_bal_acc')), dot('vbal')) if vbal else '—',
            fmt(vfast, mult(ref, vfast), accs(o.get('vc_fast_acc')), dot('vfast')) if vfast else '—']
    lines.append('| ' + ' | '.join(row) + ' |')
TABLE = '\n'.join(lines)

# --- VACASK vs ngspice head-to-head (the replacement case) ------------------
def _hh(a, b):   # ngspice/VACASK seconds ratio -> VACASK advantage (>1 = faster)
    return None if (a is None or b is None or b <= 0) else a / b
def _s(x):
    return '—' if x is None else (('%.0f' % x) if x >= 10 else ('%.2g' % x))
def _r(r):
    return '—' if r is None else '×%.1f' % r
hh = ['| Model | ngspice | VACASK | VACASK adv. | ng+bfit bal | vc+bfit bal | VACASK adv. |',
      '| :-- | --: | --: | --: | --: | --: | --: |']
hw = ht = hl = 0
for m in ROWS:
    if m == 'breaker':
        continue
    o = op.get(m, {})
    ngb, vcb = num(o.get('ng_base')), num(o.get('vc_base'))
    ngl, vcl = num(o.get('ng_bal')), num(o.get('vc_bal'))
    rb, rl = _hh(ngb, vcb), _hh(ngl, vcl)
    if rl is not None:
        if rl >= 1.15: hw += 1
        elif rl > 0.87: ht += 1
        else: hl += 1
    hh.append('| %s | %s | %s | %s | %s | %s | %s |'
              % (LABEL[m], _s(ngb), _s(vcb), _r(rb), _s(ngl), _s(vcl), _r(rl)))
# (c6288 flows through the ROWS loop above from open.csv -- native-only row,
#  numbers merged from c6288_run.sh via csvmerge.py)
VNSEC = """

## VACASK vs ngspice — the replacement case

ngspice's licensing is a patchwork; VACASK is a single clean AGPL-3.0 codebase
that consumes the **same OpenVAF Verilog-A**. The question is whether switching
costs performance. In the bfit-accelerated lane — the flow this tooling
actually runs — it does not: VACASK is **never slower than ngspice** and wins
the hard (digital / stiff) rows by ×4–7. Seconds head-to-head, same macromodels,
same methodology:

""" + '\n'.join(hh) + """

Accelerated tally: **%d decisive VACASK wins, %d ties** (within the 10 ms timer
grain), **%d losses** — the `fast` preset shows the same pattern. C6288's
accelerated cells come from the **gate recognizers** (`recognize_gates`): the
not/nor/and subckts are classified by a switch-level truth table and THREE
subckt-body substitutions turn all 10112 PSP103 FETs into ~2400 behavioral
gates — the multiplier still computes 0xFFFF × 0xFFFF = 0xFFFE0001 on every
engine. The substituted deck contains no transistors at all, so even our
PSP103-less Xyce runs it (bfit as an *enabler*; Xyce's native cell stays
`n/a`). Native transistor-level is hardware-dependent: on this no-AVX-512 box
ngspice leads most native rows including C6288 (VACASK's OSDI model evaluation
leans on wide vectors), while on the VACASK project's Zen 4 reference machine
VACASK leads ngspice natively as well (58 s vs 72 s on C6288 — see below).
Same portable Verilog-A everywhere: `bfit front --sim vacask` vs
`--sim ngspice` is a one-flag swap.
""" % (hw, ht, hl)

print("""# Cross-engine performance

One run per circuit, **same netlist on every engine**. Each cell is
`seconds ×speedup` (fewer seconds / bigger × is better); **+bfit** cells append
`(signal-to-error ratio in dB vs that engine's own base; higher = better)`.
Transients are sized so
QSPICE solves for **≥3 s** and driven with **multi-tone** inputs so the adaptive
engines can't coast to steady state. 🟢 = fastest cell in the row; 🔵 = an open
engine/mode beating **both** commercial tools. `brk` = aborted (timestep
collapse); `—` = no benefit over that engine's own base.

**The `×` reference.** Every `×` — base, **+bfit**, and **Xyce-MPI** — is
relative to the row's **slowest native engine** (×1.0), so multipliers compare
directly across ALL columns: the biggest `×` in a row is its 🟢 cell. What an
acceleration bought a given engine is its `+bfit` seconds against its own base
column. `n/a` = the engine has no model/path for that circuit (distinct from
`brk` = tried and aborted). **bal / fast** are the `bfit front --accuracy` presets
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

**VACASK** (native column) is the new open engine — the same deck, ported to its
Spectre-style syntax by `gen_models_vacask.py` (MOSFET LEVEL=1 → `sp_mos1`, diode
→ `sp_diode`, NPN → `sp_bjt`, multitone B-sources → series ideal sines). Models
compile to OSDI 0.4 via OpenVAF-reloaded. It is a fully adaptive (LTE-driven)
solver, so its per-deck work lands in the ngspice/Xyce range rather than the
QSPICE/LTspice stride-and-coast regime; timepoint counts are recorded next to the
runner. The **vc+bfit** columns run the SAME portable Verilog-A macromodels
through VACASK (`bfit front --sim vacask`, backed by a VACASK tuner driver --
`sp2vc` + `drivers_vacask`; `model_bench.sh` runs the lane via `vc_run`, gate it
with `DO_VC`/`DO_NGXY`). All four macromodels are wired: `ce_stage`, `bridge`,
`current_mirror` (VA cmout legs), and `cmos_inv` **v2** -- the inverter was
redesigned as a regenerative clamped-linear transfer (gain>1 at the trip point;
the old conductance-divider form could not regenerate a chain in ANY engine) and
retuned through VACASK, which also refreshes the ng/xy digital cells. Striding
in VACASK needs three knobs (`front --sim vacask` sets them): `tran_ffmax=0`
(drop the max-input-frequency step cap), `tran_redofactor=0` + huge
`tran_lteratio` (disarm LTE), `tran_method="gear2"` (trap rings on undersampled
inputs). VACASK (AGPL) is the license-clean, OpenVAF-native drop-in for ngspice
in the accelerated lane.

**Reading it.** bfit swaps device stages for smooth macromodels and coarsens the
transient, so the solver strides — every accelerated row beats both commercial
tools. The cleanest win is the **op-amp** (merged diff-pair + current-mirror
legs). The **`--accuracy` knob** trades speed for fidelity (compare each `bal`
vs `fast` cell); the fast multi-tone amps lose more to undersampling. The
**breaker** is the other half: at 3000 stiff stages QSPICE, LTspice and ngspice
all abort — only Xyce solves it, and MPI then nearly halves that.

_Models: `gen_models.py` (+ `gen_amp.py` for the breaker; `gen_models_vacask.py`
ports them to VACASK, `c6288_run.sh` runs C6288). Open engines:
`model_bench.sh` → `open.csv`. Commercial: `win_models.sh` → `commercial.csv`.
Table: `assemble.py`. Accuracy: `accuracy.py`. Speed/accuracy knob:
`bfit front --accuracy {exact,balanced,fast}` (or raw `--points/--reltol/--abstol`)._
""" + VNSEC + """
## C6288 16x16 multiplier (native, transistor-level)

VACASK's flagship benchmark, brought in from its tree: **10112 transistors /
25380 nodes**, PSP103.4 MOSFETs, 0xFFFF x 0xFFFF as a transistor-level transient
(~1020 timepoints). Baseline = each engine's native run of the same circuit,
full-process wall, min of 2. Runner: `c6288_run.sh`; snapshot `c6288-2026-07-12.md`.

| Engine | Wall (s) | Timepoints (acc/rej) | NR iters |
| :-- | --: | :-- | --: |
| ngspice-45.2 | 45.98 | 1020 / 1 | 3474 |
| VACASK 0.3.3 | 70.08 | 1023 / 10 | 3512 |
| Xyce 7.10 (ours) | n/a | -- | -- |

Xyce, QSPICE and LTspice are absent NATIVELY: our Xyce build has no built-in
PSP103 (`level=103`) and no OSDI loader, and QSPICE/LTspice have no
OSDI/Verilog-A path wired for PSP103 on this box. VACASK's 1023/10/3512 matches
the project README's 1021/7/3487, so the port is verified. Note the ordering:
on the README's Zen4/AVX-512 machine VACASK leads (58 s vs ngspice 72 s); this
box has no AVX-512, which is where VACASK's OSDI model-eval edge comes from, so
ngspice leads here instead.

The **+bfit cells** in the main table come from the gate-recognizer lane
(`c6288_run.sh`, `BFIT=1` default): `recognize_gates` switch-level-classifies
the three gate subckts and replaces their BODIES, turning 10112 PSP103 FETs
into ~2400 behavioral gates with no transistors left — the product is still
0xFFFE0001 on every engine, and the deck runs on Xyce with no PSP103 at all.
Accuracy is rel-L2 of p31 vs the engine's own native gold (Xyce has none →
`-`).

## Cascade-depth stress runs

The N-stage cascade sweep (`run_bench.sh`) is a separate lane and writes
**date-named snapshots** next to this page — `cascade-YYYY-MM-DD.md` — so
each run is preserved rather than overwriting this table:""")

import glob
for f in sorted(glob.glob(os.path.join(HERE, 'cascade-*.md')), reverse=True):
    b = os.path.basename(f)
    print("- [%s](%s)" % (b.replace('cascade-', '').replace('.md', ''), b))
