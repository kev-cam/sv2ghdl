package Regress::Adapter::Ltz;
#
# ltz LTspice-circuit tests: run circuits through `ltz -b` (build-area ltz +
# Xyce; .asc schematics convert to Xyce netlists, .cir simulate directly).
#
# Corpora (params{corpus}): bundled = ltz/tests/ltspice_circuits (<dir>/*.cir,
# one level); community = ../ltz-tests (cloned repos, recursive *.asc + *.cir).
# --filter selects the top-level dir/repo name.
#
# Golden reference = LTspice's own output. When LTspice (under Wine) is
# available, each circuit is ALSO run through LTspice headless
# (xvfb-run wine LTspice.exe -b -ascii) and its SPICE .raw compared to Xyce's,
# aligned by variable name (V(...)) with magnitude + relative tolerance. Per
# the analog rule the delta is reported, not gating -- PASS = ltz/Xyce simulates
# cleanly (params{fail_rtol} optionally fails on gross divergence). With no
# LTspice, it degrades to a handling-only regression.
#
use strict;
use warnings;
use File::Find ();
use Regress::Tools qw(ltz_bin ltz_tests_dir ltz_community_dir xyce_bin xyce_libdir ltspice_bin);
use Regress::Util  qw(run_capture);

sub run {
    my ($class, $block, %opt) = @_;

    my $ltz = ltz_bin() or return _err('ltz not found');
    my $community = (($block->{params}{corpus} // '') eq 'community');
    my $root = $community ? ltz_community_dir() : ltz_tests_dir();
    return _err('ltz ' . ($community ? 'community ' : '') . 'corpus not found') unless $root;

    my %env;
    if (my $x = xyce_bin())    { $env{XYCE} = $x; }
    if (my $l = xyce_libdir()) { $env{LD_LIBRARY_PATH} = $l; }

    # LTspice gold comparison is on whenever LTspice is found (unless disabled).
    my $lts = (($block->{params}{ltspice} // 1) ? ltspice_bin() : undef);
    my $fail_rtol = $block->{params}{fail_rtol};

    my %want = map { $_ => 1 }
        (defined $opt{filter} && length $opt{filter}) ? split(/,/, $opt{filter}) : ();

    my @tests = $community ? _collect_recursive($root) : _collect_bundled($root);

    my @r;
    for my $t (@tests) {
        my ($top) = split m{/}, $t->{name}, 2;
        next if %want && !$want{$top};

        (my $base = $t->{file}) =~ s/\.[^.]+$//;
        my $xraw = "$t->{dir}/$base.raw";
        unlink $xraw;
        my ($rc) = run_capture([$ltz, '-b', $t->{file}],
            dir => $t->{dir}, env => \%env, log => $opt{log});
        if ($rc != 0 || !-f $xraw) {
            push @r, { test_name => $t->{name}, status => 'fail',
                       message => "ltz -b rc=$rc" . (-f $xraw ? '' : ' (no .raw)'),
                       log_path => $opt{log} };
            next;
        }

        my $msg = 'ltz/Xyce ok';
        my $status = 'pass';
        if ($lts) {
            my ($maxrel, $cmp) = _vs_ltspice($lts, $t->{dir}, $t->{file}, $base, $xraw, $opt{log});
            $msg = "ltz/Xyce ok; vs LTspice: $cmp";
            $status = 'fail'
                if defined $fail_rtol && defined $maxrel && $maxrel > $fail_rtol;
        }
        push @r, { test_name => $t->{name}, status => $status,
                   message => $msg, log_path => $opt{log} };
    }

    return _err('no ltz circuits found') unless @r;
    return { exit_code => 0, results => \@r };
}

# Run the circuit through LTspice headless and compare its .raw to Xyce's.
# Returns ($max_rel_err, $summary).
sub _vs_ltspice {
    my ($exe, $ddir, $cir, $base, $xraw, $log) = @_;
    my $ext = ($cir =~ /\.asc$/i) ? 'asc' : 'net';   # LTspice runs .asc native
    my $refin = "${base}_ltref.$ext";
    # copy the circuit to a distinct name so LTspice's .raw doesn't clobber Xyce's
    { local $/; open my $i, '<', "$ddir/$cir" or return (undef, 'gold: read fail');
      my $c = <$i>; close $i;
      open my $o, '>', "$ddir/$refin" or return (undef, 'gold: write fail');
      print $o $c; close $o; }
    my $gold = "$ddir/${base}_ltref.raw";
    unlink $gold;
    my %wenv = ( WINEPREFIX     => "$ENV{HOME}/ltwine",
                 XDG_RUNTIME_DIR=> "$ENV{HOME}/.xdg",
                 WINEDEBUG      => '-all' );
    my ($wrc) = run_capture(['xvfb-run', '-a', 'wine', $exe, '-b', '-ascii', $refin],
        dir => $ddir, env => \%wenv, log => $log);
    my @res = (-f $gold) ? _compare_raw($gold, $xraw)
                         : (undef, "LTspice produced no .raw (rc=$wrc)");
    # tidy up the reference inputs/outputs
    unlink glob("$ddir/${base}_ltref.*");
    return @res;
}

# --- SPICE rawfile compare (LTspice ascii / Xyce binary; real or complex) ----
sub _read_raw {
    my ($file) = @_;
    open my $fh, '<:raw', $file or return undef;
    local $/; my $blob = <$fh>; close $fh;
    my ($mode, $hdr, $body);
    if    ($blob =~ /\A(.*?\n)(Binary:\s*\n)(.*)\z/s) { $mode = 'bin';   $hdr = $1; $body = $3; }
    elsif ($blob =~ /\A(.*?\n)(Values:\s*\n)(.*)\z/s) { $mode = 'ascii'; $hdr = $1; $body = $3; }
    else { return undef; }
    my ($nv) = $hdr =~ /No\.\s*Variables:\s*(\d+)/i;  return undef unless $nv;
    my ($np) = $hdr =~ /No\.\s*Points:\s*(\d+)/i;     return undef unless $np;
    my $cplx = $hdr =~ /Flags:[^\n]*\bcomplex\b/i ? 1 : 0;
    my @names;
    if ($hdr =~ /\nVariables:\s*\n(.*)\z/s) {
        for my $ln (split /\n/, $1) { push @names, $1 if $ln =~ /^\s*\d+\s+(\S+)/; }
    }
    return undef unless @names == $nv;
    my %col; $col{$names[$_]} = $_ for 0 .. $#names;
    my @rows;
    if ($mode eq 'bin') {
        my $per = $cplx ? 2 : 1;
        my @d = unpack('d<*', $body);
        for my $p (0 .. $np - 1) {
            my @row;
            for my $v (0 .. $nv - 1) {
                my $i = ($p * $nv + $v) * $per;
                push @row, $cplx ? sqrt(($d[$i] // 0)**2 + ($d[$i+1] // 0)**2) : ($d[$i] // 0);
            }
            push @rows, \@row;
        }
    } else {
        my @toks = grep { /\S/ } split /\n/, $body;
        my $i = 0;
        for my $p (0 .. $np - 1) {
            my @row;
            for my $v (0 .. $nv - 1) {
                my $ln = $toks[$i++] // '';
                $ln =~ s/^\s*\d+\s+// if $v == 0;
                $ln =~ s/^\s+//;
                push @row, ($ln =~ /,/)
                    ? do { my ($re, $im) = split /,/, $ln; sqrt($re**2 + $im**2) }
                    : $ln + 0;
            }
            push @rows, \@row;
        }
    }
    return { names => \@names, col => \%col, rows => \@rows };
}

sub _compare_raw {
    my ($gold, $xraw) = @_;
    my $g = _read_raw($gold);  return (undef, 'unparsable LTspice .raw') unless $g;
    my $x = _read_raw($xraw);  return (undef, 'unparsable Xyce .raw')    unless $x;
    my @common = grep { exists $x->{col}{$_} && $_ ne $g->{names}[0] } @{ $g->{names} };
    return (undef, 'no common variables') unless @common;
    my $n = (@{$g->{rows}} < @{$x->{rows}}) ? scalar @{$g->{rows}} : scalar @{$x->{rows}};
    return (undef, 'no points') unless $n;
    my ($maxrel, $worst) = (0, '');
    for my $var (@common) {
        for my $p (0 .. $n - 1) {
            my $a = $g->{rows}[$p][$g->{col}{$var}];
            my $b = $x->{rows}[$p][$x->{col}{$var}];
            my $rel = abs($a - $b) / (abs($a) + 1e-12);
            ($maxrel, $worst) = ($rel, sprintf("%s LT=%.4g Xy=%.4g", $var, $a, $b))
                if $rel > $maxrel;
        }
    }
    return ($maxrel, sprintf("max rel err %.2f%% over %d vars x %d pts (%s)",
                             100 * $maxrel, scalar @common, $n, $worst));
}

# --- corpus collection -------------------------------------------------------
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
