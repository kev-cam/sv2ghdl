package Regress::Util;
#
# Small shared helpers for the regression harness.
#
use strict;
use warnings;
use Exporter 'import';

our @EXPORT_OK = qw(run_capture slurp now_ms);

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
        if ($o{env}) { $ENV{$_} = $o{env}{$_} for keys %{$o{env}}; }
        if ($o{path_prepend}) {
            $ENV{PATH} = join(':', $o{path_prepend}, ($ENV{PATH} // ''));
        }
        if (ref $cmd eq 'ARRAY') { exec @$cmd }
        else                     { exec '/bin/sh', '-c', $cmd }
        _child_die("exec failed: $!");
    }
    waitpid $pid, 0;
    my $exit = $? >> 8;
    my $out = ($log && -f $log) ? slurp($log) : '';
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
