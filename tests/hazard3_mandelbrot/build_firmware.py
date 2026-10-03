#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
#
# build_firmware.py - rebuild the committed firmware images and golden outputs
# of the hazard3 regression variants, and the manifest variants.json.
#
# For each variant in VARIANTS below:
#   1. RISC-V firmware: the UNCHANGED upstream sw/mandelbrot.c, compiled with
#      clang/lld as upstream's sw/Makefile compiles it, with fw/rv32_tohost.c
#      force-included in place of sw/rv32.c, -DSIZE_LOG2/-DMAX_ITERS, and
#      linked at address 0 (-Wl,--image-base=0) so the dense preload images
#      stay small;
#   2. preload images variants/<name>/{i_ram,d_ram}.hex, written by elf2hex.py
#      (what upstream's load_elf.py computes);
#   3. golden output variants/<name>/golden.tohost: the same mandelbrot.c
#      compiled natively with gcc and fw/native_tohost.c, at -O0 and at -O2
#      (the two must agree), run on the host;
#   4. the variant's RAM depths, reset vector, cycle cap and file hashes in
#      variants.json.
# A variant's ref_cycles (the cycle count every simulator must reproduce) is
# kept when its preload images are unchanged and cleared otherwise; record it
# again from a run of the hazard3/verilator and hazard3/iverilog blocks.
#
# Needs: clang (riscv32 target) + ld.lld (clang-19/ld.lld-19 are looked for
# first), gcc, and the upstream checkout (setup.sh).
#
# Usage: build_firmware.py [--upstream DIR] [--keep DIR] [--only NAME ...]
#        build_firmware.py --set-ref NAME=CYCLES ...   (record ref_cycles only)
#
# Python 3.9+, standard library only.

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True      # no __pycache__ in the source tree
import elf2hex   # noqa: E402
import gen_soc   # noqa: E402

# The variants, smallest first. The firmware spends ~49 cycles per Mandelbrot
# iteration and ~42 per pixel, so MAX_ITERS is kept low enough for the runs
# to stay short under iverilog/vvp and the translated (vamos/nvc) simulation;
# README.md lists the measured cycle counts and run times.
VARIANTS = [
    {"name": "mandel8_i16", "size_log2": 3, "max_iters": 16},
    {"name": "mandel16_i8", "size_log2": 4, "max_iters": 8},
    {"name": "mandel32_i4", "size_log2": 5, "max_iters": 4},
]

# upstream's own benchmark (main_verilator.cpp): mandelbrot.c's defaults
BENCH = {"size_log2": 10, "max_iters": 256}

# upstream sw/Makefile: CFLAGS + CFLAGS_RV32IMAC
UPSTREAM_CFLAGS = ["-fPIC", "-ffreestanding", "-nostdlib", "-O2",
                   "-fno-unroll-loops", "-fno-inline-functions"]
RV32_FLAGS = ["-target", "riscv32-unknown-unknown", "-march=rv32imac"]
LINK_FLAGS = ["-Wl,--image-base=0"]

# Room left above the image for the stack frames of main/mandelbrot/used_image.
STACK_SLACK = 256


class BuildError(Exception):
    pass


def run(cmd, **kw):
    try:
        return subprocess.run(cmd, check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, **kw)
    except FileNotFoundError:
        raise BuildError("not found: %s" % cmd[0])
    except subprocess.CalledProcessError as e:
        raise BuildError("%s failed (exit %d):\n%s%s"
                         % (" ".join(cmd), e.returncode, e.stdout, e.stderr))


def first_tool(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def version_line(cmd):
    try:
        out = subprocess.run(cmd + ["--version"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True).stdout
    except OSError:
        return "?"
    return out.strip().splitlines()[0] if out.strip() else "?"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def next_pow2(n):
    p = 1
    while p < n:
        p <<= 1
    return p


def default_upstream():
    pin = gen_soc.read_upstream_pin()
    name = pin.get("UPSTREAM_DIRNAME", "verilator-hazard3-mandelbrot-testbench")
    cands = [os.environ.get("HAZARD3_MANDELBROT_DIR"),
             os.path.join(os.environ.get("SV2GHDL_SRC_ROOT", "/usr/local/src"), name),
             os.path.join(os.path.expanduser("~"), name)]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "soc.tmpl.v")):
            return c
    return None


