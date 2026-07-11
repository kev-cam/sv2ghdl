#!/usr/bin/env python3
# json2bench.py — yosys JSON (techmap;simplemap primitives) -> ISCAS89 .bench
#
# Usage: yosys -p "...; techmap; simplemap; opt_clean; write_json X.json"
#        json2bench.py X.json TOP out.bench
#
# Emits the primitive set Atalanta 2.0 accepts: AND OR NAND NOR XOR XNOR NOT
# BUFF DFF.  $_MUX_ decomposes to AND/OR/NOT; $_SDFF*_/$_DFFE*_ variants fold
# their reset/enable into the D cone (full-scan ATPG model: DFF = PPI/PPO, so
# the D cone is what coverage exercises).  Clock inputs vanish (bench DFFs are
# implicitly clocked).  Constants fold; a residual const net is synthesized
# from the first input (x XOR x = 0).
import json, re, sys, os

if len(sys.argv) != 4:
    sys.exit("usage: json2bench.py in.json TOP out.bench")

d = json.load(open(sys.argv[1]))
mod = d['modules'][sys.argv[2]]
out = open(sys.argv[3], 'w')

CLOCKS = {'clk', '_clk'}          # implicit in bench DFFs

names = {}                        # json bit id -> bench net name

def sanitize(nm):
    # Match gen_statemachine's cname(): non-[A-Za-z0-9_] -> '_', KEEP underscores
    # (do NOT strip — the model field is cname('\'+wire) = '_'+sanitize(wire), and
    # scan_certify.resolve() reattaches that leading '_'), 'w_' on a leading digit.
    s = ''.join(ch if (ch.isalnum() or ch == '_') else '_' for ch in nm)
    if s and s[0].isdigit():
        s = 'w_' + s
    return s or 'n'

consts = {}                       # bit id of synthesized const nets
lines = []                        # gate lines, emitted after headers
inputs, outputs, dffs = [], [], []
uid = [0]

def fresh(pfx='n'):
    uid[0] += 1
    return f"{pfx}{uid[0]}"

def bit_name(b):
    if isinstance(b, str):        # "0"/"1"/"x" constants
        return ('#0' if b in ('0', 'x', 'z') else '#1')
    if b not in names:
        names[b] = fresh()
    return names[b]

# Naming priority (all claim-only, so higher priority wins the bit):
#   (1) register manifest wires  -> match state_t fields
#   (2) ports                    -> match inputs_t / outputs_t fields
#   (3) any other named wire
# A register that directly drives an OUTPUT port thus KEEPS its state-field name
# for the PPI/PPO; the output PO is materialized separately (a port-named BUFF of
# the driver) so it still maps to an outputs_t field.
netnames = mod.get('netnames', {})

def claim(wname):
    w = netnames.get(wname)
    if not w:
        return
    nm = sanitize(wname)
    bits = w['bits']
    for i, b in enumerate(bits):
        if isinstance(b, int) and b not in names:
            names[b] = nm if len(bits) == 1 else f"{nm}_{i}"

# (1) register wires from gen_statemachine's <json>.regnames manifest (or
# J2B_REGNAMES). A register net has aliases (bus_intf...obuf_merge vs
# ...obuf_mergeff.dffs.dout_reg); the model names the state_t field after the FF
# Q wire, so claim those EXACT wires FIRST or the cone goes unmapped.
regnames_file = os.environ.get('J2B_REGNAMES') or (sys.argv[1] + '.regnames')
priority = ([l.strip() for l in open(regnames_file) if l.strip()]
            if os.path.exists(regnames_file) else [])
n_claimed = sum(1 for wn in priority if wn in netnames)
for wname in priority:
    claim(wname)

