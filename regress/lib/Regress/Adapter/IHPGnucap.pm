package Regress::Adapter::IHPGnucap;
#
# Adapter for the IHP-Open-PDK gnucap Verilog-A device tests
# (ihp-sg13g2/libs.tech/gnucap/tests/gnucap). Each <device>/*.gc deck is run
# through gnucap and its filtered output diffed against ref/<name>.gc.out by the
# suite's own Makefile, which echoes one line per test:
#   PASS <name.gc>   FAIL <name.gc>   MISS <name.gc>  (MISS = no reference)
#
use strict;
use warnings;
use Regress::Tools qw(gnucap_bin ihp_pdk_dir);
use Regress::Util  qw(run_capture);

# The gnucap test dir inside the PDK checkout (holds the test Makefile).
sub tests_dir {
    my $d = ihp_pdk_dir() or return undef;
    my $t = "$d/ihp-sg13g2/libs.tech/gnucap/tests/gnucap";
    return (-f "$t/Makefile") ? $t : undef;
}

sub run {
    my ($class, $block, %opt) = @_;

    my $gnucap = gnucap_bin() or return _err('gnucap binary not found');
    my $tdir   = tests_dir()  or return _err('IHP-Open-PDK gnucap tests not found');

    my @cmd = ('make', '-C', $tdir, 'check', "GNUCAP=$gnucap");
    # --filter selects device subdirs (e.g. resistor,capacitor,moslv,moshv).
    if (defined $opt{filter} && length $opt{filter}) {
        (my $dirs = $opt{filter}) =~ s/,/ /g;
        push @cmd, "TESTDIRS=$dirs";
    }

    my ($exit, $out) = run_capture(\@cmd, dir => $tdir, log => $opt{log});

    my @r;
    for my $line (split /\n/, $out) {
        next unless $line =~ /^\s*(PASS|FAIL|MISS)\s+(\S+)/;
        my ($verdict, $name) = ($1, $2);
        my $status = $verdict eq 'PASS' ? 'pass'
                   : $verdict eq 'MISS' ? 'skip'    # no reference output yet
                   :                      'fail';
        push @r, { test_name => $name, status => $status, log_path => $opt{log} };
    }

    return { exit_code => $exit, results => [
        { test_name => 'ihp/gnucap', status => 'error',
          message => "no PASS/FAIL/MISS output (exit $exit)", log_path => $opt{log} } ] }
        unless @r;

    return { exit_code => $exit, results => \@r };
}

sub _err {
    my ($msg) = @_;
    return { exit_code => 127, results => [
        { test_name => 'ihp-gnucap', status => 'error', message => $msg } ] };
}

1;
