#!/usr/bin/env python
"""Device-clock timing of the systolic-array kernels on Trainium (attempt 4 harness).

Attempt 3 showed that calling a @nki.jit kernel with numpy arrays (nki 0.7 "standalone" path) re-traces, recompiles,
reloads the NEFF and re-allocates device tensors on EVERY call, so wall-clock per call measured host overhead
(5-22 ms per simulated cycle), not the kernel.  This harness compiles once, loads once, checks the output once
against the numpy golden model, then times with nrtpy's device-side trace (CompiledKernel.benchmark, mode="device",
NeuronCore clock) and captures a neuron-profile NTFF for offline analysis.

Usage: nki_sa_hw2.py [N] [M list] [repeats] [--local] [--kernels sa,sa2,sa3] [--out DIR]
  --local : stop after the compile (no device): prints artifact sizes + the compiler's own latency estimate.
"""
import os, sys, time, json, glob, shutil, subprocess
from dataclasses import replace
import numpy as np
os.environ.setdefault("NEURON_LOGICAL_NC_CONFIG", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nki_sa, nki_sa2, nki_sa3
from nki.framework.compiled import compile_kernel_to_nir, StandaloneKernel
from nki.compiler.driver import _compile_bir_to_neff

args = [a for a in sys.argv[1:] if not a.startswith("--")]
flags = sys.argv[1:]
LOCAL = "--local" in flags
KERNELS = flags[flags.index("--kernels") + 1].split(",") if "--kernels" in flags else ["sa", "sa2", "sa3"]
if "--kernels" in flags:
    args = [a for a in args if a != flags[flags.index("--kernels") + 1]]
if "--out" in flags:
    args = [a for a in args if a != flags[flags.index("--out") + 1]]
OUT = flags[flags.index("--out") + 1] if "--out" in flags else os.environ.get("HW2_OUT", "/tmp/trn_hw2")
os.makedirs(OUT, exist_ok=True)
N = int(args[0]) if len(args) > 0 else 128
Ms = [int(x) for x in (args[1] if len(args) > 1 else "256,8192").split(",")]
R = int(args[2]) if len(args) > 2 else 5
WARM = int(os.environ.get("HW2_WARMUP", "2"))
VERILATOR_US = 28.6      # us per simulated cycle, single thread (tb_sa.cpp, N=128); 18.4 us with 8 threads
rng = np.random.default_rng(1)
W = rng.integers(-128, 128, size=(N, N), dtype=np.int64)
rows = []


def est_latency(artdir):
    """Post-schedule latency estimate of the compiler (PostSchedEstLatency) from the compile artifacts."""
    for p in glob.glob(os.path.join(artdir, "**", "global_metric_store.json"), recursive=True):
        try:
            j = json.load(open(p))
            v = j.get("Sum", {}).get("backend", {}).get("PostSchedEstLatency")
            if v is not None:
                return v
        except Exception:
            pass
    return None


def compile_once(kernel, inputs, tag):
    k = kernel._to_subclass(StandaloneKernel)
    frontend = k._frontend_cls(enable_backend_opt=k._enable_backend_opt)
    art = os.path.join(OUT, "art_" + tag)
    shutil.rmtree(art, ignore_errors=True)
    os.makedirs(art)
    opts = replace(k._compile_opts(), artifacts_dir=art, output_path=os.path.join(art, "kernel.neff"))
    t0 = time.time()
    nir = compile_kernel_to_nir(k, inputs=inputs, compile_opts=opts, frontend=frontend, enable_cache=False)
    t1 = time.time()
    compiled = _compile_bir_to_neff(nir, opts, inputs)
    t2 = time.time()
    neff = compiled.neff_path
    print(f"[{tag}] trace+MLIR {t1 - t0:.1f} s, neuronx-cc {t2 - t1:.1f} s, NEFF {os.path.getsize(neff) / 1e6:.2f} MB, "
          f"compiler est latency {est_latency(art)}", flush=True)
    return compiled, neff, art


def profile(tag, neff):
    """neuron-profile capture (separate process, loads the NEFF again) + summary, kept in OUT for offline analysis."""
    ntff = os.path.join(OUT, tag + ".ntff")
    r = subprocess.run(["neuron-profile", "capture", "-n", neff, "-s", ntff, "--ignore-exec-errors"],
                       capture_output=True, text=True, timeout=900)
    print(f"[{tag}] neuron-profile capture rc={r.returncode} {r.stderr.strip()[-300:]}", flush=True)
    if r.returncode != 0:
        return
    s = subprocess.run(["neuron-profile", "view", "-n", neff, "-s", ntff, "--output-format", "summary-json"],
                       capture_output=True, text=True, timeout=900)
    open(os.path.join(OUT, tag + ".summary.json"), "w").write(s.stdout)
    try:
        js = json.loads(s.stdout)
        src = js[0] if isinstance(js, list) else js
        keys = [k for k in src if any(w in k.lower() for w in ("total_time", "instruction", "dma", "utilization", "idle"))]
        print(f"[{tag}] summary: " + ", ".join(f"{k}={src[k]}" for k in keys[:14]), flush=True)
    except Exception as e:
        print(f"[{tag}] summary parse: {e}; raw {s.stdout[:400]!r} {s.stderr[-200:]!r}", flush=True)


def run_one(name, kernel, inputs, cycles, post, gold, M):
    tag = f"{name}_N{N}_M{M}"
    compiled, neff, art = compile_once(kernel, inputs, tag)
    est = est_latency(art)
    if LOCAL:
        rows.append((name, N, M, cycles, est, None, None, None, "local"))
        return
    exec_inputs = compiled.prepare_inputs(inputs)
    outputs = compiled.prepare_outputs()
    t0 = time.time()
    compiled.execute(exec_inputs, outputs)                   # NEFF load + first run
    t_first = time.time() - t0
    out = list(outputs.values())[0]
    ok = "n/a"
    if gold is not None:
        ok = "OK" if np.array_equal(post(out).astype(np.int64), gold) else "MISMATCH"
    bench = compiled.benchmark(warmup=WARM, iterations=R, **exec_inputs)      # device-clock timing
    dev_ms = bench.latency * 1e3
    dmin = bench.latency_min * 1e3
    dmax = bench.latency_max * 1e3
    us_cyc = dev_ms * 1e3 / cycles
    print(f"{name:4s} N={N} M={M:5d} cycles={cycles:6d} load+1st={t_first:6.2f}s  device mean={dev_ms:9.3f} ms "
          f"min={dmin:9.3f} max={dmax:9.3f}  ({us_cyc:8.3f} us/cycle = {cycles / (dev_ms * 1e-3):10.0f} cycles/s, "
          f"{VERILATOR_US / us_cyc:6.1f}x vs Verilator 1T)  est={est}  check={ok}", flush=True)
    rows.append((name, N, M, cycles, est, dev_ms, dmin, dmax, ok))
    try:
        shutil.copy(neff, os.path.join(OUT, tag + ".neff"))
        if os.environ.get("HW2_PROFILE", "1") != "0":
            profile(tag, neff)
    except Exception as e:
        print(f"[{tag}] profile step failed: {e}", flush=True)


for M in Ms:
    T = M + 2 * N
    nki_sa.N, nki_sa.T = N, T
    nki_sa2.M, nki_sa2.N, nki_sa2.T = M, N, T
    nki_sa3.M, nki_sa3.N, nki_sa3.T = M, N, T
    A = rng.integers(-128, 128, size=(M, N), dtype=np.int64)
    stim_a, skew, wT = nki_sa2.make_inputs(A, W)
    stim_w = np.array([W[N - 1 - t] for t in range(N)])
    gold = nki_sa.golden(stim_a, stim_w) if M <= 2048 else None
    a_in = np.ascontiguousarray(stim_a.T.astype(np.int8))
    w_in = np.ascontiguousarray(stim_w.reshape(1, N * N).astype(np.int8))
    cands = {
        "sa":  (nki_sa.sa_kernel,   {"stim_aT": a_in, "stim_w_flat": w_in}, T + N, lambda r: np.asarray(r)),
        "sa2": (nki_sa2.sa2_kernel, {"skew": skew, "wT": wT},               T,     lambda r: np.asarray(r).T),
        "sa3": (nki_sa3.sa3_kernel, {"skew": skew, "wT": wT},               T,     lambda r: np.asarray(r).T),
    }
    for name in KERNELS:
        if name == "sa2" and T * N > 150_000:
            print(f"sa2  N={N} M={M}: skipped (whole stimulus {T * N // 1024} KB/partition does not fit SBUF; use sa3)",
                  flush=True)
            continue
        kernel, inputs, cycles, post = cands[name]
        try:
            run_one(name, kernel, inputs, cycles, post, gold, M)
        except Exception as e:
            print(f"{name} N={N} M={M}: FAILED {type(e).__name__}: {str(e)[:600]}", flush=True)
            rows.append((name, N, M, cycles, None, None, None, None, "FAILED"))

print("\nname\tN\tM\tcycles\tcompiler_est\tdevice_mean_ms\tmin_ms\tmax_ms\tcheck")
for r in rows:
    print("\t".join(str(x) for x in r))
