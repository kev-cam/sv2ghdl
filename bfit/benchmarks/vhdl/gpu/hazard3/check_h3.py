#!/usr/bin/env python3
# check_h3.py - certification of rented-GPU Hazard3 runs, ON THE BUILD HOST,
# from the logs alone.  bfit GPU-farm kit (PolyForm Noncommercial 1.0.0).
#   check_h3.py --write-expect h3expect.txt     (what vast_h3.sh ships)
#   check_h3.py [results/vast_h3_*.log ...]      (default: all of them)
# Expected values come from this kit's own CPU-side evidence, never from the
# rented host:
#   thr   CHK0 = fold of tests/hazard3_mandelbrot's native golden, DONE0 = its
#         ref_cycles (expect/thr.txt); for N identical instances AGG/CAGG are
#         recomputed here as FNV-1a-64 over N copies of CHK0 / DONE0
#   split AGG = native per-tile goldens (expect/split.txt), CAGG = every tile
#         run on the gsm CPU farm and on Verilator (results/cert_cpu.log),
#         image md5 = upstream's output.ppm 693d2391e979a114a82af00b3e64e54c
# Every FARM line is checked; AGG must also agree across GPU counts.
import collections, glob, os, re, sys

K = os.path.dirname(os.path.abspath(__file__))
OFF, PR, M64 = 0xcbf29ce484222325, 0x100000001b3, (1 << 64) - 1


def fnv_vec(vals):
    h = OFF
    for v in vals:
        for b in range(8):
            h = ((h ^ ((v >> (8 * b)) & 0xff)) * PR) & M64
    return h


def fnv_rep(v, n, _c={}):
    key = (v, n)
    if key not in _c:
        h = OFF
        bs = [(v >> (8 * b)) & 0xff for b in range(8)]
        for _ in range(n):
            for x in bs:
                h = ((h ^ x) * PR) & M64
        _c[key] = h
    return _c[key]


def kv(line):
    return dict(re.findall(r'(\w+)=("[^"]*"|\S+)', line))


def expectations():
    e = {'split': {}}
    for ln in open(os.path.join(K, 'expect', 'thr.txt')):
        if ln.startswith('workload=thr'):
            d = kv(ln); e['thr'] = (d['CHK0'], int(d['DONE0']))
    for ln in open(os.path.join(K, 'expect', 'split.txt')):
        if ln.startswith('workload=split'):
            d = kv(ln); e['split'][int(d['m'])] = {'NT': int(d['NT']), 'AGG': d['AGG'], 'CHK0': d['CHK0'], 'md5': d['md5']}
    cl = os.path.join(K, 'results', 'cert_cpu.log')
    if os.path.exists(cl):
        for ln in open(cl):
            if ln.startswith('EXPECT split'):
                d = kv(ln); m = int(d['m'])
                if m in e['split']:
                    e['split'][m].update(CAGG=d['CAGG'], inst_cyc=float(d['inst_cyc']), done_max=int(d['done_max']))
            m2 = re.match(r'CERT-(CPU|VERILATOR) split m=(\d+) (MATCH|MISMATCH)', ln)
            if m2 and int(m2.group(2)) in e['split']:
                e['split'][int(m2.group(2))]['cpu_' + m2.group(1).lower()] = m2.group(3)
    return e


def write_expect(path):
    e = expectations()
    out = ['thr %s %d' % e['thr']]
    for m, s in sorted(e['split'].items()):
        if s.get('cpu_cpu') != 'MATCH' or s.get('cpu_verilator') != 'MATCH' or 'CAGG' not in s:
            sys.exit('split m=%d is not CPU-certified yet (run cert_h3.sh)' % m)
        out.append('split %d %d %s %s %s' % (m, s['NT'], s['AGG'], s['CAGG'], s['md5']))
    open(path, 'w').write('\n'.join(out) + '\n')
    print('wrote %s:\n%s' % (path, '\n'.join(out)))


