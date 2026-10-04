package Regress::IvtestFilter;
#
# `regress run --filter` for the ivtest runners.
#
# Regress::Adapter::Ivtest loads this module into the runner itself
# (`perl -I<regress>/lib -MRegress::IvtestFilter=runner vvp_reg.pl ...`, with
# the filter in $REGRESS_IVTEST_FILTER).  The runner then reads its regress
# lists exactly as in an unfiltered run - the same lists in the same order, the
# same per-list option (vvp_reg.pl reads regress-synth.list with -S), version
# prefixes and replacements - and only when it is about to run them are the
# tests whose names do not match the filter dropped from @testlist.  A filtered
# run is therefore the unfiltered run restricted to the matching tests, for
# every runner whose execute_regression() works through @testlist
# (vvp_reg.pl, vlog95_reg.pl, vhdl_reg.pl, vhdl_nvc_reg.pl, vpi_reg.pl).
#
# The filter is a Perl regex matched against each test name, unanchored; text
# that is not a valid regex is matched literally (what --filter always did).
# When it selects no test, the runner prints
#     regress-ivtest-filter: --filter '<f>' selects none of the <n> tests <runner> reads
# on stderr and exits 3 before running anything.
#
# Only `=runner` installs the wrapper; plain `use Regress::IvtestFilter` (the
# adapter, for regex()) does nothing else.  Without $REGRESS_IVTEST_FILTER, or
# with it empty, the runner runs unchanged.
#
use strict;
use warnings;

our $EXIT_NONE = 3;
our $TAG       = 'regress-ivtest-filter';

my $IN_RUNNER = 0;

sub import {
    my ($class, @args) = @_;
    $IN_RUNNER = 1 if grep { $_ eq 'runner' } @args;
}

# The filter as a regex (shared with the adapter's pre-check).
sub regex {
    my $f = shift;
    my $re = eval { qr/$f/ };
    return defined $re ? $re : qr/\Q$f\E/;
}

# INIT runs after the runner has been compiled (its subs exist) and before its
# main code: wrap execute_regression, which every runner calls once, after
# reading its lists.
INIT {
    my $filter = $ENV{REGRESS_IVTEST_FILTER};
    if ($IN_RUNNER && defined $filter && length $filter) {
        no strict 'refs';
        no warnings qw(redefine once);
        my $orig = defined &main::execute_regression ? \&main::execute_regression : undef;
        if (!$orig) {
            print STDERR "$TAG: $0 has no execute_regression(): cannot apply --filter '$filter'\n";
            exit $EXIT_NONE;
        }
        my $re = regex($filter);
        *main::execute_regression = sub {
            # @main::testlist is the runner's list (RegressionList's, imported, or its own);
            # "" marks a replaced test, which the runners skip anyway
            my @all = grep { defined $_ && length $_ } @main::testlist;
            @main::testlist = grep { $_ =~ $re } @all;
            if (!@main::testlist) {
                printf STDERR "%s: --filter '%s' selects none of the %d tests %s reads\n",
                    $TAG, $filter, scalar(@all), $0;
                exit $EXIT_NONE;
            }
            goto &$orig;
        };
    }
}

1;
