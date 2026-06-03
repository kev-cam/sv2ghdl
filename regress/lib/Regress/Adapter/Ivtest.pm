package Regress::Adapter::Ivtest;
#
# Adapter for the Icarus Verilog regression suite (iverilog/ivtest).
#
# Reuses the existing *_reg.pl runners (vvp_reg.pl, vhdl_nvc_reg.pl, ...) and
# their perl-lib/ modules — we do NOT reimplement them. The runners print one
# line per test to stdout ("<name>: <status>") plus a "Test results:" summary
# (Reporting::print_rpt writes to both stdout and a report file). We capture
# stdout and parse those lines into normalized records.
#
# The --suffix mechanism makes the runner invoke iverilog$sfx / vvp$sfx, i.e.
# the shims for the nvc engines; $NVC / $NVC_LIBDIR are honored by
# vhdl_nvc_reg.pl. Both are supplied via the engine's env (see Regress::Block).
#
use strict;
use warnings;
use File::Path qw(make_path);
use Regress::Tools qw(src_root);
use Regress::Util  qw(run_capture);

sub ivtest_dir { src_root() . '/iverilog/ivtest' }

# Lists each runner actually consumes — used when synthesizing a filtered
# subset so we don't feed e.g. vpi_regress.list (different column format) to
# vvp_reg.pl. For a full (unfiltered) run we pass no list and the runner uses
# its own built-in defaults.
my %RUNNER_LISTS = (
    'vvp_reg.pl'      => [qw(regress-vvp.list regress-sv.list regress-vlg.list
                             regress-fsv.list regress-ivl1.list regress-synth.list)],
    'vlog95_reg.pl'   => [qw(regress-vvp.list regress-sv.list regress-vlg.list)],
    'vhdl_nvc_reg.pl' => [qw(vhdl_regress.list)],
    'vhdl_reg.pl'     => [qw(vhdl_regress.list)],
    'vpi_reg.pl'      => [qw(vpi_regress.list)],
);

