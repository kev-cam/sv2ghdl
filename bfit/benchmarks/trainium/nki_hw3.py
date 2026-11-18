#!/usr/bin/env python
"""Device-clock timing of the follow-up kernels (2026-11-18): sa3 (reference), sa3u (uncertainty plane), deep (L levels).

Same method as nki_sa_hw2.py: compile once, load once, check the output once against numpy, then time with the
device-side benchmark (NeuronCore clock, warm-up 2, R iterations).
Usage: nki_hw3.py [R] [--local] [--out DIR] [--cases sa3,sa3u,deep19,deep137,deep19w,sa3u8k]
"""
import os, sys, time, json, glob, shutil
from dataclasses import replace
import numpy as np
os.environ.setdefault("NEURON_LOGICAL_NC_CONFIG", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nki_sa, nki_sa2, nki_sa3, nki_sa3u, nki_deep
from nki.framework.compiled import compile_kernel_to_nir, StandaloneKernel
from nki.compiler.driver import _compile_bir_to_neff

flags = sys.argv[1:]
LOCAL = "--local" in flags
OUT = flags[flags.index("--out") + 1] if "--out" in flags else "/tmp/trn_hw3"
CASES = flags[flags.index("--cases") + 1].split(",") if "--cases" in flags else ["sa3", "sa3u", "deep19", "deep137"]
pos = [a for i, a in enumerate(flags) if not a.startswith("--") and (i == 0 or flags[i - 1] not in ("--out", "--cases"))]
R = int(pos[0]) if pos else 5
os.makedirs(OUT, exist_ok=True)
VERILATOR_1T, VERILATOR_8T = 29.94, 15.35        # us per cycle, tuned baseline of 2026-11-17 (N=128 systolic)
N = 128
rows = []


def est_latency(artdir):
    for p in glob.glob(os.path.join(artdir, "**", "global_metric_store.json"), recursive=True):
        try:
            v = json.load(open(p)).get("Sum", {}).get("backend", {}).get("PostSchedEstLatency")
            if v is not None:
                return v
        except Exception:
            pass
    return None


def compile_once(kernel, inputs, tag):
    k = kernel._to_subclass(StandaloneKernel)
    frontend = k._frontend_cls(enable_backend_opt=k._enable_backend_opt)
    art = os.path.join(OUT, "art_" + tag)
    shutil.rmtree(art, ignore_errors=True); os.makedirs(art)
    opts = replace(k._compile_opts(), artifacts_dir=art, output_path=os.path.join(art, "kernel.neff"))
    t0 = time.time()
    nir = compile_kernel_to_nir(k, inputs=inputs, compile_opts=opts, frontend=frontend, enable_cache=False)
    t1 = time.time()
    compiled = _compile_bir_to_neff(nir, opts, inputs)
    print(f"[{tag}] trace {t1 - t0:.1f} s, neuronx-cc {time.time() - t1:.1f} s, NEFF "
          f"{os.path.getsize(compiled.neff_path) / 1e6:.2f} MB, est {est_latency(art)}", flush=True)
    return compiled, art


def run_case(tag, kernel, inputs, cycles, checker, note):
    compiled, art = compile_once(kernel, inputs, tag)
    if LOCAL:
        rows.append((tag, cycles, None, None, None, "local", note)); return
    exec_inputs = compiled.prepare_inputs(inputs)
    outputs = compiled.prepare_outputs()
    t0 = time.time(); compiled.execute(exec_inputs, outputs); t_first = time.time() - t0
    ok = checker([np.asarray(v) for v in outputs.values()])
    bench = compiled.benchmark(warmup=2, iterations=R, **exec_inputs)
    ms, mn, mx = bench.latency * 1e3, bench.latency_min * 1e3, bench.latency_max * 1e3
    us = ms * 1e3 / cycles
    print(f"{tag:9s} cycles={cycles:6d} load+1st={t_first:6.2f}s device mean={ms:9.3f} ms min={mn:9.3f} max={mx:9.3f} "
          f"({us:8.3f} us/cycle) check={ok}  {note}", flush=True)
    rows.append((tag, cycles, ms, mn, mx, ok, note))
    try:
        shutil.copy(compiled.neff_path, os.path.join(OUT, tag + ".neff"))
    except Exception as e:
        print(f"[{tag}] neff copy failed: {e}", flush=True)


def case_sa3(M):
    T = M + 2 * N
    nki_sa.N, nki_sa.T = N, T; nki_sa2.M, nki_sa2.N, nki_sa2.T = M, N, T; nki_sa3.M, nki_sa3.N, nki_sa3.T = M, N, T
    rng = np.random.default_rng(1)
    A = rng.integers(-128, 128, size=(M, N), dtype=np.int64); W = rng.integers(-128, 128, size=(N, N), dtype=np.int64)
    stim_a, skew, wT = nki_sa2.make_inputs(A, W)
    gold = nki_sa.golden(stim_a, np.array([W[N - 1 - t] for t in range(N)]))
    chk = lambda outs: "OK" if np.array_equal(outs[0].T.astype(np.int64), gold) else "MISMATCH"
    return nki_sa3.sa3_kernel, {"skew": skew, "wT": wT}, T, chk, "2-state reference (4 instr/cycle)"


def case_sa3u(M):
    nki_sa3u.M, nki_sa3u.N, nki_sa3u.T = M, N, M + 2 * N
    inputs, ref = nki_sa3u.make_case()
    gold, ugold = nki_sa3u.golden_u(*ref)

    def chk(outs):
        v = [o for o in outs if o.dtype == np.int32][0].T
        u = [o for o in outs if o.dtype == np.int8][0].T
        ub, vb, nc, nu = nki_sa3u.check(v, u, gold, ugold)
        return "OK" if (ub == 0 and vb == 0) else f"MISMATCH(flags {ub}, values {vb})"
    return nki_sa3u.sa3u_kernel, inputs, M + 2 * N, chk, "value + uncertain plane (8 instr/cycle)"


def case_deep(levels, cycles):
    inputs = nki_deep.make_inputs()
    gold = nki_deep.golden(inputs, levels, cycles)
    chk = lambda outs: "OK" if np.array_equal(outs[0].astype(np.int64), gold) else "MISMATCH"
    return nki_deep.make_kernel(levels, cycles), inputs, cycles, chk, f"{levels} dependent levels per cycle"


BUILD = {
    "sa3":     lambda: case_sa3(256),
    "sa3u":    lambda: case_sa3u(256),
    "sa3u8k":  lambda: case_sa3u(8192),
    "deep19":  lambda: case_deep(19, 512),
    "deep137": lambda: case_deep(137, 128),
    "deep4":   lambda: case_deep(4, 1024),
}
for name in CASES:
    try:
        kernel, inputs, cycles, chk, note = BUILD[name]()
        run_case(name, kernel, inputs, cycles, chk, note)
    except Exception as e:
        print(f"{name}: FAILED {type(e).__name__}: {str(e)[:700]}", flush=True)
        rows.append((name, None, None, None, None, "FAILED", ""))

print("\ncase\tcycles\tdevice_mean_ms\tmin_ms\tmax_ms\tus_per_cycle\tvs_verilator_1T\tvs_8T\tcheck\tnote")
for tag, cyc, ms, mn, mx, ok, note in rows:
    us = (ms * 1e3 / cyc) if ms else None
    r1 = f"{VERILATOR_1T / us:.1f}x" if us and tag.startswith("sa3") else ""
    r8 = f"{VERILATOR_8T / us:.1f}x" if us and tag.startswith("sa3") else ""
    print("\t".join(str(x) for x in (tag, cyc, ms, mn, mx, None if us is None else round(us, 3), r1, r8, ok, note)))
