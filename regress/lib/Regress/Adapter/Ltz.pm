package Regress::Adapter::Ltz;
#
# ltz LTspice-circuit tests: run each tests/ltspice_circuits/<dir>/*.cir through
# `ltz -b` (the build-area ltz wrapping the build-area Xyce). --filter selects
# test-dir names (e.g. 00_RC_LOW_PASS_FILTER,05_MEAS).
#
# PASS criterion: ltz/Xyce simulates the circuit cleanly (exit 0). The golden
# reference for these is LTspice's own output -- LTspice runs on Linux under
# Wine but is not installed on this machine, so there's no gold here yet. When
# LTspice (Wine) is available, add an analog comparison like xyce/ihp-pdk
# (clean run stays the pass; the LTspice delta becomes a reported metric). For
# now this is a "does the LTspice->Xyce flow handle these circuits" regression.
#
use strict;
use warnings;
use Regress::Tools qw(ltz_bin ltz_tests_dir xyce_bin xyce_libdir);
use Regress::Util  qw(run_capture);

sub run {
    my ($class, $block, %opt) = @_;

    my $ltz  = ltz_bin()       or return _err('ltz not found');
    my $tdir = ltz_tests_dir() or return _err('ltz test corpus not found');

    # Validate the build-area Xyce (per project rule).
    my %env;
    if (my $x = xyce_bin())    { $env{XYCE} = $x; }
    if (my $l = xyce_libdir()) { $env{LD_LIBRARY_PATH} = $l; }

    my %want = map { $_ => 1 }
        (defined $opt{filter} && length $opt{filter}) ? split(/,/, $opt{filter}) : ();

    opendir(my $dh, $tdir) or return _err("cannot read $tdir");
    my @dirs = sort grep { -d "$tdir/$_" && !/^\./ } readdir $dh;
    closedir $dh;

    my @r;
    for my $d (@dirs) {
        next if %want && !$want{$d};
        my $ddir = "$tdir/$d";
        opendir(my $cd, $ddir) or next;
        my @cirs = sort grep { /\.cir$/i && -f "$ddir/$_" } readdir $cd;
        closedir $cd;
        for my $cir (@cirs) {
            my $name = "$d/$cir";
            my ($rc, $out) = run_capture([$ltz, '-b', $cir],
                dir => $ddir, env => \%env, log => $opt{log});
            my $status = ($rc == 0) ? 'pass' : 'fail';
            my $msg = ($rc == 0) ? 'ltz/Xyce ok'
                    : "ltz -b rc=$rc"
                      . (($out // '') =~ /Xyce Abort|MSG_FATAL|MSG_ERROR/ ? ' (sim error)' : '');
            push @r, { test_name => $name, status => $status,
                       message => $msg, log_path => $opt{log} };
        }
    }

    return _err('no ltz circuits found') unless @r;
    return { exit_code => 0, results => \@r };
}

sub _err {
    my ($msg) = @_;
    return { exit_code => 127, results => [
        { test_name => 'ltz', status => 'error', message => $msg } ] };
}

1;
