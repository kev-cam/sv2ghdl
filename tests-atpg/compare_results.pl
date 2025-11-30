#!/usr/bin/perl
#
# compare_results.pl - Compare .test expected patterns with .run observed patterns
# Usage: compare_results.pl [work_directory]
#

use strict;
use warnings;
use File::Basename;

my $work_dir = $ARGV[0] || 'work';

# Find all .test files in work directory
opendir(my $dh, $work_dir) or die "Can't open directory $work_dir: $!\n";
my @test_files = grep { /\.test$/ && -f "$work_dir/$_" } readdir($dh);
closedir($dh);

if (@test_files == 0) {
    die "No .test files found in $work_dir/\n";
}

print "Comparing test results in $work_dir/\n";
print "=" x 70 . "\n\n";

my $total_tests = 0;
my $total_patterns = 0;
my $total_passed = 0;
my $total_failed = 0;
my @failed_tests;

foreach my $test_file (sort @test_files) {
    my $test_path = "$work_dir/$test_file";
    my $basename = basename($test_file, '.test');
    my $run_file = "$work_dir/$basename.run";

    $total_tests++;

    unless (-f $run_file) {
        print "⚠ $basename: No .run file found (skipping)\n";
        next;
    }

    # Parse expected patterns from .test file
    my %expected = parse_test_file($test_path);

    # Parse observed patterns from .run file
    my %observed = parse_run_file($run_file);

    # Compare patterns
    my $circuit = $expected{circuit} || $basename;
    my @exp_patterns = @{$expected{patterns}};
    my @obs_patterns = @{$observed{patterns}};

    my $num_patterns = scalar @exp_patterns;
    my $passed = 0;
    my $failed = 0;
    my @mismatches;

    for (my $i = 0; $i < $num_patterns; $i++) {
        my $exp = $exp_patterns[$i];
        my $obs = $obs_patterns[$i] || '';

        if ($exp eq $obs) {
            $passed++;
        } else {
            $failed++;
            push @mismatches, {
                pattern_num => $i + 1,
                expected => $exp,
                observed => $obs
            };
        }
    }

    $total_patterns += $num_patterns;
    $total_passed += $passed;
    $total_failed += $failed;

    # Print results for this test
    if ($failed == 0) {
        print "✓ $circuit: PASS ($passed/$num_patterns patterns correct)\n";
    } else {
        print "✗ $circuit: FAIL ($passed/$num_patterns patterns correct, $failed failed)\n";
        push @failed_tests, $circuit;

        # Show mismatches
        foreach my $mm (@mismatches) {
            print "    Pattern $mm->{pattern_num}: expected '$mm->{expected}' but got '$mm->{observed}'\n";
        }
    }
}

# Summary
print "\n" . "=" x 70 . "\n";
print "Summary:\n";
print "  Total tests: $total_tests\n";
print "  Total patterns: $total_patterns\n";
print "  Passed: $total_passed\n";
print "  Failed: $total_failed\n";

if ($total_failed == 0) {
    print "\n✓ All tests passed!\n";
} else {
    print "\n✗ Some tests failed:\n";
    foreach my $test (@failed_tests) {
        print "  - $test\n";
    }
}

exit ($total_failed > 0) ? 1 : 0;

#-----------------------------------------------------------------------------
# Parse .test file to extract expected patterns
#-----------------------------------------------------------------------------

sub parse_test_file {
    my ($file) = @_;

    open my $fh, '<', $file or die "Can't open $file: $!\n";
    my @lines = <$fh>;
    close $fh;

    my $circuit = '';
    my @patterns;

    foreach my $line (@lines) {
        chomp $line;

        # Extract circuit name
        if ($line =~ /Name of circuit:\s+(\w+)\.bench/) {
            $circuit = $1;
        }

        # Extract test patterns (format: "  N: inputs outputs")
        if ($line =~ /^\s*\d+:\s+([\d\s]+)$/) {
            my $pattern = $1;
            $pattern =~ s/\s+//g;  # Remove all spaces
            push @patterns, $pattern;
        }
    }

    return (
        circuit => $circuit,
        patterns => \@patterns
    );
}

#-----------------------------------------------------------------------------
# Parse .run file to extract observed patterns
#-----------------------------------------------------------------------------

sub parse_run_file {
    my ($file) = @_;

    open my $fh, '<', $file or die "Can't open $file: $!\n";
    my @lines = <$fh>;
    close $fh;

    my @patterns;

    foreach my $line (@lines) {
        chomp $line;

        # Extract observed patterns from report statements
        # Format: "   N: inputs outputs" (may have extra report prefix)
        if ($line =~ /\s+\d+:\s+([\d\s]+)$/) {
            my $pattern = $1;
            $pattern =~ s/\s+//g;  # Remove all spaces
            push @patterns, $pattern;
        }
    }

    return (
        patterns => \@patterns
    );
}

__END__

=head1 NAME

compare_results.pl - Compare expected and observed test patterns

=head1 SYNOPSIS

  compare_results.pl [work_directory]

=head1 DESCRIPTION

Compares .test files (expected patterns) with .run files (observed patterns
from ghdl simulation) and reports any mismatches.

For each test case:
- Reads expected patterns from .test file
- Reads observed patterns from .run file
- Compares pattern by pattern
- Reports PASS/FAIL and shows mismatches

=head1 EXIT STATUS

Returns 0 if all tests pass, 1 if any test fails.

=cut
