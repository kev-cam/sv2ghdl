package Regress::Adapter::Rtlmeter;
#
# Adapter for RTLMeter (github.com/verilator/rtlmeter) under either native
# verilator or the verilator-sv2ghdl shim (nvc backend).
#
# RTLMeter drives verilator via its own CLI; selecting the shim is done by
# putting verilator-sv2ghdl earlier on PATH as `verilator` OR via $VERILATOR.
# Pass/fail is by exit status of the rtlmeter run (and the design's own
# TEST_PASSED / _rtlmeter_cycles.txt contract, handled by the shim).
#
# NOTE: the exact rtlmeter invocation is finalized once the tree is present;
# until then run() reports the block as not-ready rather than guessing.
#
use strict;
use warnings;
use Regress::Tools qw(src_root verilator_bin);
use Regress::Util  qw(run_capture);

sub rtlmeter_dir { $ENV{RTLMETER_DIR} || src_root() . '/rtlmeter' }

sub ready {
    my $d = rtlmeter_dir();
    return (-d $d && (-x "$d/rtlmeter" || -f "$d/rtlmeter")) ? 1 : 0;
}

sub run {
    my ($class, $block, %opt) = @_;
    my $d = rtlmeter_dir();
    unless (ready()) {
        return { exit_code => 0, results => [
            { test_name => $block->{name}, status => 'skip',
              message => "rtlmeter not present at $d" } ] };
    }

    # rtlmeter's CLI: `run --cases '<pat> <pat> ...'` — ONE space-separated
    # fnmatch-pattern argument (+tag / !exclude supported); there is no --sim
    # flag (the simulator is whatever `verilator` resolves to — the shim block
    # sets $VERILATOR/PATH via Block.pm engine env). --filter overrides cases.
    my $cases = $block->{params}{cases} || 'VeeR-EH1:default:hello';
    $cases = join(' ', split(/,/, $opt{filter}))
        if defined $opt{filter} && length $opt{filter};

    # Engine-specific workRoot: rtlmeter reuses the FIRST compiled simulator
    # found under <workRoot>/<design>/<config>/compile-0, so native verilator
    # and the nvc shim MUST NOT share a workRoot or one silently executes the
    # other's artifacts.
    (my $eng = $block->{engine} || 'default') =~ s/[^\w.-]/_/g;
    my $work = "$d/work-$eng";

    my @cmd = ('./rtlmeter', 'run', '--cases', $cases,
               '--workRoot', $work, '--timeout', '60');

    my ($exit, $out) = run_capture(\@cmd,
        dir => $d, env => $block->{env}, path_prepend => $block->{path_prepend},
        log => $opt{log});

    # One record per case from rtlmeter's summary; fall back to a single
    # block-level record keyed on exit status.
    my @r;
    for my $line (split /\n/, $out) {
        # rtlmeter prints per-case lines; refine once the tree is inspected.
        if ($line =~ /^\s*(\S+)\s+.*\b(PASS|FAIL|OK|ERROR)\b/i) {
            my ($name, $verdict) = ($1, uc $2);
            my $st = ($verdict eq 'PASS' || $verdict eq 'OK') ? 'pass'
                   : ($verdict eq 'FAIL') ? 'fail' : 'error';
            push @r, { test_name => $name, status => $st, message => $verdict,
                       log_path => $opt{log} };
        }
    }
    unless (@r) {
        push @r, { test_name => $block->{name},
                   status => ($exit == 0 ? 'pass' : 'fail'),
                   message => "exit=$exit", log_path => $opt{log} };
    }
    return { exit_code => $exit, results => \@r };
}

1;
