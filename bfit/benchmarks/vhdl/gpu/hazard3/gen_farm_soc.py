#!/usr/bin/env python3
# gen_farm_soc.py - the GPU-farm variant of the Verijit Hazard3 Mandelbrot SoC.
#
# Part of the bfit GPU-farm kit (PolyForm Noncommercial 1.0.0, bfit/LICENSE).
# Reads upstream's soc.tmpl.v at build time (verilator-hazard3-mandelbrot-testbench,
# Apache-2.0, pinned commit in tests/hazard3_mandelbrot/UPSTREAM) and writes a
# modified copy; nothing of the template is stored in this kit.  The output is
# therefore a derivative of an Apache-2.0 file and carries that notice.
#
# Changes, each matched exactly once against the pinned template (a mismatch
# means the template moved: fix this script, never guess):
#   1. ports: + input [31:0] tile_id, + output tohost_valid / tohost_data[31:0]
#   2. ${reset_vector} <- the ELF entry point (decimal, as load_elf.py)
#   3. RESET_REGFILE: upstream's 0 is KEPT by default (the gsm model and the
#      Verilator twin both start every register and RAM at 0, which is what
#      upstream relies on: sp = 0); --regfile-reset 1 gives the 4-state-safe
#      form tests/hazard3_mandelbrot uses (same cycle counts; the gsm model is
#      1,409 instead of 1,253 cells and 20-28% slower on a CPU core)
#   4. i_ram DEPTH 1<<24 -> --i-depth, preload = the ELF's executable image
#      FOLDED modulo the depth (the RAM decodes only the low address bits, so a
#      word at vaddr A lives at (A/4) mod depth; collisions are an error)
#   5. d_ram DEPTH 1<<24 -> --d-depth, preload = none (default) or the ELF's
#      data image folded likewise.  'none' is exact for this firmware: the only
#      non-executable segments are the ELF's own headers, which nothing reads
#      (build_h3.sh asserts the ELF has no allocatable data sections)
#   6. d_ram rdata goes through a mux: a read of TILE_ID_ADDR returns tile_id
#   7. the hierarchical 'finished' (core.core.fd_cir) is removed; finished = 0
#   8. TOHOST snooper (store to 0x1000 -> tohost_valid/tohost_data one cycle
#      after the data phase, the same timing as tests/hazard3_mandelbrot's
#      portable SoC, so cycle counts compare 1:1 with its ref_cycles) and the
#      TILE_ID read decoder (0x1004, data phase override)
#
#   gen_farm_soc.py --template soc.tmpl.v --elf fw.elf --i-depth 128 --d-depth 512 -o soc_farm.v
#   (writes <out-stem>_i_ram.hex [and _d_ram.hex]; prints a one-line summary)
import argparse, os, re, subprocess, sys

ELF2HEX = '/usr/local/src/sv2ghdl/tests/hazard3_mandelbrot/elf2hex.py'   # Apache-2.0, run (not copied)
TOHOST_ADDR = 0x00001000
TILE_ID_ADDR = 0x00001004

FARM_LOGIC = r'''
  // ---- GPU-farm additions (bfit gpu/hazard3/gen_farm_soc.py) -----------------
  // TOHOST: a write transfer to TOHOST_ADDR seen in its AHB-Lite address phase
  // is captured in its data phase (next cycle with d_hready): tohost_data <=
  // d_hwdata and tohost_valid pulses for one cycle.  TILE_ID: a read transfer
  // to TILE_ID_ADDR in its address phase makes the data phase return the
  // tile_id input instead of the RAM word.  The RAM still sees both accesses
  // (it decodes only the low address bits); the firmware uses neither word.
  localparam [31:0] TOHOST_ADDR  = 32'h%08x;
  localparam [31:0] TILE_ID_ADDR = 32'h%08x;
  reg tohost_dphase;
  reg tile_dphase;
  initial begin
    tohost_dphase = 1'b0;
    tile_dphase   = 1'b0;
    tohost_valid  = 1'b0;
    tohost_data   = 32'h0;
  end
  assign d_hrdata = tile_dphase ? tile_id : d_ram_hrdata;
  always @(posedge clock) begin
    if (reset) begin
      tohost_dphase <= 1'b0;
      tile_dphase   <= 1'b0;
      tohost_valid  <= 1'b0;
    end else begin
      tohost_valid <= 1'b0;
      if (d_hready) begin
        if (tohost_dphase) begin
          tohost_valid <= 1'b1;
          tohost_data  <= d_hwdata;
        end
        tohost_dphase <= d_htrans[1] &&  d_hwrite && (d_haddr == TOHOST_ADDR);
        tile_dphase   <= d_htrans[1] && !d_hwrite && (d_haddr == TILE_ID_ADDR);
      end
    end
  end
''' % (TOHOST_ADDR, TILE_ID_ADDR)


class GenError(Exception):
    pass


def edit(text, title, pat, repl, kind='literal'):
    if kind == 'literal':
        n = text.count(pat)
        if n == 1:
            return text.replace(pat, repl)
    else:
        rx = re.compile(pat, re.S)
        n = len(rx.findall(text))
        if n == 1:
            return rx.sub(lambda m: m.expand(repl) if kind == 'regex' else repl, text)
    raise GenError('%s: expected 1 match of %r in the template, found %d (not the pinned soc.tmpl.v?)' % (title, pat, n))


def read_hex(path):
    return [int(l, 16) for l in open(path).read().split()]


