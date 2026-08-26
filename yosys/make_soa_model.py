#!/usr/bin/env python3
# Struct-of-Arrays transform for a generated gsm model: every state_t /
# inputs_t / outputs_t member becomes an [SM_N]-strided array (limb-major,
# instance-fastest) and every access is indexed by sm_gid.  SM_N and sm_gid
# are macros: CPU cert build = 1/0 (bit-identical semantics), CUDA build =
# farm width / thread id.  Wide-helper calls whose args mix state arrays
# (stride SM_N) and function-local temps (stride 1) are rewritten to
# stride-parametric *2 variants defined in the injected prelude.
#   make_soa_model.py model.c > model_soa.c
import re, sys

# multi-file: member table from ALL files, transform EACH, suffix _soa
files = sys.argv[1:]
srcs = {f: open(f).read() for f in files}
src = '\n/*FILE-BREAK*/\n'.join(srcs[f] for f in files)

# ---- 1. member table from the three struct blocks -------------------------
members = {}   # name -> 'scalar' | 'array'
struct_spans = []
for m in re.finditer(r'typedef struct \{(.*?)\} (\w*state_t|\w*inputs_t|\w*outputs_t);', src, re.S):
    struct_spans.append((m.start(1), m.end(1)))
    for line in m.group(1).splitlines():
        d = re.match(r'\s*(uint8_t|uint32_t|uint64_t|int)\s+(_[A-Za-z0-9_]+)(\[(\d+)\])?(\[\d+\])?\s*;', line)
        if d:
            members[d.group(2)] = 'array' if d.group(3) else 'scalar'
            if d.group(5):  # 2-D (coverage tables) — guarded out, treat as array
                members[d.group(2)] = 'array2'

# ---- 2. rewrite struct declarations ---------------------------------------
def fix_decl(mm):
    typ, name, arr = mm.group(1), mm.group(2), mm.group(3)
    if arr:
        n = arr[1:-1]
        return f'{typ} {name}[({n})*SM_N];'
    return f'{typ} {name}[SM_N];'

out_parts, pos = [], 0
for a, b in struct_spans:
    out_parts.append(src[pos:a])
    body = src[a:b]
    body = re.sub(r'(uint8_t|uint32_t|uint64_t|int)\s+(_[A-Za-z0-9_]+)(\[\d+\])?\s*;', fix_decl, body)
    out_parts.append(body)
    pos = b
out_parts.append(src[pos:])
src = ''.join(out_parts)

# ---- 3. rewrite member accesses -------------------------------------------
tok = re.compile(r'(&?)\b(s|vin_veer|vo_veer|vin_ic|vo_ic|V|IC|in|o|k|S_|I|O)\s*(->|\.)\s*(_[A-Za-z0-9_]+)')
def find_bracket(s, i):
    # s[i] == '['; return index after matching ']'
    depth = 0
    while i < len(s):
        if s[i] == '[': depth += 1
        elif s[i] == ']':
            depth -= 1
            if depth == 0: return i + 1
        i += 1
    raise SystemExit('unbalanced bracket')

res, pos = [], 0
while True:
    m = tok.search(src, pos)
    if not m:
        res.append(src[pos:]); break
    res.append(src[pos:m.start()])
    amp, base, op, name = m.groups()
    ref = f'{base}{op}{name}'
    kind = members.get(name)
    j = m.end()
    while j < len(src) and src[j] in ' \t': j += 1
    if kind is None:                      # not a struct member (shouldn't happen)
        res.append(m.group(0)); pos = m.end(); continue
    if kind == 'scalar':
        res.append(f'{amp}{ref}[sm_gid]' if not amp else f'(&{ref}[sm_gid])')
        pos = m.end(); continue
    # array member
    if j < len(src) and src[j] == '[':
        k = find_bracket(src, j)
        idx = src[j+1:k-1]
        if amp:                           # &s->arr[idx]  -> strided pointer
            res.append(f'({ref}+((size_t)({idx}))*SM_N+sm_gid)')
        else:                             # s->arr[idx]   -> element
            res.append(f'{ref}[((size_t)({idx}))*SM_N+sm_gid]')
        pos = k; continue
    # bare array base (helper arg) -> gid-offset pointer, stride at call site
    res.append(f'({ref}+sm_gid)')
    pos = m.end()
src = ''.join(res)

# ---- 4. rewrite helper calls with per-arg strides -------------------------
# an arg is state-strided iff its text ends with '+sm_gid)' or contains '*SM_N+sm_gid)'
def split_args(s):
    args, depth, cur = [], 0, []
    for ch in s:
        if ch == ',' and depth == 0:
            args.append(''.join(cur).strip()); cur = []
        else:
            if ch in '([': depth += 1
            if ch in ')]': depth -= 1
            cur.append(ch)
    args.append(''.join(cur).strip())
    return args

def stride_of(a):
    return 'SM_N' if ('sm_gid)' in a or 'sm_gid]' in a) else '1'

