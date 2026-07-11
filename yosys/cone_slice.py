#!/usr/bin/env python3
# cone_slice.py — extract the combinational cone of one output from a full-scan
# .bench (json2bench output), as a small standalone .bench.
#
# The whole-unit full-scan bench can be too large for a vintage ATPG tool
# (atalanta 2.0 chokes ~47k gates). But each output/next_<reg> is driven by a
# SMALL cone; certifying every cone independently covers the whole model and is
# the natural fork-farm unit. A cone's support (the PIs/PPIs it reaches) becomes
# its INPUTs; the target is the sole OUTPUT; only cone gates are emitted.
#
# Usage: cone_slice.py full.bench OUTPUT_NAME cone.bench
import re, sys

if len(sys.argv) != 4:
    sys.exit("usage: cone_slice.py full.bench OUTPUT cone.bench")
FULL, TARGET, OUT = sys.argv[1:4]

inputs = set()
gate_of = {}          # net -> (op, [args])
order = []            # gate emission order (net names)
for line in open(FULL):
    s = line.strip()
    m = re.match(r'INPUT\((\w+)\)', s)
    if m:
        inputs.add(m.group(1)); continue
    m = re.match(r'(\w+)\s*=\s*(\w+)\((.*)\)', s)
    if m:
        net, op, args = m.group(1), m.group(2), \
            [a.strip() for a in m.group(3).split(',')]
        gate_of[net] = (op, args); order.append(net)

if TARGET not in gate_of and TARGET not in inputs:
    sys.exit(f"cone_slice: target {TARGET} is neither gate nor input")

# backward reachability from TARGET, stopping at PIs
cone = set()
support = set()
wl = [TARGET]
while wl:
    n = wl.pop()
    if n in cone or n in support:
        continue
    if n in inputs:
        support.add(n); continue
    if n not in gate_of:
        support.add(n); continue     # undriven -> treat as support (const-ish)
    cone.add(n)
    for a in gate_of[n][1]:
        wl.append(a)

# ---- topological order of the raw cone (def before use) ----
sys.setrecursionlimit(1 << 20)
topo, seen = [], set()
def visit(net):
    if net in seen or net in support:
        return
    seen.add(net)
    for a in gate_of[net][1]:
        if a in cone:
            visit(a)
    topo.append(net)
visit(TARGET)

# ---- constant-fold + identity-alias pass ----------------------------------
# json2bench ties floating nets to const0 = XOR(rst_l,rst_l) and threads them
# through XOR/AND with real logic. Those const inputs carry untestable
# (redundant) stuck-at faults that cap coverage and bloat the cone. Fold them
# out: the result is a logically-equivalent, minimal bench whose faults are all
# testable — a meaningful full-coverage certificate for the .so.
val   = {}   # net -> 0/1  (known constant)
alias = {}   # net -> net  (pass-through)
newg  = {}   # net -> (op, [arg names])   surviving real gates
def R(a):
    s = set()
    while a in alias and a not in s:
        s.add(a); a = alias[a]
    return ('c', val[a]) if a in val else ('n', a)

for net in topo:
    op, args = gate_of[net]
    r = [R(a) for a in args]
    if op in ('BUFF', 'BUF'):
        t, v = r[0]
        (val if t == 'c' else alias).__setitem__(net, v); continue
    if op == 'NOT':
        t, v = r[0]
        if t == 'c': val[net] = 1 - v
        else:        newg[net] = ('NOT', [v])
        continue
    if op in ('AND', 'NAND'):
        z = any(t == 'c' and v == 0 for t, v in r)          # any 0 -> AND is 0
        nets = [v for t, v in r if t == 'n']
        if op == 'AND':
            if z: val[net] = 0
            elif not nets: val[net] = 1
            elif len(nets) == 1: alias[net] = nets[0]
            else: newg[net] = ('AND', nets)
        else:  # NAND
            if z: val[net] = 1
            elif not nets: val[net] = 0
            else: newg[net] = ('NAND', nets)
        continue
    if op in ('OR', 'NOR'):
        one = any(t == 'c' and v == 1 for t, v in r)
        nets = [v for t, v in r if t == 'n']
        if op == 'OR':
            if one: val[net] = 1
            elif not nets: val[net] = 0
            elif len(nets) == 1: alias[net] = nets[0]
            else: newg[net] = ('OR', nets)
        else:  # NOR
            if one: val[net] = 0
            elif not nets: val[net] = 1
            else: newg[net] = ('NOR', nets)
        continue
    if op == 'XOR':
        parity, nets = 0, []
        for t, v in r:
            if t == 'c': parity ^= v
            else: nets.append(v)
        cnt = {}
        for n in nets: cnt[n] = cnt.get(n, 0) + 1          # XOR(x,x)=0
        nets = [n for n, c in cnt.items() if c % 2]
        if not nets:
            val[net] = parity
        elif len(nets) == 1:
            if parity == 0: alias[net] = nets[0]
            else:           newg[net] = ('NOT', [nets[0]])
        elif parity == 0:
            newg[net] = ('XOR', nets)
        else:
            newg[net + '__x'] = ('XOR', nets)
            newg[net] = ('NOT', [net + '__x'])
        continue
    # unknown gate: keep, substitute constants via lazy const source below
    newg[net] = (op, [v if t == 'n' else ('__const1' if v else '__const0')
                      for t, v in r])

# ---- resolve the output driver, add const sources only if needed ----------
need_const = set()
def as_const(v):
    n = '__const1' if v else '__const0'
    need_const.add(v); return n
rt, rv = R(TARGET)
if rt == 'c':                                  # degenerate: output is constant
    newg[TARGET] = ('BUFF', [as_const(rv)])
elif rv != TARGET:                             # output aliases a PI/other net
    newg[TARGET] = ('BUFF', [rv])
# else TARGET already drives itself in newg

if need_const:
    pi0 = sorted(support)[0] if support else next(iter(inputs))
    newg['__const0'] = ('XOR', [pi0, pi0])
    if 1 in need_const:
        newg['__const1'] = ('NOT', ['__const0'])

# ---- reachable survivors from TARGET, emit topo, only referenced PIs -------
usedg, usedpi = set(), set()
def mark(net):
    if net in usedg: return
    if net not in newg:
        if net in inputs or net in support: usedpi.add(net)
        return
    usedg.add(net)
    for a in newg[net][1]:
        mark(a)
mark(TARGET)

emit_order, emitted = [], set()
def emit(net):
    if net in emitted or net not in usedg: return
    emitted.add(net)
    for a in newg[net][1]:
        emit(a)
    emit_order.append(net)
emit(TARGET)

with open(OUT, 'w') as f:
    f.write(f"# cone of {TARGET} sliced from {FULL} (const-folded)\n")
    for pi in sorted(usedpi):
        f.write(f"INPUT({pi})\n")
    f.write(f"OUTPUT({TARGET})\n\n")
    for net in emit_order:
        op, a = newg[net]
        f.write(f"{net} = {op}({', '.join(a)})\n")
print(f"{TARGET}: {len(usedpi)} support PIs, "
      f"{len([n for n in usedg if not n.endswith('__x')])} gates "
      f"(raw cone {len(cone)})")
