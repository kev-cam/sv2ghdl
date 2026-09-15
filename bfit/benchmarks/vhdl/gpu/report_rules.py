#!/usr/bin/env python3
# Scaling rules from the single-GPU sweeps: plateau agg inst-cyc/s -> comb-cell
# evaluations/s per card; N at 90% of plateau vs the card's resident-thread
# capacity; ratio to the same compiled model on one CPU core.
import re, glob, collections
CELLS = {'b01':32,'b06':33,'b12':463,'b14':328,'b17':2517,'b22':1000}
REGS  = {'b01':3,'b06':5,'b12':51,'b14':15,'b17':135,'b22':44}
CPU1  = {'b01':3e6/0.1609,'b06':2e6/0.1917,'b12':3e6/2.929,'b14':1e6/0.3353,'b17':1e6/6.0248,'b22':1e6/1.2886}  # this VM, 1 core, same model
SMS   = {'RTX 4090':(128,1536),'RTX 3090':(82,1536),'A100':(108,2048),'H100 80GB HBM3':(132,2048),'H100 PCIe':(114,2048)}
pts = collections.defaultdict(dict)
for f in glob.glob('results/vast_*.log'):
    for line in open(f, errors='replace'):
        m = re.match(r'FARM design=(\w+) dev="(?:1x )?(NVIDIA [^"]+)" N=(\d+) cycles=\d+ block=\d+ secs=[\d.]+ agg_inst_cyc_per_s=([\d.e+]+)', line)
        if m:
            d, dev, N, agg = m.group(1), m.group(2), int(m.group(3)), float(m.group(4))
            pts[(dev, d)][N] = max(pts[(dev, d)].get(N, 0), agg)
def card(dev):
    for k in SMS:
        if k in dev: return k
print('| card | design | cells | regs | plateau inst-cyc/s | cell-evals/s | N at 90% | resident threads | ×1 CPU core |')
print('| :-- | :-- | --: | --: | --: | --: | --: | --: | --: |')
ce = collections.defaultdict(list)
for (dev, d), byN in sorted(pts.items()):
    plat = max(byN.values()); n90 = min(N for N, v in sorted(byN.items()) if v >= 0.9 * plat)
    c = card(dev); cap = SMS[c][0] * SMS[c][1] if c else 0
    ce[c].append(plat * CELLS[d])
    print(f'| {c} | {d} | {CELLS[d]} | {REGS[d]} | {plat:.2e} | {plat*CELLS[d]:.1e} | {n90:,} | {cap:,} | ×{plat/CPU1[d]:,.0f} |')
print()
for c, v in ce.items():
    v = sorted(v); print(f'{c}: cell-evals/s range {v[0]:.1e} .. {v[-1]:.1e}; without b17 (spill-bound) min {sorted(v)[1] if len(v)>1 else v[0]:.1e}')
