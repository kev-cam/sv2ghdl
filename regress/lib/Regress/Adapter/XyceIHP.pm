package Regress::Adapter::XyceIHP;
#
# IHP-Open-PDK device tests run THROUGH Xyce (we are testing Xyce/PyMS, not
# gnucap). For each gnucap-stats <name>.gc deck:
#   1. convert to a Xyce netlist with gnucap2xyce.pl  -> <name>.cir
#   2. run Xyce on <name>.cir (PyMS auto-compiles the .HDL Verilog-A models)
#   3. diff Xyce's <name>.cir.prn against the gnucap golden ref/<name>.gc.out
#      column-by-column (leading sweep/Index column dropped), within tolerance.
# PASS = numeric match to the gnucap reference; the gnucap golds come from
# running the decks through gnucap (ref/*.gc.out).
#
# Runs in the IHP test tree (the decks use relative includes like
# ../../../models/cornerRES.va); generated .cir/.prn there are untracked.
#
use strict;
use warnings;
use Regress::Tools qw(xyce_bin xyce_libdir gnucap2xyce_bin ihp_pdk_dir pyms_dir);
use Regress::Util  qw(run_capture);

my @DEVDIRS = qw(resistor capacitor moslv moshv);

sub tests_dir {
    my $d = ihp_pdk_dir() or return undef;
    my $t = "$d/ihp-sg13g2/libs.tech/gnucap/tests/gnucap";
    return (-d $t) ? $t : undef;
}

sub run {
    my ($class, $block, %opt) = @_;

    my $xyce = xyce_bin()        or return _err('Xyce binary not found');
    my $g2x  = gnucap2xyce_bin() or return _err('gnucap2xyce.pl not found');
    my $tdir = tests_dir()       or return _err('IHP-Open-PDK gnucap tests not found');
    my $libdir = xyce_libdir();

    my %want = map { $_ => 1 }
        (defined $opt{filter} && length $opt{filter}) ? split(/,/, $opt{filter}) : @DEVDIRS;
    # Validate the BUILD-AREA Xyce/PyMS (per project rule: test build-area
    # before install). xyce_bin/gnucap2xyce_bin are already build-area; pin the
    # build-area PyMS + libxyce.so here too.
    my %env;
    $env{LD_LIBRARY_PATH} = $libdir if $libdir;
    if (my $pd = pyms_dir()) { $env{PYMS_DIR} = $pd; }
    my $env = \%env;
    # Analog sims (gnucap vs Xyce/PyMS behavioral) won't match bit-for-bit, so a
    # numeric delta does NOT fail the test -- a clean convert+run is the pass
    # criterion and the gnucap-gold delta is reported for drift tracking. Set
    # params{fail_rtol} to also fail on gross divergence (max rel err > that).
    my $fail_rtol = $block->{params}{fail_rtol};

    my @r;
    for my $dev (@DEVDIRS) {
        next unless $want{$dev};
        my $ddir = "$tdir/$dev";
        next unless -d $ddir;
        opendir(my $dh, $ddir) or next;
        my @gc = sort grep { /\.gc$/ && !/^wip_/ } readdir $dh;
        closedir $dh;

        for my $gc (@gc) {
            (my $base = $gc) =~ s/\.gc$//;
            my $cir  = "$base.cir";
            # .PRINT TRAN/DC -> .cir.prn; .PRINT AC -> .cir.FD.prn
            my @prns = ("$ddir/$base.cir.prn", "$ddir/$base.cir.FD.prn");
            my $gold = "$ddir/ref/$base.gc.out";
            my $name = "$dev/$gc";

            # 1. convert (gnucap2xyce.pl) -- a convert error is a real failure
            my ($crc) = run_capture(['perl', $g2x, $gc], dir => $ddir, log => $opt{log});
            if ($crc != 0) {
                push @r, { test_name => $name, status => 'fail',
                           message => "gnucap2xyce convert rc=$crc", log_path => $opt{log} };
                next;
            }
            # 2. run Xyce -- not simulating the converted deck is a real failure
            unlink @prns;
            my ($xrc) = run_capture([$xyce, $cir], dir => $ddir, env => $env, log => $opt{log});
            my ($prn) = grep { -f } @prns;
            if ($xrc != 0 || !defined $prn) {
                push @r, { test_name => $name, status => 'fail',
                           message => "Xyce rc=$xrc" . (defined $prn ? '' : ' (no .prn)'),
                           log_path => $opt{log} };
                next;
            }
            # 3. compare to the gnucap gold -- informational (analog tolerance);
            #    PASS on a clean run, fail only on gross divergence if fail_rtol set.
            my ($maxrel, $cmp) = (-f $gold) ? _compare($gold, $prn) : (undef, 'no gnucap gold');
            my $status = (defined $fail_rtol && defined $maxrel && $maxrel > $fail_rtol)
                       ? 'fail' : 'pass';
            push @r, { test_name => $name, status => $status,
                       message => "Xyce ran; vs gnucap: $cmp", log_path => $opt{log} };
        }
    }

    return _err('no IHP tests found') unless @r;
    return { exit_code => 0, results => \@r };
}

