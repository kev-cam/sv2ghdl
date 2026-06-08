package Regress::Adapter::NvcNative;
#
# Adapter for nvc's own test suites, run from the build area:
#   mode 'regr' -> bin/run_regr   (~1256 VHDL functional tests; "<name> : ok|failed|skipped")
#   mode 'unit' -> bin/unit_test  (C unit tests via the 'check' framework)
#
# run_regr discovers its test dir relative to BUILD_DIR; it needs BUILD_DIR and
# NVC_LIBPATH pointing at the build tree (mirrors the Makefile TESTS_ENVIRONMENT).
#
use strict;
use warnings;
use Cwd ();
use Regress::Tools qw(src_root run_regr_bin unit_test_bin);
use Regress::Util  qw(run_capture);

sub build_dir { src_root() . '/nvc-build' }

# Accel env vars. NVC_ACCEL enables the --accel equivalent (auto compile +
# engage); the rest tune/override accel. Regular runs CLEAR all of them so a
# stray value in the caller's environment can't silently accelerate the
# baseline ("regular tests use a clean environment").
my @ACCEL_ENV = qw(NVC_ACCEL NVC_USE_ACCEL NVC_ACCEL_CC NVC_ACCEL_JIT
                   NVC_ACCEL_JIT_DEBUG);

sub run {
    my ($class, $block, %opt) = @_;
    my $mode = $block->{params}{mode} || 'regr';
    return _run_unit($block, %opt)       if $mode eq 'unit';
    return _run_regr_accel($block, %opt) if $block->{params}{accel};
    return _run_regr($block, %opt);
}

sub _env {
    my $bd = Cwd::abs_path(build_dir());
    return { BUILD_DIR => $bd, NVC_LIBPATH => "$bd/lib", NVC_IMP_LIB => "$bd/lib" };
}

sub _parse_regr {
    my ($out, $log) = @_;
    my @r;
    for my $line (split /\n/, $out) {
        next unless $line =~ /^\s*(\S+)\s*:\s*(ok|failed|skipped)\b(.*)$/;
        my ($name, $st, $rest) = ($1, $2, $3);
        my $status = $st eq 'ok' ? 'pass' : $st eq 'skipped' ? 'skip' : 'fail';
        push @r, { test_name => $name, status => $status,
                   message => ($rest =~ /\S/ ? "$st$rest" : $st), log_path => $log };
    }
    return @r;
}

# Run run_regr in a CLEAN accel environment (all NVC_ACCEL* cleared first), then
# enable NVC_ACCEL when accel=1 so nvc auto-accelerates without touching the
# script. @{$a{tests}} restricts to those test names (empty = whole suite).
# Returns ($exit, \@results).
sub _invoke_regr {
    my (%a) = @_;
    my $bin = run_regr_bin() or return (127, [
        { test_name => 'run_regr', status => 'error', message => 'run_regr binary not found' } ]);
    my @cmd = ($bin, @{ $a{tests} || [] });
    my %env = %{ _env() };
    $env{NVC_ACCEL} = '1' if $a{accel};
    my ($exit, $out) = run_capture(\@cmd, dir => build_dir(),
        env => \%env, unset => [@ACCEL_ENV], log => $a{log});
    return ($exit, [ _parse_regr($out, $a{log}) ]);
}

sub _run_regr {
    my ($block, %opt) = @_;
    my @tests = (defined $opt{filter} && length $opt{filter})
              ? split(/,/, $opt{filter}) : ();
    my ($exit, $res) = _invoke_regr(tests => \@tests, log => $opt{log});
    return { exit_code => $exit, results => $res };
}

# --accel variant: only tests that PASS the normal (clean-env) run are retried
# with NVC_ACCEL=1, and the accel results are what THIS block records -- logged
# separately from nvc/regr. A test that fails normally is not accelerated (it
# would just inherit the failure). Two run_regr passes: gate (normal), then
# accel on the passers.
sub _run_regr_accel {
    my ($block, %opt) = @_;
    my @tests = (defined $opt{filter} && length $opt{filter})
              ? split(/,/, $opt{filter}) : ();
    my $gate_log = defined $opt{log} ? "$opt{log}.normal" : undef;
    my ($gexit, $gres) = _invoke_regr(tests => \@tests, log => $gate_log);
    my @pass = map { $_->{test_name} } grep { $_->{status} eq 'pass' } @$gres;
    return { exit_code => $gexit, results => [
        { test_name => '(gate)', status => 'skip',
          message => 'no tests passed normal simulation; nothing to accelerate',
          log_path => $gate_log } ] } unless @pass;
    my ($aexit, $ares) = _invoke_regr(tests => \@pass, accel => 1, log => $opt{log});
    return { exit_code => $aexit, results => $ares };
}

sub _run_unit {
    my ($block, %opt) = @_;
    my $bin = unit_test_bin() or return { exit_code => 127, results => [
        { test_name => 'unit_test', status => 'error', message => 'unit_test binary not found' } ] };

    # One suite per arg; if filter given, run just those suites, else all.
    my @suites = (defined $opt{filter} && length $opt{filter})
               ? split(/,/, $opt{filter}) : ();

    my @cmd = ($bin, @suites);
    my ($exit, $out) = run_capture(\@cmd,
        dir => build_dir(), env => _env(), log => $opt{log});

    # check prints "Checks: N, Failures: F, Errors: E" summaries. Record one
    # result per run with the rolled-up counts; mark fail if exit!=0.
    my ($fails, $errs) = (0, 0);
    for my $line (split /\n/, $out) {
        if ($line =~ /Failures:\s*(\d+),\s*Errors:\s*(\d+)/) { $fails += $1; $errs += $2 }
    }
    my $name = @suites ? join('+', @suites) : 'unit_test:all';
    my $status = ($exit == 0 && $fails == 0 && $errs == 0) ? 'pass' : 'fail';
    return { exit_code => $exit, results => [
        { test_name => $name, status => $status,
          message => "exit=$exit failures=$fails errors=$errs", log_path => $opt{log} } ] };
}

1;
