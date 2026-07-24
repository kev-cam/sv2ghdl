#!/usr/bin/env python3
"""Verify a packed (l3dw) module against its logic3d original, and remember.

    verify_l3dw.py <original.vhd> <packed.vhd> <module> --cache C [--cycles N]

For a bitwise module whose ports are all logic3d_vector buses, generate two
self-checking testbenches driven by the SAME LFSR stimulus -- one on the
logic3d original, one on the l3dw packed form -- each folding its outputs'
value plane into a 64-bit checksum. If the checksums match, the guess is
correct: promote the module's cache entry pending->verified and record the
measured speedup (packed run time / original run time). If they differ, mark it
rejected so it is never packed again. Repeated simulations thus converge on the
validated packing set.
"""
import re, sys, json, os, subprocess, time

NVC = "/usr/local/src/nvc-build/bin/nvc"
LIB = "/usr/local/src/nvc-build/lib"

def ports_of(text, mod):
    m = re.search(r"\bentity\s+%s\s+is\b(.*?)\bend\s+entity\b" % re.escape(mod),
                  text, re.I | re.S)
    body = m.group(1)
    pm = re.search(r"port\s*\((.*)\)\s*;", body, re.I | re.S)
    ports = []
    for decl in pm.group(1).split(";"):
        dm = re.match(r"\s*([\w,\s]+):\s*(in|out|inout)\s+logic3d_vector\s*\(\s*(\d+)\s+downto\s+(\d+)\s*\)",
                      decl, re.I | re.S)
        if not dm: continue
        names = [n.strip() for n in dm.group(1).split(",")]
        d = dm.group(2).lower(); w = int(dm.group(3)) - int(dm.group(4)) + 1
        for n in names: ports.append((n, d, w))
    return ports

