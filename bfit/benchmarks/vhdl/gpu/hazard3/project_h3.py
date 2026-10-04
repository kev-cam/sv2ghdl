#!/usr/bin/env python3
# project_h3.py - PROJECTION (not a measurement) of the farm on rented cards,
# from gpu_farm.md's rules 1-3 and this kit's measured CPU-side numbers.
# bfit GPU-farm kit (PolyForm Noncommercial 1.0.0).
#   project_h3.py [--tiles split_m0.txt[,m2,m4]] [--cells 1253] [--no-anchor] [--anchor-* ...]
# Two projections, both printed:
#  A. rules 1-3 alone (below): gpu_farm.md's ITC cell-eval constant per card.
#  B. MEASUREMENT-ANCHORED (anchored(), added in review): the same kernel measured on a
#     local GPU (local_gpu_h3.sh; defaults = this box's T1000) scaled to the rented cards.
#     B is the one to quote; A over-estimates Hazard3 by ~2.5x (see anchored()).
# Rule 1: aggregate instance-cycles/s = K_card / comb cells (no spill);
#         spill-class designs (rule 2) sit at the b17 cell-eval rate instead.
# Rule 3: saturation N = SMs x resident threads/SM; at 255 registers a SM
#         holds 65536/(255->256 x 32) = 8 warps = 256 threads.
# Split render (A): per-tile cycle counts (gsm CPU farm = Verilator, every tile)
# -> blocks of 32 or 128 threads (a block holds its slot until its slowest thread
# ends) -> greedy list schedule on N_sat / block slots, per-thread rate
# r = plateau / N_sat (constant below saturation, rule 3; measured on the anchor
# this is 1.7x too pessimistic for tail-heavy launches: lone warps run ~3x faster).
# Multi-GPU: contiguous gid slices per card (h3farm.cu), wall = slowest card.
import argparse, collections, heapq, os, sys

# card: SMs, K low/central/high (rule 1 band, gpu_farm.md), K spill-class (b17), $/h paid (gpu_farm.md)
CARDS = collections.OrderedDict([
    ('RTX 4090', dict(sm=128, k=(4.0e12, 4.5e12, 5.6e12), kspill=4.67e8 * 2517, dph=0.36)),
    ('RTX 3090', dict(sm=82,  k=(1.6e12, 2.0e12, 2.5e12), kspill=1.13e8 * 2517, dph=0.11)),
    ('H100 SXM', dict(sm=132, k=(2.8e12, 3.5e12, 4.3e12), kspill=2.68e8 * 2517, dph=2.94)),
    # L40S: not in rule 1's table.  Central = the 4090's central K x 142/128 SMs (same 2.52 GHz
    # boost); low = the 4090's low K; high = the 4090's high K x 1.27 (Servant, register-resident,
    # ran 1.27x the 4090 on the L40S); spill-class = the 4090's x 0.77 (VeeR-EH1 ran 0.77x).
    ('L40S',     dict(sm=142, k=(4.0e12, 4.5e12 * 142 / 128, 5.6e12 * 1.27), kspill=4.67e8 * 2517 * 0.77, dph=0.80)),
])


LAUNCH, BOOT = 0.6, 240.0        # s per binary invocation (process + CUDA context); s of boot/ship/teardown per rental


def load_tiles(path):
    d = []
    for ln in open(path):
        p = ln.split()
        d.append(int(p[2]))
    return d


def makespan(done, slots, r, block=128):
    """greedy list schedule of blocks (in gid order) on `slots` block slots; block time = max done / r"""
    t = [0.0] * slots
    heapq.heapify(t)
    end = 0.0
    for i in range(0, len(done), block):
        dur = max(done[i:i + block]) / r
        s = heapq.heappop(t) + dur
        end = max(end, s)
        heapq.heappush(t, s)
    return end


