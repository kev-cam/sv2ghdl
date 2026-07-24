#!/usr/bin/env python3
"""Demand-driven (pull / backward) vs eager (push / forward) evaluation.

Verilator is PUSH: every cycle it evaluates the whole design, in time order,
because it is compiled that way -- it only works forward. A pull evaluator
computes a signal's value ONLY when it is observed, by recursing backward
through its dependency cone, memoised per (node, time). Two consequences:
  * logic not in the backward cone of any observed output is never evaluated
    (dead / unobserved logic is free),
  * time that is never observed is skipped -- a registered value at cycle t
    pulls its driver from cycle t-1 only as deep as an observation requires.

This models a small synchronous netlist both ways, drives it, observes the
outputs SPARSELY, and counts node-evaluations. The pull result MUST equal the
push result at every observed point (verify-then-trust) -- speed is meaningless
if it is wrong.
"""
import sys
sys.setrecursionlimit(1 << 20)

# ---- netlist model -------------------------------------------------------
# node = (kind, args). kind: 'in' | 'reg' | 'gate'
#   in   : args = index into the stimulus rows
#   reg  : args = (driver_node,)                 value[t] = value(driver, t-1)
#   gate : args = (fn, (pred_nodes...))          value[t] = fn(value(preds, t))
class Net:
    def __init__(self):
        self.kind = []; self.args = []
    def add(self, kind, args):
        self.kind.append(kind); self.args.append(args); return len(self.kind) - 1

def build(depth, dead_regs, live_width):
    """A 'live' pipeline (feeds the observed output) plus a pile of 'dead'
    free-running logic that a forward sim must still evaluate every cycle."""
    n = Net()
    ins = [n.add('in', i) for i in range(live_width)]
    # live: live_width-wide combinational mix, then a `depth`-stage pipeline
    stage = ins
    for _ in range(depth):
        mixed = [n.add('gate', ((lambda a, b: (a ^ b) & 0xFF),
                                 (stage[i], stage[(i + 1) % live_width])))
                 for i in range(live_width)]
        stage = [n.add('reg', (mixed[i],)) for i in range(live_width)]
    out = n.add('gate', ((lambda *v: sum(v) & 0xFF), tuple(stage)))
    # dead: free-running counters/toggles feeding nothing observed
    for d in range(dead_regs):
        g = n.add('gate', ((lambda a: (a + 1) & 0xFF), (d % max(1, live_width) + 1e-9 and ins[d % live_width],)))
        n.add('reg', (g,))
    return n, ins, out

# ---- PUSH: forward, evaluate everything every cycle ----------------------
def run_push(net, ins, out, stim, cycles):
    evals = 0
    N = len(net.kind)
    cur = [0] * N; nxt = [0] * N
    outs = {}
    for t in range(cycles):
        for i in range(len(ins)):
            cur[ins[i]] = stim(t, i)
        # combinational settle (single pass is enough for this acyclic mix)
        for node in range(N):
            k = net.kind[node]
            if k == 'gate':
                fn, preds = net.args[node]
                cur[node] = fn(*[cur[p] for p in preds]); evals += 1
            elif k == 'reg':
                evals += 1  # reg output already in cur from last commit
        for node in range(N):          # commit registers
            if net.kind[node] == 'reg':
                nxt[node] = cur[net.args[node][0]]
        outs[t] = cur[out]
        for node in range(N):
            if net.kind[node] == 'reg':
                cur[node] = nxt[node]
    return outs, evals

# ---- PULL: backward, evaluate only what an observation needs --------------
def run_pull(net, ins, out, stim, observe_times):
    memo = {}; evals = [0]
    def value(node, t):
        if t < 0:
            return 0
        key = (node, t)
        if key in memo:
            return memo[key]
        k = net.kind[node]
        if k == 'in':
            v = stim(t, net.args[node])
        elif k == 'reg':
            v = value(net.args[node][0], t - 1)     # look BACKWARD one cycle
        else:
            fn, preds = net.args[node]
            v = fn(*[value(p, t) for p in preds])
        evals[0] += 1
        memo[key] = v
        return v
    outs = {t: value(out, t) for t in observe_times}
    return outs, evals[0]

def main():
    depth, dead, width, cycles = 20, 4000, 8, 5000
    net, ins, out = build(depth, dead, width)
    stim = lambda t, i: ((t * 2654435761 + i * 40503) >> 3) & 0xFF
    total_nodes = len(net.kind)

    push_outs, push_evals = run_push(net, ins, out, stim, cycles)

    for label, obs in [("observe every cycle", list(range(cycles))),
                       ("observe last 1%", list(range(cycles - cycles // 100, cycles))),
                       ("observe only final", [cycles - 1])]:
        pull_outs, pull_evals = run_pull(net, ins, out, stim, obs)
        ok = all(push_outs[t] == pull_outs[t] for t in obs)
        print(f"{label:22s} correct={ok}  pull_evals={pull_evals:>10,}  "
              f"vs push_evals={push_evals:>12,}  ({push_evals/max(1,pull_evals):.1f}x less work)")

    print(f"\ndesign: {total_nodes} nodes ({dead} dead regs), {cycles} cycles, "
          f"pipeline depth {depth}")
    print("push (Verilator-style) must evaluate every node every cycle.")
    print("pull evaluates only the observed output's backward cone, memoised.")

main()