def check_pin(upstream):
    pin = gen_soc.read_upstream_pin()
    try:
        head = run(["git", "-C", upstream, "rev-parse", "HEAD"]).stdout.strip()
    except BuildError:
        print("note: %s is not a git checkout; cannot check the pinned commit" % upstream)
        return pin
    if head != pin["UPSTREAM_COMMIT"]:
        raise BuildError("%s is at %s, not the pinned %s (run setup.sh)"
                         % (upstream, head, pin["UPSTREAM_COMMIT"]))
    return pin


def tohost_addr_in_firmware():
    with open(os.path.join(HERE, "fw", "rv32_tohost.c")) as f:
        m = re.search(r"#define\s+TOHOST_ADDR\s+(0x[0-9a-fA-F]+)u?", f.read())
    if not m:
        raise BuildError("fw/rv32_tohost.c: no TOHOST_ADDR")
    return int(m.group(1), 16)


def check_golden(path, v):
    size = 1 << v["size_log2"]
    with open(path) as f:
        lines = f.read().splitlines()
    want = 2 + size * size + 2
    if len(lines) != want:
        raise BuildError("%s: %d lines, expected %d" % (path, len(lines), want))
    for ln in lines:
        if not re.match(r"^TOHOST [0-9a-f]{8}$", ln):
            raise BuildError("%s: bad line %r" % (path, ln))
    words = [int(ln.split()[1], 16) for ln in lines]
    if words[0] != size or words[1] != v["max_iters"] or words[-1] != 0xffffffff:
        raise BuildError("%s: bad header or DONE marker" % path)
    if any(w >> 31 for w in words[:-1]):
        raise BuildError("%s: a word before DONE has bit 31 set" % path)