def gen_tb(mod, ports, cycles, packed):
    ins  = [p for p in ports if p[1] == "in"]
    outs = [p for p in ports if p[1] == "out"]
    L, w = [], lambda s: L.append(s)
    w("library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;")
    w("library sv2vhdl; use sv2vhdl.logic3d_types_pkg.all;")
    if packed: w("use sv2vhdl.logic3dw_pkg.all;")
    w("use std.env.stop; use std.textio.all;")
    ent = "%s_%s_tb" % (mod, "l3dw" if packed else "l3d")
    w("entity %s is end entity;" % ent)
    w("architecture t of %s is" % ent)
    def vtype(width):
        if packed: return "l3dw_vector(%d downto 0)" % ((width + 7)//8 - 1)
        return "logic3d_vector(%d downto 0)" % (width - 1)
    for n, d, width in ports:
        w("  signal %s : %s;" % (n, vtype(width)))
    w("begin")
    conns = ", ".join("%s => %s" % (n, n) for n, _, _ in ports)
    w("  dut: entity work.%s port map (%s);" % (mod, conns))
    w("  process")
    w("    variable lfsr : unsigned(31 downto 0) := x\"1234abcd\";")
    w("    variable chk  : unsigned(63 downto 0) := (others => '0');")
    w("    variable vw   : integer; variable l : line;")
    w("  begin")
    w("    for k in 1 to %d loop" % cycles)
    # drive inputs: identical per-wire LFSR order (wire 0..W-1) in both reps
    for n, d, width in ins:
        w("      vw := 16#FF00#;")
        w("      for i in 0 to %d loop" % (width - 1))
        w("        lfsr := lfsr(30 downto 0) & (lfsr(31) xor lfsr(21) xor lfsr(1) xor lfsr(0));")
        if packed:
            w("        if lfsr(0)='1' then vw := vw + 2**(i mod 8); end if;")
            w("        if (i mod 8 = 7) or (i = %d) then" % (width - 1))
            w("          %s(i/8) <= l3dw(vw); vw := 16#FF00#;" % n)
            w("        end if;")
        else:
            w("        %s(i) <= L3D_1 when lfsr(0)='1' else L3D_0;" % n)
        w("      end loop;")
    w("      wait for 1 ns;")
    # fold outputs' value plane in the same wire order 0..W-1
    for n, d, width in outs:
        w("      for i in 0 to %d loop" % (width - 1))
        w("        chk := chk(62 downto 0) & chk(63);")
        if packed:
            w("        if ((integer(%s(i/8)) / (2**(i mod 8))) mod 2) = 1 then chk(0) := not chk(0); end if;" % n)
        else:
            w("        if is_one(%s(i)) then chk(0) := not chk(0); end if;" % n)
        w("      end loop;")
    w("    end loop;")
    w("    write(l, string'(\"CHK=\")); hwrite(l, std_logic_vector(chk)); writeline(output, l);")
    w("    stop;")
    w("  end process;")
    w("end architecture;")
    return "\n".join(L)

def run(dut_src, tb_src, tb_ent, work):
    subprocess.run("rm -rf %s" % work, shell=True)
    for f, s in (("dut.vhd", dut_src), ("tb.vhd", tb_src)):
        open("%s_%s" % (work, f), "w").write(s)
    a = subprocess.run([NVC, "-L", LIB, "--std=2040", "--work="+work, "-a",
                        work+"_dut.vhd", work+"_tb.vhd"], capture_output=True, text=True)
    if a.returncode: return None, a.stderr
    e = subprocess.run([NVC, "-L", LIB, "--std=2040", "--work="+work, "-e", tb_ent],
                       capture_output=True, text=True)
    if e.returncode: return None, e.stderr
    t0 = time.monotonic()
    r = subprocess.run([NVC, "-L", LIB, "--std=2040", "--work="+work, "-r", tb_ent],
                       capture_output=True, text=True)
    dt = time.monotonic() - t0
    m = re.search(r"CHK=([0-9A-Fa-f]+)", r.stdout + r.stderr)
    return (m.group(1) if m else None, dt), r.stderr

def sig_of_module(orig_text, mod):
    import hashlib
    m = re.search(r"\barchitecture\s+\w+\s+of\s+%s\s+is\b" % re.escape(mod), orig_text, re.I)
    end = re.search(r"\bend\s+architecture\b", orig_text[m.end():], re.I)
    body = orig_text[m.end(): m.end() + end.start()]
    ent = re.search(r"\bentity\s+%s\s+is\b(.*?)\bend\s+entity\b" % re.escape(mod),
                    orig_text, re.I | re.S).group(1)
    return "%s@%s" % (mod, hashlib.sha1((ent+"\0"+body).encode()).hexdigest()[:12])

def main():
    a = sys.argv[1:]
    cycles = 20000
    if "--cycles" in a:
        i = a.index("--cycles"); cycles = int(a[i+1]); del a[i:i+2]
    cache_path = None
    if "--cache" in a:
        i = a.index("--cache"); cache_path = a[i+1]; del a[i:i+2]
    orig, packed, mod = a[0], a[1], a[2]
    otext, ptext = open(orig).read(), open(packed).read()
    ports = ports_of(otext, mod)
    work = "/tmp/vl3dw_%s" % mod

    tb1 = gen_tb(mod, ports, cycles, packed=False)
    tb2 = gen_tb(mod, ports, cycles, packed=True)
    (r1, e1) = run(otext, tb1, "%s_l3d_tb" % mod, work + "_a")
    (r2, e2) = run(ptext, tb2, "%s_l3dw_tb" % mod, work + "_b")

    # A technique is only worth applying where it actually pays. Correctness is
    # necessary but not sufficient: a packing that verifies but is not faster is
    # recorded "nogain" and left alone, so we throw l3dw only at buses it helps.
    GAIN = float(os.environ.get("L3DW_MIN_SPEEDUP", "1.05"))
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    sig = sig_of_module(otext, mod)
    status, speed = "rejected", None
    if r1 is None or r1[0] is None:
        print("VERIFY %s: original failed to run: %s" % (mod, (e1 or '')[:200])); status="error"
    elif r2 is None or r2[0] is None:
        print("VERIFY %s: packed failed to run: %s" % (mod, (e2 or '')[:200]))
    elif r1[0].upper() != r2[0].upper():
        print("VERIFY %s: MISMATCH logic3d=%s l3dw=%s -> REJECT" % (mod, r1[0], r2[0]))
    else:
        speed = round(r1[1] / r2[1], 3) if r2[1] else None
        if speed is not None and speed >= GAIN:
            status = "verified"
            print("VERIFY %s: MATCH chk=%s  logic3d %.3fs -> l3dw %.3fs  (%sx) -> PACK"
                  % (mod, r1[0], r1[1], r2[1], speed))
        else:
            status = "nogain"    # correct but not worth packing
            print("VERIFY %s: MATCH but only %sx (< %.2f) -> leave as logic3d"
                  % (mod, speed, GAIN))

    ent = cache.get(sig, {"module": mod})
    ent.update({"status": status, "speedup": speed})
    cache[sig] = ent
    if cache_path:
        json.dump(cache, open(cache_path, "w"), indent=1, sort_keys=True)
    print("cache[%s] = %s" % (sig, {"status": status, "speedup": speed}))
    sys.exit(0 if status == "verified" else 1)

main()
