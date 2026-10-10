package Regress::Util;
#
# Small shared helpers for the regression harness.
#
use strict;
use warnings;
use Exporter 'import';

our @EXPORT_OK = qw(run_capture slurp now_ms run_workdir);

# run_workdir($regress_dir, $db_path, $run_id, $started_at) -> the work dir of a run
#
# <regress_dir>/out/run-<id>-<tag>: logs, timing, the dispatch makefile and the
# ivtest work areas of run <id> of the results DB at $db_path.  <tag> (8 hex
# digits) comes from the DB file's real path and the run's started_at, so the
# copies of one DB, which hand out the same next run id, never share (and
# overwrite) a work dir, and whoever holds the DB (run-one, the dashboard) finds
# the dir again.  Runs recorded before this rule used out/run-<id>.
sub run_workdir {
    my ($regress_dir, $db, $run_id, $started_at) = @_;
    require Cwd;
    require Digest::MD5;
    my $real = Cwd::abs_path($db);
    $real = $db unless defined $real;
    my $tag = substr(Digest::MD5::md5_hex(join("\0", $real, $started_at // '')), 0, 8);
    return "$regress_dir/out/run-$run_id-$tag";
}

# run_capture($cmd, %opts) -> ($exit_code, $output_text)
#
#   $cmd          arrayref (exec'd directly) or string (run via /bin/sh -c)
#   $opts{dir}    chdir here in the child before exec
#   $opts{env}    hashref of extra environment to set
#   $opts{path_prepend}  string prepended to PATH (colon-joined)
#   $opts{log}    capture combined stdout+stderr to this file (also returned)
#
sub run_capture {
    my ($cmd, %o) = @_;
    my $log = $o{log};
    my $pid = fork;
    die "fork: $!" unless defined $pid;
    if ($pid == 0) {
        if ($log) {
            open STDOUT, '>', $log  or _child_die("open $log: $!");
            open STDERR, '>&', \*STDOUT or _child_die("dup stderr: $!");
        }
        if ($o{dir}) { chdir $o{dir} or _child_die("chdir $o{dir}: $!"); }
        if ($o{unset}) { delete $ENV{$_} for @{$o{unset}}; }
        if ($o{env}) { $ENV{$_} = $o{env}{$_} for keys %{$o{env}}; }
        if ($o{path_prepend}) {
            $ENV{PATH} = join(':', $o{path_prepend}, ($ENV{PATH} // ''));
        }
        if (ref $cmd eq 'ARRAY') { exec @$cmd }
        else                     { exec '/bin/sh', '-c', $cmd }
        _child_die("exec failed: $!");
    }
    # timeout => N: kill the child (TERM, then KILL) after N seconds and report
    # exit 124, like coreutils timeout(1). Guards gold/reference tools that can
    # wedge for ever, e.g. a native Windows LTspice.exe stuck behind a dialog.
    my $timed_out = 0;
    if (my $t = $o{timeout}) {
        require POSIX;
        my $deadline = time + $t;
        while (waitpid($pid, POSIX::WNOHANG()) == 0) {
            if (time >= $deadline) {
                kill 'TERM', $pid;
                select undef, undef, undef, 2;
                kill 'KILL', $pid;
                waitpid $pid, 0;
                $timed_out = 1;
                last;
            }
            select undef, undef, undef, 0.2;
        }
    } else {
        waitpid $pid, 0;
    }
    my $exit = $timed_out ? 124 : $? >> 8;
    my $out = ($log && -f $log) ? slurp($log) : '';
    if ($timed_out) {
        my $note = "run_capture: timed out after $o{timeout}s, killed\n";
        $out .= $note;
        if ($log && open(my $lf, '>>', $log)) { print {$lf} $note; close $lf; }
    }
    return ($exit, $out);
}

sub _child_die { print STDERR $_[0], "\n"; exit 127 }

sub slurp {
    my $f = shift;
    open my $fh, '<', $f or return '';
    local $/; my $c = <$fh>; close $fh;
    return $c // '';
}

# millisecond wall clock (for per-test/per-block timing where the suite
# doesn't report its own). Uses Time::HiRes if available.
my $HIRES = eval { require Time::HiRes; 1 } ? 1 : 0;
sub now_ms { $HIRES ? int(Time::HiRes::time() * 1000) : time() * 1000 }

1;
