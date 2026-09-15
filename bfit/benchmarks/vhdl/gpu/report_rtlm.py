#!/usr/bin/env python3
# Table for the RTLMeter-core farms: design size, CPU compiled-model rate, local
# Verilator rate (same stimulus, same VM), RTLMeter CI Verilator kHz, GPU plateau
# and ratios.  Inputs: results/rtlm_designs.tsv (name cells regs cpu_cyc_s vl_cyc_s rtlmeter_khz)
# and the sweep logs results/vast_*rtlm*.log.
import re, sys, glob, collections
meta = {}
for line in open('results/rtlm_designs.tsv'):
    if line.startswith('#') or not line.strip(): continue
    n, cells, regs, cpu, vl, ci = line.split()[:6]
    meta[n] = (int(cells), int(regs), float(cpu), float(vl), float(ci))
pts = collections.defaultdict(dict); cert = {}; dph = {}
for f in sorted(glob.glob(sys.argv[1] if len(sys.argv) > 1 else 'results/vast_*rtlm*.log')):
    gpu = None; cur = None
    for line in open(f, errors='replace'):
        m = re.match(r'== (\S+)', line)
        if m: cur = m.group(1)
        m = re.search(r'DPH=([\d.]+) GPU=(\S+)', line)
        if m: gpu = m.group(2); dph[gpu] = float(m.group(1))
        m = re.match(r'FARM design=(\w+) dev="(?:1x )?([^"]+)" N=(\d+) cycles=(\d+) block=\d+ secs=[\d.]+ agg_inst_cyc_per_s=([\d.e+]+)', line)
        if m: pts[(gpu, cur or m.group(1))][int(m.group(3))] = max(pts[(gpu, cur or m.group(1))].get(int(m.group(3)), 0), float(m.group(5)))
        m = re.match(r'CERT (\w+) (MATCH|MISMATCH)', line)
        if m: cert[(gpu, m.group(1))] = m.group(2)
print('| GPU | design | cells | regs | cert | GPU plateau inst-cyc/s | at N | cell-evals/s | CPU model 1 core | Verilator 1T (this VM) | ×Verilator (this VM) | RTLMeter CI kHz | ×RTLMeter CI |')
print('| :-- | :-- | --: | --: | :-- | --: | --: | --: | --: | --: | --: | --: | --: |')
for (g, d), byN in sorted(pts.items()):
    cells, regs, cpu, vl, ci = meta.get(d, meta.get(d.replace('_soa', ''), (0, 0, float('nan'), float('nan'), float('nan'))))
    plat = max(byN.values()); nb = max(byN, key=byN.get)
    print(f'| {g} | {d} | {cells:,} | {regs:,} | {cert.get((g,d),"?")} | {plat:.2e} | {nb:,} | {plat*cells:.1e} | {cpu:.2e} | {vl:.2e} | ×{plat/vl:,.0f} | {ci:.1f} | ×{plat/(ci*1e3):,.0f} |')
