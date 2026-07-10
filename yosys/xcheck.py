#!/usr/bin/env python3
# xcheck.py — random differential cross-check of a gen_statemachine .so against
# an independent iverilog sim of the SAME netlist, to find pure codegen bugs.
#
# Usage: xcheck.py <ref.v> <generated.c> <top_module> [cycles] [seed]
#   <ref.v>  iverilog-readable Verilog of the netlist. Produce from a subtree.v
#            (which yosys accepts but iverilog often will not) with:
#     yosys -q -p "read_verilog -sv subtree.v; hierarchy -top TOP; proc; flatten; \
#                  opt_clean; write_verilog -noattr ref.v"
#   <generated.c>  the gen_statemachine C (defines sm_reset/sm_clock/sm_comb).
# Drives both with identical random vectors (rst_l biased high, a reset preamble
# first so iverilog X-state flushes) and reports the earliest per-output mismatch.
# A mismatch on a signal the reference holds constant while the .so varies is the
# classic multi-source Y-scatter / clock-enable codegen bug signature.

import re, random, subprocess, sys, os

V   = sys.argv[1]                      # subtree.v
C   = sys.argv[2]                      # generated .c (has sm_reset/sm_clock/sm_comb)
TOP = sys.argv[3]                      # top module name
N   = int(sys.argv[4]) if len(sys.argv) > 4 else 400
SEED= int(sys.argv[5]) if len(sys.argv) > 5 else 1
random.seed(SEED)
WORK=os.environ.get("XCHECK_WORK","/tmp/xcheck"); os.makedirs(WORK, exist_ok=True)

# ---- parse top-module port list (name, dir, width), in declaration order ----
ports=[]; inmod=False
for l in open(V).read().splitlines():
    if re.match(r'^module\s+'+re.escape(TOP)+r'\b', l): inmod=True; continue
    if inmod:
        if l.strip().startswith(');') or l.strip()==');': break
        m=re.match(r'\s*(input|output)\s*(?:reg\s+)?(\[(\d+):(\d+)\])?\s*(\w+)', l)
        if m:
            w=(int(m.group(3))-int(m.group(4))+1) if m.group(2) else 1
            ports.append((m.group(5), m.group(1), w))
inputs =[(n,w) for n,d,w in ports if d=='input']
outputs=[(n,w) for n,d,w in ports if d=='output']
# clock set: main 'clk' + whatever the generated C names in sm_extra_clocks[]
mx=re.search(r'sm_extra_clocks\[\] = \{([^}]*)\}', open(C).read())
EXTRA=[s.strip().strip('"') for s in mx.group(1).split(',') if s.strip() not in ('','0')] if mx else []
CLK={'clk'} | set(EXTRA)
CLK &= {n for n,_ in inputs}          # only clocks that are real ports
vin=[(n,w) for n,w in inputs if n not in CLK]
def limbs(w): return (w+31)//32
def hexdig(w): return (w+3)//4

# ---- generate vector file. Reset preamble first: cyc0 rst_l=1 (so cyc1's
# rst_l=0 is a negedge that triggers iverilog's async resets), cyc1..PRE-1
# rst_l=0 all-inputs-0 to flush X out of the flops, then random. Compare only
# after the preamble.
PRE=8
vecs=[]
for c in range(N):
    row={}
    for n,w in vin:
        if c < PRE:
            row[n] = (1 if c==0 else 0) if n=='rst_l' else 0
        else:
            row[n] = (1 if random.random()<0.90 else 0) if n=='rst_l' else random.getrandbits(w)
    vecs.append(row)
with open(f"{WORK}/vecs.txt","w") as f:
    for row in vecs:
        f.write(' '.join('%0*x'%(hexdig(w),row[n]) for n,w in vin)+'\n')

# ---- iverilog testbench ----
tb=[f'`timescale 1ns/1ns','module tb;']
tb.append('  reg '+', '.join(sorted(CLK))+';')
for n,w in vin:  tb.append(f'  reg [{w-1}:0] {n};')
for n,w in outputs: tb.append(f'  wire [{w-1}:0] {n};')
conn=[f'.{n}({n})' for n in sorted(CLK)]
conn+=[f'.{n}({n})' for n,_ in vin]+[f'.{n}({n})' for n,_ in outputs]
tb.append(f'  {TOP} dut({", ".join(conn)});')
tb.append('  integer fd, cyc, code;')
tb.append('  initial begin')
tb.append('    '+' '.join(f'{n}=0;' for n in sorted(CLK)))
for n,w in vin: tb.append(f'    {n}=0;')
tb.append(f'    fd=$fopen("{WORK}/vecs.txt","r");')
scan='    code=$fscanf(fd,"'+' '.join(['%h']*len(vin))+r'\n",'+','.join(n for n,_ in vin)+');'
tb.append(f'    for (cyc=0; cyc<{N}; cyc=cyc+1) begin')
tb.append(scan)
tb.append('      #1; '+' '.join(f'{n}=1;' for n in sorted(CLK))+' #1; '+' '.join(f'{n}=0;' for n in sorted(CLK))+' #1;')
fmt=' '.join(['%0'+str(hexdig(w))+'h' for _,w in outputs])
tb.append(f'      $display("{fmt}",'+','.join(n for n,_ in outputs)+');')
tb.append('    end $finish; end')
tb.append('endmodule')
open(f"{WORK}/tb.v","w").write('\n'.join(tb)+'\n')

