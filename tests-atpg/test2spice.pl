#!/usr/bin/perl
#
# test2spice.pl - Convert Atalanta .tst file to SPICE stimulus for Xyce
#
# Generates PWL voltage sources from ATPG test patterns, suitable for
# transistor-level Monte Carlo simulation of standard cells.
#
# Usage:
#   test2spice.pl input.tst [options]
#
# Options:
#   -o FILE          Output file (default: <circuit>_stim.cir)
#   -vdd VALUE       Supply voltage (default: 1.8)
#   -trise VALUE     Rise/fall time (default: 100p)
#   -tsetup VALUE    Setup time before measurement (default: 1n)
#   -thold VALUE     Hold time / pattern period (default: 10n)
#   -subckt NAME     Subcircuit to instantiate (default: circuit name)
#   -lib FILE        .lib or .include for cell model
#   -mc COUNT        Number of Monte Carlo samples (default: 0 = off)
#   -measure         Add .MEASURE statements for output levels
#
# Output:
#   A SPICE netlist with PWL stimulus driving the cell under test.
#   Each ATPG pattern becomes a time window in the PWL waveform.
#   With -mc, adds Xyce .SAMPLING for process variation analysis.
#

use strict;
use warnings;

# --- Parse command line ---
my $test_file;
my $output_file;
my $vdd       = 1.8;
my $trise     = 100e-12;
my $tsetup    = 1e-9;
my $thold     = 10e-9;
my $subckt;
my $lib_file;
my $mc_count  = 0;
my $do_measure = 0;

my @args = @ARGV;
while (@args) {
    my $arg = shift @args;
    if    ($arg eq '-o')       { $output_file = shift @args; }
    elsif ($arg eq '-vdd')     { $vdd         = shift @args; }
    elsif ($arg eq '-trise')   { $trise       = parse_eng(shift @args); }
    elsif ($arg eq '-tsetup')  { $tsetup      = parse_eng(shift @args); }
    elsif ($arg eq '-thold')   { $thold       = parse_eng(shift @args); }
    elsif ($arg eq '-subckt')  { $subckt      = shift @args; }
    elsif ($arg eq '-lib')     { $lib_file    = shift @args; }
    elsif ($arg eq '-mc')      { $mc_count    = shift @args; }
    elsif ($arg eq '-measure') { $do_measure  = 1; }
    elsif ($arg eq '-h' || $arg eq '--help') { usage(); exit 0; }
    elsif (!defined $test_file) { $test_file = $arg; }
    else  { die "Unknown option: $arg\n"; }
}

die "Usage: $0 input.tst [options]\n" unless defined $test_file;

# --- Parse .tst file (same format as test2vhdl.pl) ---
open my $fh, '<', $test_file or die "Can't open $test_file: $!\n";
my @lines = <$fh>;
close $fh;

my $circuit_name;
my @inputs;
my @outputs;
my @patterns;

my $in_inputs = 0;
my $in_outputs = 0;

