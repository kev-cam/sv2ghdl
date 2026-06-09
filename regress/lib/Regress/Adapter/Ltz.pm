package Regress::Adapter::Ltz;
#
# ltz LTspice-circuit tests: run circuits through `ltz -b` (the build-area ltz
# wrapping the build-area Xyce; .asc schematics are converted to Xyce netlists,
# .cir are simulated directly). PASS = ltz/Xyce simulates the circuit cleanly
# (exit 0).
#
# Two corpora (params{corpus}):
#   bundled  (default) = ltz/tests/ltspice_circuits, <dir>/*.cir, one level
#   community          = ../ltz-tests, several cloned repos walked recursively
#                        for *.asc + *.cir
# --filter selects the top-level dir/repo name (e.g. circuits-ltspice,ecircuit).
#
# The golden reference for these is LTspice's own output (runs under Wine; not
# installed here yet) -- so for now this is a handling regression (clean run =
# pass); add the LTspice comparison like xyce/ihp-pdk once LTspice is available.
#
use strict;
use warnings;
use File::Find ();
use Regress::Tools qw(ltz_bin ltz_tests_dir ltz_community_dir xyce_bin xyce_libdir);
use Regress::Util  qw(run_capture);

sub run {
    my ($class, $block, %opt) = @_;

    my $ltz = ltz_bin() or return _err('ltz not found');
    my $community = (($block->{params}{corpus} // '') eq 'community');
    my $root = $community ? ltz_community_dir() : ltz_tests_dir();
    return _err('ltz ' . ($community ? 'community ' : '') . 'corpus not found') unless $root;

    # Validate the build-area Xyce (per project rule).
    my %env;
    if (my $x = xyce_bin())    { $env{XYCE} = $x; }
    if (my $l = xyce_libdir()) { $env{LD_LIBRARY_PATH} = $l; }

    my %want = map { $_ => 1 }
        (defined $opt{filter} && length $opt{filter}) ? split(/,/, $opt{filter}) : ();

    my @tests = $community ? _collect_recursive($root) : _collect_bundled($root);

    my @r;
    for my $t (@tests) {
        my ($top) = split m{/}, $t->{name}, 2;
        next if %want && !$want{$top};
        my ($rc, $out) = run_capture([$ltz, '-b', $t->{file}],
            dir => $t->{dir}, env => \%env, log => $opt{log});
        my $status = ($rc == 0) ? 'pass' : 'fail';
        my $msg = ($rc == 0) ? 'ltz/Xyce ok'
                : "ltz -b rc=$rc"
                  . (($out // '') =~ /Xyce Abort|MSG_FATAL|MSG_ERROR/ ? ' (sim error)' : '');
        push @r, { test_name => $t->{name}, status => $status,
                   message => $msg, log_path => $opt{log} };
    }

    return _err('no ltz circuits found') unless @r;
    return { exit_code => 0, results => \@r };
}

# Bundled corpus: <dir>/*.cir, one level deep.
sub _collect_bundled {
    my ($root) = @_;
    opendir(my $dh, $root) or return ();
    my @dirs = sort grep { -d "$root/$_" && !/^\./ } readdir $dh;
    closedir $dh;
    my @t;
    for my $d (@dirs) {
        my $ddir = "$root/$d";
        opendir(my $cd, $ddir) or next;
        for my $cir (sort grep { /\.cir$/i && -f "$ddir/$_" } readdir $cd) {
            push @t, { name => "$d/$cir", dir => $ddir, file => $cir };
        }
        closedir $cd;
    }
    return @t;
}

# Community corpus: walk recursively for *.asc + *.cir (skip VCS metadata).
sub _collect_recursive {
    my ($root) = @_;
    my @t;
    File::Find::find({ no_chdir => 1, wanted => sub {
        my $p = $File::Find::name;
        return if $p =~ m{/\.git(/|$)};
        return unless -f $p && $p =~ /\.(?:asc|cir)$/i;
        (my $rel = $p) =~ s{^\Q$root\E/?}{};
        (my $dir = $p) =~ s{/[^/]+$}{};
        (my $file = $p) =~ s{.*/}{};
        push @t, { name => $rel, dir => $dir, file => $file };
    } }, $root);
    return sort { $a->{name} cmp $b->{name} } @t;
}

sub _err {
    my ($msg) = @_;
    return { exit_code => 127, results => [
        { test_name => 'ltz', status => 'error', message => $msg } ] };
}

1;
