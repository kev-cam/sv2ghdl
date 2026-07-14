#!/usr/bin/env python3
"""
verilator_ref.py -- the ivtest "Verilator value-reference".

For each ivtest, run three engines fresh and classify the relationship:

  * iverilog (native Icarus, build-area)  -- the 4-state reference (gold generator)
  * shim     (iverilog-sv2ghdl + vvp-sv2ghdl, the nvc 3D-logic path)
  * verilator (2-state, the value oracle)

The point is NOT to treat Verilator as ground truth -- `rop` shows that for
inherently-4-state tests Verilator degenerates (3'b10z -> 3'b000). Instead we
CLASSIFY each shim-vs-iverilog divergence by what Verilator says:

  AGREE            shim == iverilog             (no divergence; verilator skipped)
  VL_CONFIRMS_SHIM shim == verilator != iverilog (3D-logic matches 2-state;
                                                   iverilog is x-pessimistic -> EXPECTED/GOOD)
  VL_CONFIRMS_IVL  iverilog == verilator != shim (shim diverges from BOTH -> likely real bug)
  VL_DEGENERATE    verilator != shim and != iverilog (4-state-only test; VL can't arbitrate)
  VL_NOCOMPILE     verilator could not build/run this test
  SHIM_NO_OUTPUT   shim ran but produced no output where iverilog did (translation gap)
  SHIM_ERROR       shim failed to compile/run (not a value divergence)
  IVL_ERROR        native iverilog failed / produced no output (not usable as reference)

Usage:
  verilator_ref.py [--jobs N] [--limit N] [--tests name,name] [--out report.json]
                   [--manifest regress-vlg.list,...] [--verbose]
"""
import argparse, concurrent.futures as cf, json, os, re, shutil, subprocess, sys, tempfile

IVTEST   = "/usr/local/src/iverilog/ivtest"
IVLTESTS = os.path.join(IVTEST, "ivltests")
VL       = "/usr/local/src/verilator-build/dest/usr/local/bin/verilator"
SHIM_ENV = dict(os.environ)
SHIM_ENV["PATH"] = ("/usr/local/src/nvc-build/bin:/usr/local/src/sv2ghdl/bin:"
                    "/usr/local/src/iverilog/_install/bin:/usr/bin:/bin:" + SHIM_ENV.get("PATH",""))
SHIM_ENV["PYTHONPATH"] = "/usr/local/src/nvc/lib/sv2vhdl:" + SHIM_ENV.get("PYTHONPATH","")
IVERILOG = "/usr/local/src/iverilog/_install/bin/iverilog"
VVP      = "/usr/local/src/iverilog/_install/bin/vvp"

MANIFESTS = ["regress-vvp.list","regress-sv.list","regress-vlg.list",
             "regress-fsv.list","regress-ivl1.list","regress-synth.list"]

# ---- output normalization -------------------------------------------------
# Lines emitted by the shim/verilator epilogue that are not part of the DUT's
# displayed values.
_STRIP = [
    re.compile(r'^FINISH called\s*$'),
    re.compile(r'^- '),                        # verilator banner ("- Simulation Report" etc.)
    re.compile(r'^\s*$'),                       # blank
    re.compile(r'.*\$finish.*', re.I),
]
def normalize(text):
    out = []
    for ln in text.splitlines():
        ln = ln.rstrip()
        if any(p.match(ln) or p.search(ln) for p in _STRIP):
            continue
        out.append(ln)
    return out

# Verdict lines (self-check) -- used to tell "value matches, only verdict differs"
_VERDICT = re.compile(r'^\s*(PASSED|FAILED|All tests passed|ERROR\b).*', re.I)
def value_lines(lines):
    return [l for l in lines if not _VERDICT.match(l)]

