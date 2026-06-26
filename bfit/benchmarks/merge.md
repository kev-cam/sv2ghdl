# `bfit --merge` benchmark

`bfit merge` is the **analytical / lossless** path (distinct from `bfit front`,
which substitutes reduced-order behavioral macromodels). It merges directly-coupled
transistor structures into one component. Three recognizers (see `bfit/merge.py`):

| recognizer | structure | merged result |
|---|---|---|
| `recognize_cascode` | diode-connected series stack (internal seam node) | square-law → 1 element, **internal node eliminated** (`β_eff=kt·kb/(kt+kb)`); general (`--device-va`) → inline real device, node kept |
| `recognize_diff_pair` | matched pair, shared tail | 1 Verilog-A component (coupled Jacobian); tail kept |
| `recognize_xcoupled` | two inverters, each gate = other's output | 1 component, regenerative loop in one Jacobian |

All three are **exact**: validated bit-identical to the transistor reference
(cascode 147.0 µA / node gone; diff-pair 5.000/9.61302 µA balanced+steered;
latch bistable, both states).

## Measurement (`N=400 bash merge_bench.sh`, ngspice 45.2)

| case | nodes | timesteps | time(s) |
|---|---|---|---|
| cascode — native level-1 (2 dev + V1) | 801 | 408 | 0.15 |
| cascode — **OSDI/VA** (2 dev + V1)    | 801 | 408 | **0.79** |
| cascode — **merged** (1 elem, V1 gone)| **401** | 408 | **0.21** |
| latch — native level-1 (4 dev)        | 800 | 1611 | 0.98 |
| latch — merged (1 component)          | 800 | 1611 | 1.57 |

The cascode merge (1 native B-source, internal node gone) is **3.8× faster than
the OSDI/VA-device baseline** (0.79 → 0.21 s) — the in-scope comparison, since
`--merge` targets VA-described devices — but **slower than native level-1**
(0.15 s), which is unbeatable at this scale. The latch (no node to eliminate)
takes the same 1611 timesteps either way and the merged OSDI component is slower.
diff-pair is omitted from the timing table: it eliminates no node, so there is no
speed dimension (validated exact separately).

## Findings (honest)

- **`--merge` is not a blanket speedup.** Its speed benefit comes from
  **eliminating internal matrix nodes** (only the cascode/stack does this) and
  only pays off when **device evaluation is costly** — i.e. against OSDI /
  compiled-VA devices (the scope). There the cascode merge is **~2×** (halves the
  node count *and* the device-interface calls).
- **Against native level-1/BSIM C devices it is slower**: the merged element goes
  through a B-source / OSDI path that adds per-evaluation overhead the small node
  reduction can't overcome at this scale (it would at much larger node counts where
  the sparse-solve dominates).
- **diff-pair and latch eliminate no node** (the tail / storage nodes fan out), so
  there is no speed benefit — their value is **exactness + one portable coupled
  component**, not speed.
- The **convergence/stability win was measured and did NOT appear** on a
  well-behaved latch array (4-transistor and merged took the *same* 1611 timesteps;
  merged was slower). The coupled-Jacobian benefit only shows if the transistor
  version genuinely fails to converge (timestep collapse) — a smooth level-1 latch
  doesn't, so there is nothing to rescue.

**Bottom line:** `--merge` buys **exactness, portability, and node-elimination at
scale against costly device models**. Treat it as a structural/accuracy tool, not
a general accelerator; reach for it on cascode/stack-heavy VA-device designs.

Reproduce: `N=400 bash merge_bench.sh` (needs ngspice ≥ 45, openvaf, `bfit merge`).