for my $line (@lines) {
    chomp $line;

    # Circuit name — handle path prefix and * comment prefix
    if ($line =~ /Name of circuit:\s+(?:.*\/)?(\w+)\.bench/) {
        $circuit_name = $1;
    }

    if ($line =~ /Primary inputs/) {
        $in_inputs = 1; $in_outputs = 0; next;
    }
    if ($line =~ /Primary outputs/) {
        $in_inputs = 0; $in_outputs = 1; next;
    }
    if ($line =~ /Test patterns/) {
        $in_inputs = 0; $in_outputs = 0; next;
    }

    # Port names — may have * prefix or trailing punctuation
    if ($in_inputs && $line =~ /^\s*\*?\s+([\w\s]+?)\s*$/) {
        push @inputs, grep { $_ ne '' } split /\s+/, $1;
    }
    if ($in_outputs && $line =~ /^\s*\*?\s+([\w\s]+?)\s*$/) {
        push @outputs, grep { $_ ne '' } split /\s+/, $1;
    }

    # Pattern line: "  N: <input_bits> <output_bits>"
    # Bits may be grouped (e.g. "10 0") or spaced ("1 0 0")
    if ($line =~ /^\s*\d+:\s+([\d\s]+)$/) {
        my $pattern = $1;
        $pattern =~ s/\s+//g;
        my @bits = split //, $pattern;
        my $num_in = scalar @inputs;
        my $input_bits  = join('', @bits[0 .. $num_in - 1]);
        my $output_bits = join('', @bits[$num_in .. $#bits]);
        push @patterns, { input => $input_bits, output => $output_bits };
    }
}

die "Could not parse circuit name from $test_file\n" unless $circuit_name;
die "No inputs found\n" unless @inputs;
die "No outputs found\n" unless @outputs;
die "No test patterns found\n" unless @patterns;

$subckt //= $circuit_name;
$output_file //= "${circuit_name}_stim.cir";

# --- Generate SPICE netlist ---
open my $out, '>', $output_file or die "Can't create $output_file: $!\n";

my $num_patterns = scalar @patterns;
my $t_total = $num_patterns * $thold;

print $out "* SPICE stimulus generated from $test_file\n";
print $out "* Circuit: $circuit_name\n";
print $out "* Patterns: $num_patterns (Atalanta ATPG)\n";
print $out "* VDD=$vdd  trise=", eng($trise), "  thold=", eng($thold), "\n";
print $out "*\n\n";

# Include cell model if specified
if ($lib_file) {
    print $out ".include $lib_file\n\n";
}

# Supply
print $out "* Power supply\n";
print $out "Vvdd vdd 0 $vdd\n";
print $out "Vvss vss 0 0\n\n";

# PWL stimulus for each input
print $out "* Input stimulus (PWL from ATPG patterns)\n";
for my $i (0 .. $#inputs) {
    my $name = $inputs[$i];
    my @pwl_points;

    # Track previous bit value (not voltage)
    my $prev_bit = -1;

    for my $p (0 .. $#patterns) {
        my $bit = substr($patterns[$p]{input}, $i, 1) + 0;
        my $v = $bit ? $vdd : 0;
        my $t_start = $p * $thold;

        if ($bit != $prev_bit) {
            if ($p == 0) {
                # Start at this value
                push @pwl_points, sprintf("%.4g %.4g", 0, $v);
            } else {
                # Transition: hold previous level, then ramp
                my $v_prev = $prev_bit ? $vdd : 0;
                push @pwl_points, sprintf("%.4g %.4g", $t_start, $v_prev);
                push @pwl_points, sprintf("%.4g %.4g", $t_start + $trise, $v);
            }
        }
        $prev_bit = $bit;
    }

    # Hold final value
    push @pwl_points, sprintf("%.4g %.4g", $t_total, $prev_bit ? $vdd : 0);

    print $out "V$name $name 0 PWL(\n";
    for my $j (0 .. $#pwl_points) {
        my $cont = ($j < $#pwl_points) ? "" : "";
        print $out "+  $pwl_points[$j]\n";
    }
    print $out "+ )\n\n";
}

# Cell instantiation
print $out "* Device under test\n";
my @port_list;
push @port_list, @inputs;
push @port_list, @outputs;
push @port_list, "vdd", "vss";
print $out "X1 " . join(' ', @port_list) . " $subckt\n\n";

# Output loads (small capacitance)
print $out "* Output loads\n";
for my $o (@outputs) {
    print $out "Cload_$o $o 0 1f\n";
}
print $out "\n";

# Measurement statements
if ($do_measure) {
    print $out "* Output level measurements at each pattern\n";
    for my $p (0 .. $#patterns) {
        my $t_meas = $p * $thold + $tsetup;
        for my $oi (0 .. $#outputs) {
            my $expected = substr($patterns[$p]{output}, $oi, 1);
            my $oname = $outputs[$oi];
            printf $out ".MEASURE TRAN pat%d_%s AVG V(%s) FROM=%.4g TO=%.4g\n",
                $p + 1, $oname, $oname, $t_meas, $t_meas + $trise;
        }
    }
    print $out "\n";
}

# Monte Carlo sampling
if ($mc_count > 0) {
    print $out "* Monte Carlo process variation\n";
    print $out ".global_param vth_shift=0\n";
    print $out ".SAMPLING\n";
    print $out "+ param=vth_shift\n";
    print $out "+ type=uniform\n";
    print $out "+ lower=-0.05\n";
    print $out "+ upper=0.05\n";
    print $out "+ num_samples=$mc_count\n";
    print $out "\n";
}

# Transient analysis
print $out "* Transient analysis\n";
printf $out ".TRAN %s %s\n", eng($trise), eng($t_total + $thold);
print $out ".PRINT TRAN FORMAT=CSV";
for my $name (@inputs) {
    print $out " V($name)";
}
for my $name (@outputs) {
    print $out " V($name)";
}
print $out "\n";

print $out ".END\n";

close $out;

# Summary
print STDERR "Generated: $output_file\n";
print STDERR "  Circuit:  $circuit_name (subckt: $subckt)\n";
print STDERR "  Inputs:   @inputs\n";
print STDERR "  Outputs:  @outputs\n";
print STDERR "  Patterns: $num_patterns\n";
print STDERR "  Duration: ", eng($t_total + $thold), "\n";
print STDERR "  MC:       $mc_count samples\n" if $mc_count > 0;

exit 0;

# --- Helpers ---

sub usage {
    print <<'EOF';
Usage: test2spice.pl input.tst [options]

Convert Atalanta ATPG patterns to SPICE stimulus for Xyce.

Options:
  -o FILE          Output file (default: <circuit>_stim.cir)
  -vdd VALUE       Supply voltage (default: 1.8)
  -trise VALUE     Rise/fall time (default: 100p)
  -tsetup VALUE    Setup time before measurement (default: 1n)
  -thold VALUE     Pattern period (default: 10n)
  -subckt NAME     Subcircuit name (default: circuit name from .tst)
  -lib FILE        .include for cell SPICE model
  -mc COUNT        Monte Carlo samples (0 = off)
  -measure         Add .MEASURE for output levels
  -h, --help       This help
EOF
}

# Parse engineering notation: 100p -> 100e-12
sub parse_eng {
    my $s = shift;
    return $s if $s =~ /^[\d.eE+-]+$/;
    my %mult = (
        f => 1e-15, p => 1e-12, n => 1e-9, u => 1e-6,
        m => 1e-3,  k => 1e3,   M => 1e6,  G => 1e9,
    );
    if ($s =~ /^([\d.]+)([fpnumkMG])$/) {
        return $1 * $mult{$2};
    }
    return $s;
}

# Format as engineering notation
sub eng {
    my $v = shift;
    my @units = (
        [1e-15, 'f'], [1e-12, 'p'], [1e-9, 'n'], [1e-6, 'u'],
        [1e-3, 'm'],  [1, ''],      [1e3, 'k'],  [1e6, 'M'],
    );
    for my $i (reverse 0 .. $#units) {
        if (abs($v) >= $units[$i][0] * 0.999) {
            return sprintf("%.4g%s", $v / $units[$i][0], $units[$i][1]);
        }
    }
    return sprintf("%.4g", $v);
}
