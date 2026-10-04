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
# Work area: the runners work in their cwd (vsim, log/, work/, vhdl/, vpi_log/,
# the report file, and whatever the tests themselves write), so two runs in one
# ivtest tree trample each other (a concurrent run corrupted the other's
# vsim/nvc WORK library).  Each block run therefore works in a private area,
# <workdir>/ivtest-<block>/, holding a symlink to every source entry of the
# ivtest tree (the lists, the runners, perl-lib/, ivltests/, gold/, ...) and
# nothing else; the tree itself is only read.  REGRESS_IVTEST_SHARED=1 runs in
# the ivtest tree itself, as before.
#
# --filter: the runner reads its own lists as in an unfiltered run, and
# Regress::IvtestFilter (loaded into it with -M) keeps the tests whose names
# match; a filter that selects no test is an error, never a whole-suite run.
#
use strict;
use warnings;
use Cwd qw(abs_path);
use File::Basename qw(dirname);
use File::Path qw(make_path);
use Regress::IvtestFilter;
use Regress::Tools qw(src_root);
use Regress::Util  qw(run_capture);

sub ivtest_dir { src_root() . '/iverilog/ivtest' }

# the harness's lib/ (Regress::IvtestFilter), for the runner's -I
my $LIBDIR = abs_path(dirname(__FILE__) . '/../..');

# The lists each runner reads when it is given none, in its own order (the
# filter pre-check below; the runner itself does the real reading).
# vvp_reg.pl and vlog95_reg.pl read regress-fsv.list first with --force-sv only.
my %RUNNER_LISTS = (
    'vvp_reg.pl'      => [qw(regress-ivl1.list regress-vlg.list regress-sv.list
                             regress-vhdl.list regress-synth.list)],
    'vlog95_reg.pl'   => [qw(regress-vlog95.list regress-ivl1.list regress-vlg.list
                             regress-sv.list regress-vhdl.list regress-synth.list)],
    'vhdl_nvc_reg.pl' => [qw(vhdl_regress.list)],
    'vhdl_reg.pl'     => [qw(vhdl_regress.list)],
    'vpi_reg.pl'      => [qw(vpi_regress.list)],
);

# Runtime products of the runners: never linked into a work area.
my %RUNTIME = map { $_ => 1 } qw(log work vhdl vsim vpi_log ivl_vhdl_work);

sub run {
    my ($class, $block, %opt) = @_;
    my $p   = $block->{params};
    my $dir = ivtest_dir();
    (my $safe = $block->{name}) =~ s{[/ ]}{_}g;
    my $log = $opt{log};
    my $filter = (defined $opt{filter} && length $opt{filter}) ? $opt{filter} : undef;

    # a filter that matches no test name in the runner's lists: an error, no run
    if (defined $filter) {
        my $why = filter_check($block, $filter);
        return _error_result($block, $why, $log) if defined $why;
    }

    my $env_extra = {};
    my @cmd = ('perl');
    if (defined $filter) {
        push @cmd, "-I$LIBDIR", '-MRegress::IvtestFilter=runner';
        $env_extra->{REGRESS_IVTEST_FILTER} = $filter;
    }
    push @cmd, $p->{runner};
    push @cmd, "--suffix=$p->{suffix}" if defined $p->{suffix} && length $p->{suffix};
    push @cmd, @{ $p->{args} } if $p->{args};
    push @cmd, @{ $p->{lists} } if $p->{lists};

    # Instrument the tools the runner calls with wrappers that (a) time each
    # invocation per test (keyed by the source file) and (b) optionally enforce
    # a process-group-killing timeout. Timing gives per-test run time; the
    # timeout bounds the nvc shim path, which can hang on invalid input where
    # native iverilog returns instantly.
    my $timeout = $opt{timeout} // $block->{params}{timeout};
    my $timingfile = $opt{workdir} ? "$opt{workdir}/timing-$safe.tsv" : undef;
    unlink $timingfile if $timingfile && -e $timingfile;
    my ($env, $pp) = _instrument($block, $timeout, $opt{workdir}, $timingfile);
    $env->{$_} = $env_extra->{$_} for keys %$env_extra;

    my $cwd = $ENV{REGRESS_IVTEST_SHARED} ? $dir : work_area($dir, $opt{workdir}, $safe);

    my ($exit, $out) = run_capture(\@cmd,
        dir          => $cwd,
        env          => $env,
        path_prepend => $pp,
        log          => $log,
    );

    my @results = _parse($out, $log);
    if (defined $filter && !@results
            && $out =~ /^\Q$Regress::IvtestFilter::TAG\E: (.*?)\s*$/m) {
        # the runner found nothing to run under the filter (Regress::IvtestFilter)
        return { exit_code => $exit,
                 results => [ _error_row($block, "$1 (block $block->{name})", $log) ] };
    }
    _attach_durations(\@results, $timingfile) if $timingfile && -f $timingfile;
    return { exit_code => $exit, results => \@results };
}

