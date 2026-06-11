package Regress::RawCompare;
#
# SPICE rawfile reading + engine-vs-engine waveform comparison, shared by the
# gold-comparing adapters (Ltz vs LTspice, Qspice vs QSPICE64).
#
# Reader: ASCII or binary rawfiles, single- or multi-section (Xyce emits one
# header+data section per .STEP point; sections are concatenated in order).
# Complex (AC) values reduce to magnitude. Binary payloads are consumed by
# exact byte count (np*nv*(1|2)*8 little-endian doubles), never scanned, so
# waveform bytes can't be mistaken for section headers.
#
# Comparator (the rules each corpus taught us):
#   - variables align by normalized name: V(x) -> x, I(Vsrc) -> vsrc#branch;
#   - engines pick different step grids: interpolate onto the gold abscissa,
#     normalize each signal's error by its gold peak-to-peak range;
#   - near-zero-range signals (DC gate currents) are noise: skipped;
#   - free-running oscillators never phase-align: when the leading signal
#     oscillates, compare frequency + amplitude instead of pointwise;
#   - non-monotonic abscissa (nested .dc / concatenated .STEP): row-aligned;
#   - single-point data (.op): pointwise, magnitude-normalized.
#
use strict;
use warnings;
use Exporter 'import';

our @EXPORT_OK = qw(read_raw compare_raw);

sub read_raw {
    my ($file) = @_;
    open my $fh, '<:raw', $file or return undef;
    local $/; my $blob = <$fh>; close $fh;
    return undef unless defined $blob && length $blob;

    my (@names, @rows, $cplx_any);
    my $pos = 0;
    my $len = length $blob;
    my $section = 0;

    while ($pos < $len) {
        my $rest = substr($blob, $pos);
        last unless $rest =~ /\S/;
        # header runs to the first data marker
        last unless $rest =~ /\A(.*?\n)(Binary:[^\n]*\n|Values:[^\n]*\n)/s;
        my ($hdr, $marker) = ($1, $2);
        my $data_off = $pos + length($hdr) + length($marker);
        my ($nv) = $hdr =~ /No\.\s*Variables:\s*(\d+)/i;
        my ($np) = $hdr =~ /No\.\s*Points:\s*(\d+)/i;
        return undef unless $nv && defined $np;
        my $cplx = $hdr =~ /Flags:[^\n]*\bcomplex\b/i ? 1 : 0;
        $cplx_any ||= $cplx;
        $section++;
        if ($section == 1 && $hdr =~ /\nVariables:\s*\n(.*)\z/s) {
            for my $ln (split /\n/, $1) {
                push @names, $1 if $ln =~ /^\s*\d+\s+(\S+)/;
            }
            return undef unless @names == $nv;
        }

        if ($marker =~ /^Binary:/) {
            my $per   = $cplx ? 2 : 1;
            my $bytes = $np * $nv * $per * 8;
            my $body  = substr($blob, $data_off, $bytes);
            my @d = unpack('d<*', $body);
            for my $p (0 .. $np - 1) {
                my @row;
                for my $v (0 .. $nv - 1) {
                    my $i = ($p * $nv + $v) * $per;
                    push @row, $cplx
                        ? sqrt(($d[$i] // 0)**2 + ($d[$i+1] // 0)**2)
                        : ($d[$i] // 0);
                }
                push @rows, \@row;
            }
            $pos = $data_off + $bytes;
        } else {
            # ASCII: numeric lines until the next section header (or EOF)
            my $body = substr($blob, $data_off);
            my $endrel = $body =~ /^(?:Title|Plotname):/m ? $-[0] : length $body;
            my @toks;
            for my $ln (split /\n/, substr($body, 0, $endrel)) {
                for my $t (split /\s+/, $ln) {
                    next unless length $t;
                    if ($t =~ /,/) {
                        my ($re, $im) = split /,/, $t;
                        push @toks, sqrt(($re // 0)**2 + ($im // 0)**2);
                    } elsif ($t =~ /^-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?$/i) {
                        push @toks, $t + 0;
                    }
                }
            }
            while (@toks >= $nv + 1) {
                my @grp = splice @toks, 0, $nv + 1;
                shift @grp;          # leading point index
                push @rows, \@grp;
            }
            $pos = $data_off + $endrel;
        }
    }

    return undef unless @names && @rows;
    my %col;
    $col{ _norm($names[$_]) } = $_ for 0 .. $#names;
    return { names => \@names, col => \%col, rows => \@rows };
}

# Align engine spellings: V(x) -> x, I(Vsrc) -> vsrc#branch, case-folded.
sub _norm {
    my ($n) = @_;
    $n = lc $n;
    return "$1#branch" if $n =~ /^i\((v\w+)\)$/;
    return $1 if $n =~ /^v\((\w+)\)$/;
    return $n;
}

# compare_raw($gold_file, $test_file) -> ($max_rel_err, $summary)
sub compare_raw {
    my ($gf, $xf) = @_;
    my $g = read_raw($gf);  return (undef, 'unparsable gold rawfile') unless $g;
    my $x = read_raw($xf);  return (undef, 'unparsable test rawfile') unless $x;

    my $g_ab = _norm($g->{names}[0]);
    my @common = grep { exists $x->{col}{$_} }
                 grep { $_ ne $g_ab && $_ !~ /^(?:time|sweep|frequency)$/ }
                 map  { _norm($_) } @{ $g->{names} };
    return (undef, 'no common variables') unless @common;

    # Single-point data (.op): pointwise, magnitude-normalized.
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

    # gold ranges; skip near-zero-range signals (noise)
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

    # Everything flat (a DC circuit under .tran/.dc): compare steady values
    # magnitude-normalized instead of declaring nothing comparable.
    if (!@sig) {
        my ($maxrel, $worst) = (0, '');
        for my $v (@common) {
            my $a = $g->{rows}[-1][ $g->{col}{$v} ];
            my $b = $x->{rows}[-1][ $x->{col}{$v} ];
            my $rel = abs($a - $b) / (abs($a) + 1e-12);
            ($maxrel, $worst) = ($rel, $v) if $rel > $maxrel;
        }
        return ($maxrel, sprintf('flat signals: max rel err %.3g%% over %d vars (%s)',
                                 100 * $maxrel, scalar @common, $worst));
    }

    # oscillator: if the most active signal oscillates, pointwise is phase noise
    my ($lead) = sort { $range{$b} <=> $range{$a} } @sig;
    my $gosc = _osc_freq($g, $lead);
    if ($g_ab eq 'time' && $gosc) {
        my $xosc = _osc_freq($x, $lead);
        return (undef, sprintf('gold oscillates (%.4g Hz) but test does not', $gosc->{freq}))
            unless $xosc;
        my $df = abs($gosc->{freq} - $xosc->{freq}) / $gosc->{freq};
        my $da = ($gosc->{amp} > 1e-12)
               ? abs($gosc->{amp} - $xosc->{amp}) / $gosc->{amp} : 0;
        my $worst = $df > $da ? $df : $da;
        return ($worst, sprintf('oscillator %s: freq %.4g vs %.4g Hz (%.2f%%), amp %.4g vs %.4g (%.2f%%)',
            $lead, $gosc->{freq}, $xosc->{freq}, 100 * $df,
            $gosc->{amp}, $xosc->{amp}, 100 * $da));
    }

    # steady: interpolate onto the gold abscissa (row-aligned if non-monotonic)
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

# rising-mean-crossing frequency + peak amplitude over the settled tail
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

1;
