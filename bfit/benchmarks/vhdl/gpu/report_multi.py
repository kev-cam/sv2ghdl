#!/usr/bin/env python3
# Multi-GPU scaling tables from sweep_multi.sh logs: for each node x design, agg
# inst-cyc/s vs GPU count at each total N, with scaling efficiency vs 1 GPU.
import re, sys, glob, collections
rows = collections.defaultdict(dict); cert = {}; aggs = collections.defaultdict(set)
for f in sorted(glob.glob(sys.argv[1] if len(sys.argv) > 1 else 'results/vast_*x*_*.log')):
    node = None
    for line in open(f, errors='replace'):
        m = re.search(r'DPH=([\d.]+) GPU=(\S+)', line)
        if m: node = m.group(2)
        m = re.match(r'FARM design=(\w+) dev="(\d+)x ([^"]+)" N=(\d+) cycles=(\d+) block=\d+ secs=([\d.]+) agg_inst_cyc_per_s=([\d.e+]+) .*AGG=(\w+)', line)
        if m:
            d, g, name, N, cyc, secs, agg, AGG = m.groups()
            k = (node, d); g, N = int(g), int(N)
            rows[k][(g, N)] = max(rows[k].get((g, N), 0), float(agg))
            aggs[(node, d, N, int(cyc))].add(AGG)
        m = re.match(r'CERT (\w+) (MATCH|MISMATCH)', line)
        if m: cert[(node, m.group(1))] = m.group(2)
for (node, d), pts in sorted(rows.items()):
    gs = sorted({g for g, _ in pts}); Ns = sorted({N for _, N in pts})
    print(f'\n### {node} {d}  cert={cert.get((node, d), "?")}   (agg inst-cyc/s; eff = vs 1 GPU at same N)')
    print('| total N | ' + ' | '.join(f'{g} GPU' for g in gs) + ' |')
    print('| --: | ' + ' | '.join('--:' for _ in gs) + ' |')
    for N in Ns:
        cells = []
        for g in gs:
            v = pts.get((g, N))
            if v is None: cells.append('—'); continue
            base = pts.get((1, N))
            cells.append(f'{v:.2e}' + (f' ({v/base/g*100:.0f}%)' if base and g > 1 else ''))
        print(f'| {N} | ' + ' | '.join(cells) + ' |')
    chk = [(N, c, len(a)) for (n2, d2, N, c), a in aggs.items() if (n2, d2) == (node, d)]
    multi = [(N, c, k) for N, c, k in chk if k > 1]
    print(f'AGG consistent for equal (N, cycles) across GPU counts: {"YES" if not multi else "NO " + str(multi)}')
