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
use Regress::RawCompare qw(compare_raw);

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

        # Schematic-only sources (no simulation directive anywhere) are not
        # runnable tests: LTspice users add .tran/.ac interactively. Skip,
        # matching the bundled-corpus policy for directive-less library decks.
        if (!_has_analysis_directive("$t->{dir}/$t->{file}")) {
            push @r, { test_name => $t->{name}, status => 'skip',
                       message => 'no analysis directive in source (schematic-only)',
                       log_path => $opt{log} };
            next;
        }

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

# ltz's bundled model library, spelled so the gold LTspice can open it: a
# native Windows LTspice (interop) needs a Windows path (resolve symlinks,
# /mnt/<d>/ -> <D>:\); a Wine LTspice opens POSIX paths via the Z: drive.
sub _ltz_lib_for {
    my ($exe) = @_;
    my $ltz = ltz_bin() or return undef;
    (my $lib = $ltz) =~ s{/bin/ltz$}{/lib/standard.lib};
    return undef unless -f $lib;
    return $lib if $exe =~ m{/ltwine/};
    require Cwd;
    my $real = Cwd::realpath($lib) // $lib;
    if ($real =~ m{^/mnt/([a-z])/(.*)$}i) {
        my ($drv, $rest) = (uc $1, $2);
        $rest =~ s{/}{\\}g;
        return "$drv:\\$rest";
    }
    return $real;
}

# Does the source carry a simulation directive? .cir/.net: a dot-analysis
# line. .asc: a TEXT record whose payload is a !-prefixed analysis directive.
sub _has_analysis_directive {
    my ($path) = @_;
    open my $fh, '<:raw', $path or return 1;   # unreadable: let the run report it
    local $/; my $s = <$fh>; close $fh;
    return 1 if $s =~ /^\s*\.(?:tran|ac|dc|op|noise|four|tf|step)\b/im;
    # .asc: a TEXT !-block holds the whole directive text on one record with
    # literal \n escapes; the analysis line can sit anywhere inside the block
    # (after params/measures), so accept '!' or an escaped newline before it.
    return 1 if $path =~ /\.asc$/i && $s =~ /(?:!|\\n)\s*\.(?:tran|ac|dc|op|noise|four|tf)\b/i;
    return 0;
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
      # Parity with the Xyce side: ltz injects its bundled standard.lib, so
      # decks may reference models (1N4148 etc.) they don't define. Give the
      # gold run the same models. Netlists only -- .asc text is a schematic.
      if ($ext eq 'net' && (my $lib = _ltz_lib_for($exe))) {
          $c =~ s/\n/\n.lib "$lib"\n/;     # after the title line
      }
      open my $o, '>', "$ddir/$refin" or return (undef, 'gold: write fail');
      print $o $c; close $o; }
    my $gold = "$ddir/${base}_ltref.raw";
    unlink $gold;
    my $wrc;
    if ($exe =~ m{/ltwine/}) {
        # Wine-prefixed LTspice (Linux host)
        my %wenv = ( WINEPREFIX     => "$ENV{HOME}/ltwine",
                     XDG_RUNTIME_DIR=> "$ENV{HOME}/.xdg",
                     WINEDEBUG      => '-all' );
        ($wrc) = run_capture(['xvfb-run', '-a', 'wine', $exe, '-b', '-ascii', $refin],
            dir => $ddir, env => \%wenv, log => $log);
    } else {
        # native Windows LTspice, reached from WSL via interop
        ($wrc) = run_capture([$exe, '-b', '-ascii', $refin],
            dir => $ddir, log => $log);
    }
    my @res = (-f $gold) ? compare_raw($gold, $xraw)
                         : (undef, "LTspice produced no .raw (rc=$wrc)");
    # tidy up the reference inputs/outputs
    unlink glob("$ddir/${base}_ltref.*");
    return @res;
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