def thr_run_secs(N, nsat, r, cyc):
    """N identical instances: rule 3 -> waves of N_sat at the latency-bound per-thread rate r"""
    return -(-N // nsat) * cyc / r


def sweep_estimate(a, tables):
    """wall time of sweep_h3.sh per card (its own run list), and the rental cost at gpu_farm.md's paid $/h"""
    MINBS, VAR_NS = 3, (65536, 262144)
    NLIST = (1, 32, 4096, 16384, 65536, 262144, 1048576)
    print('Sweep estimate (sweep_h3.sh run list; launch overhead %.1f s/run; +%.0f s boot/ship/teardown per rental):' % (LAUNCH, BOOT))
    print('| card | GPUs | sweep min central / spill-class | session min | $/h paid (gpu_farm.md) | $ central / spill-class |')
    print('| :-- | --: | --: | --: | --: | --: |')
    rows = []
    for c, p in CARDS.items():
        for ng, dph in ((1, p['dph']), (8, p['dph'] * 8 * (1.88 / 4 / 0.36 if c == 'RTX 4090' else 1.0))):
            if ng == 8 and c != 'RTX 4090':
                continue
            nsat = p['sm'] * 256; out = []
            for k in (p['k'][1], p['kspill']):
                P = k / a.cells; r = P / nsat; t = 0.0; runs = 0
                for N in [4096] * MINBS + list(VAR_NS) * MINBS + list(NLIST):
                    t += thr_run_secs(N, nsat, r, a.thr_cycles); runs += 1
                if ng > 1:
                    for N in (1048576, ng * 262144, ng * 1048576):
                        t += thr_run_secs(N // ng, nsat, r, a.thr_cycles); runs += 1
                for done in tables:
                    layouts = [[done[len(done) * g // ng: len(done) * (g + 1) // ng] for g in range(ng)]]
                    if ng > 1:
                        layouts.append([done[g::ng] for g in range(ng)])
                    for parts in layouts:
                        for blk in (32, 128):
                            t += max(makespan(part, nsat // blk, r, blk) for part in parts); runs += 1
                out.append(t + runs * LAUNCH)
            sess = (out[1] + BOOT) / 60
            rows.append((c, ng, dph, out, sess))
            print('| %s | %d | %.1f / %.1f | %.1f (spill-class) | $%.2f | $%.3f / $%.3f |' % (
                c, ng, out[0] / 60, out[1] / 60, sess, dph, (out[0] + BOOT) / 3600 * dph, (out[1] + BOOT) / 3600 * dph))
    tot_c = sum((r[3][0] + BOOT) / 3600 * r[2] for r in rows); tot_s = sum((r[3][1] + BOOT) / 3600 * r[2] for r in rows)
    print('\nAll %d rentals: $%.2f central, $%.2f spill-class (at the paid $/h; a failed boot costs a few cents)' % (len(rows), tot_c, tot_s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tiles', default='')
    ap.add_argument('--cells', type=int, default=1253)
    ap.add_argument('--thr-cycles', type=int, default=95038)
    ap.add_argument('--no-anchor', action='store_true')
    ap.add_argument('--anchor-gpu', default='NVIDIA T1000 (local, WSL2, local_gpu_h3.sh 2026-10-03)')
    ap.add_argument('--anchor-sm', type=int, default=14)
    ap.add_argument('--anchor-ghz', type=float, default=1.86)          # nvidia-smi clocks.sm during the runs
    ap.add_argument('--anchor-plateau', type=float, default=1.04e8)    # thr, 5,000 cycles, N = 3,584..14,336: 1.02-1.06e8 in 3 runs
    ap.add_argument('--anchor-lone', type=float, default=1.0e5)        # one warp alone (MINB 1, N = 32)
    ap.add_argument('--anchor-split-secs', default='59.19,60.00,60.69')  # whole image, one launch, m = 0,2,4, block 32
    a = ap.parse_args()
    print('PROJECTION from gpu_farm.md rules 1-3; cells=%d, 255 registers / 0 spill (ptxas) -> 256 threads/SM\n' % a.cells)
    print('| card | N_sat (rule 3) | thr plateau inst-cyc/s low / central / high (rule 1) | spill-class (rule 2) | per-instance cyc/s (central) | thr images/s (central) | $ per 1e12 inst-cyc |')
    print('| :-- | --: | --: | --: | --: | --: | --: |')
    for c, p in CARDS.items():
        nsat = p['sm'] * 256
        lo, ce, hi = (k / a.cells for k in p['k'])
        sp = p['kspill'] / a.cells
        print('| %s | %d | %.2e / %.2e / %.2e | %.2e | %.2e | %.2e | $%.3f |' % (
            c, nsat, lo, ce, hi, sp, ce / nsat, ce / a.thr_cycles, p['dph'] / 3600 / ce * 1e12))
    tables = []
    if not a.tiles:
        if not a.no_anchor:
            anchored(a, tables)
        return
    print()
    for path in a.tiles.split(','):
        done = load_tiles(path)
        tables.append(done)
        tot, mx = sum(done), max(done)
        w = sum(max(done[i:i + 32]) * len(done[i:i + 32]) for i in range(0, len(done), 32))
        b = sum(max(done[i:i + 128]) * len(done[i:i + 128]) for i in range(0, len(done), 128))
        print('%s: N=%d tiles, sum %.4e inst-cycles, mean %.0f, max %d; SIMT efficiency warp(32) %.3f, block(128) %.3f' % (
            os.path.basename(path), len(done), tot, tot / len(done), mx, tot / w, tot / b))
        print('| card | GPUs | layout | block | wall s low / central / high (rule 1) | spill-class | slowest card share |')
        print('| :-- | --: | :-- | --: | --: | --: | --: |')
        for c, p in CARDS.items():
            nsat = p['sm'] * 256
            for ng, lay in ((1, '-'), (8, 'contiguous'), (8, 'interleaved')):
                parts = [done[len(done) * g // ng: len(done) * (g + 1) // ng] for g in range(ng)] if lay != 'interleaved' \
                    else [done[g::ng] for g in range(ng)]
                for blk in (32, 128):
                    slots = nsat // blk; res = []
                    for k in list(p['k']) + [p['kspill']]:
                        P = k / a.cells; r = P / nsat
                        res.append(max(makespan(part, slots, r, blk) for part in parts))
                    print('| %s | %d | %s | %d | %.2f / %.2f / %.2f | %.2f | %.2f of total |' % (
                        c, ng, lay, blk, res[0], res[1], res[2], res[3], max(sum(x) for x in parts) / tot))
        print()
    sweep_estimate(a, tables)
    if not a.no_anchor:
        anchored(a, tables)


# ---- measurement-anchored projection (added in review, 2026-10-03) ---------------------------
# The rule-1 numbers above assume Hazard3 evaluates cells as fast as the ITC FSMs.  It does not:
# the SAME h3farm.cu + models, built for sm_75 (SASS instruction count and mix equal to the
# shipped sm_80..sm_90 SASS within 0.5%), measured on this box's T1000 (local_gpu_h3.sh) runs
# 1.33e11 cell-evals/s = 0.32-0.53 of the T1000's ITC rule-1 rate.  So the cards are projected
# from that measurement instead:
#   4090 = anchor x (128 SMs x 2.75 GHz) / (anchor SMs x anchor GHz) x 0.90 / 1.00 / 1.20 per SM
#          per clock (Ada vs Turing: same 4 x 16 INT32 lanes and issue width per SM, larger L1/L2;
#          the ITC rule-1 designs give 1.00-1.15, an upper-ish estimate because their T1000 column
#          was taken at N = 4,096, possibly below that card's plateau)
#   3090 / H100 SXM = 4090 x their measured ITC plateau ratios (0.43-0.44 / 0.78-0.81);
#   L40S = 4090 x 1.00 .. 1.11 (SM count) .. 1.27 (Servant)
# Split render: processor sharing, validated on the anchor's 293 measured one-wave launches
# (predicted 61-68 s vs measured 64.9 s; the constant-rate list schedule above predicts 112 s):
# every resident warp advances at min(r_lone, W / active warps), W = plateau / 32 warp-cycles/s;
# a block keeps its slot until its slowest warp ends.  Whole-image launches run 6-11% slower than
# that model at the thr plateau (split-kernel cycles cost a little more, and the anchor's clock sags
# from 1.86 to 1.81 GHz over a minute), so the split times are calibrated on the anchor's certified
# single-launch renders (--anchor-split-secs, results/local_sweep_sm_75.log: m = 0 / 2 / 4 in
# 59.2 / 60.0 / 60.7 s; an earlier run 58.7 / 59.3 / 59.9 s): cal[m] = measured / model.
ANCHOR_RATIO = collections.OrderedDict([      # card plateau / 4090 plateau: low, central, high
    ('RTX 4090', (1.0, 1.0, 1.0)), ('RTX 3090', (0.43, 0.435, 0.44)),
    ('H100 SXM', (0.78, 0.795, 0.81)), ('L40S', (1.0, 1.11, 1.27))])
VERILATOR_CPS, VERILATOR_IMG, VERIJIT_IMG = (3.5e6, 3.7e6), (1240.0, 1274.0), 4637655132 / 766.8e6
MINB_FACTOR = {1: 1.0, 3: 0.80, 4: 0.66}      # measured on the anchor (split, one wave of in-set tiles)


def ps_makespan(done, slots_warps, W, r_lone, block=32):
    """processor-sharing schedule of blocks (gid order) of block/32 warps; returns seconds"""
    wpb = max(1, block // 32)
    wl = [max(done[i:i + 32]) for i in range(0, len(done), 32)]
    blocks = [wl[i:i + wpb] for i in range(0, len(wl), wpb)]
    slots = max(1, slots_warps // wpb)
    t = phi = 0.0; q = 0; heap = []; left = {}
    def admit(b):
        left[b] = len(blocks[b])
        for w in blocks[b]:
            heapq.heappush(heap, (phi + w, b))
    while q < len(blocks) and len(left) < slots:
        admit(q); q += 1
    while heap:
        v = min(r_lone, W / len(heap))
        f, b = heapq.heappop(heap)
        t += (f - phi) / v; phi = f
        left[b] -= 1
        if not left[b]:
            del left[b]
            if q < len(blocks):
                admit(q); q += 1
    return t


def anchored(a, tables):
    f_lo, f_c, f_hi = 0.90, 1.00, 1.20
    base = a.anchor_plateau * 128 * 2.75 / (a.anchor_sm * a.anchor_ghz)
    p4090 = (base * f_lo, base * f_c, base * f_hi)
    lone4090 = a.anchor_lone * 2.75 / a.anchor_ghz * f_c
    print('\nMEASUREMENT-ANCHORED PROJECTION (anchor: %s, %d SMs at %.2f GHz, thr plateau %.3g inst-cyc/s, '
          'lone warp %.3g cyc/s; still a projection for the rented cards)' % (a.anchor_gpu, a.anchor_sm, a.anchor_ghz,
          a.anchor_plateau, a.anchor_lone))
    print('| card | thr plateau inst-cyc/s low / central / high | x one Verilator thread (central) | rule-1 central above | thr images/s (central) |')
    print('| :-- | --: | --: | --: | --: |')
    cards = collections.OrderedDict()
    for c, rr in ANCHOR_RATIO.items():
        P = tuple(p * r for p, r in zip(p4090, rr))
        sm = CARDS[c]['sm']
        lone = lone4090 * (P[1] / sm) / (p4090[1] / 128)
        cards[c] = (P, lone, sm)
        print('| %s | %.2e / %.2e / %.2e | %.0f-%.0f | %.2e (%.2fx) | %.0f |' % (c, P[0], P[1], P[2], P[1] / VERILATOR_CPS[1],
              P[1] / VERILATOR_CPS[0], CARDS[c]['k'][1] / a.cells, P[1] / (CARDS[c]['k'][1] / a.cells), P[1] / a.thr_cycles))
    if not tables:
        return
    meas = [float(x) for x in a.anchor_split_secs.split(',')]
    cal = {}
    for m, done, t in zip((0, 2, 4), tables, meas):
        model = ps_makespan(done, a.anchor_sm * 8, a.anchor_plateau / 32, a.anchor_lone, 32)
        cal[m] = t / model
        print('calibration m=%d: anchor one-launch image %.2f s measured, %.2f s modelled -> x %.3f' % (m, t, model, cal[m]))
    print('\nsplit render, whole 1024x1024 image, kernel seconds (processor sharing x calibration; 8 GPUs = H3_GPU_INTERLEAVE=1)')
    print('| card | GPUs | m | block | s at the high / central / low plateau | x Verilator 1,240-1,274 s (central) | vs Verijit claim 6.05 s (central) |')
    print('| :-- | --: | --: | --: | --: | --: | --: |')
    for c, (P, lone, sm) in cards.items():
        for m, done in zip((0, 2, 4), tables):
            for ng in (1, 8):
                for blk in (32, 128):
                    res = [cal[m] * max(ps_makespan(done[g::ng], sm * 8, Pk / 32, lone * Pk / P[1], blk) for g in range(ng)) for Pk in P]
                    print('| %s | %d | %d | %d | %.2f / %.2f / %.2f | %.0f-%.0f | %.2fx |' % (c, ng, m, blk, res[2], res[1], res[0],
                          VERILATOR_IMG[0] / res[1], VERILATOR_IMG[1] / res[1], VERIJIT_IMG / res[1]))
    # sweep_h3.sh run list -> session minutes and dollars per planned rental
    print('\nsweep_h3.sh wall time and rental cost (central plateau; MINB 3/4 at %.2f/%.2f of MINB 1 as on the anchor; '
          '%.1f s per invocation; +%.0f s create->sweep start and destroy, measured 1.2-3.8 min on the 2026-09 rentals)'
          % (MINB_FACTOR[3], MINB_FACTOR[4], LAUNCH, BOOT))
    print('| node | sweep min | session min | $/h paid (gpu_farm.md) | $ expected |')
    print('| :-- | --: | --: | --: | --: |')
    tot = 0.0
    for c, ng, dph in (('RTX 4090', 1, 0.36), ('RTX 3090', 1, 0.11), ('L40S', 1, 0.80), ('H100 SXM', 1, 2.94), ('RTX 4090', 8, 3.76)):
        P, lone, sm = cards[c]
        W = P[1] / 32; slots = sm * 8
        def thr(n, minb=1, ngpu=1):
            per = -(-n // ngpu); warps_ = -(-per // 32)
            v = min(lone, W / min(warps_, slots)) * MINB_FACTOR[minb]
            return -(-warps_ // slots) * a.thr_cycles / v
        t = sum(thr(4096, mb) for mb in (1, 3, 4)) + sum(thr(n, mb) for mb in (1, 3, 4) for n in (65536, 262144))
        t += sum(thr(n) for n in (1, 32, 4096, 16384, 65536, 262144, 1048576))
        runs = 3 + 6 + 7
        if ng > 1:
            t += sum(thr(n, 1, ng) for n in (1048576, ng * 262144, ng * 1048576)); runs += 3
        for m, done in zip((0, 2, 4), tables):
            for lay in ((1, 0) if ng > 1 else (0,)):
                parts = [done[g::ng] for g in range(ng)] if lay else [done[len(done) * g // ng: len(done) * (g + 1) // ng] for g in range(ng)]
                for blk in (32, 128):
                    t += cal[m] * max(ps_makespan(p, slots, W, lone, blk) for p in parts); runs += 1
        t += runs * LAUNCH
        sess = (t + BOOT) / 60; usd = sess / 60 * dph; tot += usd
        print('| %s x%d | %.1f | %.1f | $%.2f | $%.2f |' % (c, ng, t / 60, sess, dph, usd))
    print('| all five | | | | $%.2f |' % tot)


if __name__ == '__main__':
    main()
