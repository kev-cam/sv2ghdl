#!/usr/bin/env python3
"""Promote a VHDL design tree from std_logic/bit to the fork's 3D-Logic types.

    promote_3dlogic.py <src_dir> <dst_dir>

Walks <src_dir>, copies every file to <dst_dir> preserving structure, and rewrites
each .vhd/.vhdl file:

  * types      std_logic[_vector] / std_ulogic[_vector] / bit[_vector]  ->  logic3d[_vector]
  * literals   '0' '1' 'X' 'Z' 'U' 'L' 'H' 'W' '-'  ->  L3D_0 L3D_1 ...   (scalar logic values)
  * context    injects `library sv2vhdl; use sv2vhdl.logic3d_types_pkg.all;
                        use sv2vhdl.logic3d_vec_pkg.all;` and comments the
               ieee.std_logic_1164 use (types now come from the logic3d pkgs).

SCOPE / KNOWN LIMITS (this is a mechanical transform, not a typed compiler):
  * It does NOT rewrite gate operators. `a and b` on logic3d *scalars* has no
    operator overload (only l3d_and(a,b)), so a design that drives signals with
    bare `and/or/xor/not` will need those turned into l3d_* calls by hand. It is
    left alone on purpose because `and/or` on the *booleans* produced by `=`
    comparisons (the common FSM-condition case, e.g. b01) is already correct and
    must NOT be rewritten. Gate-level netlists are flagged (see --warn output).
  * Vector string literals ("0101") are reported, not converted.
  * numeric_std arithmetic on the old vectors is left as-is; logic3d_vec_pkg
    provides "+"/"-"/"=" on logic3d_vector, but wider numeric use needs review.

3D-Logic semantics deliberately differ from 01XZ, so a promoted design is NOT
expected to reproduce the std_logic checksum -- it is a separate engine/mode for
measuring the fork under its native type system.
"""
import os, re, sys, shutil

LIT = {"'0'": "L3D_0", "'1'": "L3D_1", "'X'": "L3D_X", "'Z'": "L3D_Z",
       "'U'": "L3D_U", "'L'": "L3D_L", "'H'": "L3D_H", "'W'": "L3D_W",
       "'-'": "L3D_X"}

# logic3d_types_pkg carries the types, l3d_* gates, L3D_* constants, the
# arithmetic / comparison operators and (now) the gate-operator overloads.
# logic3d_vec_pkg is an *uninstantiated generic* package (parameterised by WIDTH)
# and cannot be `use`d directly, so it is deliberately not injected.
USE_BLOCK = ("library sv2vhdl;\n"
             "use sv2vhdl.logic3d_types_pkg.all;\n")

def promote(text):
    warns = []
    # 1) types  (vector forms before scalar; word boundaries)
    for pat, rep in [
        (r"\bstd_ulogic_vector\b", "logic3d_vector"),
        (r"\bstd_logic_vector\b",  "logic3d_vector"),
        (r"\bstd_ulogic\b",        "logic3d"),
        (r"\bstd_logic\b",         "logic3d"),
        (r"\bbit_vector\b",        "logic3d_vector"),
        (r"\bbit\b",               "logic3d"),
    ]:
        text = re.sub(pat, rep, text, flags=re.I)
    # 2) scalar logic literals
    text = re.sub(r"'[01XZULHW-]'", lambda m: LIT.get(m.group(0).upper(), m.group(0)), text)
    # 3) context clause: comment std_logic_1164, inject logic3d uses once per file
    text = re.sub(r"(?im)^([ \t]*use\s+ieee\.std_logic_1164\.all\s*;)",
                  r"-- \1  -- (types promoted to 3D-Logic)", text)
    # inject after the first `library ieee;` or at the top of the first context clause
    if "logic3d_types_pkg" not in text:
        m = re.search(r"(?im)^[ \t]*library\s+ieee\s*;[ \t]*$", text)
        if m:
            text = text[:m.end()] + "\n" + USE_BLOCK + text[m.end():]
        else:
            text = USE_BLOCK + text
    # 4) flag likely gate-level operators on (now) logic3d signals: `<= ... and/or/xor ...`
    for m in re.finditer(r"(?im)<=\s*[^;]*\b(and|or|xor|nand|nor|xnor|not)\b[^;]*;", text):
        warns.append("gate operator in assignment (needs l3d_* by hand): " + m.group(0).strip()[:70])
    for m in re.finditer(r'"[01XZULHW-]{2,}"', text):
        warns.append("vector string literal not converted: " + m.group(0))
    return text, warns

def main():
    if len(sys.argv) != 3:
        sys.exit("usage: promote_3dlogic.py <src_dir> <dst_dir>")
    src, dst = sys.argv[1], sys.argv[2]
    nfiles = nwarn = 0
    for root, _, files in os.walk(src):
        rel = os.path.relpath(root, src)
        outdir = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(outdir, exist_ok=True)
        for fn in files:
            sp, dp = os.path.join(root, fn), os.path.join(outdir, fn)
            if fn.lower().endswith((".vhd", ".vhdl")):
                txt, warns = promote(open(sp, encoding="latin-1").read())
                open(dp, "w", encoding="latin-1").write(txt)
                nfiles += 1
                for w in warns:
                    print("  [warn] %s: %s" % (fn, w), file=sys.stderr); nwarn += 1
            else:
                shutil.copy2(sp, dp)
    print("promoted %d VHDL file(s) -> %s  (%d warning(s))" % (nfiles, dst, nwarn))

main()