sub run {
    my ($class, $block, %opt) = @_;
    my $p   = $block->{params};
    my $dir = ivtest_dir();

    my @cmd = ('perl', $p->{runner});
    push @cmd, "--suffix=$p->{suffix}" if defined $p->{suffix} && length $p->{suffix};
    push @cmd, @{ $p->{args} } if $p->{args};

    # Optional filtering to a subset (smoke runs). ivtest runners take a
    # regress-list *file*, not test names, so synthesize a temp list of
    # matching lines from the candidate lists.
    my @lists = $p->{lists} ? @{ $p->{lists} } : ();
    my $tmpfile;
    if (defined $opt{filter} && length $opt{filter}) {
        my @cand = @lists ? @lists : @{ $RUNNER_LISTS{ $p->{runner} } // [] };
        $tmpfile = _filtered_list($dir, \@cand, $opt{filter});
        @lists = ($tmpfile) if $tmpfile;
    }
    push @cmd, @lists;

    # Instrument the tools the runner calls with wrappers that (a) time each
    # invocation per test (keyed by the source file) and (b) optionally enforce
    # a process-group-killing timeout. Timing gives per-test run time; the
    # timeout bounds the nvc shim path, which can hang on invalid input where
    # native iverilog returns instantly.
    my $timeout = $opt{timeout} // $block->{params}{timeout};
    (my $safe = $block->{name}) =~ s{[/ ]}{_}g;
    my $timingfile = $opt{workdir} ? "$opt{workdir}/timing-$safe.tsv" : undef;
    unlink $timingfile if $timingfile && -e $timingfile;
    my ($env, $pp) = _instrument($block, $timeout, $opt{workdir}, $timingfile);

    my $log = $opt{log};
    my ($exit, $out) = run_capture(\@cmd,
        dir          => $dir,
        env          => $env,
        path_prepend => $pp,
        log          => $log,
    );

    unlink $tmpfile if $tmpfile;   # don't leave temp filter lists in ivtest/

    my @results = _parse($out, $log);
    _attach_durations(\@results, $timingfile) if $timingfile && -f $timingfile;
    return { exit_code => $exit, results => \@results };
}

# Build timing/timeout wrappers for the tools this runner invokes and prepend
# their dir to PATH. Returns (\%env, $path_prepend). Wraps the suffixed
# iverilog/vvp and (for nvc engines) nvc via $NVC. Sets TIMING_FILE so the
# wrappers log per-test elapsed; $timeout (may be undef/0) bounds each call.
sub _instrument {
    my ($block, $timeout, $workdir, $timingfile) = @_;
    my $p   = $block->{params};
    my %env = %{ $block->{env} // {} };
    my $pp  = $block->{path_prepend} // '';
    return (\%env, $pp) unless $workdir;     # nothing to write to
    (my $safe = $block->{name}) =~ s{[/ ]}{_}g;
    my $bindir = "$workdir/to-$safe";
    make_path($bindir);
    $env{TIMING_FILE} = $timingfile if $timingfile;
    my $to = $timeout && $timeout > 0 ? $timeout : 0;

    my $sfx = $p->{suffix} // '';
    my @tools = $p->{runner} =~ /^(?:vvp_reg|vlog95_reg)\.pl$/ ? ("iverilog$sfx", "vvp$sfx")
              : $p->{runner} eq 'vhdl_nvc_reg.pl'              ? ("iverilog")
              : ();
    my @search = (split(/:/, $pp), split(/:/, $ENV{PATH} // ''));
    for my $t (@tools) {
        my ($real) = grep { -x $_ } map { "$_/$t" } @search;
        _write_wrapper("$bindir/$t", $to, $real) if $real;
    }
    if ($env{NVC}) {                          # nvc -a <test>.vhd is keyable too
        _write_wrapper("$bindir/nvc.to", $to, $env{NVC});
        $env{NVC} = "$bindir/nvc.to";
    }
    return (\%env, join(':', $bindir, $pp));
}

# Wrapper: runs the real tool in its own process group; logs per-test elapsed
# (keyed by the source-file basename) to $TIMING_FILE; if TIMEOUT>0, kills the
# whole group on timeout so the shim's iverilog/nvc grandchildren die too.
sub _write_wrapper {
    my ($path, $timeout, $real) = @_;
    open my $w, '>', $path or return;
    print $w <<"SH";
#!/bin/bash
set -m
key=""
for a in "\$@"; do case "\$a" in *.v|*.sv|*.vhd) b=\${a##*/}; key=\${b%.*};; esac; done
s=\$(date +%s.%N)
"$real" "\$@" &
p=\$!
w=""
if [ "$timeout" -gt 0 ]; then
  ( sleep $timeout; kill -TERM -"\$p" 2>/dev/null; sleep 5; kill -KILL -"\$p" 2>/dev/null ) &
  w=\$!
fi
wait "\$p"; rc=\$?
[ -n "\$w" ] && kill "\$w" 2>/dev/null
e=\$(date +%s.%N)
if [ -n "\$key" ] && [ -n "\$TIMING_FILE" ]; then
  awk "BEGIN{printf \\"%s\\t%d\\n\\",\\"\$key\\",(\$e-\$s)*1000}" >> "\$TIMING_FILE"
fi
exit \$rc
SH
    close $w;
    chmod 0755, $path;
}

# read the timing file (key<TAB>ms, possibly multiple lines per key) and set
# duration_ms on results whose test_name matches a key (summing invocations).
sub _attach_durations {
    my ($results, $timingfile) = @_;
    open my $fh, '<', $timingfile or return;
    my %ms;
    while (<$fh>) { my ($k, $v) = split /\t/; $ms{$k} += $v if defined $v }
    close $fh;
    for my $r (@$results) {
        $r->{duration_ms} = $ms{ $r->{test_name} } if exists $ms{ $r->{test_name} };
    }
}

# Map an ivtest status string to the canonical vocabulary.
sub _status {
    local $_ = shift;
    return 'notimpl' if /^Not Implemented/i;
    return 'xfail'   if /^Passed\s*-\s*expected fail/i;
    return 'pass'    if /^Passed/i;
    return 'fail'    if /^Failed/i;
    return 'error';
}

sub _parse {
    my ($out, $log) = @_;
    my @r;
    for my $line (split /\n/, $out) {
        # Failure lines can carry a "==> " annotation before the status word,
        # e.g. "hello1: ==> Failed - running iverilog."
        next unless $line =~ /^\s*(\S+):\s+(?:==>\s*)?((?:Passed|Failed|Not Implemented).*?)\s*$/;
        my ($name, $msg) = ($1, $2);
        push @r, {
            test_name   => $name,
            status      => _status($msg),
            message     => $msg,
            duration_ms => undef,
            log_path    => $log,
        };
    }
    return @r;
}

# Build a temp regress-list of lines whose first field matches $filter,
# pulled from the given lists (or every regress-*.list / *_regress.list in
# the dir if none were specified).
sub _filtered_list {
    my ($dir, $lists, $filter) = @_;
    my @candidates = @$lists;
    unless (@candidates) {
        opendir(my $dh, $dir) or return undef;
        @candidates = grep { /^regress-.*\.list$/ || /_regress\.list$/ || /^vhdl_regress\.list$/ }
                      readdir $dh;
        closedir $dh;
    }
    my $re = eval { qr/$filter/ } || qr/\Q$filter\E/;
    my @hit;
    for my $lf (@candidates) {
        my $path = $lf =~ m{/} ? $lf : "$dir/$lf";
        open my $fh, '<', $path or next;
        while (my $l = <$fh>) {
            next if $l =~ /^\s*#/ || $l =~ /^\s*$/;
            my ($first) = $l =~ /^\s*(\S+)/;
            push @hit, $l if defined $first && $first =~ $re;
        }
        close $fh;
    }
    return undef unless @hit;
    my $tmp = "$dir/.regress-filter.$$.list";
    open my $w, '>', $tmp or return undef;
    print $w @hit;
    close $w;
    return $tmp;
}

1;
