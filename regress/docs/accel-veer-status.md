# Fused VeeR-EH2 --accel status (honest interim, 2026-08-06)

## Where it stands

- **Configuration**: whole-core VeeR-EH2 hello under nvc --accel, 14 merged
  chunks installed (`NVC_ACCEL_MERGE=1`, skips `ic_mem,ic_data`), warm
  two-tier cache. Every run in this document is a warm double-run with the
  install manifest verified (installs==14 both passes).
- **Correctness**: retire-stream byte-exact against the interp reference
  through cyc85 (~1090ns). Thread 1 starts on time, gated clocks sustain,
  reset captures land, waveform-class clock outputs publish real-time.
- **Residual**: 25 retires vs 9 diff lines at 1100ns — the fused run misses
  the 4-cycle pipeline pause after the mhartstart CSR write (cyc86-89).
  Fused hello does NOT reach TEST_PASSED. Parity measurement (#64) stays
  blocked on this.

## The residual, mechanized

The interp reference holds the rim plane X through the reset window and
mass-settles X->certain at the first clock edge under reset assertion
(35ns: ~103k events; S_RST_L is high 0-30ns, asserts 30-110ns). The fused
build publishes the same values certain at t=0 — hashes identical, wake
EVENTS gone. Comb islands (IFC) that wake on the settle events instead
capture X once around the 35ns fetch qualification and recirculate it
through miss-FSM feedback; an X-marked CSR address later classifies the
mhartstart postsync write as ordinary via case-dispatch fall-through, so
the mandatory stall never issues. Values were right all along; the event
timeline was not.

## Five refuted runtime repairs (all measured, all default-off knobs)

| approach | knob / commit | result |
|---|---|---|
| flat X-consistency sweep | NVC_ACCEL_XCONS (cc7fe7a53) | 183 -> 68,311 X explosion |
| topo-ordered X settle | NVC_ACCEL_XCONS (12e7ca849) | neutral: island X is flop-held |
| byte-equal deposit force-events | NVC_ACCEL_FORCEEV (cc7fe7a53) | neutral: readers recompute the same X |
| alias-extra region timing | landed rules (4959c5d33) | 30/14 -> 26/10, then plateau |
| reset-window publication hold | NVC_ACCEL_RST_HOLD (1d25f703d) | retires 25 -> 5: the reset window carries real AXI/boot transactions |

The last two bracket the problem: restoring the events without the values
does nothing; restoring the value timeline starves 30ns of real behavior.
No runtime publication policy closes the hole. The settle cascade must
stay fully interp — the subtree emitter must stop internalizing nets that
have readers outside the reroute set (emit-time boundary reduction; the
task list carries this as the successor item).

## Numbers that stand today

- Interp VeeR hello: plain 11.0 cyc/s, sweep 10.4 cyc/s (valid,
  retire-exact); Verilator baseline 22,086 cyc/s (speedcmp.sh
  marginal-wall method).
- Fused accel: NOT eligible for a parity number until the residual lands
  (a run that misses a required stall is not the same workload).
- Shipped accel-vs-interp on the 19-design accelbench: 17.3x geomean
  (accel_real_numbers_corrected), gate-protected by accel-gate.sh.

## Reproduction

Retire probe: run veer_eh1_tb to 1100ns with the standard fused env, grep
RETIRE, diff against the first 22 lines of probe_ref3000 (see
regress/docs + the campaign log in the session memory for exact
commands). Always 2>&1 (notes go to stderr); always double-run warm;
always verify installs==14.
