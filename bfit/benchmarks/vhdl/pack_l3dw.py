#!/usr/bin/env python3
"""Pack bitwise-dominated logic3d buses to the 3D-logic word (l3dw), with a
persistent record of which guesses were verified.

    pack_l3dw.py <in.vhd> [-o out.vhd] [--cache pack_l3dw_cache.json]

A post-translation pass over tgt-vhdl's emitted VHDL. For each architecture it
GUESSES whether the module is bitwise-dominated -- every logic3d_vector used
only in l3d_and/or/xor/not and whole-vector assignment, never indexed, sliced,
concatenated, compared or arithmetic'd. Such a module can be rewritten to the
packed word: logic3d_vector(N-1 downto 0) -> l3dw_vector(ceil(N/8)-1 downto 0),
l3d_* -> l3dw_*, L3D_{0,1,X} -> L3DW_{0,1,X}.

The guess is meant to be VERIFIED, and because most simulations get repeated,
the verdict is remembered. Each module is keyed by (name + content hash) in a
JSON cache with a status:
    pending   guessed packable this run, not yet verified
    verified  packed form matched the reference under simulation -> trust it
    rejected  packed form diverged (or heuristic said no) -> never pack
On a repeat run a `verified` module is packed with confidence and a `rejected`
one is left alone; only genuinely new/changed modules are guessed afresh. The
companion verify step (verify_l3dw.py) promotes pending->verified or ->rejected
after a differential run and records the measured speedup.

Whole-file gating: the file is packed only if every module is pack-or-verified,
so port boundaries stay consistent (no logic3d<->l3dw conversion needed).
"""
import re, sys, json, hashlib, os

DISQUAL_TOKENS = [
    r"\bl3d_to_unsigned\b", r"\bunsigned_to_l3d\b", r"\bto_l3d\b",
    r"\bl3d_index\b", r"\bl3d_resize\w*\b", r"\bl3d_to_signed\b",
    r"\bl3d_lt\w*\b", r"\bl3d_gt\w*\b", r"\bl3d_le\w*\b", r"\bl3d_ge\w*\b",
]

def ceil8(n): return (n + 7) // 8

def sig_of(ename, ent_txt, body):
    h = hashlib.sha1((ent_txt + "\0" + body).encode()).hexdigest()[:12]
    return "%s@%s" % (ename, h)

def is_packable(arch_body, entity_ports_text):
    scope = entity_ports_text + "\n" + arch_body
    if not re.search(r"\blogic3d_vector\b", scope, re.I):
        return False, "no logic3d_vector"
    for tok in DISQUAL_TOKENS:
        if re.search(tok, arch_body, re.I):
            return False, "uses " + tok.strip("\\b")
    if re.search(r"[)\w]\s+[-+*]\s+[\w(]", arch_body):
        return False, "arithmetic (+,-,*)"
    if re.search(r"[)\w]\s+&\s+[\w(]", arch_body):
        return False, "concatenation (&)"
    names = set(re.findall(r"signal\s+(\w+)\s*:\s*logic3d_vector", arch_body, re.I))
    names |= set(re.findall(r"(\w+)\s*:\s*(?:in|out|inout)\s+logic3d_vector",
                            entity_ports_text, re.I))
    for nm in names:
        if re.search(r"\b" + re.escape(nm) + r"\s*\(", arch_body):
            return False, "index/slice of " + nm
    return True, "bitwise-only"

def rewrite_width(m):
    hi, lo = int(m.group(1)), int(m.group(2))
    return "l3dw_vector(%d downto 0)" % (ceil8(hi - lo + 1) - 1)

def pack_text(text):
    text = re.sub(r"logic3d_vector\s*\(\s*(\d+)\s+downto\s+(\d+)\s*\)",
                  rewrite_width, text, flags=re.I)
    text = re.sub(r"\bl3d_(and|or|xor|not)\b", r"l3dw_\1", text)
    for k in ("0", "1", "X"):
        text = re.sub(r"\bL3D_%s\b" % k, "L3DW_%s" % k, text)
    if "logic3dw_pkg" not in text:
        text = re.sub(r"(use\s+sv2vhdl\.logic3d_types_pkg\.all\s*;)",
                      r"\1\nuse sv2vhdl.logic3dw_pkg.all;", text, count=1, flags=re.I)
    return text

def load_cache(path):
    if path and os.path.exists(path):
        try: return json.load(open(path))
        except Exception: pass
    return {}

def main():
    args = sys.argv[1:]
    out = cache_path = None
    for flag, setter in (("-o", "out"), ("--cache", "cache")):
        if flag in args:
            i = args.index(flag)
            if flag == "-o": out = args[i+1]
            else: cache_path = args[i+1]
            del args[i:i+2]
    src = args[0]
    text = open(src).read()
    cache = load_cache(cache_path)

    archs = list(re.finditer(r"\barchitecture\s+(\w+)\s+of\s+(\w+)\s+is\b", text, re.I))
    decisions = []          # (ename, sig, will_pack, status, reason)
    for m in archs:
        aname, ename = m.group(1), m.group(2)
        end = re.search(r"\bend\s+architecture\b", text[m.end():], re.I)
        body = text[m.end(): m.end() + (end.start() if end else len(text))]
        ent = re.search(r"\bentity\s+%s\s+is\b(.*?)\bend\s+entity\b" % re.escape(ename),
                        text, re.I | re.S)
        ent_txt = ent.group(1) if ent else ""
        sig = sig_of(ename, ent_txt, body)
        cached = cache.get(sig)
        st = cached["status"] if cached else None
        if st == "verified":                       # correct AND faster: trust it
            decisions.append((ename, sig, True, "verified", cached.get("reason", "")))
        elif st in ("rejected", "nogain", "error"):  # wrong, or not worth it
            decisions.append((ename, sig, False, st, cached.get("reason", "")))
        else:
            ok, why = is_packable(body, ent_txt)   # unseen/changed: guess
            decisions.append((ename, sig, ok, "pending" if ok else "skip", why))

    all_pack = bool(archs) and all(d[2] for d in decisions)
    if all_pack:
        text = pack_text(text)

    # record pending guesses so a later verify step can promote/reject them
    if cache_path:
        for ename, sig, will_pack, status, why in decisions:
            if status in ("pending",) and all_pack:
                cache.setdefault(sig, {"status": "pending", "module": ename,
                                       "reason": why, "speedup": None})
        json.dump(cache, open(cache_path, "w"), indent=1, sort_keys=True)

    sys.stderr.write("pack_l3dw: %s\n" % src)
    for ename, sig, will_pack, status, why in decisions:
        sys.stderr.write("  %-18s %-8s %s (%s)\n" %
                         (ename, status, "PACK" if will_pack else "skip", why))
    sys.stderr.write("  => %s\n" % ("PACKED whole file" if all_pack
                                    else "left as logic3d (not all modules packable)"))
    (open(out, "w") if out else sys.stdout).write(text)

main()