# The private work area of one block run (see the header): <workdir>/ivtest-<safe>,
# a symlink to each source entry of the ivtest tree $dir.  Without a workdir, a
# temporary directory.  An existing area is reused (the runners clear their own
# per-test files).
sub work_area {
    my ($dir, $workdir, $safe) = @_;
    my $src = abs_path($dir);
    die "regress: no ivtest tree at $dir\n" unless defined $src && -d $src;
    if (!$workdir) {
        require File::Temp;
        $workdir = File::Temp::tempdir('regress-ivtest-XXXXXX', TMPDIR => 1, CLEANUP => 1);
    }
    my $area = "$workdir/ivtest-$safe";
    # log/ and work/ as a used tree has them: vvp_reg.pl makes both, but
    # vhdl_nvc_reg.pl and vhdl_reg.pl only write into log/
    make_path($area, "$area/log", "$area/work");
    opendir(my $dh, $src) or die "regress: cannot read the ivtest tree $src: $!\n";
    my @entries = grep { _source_entry($src, $_) } readdir $dh;
    closedir $dh;
    die "regress: $src holds no ivtest runner (*_reg.pl)\n" unless grep { /_reg\.pl$/ } @entries;
    for my $e (sort @entries) {
        my $l = "$area/$e";
        next if -l $l && readlink($l) eq "$src/$e";
        unlink $l if -l $l;
        die "regress: $l exists and is not a link into the ivtest tree\n" if -e $l;
        symlink("$src/$e", $l) or die "regress: cannot link $l -> $src/$e: $!\n";
    }
    return $area;
}

# A source entry of the ivtest tree: a directory other than the runtime ones, or a
# list, runner, module or doc file.  Stray files a run left at the top of the tree
# (dump.vcd, report files, ...) are not linked, so no test writes through to them.
sub _source_entry {
    my ($dir, $e) = @_;
    return 0 if $e =~ /^\./ || $RUNTIME{$e};
    return 1 if -d "$dir/$e";
    return $e =~ /\.(?:list|pl|pm|py)$/ || $e =~ /^(?:regress|README.*|COPYING|find_valg_\w+)$/
        ? 1 : 0;
}

# undef when --filter $filter matches a test name in the lists the block's runner
# reads (its default lists, or the block's own `lists`), else why not (one line).
sub filter_check {
    my ($block, $filter) = @_;
    my $p = $block->{params};
    my $dir = ivtest_dir();
    my @lists = $p->{lists} ? @{ $p->{lists} } : @{ $RUNNER_LISTS{ $p->{runner} } // [] };
    unshift @lists, 'regress-fsv.list'
        if !$p->{lists} && grep { $_ eq '--force-sv' } @{ $p->{args} // [] };
    return undef unless @lists;                  # a runner we do not know: let it run
    my $re = Regress::IvtestFilter::regex($filter);
    my ($n, %seen) = (0);
    for my $lf (@lists) {
        my $path = $lf =~ m{^/} ? $lf : "$dir/$lf";
        open my $fh, '<', $path or return undef; # cannot tell: the runner will complain
        while (my $l = <$fh>) {
            next if $l =~ /^\s*(?:#|$)/;
            my ($name) = $l =~ /^\s*(\S+)/;
            $name =~ s/^[^:]*://;                # v13:name, -S:name
            next if $seen{$name}++;
            $n++;
            return undef if $name =~ $re;
        }
        close $fh;
    }
    return sprintf("--filter '%s' selects none of the %d tests %s reads (%s) for block %s",
                   $filter, $n, $p->{runner}, join(' ', @lists), $block->{name});
}

sub _error_row {
    my ($block, $msg, $log) = @_;
    return { test_name => $block->{name}, status => 'error', message => $msg,
             duration_ms => undef, log_path => $log };
}

# An error result without running the runner; the message goes to the block log too.
sub _error_result {
    my ($block, $msg, $log) = @_;
    if ($log && open my $w, '>', $log) { print $w "regress: $msg\n"; close $w }
    return { exit_code => 2, results => [ _error_row($block, $msg, $log) ] };
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
# whole group on timeout so the shim's iverilog/nvc grandchildren die too.  The
# watchdog is a job of its own (set -m): the wrapper ends it as a group, so its
# sleep does not linger holding the caller's stdout (a runner reading
# `iverilog -V` through a pipe waited for the whole timeout).
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
[ -n "\$w" ] && kill -- -"\$w" 2>/dev/null
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

1;