def build_variant(v, a, tools, tmp, old):
    name = v["name"]
    size = 1 << v["size_log2"]
    src = os.path.join(a.upstream, "sw", "mandelbrot.c")
    defs = ["-DSIZE_LOG2=%d" % v["size_log2"], "-DMAX_ITERS=%d" % v["max_iters"]]
    vdir = os.path.join(HERE, "variants", name)
    os.makedirs(vdir, exist_ok=True)

    # 1. RISC-V firmware
    elf = os.path.join(tmp, name + ".elf")
    run([tools["clang"]] + UPSTREAM_CFLAGS + RV32_FLAGS
        + ["--ld-path=" + tools["lld"]] + LINK_FLAGS + defs
        + ["--include", os.path.join(HERE, "fw", "rv32_tohost.c"), "-o", elf, src])

    # 2. preload images
    reset_vector, inst, data = elf2hex.build_images(elf)
    i_hex = os.path.join(vdir, "i_ram.hex")
    d_hex = os.path.join(vdir, "d_ram.hex")
    elf2hex.write_image(i_hex, 32, inst)
    elf2hex.write_image(d_hex, 32, data)

    # RAM depths: the instruction RAM holds the code plus a few zero words the
    # prefetcher may read past the end; the data RAM holds the ELF-header words
    # at the bottom, the TOHOST word, and the stack (mostly the SIZE x SIZE
    # image) that wraps from address 0 to the top.
    i_depth = next_pow2(max(inst) + 1 + 4)
    tohost = a.tohost_addr
    image_bytes = 4 * size * size
    d_depth = next_pow2((tohost + 4 + image_bytes + STACK_SLACK + 3) // 4)
    if max(data) >= tohost // 4:
        raise BuildError("%s: data image reaches the TOHOST word" % name)
    if tohost + 4 > d_depth * 4 - image_bytes - STACK_SLACK:
        raise BuildError("%s: TOHOST word inside the stack" % name)

    # 3. golden: native build at -O0 and -O2, the outputs must agree
    outs = []
    for opt in ("-O0", "-O2"):
        exe = os.path.join(tmp, "%s%s.native" % (name, opt))
        run([tools["cc"], opt] + defs
            + ["-include", os.path.join(HERE, "fw", "native_tohost.c"), "-o", exe, src])
        outs.append(run([exe]).stdout)
    if outs[0] != outs[1]:
        raise BuildError("%s: native -O0 and -O2 outputs differ" % name)
    golden = os.path.join(vdir, "golden.tohost")
    with open(golden, "w", newline="\n") as f:
        f.write(outs[0])
    check_golden(golden, v)

    if a.keep:
        os.makedirs(a.keep, exist_ok=True)
        shutil.copy(elf, a.keep)
        with open(os.path.join(a.keep, name + ".asm"), "w") as f:
            f.write(run([tools["objdump"], "-d", elf]).stdout if tools["objdump"] else "")

    # worst case: every pixel runs MAX_ITERS iterations; generous per-iteration
    # and per-pixel budgets (the measured cost is ~40-60 cycles/iteration).
    cycle_cap = size * size * (v["max_iters"] * 200 + 400) + 200000
    files = {"i_ram.hex": sha256(i_hex), "d_ram.hex": sha256(d_hex),
             "golden.tohost": sha256(golden)}
    ref = None
    prev = old.get(name)
    if prev and prev.get("files", {}).get("i_ram.hex") == files["i_ram.hex"] \
            and prev.get("files", {}).get("d_ram.hex") == files["d_ram.hex"]:
        ref = prev.get("ref_cycles")
    elif prev and prev.get("ref_cycles"):
        print("note: %s: preload images changed; ref_cycles cleared "
              "(record it again from the verilator and iverilog blocks)" % name)
    return {
        "name": name, "size_log2": v["size_log2"], "max_iters": v["max_iters"],
        "size": size, "reset_vector": "0x%08x" % reset_vector,
        "i_depth": i_depth, "d_depth": d_depth,
        "tohost_words": 2 + size * size + 2,
        "cycle_cap": cycle_cap, "ref_cycles": ref, "files": files,
    }


def bench_golden(a, tools, tmp, old_bench):
    """The image upstream's 1024x1024 benchmark must write: mandelbrot.c with
    its default SIZE_LOG2/MAX_ITERS, run natively, as the P6 output.ppm that
    main_verilator.cpp writes ("P6\\n<SIZE> <SIZE>\\n255\\n" + r,g,b bytes)."""
    src = os.path.join(a.upstream, "sw", "mandelbrot.c")
    exe = os.path.join(tmp, "bench.native")
    run([tools["cc"], "-O2", "-DSIZE_LOG2=%d" % BENCH["size_log2"],
         "-DMAX_ITERS=%d" % BENCH["max_iters"],
         "-include", os.path.join(HERE, "fw", "native_tohost.c"), "-o", exe, src])
    words = [int(ln[7:], 16) for ln in run([exe]).stdout.splitlines()]
    size = 1 << BENCH["size_log2"]
    if words[0] != size or len(words) != 2 + size * size + 2:
        raise BuildError("bench golden: unexpected native output")
    rgb = bytearray()
    for p in words[2:2 + size * size]:
        rgb += bytes(((p >> 16) & 0xff, (p >> 8) & 0xff, p & 0xff))
    ppm = b"P6\n%d %d\n255\n" % (size, size) + bytes(rgb)
    rec = dict(BENCH)
    rec["firmware"] = "sw/bin/mandelbrot_rv32imac (upstream's committed build)"
    rec["ppm_md5"] = hashlib.md5(ppm).hexdigest()
    rec["ref_cycles"] = (old_bench or {}).get("ref_cycles")
    return rec


def set_refs(pairs):
    """--set-ref NAME=CYCLES ...: record reference cycle counts, no rebuild."""
    mpath = os.path.join(HERE, "variants.json")
    with open(mpath) as f:
        manifest = json.load(f)
    recs = {v["name"]: v for v in manifest["variants"]}
    if "bench" in manifest:
        recs["bench"] = manifest["bench"]
    for p in pairs:
        name, _, val = p.partition("=")
        if name not in recs or not val.isdigit():
            raise BuildError("--set-ref %s: want NAME=CYCLES with NAME one of %s"
                             % (p, ", ".join(sorted(recs))))
        recs[name]["ref_cycles"] = int(val)
    with open(mpath, "w", newline="\n") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="rebuild the hazard3 variant firmware and goldens")
    ap.add_argument("--upstream", help="upstream checkout (default: as the harness finds it)")
    ap.add_argument("--keep", help="also keep the ELF files and disassembly here")
    ap.add_argument("--only", nargs="*", help="rebuild only these variants")
    ap.add_argument("--set-ref", nargs="+", metavar="NAME=CYCLES",
                    help="only record reference cycle counts in variants.json "
                         "(NAME = a variant or 'bench')")
    a = ap.parse_args(argv)
    try:
        if a.set_ref:
            set_refs(a.set_ref)
            return 0
        a.upstream = a.upstream or default_upstream()
        if not a.upstream:
            raise BuildError("no upstream checkout found; run setup.sh or pass --upstream")
        pin = check_pin(a.upstream)
        a.tohost_addr = tohost_addr_in_firmware()
        if a.tohost_addr != gen_soc.TOHOST_ADDR:
            raise BuildError("TOHOST_ADDR differs: fw/rv32_tohost.c 0x%x, gen_soc.py 0x%x"
                             % (a.tohost_addr, gen_soc.TOHOST_ADDR))
        tools = {
            "clang": first_tool("clang-19", "clang"),
            "lld": first_tool("ld.lld-19", "ld.lld"),
            "cc": first_tool("gcc", "cc"),
            "objdump": first_tool("llvm-objdump-19", "llvm-objdump"),
        }
        for k in ("clang", "lld", "cc"):
            if not tools[k]:
                raise BuildError("no %s found" % k)

        mpath = os.path.join(HERE, "variants.json")
        old = {}
        old_bench = None
        if os.path.isfile(mpath):
            with open(mpath) as f:
                prev = json.load(f)
            old = {v["name"]: v for v in prev.get("variants", [])}
            old_bench = prev.get("bench")
        todo = [v for v in VARIANTS if not a.only or v["name"] in a.only]
        out = []
        with tempfile.TemporaryDirectory(prefix="hazard3-fw-") as tmp:
            for v in VARIANTS:
                if v in todo:
                    rec = build_variant(v, a, tools, tmp, old)
                    print("%-14s reset=%s i_ram=%d d_ram=%d words=%d"
                          % (rec["name"], rec["reset_vector"], rec["i_depth"],
                             rec["d_depth"], rec["tohost_words"]))
                elif v["name"] in old:
                    rec = old[v["name"]]
                else:
                    raise BuildError("%s was never built; rebuild it too" % v["name"])
                out.append(rec)
            bench = bench_golden(a, tools, tmp, old_bench)
            print("bench          1024x1024 MAX_ITERS=256 output.ppm md5 %s" % bench["ppm_md5"])
        for name in sorted(set(old) - set(v["name"] for v in VARIANTS)):
            print("note: variant %s is no longer built; remove variants/%s" % (name, name))
        manifest = {
            "comment": "GENERATED by build_firmware.py; see README.md. The preload images "
                       "and golden outputs in variants/ are built from upstream's "
                       "sw/mandelbrot.c (Copyright 2026 Can Joshua Lehmann, Apache-2.0) "
                       "with fw/rv32_tohost.c and fw/native_tohost.c (variants/README). "
                       "ref_cycles: the cycle count (tb_hazard3.v's HAZARD3 DONE cycles=) "
                       "every simulator must reproduce; null = not recorded yet.",
            "upstream_commit": pin["UPSTREAM_COMMIT"],
            "hazard3_commit": pin["HAZARD3_COMMIT"],
            "tohost_addr": "0x%08x" % a.tohost_addr,
            "toolchain": {
                "clang": version_line([tools["clang"]]),
                "ld.lld": version_line([tools["lld"]]),
                "gcc": version_line([tools["cc"]]),
            },
            "rv32_command": "clang " + " ".join(UPSTREAM_CFLAGS + RV32_FLAGS)
                            + " --ld-path=<ld.lld> " + " ".join(LINK_FLAGS)
                            + " -DSIZE_LOG2=<n> -DMAX_ITERS=<n> --include fw/rv32_tohost.c"
                              " -o <elf> sw/mandelbrot.c",
            "native_command": "gcc -O0|-O2 -DSIZE_LOG2=<n> -DMAX_ITERS=<n> "
                              "-include fw/native_tohost.c -o <exe> sw/mandelbrot.c",
            "variants": out,
            "bench": bench,
        }
        with open(mpath, "w", newline="\n") as f:
            json.dump(manifest, f, indent=2)
            f.write("\n")
        return 0
    except (BuildError, elf2hex.ElfError, gen_soc.GenError, OSError) as e:
        print("build_firmware: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
