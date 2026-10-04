#!/usr/bin/env python3
# mem_prep.py - memory transforms of a (stripped) gsm model for the farm.
# Part of the bfit GPU-farm kit (PolyForm Noncommercial 1.0.0).
#
#  ROM:    a memory named by --rom (regex over state_t member names; default:
#          the instruction RAM and yosys proc_rom case tables) leaves state_t
#          and becomes ONE shared read-only table
#              static SM_ROM_ATTR const uintNN_t <name>_rom[D] = { init... };
#          read as SM_ROM_LD(<name>_rom, idx) (GPU: __ldg from global memory;
#          CPU: a plain load).  Every write statement to it is replaced by
#          H3_TRAP() under its ORIGINAL guard, so the transform can never
#          silently diverge: the i_ram's write port is tied off in RTL
#          (HAS_WRITE_PORT=0 -> the guard folds to 0 and the compiler drops
#          it), and if a write ever fired the run would abort.
#  narrow: every other memory whose words are <= 32 bits is stored as
#          uint32_t instead of uint64_t (reads zero-extend; a write's value is
#          old & ~mask | data & mask with mask < 2^width, so nothing is lost).
# Effect: per-instance state loses the whole i_ram and half of every RAM.
#   mem_prep.py model_s.c > model_m.c        (summary on stderr)
import re, sys, argparse

ap = argparse.ArgumentParser()
ap.add_argument('model')
ap.add_argument('--rom', default=r'(_i_ram_.*_mem$|_proc_rom_)')
ap.add_argument('--no-narrow', action='store_true')
a = ap.parse_args()
src = open(a.model).read()

m = re.search(r'typedef struct \{\n(.*?)\n\} state_t;', src, re.S)
if not m:
    sys.exit('mem_prep: no state_t')
body = m.group(1)
mems = {}   # name -> (depth, width)
for ln in body.split('\n'):
    d = re.match(r'\s*uint64_t (\w+)\[(\d+)\];\s*// (\d+) x (\d+)-bit\s*$', ln)
    if d:
        mems[d.group(1)] = (int(d.group(2)), int(d.group(4)))
roms = [n for n in mems if re.search(a.rom, n)]
narrow = [] if a.no_narrow else [n for n in mems if n not in roms and mems[n][1] <= 32]


def ctype(w):
    return 'uint8_t' if w <= 8 else 'uint16_t' if w <= 16 else 'uint32_t' if w <= 32 else 'uint64_t'


def sub_reads(text, name, repl_fn):
    """replace s->NAME[expr] (bracket-matched) by repl_fn(expr)"""
    key = 's->%s[' % name
    out, i = [], 0
    while True:
        j = text.find(key, i)
        if j < 0:
            out.append(text[i:]); break
        out.append(text[i:j])
        k, depth = j + len(key), 1
        while depth:
            c = text[k]
            depth += (c == '[') - (c == ']')
            k += 1
        out.append(repl_fn(text[j + len(key):k - 1]))
        i = k
    return ''.join(out)


tables = []
stats = []
for name in roms:
    depth, width = mems[name]
    init = {}
    def grab(mm):
        init[int(mm.group(1))] = int(mm.group(2), 16)
        return ''
    src, ninit = re.subn(r'\n    s->%s\[(\d+)\] = (?:UINT64_C\()?(0x[0-9a-fA-F]+)U?\)?;' % re.escape(name),
                         lambda mm: grab(mm), src)
    # writes: one statement per line, "s->NAME[_wa] = <expr>; }" -> trap under the same guard
    src, nw = re.subn(r's->%s\[_wa\] = [^\n]*?; \}$' % re.escape(name), 'H3_TRAP(); }', src, flags=re.M)
    if ('s->%s[_wa]' % name) in src:
        sys.exit('mem_prep: %s: a write statement was not recognised' % name)
    nr = src.count('s->%s[' % name)
    src = sub_reads(src, name, lambda e, n=name: 'SM_ROM_LD(%s_rom, %s)' % (n, e))
    src = re.sub(r'\n\s*uint64_t %s\[\d+\];[^\n]*' % re.escape(name), '\n    /* %s: shared ROM (mem_prep.py) */' % name, src, count=1)
    words = [init.get(i, 0) for i in range(depth)]
    tables.append('static SM_ROM_ATTR const %s %s_rom[%d] = {%s};' % (
        ctype(width), name, depth, ','.join('0x%x' % w for w in words)))
    stats.append('%s: ROM %dx%d (%d init words, %d writes trapped, %d reads)' % (name, depth, width, ninit, nw, nr))
for name in narrow:
    depth, width = mems[name]
    src, n = re.subn(r'(\n\s*)uint64_t (%s\[\d+\];)' % re.escape(name), r'\1uint32_t \2', src, count=1)
    stats.append('%s: narrowed to uint32_t (%dx%d)' % (name, depth, width))
# tables go right before sm_reset (after the typedefs)
src = src.replace('\nvoid sm_reset(state_t *s) {', '\n/* mem_prep.py shared ROMs */\n' + '\n'.join(tables) + '\n\nvoid sm_reset(state_t *s) {', 1)
sys.stdout.write('/* mem_prep.py: %s */\n' % '; '.join(stats) + src)
sys.stderr.write('mem_prep: ' + '\n          '.join(stats) + '\n')