# ---- engine runners -------------------------------------------------------
def run(cmd, cwd, timeout, env=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, timeout=timeout, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return p.returncode, p.stdout.decode('utf-8','replace'), p.stderr.decode('utf-8','replace')
    except subprocess.TimeoutExpired:
        return 124, "", "TIMEOUT"
    except Exception as e:
        return 125, "", str(e)

def run_iverilog(src, args, w):
    rc,o,e = run([IVERILOG]+args+["-o","v",src], w, 120)
    if rc != 0: return None, "compile: "+e[-400:]
    rc,o,e = run([VVP,"v"], w, 60)
    if rc != 0 and not o: return None, "run: "+e[-400:]
    return o, None

def run_shim(src, args, w):
    rc,o,e = run(["iverilog-sv2ghdl"]+args+["-o","vsim",src], w, 300, SHIM_ENV)
    if rc != 0: return None, "compile: "+e[-400:]
    rc,o,e = run(["vvp-sv2ghdl","vsim"], w, 180, SHIM_ENV)
    if rc != 0 and not o: return None, "run: "+e[-400:]
    return o, None

def run_verilator(src, args, w, top):
    d = os.path.join(w,"vl")
    cmd = [VL,"--binary","--timing","--quiet","-Wno-fatal","-Mdir",d,"-o","sim"]
    # translate a couple of language-version args
    if "-g2005-sv" in args or "-g2009" in args or "-g2012" in args:
        cmd += ["--default-language","1800-2017"]
    if top: cmd += ["--top-module", top]
    cmd += ["-I"+IVLTESTS, src]
    # Verilator's generated makefile defaults OBJCACHE to ccache; force it empty
    # so a missing ccache doesn't make every compile fail (spurious VL_NOCOMPILE).
    vlenv = dict(os.environ); vlenv["OBJCACHE"] = ""
    rc,o,e = run(cmd, w, 180, vlenv)
    if rc != 0: return None, "compile"
    rc,o,e = run([os.path.join(d,"sim")], w, 60)
    if rc != 0 and not o: return None, "run"
    return o, None

def top_module(src):
    try:
        txt = open(src, errors='replace').read()
    except Exception:
        return None
    mods = re.findall(r'^\s*module\s+([A-Za-z_]\w*)', txt, re.M)
    return mods[0] if mods else None

# ---- per-test classification ---------------------------------------------
def _snap(res, nivl, nshim, nvl, n=12):
    res["ivl"]  = nivl[:n]
    res["shim"] = nshim[:n]
    if nvl is not None: res["vl"] = nvl[:n]

def classify(test):
    tname, ty, args, src = test["name"], test["type"], test["args"], test["src"]
    res = {"name":tname, "type":ty}
    if not os.path.exists(src):
        res["class"]="MISSING_SRC"; return res
    w = tempfile.mkdtemp(prefix="vref_"+tname+"_")
    try:
        ivl, ierr = run_iverilog(src, args, w)
        if ivl is None:
            res["class"]="IVL_ERROR"; res["detail"]=ierr; return res
        nivl = normalize(ivl)
        if not nivl:
            res["class"]="IVL_ERROR"; res["detail"]="no output"; return res
        shim, serr = run_shim(src, args, w)
        if shim is None:
            res["class"]="SHIM_ERROR"; res["detail"]=serr; return res
        nshim = normalize(shim)
        if not nshim:
            res["class"]="SHIM_NO_OUTPUT"; return res      # translation gap, not a value divergence
        if nivl == nshim:
            res["class"]="AGREE"; return res
        # divergence -> consult verilator
        top = top_module(src)
        vl, verr = run_verilator(src, args, w, top)
        # record whether the divergence is verdict-only (value lines match iverilog)
        res["value_matches_ivl"] = (value_lines(nivl) == value_lines(nshim))
        if vl is None:
            res["class"]="VL_NOCOMPILE"; res["detail"]=verr
            _snap(res, nivl, nshim, None); return res
        nvl = normalize(vl)
        if nvl == nshim:
            res["class"]="VL_CONFIRMS_SHIM"
        elif nvl == nivl:
            res["class"]="VL_CONFIRMS_IVL"
        else:
            res["class"]="VL_DEGENERATE"
        _snap(res, nivl, nshim, nvl)
        return res
    finally:
        shutil.rmtree(w, ignore_errors=True)

# ---- manifest parsing -----------------------------------------------------
def parse_manifests(manifests, only=None):
    tests, seen = [], set()
    for m in manifests:
        path = os.path.join(IVTEST, m)
        if not os.path.exists(path): continue
        for line in open(path):
            line = line.split("#",1)[0].strip()
            if not line: continue
            f = line.split()
            if len(f) < 2: continue
            tname = f[0]
            if tname in seen: continue
            if f[1].endswith(".json"):
                try: d = json.load(open(os.path.join(IVTEST, f[1])))
                except Exception: continue
                ty = d.get("type","normal"); args = d.get("iverilog-args",[])
                srcdir = "ivltests"; source = d.get("source", tname+".v")
            else:
                # inline: name  type[,arg,arg]  srcdir
                parts = f[1].split(",")
                ty = parts[0]; args = parts[1:]
                srcdir = f[2] if len(f) > 2 else "ivltests"; source = tname+".v"
            if only and tname not in only: continue
            # only value/normal-ish tests
            if ty not in ("normal","normal-vlog95","RE"): continue
            src = os.path.join(IVTEST, srcdir, source)
            seen.add(tname)
            tests.append({"name":tname,"type":ty,"args":list(args),"src":src})
    return tests

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tests", default="")
    ap.add_argument("--manifest", default="")
    ap.add_argument("--out", default="/tmp/verilator_ref_report.json")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    only = set(x for x in a.tests.split(",") if x) or None
    mans = a.manifest.split(",") if a.manifest else MANIFESTS
    tests = parse_manifests(mans, only)
    if a.limit: tests = tests[:a.limit]
    print(f"[verilator_ref] {len(tests)} tests, jobs={a.jobs}", file=sys.stderr)
    results = []
    with cf.ProcessPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(classify, t): t["name"] for t in tests}
        done = 0
        for fu in cf.as_completed(futs):
            r = fu.result(); results.append(r); done += 1
            if a.verbose or r["class"] not in ("AGREE",):
                print(f"  {done}/{len(tests)} {r['name']:28s} {r['class']}", file=sys.stderr)
            elif done % 25 == 0:
                print(f"  ..{done}/{len(tests)}", file=sys.stderr)
    # summary
    from collections import Counter
    c = Counter(r["class"] for r in results)
    print("\n=== SUMMARY ===", file=sys.stderr)
    for k,v in sorted(c.items(), key=lambda kv:-kv[1]):
        print(f"  {k:20s} {v}", file=sys.stderr)
    json.dump({"summary":dict(c),"results":sorted(results,key=lambda r:(r['class'],r['name']))},
              open(a.out,"w"), indent=1)
    print(f"[verilator_ref] wrote {a.out}", file=sys.stderr)

if __name__ == "__main__":
    main()
