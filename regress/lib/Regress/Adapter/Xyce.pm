package Regress::Adapter::Xyce;
#
# Adapter for the Xyce_Regression suite, driven by Xyce's own native Perl
# runner (TestScripts/run_xyce_regression) -- NO cmake/ctest dependency.
#
# The runner descends Xyce_Regression/Netlists, executes each test's
# <name>.cir.sh (which runs Xyce on the netlist and diffs against the gold
# OutputData/.prn via xyce_verify.pl), and writes --passlist / --faillist files
# we parse into per-test results.
#
# Test selection is by tag (default +serial+nightly). We do NOT capability-probe
# Xyce (that is the only thing cmake/ctest add); instead, tests for capabilities
# this Xyce lacks fail *consistently*, so they sit in the baseline and only a
# new pass->fail transition shows up as a regression -- exactly how the harness
# already treats nvc/regr's pre-existing failures.
#
use strict;
use warnings;
use File::Path ();
use Regress::Tools qw(xyce_bin xyce_regr_dir xyce_regr_runner);
use Regress::Util  qw(run_capture);

sub run {
    my ($class, $block, %opt) = @_;

    my $xyce   = xyce_bin()         or return _err('Xyce binary not found');
    my $xr     = xyce_regr_dir()    or return _err('Xyce_Regression not found');
    my $runner = xyce_regr_runner() or return _err('run_xyce_regression not found');

    my $tags = $block->{params}{tags} || '+serial+nightly';

    # Keep the runner's Results_*/output out of the source tree.
    my $work = ($opt{workdir} ? "$opt{workdir}/xyce" : "/tmp/xyce-regr-$$");
    File::Path::make_path($work);
    my $pass = "$work/passlist";
    my $fail = "$work/faillist";
    unlink $pass, $fail;

    my @cmd = ('perl', $runner,
        "--xyce_test=$xr",
        "--xyce_verify=$xr/TestScripts/xyce_verify.pl",
        "--xyce_compare=$xr/TestScripts/xyce_verify.pl",
        "--taglist=$tags",
        "--output=$work",
        "--passlist=$pass",
        "--faillist=$fail");
    # Harness --filter -> one or more --onetest=<DIR[/CIR]> selectors.
    if (defined $opt{filter} && length $opt{filter}) {
        push @cmd, "--onetest=$_" for split /,/, $opt{filter};
    }
    push @cmd, $xyce;

    my ($exit, $out) = run_capture(\@cmd, dir => $work, log => $opt{log});

    my @r = (_parse_list($pass, 'pass', $opt{log}),
             _parse_list($fail, 'fail', $opt{log}));

    return { exit_code => $exit, results => [
        { test_name => 'run_xyce_regression', status => 'error',
          message => "no pass/fail output (exit $exit)", log_path => $opt{log} } ] }
        unless @r;

    return { exit_code => $exit, results => \@r };
}

# pass/fail lists hold "<category> <netlist.cir>" lines (plus '#' comments).
sub _parse_list {
    my ($file, $status, $log) = @_;
    open my $fh, '<', $file or return ();
    my @r;
    while (my $line = <$fh>) {
        next if $line =~ /^\s*#/ || $line !~ /\S/;
        my ($cat, $cir) = split ' ', $line, 2;
        next unless defined $cir;
        $cir =~ s/\s+$//;
        next unless length $cir;
        push @r, { test_name => "$cat/$cir", status => $status, log_path => $log };
    }
    close $fh;
    return @r;
}

sub _err {
    my ($msg) = @_;
    return { exit_code => 127, results => [
        { test_name => 'xyce', status => 'error', message => $msg } ] };
}

1;
