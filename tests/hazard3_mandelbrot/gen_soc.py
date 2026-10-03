#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# gen_soc.py - generate the portable hazard3 SoC of one regression variant
# from upstream's SoC template.
#
# The input is soc.tmpl.v of verilator-hazard3-mandelbrot-testbench,
#   https://github.com/verijit/verilator-hazard3-mandelbrot-testbench
#   commit 9e768306a3cb03b9894aee609545fe3ed829d6be (pinned in UPSTREAM in
#   this directory), file soc.tmpl.v
# which is licensed under the Apache License, Version 2.0
# (LICENSES/Apache-2.0.txt at the root of this repository). The output is a
# modified copy of it; every change is listed in CHANGES below and recorded in
# the header of the generated file. This script embeds the fragments of the
# template it matches; nothing else of the template is stored in this
# repository: the harness runs this script on the upstream checkout.
#
# Usage:
#   gen_soc.py --template <checkout>/soc.tmpl.v --variant NAME -o soc_portable.v
# The variant's reset vector, RAM depths and preload images come from
# variants.json and variants/NAME/ next to this script.
#
# Python 3.9+, standard library only.

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# The TOHOST word the snooper watches. Keep in step with TOHOST_ADDR in
# fw/rv32_tohost.c (build_firmware.py checks the two agree).
TOHOST_ADDR = 0x00001000

SNOOPER = r"""
  // -------------------------------------------------------------------------
  // TOHOST snooper (added by gen_soc.py). An AHB-Lite monitor on the data
  // port: a write transfer to TOHOST_ADDR is noted in its address phase, and
  // in its data phase (the next cycle in which d_hready is high) the write
  // data is captured into tohost_data while tohost_valid goes high for one
  // cycle. The data RAM still performs the write (it decodes only the low
  // address bits); the firmware uses nothing else at that RAM word.
  localparam [31:0] TOHOST_ADDR = 32'h%08x;

  reg tohost_dphase;
  initial begin
    tohost_dphase = 1'b0;
    tohost_valid  = 1'b0;
    tohost_data   = 32'h0;
  end

  always @(posedge clock) begin
    if (reset) begin
      tohost_dphase <= 1'b0;
      tohost_valid  <= 1'b0;
    end else begin
      tohost_valid <= 1'b0;
      if (d_hready) begin
        if (tohost_dphase) begin
          tohost_valid <= 1'b1;
          tohost_data  <= d_hwdata;
        end
        tohost_dphase <= d_htrans[1] && d_hwrite && (d_haddr == TOHOST_ADDR);
      end
    end
  end
""" % TOHOST_ADDR

FINISHED_NOTE = """\
  // (gen_soc.py) Removed here: upstream set 'finished' from a hierarchical
  // reference to the core's current-instruction register
  // (core.core.fd_cir == EBREAK), with a non-Verilator declaration of that
  // reference. The portable variant ends on the TOHOST DONE marker instead;
  // 'finished' stays a port and stays 0.
"""


def CHANGES(v, i_hex, d_hex):
    """(title, why, kind, pattern, replacement, expected match count).

    kind: "literal" (plain text), "regex" (the replacement may use \\g<n> group
    references) or "regex_text" (the replacement is inserted as it is)."""
    return [
        ("tohost ports",
         "new outputs tohost_valid and tohost_data, driven by the TOHOST snooper",
         "literal", "output reg finished);",
         "output reg finished,\n"
         "           output reg tohost_valid,\n"
         "           output reg [31:0] tohost_data);", 1),
        ("reset vector",
         "${reset_vector} filled with the ELF entry point, in decimal, as load_elf.py does",
         "literal", "${reset_vector}", str(v["reset_vector_int"]), 1),
        ("RESET_REGFILE = 1",
         "was 0. The firmware's _start never sets sp: under Verilator "
         "(--x-initial fast) the register file starts at 0, so sp = 0 and the "
         "stack wraps to the top of the data RAM; a 4-state simulator needs the "
         "register-file reset to start from the same state",
         "literal", "localparam RESET_REGFILE = 0;", "localparam RESET_REGFILE = 1;", 1),
        ("instruction RAM depth",
         "i_ram DEPTH 1 << 24 words -> %d words: room for this variant's code "
         "(the RAM decodes only the low address bits)" % v["i_depth"],
         "regex",
         r"(ahb_sync_sram #\(\s*\.DEPTH\()1 << 24(\),(?:(?!ahb_sync_sram).)*?\) i_ram \()",
         r"\g<1>%d\g<2>" % v["i_depth"], 1),
        ("data RAM depth",
         "d_ram DEPTH 1 << 24 words -> %d words: room for the ELF header words "
         "at the bottom, the TOHOST word at 0x%x and the stack (the image) that "
         "wraps from address 0 to the top" % (v["d_depth"], TOHOST_ADDR),
         "regex",
         r"(ahb_sync_sram #\(\s*\.DEPTH\()1 << 24(\),(?:(?!ahb_sync_sram).)*?\) d_ram \()",
         r"\g<1>%d\g<2>" % v["d_depth"], 1),
        ("instruction preload",
         "${i_ram_preload} filled with the committed image variants/%s/i_ram.hex" % v["name"],
         "literal", "${i_ram_preload}", i_hex, 1),
        ("data preload",
         "${d_ram_preload} filled with the committed image variants/%s/d_ram.hex" % v["name"],
         "literal", "${d_ram_preload}", d_hex, 1),
        ("hierarchical finish removed",
         "the non-Verilator (* keep, hierconn *) declaration of core.core.fd_cir and the "
         "always block that set finished from it are removed (no hierarchical "
         "references remain)",
         "regex_text", r"`ifndef VERILATOR\n.*?finished <= 1'b1;\n", FINISHED_NOTE, 1),
        ("TOHOST snooper",
         "an AHB-Lite write monitor on the data port for TOHOST 0x%08x, "
         "inserted before endmodule" % TOHOST_ADDR,
         "regex_text", r"\nendmodule\s*$", "\n" + SNOOPER + "endmodule\n", 1),
    ]


