package Regress::Adapter::Vamos;
#
# vamos's own tests and the VACASK<->nvc co-simulation demos: the mixed-signal
# acceptance of a VACASK build (and of the nvc/sv2ghdl it runs with).
#
# params{kind}:
#   unit   tests/vamos of this checkout: one `python3 -m unittest discover`
#          per module (vamos_testlib needs discover), up to 8 at once, as the
#          manual recipe does.  Test names are "<module>:<Class>.<test>"; a
#          module that dies before reporting (an import error, a timeout) is
#          one failing test named "<module>".  Skips (CMC examples, cadnip,
#          ngspice, ... not on this machine) are skips.
#   cosim  demo/cosim/run.sh min|a2d|glitch of the VACASK tree under test,
#          run from a copy outside it, against the VACASK build under test
#          (VCB) and the build-area nvc (NVCB).  Pass = exit 0.
#
# The VACASK build is $VAMOS_VACASK_HOME (set by the engine profile: a
# `regress candidate --repo VACASK` points it at the candidate's build), else
# <src root>/VACASK/build.  --filter: comma-separated module names (unit) or
# demo names (cosim).
#
use strict;
use warnings;
use File::Path ();
use File::Temp ();
use POSIX ();
use Regress::Tools qw(src_root nvc_bin python3_bin vamos_tests_dir vacask_build_dir);
use Regress::Util  qw(run_capture);

our $MODULE_TIMEOUT = $ENV{REGRESS_VAMOS_TIMEOUT} || 1800;
our $DEMO_TIMEOUT   = $ENV{REGRESS_COSIM_TIMEOUT} || 600;
our @DEMOS = qw(min a2d glitch);

# Runs one module as `python3 -m unittest discover -s DIR -p MODULE.py -v`
# would (its usual output goes to the log), and also writes one line per
# test to $REGRESS_VAMOS_RESULTS: "<status>\t<Class>.<test>\t<message>".
# (Parsing -v output loses tests whose warnings land between name and
# verdict.)
our $RUNNER = <<'PY';
import os, sys, unittest
d, mod, out = sys.argv[1], sys.argv[2], os.environ["REGRESS_VAMOS_RESULTS"]
rows = []
def name(t):
    tid = t.id().split(".")
    return ".".join(tid[-2:]) if len(tid) >= 2 else t.id()
def first(err):
    text = str(err[1]) if isinstance(err, tuple) else str(err)
    return " ".join(text.split())[:300]
class R(unittest.TextTestResult):
    def addSuccess(self, t): super().addSuccess(t); rows.append(("pass", name(t), ""))
    def addFailure(self, t, e): super().addFailure(t, e); rows.append(("fail", name(t), "FAIL: " + first(e)))
    def addError(self, t, e):
        super().addError(t, e)
        rows.append(("fail", name(t) if hasattr(t, "id") else str(t), "ERROR: " + first(e)))
    def addSkip(self, t, why): super().addSkip(t, why); rows.append(("skip", name(t), " ".join(str(why).split())[:300]))
    def addExpectedFailure(self, t, e): super().addExpectedFailure(t, e); rows.append(("xfail", name(t), ""))
    def addUnexpectedSuccess(self, t): super().addUnexpectedSuccess(t); rows.append(("fail", name(t), "unexpected success"))
    def addSubTest(self, t, sub, e):
        super().addSubTest(t, sub, e)
        if e is not None:
            rows.append(("fail", name(t), "subtest %s: %s" % (sub._subDescription(), first(e))))
sys.path.insert(0, d)
suite = unittest.defaultTestLoader.discover(d, pattern=mod + ".py", top_level_dir=d)
try:
    unittest.TextTestRunner(verbosity=2, resultclass=R, stream=sys.stderr).run(suite)
finally:
    with open(out, "w") as f:
        for status, n, msg in rows:
            f.write("%s\t%s\t%s\n" % (status, n, msg.replace("\t", " ")))
PY

sub run {
    my ($class, $block, %opt) = @_;
    my $kind = $block->{params}{kind} // 'unit';
    return $kind eq 'cosim' ? _cosim($block, %opt) : _unit($block, %opt);
}