HELPERS = {  # name -> list of arg positions that are limb-array pointers
    'worbits':   [0, 2], 'wplacew': [0, 2], 'wcopy': [0, 1],
    'worbits_s': [0],    'wplacew_s': [0],  'wslice64': [0],
    'wred_or':   [0],
}
call = re.compile(r'\b(worbits_s|wplacew_s|worbits|wplacew|wcopy|wslice64|wred_or)\(')
res, pos = [], 0
while True:
    m = call.search(src, pos)
    if not m:
        res.append(src[pos:]); break
    name = m.group(1)
    # find matching close paren
    j, depth = m.end(), 1
    while depth:
        if src[j] == '(': depth += 1
        elif src[j] == ')': depth -= 1
        j += 1
    args = split_args(src[m.end():j-1])
    ptr_pos = HELPERS[name]
    strides = [stride_of(args[p]) for p in ptr_pos]
    if all(st == '1' for st in strides):
        res.append(src[pos:j]); pos = j; continue
    new_args = []
    for i, a in enumerate(args):
        new_args.append(a)
        if i in ptr_pos:
            new_args.append(strides[ptr_pos.index(i)])
    res.append(src[pos:m.start()] + f'{name}2(' + ','.join(new_args) + ')')
    pos = j
src = ''.join(res)

# ---- 5. inject prelude after the last original helper ---------------------
PRELUDE = r'''
/* ==== SoA farm mode (make_soa_model.py) ==== */
#ifndef SM_N
#define SM_N 1
#endif
#ifndef sm_gid
#define sm_gid 0
#endif
static inline uint64_t wslice642(const uint32_t*s,int ss,int off,int w,int n){
  int l=off>>5,b=off&31;uint64_t lo=s[(size_t)l*ss];
  if(l+1<n)lo|=(uint64_t)s[(size_t)(l+1)*ss]<<32;uint64_t v=lo>>b;
  if(b&&w+b>64&&l+2<n)v|=(uint64_t)s[(size_t)(l+2)*ss]<<(64-b);
  return w>=64?v:(v&((UINT64_C(1)<<w)-1));}
static inline void wcopy2(uint32_t*d,int ds,const uint32_t*s,int ss,int n){
  for(int i=0;i<n;i++)d[(size_t)i*ds]=s[(size_t)i*ss];}
static inline int wred_or2(const uint32_t*a,int as,int n){
  for(int i=0;i<n;i++)if(a[(size_t)i*as])return 1;return 0;}
static inline void worbits2(uint32_t*d,int ds,int doff,const uint32_t*s,int ss,int soff,int w){
  while(w>0){int db=doff&31,sb=soff&31;int n=32-(db>sb?db:sb);if(n>w)n=w;
    uint32_t mk=(n>=32)?0xffffffffu:((1u<<n)-1u);
    d[(size_t)(doff>>5)*ds]|=(((s[(size_t)(soff>>5)*ss]>>sb)&mk)<<db);
    doff+=n;soff+=n;w-=n;}}
static inline void worbits_s2(uint32_t*d,int ds,int doff,uint64_t s,int soff,int w){
  s>>=soff;
  while(w>0){int db=doff&31;int n=32-db;if(n>w)n=w;
    uint32_t mk=(n>=32)?0xffffffffu:((1u<<n)-1u);
    d[(size_t)(doff>>5)*ds]|=(((uint32_t)s&mk)<<db);
    s>>=n;doff+=n;w-=n;}}
static inline void wplacew2(uint32_t*d,int ds,int doff,const uint32_t*s,int ss,int soff,int w){
  while(w>0){int db=doff&31,sb=soff&31;int n=32-(db>sb?db:sb);if(n>w)n=w;
    uint32_t mk=(n>=32)?0xffffffffu:((1u<<n)-1u);
    d[(size_t)(doff>>5)*ds]=(d[(size_t)(doff>>5)*ds]&~(mk<<db))|(((s[(size_t)(soff>>5)*ss]>>sb)&mk)<<db);
    doff+=n;soff+=n;w-=n;}}
static inline void wplacew_s2(uint32_t*d,int ds,int doff,uint64_t s,int soff,int w){
  s>>=soff;
  while(w>0){int db=doff&31;int n=32-db;if(n>w)n=w;
    uint32_t mk=(n>=32)?0xffffffffu:((1u<<n)-1u);
    d[(size_t)(doff>>5)*ds]=(d[(size_t)(doff>>5)*ds]&~(mk<<db))|(((uint32_t)s&mk)<<db);
    s>>=n;doff+=n;w-=n;}}
/* ==== end SoA prelude ==== */
'''
anchor = src.find('const int sm_live_outputs_words')
if anchor < 0:
    # no live-outputs marker (multi-file VeeR flow): prepend to first file
    src = PRELUDE + src
else:
    src = src[:anchor] + PRELUDE + src[anchor:]
if len(files) == 1:
    sys.stdout.write(src)
else:
    parts = src.split('\n/*FILE-BREAK*/\n')
    for f, body in zip(files, parts):
        outn = f.replace('.c', '_soa.c').replace('.h', '_soa.h')
        open(outn, 'w').write(body)
        sys.stderr.write('wrote %s\n' % outn)
'''NOTE: original helper semantics for the _s variants shift the scalar by
soff first; verified against the in-file definitions before use.'''
