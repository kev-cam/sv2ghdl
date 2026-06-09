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
    my $rtol = $block->{params}{rtol} // 0.01;

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
            my $prn  = "$ddir/$base.cir.prn";
            my $gold = "$ddir/ref/$base.gc.out";
            my $name = "$dev/$gc";

            unless (-f $gold) {
                push @r, { test_name => $name, status => 'skip',
                           message => 'no gnucap gold (ref/*.gc.out)', log_path => $opt{log} };
                next;
            }
            # 1. convert
            my ($crc) = run_capture(['perl', $g2x, $gc], dir => $ddir, log => $opt{log});
            if ($crc != 0) {
                push @r, { test_name => $name, status => 'fail',
                           message => "gnucap2xyce convert rc=$crc", log_path => $opt{log} };
                next;
            }
            # 2. run Xyce
            unlink $prn;
            my ($xrc) = run_capture([$xyce, $cir], dir => $ddir, env => $env, log => $opt{log});
            if ($xrc != 0 || !-f $prn) {
                push @r, { test_name => $name, status => 'fail',
                           message => "Xyce rc=$xrc" . (-f $prn ? '' : ' (no .prn)'),
                           log_path => $opt{log} };
                next;
            }
            # 3. compare to the gnucap gold
            my ($ok, $msg) = _compare($gold, $prn, $rtol);
            push @r, { test_name => $name, status => ($ok ? 'pass' : 'fail'),
                       message => $msg, log_path => $opt{log} };
        }
    }

    return _err('no IHP tests found') unless @r;
    return { exit_code => 0, results => \@r };
}

# Read numeric data rows, dropping the leading sweep/Index column.
sub _read_data {
    my ($f) = @_;
    open my $h, '<', $f or return undef;
    my @rows;
    while (my $line = <$h>) {
        $line =~ s/^\s+//;
        next if $line !~ /\S/ || $line =~ /^[#A-Za-z]/;   # header/comment/blank
        my @c = grep { /^[-+]?(?:\d|\.\d)/ } split ' ', $line;
        next unless @c >= 2;
        shift @c;                                          # drop leading column
        push @rows, [ map { $_ + 0 } @c ];
    }
    close $h;
    return \@rows;
}

# Column-by-column numeric compare within relative+abs tolerance.
sub _compare {
    my ($gold, $prn, $rtol) = @_;
    my $atol = 1e-6;
    my $g = _read_data($gold);
    my $p = _read_data($prn);
    return (0, 'no gold data')  unless $g && @$g;
    return (0, 'no Xyce data')  unless $p && @$p;
    my $nrow = (@$g < @$p) ? scalar @$g : scalar @$p;
    for my $i (0 .. $nrow - 1) {
        my @gr = @{ $g->[$i] };
        my @pr = @{ $p->[$i] };
        return (0, "row $i: gold has " . scalar(@gr) . " cols, Xyce " . scalar(@pr))
            unless @gr && @gr == @pr;
        for my $j (0 .. $#gr) {
            my ($a, $b) = ($gr[$j], $pr[$j]);
            return (0, sprintf("mismatch row %d col %d: gold %.5g vs Xyce %.5g", $i, $j, $a, $b))
                if abs($a - $b) > $rtol * abs($a) + $atol;
        }
    }
    return (1, "matches gnucap gold ($nrow rows)");
}

sub _err {
    my ($msg) = @_;
    return { exit_code => 127, results => [
        { test_name => 'xyce/ihp-pdk', status => 'error', message => $msg } ] };
}

1;
