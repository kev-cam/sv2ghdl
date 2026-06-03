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

sub run {
    my ($class, $block, %opt) = @_;
    my $mode = $block->{params}{mode} || 'regr';
    return $mode eq 'unit' ? _run_unit($block, %opt) : _run_regr($block, %opt);
}

sub _env {
    my $bd = Cwd::abs_path(build_dir());
    return { BUILD_DIR => $bd, NVC_LIBPATH => "$bd/lib", NVC_IMP_LIB => "$bd/lib" };
}

sub _run_regr {
    my ($block, %opt) = @_;
    my $bin = run_regr_bin() or return { exit_code => 127, results => [
        { test_name => 'run_regr', status => 'error', message => 'run_regr binary not found' } ] };
    my @cmd = ($bin);
    push @cmd, split(/,/, $opt{filter}) if defined $opt{filter} && length $opt{filter};

    my ($exit, $out) = run_capture(\@cmd,
        dir => build_dir(), env => _env(), log => $opt{log});

    my @r;
    for my $line (split /\n/, $out) {
        next unless $line =~ /^\s*(\S+)\s*:\s*(ok|failed|skipped)\b(.*)$/;
        my ($name, $st, $rest) = ($1, $2, $3);
        my $status = $st eq 'ok' ? 'pass' : $st eq 'skipped' ? 'skip' : 'fail';
        push @r, { test_name => $name, status => $status,
                   message => ($rest =~ /\S/ ? "$st$rest" : $st), log_path => $opt{log} };
    }
    return { exit_code => $exit, results => \@r };
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