class GenError(Exception):
    pass


def apply_changes(text, changes):
    for n, (title, _why, kind, pat, repl, want) in enumerate(changes, 1):
        if kind == "literal":
            got = text.count(pat)
            if got == want:
                text = text.replace(pat, repl)
        else:
            rx = re.compile(pat, re.S)
            got = len(rx.findall(text))
            if got == want:
                if kind == "regex":
                    text = rx.sub(lambda m, r=repl: m.expand(r), text)
                else:
                    text = rx.sub(lambda m, r=repl: r, text)
        if got != want:
            raise GenError("change %d (%s): expected %d match(es) of %r in "
                           "soc.tmpl.v, found %d - the upstream template is not "
                           "the pinned one; update gen_soc.py"
                           % (n, title, want, pat, got))
    return text


def load_variant(name):
    with open(os.path.join(HERE, "variants.json")) as f:
        manifest = json.load(f)
    for v in manifest["variants"]:
        if v["name"] == name:
            v = dict(v)
            v["reset_vector_int"] = int(v["reset_vector"], 0)
            return manifest, v
    raise GenError("no variant %r in variants.json (have: %s)"
                   % (name, ", ".join(x["name"] for x in manifest["variants"])))


def read_upstream_pin():
    pin = {}
    with open(os.path.join(HERE, "UPSTREAM")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, val = line.split("=", 1)
                pin[k.strip()] = val.strip()
    return pin


def generate(template_path, name):
    manifest, v = load_variant(name)
    pin = read_upstream_pin()
    vdir = os.path.join(HERE, "variants", name)
    i_hex = os.path.join(vdir, "i_ram.hex")
    d_hex = os.path.join(vdir, "d_ram.hex")
    for p in (i_hex, d_hex):
        if not os.path.isfile(p):
            raise GenError("missing preload image %s (run build_firmware.py)" % p)
    if int(manifest.get("tohost_addr", "0"), 0) != TOHOST_ADDR:
        raise GenError("variants.json tohost_addr %s != gen_soc.py TOHOST_ADDR 0x%x"
                       % (manifest.get("tohost_addr"), TOHOST_ADDR))
    with open(template_path) as f:
        text = f.read()
    changes = CHANGES(v, i_hex, d_hex)
    body = apply_changes(text, changes)
    hdr = [
        "// soc_portable.v - GENERATED by sv2ghdl tests/hazard3_mandelbrot/gen_soc.py; do not edit.",
        "//",
        "// A modified copy of soc.tmpl.v from verilator-hazard3-mandelbrot-testbench,",
        "//   %s" % pin.get("UPSTREAM_URL", "?"),
        "//   commit %s, file soc.tmpl.v" % pin.get("UPSTREAM_COMMIT", "?"),
        "// Licensed under the Apache License, Version 2.0 (LICENSES/Apache-2.0.txt",
        "// in sv2ghdl). Changes made by gen_soc.py:",
    ]
    for n, (title, why, *_rest) in enumerate(changes, 1):
        hdr.append("//   %d. %s: %s" % (n, title, why))
    hdr += [
        "//",
        "// Variant %s: SIZE_LOG2=%d MAX_ITERS=%d reset_vector=%s i_ram=%d words "
        "d_ram=%d words TOHOST=0x%08x"
        % (v["name"], v["size_log2"], v["max_iters"], v["reset_vector"],
           v["i_depth"], v["d_depth"], TOHOST_ADDR),
        "// Compile with SIM defined: sram_sync.v then zero-fills both RAMs before",
        "// the preload, so they start as Verilator's zero-initialised memories do.",
        "",
    ]
    # Wrap long header lines at ~100 columns. A continuation line must not
    # start with the word "verilator": Verilator reads a comment that begins
    # with it as a metacomment (and rejects unknown ones).
    out = []
    for line in hdr:
        while len(line) > 100 and line.startswith("//"):
            cut = line.rfind(" ", 0, 100)
            while cut > 6 and line[cut + 1:].lower().startswith("verilator"):
                cut = line.rfind(" ", 0, cut)
            if cut <= 6:
                break
            out.append(line[:cut])
            line = "//      " + line[cut + 1:]
        out.append(line)
    return "\n".join(out) + "\n" + body


def main(argv=None):
    ap = argparse.ArgumentParser(description="generate the portable hazard3 SoC of a variant")
    ap.add_argument("--template", required=True, help="upstream soc.tmpl.v")
    ap.add_argument("--variant", required=True, help="variant name (variants.json)")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)
    try:
        text = generate(a.template, a.variant)
        with open(a.output, "w", newline="\n") as f:
            f.write(text)
    except (GenError, OSError, KeyError, ValueError) as e:
        print("gen_soc: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
