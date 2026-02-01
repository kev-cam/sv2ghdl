#!/usr/bin/perl
#
# verilog2bench.pl - Convert simple gate-level Verilog to ISCAS89 bench format
#
# Usage: verilog2bench.pl input.v output.bench
#
# Supports: and, or, nand, nor, xor, xnor, not, buf gates
#

use strict;
use warnings;

die "Usage: $0 input.v output.bench\n" unless @ARGV == 2;

my ($input_file, $output_file) = @ARGV;

open my $in, '<', $input_file or die "Can't open $input_file: $!\n";
my @lines = <$in>;
close $in;

my @inputs;
my @outputs;
my @gates;
my $module_name = "unknown";

foreach my $line (@lines) {
    chomp $line;

    # Module name
    if ($line =~ /^\s*module\s+(\w+)/) {
        $module_name = $1;
    }

    # Input ports
    if ($line =~ /^\s*input\s+(\w+)/) {
        push @inputs, $1;
    }

    # Output ports
    if ($line =~ /^\s*output\s+(\w+)/) {
        push @outputs, $1;
    }

    # Gate primitives: gate_type instance_name (output, input1, input2, ...)
    # 2-input gates
    if ($line =~ /^\s*(and|or|nand|nor|xor|xnor)\s+\w+\s*\(\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*\)/) {
        my ($gate_type, $out, $in1, $in2) = ($1, $2, $3, $4);
        push @gates, { type => uc($gate_type), out => $out, inputs => [$in1, $in2] };
    }

    # 1-input gates: not, buf
    if ($line =~ /^\s*(not|buf)\s+\w+\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)/) {
        my ($gate_type, $out, $in1) = ($1, $2, $3);
        my $bench_type = ($gate_type eq 'not') ? 'NOT' : 'BUFF';
        push @gates, { type => $bench_type, out => $out, inputs => [$in1] };
    }

    # Wire declarations with assign (continuous assignment)
    if ($line =~ /^\s*assign\s+(\w+)\s*=\s*(.+);/) {
        my ($out, $expr) = ($1, $2);

        # Simple cases: a & b, a | b, ~a, etc.
        if ($expr =~ /(\w+)\s*&\s*(\w+)/) {
            push @gates, { type => 'AND', out => $out, inputs => [$1, $2] };
        } elsif ($expr =~ /(\w+)\s*\|\s*(\w+)/) {
            push @gates, { type => 'OR', out => $out, inputs => [$1, $2] };
        } elsif ($expr =~ /(\w+)\s*\^\s*(\w+)/) {
            push @gates, { type => 'XOR', out => $out, inputs => [$1, $2] };
        } elsif ($expr =~ /~(\w+)/) {
            push @gates, { type => 'NOT', out => $out, inputs => [$1] };
        } elsif ($expr =~ /^(\w+)$/) {
            push @gates, { type => 'BUFF', out => $out, inputs => [$1] };
        }
    }
}

# Write bench file
open my $out_fh, '>', $output_file or die "Can't create $output_file: $!\n";

print $out_fh "# $module_name.bench\n";
print $out_fh "# Generated from $input_file by verilog2bench.pl\n\n";

# Inputs
foreach my $inp (@inputs) {
    print $out_fh "INPUT($inp)\n";
}
print $out_fh "\n";

# Outputs
foreach my $outp (@outputs) {
    print $out_fh "OUTPUT($outp)\n";
}
print $out_fh "\n";

# Gates
foreach my $gate (@gates) {
    my $inputs_str = join(', ', @{$gate->{inputs}});
    print $out_fh "$gate->{out} = $gate->{type}($inputs_str)\n";
}

close $out_fh;

print STDERR "Converted $module_name: " . scalar(@inputs) . " inputs, " .
             scalar(@outputs) . " outputs, " . scalar(@gates) . " gates\n";