def check(logs):
    e = expectations(); tchk, tdone = e['thr']
    rows, aggs, bad = [], collections.defaultdict(set), 0
    for f in logs:
        gpu = dph = None; md5 = {}
        lines = open(f, errors='replace').read().splitlines()
        for ln in lines:
            m = re.search(r'IMAGE-MD5 m=(\d+) (\w+)', ln)
            if m: md5.setdefault(int(m.group(1)), []).append(m.group(2))
        md5_i = collections.Counter()
        if not any(ln.startswith('SWEEP-END') for ln in lines):
            rows.append((os.path.basename(f), '?', 0, '-', '-', '-', 0, 0.0, 0.0, 'MISMATCH incomplete log (no SWEEP-END)', None)); bad += 1
        for ln in lines:
            m = re.search(r'DPH=([\d.]+) GPU=(\S+)', ln)
            if m: dph, gpu = float(m.group(1)), m.group(2)
            if ln.startswith('FARM-ERROR') or (ln.startswith('CERT ') and 'MISMATCH' in ln):
                rows.append((os.path.basename(f), gpu or '?', 0, '-', '-', '-', 0, 0.0, 0.0, 'MISMATCH ' + ln[:120], dph)); bad += 1
                continue
            if not ln.startswith('FARM design=h3'):
                continue
            d = kv(ln); des = d['design']; N = int(d['N']); cyc = int(d['cycles'])
            dev = d['dev'].strip('"'); ng = int(dev.split('x')[0]) if 'x ' in dev else 1
            why = []
            if des == 'h3thr':
                if cyc >= tdone:
                    if d['CHK0'] != tchk: why.append('CHK0')
                    if int(d['DONE0']) != tdone: why.append('DONE0')
                    if int(d['ndone']) != N: why.append('ndone')
                    if d['AGG'] != '%016X' % fnv_rep(int(tchk, 16), N): why.append('AGG')
                    if d['CAGG'] != '%016X' % fnv_rep(tdone, N): why.append('CAGG')
                else:
                    why.append('partial (cycles < %d): throughput only' % tdone)
                key = (des, N, cyc, 0)
            else:
                mm = int(d['m']); s = e['split'][mm]
                if N != s['NT'] or int(d['ndone']) != N: why.append('N/ndone')
                if d['AGG'] != s['AGG']: why.append('AGG')
                if d['CAGG'] != s.get('CAGG'): why.append('CAGG')
                k = md5_i[mm]; md5_i[mm] += 1
                got = md5.get(mm, [None] * (k + 1))[k] if k < len(md5.get(mm, [])) else None
                if got != s['md5']: why.append('image md5 %s' % got)
                key = (des, N, cyc, mm)
            aggs[key].add(d['AGG'])
            ok = not why or why[0].startswith('partial')
            bad += not ok
            rows.append((os.path.basename(f), gpu or dev, ng, des, d.get('m', '0'), d.get('minb', '?'), N, float(d['secs']),
                         float(d['agg_inst_cyc_per_s']), 'MATCH' if not why else ('-' if ok else 'MISMATCH ' + ','.join(why)), dph))
    print('| log | GPU | GPUs | workload | m | MINB | N | kernel secs | agg inst-cyc/s | cert |')
    print('| :-- | :-- | --: | :-- | --: | --: | --: | --: | --: | :-- |')
    for r in rows:
        print('| %s | %s | %d | %s | %s | %s | %d | %.3f | %.3e | %s |' % (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]))
    multi = {k: v for k, v in aggs.items() if len(v) > 1}
    print('\nAGG consistent across GPU counts/cards for equal (workload, N, cycles, m): %s' % ('YES' if not multi else 'NO %s' % multi))
    print('rows checked (FARM lines, errors, incomplete logs): %d, failing certification: %d' % (len(rows), bad))
    return 1 if (bad or multi) else 0


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--write-expect':
        write_expect(sys.argv[2]); sys.exit(0)
    logs = sys.argv[1:] or sorted(glob.glob(os.path.join(K, 'results', 'vast_h3_*.log')))
    if not logs:
        sys.exit('no logs')
    sys.exit(check(logs))
