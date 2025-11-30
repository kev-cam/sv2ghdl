#!/usr/bin/perl
#
# test2vhdl.pl - Convert Atalanta .test file to VHDL testbench
# Usage: test2vhdl.pl input.test [output.vhd]
#

use strict;
use warnings;

my $test_file = $ARGV[0] or die "Usage: $0 input.test [output.vhd]\n";
my $output_file = $ARGV[1];

# Read test file
open my $fh, '<', $test_file or die "Can't open $test_file: $!\n";
my @lines = <$fh>;
close $fh;

# Parse test file
my $circuit_name;
my @inputs;
my @outputs;
my @patterns;

my $in_inputs = 0;
my $in_outputs = 0;

for (my $i = 0; $i < @lines; $i++) {
    my $line = $lines[$i];
    chomp $line;

    # Extract circuit name
    if ($line =~ /Name of circuit:\s+(\w+)\.bench/) {
        $circuit_name = $1;
    }

    # Mark section for inputs
    if ($line =~ /Primary inputs/) {
        $in_inputs = 1;
        $in_outputs = 0;
        next;
    }

    # Mark section for outputs
    if ($line =~ /Primary outputs/) {
        $in_inputs = 0;
        $in_outputs = 1;
        next;
    }

    # End of port section
    if ($line =~ /Test patterns/) {
        $in_inputs = 0;
        $in_outputs = 0;
        next;
    }

    # Extract inputs
    if ($in_inputs && $line =~ /^\s+([\w\s]+)$/) {
        my $ports = $1;
        @inputs = split /\s+/, $ports;
        @inputs = grep { $_ ne '' } @inputs;
        $in_inputs = 0;
    }

    # Extract outputs
    if ($in_outputs && $line =~ /^\s+([\w\s]+)$/) {
        my $ports = $1;
        @outputs = split /\s+/, $ports;
        @outputs = grep { $_ ne '' } @outputs;
        $in_outputs = 0;
    }

    # Extract test patterns (format: "  N: inputs outputs")
    if ($line =~ /^\s*\d+:\s+([\d\s]+)$/) {
        my $pattern = $1;
        $pattern =~ s/\s+//g;  # Remove all spaces to get continuous bit string

        # Split into input and output bits by position
        my $num_inputs = scalar @inputs;
        my $num_outputs = scalar @outputs;
        my @all_bits = split //, $pattern;

        my $input_bits = join('', @all_bits[0 .. $num_inputs - 1]);
        my $output_bits = join('', @all_bits[$num_inputs .. $num_inputs + $num_outputs - 1]);

        push @patterns, { input => $input_bits, output => $output_bits };
    }
}

die "Could not parse circuit name\n" unless $circuit_name;
die "Could not parse inputs\n" unless @inputs;
die "Could not parse outputs\n" unless @outputs;
die "No test patterns found\n" unless @patterns;

# Determine output filename
unless ($output_file) {
    $output_file = "${circuit_name}_tb.vhd";
}

# Generate VHDL testbench
open my $out, '>', $output_file or die "Can't create $output_file: $!\n";

print $out generate_vhdl_testbench($circuit_name, \@inputs, \@outputs, \@patterns);

close $out;

print "Generated VHDL testbench: $output_file\n";
print "  Circuit: $circuit_name\n";
print "  Inputs:  @inputs\n";
print "  Outputs: @outputs\n";
print "  Patterns: " . scalar(@patterns) . "\n";

exit 0;

#-----------------------------------------------------------------------------
# Generate VHDL testbench
#-----------------------------------------------------------------------------

sub generate_vhdl_testbench {
    my ($entity_name, $inputs_ref, $outputs_ref, $patterns_ref) = @_;

    my @inputs = @$inputs_ref;
    my @outputs = @$outputs_ref;
    my @patterns = @$patterns_ref;

    my $tb = "";

    # Header
    $tb .= "-- Auto-generated testbench from $test_file\n";
    $tb .= "-- Entity: $entity_name\n\n";

    $tb .= "library ieee;\n";
    $tb .= "use ieee.std_logic_1164.all;\n\n";

    # Testbench entity
    $tb .= "entity ${entity_name}_tb is\n";
    $tb .= "end entity;\n\n";

    # Architecture
    $tb .= "architecture testbench of ${entity_name}_tb is\n";
    $tb .= "  -- Component declaration\n";
    $tb .= "  component $entity_name is\n";
    $tb .= "    port (\n";

    # Port declarations
    my @all_ports;
    foreach my $i (0 .. $#inputs) {
        my $comma = ($i == $#inputs && @outputs == 0) ? '' : ';';
        push @all_ports, "      $inputs[$i] : in std_logic$comma\n";
    }
    foreach my $i (0 .. $#outputs) {
        my $comma = ($i == $#outputs) ? '' : ';';
        push @all_ports, "      $outputs[$i] : inout std_logic$comma\n";
    }
    $tb .= join('', @all_ports);

    $tb .= "    );\n";
    $tb .= "  end component;\n\n";

    # Signal declarations
    $tb .= "  -- Signals\n";
    foreach my $in (@inputs) {
        $tb .= "  signal $in : std_logic := '0';\n";
    }
    foreach my $out (@outputs) {
        $tb .= "  signal $out : std_logic;\n";
    }
    $tb .= "\nbegin\n\n";

    # Component instantiation
    $tb .= "  -- Device Under Test\n";
    $tb .= "  DUT: $entity_name port map (\n";
    my @port_maps;
    foreach my $in (@inputs) {
        push @port_maps, "$in => $in";
    }
    foreach my $out (@outputs) {
        push @port_maps, "$out => $out";
    }
    $tb .= "    " . join(",\n    ", @port_maps) . "\n";
    $tb .= "  );\n\n";

    # Test process
    $tb .= "  -- Test process\n";
    $tb .= "  test_proc: process\n";
    $tb .= "  begin\n";
    $tb .= "    report \"Starting testbench for $entity_name\";\n";
    $tb .= "    report \"Test patterns and observed responses:\";\n";
    $tb .= "    report \"\";\n\n";

    # Generate test patterns
    my $pattern_num = 1;
    foreach my $pat (@patterns) {
        my @input_bits = split //, $pat->{input};
        my @output_bits = split //, $pat->{output};

        $tb .= "    -- Test pattern $pattern_num\n";

        # Apply inputs
        foreach my $i (0 .. $#inputs) {
            $tb .= "    $inputs[$i] <= '$input_bits[$i]';\n";
        }

        $tb .= "    wait for 10 ns;\n";

        # Report observed waveform in .test file format
        $tb .= "    report \"   $pattern_num: " . $pat->{input} . " \" & ";
        my @output_conversions;
        foreach my $i (0 .. $#outputs) {
            if ($i == 0) {
                push @output_conversions, "std_logic'image($outputs[$i])(2)";
            } else {
                push @output_conversions, "\" \" & std_logic'image($outputs[$i])(2)";
            }
        }
        $tb .= join(' & ', @output_conversions) . ";\n";

        # Check outputs
        foreach my $i (0 .. $#outputs) {
            $tb .= "    assert $outputs[$i] = '$output_bits[$i]' report \"Pattern $pattern_num failed: $outputs[$i] expected '$output_bits[$i]'\" severity error;\n";
        }

        $tb .= "\n";
        $pattern_num++;
    }

    $tb .= "    report \"\";\n";
    $tb .= "    report \"Testbench completed\";\n";
    $tb .= "    wait;\n";
    $tb .= "  end process;\n\n";

    $tb .= "end architecture;\n";

    return $tb;
}
