# Fused VeeR-EH2 --accel status (honest interim, 2026-08-06)

## Where it stands

- **Configuration**: whole-core VeeR-EH2 hello under nvc --accel, 14 merged
  chunks installed (`NVC_ACCEL_MERGE=1`, skips `ic_mem,ic_data`), warm
  two-tier cache. Every run in this document is a warm double-run with the
  install manifest verified (installs==14 both passes).
- **Correctness**: retire-stream byte-exact against the interp reference
  through cyc85 (~1090ns). Thread 1 starts on time, gated clocks sustain,
  reset captures land, waveform-class clock outputs publish real-time.
- **Residual: CLOSED (2026-08-07).** The same-ultimate-nexus publication
  fanout (a flop's dout feeds several port-connected rims; the chunk
  published one while 307 reader-bearing siblings starved at init-X;
  each is now an out_extra target of its ord, back-filled at install)
  restored the cyc86-89 postsync pause. Measured: 1100ns retire stream
  window-exact, 3000ns byte-exact against the full 125-line reference
  (diff=0), fused hello TEST_PASSED cycles=2519, suite 12/12. Parity
  measurement (#64) is unblocked.

## The residual, mechanized

The interp reference holds the rim plane X through the reset window and
mass-settles X->certain at the first clock edge under reset assertion
(35ns: ~103k events; S_RST_L is high 0-30ns, asserts 30-110ns). The fused
build publishes the same values certain at t=0 — hashes identical, wake
EVENTS gone. Comb islands (IFC) that wake on the settle events instead
capture X once around the 35ns fetch qualification and recirculate it
through miss-FSM feedback, and downstream control reads starved values.
(CORRECTION 2026-08-08, fixture-tested: the original "case-dispatch
fall-through on X" wording was wrong — VHDL cannot `case` over
logic3d_vector and the sv2vhdl `=` overload is value-plane, so translated
dispatch follows value bits. The operative mechanism was VALUE
starvation of never-published rims; the fanout fix and every measurement
stand unchanged.)

## Five refuted runtime repairs (all measured, all default-off knobs)

| approach | knob / commit | result |
|---|---|---|
| flat X-consistency sweep | NVC_ACCEL_XCONS (cc7fe7a53) | 183 -> 68,311 X explosion |
| topo-ordered X settle | NVC_ACCEL_XCONS (12e7ca849) | neutral: island X is flop-held |
| byte-equal deposit force-events | NVC_ACCEL_FORCEEV (cc7fe7a53) | neutral: readers recompute the same X |
| alias-extra region timing | landed rules (4959c5d33) | 30/14 -> 26/10, then plateau |
| reset-window publication hold | NVC_ACCEL_RST_HOLD (1d25f703d) | CORRECTED: neutral (30/14 = baseline). The commit message's "25 -> 5" was a measurement artifact — the probe ran outside the program.hex directory and the core booted empty |

Under the corrected protocol every knob measures NEUTRAL: the residual
is insensitive to publication timing altogether. That still points away
from runtime repairs and toward the boundary itself (emit-time
reduction; the task list carries this as the successor item), but the
earlier "bracket" argument — which leaned on the now-retracted
RST_HOLD catastrophe — is withdrawn with it.

## Numbers that stand today

- Interp VeeR hello: plain 11.0 cyc/s, sweep 10.4 cyc/s (valid,
  retire-exact); Verilator baseline 22,086 cyc/s (speedcmp.sh
  marginal-wall method).
- Fused accel (2026-08-07, retire-exact config): marginal 16.5 cyc/s vs
  interp's re-measured 12.3 — a real 1.34x where it counts. After the
  aj_uniquify_modules fix (038e1a83d: the transitive-hash fixpoint
  re-ran its strstr search every pass — 71% of warm startup), fused warm
  start dropped 266s -> 43.5s and fused hello now wins END-TO-END too
  (~178s vs interp 211s). The ~1,340x gap to Verilator is coverage/perf:
  chunks are <0.6% of steady-state sim time. Two interp-side levers
  have since landed: l3d_addsub word-packing (4d6af7710) and the
  T_DEPOSIT inline byte-equal gates (4435e2558: 93.1% of 77.26M deposit
  visits were no-ops; calls cut 14.3x, deposit_signal_impl off the
  top-10 profile, top steady symbol now ~3%). Protocol lesson from that
  campaign: lowering changes are invisible until prebuilt work
  libraries are RE-ELABORATED. Remaining levers: scheduler shares
  (~4.6%), coverage expansion. Correctness is no longer the blocker.
- Shipped accel-vs-interp on the 19-design accelbench: 17.3x geomean
  (accel_real_numbers_corrected), gate-protected by accel-gate.sh.

## Baseline correction (2026-08-07)

A measurement-protocol audit found the retire probe is only valid when
run from a directory containing program.hex (the tb readmem is
CWD-relative and FAILS WITH ONLY A WARNING — the core boots empty and
retires one bubble per 17 cycles). Re-measured under the clean protocol,
the standing fused baseline is retires=30 diff=14 (installs=14,
deterministic; interp = 26 retires, window-exact), and RST_HOLD/XCONS
are neutral against it. The earlier 25/9 figure belongs to a
measurement regime whose exact cache/source micro-state is no longer
reproducible.

## Reproduction

Retire probe: run veer_eh1_tb to 1100ns with the standard fused env
FROM A DIRECTORY CONTAINING program.hex (e.g. ~/accel_run), grep RETIRE,
diff against the first 22 lines of probe_ref3000. Always 2>&1 (notes go
to stderr); always double-run warm; always verify installs==14 AND
`grep -c 'could not open' log == 0` — a missing program.hex is a
warning, not an error, and silently voids the run.
