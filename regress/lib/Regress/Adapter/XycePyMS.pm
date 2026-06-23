package Regress::Adapter::XycePyMS;
#
# ADMS-example Verilog-A decks run THROUGH Xyce via PyMS (the ADMS replacement),
# NOT the deprecated compiled-in ADMS device library. Each .sp deck (which
# carries a .hdl directive) is:
#   1. staged with its .va / .include / modelcard deps into a work dir
#   2. preprocessed by cadence2xyce.pl  -> <name>.cir  (.sp syntax -> Xyce, and
#      the deck's .hdl is preserved)
#   3. run in Xyce with PYMS_DIR set -> PyMS JIT-compiles the .hdl Verilog-A at
#      runtime and registers the device (no -adms, no compiled-in model needed).
#
# PASS = clean convert + clean Xyce run (rc==0). PyMS JIT-registers the device
# and the device instantiates (this is what the type_index fix unblocked, see
# DeviceMgr fix/device-typeindex-crash); models that also converge pass, models
# that diverge numerically (e.g. heavy BSIM variants) fail and are real signal.
# --filter selects model-family subdirs (e.g. psp103, BSIM6.1.1).
#
use strict;
use warnings;
use File::Path ();
use File::Copy ();
use File::Basename ();
use Regress::Tools qw(xyce_bin xyce_libdir cadence2xyce_bin adms_examples_dir pyms_dir);
use Regress::Util  qw(run_capture);

sub run {
    my ($class, $block, %opt) = @_;

    my $xyce = xyce_bin()          or return _err('Xyce binary not found');
    my $c2x  = cadence2xyce_bin()  or return _err('cadence2xyce.pl not found');
    my $edir = adms_examples_dir() or return _err('ADMS examples dir not found');
    my $libdir = xyce_libdir();

    # Validate the BUILD-AREA Xyce/PyMS: pin build-area libxyce.so + PyMS tree.
    my %env;
    $env{LD_LIBRARY_PATH} = $libdir if $libdir;
    if (my $pd = pyms_dir()) { $env{PYMS_DIR} = $pd; }
    my $env = \%env;

    # Discover .sp decks (exclude non-functional). --filter selects family dirs.
    my @sp;
    my $finder; $finder = sub {
        my ($d) = @_;
        opendir(my $dh, $d) or return;
        for my $e (sort readdir $dh) {
            next if $e =~ /^\./;
            my $p = "$d/$e";
            if (-d $p) { next if $p =~ m{/non-functional(/|$)}; $finder->($p); }
            elsif ($e =~ /\.sp$/) { push @sp, $p; }
        }
        closedir $dh;
    };
    $finder->($edir);

    if (defined $opt{filter} && length $opt{filter}) {
        my %want = map { $_ => 1 } split /,/, $opt{filter};
        @sp = grep {
            my $rel = substr($_, length($edir) + 1);
            grep { index($rel, $_) == 0 } keys %want
        } @sp;
    }

    my $work = $opt{workdir} ? "$opt{workdir}/xyce-pyms" : "/tmp/xyce-pyms-$$";

    my @r;
    for my $sp (@sp) {
        my $rel  = substr($sp, length($edir) + 1);
        my $name = File::Basename::basename($sp, '.sp');
        my $dir  = File::Basename::dirname($sp);
        (my $tdname = $rel) =~ s{/}{_}g;
        $tdname =~ s/\.sp$//;
        my $td = "$work/$tdname";
        File::Path::make_path($td);

        # Stage the deck + its model/Verilog-A dependencies (including the
        # sibling code/ trees the ADMS examples keep the .va in).
        File::Copy::copy($sp, "$td/");
        for my $g (glob("$dir/modelcard* $dir/*.lib $dir/*.inc $dir/*.include $dir/*.va")) {
            File::Copy::copy($g, "$td/") if -f $g;
        }
        for my $vadir ("$dir/../code", "$dir/../../code", "$dir/..") {
            next unless -d $vadir;
            for my $g (glob("$vadir/*.va $vadir/*.include")) {
                File::Copy::copy($g, "$td/") if -f $g;
            }
        }

        my $cir = "$td/$name.cir";
        # 1. preprocess .sp -> .cir (a convert error is a real failure)
        my ($crc) = run_capture(['perl', $c2x, "$td/$name.sp", '-o', $cir],
                                dir => $td, log => $opt{log});
        if ($crc != 0 || !-f $cir) {
            push @r, { test_name => $rel, status => 'fail',
                       message => "cadence2xyce rc=$crc", log_path => $opt{log} };
            next;
        }

        # 2. run via PyMS (.hdl JIT-compiles the Verilog-A at runtime). rc==0 is
        #    a clean run: device registered, instantiated, and simulated.
        my ($xrc) = run_capture([$xyce, "$name.cir"],
                                dir => $td, env => $env, log => $opt{log});
        push @r, { test_name => $rel,
                   status  => ($xrc == 0) ? 'pass' : 'fail',
                   message => ($xrc == 0) ? 'PyMS .HDL ran clean' : "Xyce rc=$xrc",
                   log_path => $opt{log} };
    }

    return _err('no ADMS-example decks found') unless @r;
    return { exit_code => 0, results => \@r };
}

sub _err {
    my ($msg) = @_;
    return { exit_code => 127, results => [
        { test_name => 'xyce/pyms', status => 'error', message => $msg } ] };
}

1;