def fold(words, depth, what):
    out = [0] * depth
    owner = [None] * depth
    for i, w in enumerate(words):
        if w == 0:
            continue
        j = i % depth
        if owner[j] is not None:
            raise GenError('%s: words %d and %d both fold to %d at depth %d' % (what, owner[j], i, j, depth))
        out[j], owner[j] = w, i
    return out


def write_hex(path, words):
    with open(path, 'w', newline='\n') as f:
        for w in words:
            f.write('%08x\n' % w)


def main(argv=None):
    ap = argparse.ArgumentParser(description='GPU-farm Hazard3 SoC from upstream soc.tmpl.v')
    ap.add_argument('--template', required=True)
    ap.add_argument('--elf', required=True)
    ap.add_argument('--i-depth', type=int, required=True)
    ap.add_argument('--d-depth', type=int, required=True)
    ap.add_argument('--d-preload', choices=('none', 'elf'), default='none')
    ap.add_argument('--regfile-reset', type=int, choices=(0, 1), default=0)
    ap.add_argument('-o', '--output', required=True)
    a = ap.parse_args(argv)
    try:
        for d, nm in ((a.i_depth, 'i-depth'), (a.d_depth, 'd-depth')):
            if d < 4 or d & (d - 1):
                raise GenError('--%s must be a power of two >= 4' % nm)
        stem = os.path.splitext(os.path.abspath(a.output))[0]
        dense_i, dense_d = stem + '_i_dense.hex', stem + '_d_dense.hex'
        r = subprocess.run(['python3', ELF2HEX, '--images', a.elf, dense_i, dense_d],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        if r.returncode:
            raise GenError('elf2hex failed: %s%s' % (r.stdout, r.stderr))
        reset_vector = int(re.search(r'reset_vector=(0x[0-9a-fA-F]+)', r.stdout).group(1), 16)
        i_words = fold(read_hex(dense_i), a.i_depth, 'i_ram')
        i_hex = stem + '_i_ram.hex'
        write_hex(i_hex, i_words)
        d_hex = ''
        if a.d_preload == 'elf':
            d_hex = stem + '_d_ram.hex'
            write_hex(d_hex, fold(read_hex(dense_d), a.d_depth, 'd_ram'))
        os.remove(dense_i); os.remove(dense_d)

        t = open(a.template).read()
        t = edit(t, 'ports', 'output reg finished);',
                 'output reg finished,\n           input [31:0] tile_id,\n'
                 '           output reg tohost_valid,\n           output reg [31:0] tohost_data);')
        t = edit(t, 'reset vector', '${reset_vector}', str(reset_vector))
        if a.regfile_reset:
            t = edit(t, 'RESET_REGFILE', 'localparam RESET_REGFILE = 0;', 'localparam RESET_REGFILE = 1;')
        t = edit(t, 'i_ram depth', r'(ahb_sync_sram #\(\s*\.DEPTH\()1 << 24(\),(?:(?!ahb_sync_sram).)*?\) i_ram \()',
                 r'\g<1>%d\g<2>' % a.i_depth, 'regex')
        t = edit(t, 'd_ram depth', r'(ahb_sync_sram #\(\s*\.DEPTH\()1 << 24(\),(?:(?!ahb_sync_sram).)*?\) d_ram \()',
                 r'\g<1>%d\g<2>' % a.d_depth, 'regex')
        t = edit(t, 'i_ram preload', '${i_ram_preload}', i_hex)
        t = edit(t, 'd_ram preload', '${d_ram_preload}', d_hex)
        t = edit(t, 'd_ram rdata', '.ahbls_hrdata(d_hrdata)', '.ahbls_hrdata(d_ram_hrdata)')
        t = edit(t, 'd_ram rdata wire', '  wire [W_DATA-1:0]  d_hrdata;\n',
                 '  wire [W_DATA-1:0]  d_hrdata;\n  wire [W_DATA-1:0]  d_ram_hrdata;   // farm: RAM data before the TILE_ID mux\n')
        t = edit(t, 'hierarchical finished', r'`ifndef VERILATOR\n.*?finished <= 1\'b1;\n',
                 '  // farm: upstream\'s hierarchical finished (core.core.fd_cir == EBREAK) removed; the\n'
                 '  // run ends on the TOHOST DONE marker.  finished stays a port, 0.\n', 'regex_text')
        t = edit(t, 'farm logic', r'\nendmodule\s*$', '\n' + FARM_LOGIC + 'endmodule\n', 'regex_text')
        hdr = ('// GENERATED by bfit gpu/hazard3/gen_farm_soc.py from upstream soc.tmpl.v\n'
               '// (verilator-hazard3-mandelbrot-testbench, Apache License 2.0); see that script\n'
               '// for the list of changes.  elf=%s reset_vector=0x%08x i_depth=%d d_depth=%d\n'
               '// d_preload=%s RESET_REGFILE=%d TOHOST=0x%08x TILE_ID=0x%08x\n'
               % (os.path.basename(a.elf), reset_vector, a.i_depth, a.d_depth, a.d_preload,
                  a.regfile_reset, TOHOST_ADDR, TILE_ID_ADDR))
        with open(a.output, 'w', newline='\n') as f:
            f.write(hdr + t)
        print('gen_farm_soc: %s reset_vector=0x%08x i_ram %d words (%d nonzero) d_ram %d words preload=%s'
              % (os.path.basename(a.output), reset_vector, a.i_depth, sum(1 for w in i_words if w),
                 a.d_depth, a.d_preload))
        return 0
    except (GenError, OSError, AttributeError) as e:
        print('gen_farm_soc: %s' % e, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
