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
            my ($maxrel, $cmp) = (-s $gold) ? _compare_raw($gold, $xraw)
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

# --- rawfile reading ---------------------------------------------------------
# ASCII SPICE rawfile, possibly multi-section (Xyce writes one header+Values
# block per .STEP point); sections are concatenated. Complex values -> mag.
sub _read_raw {
    my ($file) = @_;
    open my $fh, '<', $file or return undef;
    my (@names, @toks, $nv);
    my ($in_vars, $in_vals, $section) = (0, 0, 0);
    while (my $l = <$fh>) {
        $l =~ s/\r?\n$//;
        if ($l =~ /^(?:Title|Plotname):/i) { $in_vals = 0; $in_vars = 0; $section++ if $l =~ /^Plotname:/i; next; }
        if ($l =~ /^No\.\s*Variables:\s*(\d+)/i) { $nv = $1; next; }
        if ($l =~ /^Variables:/i) { $in_vars = 1; next; }
        if ($l =~ /^Values:/i)    { $in_vars = 0; $in_vals = 1; next; }
        if ($in_vars) {
            push @names, $1 if $section <= 1 && $l =~ /^\s*\d+\s+(\S+)\s+\S+/;
            next;
        }
        next unless $in_vals;
        for my $t (split /\s+/, $l) {
            next unless length $t;
            if ($t =~ /,/) {   # complex re,im -> magnitude
                my ($re, $im) = split /,/, $t;
                push @toks, sqrt(($re // 0)**2 + ($im // 0)**2);
            } elsif ($t =~ /^-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?$/i) {
                push @toks, $t + 0;
            }
        }
    }
    close $fh;
    return undef unless $nv && @names == $nv;
    my @rows;
    while (@toks >= $nv + 1) {
        my @grp = splice @toks, 0, $nv + 1;
        shift @grp;            # leading point index
        push @rows, \@grp;
    }
    return undef unless @rows;
    my %col;
    $col{ _norm($names[$_]) } = $_ for 0 .. $#names;
    return { names => \@names, col => \%col, rows => \@rows };
}

# Align QSPICE and Xyce variable spellings: V(x) -> x, I(Vsrc) -> vsrc#branch.
sub _norm {
    my ($n) = @_;
    $n = lc $n;
    return "$1#branch" if $n =~ /^i\((v\w+)\)$/;
    return $1 if $n =~ /^v\((\w+)\)$/;
    return $n;
}

# --- comparison --------------------------------------------------------------
sub _compare_raw {
    my ($gold, $xraw) = @_;
    my $g = _read_raw($gold);  return (undef, 'unparsable QSPICE .qraw') unless $g;
    my $x = _read_raw($xraw);  return (undef, 'unparsable Xyce raw')     unless $x;

    my $g_ab = _norm($g->{names}[0]);
    my @common = grep { exists $x->{col}{$_} }
                 grep { $_ ne $g_ab && $_ !~ /^(?:time|sweep|frequency)$/ }
                 map  { _norm($_) } @{ $g->{names} };
    return (undef, 'no common variables') unless @common;

    # Single-point data (an .op): ranges are all zero, so compare pointwise
    # with magnitude normalization instead.
    if (@{ $g->{rows} } == 1 && @{ $x->{rows} } == 1) {
        my ($maxrel, $worst) = (0, '');
        for my $v (@common) {
            my $a = $g->{rows}[0][ $g->{col}{$v} ];
            my $b = $x->{rows}[0][ $x->{col}{$v} ];
            my $rel = abs($a - $b) / (abs($a) + 1e-12);
            ($maxrel, $worst) = ($rel, $v) if $rel > $maxrel;
        }
        return ($maxrel, sprintf('op point: max rel err %.3g%% over %d vars (%s)',
                                 100 * $maxrel, scalar @common, $worst));
    }

    my @gt = map { $_->[0] } @{ $g->{rows} };
    my @xt = map { $_->[0] } @{ $x->{rows} };
    my $monotonic = 1;
    for my $p (1 .. $#xt) { if ($xt[$p] < $xt[$p-1]) { $monotonic = 0; last; } }

    # Pre-compute gold ranges; skip near-zero-range variables (noise).
    my (%range, $maxrange);
    for my $v (@common) {
        my $c = $g->{col}{$v};
        my ($mn, $mx) = ($g->{rows}[0][$c]) x 2;
        for my $r (@{ $g->{rows} }) {
            my $y = $r->[$c];
            $mn = $y if $y < $mn; $mx = $y if $y > $mx;
        }
        $range{$v} = $mx - $mn;
        $maxrange = $range{$v} if !defined $maxrange || $range{$v} > $maxrange;
    }
    my @sig = grep { $range{$_} > 1e-12 && (!$maxrange || $range{$_} > 1e-6 * $maxrange) } @common;
    return (undef, 'no significant common variables') unless @sig;

    # Oscillator detection on the most active signal: if both waveforms cross
    # their tail mean repeatedly, pointwise comparison is phase noise -- use
    # frequency + amplitude instead.
    my ($lead) = sort { $range{$b} <=> $range{$a} } @sig;
    my $gosc = _osc_freq($g, $lead);
    if ($g_ab eq 'time' && $gosc) {
        my $xosc = _osc_freq($x, $lead);
        return (undef, sprintf('gold oscillates (%.4g Hz) but Xyce does not', $gosc->{freq}))
            unless $xosc;
        my $df = abs($gosc->{freq} - $xosc->{freq}) / $gosc->{freq};
        my $da = ($gosc->{amp} > 1e-12)
               ? abs($gosc->{amp} - $xosc->{amp}) / $gosc->{amp} : 0;
        my $worst = $df > $da ? $df : $da;
        return ($worst, sprintf('oscillator %s: freq %.4g vs %.4g Hz (%.2f%%), amp %.4g vs %.4g (%.2f%%)',
            $lead, $gosc->{freq}, $xosc->{freq}, 100 * $df,
            $gosc->{amp}, $xosc->{amp}, 100 * $da));
    }

    # Steady comparison: interpolate Xyce onto the gold abscissa (row-aligned
    # when the abscissa is non-monotonic, i.e. nested-DC / concatenated .STEP).
    my ($maxrel, $worst) = (0, '');
    my $np = (@gt < @xt) ? scalar @gt : scalar @xt;
    for my $v (@sig) {
        my ($gc, $xc) = ($g->{col}{$v}, $x->{col}{$v});
        my @xv = map { $_->[$xc] } @{ $x->{rows} };
        for my $p (0 .. $#gt) {
            my $xval;
            if ($monotonic) { $xval = _interp(\@xt, \@xv, $gt[$p]); }
            else            { $xval = ($p < $np) ? $xv[$p] : undef; }
            next unless defined $xval;
            my $rel = abs($g->{rows}[$p][$gc] - $xval) / $range{$v};
            ($maxrel, $worst) = ($rel, $v) if $rel > $maxrel;
        }
    }
    return ($maxrel, sprintf('max range-norm err %.3g%% over %d vars (%s)',
                             100 * $maxrel, scalar @sig, $worst));
}

# Rising-mean-crossing frequency + peak amplitude of one variable's tail
# (last 60%). Returns undef unless it crosses at least 3 times (oscillating).
sub _osc_freq {
    my ($raw, $var) = @_;
    my $c = $raw->{col}{$var} // return undef;
    my @t = map { $_->[0] }  @{ $raw->{rows} };
    my @y = map { $_->[$c] } @{ $raw->{rows} };
    my $start = int(@t * 0.4);
    @t = @t[$start .. $#t];  @y = @y[$start .. $#y];
    return undef if @t < 8;
    my $mean = 0; $mean += $_ for @y; $mean /= @y;
    my ($mn, $mx) = ($y[0]) x 2;
    for (@y) { $mn = $_ if $_ < $mn; $mx = $_ if $_ > $mx; }
    my @cross;
    for my $i (1 .. $#y) {
        next unless ($y[$i-1] - $mean) < 0 && ($y[$i] - $mean) >= 0;
        my $dy = $y[$i] - $y[$i-1];
        push @cross, $dy != 0
            ? $t[$i-1] + ($t[$i] - $t[$i-1]) * ($mean - $y[$i-1]) / $dy
            : $t[$i];
    }
    return undef unless @cross >= 3;
    my $period = ($cross[-1] - $cross[0]) / (@cross - 1);
    return undef unless $period > 0;
    return { freq => 1 / $period, amp => ($mx - $mn) / 2 };
}

sub _interp {
    my ($xs, $ys, $t) = @_;
    return undef if $t < $xs->[0] || $t > $xs->[-1];
    my ($lo, $hi) = (0, $#$xs);
    while ($hi - $lo > 1) {
        my $mid = int(($lo + $hi) / 2);
        ($xs->[$mid] <= $t) ? ($lo = $mid) : ($hi = $mid);
    }
    my $dx = $xs->[$hi] - $xs->[$lo];
    return $ys->[$lo] if $dx == 0;
    return $ys->[$lo] + ($ys->[$hi] - $ys->[$lo]) * ($t - $xs->[$lo]) / $dx;
}

sub _err {
    my ($msg) = @_;
    return { exit_code => 127, results => [
        { test_name => 'qspice', status => 'error', message => $msg } ] };
}

1;
