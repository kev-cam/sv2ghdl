package Regress::Run;
#
# Run-level helpers: capture the git SHA / version of each repo+tool so a run
# is reproducible and regressions can be attributed to a specific revision.
#
use strict;
use warnings;
use Exporter 'import';
use Regress::Tools qw(src_root tool_versions);

our @EXPORT_OK = qw(repo_shas);

# repo => { sha => ..., version => ... }
sub repo_shas {
    my %out;
    my $root = src_root();
    my %repo_dir = (
        sv2ghdl  => "$root/sv2ghdl",
        iverilog => "$root/iverilog",
        nvc      => "$root/nvc",
        'sv-tests'=> "$root/sv-tests",
        smak     => "$root/smak",
        rtlmeter => "$root/rtlmeter",
    );
    for my $repo (sort keys %repo_dir) {
        my $dir = $repo_dir{$repo};
        next unless -d "$dir/.git";
        my $sha = _sha($dir);
        $out{$repo} = { sha => $sha, version => undef };
    }
    # tool versions (verilator is a system package, no local git)
    my $tv = tool_versions();
    for my $t (keys %$tv) {
        next unless defined $tv->{$t};
        $out{$t} ||= {};
        $out{$t}{version} = $tv->{$t};
    }
    return \%out;
}

sub _sha {
    my $dir = shift;
    my $sha = `git -C '$dir' rev-parse HEAD 2>/dev/null`;
    chomp $sha if defined $sha;
    return ($sha && length $sha) ? $sha : undef;
}

1;
