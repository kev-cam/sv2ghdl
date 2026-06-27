# coord-gate — coordinated multi-repo digital regression gate

`coord-gate.sh` runs the digital SV→VHDL→nvc regressions on the **currently
checked-out branch**, reports, and — only if clean — fast-forward-merges the
branch to `origin/<default>` across every repo it touches.

It exists because the RTLMeter/VeeR work lands as one **coordinated branch of
the same name across three repos at once**, which the built-in per-repo
`regress gate` (`lib/Regress/Gate.pm`) cannot express:

| repo     | file(s) changed                     | build needed                       |
|----------|-------------------------------------|------------------------------------|
| sv2ghdl  | `bin/sv-normalize`                  | none (perl)                        |
| iverilog | `tgt-vhdl/{cast,scope,stmt}.cc`     | `vhdl.tgt` (the `-tvhdl` target)   |
| nvc      | `lib/sv2vhdl/logic3d_types_pkg.vhd` | none — VHDL pkg, re-analyzed at run |

All three feed exactly one regression block: **`ivtest/iverilog-nvc`** (the
iverilog-sv2ghdl shim path — the only block that exercises sv-normalize *and*
tgt-vhdl *and* the sv2vhdl package). Every other block is unaffected *by
construction*: native Icarus (`ivtest/iverilog`, `sv-tests/iverilog`) never
runs `-tvhdl`; native VHDL/nvc (`ivtest/nvc-vhdl`, `nvc/regr`, `nvc/unit`) use
the unchanged nvc binary and don't touch the sv2vhdl shim package.

## Usage

```sh
cd /usr/local/src/sv2ghdl/regress

# 1. report only — run the gate block on the current branch, diff, no writes:
./coord-gate.sh

# 2. see the plan without running anything:
./coord-gate.sh --dry-run

# 3. gate AND merge if clean (the real landing command):
./coord-gate.sh --push
```

Options: `--branch NAME` (default: branch checked out in sv2ghdl),
`--block BLOCK` (default `ivtest/iverilog-nvc`), `--ref-run N` (default: most
recent prior run of the block), `--push` (FF-merge when clean), `--dry-run`.

Exit codes: `0` clean (merged if `--push`), `1` held (regressions), `2` setup
error.

## What it does

1. **Detect** which of the three repos carry `<branch>` and are ahead of their
   `origin/<default>` (fetches first; skips repos already merged).
2. **Run** `ivtest/iverilog-nvc --seq` on the *current build* → candidate run.
3. **Diff** candidate vs the reference run (per-test `pass→fail` /
   `fail→pass`). `pass→fail` is the only merge-blocking signal.
4. **Re-confirm** every `pass→fail` `--seq` and drop noise: a test that passes
   on rerun, or fails at ~timeout duration (≥29 s), is a **clock-jump
   artifact** of this dev container, not a regression.
5. **Verdict**:
   - **dirty** → print a `PROBLEMS` block ready to paste into the Simulator
     net-chat for the other Claude; merge nothing.
   - **clean + `--push`** → for each touched repo: tag `<branch>-pre-rebase`,
     **rebase onto `origin/<default>`** (origin routinely advances under us),
     verify our changed files are **byte-identical** before/after the rebase
     (hash check; mismatch ⇒ hold that repo), `git push` (FF-only — the server
     refuses a non-fast-forward; never `--force`), sync the local default
     branch, and attach + push a `git note` recording the gate result.

## Guardrails

- **FF-only.** Pushes are plain `git push origin <branch>:refs/heads/<default>`.
  A non-fast-forward is refused, not forced. Pre-rebase tips are tagged.
- **Rebase-preserve check.** Our files are SHA-256'd before/after the rebase;
  any change aborts that repo's push. (Proves the intervening origin commits
  were orthogonal — e.g. the EH2-build landing rebased cleanly past `bfit/`
  analog work in sv2ghdl and a `vvp/Makefile.in` flag commit in iverilog.)
- **No self-baseline.** Never regenerates gold/reference data. The reference is
  an existing recorded run; the diff is pass/fail deltas only. (See memory
  `feedback_no_self_baseline`.)
- **Clock-jump guard** (≥29 s FAIL == noise) and **mass-regression guard**
  (>30 `pass→fail` ⇒ held as systemic) — see memory
  `regress_clock_jump_timeouts`.

## Worked example — EH2-build landing (2026-06-26)

Branch `rtlmeter-eh2-build` (sv-normalize multi-dim dynamic-index + iverilog
tgt-vhdl width/LPM/scalar→vector + nvc sv2vhdl logic3d overloads). Candidate
run #81 vs reference #78 on `ivtest/iverilog-nvc`: **0 pass→fail, +12 fixes**
(821 vs 809 pass). Three `normal` tests (`br_gh209`, `fdisplay2`, `sp2`) showed
as count-only deltas — pre-existing gold-diff failures (sim emits no output →
`Error: unable to open work/X for reading`, which the Ivtest adapter doesn't
record as a result row); already failing in #78, no pass lost. Merged:
sv2ghdl `6c95e01`→main, iverilog `8e48e5c41`→main, nvc `b8aa6b9f1`→master
(sv2ghdl + iverilog rebased onto advanced origins first).
