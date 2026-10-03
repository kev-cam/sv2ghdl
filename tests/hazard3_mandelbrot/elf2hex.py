#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
#
# elf2hex.py - ELF -> $readmemh preload images for the hazard3 SoC.
#
# A standard-library re-implementation of load_elf.py from
# verilator-hazard3-mandelbrot-testbench,
#   https://github.com/verijit/verilator-hazard3-mandelbrot-testbench
#   commit 9e768306a3cb03b9894aee609545fe3ed829d6be, file load_elf.py
#   Copyright (c) 2025 Can Joshua Lehmann
# licensed under the Apache License, Version 2.0 (LICENSES/Apache-2.0.txt at
# the root of this repository).
#
# CHANGED: the ELF file is parsed here with the standard library instead of
# pyelftools, so the harness needs no extra Python package; the template path
# is an option instead of always ./soc.tmpl.v; the work is split into
# functions that build_firmware.py reuses. What it computes is the same as
# load_elf.py:
#   - every program header is loaded in file order (PT_LOAD and also the
#     others, e.g. PT_PHDR and PT_RISCV_ATTRIBUTES, which load_elf.py's
#     iter_segments() also visits); a segment with PF_X goes to the
#     instruction RAM image, any other to the data RAM image;
#   - bytes [0, p_filesz) come from the file and [p_filesz, p_memsz) are 0;
#     each byte is OR-ed into the 32-bit little-endian word addr // 4;
#   - an image is written densely from word 0 to its highest word, one word
#     per line as 8 lower-case hex digits;
#   - in template mode the three ${...} placeholders of soc.tmpl.v are
#     replaced: ${reset_vector} by the decimal entry point, ${i_ram_preload}
#     and ${d_ram_preload} by the absolute paths of the two images, which are
#     written next to the output as <output minus .v>_{i,d}_ram_preload.hex.
#
# Usage (template mode, as upstream's Makefile runs load_elf.py):
#   elf2hex.py [--template soc.tmpl.v] ELF OUTPUT.v
# Usage (images only):
#   elf2hex.py --images ELF I_RAM.hex D_RAM.hex       (prints the entry point)
#
# Python 3.9+, standard library only.

import argparse
import os
import struct
import sys

PF_X = 0x1


class ElfError(Exception):
    pass


def read_elf(path):
    """Return (e_entry, [segment dict...]) for a 32- or 64-bit ELF file."""
    with open(path, "rb") as f:
        blob = f.read()
    if len(blob) < 16 or blob[:4] != b"\x7fELF":
        raise ElfError("%s: not an ELF file" % path)
    ei_class, ei_data = blob[4], blob[5]
    if ei_class not in (1, 2):
        raise ElfError("%s: unknown ELF class %d" % (path, ei_class))
    if ei_data not in (1, 2):
        raise ElfError("%s: unknown ELF data encoding %d" % (path, ei_data))
    end = "<" if ei_data == 1 else ">"
    if ei_class == 1:   # ELF32
        hdr = struct.unpack_from(end + "HHIIIIIHHHHHH", blob, 16)
        (_e_type, _e_machine, _e_version, e_entry, e_phoff, _e_shoff,
         _e_flags, _e_ehsize, e_phentsize, e_phnum, _e_shentsize,
         _e_shnum, _e_shstrndx) = hdr
        phfmt = end + "IIIIIIII"
    else:               # ELF64
        hdr = struct.unpack_from(end + "HHIQQQIHHHHHH", blob, 16)
        (_e_type, _e_machine, _e_version, e_entry, e_phoff, _e_shoff,
         _e_flags, _e_ehsize, e_phentsize, e_phnum, _e_shentsize,
         _e_shnum, _e_shstrndx) = hdr
        phfmt = end + "IIQQQQQQ"
    segs = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        if off + struct.calcsize(phfmt) > len(blob):
            raise ElfError("%s: program header %d is truncated" % (path, i))
        f = struct.unpack_from(phfmt, blob, off)
        if ei_class == 1:
            p_type, p_offset, p_vaddr, _p_paddr, p_filesz, p_memsz, p_flags, _p_align = f
        else:
            p_type, p_flags, p_offset, p_vaddr, _p_paddr, p_filesz, p_memsz, _p_align = f
        if p_offset + p_filesz > len(blob):
            raise ElfError("%s: segment %d runs past the end of the file" % (path, i))
        segs.append({
            "type": p_type, "flags": p_flags, "vaddr": p_vaddr,
            "filesz": p_filesz, "memsz": p_memsz,
            "data": blob[p_offset:p_offset + p_filesz],
        })
    return e_entry, segs


def build_images(path):
    """Return (reset_vector, inst_memory, data_memory) as load_elf.py builds them."""
    reset_vector, segs = read_elf(path)
    data_memory = {}
    inst_memory = {}
    for seg in segs:
        mem = inst_memory if seg["flags"] & PF_X else data_memory
        addr = seg["vaddr"]
        data = seg["data"]
        for it in range(seg["memsz"]):
            byte = data[it] if it < len(data) else 0
            index = addr // 4
            mem[index] = mem.get(index, 0) | (byte << ((addr % 4) * 8))
            addr += 1
    return reset_vector, inst_memory, data_memory


def write_image(path, width, data):
    """Write a dense $readmemh image (load_elf.py's init_memory)."""
    if not data:
        raise ElfError("%s: no bytes for this memory in the ELF file" % path)
    digits = width // 4
    with open(path, "w", newline="\n") as f:
        for index in range(max(data.keys()) + 1):
            f.write("%0*x\n" % (digits, data.get(index, 0)))


def fill_template(template_text, reset_vector, i_path, d_path):
    code = template_text
    code = code.replace("${reset_vector}", str(reset_vector))
    code = code.replace("${i_ram_preload}", i_path)
    code = code.replace("${d_ram_preload}", d_path)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="ELF -> $readmemh preload images (a stdlib load_elf.py)")
    ap.add_argument("--template", default="soc.tmpl.v",
                    help="SoC template (default: ./soc.tmpl.v, as load_elf.py)")
    ap.add_argument("--images", action="store_true",
                    help="write only the two images: ELF I_RAM.hex D_RAM.hex")
    ap.add_argument("elf_file")
    ap.add_argument("outputs", nargs="+")
    a = ap.parse_args(argv)
    try:
        reset_vector, inst_memory, data_memory = build_images(a.elf_file)
        if a.images:
            if len(a.outputs) != 2:
                ap.error("--images needs ELF I_RAM.hex D_RAM.hex")
            write_image(a.outputs[0], 32, inst_memory)
            write_image(a.outputs[1], 32, data_memory)
            print("reset_vector=0x%08x" % reset_vector)
            return 0
        if len(a.outputs) != 1:
            ap.error("template mode needs ELF OUTPUT.v")
        out = a.outputs[0]
        i_path = os.path.abspath(out.replace(".v", "_i_ram_preload.hex"))
        d_path = os.path.abspath(out.replace(".v", "_d_ram_preload.hex"))
        write_image(i_path, 32, inst_memory)
        write_image(d_path, 32, data_memory)
        with open(a.template, "r") as f:
            template = f.read()
        with open(out, "w", newline="\n") as f:
            f.write(fill_template(template, reset_vector, i_path, d_path))
        return 0
    except (ElfError, OSError) as e:
        print("elf2hex: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