# (2) ports: name any UNCLAIMED bit; build the PI list; collect output ports to
# materialize after the gate helpers are defined.
out_ports = []
for pname, p in mod['ports'].items():
    bits = p['bits']
    for i, b in enumerate(bits):
        if isinstance(b, int) and b not in names:
            names[b] = pname if len(bits) == 1 else f"{pname}_{i}"
    if p['direction'] == 'input':
        if pname in CLOCKS:
            continue
        for i, b in enumerate(bits):
            inputs.append(pname if len(bits) == 1 else f"{pname}_{i}")
    elif p['direction'] == 'output':
        for i, b in enumerate(bits):
            out_ports.append((pname if len(bits) == 1 else f"{pname}_{i}", b))

# (3) any remaining named wires
for wname in netnames:
    if wname.startswith('$'):
        continue
    claim(wname)

mod_inputs0 = list(inputs)

def subst(ins):
    return [i if i not in ('#0', '#1') else (const0() if i == '#0' else const1())
            for i in ins]

def gate(op, y, ins):
    lines.append(f"{y} = {op}({', '.join(subst(ins))})")

_c0root = [None]
_c0pool = []
_c0use = [0]
def const0():
    # single XOR-self root, fanned out through a buffer pool so no const net
    # exceeds ~300 fanout (atalanta MAXFOUT); round-robin every use.
    if _c0root[0] is None:
        s0 = inputs[0] if inputs else 'G0'
        _c0root[0] = fresh('const0_')
        lines.append(f"{_c0root[0]} = XOR({s0}, {s0})")
    if _c0use[0] % 300 == 0:
        b = fresh('c0b_'); lines.append(f"{b} = BUFF({_c0root[0]})"); _c0pool.append(b)
    _c0use[0] += 1
    return _c0pool[(_c0use[0]-1)//300]

def const1():
    c1 = fresh('const1_')
    lines.append(f"{c1} = NOT({const0()})")
    return c1

# Materialize output ports: OUTPUT(port) = BUFF(driver). Naming the PO by the
# PORT (not the driver net) guarantees it maps to an outputs_t field even when
# the driver is a register whose net carries a state_t name (a register that
# directly drives an output). When the driver already IS the port net (ordinary
# combinational output), no buffer is added.
for pn, b in out_ports:
    drv = bit_name(b)
    if drv == pn:
        outputs.append(pn)
    else:
        gate('BUFF', pn, [drv])
        outputs.append(pn)

SIMPLE = {'$_AND_': 'AND', '$_OR_': 'OR', '$_NAND_': 'NAND', '$_NOR_': 'NOR',
          '$_XOR_': 'XOR', '$_XNOR_': 'XNOR', '$_NOT_': 'NOT', '$_BUF_': 'BUFF'}

for cname, cell in mod['cells'].items():
    t = cell['type']
    cn = cell['connections']
    if t in SIMPLE:
        y = bit_name(cn['Y'][0])
        ins = [bit_name(cn[k][0]) for k in ('A', 'B') if k in cn]
        gate(SIMPLE[t], y, ins)
    elif t == '$_MUX_':
        a, b, s = (bit_name(cn[k][0]) for k in ('A', 'B', 'S'))
        y = bit_name(cn['Y'][0])
        ns, t1, t2 = fresh(), fresh(), fresh()
        gate('NOT', ns, [s])
        gate('AND', t1, [a, ns])
        gate('AND', t2, [b, s])
        gate('OR', y, [t1, t2])
    elif t.startswith(('$_DFF', '$_SDFF', '$_DFFE', '$_SDFFE', '$_SDFFCE')):
        q = bit_name(cn['Q'][0])
        dcone = bit_name(cn['D'][0])
        # fold sync reset: $_SDFF_<CLKPOL><RSTPOL><RSTVAL>_
        if t.startswith('$_SDFF'):
            r = bit_name(cn['R'][0])
            pol = t.split('_')[2]        # e.g. PN0 / PP1
            rpol, rval = pol[1], pol[2]
            ract = r if rpol == 'P' else None
            if ract is None:             # active-low: reset when NOT r
                nr = fresh(); gate('NOT', nr, [r]); ract = nr
            nd = fresh()
            if rval == '0':              # q<=0 on reset: D' = D AND NOT ract
                na = fresh(); gate('NOT', na, [ract])
                gate('AND', nd, [dcone, na])
            else:                        # q<=1 on reset: D' = D OR ract
                gate('OR', nd, [dcone, ract])
            dcone = nd
        if 'E' in t.split('_')[1]:       # enable variants: D' = E?D:Q
            e = bit_name(cn['E'][0])
            ne, t1, t2, nd = fresh(), fresh(), fresh(), fresh()
            gate('NOT', ne, [e])
            gate('AND', t1, [dcone, e])
            gate('AND', t2, [q, ne])
            gate('OR', nd, [t1, t2])
            dcone = nd
        dffs.append((q, dcone))
    elif t == '$scopeinfo':
        continue                      # yosys hierarchy metadata, no logic
    else:
        sys.exit(f"json2bench: unhandled cell type {t} ({cname})")

# FULL-SCAN view: the certify model treats registers as pseudo-PI/PO
# ({PI, PPI=state} in, {PO, PPO=next-state} out), so emit exactly that —
# a purely combinational bench (every DFF Q becomes INPUT, its D cone
# becomes OUTPUT next_<q> via a BUFF). Tool-agnostic: works with
# combinational-only ATPG (this Atalanta build fatals on any FF).
# Bench sanitizer (atalanta fatals on floating nets — its own two fixes,
# automated to fixpoint):
#   (a) a net referenced (gate input / OUTPUT / DFF-D) but never driven
#       (no INPUT, no gate LHS) is tied to constant 0 via a synth gate;
#   (b) an INPUT/PPI never referenced is dropped.
# Iterate: dropping an input can orphan a gate, orphaning can un-drive.
ppi_names = [q for q, _ in dffs]
def parse_line(l):
    tgt = l.split('=')[0].strip()
    args = [a.strip() for a in re.findall(r'\((.*)\)', l)[0].split(',')]
    return tgt, args
n_tied = 0
while True:
    driven = set(inputs) | set(ppi_names) | {parse_line(l)[0] for l in lines}
    referenced = set(outputs) | {dc for _, dc in dffs}
    for l in lines:
        referenced.update(parse_line(l)[1])
    floating = [n for n in referenced if n not in driven
                and not n.startswith('const')]
    if floating:
        for n in floating:
            lines.append(f"{n} = BUFF({const0()})")
            n_tied += 1
        continue                   # added defs; re-check
    # drop unused inputs / PPIs
    used = set(outputs) | {dc for _, dc in dffs}
    for l in lines:
        used.update(parse_line(l)[1])
    new_in  = [i for i in inputs    if i in used]
    new_ppi = [q for q in ppi_names if q in used]
    if len(new_in) == len(inputs) and len(new_ppi) == len(ppi_names):
        break
    inputs, ppi_names = new_in, new_ppi
dropped = [i for i in mod_inputs0 if i not in set(inputs)]
dffs = [(q, dc) for q, dc in dffs if q in set(ppi_names)]
live_inputs = inputs

out.write(f"# generated by json2bench.py from {sys.argv[1]} ({sys.argv[2]})\n")
out.write(f"# full-scan: {len(dffs)} registers as PPI/PPO\n")
if dropped:
    out.write(f"# dropped {len(dropped)} floating inputs: "
              f"{' '.join(dropped[:12])}{' ...' if len(dropped)>12 else ''}\n")
for i in live_inputs:
    out.write(f"INPUT({i})\n")
for q, _ in dffs:
    out.write(f"INPUT({q})\n")
for o in outputs:
    out.write(f"OUTPUT({o})\n")
for q, _ in dffs:
    out.write(f"OUTPUT(next_{q})\n")
out.write("\n")
for q, dc in dffs:
    dc = subst([dc])[0]
    out.write(f"next_{q} = BUFF({dc})\n")
for l in lines:
    out.write(l + "\n")
out.close()
print(f"bench (full-scan): {len(live_inputs)}+{len(dffs)} PI, "
      f"{len(outputs)}+{len(dffs)} PO, {len(lines)} gates "
      f"[{n_claimed}/{len(priority)} manifest reg-names claimed]")