# ---- C driver ----
def is_wide(w): return w>64
cd=['#define SM_NO_MAIN 1', f'#include "{C}"',
    '#include <stdio.h>','#include <stdlib.h>','#include <string.h>',
    'static void rdhex(const char*t, uint32_t*lm, int nl){',
    '  int L=strlen(t); memset(lm,0,nl*4);',
    '  for(int i=0;i<L;i++){int c=t[L-1-i]; int v=(c>=48&&c<=57)?c-48:(c>=97&&c<=102)?c-87:(c>=65&&c<=70)?c-55:0;',
    '    int bit=i*4; if(bit/32<nl) lm[bit/32]|=(uint32_t)v<<(bit%32);}}',
    'int main(){ state_t s; inputs_t in; outputs_t o; memset(&in,0,sizeof in); sm_reset(&s);',
    f'  FILE*f=fopen("{WORK}/vecs.txt","r"); char tok[64];',
    f'  for(int cyc=0;cyc<{N};cyc++){{']
for n,w in vin:
    cd.append(f'    if(fscanf(f,"%63s",tok)!=1) return 1;')
    if is_wide(w):
        cd.append(f'    rdhex(tok, in._{n}, {limbs(w)});')
    else:
        cd.append(f'    in._{n} = (uint64_t)strtoull(tok,0,16);')
cd.append('    sm_clock(&s,&in); sm_comb(&s,&in,&o);')
outfmt=' '.join(['%0'+str(hexdig(w))+ ('x' if not is_wide(w) else 'x') for _,w in outputs])
# build print args: wide outputs printed limb0 only won't match; print full hex for wide
prints=[]; args=[]
for n,w in outputs:
    if is_wide(w):
        # print limbs high->low as concatenated hex (match iverilog %h of full width)
        nl=limbs(w)
        # emit a small inline loop via snprintf is complex; print each limb fixed 8 hex, high first,
        # then iverilog side prints full width -> we instead compare only low 64 bits for wide.
        prints.append('%08x%08x'); args.append(f'(unsigned)o._{n}[1]'); args.append(f'(unsigned)o._{n}[0]')
    else:
        prints.append('%0'+str(hexdig(w))+'x'); args.append(f'(unsigned long long)o._{n}')
# wide outputs: print ALL limbs high->low (so >64b signals compare fully)
pr=[]; ar=[]
for n,w in outputs:
    if is_wide(w):
        nl=limbs(w); pr.append('%08x'*nl)
        for k in range(nl-1,-1,-1): ar.append(f'(unsigned)o._{n}[{k}]')
    else:
        pr.append('%0'+str(hexdig(w))+'llx'); ar.append(f'(unsigned long long)o._{n}')
cd.append('    printf("'+' '.join(pr)+r'\n",'+','.join(ar)+');')
cd.append('  } return 0; }')
open(f"{WORK}/drv.c","w").write('\n'.join(cd)+'\n')

# ---- build + run ----
r=subprocess.run(['iverilog','-o',f'{WORK}/tb.vvp',f'{WORK}/tb.v',V],capture_output=True,text=True)
if r.returncode: print("IVERILOG BUILD FAIL:\n",r.stderr[:3000]); sys.exit(1)
r=subprocess.run(['cc','-O1','-o',f'{WORK}/drv',f'{WORK}/drv.c'],capture_output=True,text=True)
if r.returncode: print("CC BUILD FAIL:\n",r.stderr[:3000]); sys.exit(1)
iv=subprocess.run(['vvp',f'{WORK}/tb.vvp'],capture_output=True,text=True).stdout.splitlines()
dv=subprocess.run([f'{WORK}/drv'],capture_output=True,text=True).stdout.splitlines()
iv=[x for x in iv if x and not x.startswith('VCD')]
onames=[n for n,_ in outputs]
print(f"cycles: iverilog={len(iv)} cdriver={len(dv)}  outputs={len(onames)}")
owidths=[w for _,w in outputs]
def eq(a,b,w):
    # compare low w bits; iverilog x/z nibbles are don't-care (undriven/4-state)
    a=a.lower(); b=b.lower(); n=(w+3)//4
    a=a.rjust(n,'0')[-n:]; b=b.rjust(n,'0')[-n:]
    return all(a[i] in 'xz' or a[i]==b[i] for i in range(n))
mism={}
for cyc in range(8, min(len(iv),len(dv))):   # skip reset preamble
    a=iv[cyc].split(); b=dv[cyc].split()
    if len(a)!=len(onames) or len(b)!=len(onames): continue
    for j,nm in enumerate(onames):
        if not eq(a[j],b[j],owidths[j]) and nm not in mism:
            mism[nm]=(cyc,a[j],b[j])
if not mism: print("*** NO MISMATCH — .so matches iverilog on all outputs ***")
else:
    print(f"{len(mism)} outputs mismatch. Earliest by cycle:")
    for nm,(cyc,iva,dva) in sorted(mism.items(),key=lambda kv:kv[1][0])[:25]:
        print(f"  cyc={cyc:>4}  {nm:<28} iverilog={iva} cdriver={dva}")
