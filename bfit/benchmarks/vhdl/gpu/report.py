#!/usr/bin/env python3
# Parse sweep logs (results/vast_*.log) -> markdown tables.
# For each GPU x design: certification, plateau agg inst-cyc/s (best over N/block),
# N at plateau, per-instance cyc/s at N=4096, ratio vs fastest single CPU engine
# (vhdl_perf.md row), ratio vs the T1000 column, $ per 1e12 instance-cycles.
import re, sys, glob, collections
FASTEST = {'b01':1.790,'b06':1.658,'b12':2.905,'b14':0.774,'b17':1.882,'b22':1.451}   # s, fastest single engine (vhdl_perf.md)
CYC     = {'b01':3e6,'b06':2e6,'b12':3e6,'b14':1e6,'b17':1e6,'b22':1e6}
T1000   = {'b01':8.85e9,'b06':7.74e9,'b12':5.33e8,'b14':1.24e9,'b17':1.81e7,'b22':2.10e8}
rows = collections.defaultdict(list); cert = {}; meta = {}
for f in sorted(glob.glob(sys.argv[1] if len(sys.argv) > 1 else 'results/vast_*.log')):
    gpu = dph = None
    for line in open(f, errors='replace'):
        m = re.search(r'DPH=([\d.]+) GPU=(\S+)', line)
        if m: dph, gpu = float(m.group(1)), m.group(2)
        m = re.match(r'FARM design=(\w+) dev="([^"]+)" N=(\d+) cycles=(\d+) block=(\d+) secs=([\d.]+) agg_inst_cyc_per_s=([\d.e+]+) per_inst_cyc_per_s=([\d.e+]+) CHK0=(\w+)', line)
        if m:
            d, dev, N, cyc, blk, secs, agg, per, chk = m.groups()
            rows[(gpu or dev, d)].append((int(N), int(blk), int(cyc), float(secs), float(agg), float(per)))
            meta[gpu or dev] = (dev, dph)
        m = re.match(r'CERT (\w+) (MATCH|MISMATCH)', line)
        if m: cert[(gpu, m.group(1))] = m.group(2)
print('| GPU | design | cert | plateau agg inst-cyc/s | at N / block | per-inst cyc/s @4096 | vs fastest CPU engine | vs T1000 | $ per 1e12 inst-cyc |')
print('| :-- | :-- | :-- | --: | :-- | --: | --: | --: | --: |')
for (g, d), pts in sorted(rows.items()):
    dev, dph = meta[g]
    best = max(pts, key=lambda p: p[4])
    p4096 = [p for p in pts if p[0] == 4096]
    per4096 = max(p4096, key=lambda p: p[4])[5] if p4096 else float('nan')
    single = CYC[d] / FASTEST[d]
    cost = (dph / 3600) / best[4] * 1e12 if dph else float('nan')
    print(f'| {g} ({dev}) | {d} | {cert.get((g,d),"?")} | {best[4]:.2e} | {best[0]} / {best[1]} | {per4096:.2e} | ×{best[4]/single:,.0f} | ×{best[4]/T1000[d]:.1f} | ${cost:.3f} |')
print()
print('Per-N detail (best block): agg inst-cyc/s')
for (g, d), pts in sorted(rows.items()):
    byN = {}
    for p in pts: byN[p[0]] = max(byN.get(p[0], 0), p[4])
    print(f'{g} {d}: ' + '  '.join(f'N={N}:{v:.2e}' for N, v in sorted(byN.items())))