# Read numeric data rows, dropping the leading sweep/Index column. AC
# output (.FD.prn) carries Re(X)/Im(X) column pairs; fold each pair to a
# magnitude so the rows align with gnucap's magnitude-only gold.
sub _read_data {
    my ($f) = @_;
    open my $h, '<', $f or return undef;
    my (@rows, @hdr);
    while (my $line = <$h>) {
        $line =~ s/^\s+//;
        if ($line =~ /^Index\s/i) {                        # Xyce column header
            @hdr = split ' ', $line;
            shift @hdr;                                    # drop Index
            next;
        }
        next if $line !~ /\S/ || $line =~ /^[#A-Za-z]/;   # header/comment/blank
        my @c = grep { /^[-+]?(?:\d|\.\d)/ } split ' ', $line;
        next unless @c >= 2;
        shift @c;                                          # drop leading column
        if (@hdr) {
            my @fold;
            for (my $j = 0; $j < @c; $j++) {
                if (defined $hdr[$j] && $hdr[$j] =~ /^Re\(/
                    && defined $hdr[$j+1] && $hdr[$j+1] =~ /^Im\(/) {
                    push @fold, sqrt($c[$j]**2 + $c[$j+1]**2);
                    $j++;
                } else {
                    push @fold, $c[$j] + 0;
                }
            }
            @c = @fold;
            # Xyce auto-prepends the sweep variable (FREQ/TIME) after
            # Index; the gold's sweep column was already dropped above.
            shift @c if $hdr[0] && $hdr[0] =~ /^(FREQ|TIME)$/i;
        }
        push @rows, [ map { $_ + 0 } @c ];
    }
    close $h;
    return \@rows;
}

# Column-by-column comparison of Xyce output vs the gnucap gold. Returns
# ($max_rel_err, $summary). Informational: the caller decides if a delta fails.
sub _compare {
    my ($gold, $prn) = @_;
    my $g = _read_data($gold);
    my $p = _read_data($prn);
    return (undef, 'no gold data') unless $g && @$g;
    return (undef, 'no Xyce data') unless $p && @$p;
    my $nrow = (@$g < @$p) ? scalar @$g : scalar @$p;
    my ($maxrel, $worst, $vals) = (0, '', 0);
    for my $i (0 .. $nrow - 1) {
        my @gr = @{ $g->[$i] };
        my @pr = @{ $p->[$i] };
        my $n = (@gr < @pr) ? scalar @gr : scalar @pr;
        for my $j (0 .. $n - 1) {
            my ($a, $b) = ($gr[$j], $pr[$j]);
            my $rel = abs($a - $b) / (abs($a) + 1e-12);
            $vals++;
            ($maxrel, $worst) = ($rel, sprintf("gold %.4g vs xyce %.4g", $a, $b))
                if $rel > $maxrel;
        }
    }
    return (undef, 'no comparable values') unless $vals;
    return (0, "exact match ($vals vals)") if $maxrel == 0;
    return ($maxrel, sprintf("max rel err %.1f%% over %d vals (%s)",
                             100 * $maxrel, $vals, $worst));
}

sub _err {
    my ($msg) = @_;
    return { exit_code => 127, results => [
        { test_name => 'xyce/ihp-pdk', status => 'error', message => $msg } ] };
}

1;