sub filter_check {
    my ($block, $filter) = @_;
    return undef unless defined $filter && length $filter;
    my @have = ($block->{params}{kind} // 'unit') eq 'cosim' ? @DEMOS : _modules();
    my %have = map { $_ => 1 } @have;
    return (grep { $have{$_} } split /,/, $filter) ? undef
         : "no " . (($block->{params}{kind} // 'unit') eq 'cosim' ? 'demo' : 'module')
           . " called $filter";
}

sub _modules {
    my $dir = vamos_tests_dir() or return ();
    opendir(my $dh, $dir) or return ();
    my @m = sort map { /^(test_\w+)\.py$/ ? $1 : () } readdir $dh;
    closedir $dh;
    return @m;
}

sub _wanted {
    my ($filter, @all) = @_;
    return @all unless defined $filter && length $filter;
    my %w = map { $_ => 1 } split /,/, $filter;
    return grep { $w{$_} } @all;
}

# ---- tests/vamos ------------------------------------------------------------

sub _unit {
    my ($block, %opt) = @_;
    my $dir = vamos_tests_dir() or return _err('tests/vamos not found');
    my $py  = python3_bin()     or return _err('python3 not found');
    (my $top = $dir) =~ s{/tests/vamos$}{};
    my @mods = _wanted($opt{filter}, _modules());
    return _err('no vamos test modules') unless @mods;

    my $work = File::Temp::tempdir('regress-vamos-XXXXXX', TMPDIR => 1, CLEANUP => 1);
    { open(my $fh, '>', "$work/runner.py") or return _err("cannot write runner: $!");
      print $fh $RUNNER; close $fh; }
    my $jobs = $opt{jobs} || 8;
    $jobs = 8 if $jobs > 8;
    my (%running, @queue) = ();
    @queue = @mods;
    my %rc;
    while (@queue || %running) {
        while (@queue && keys(%running) < $jobs) {
            my $m = shift @queue;
            my $pid = fork;
            die "fork: $!" unless defined $pid;
            if ($pid == 0) {
                my ($rc) = run_capture(
                    [$py, "$work/runner.py", $dir, $m],
                    dir => $top, path_prepend => $block->{path_prepend},
                    env => { %{ $block->{env} || {} },
                             REGRESS_VAMOS_RESULTS => "$work/$m.results" },
                    log => "$work/$m.log", timeout => $MODULE_TIMEOUT);
                POSIX::_exit($rc > 255 ? 255 : $rc);
            }
            $running{$pid} = $m;
        }
        my $pid = waitpid(-1, 0);
        last if $pid <= 0;
        my $m = delete $running{$pid} or next;
        $rc{$m} = $? >> 8;
    }

    my @r;
    for my $m (@mods) {
        my $log = "$work/$m.log";
        my @got = _results($m, "$work/$m.results");
        if (!@got || ($rc{$m} && !grep { $_->{status} eq 'fail' } @got)) {
            push @got, { test_name => $m, status => 'fail',
                         message => $rc{$m} == 124 ? "timed out after ${MODULE_TIMEOUT}s"
                                  : "module exited $rc{$m}" . (@got ? '' : ' before reporting any test'),
                         log_path => _keep($log, $opt{log}) };
        }
        $_->{log_path} //= _keep($log, $opt{log}) for @got;
        push @r, @got;
    }
    return { exit_code => 0, results => \@r };
}

sub _results {
    my ($mod, $file) = @_;
    open(my $fh, '<', $file) or return ();
    my (@r, %at);
    while (my $line = <$fh>) {
        chomp $line;
        my ($status, $name, $msg) = split /\t/, $line, 3;
        next unless defined $name;
        my $t = "$mod:$name";
        if (exists $at{$t}) {           # subtests: a failure wins
            $r[$at{$t}] = { test_name => $t, status => $status, message => $msg }
                if $status eq 'fail';
            next;
        }
        $at{$t} = @r;
        push @r, { test_name => $t, status => $status, message => $msg // '' };
    }
    close $fh;
    return @r;
}

# ---- demo/cosim --------------------------------------------------------------

sub _cosim {
    my ($block, %opt) = @_;
    my $vsrc = src_root() . '/VACASK';
    my $demo = "$vsrc/demo/cosim";
    return _err("no $demo") unless -f "$demo/run.sh";
    my $vcb = vacask_build_dir() or return _err('no VACASK build (VAMOS_VACASK_HOME)');
    return _err("$vcb has no cinterface/libvacaskcinterface.so (VACASK_CINTERFACE=ON?)")
        unless -f "$vcb/cinterface/libvacaskcinterface.so";
    my $nvc = nvc_bin() or return _err('nvc not found');
    (my $nvcb = $nvc) =~ s{/bin/nvc$}{};

    my $work = File::Temp::tempdir('regress-cosim-XXXXXX', TMPDIR => 1, CLEANUP => 1);
    opendir(my $dh, $demo) or return _err("cannot read $demo: $!");
    for my $f (grep { -f "$demo/$_" } readdir $dh) {
        require File::Copy;
        File::Copy::copy("$demo/$f", "$work/$f") or return _err("copy $f: $!");
    }
    closedir $dh;
    chmod 0755, "$work/run.sh";

    my @r;
    for my $t (_wanted($opt{filter}, @DEMOS)) {
        my $log = "$work/$t.log";
        my ($rc, $out) = run_capture(['bash', "$work/run.sh", $t], dir => $work,
            env => { %{ $block->{env} || {} }, NVCB => $nvcb, VCB => $vcb },
            path_prepend => $block->{path_prepend}, log => $log, timeout => $DEMO_TIMEOUT);
        my $last = '';
        if (open(my $fh, '<', $log)) { while (<$fh>) { $last = $_ if /\S/ } close $fh }
        chomp $last;
        push @r, { test_name => $t, status => $rc == 0 ? 'pass' : 'fail',
                   message => $rc == 0 ? 'ok' : ($rc == 124 ? "timed out" : "exit $rc: $last"),
                   log_path => _keep($log, $opt{log}) };
    }
    return _err('no cosim demo selected') unless @r;
    return { exit_code => 0, results => \@r };
}

# keep a per-test log next to the block's log (the work dir goes away)
sub _keep {
    my ($log, $blog) = @_;
    return $blog unless defined $blog && -f $log;
    (my $dir = $blog) =~ s{/[^/]*$}{};
    (my $name = $log) =~ s{.*/}{};
    my $to = "$dir/vamos-$name";
    require File::Copy;
    File::Copy::copy($log, $to) or return $blog;
    return $to;
}

# 99: the block could not run (a candidate gate then fails, rather than
# passing on no tests)
sub _err {
    return { exit_code => 99, results => [
        { test_name => 'vamos', status => 'error', message => $_[0] } ] };
}

1;
