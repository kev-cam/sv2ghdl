# nvc `--accel` regression testing

Every nvc VHDL test that **passes normal simulation** is retried with nvc's
acceleration engaged, and the accelerated results are logged **separately** so
an *accel-only* regression (a test that passes normally but breaks under
`--accel`) is visible.

## How it works

Two blocks over the same suite (`nvc-build/bin/run_regr`):

| block            | what it runs                                                        |
|------------------|---------------------------------------------------------------------|
| `nvc/regr`       | normal simulation, in a **clean** accel environment                 |
| `nvc/regr-accel` | gate on `nvc/regr`'s passers, re-run **only those** with `NVC_ACCEL=1` |

`nvc/regr-accel` is a two-pass adapter (`lib/Regress/Adapter/NvcNative.pm`):

1. **Gate** — run `run_regr` normally (clean env) and collect the tests that pass.
2. **Accel** — re-run *exactly* those passers with `NVC_ACCEL=1`.

A test that fails normal sim is **not** accelerated (it would only inherit the
failure), so the accel block's failures are genuine accel regressions. The
accel block's total therefore equals the normal-pass count.

## The enabling mechanism: `NVC_ACCEL`

`NVC_ACCEL` in the environment is the equivalent of nvc's `--accel` option
(auto compile + engage, via `accel_auto`) — set & non-empty & not `0`. Scripts
and `run_regr` need **no** modification; they just inherit the variable. This
is distinct from `NVC_USE_ACCEL`, which names a *prebuilt* `.so` for `accel_load`.

```sh
NVC_ACCEL=1 nvc -a foo.vhd && NVC_ACCEL=1 nvc -e top && NVC_ACCEL=1 nvc -r top
```

The harness clears **all** `NVC_ACCEL*` vars (`NVC_ACCEL`, `NVC_USE_ACCEL`,
`NVC_ACCEL_CC`, `NVC_ACCEL_JIT`, `NVC_ACCEL_JIT_DEBUG`) before every nvc run, so
a stray value in your shell can't silently accelerate the baseline. The accel
block then sets `NVC_ACCEL=1` on top of that cleaned environment.

## Prerequisite

nvc must be built from a source that honours `NVC_ACCEL` (commit "nvc: honor
NVC_ACCEL in the environment as the --accel equivalent"). Rebuild the build
area if needed:

```sh
cd /usr/local/src/nvc-build && smak -j16 && make -j16
# confirm: strings bin/nvc | grep -qx NVC_ACCEL && echo ok
```

(The CI gate's `build_nvc` rebuilds it automatically.)

## Running

```sh
cd /usr/local/src/sv2ghdl/regress

# full suite: normal baseline + accel variant, into the persistent DB
./regress run nvc/regr nvc/regr-accel

# just the accel variant (it gates itself by running normal first)
./regress run nvc/regr-accel

# a subset while iterating (comma-separated test-name filter)
./regress run nvc/regr nvc/regr-accel --filter guard2,guard3,conf1
```

## Reading the results

```sh
./regress report            # per-block PASS/FAIL/SKIP for the latest run
./regress diff <A> <B>      # what changed between two runs (new accel regressions/fixes)
```

List the accel-only regressions (pass normal, fail `--accel`) for the latest run:

```sh
perl -I lib -MRegress::DB -e '
  my $db = Regress::DB->new(path=>"results.db");
  my $rid = $db->{dbh}->selectrow_array("SELECT MAX(run_id) FROM block_run");
  my $rows = $db->{dbh}->selectall_arrayref(
    "SELECT r.test_name FROM result r JOIN block_run br ON br.block_run_id=r.block_run_id
     WHERE br.run_id=? AND br.block=? AND r.status=? ORDER BY r.test_name",
    {Slice=>{}}, $rid, "nvc/regr-accel", "fail");
  print "$_->{test_name}\n" for @$rows;'
```

The per-test nvc output is in the block log; the accel block also writes the
gate (normal) pass to `<log>.normal` alongside the accel log.
