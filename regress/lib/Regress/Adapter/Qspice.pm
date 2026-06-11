package Regress::Adapter::Qspice;
#
# QSPICE-schematic tests: netlist each .qsch with the native QSPICE tool
# (QUX.exe -Netlist, via WSL interop), translate the QSPICE dialect with
# qspice2xyce.pl, and simulate with the build-area Xyce.
#
# Corpus: qspice_tests_dir() (*.qsch, flat). --filter selects basenames.
#
# Golden reference = QSPICE's own simulator: the same netlist is run through
# QSPICE64.exe (headless, -ascii -r) and compared to Xyce's rawfile. Per the
# analog rule the delta is reported, not gating -- PASS = the QUX -> translate
# -> Xyce pipeline simulates cleanly (params{fail_rtol} optionally fails on
# gross divergence).
#
# Comparison notes (learned the hard way):
#   - Xyce emits one rawfile section per .STEP point; QSPICE concatenates.
#     Sections are concatenated before comparing.
#   - The engines choose different timestep grids: signals are interpolated
#     onto the gold abscissa, errors normalized by each signal's gold range.
#   - Free-running oscillators never phase-align between engines, so when both
#     waveforms oscillate the comparison switches to frequency + amplitude.
#   - Near-zero-range variables (e.g. DC gate currents) are noise; skipped.
#
use strict;
use warnings;
use Regress::Tools qw(qspice_sim_bin qspice_qux_bin qspice2xyce_bin
                      qspice_tests_dir xyce_bin xyce_libdir);
use Regress::Util  qw(run_capture);
use Regress::RawCompare qw(compare_raw);

sub run {
    my ($class, $block, %opt) = @_;

    my $qux  = qspice_qux_bin()   or return _err('QUX.exe not found (QSPICE install)');
    my $q2x  = qspice2xyce_bin()  or return _err('qspice2xyce.pl not found');
    my $xyce = xyce_bin()         or return _err('Xyce not found');
    my $root = qspice_tests_dir() or return _err('qspice-tests corpus not found');
    my $sim  = ($block->{params}{qspice} // 1) ? qspice_sim_bin() : undef;
    my $fail_rtol = $block->{params}{fail_rtol};

    my %env;
    if (my $l = xyce_libdir()) { $env{LD_LIBRARY_PATH} = $l; }

    my %want = map { $_ => 1 }
        (defined $opt{filter} && length $opt{filter}) ? split(/,/, $opt{filter}) : ();

    opendir(my $dh, $root) or return _err("cannot read $root");
    my @qsch = sort grep { /\.qsch$/i && -f "$root/$_" } readdir $dh;
    closedir $dh;

    my @r;
    for my $q (@qsch) {
        (my $base = $q) =~ s/\.qsch$//i;
        next if %want && !$want{$base};

        my $res = sub {
            push @r, { test_name => $base, status => $_[0],
                       message => $_[1], log_path => $opt{log} };
        };

        # 1. netlist the schematic with QSPICE's own netlister
        unlink "$root/$base.cir";
        run_capture([$qux, '-Netlist', $q], dir => $root, log => $opt{log});
        if (!-s "$root/$base.cir") { $res->('error', 'QUX -Netlist produced no .cir'); next; }

        # 2. translate the QSPICE dialect for Xyce
        my ($trc) = run_capture(['perl', $q2x, '-o', "$base.xyce.cir", "$base.cir"],
            dir => $root, log => $opt{log});
        if ($trc != 0 || !-s "$root/$base.xyce.cir") {
            $res->('fail', "qspice2xyce rc=$trc"); next;
        }

        # 3. simulate with Xyce
        my $xraw = "$root/$base.xraw";
        unlink $xraw;
        my ($rc) = run_capture([$xyce, '-r', "$base.xraw", '-a', "$base.xyce.cir"],
            dir => $root, env => \%env, log => $opt{log});
        if ($rc != 0 || !-s $xraw) {
            $res->('fail', "Xyce rc=$rc" . (-s $xraw ? '' : ' (no raw)')); next;
        }

        # 4. golden reference: QSPICE itself on the same netlist
        my $msg = 'qspice2xyce/Xyce ok';
        my $status = 'pass';
        if ($sim) {
            my $gold = "$root/$base.qraw";
            unlink $gold;
            run_capture([$sim, "$base.cir", '-ascii', '-r', "$base.qraw", '-o', "$base.qout"],
                dir => $root, log => $opt{log});
            my ($maxrel, $cmp) = (-s $gold) ? compare_raw($gold, $xraw)
                                            : (undef, 'QSPICE produced no .qraw');
            $msg = "qspice2xyce/Xyce ok; vs QSPICE: $cmp";
            $status = 'fail'
                if defined $fail_rtol && defined $maxrel && $maxrel > $fail_rtol;
        }
        $res->($status, $msg);
    }

    return _err('no .qsch circuits found') unless @r;
    return { exit_code => 0, results => \@r };
}

sub _err {
    my ($msg) = @_;
    return { exit_code => 127, results => [
        { test_name => 'qspice', status => 'error', message => $msg } ] };
}

1;
