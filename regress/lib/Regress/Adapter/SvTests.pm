package Regress::Adapter::SvTests;
#
# Adapter for the CHIPS-Alliance sv-tests corpus (/usr/local/src/sv-tests).
#
# We drive the existing Makefile restricted to a single runner:
#     make tests RUNNERS=<runner> OUT_DIR=<fresh> TESTS=<subset?> -j<N>
# tools/runner logs "PASS: <runner>/<test>", "FAIL: ...", "Skipping ..." to
# stderr for every test; we parse those from the captured output. A fresh
# per-block OUT_DIR avoids make treating prior logs as up-to-date and skipping.
#
# The block's engine selects the runner name (e.g. 'verilator', 'icarus'); the
# corresponding tool must be discoverable by sv-tests' check-runners (on PATH).
#
use strict;
use warnings;
use File::Path qw(remove_tree);
use Regress::Tools qw(src_root);
use Regress::Util  qw(run_capture);

sub svtests_dir { src_root() . '/sv-tests' }

sub run {
    my ($class, $block, %opt) = @_;
    my $runner = $block->{params}{runner} or return {
        exit_code => 2,
        results   => [ { test_name => $block->{name}, status => 'error',
                         message => 'no sv-tests runner configured' } ] };

    my $dir   = svtests_dir();
    my $jobs  = $opt{jobs} || 8;
    (my $safe = $block->{name}) =~ s{[/ ]}{_}g;
    my $outdir = ($opt{workdir} || "$dir/out") . "/svtests-$safe";
    # always start from an empty OUT_DIR, else make sees prior per-test logs as
    # up-to-date and emits no PASS/FAIL verdicts (0 tests).
    remove_tree($outdir) if -d $outdir;

    # RUNNER_PARAM= drops the Makefile's default --quiet so tools/runner logs
    # at DEBUG and emits the "PASS:/FAIL:/Skipping" verdict lines we parse.
    # The two sv-tests blocks must not run concurrently (shared make state in
    # the sv-tests tree) — the dispatcher serializes the 'sv-tests' suite.
    my @cmd = ('make', 'tests', "RUNNERS=$runner", "OUT_DIR=$outdir",
               'RUNNER_PARAM=', "-j$jobs");
    if (defined $opt{filter} && length $opt{filter}) {
        # TESTS is relative to tests/; let the caller pass a relative .sv path
        push @cmd, "TESTS=$opt{filter}";
    }

    my ($exit, $out) = run_capture(\@cmd,
        dir => $dir, env => $block->{env}, path_prepend => $block->{path_prepend},
        log => $opt{log});

    # Lines look like "INFO    | PASS: Verilator/<test>" / "WARNING | FAIL: ..."
    # (logging level prefix, then the verdict). Tolerate the prefix.
    my @r;
    for my $line (split /\n/, $out) {
        if ($line =~ m{(?:^|\|\s*)(PASS|FAIL):\s*\Q$runner\E/(\S+)}) {
            push @r, { test_name => $2, status => ($1 eq 'PASS' ? 'pass' : 'fail'),
                       message => $1, log_path => "$outdir/logs/$runner/$2.log" };
        }
        elsif ($line =~ m{Skipping\s+\Q$runner\E/(\S+)}) {
            push @r, { test_name => $1, status => 'skip', message => 'skipped',
                       log_path => "$outdir/logs/$runner/$1.log" };
        }
    }
    # per-test run time from each log's "time_elapsed: <seconds>" metadata
    for my $rec (@r) {
        next unless $rec->{log_path} && -f $rec->{log_path};
        open my $fh, '<', $rec->{log_path} or next;
        while (<$fh>) {
            if (/^\s*time_elapsed\s*:\s*([\d.]+)/) { $rec->{duration_ms} = int($1 * 1000); last }
            last if /^\s*$/;   # metadata ends at the blank line
        }
        close $fh;
    }
    return { exit_code => $exit, results => \@r };
}

1;
