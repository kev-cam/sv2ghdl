#!/usr/bin/perl
#
# sv2ghdl - SystemVerilog to VHDL Translator
# Copyright (C) 2024 Kevin Cameron
# License: GPL v2+
#
# Translates SystemVerilog RTL to VHDL with enhanced mixed-signal semantics

use strict;
use warnings;
use Getopt::Long;
use File::Basename;

our $VERSION = "0.1.0";

# Translation mode
my %MODES = (
    'standard'           => { lib => 'ieee',  temporal => 0, accel => 0 },
    'enhanced'           => { lib => 'ceda',  temporal => 0, accel => 0 },
);

# Command-line options
my $mode = 'standard';
my $output_file;
my $emit_line_directives = 1;
my $verbose = 0;
my $help = 0;
my $version = 0;

GetOptions(
    'mode=s'              => \$mode,
    'output|o=s'          => \$output_file,
    'no-line-directives'  => sub { $emit_line_directives = 0 },
    'verbose|v'           => \$verbose,
    'help|h'              => \$help,
    'version'             => \$version,
) or die "Error in command line arguments\n";

if ($version) {
    print "sv2ghdl version $VERSION\n";
    exit 0;
}

if ($help) {
    print_help();
    exit 0;
}

# Check mode is valid
die "Unknown mode: $mode\n" unless exists $MODES{$mode};

# Get input file
my $input_file = shift @ARGV or die "No input file specified\n";
die "Input file not found: $input_file\n" unless -f $input_file;

# Set output file if not specified
unless ($output_file) {
    my $basename = basename($input_file, '.v', '.sv');
    $output_file = "$basename.vhd";
}

# Main translation
print STDERR "Translating $input_file -> $output_file (mode: $mode)\n" if $verbose;

translate_file($input_file, $output_file, $mode);

print STDERR "✓ Translation complete\n" if $verbose;

exit 0;

#-----------------------------------------------------------------------------
# Main translation function
#-----------------------------------------------------------------------------

sub translate_file {
    my ($input, $output, $mode) = @_;
    
    # Read input
    open my $in_fh, '<', $input or die "Can't open $input: $!\n";
    my @lines = <$in_fh>;
    close $in_fh;
    
    # Open output
    open my $out_fh, '>', $output or die "Can't create $output: $!\n";
    
    # Generate VHDL
    my $module_name = extract_module_name(\@lines);
    
    print $out_fh generate_header($mode, $input, $module_name);
    
    # Translate line by line
    my $line_num = 0;
    my $in_entity = 0;
    my $in_architecture = 0;
    
    foreach my $line (@lines) {
        $line_num++;
        
        # State machine for entity/architecture
        if ($line =~ /^\s*module\s+\w+/) {
            $in_entity = 1;
        }
        if ($line =~ /^\s*endmodule/) {
            if ($in_entity && !$in_architecture) {
                print $out_fh "end entity;\n\n";
                print $out_fh generate_architecture_header($module_name, $mode);
                $in_architecture = 1;
            }
        }
        
        my $vhdl = translate_line($line, $line_num, $input, $mode);
        print $out_fh $vhdl if $vhdl;
    }
    
    print $out_fh generate_footer($mode);
    
    close $out_fh;
}

#-----------------------------------------------------------------------------
# Line-by-line translation
#-----------------------------------------------------------------------------

sub translate_line {
    my ($line, $line_num, $source_file, $mode) = @_;
    
    my $config = $MODES{$mode};
    
    # Preserve blank lines
    return $line if $line =~ /^\s*$/;
    
    # Preserve comments
    if ($line =~ m{^\s*//(.*)}) {
        return "--$1\n";
    }
    
    # Module declaration
    if ($line =~ /^\s*module\s+(\w+)\s*\(/) {
        return line_directive($line_num, $source_file) .
               "entity $1 is\n  port (\n";
    }
    if ($line =~ /^\s*module\s+(\w+)\s*;/) {
        return line_directive($line_num, $source_file) .
               "entity $1 is\n";
    }
    
    # Parameters -> Generics
    if ($line =~ /^\s*parameter\s+(\w+)\s*=\s*(.+);/) {
        return line_directive($line_num, $source_file) .
               "  generic (\n    $1 : integer := $2\n  );\n";
    }
    
    # Ports
    if ($line =~ /^\s*input\s+(?:wire\s+)?(?:\[(\d+):(\d+)\]\s+)?(\w+)([,;])/) {
        my ($msb, $lsb, $name, $term) = ($1, $2, $3, $4);
        my $vhdl_type = port_type($msb, $lsb, $config->{lib});
        my $separator = ($term eq ',') ? ';' : '';
        return line_directive($line_num, $source_file) .
               "    $name : in $vhdl_type$separator\n";
    }
    
    if ($line =~ /^\s*output\s+(?:reg\s+)?(?:\[(\d+):(\d+)\]\s+)?(\w+)([,;])/) {
        my ($msb, $lsb, $name, $term) = ($1, $2, $3, $4);
        my $vhdl_type = port_type($msb, $lsb, $config->{lib});
        my $separator = ($term eq ',') ? ';' : '';
        return line_directive($line_num, $source_file) .
               "    $name : out $vhdl_type$separator\n";
    }
    
    # Wire/reg declarations (internal signals)
    if ($line =~ /^\s*(?:wire|reg)\s+(?:\[(\d+):(\d+)\]\s+)?(\w+);/) {
        my ($msb, $lsb, $name) = ($1, $2, $3);
        my $vhdl_type = port_type($msb, $lsb, $config->{lib});
        return line_directive($line_num, $source_file) .
               "  signal $name : $vhdl_type;\n";
    }
    
    # Always blocks
    if ($line =~ /^\s*always\s+@\(posedge\s+(\w+)\)/) {
        if ($config->{temporal}) {
            return line_directive($line_num, $source_file) .
                   "process\nbegin\n  wait until $1'event;\n  wait until rising_edge($1);\n";
        } else {
            return line_directive($line_num, $source_file) .
                   "process($1)\nbegin\n  if rising_edge($1) then\n";
        }
    }
    
    if ($line =~ /^\s*always\s+@\(\*\)/) {
        return line_directive($line_num, $source_file) .
               "process(all)\nbegin\n";  # VHDL-2008 process(all)
    }
    
    # Assignments
    if ($line =~ /^\s*assign\s+(\w+)\s*=\s*(.+);/) {
        return line_directive($line_num, $source_file) .
               "  $1 <= $2;\n";
    }
    
    # Non-blocking assignment (already uses <=)
    if ($line =~ /^\s*(\w+)\s*<=\s*(.+);/) {
        return line_directive($line_num, $source_file) .
               "    $1 <= " . translate_expression($2) . ";\n";
    }
    
    # Blocking assignment (= -> :=)
    if ($line =~ /^\s*(\w+)\s*=\s*(.+);/) {
        return line_directive($line_num, $source_file) .
               "    $1 := " . translate_expression($2) . ";\n";
    }
    
    # If statements
    if ($line =~ /^\s*if\s*\((.+)\)\s*$/) {
        return line_directive($line_num, $source_file) .
               "    if $1 then\n";
    }
    
    # Else statements
    if ($line =~ /^\s*else\s*$/) {
        return line_directive($line_num, $source_file) .
               "    else\n";
    }
    
    # Begin/end blocks
    if ($line =~ /^\s*begin\s*$/) {
        return "";  # VHDL doesn't need begin after if/process
    }
    
    if ($line =~ /^\s*end\s*$/) {
        return "  end if;\nend process;\n";
    }
    
    # Endmodule
    if ($line =~ /^\s*endmodule/) {
        return "";  # Handled by state machine
    }
    
    # Port list terminator
    if ($line =~ /^\s*\);/) {
        return "  );\n";
    }
    
    # Default: return as comment (needs manual translation)
    return "-- FIXME: " . $line;
}

#-----------------------------------------------------------------------------
# Helper functions
#-----------------------------------------------------------------------------

sub extract_module_name {
    my ($lines) = @_;
    foreach my $line (@$lines) {
        if ($line =~ /^\s*module\s+(\w+)/) {
            return $1;
        }
    }
    return "unknown";
}

sub port_type {
    my ($msb, $lsb, $lib) = @_;
    
    if (defined $msb && defined $lsb) {
        return "std_logic_vector($msb downto $lsb)";
    } else {
        return "std_logic";
    }
}

sub translate_expression {
    my ($expr) = @_;
    
    # Bit concatenation: {a, b, c} -> a & b & c
    $expr =~ s/\{([^}]+)\}/concat($1)/ge;
    
    # Ternary: a ? b : c -> b when a else c
    if ($expr =~ /(.+?)\s*\?\s*(.+?)\s*:\s*(.+)/) {
        return "$2 when $1 else $3";
    }
    
    return $expr;
}

sub concat {
    my ($items) = @_;
    my @parts = split /\s*,\s*/, $items;
    return join(' & ', @parts);
}

sub line_directive {
    my ($line_num, $source_file) = @_;
    return "" unless $emit_line_directives;
    return "-- #line $line_num \"$source_file\"\n";
}

sub generate_header {
    my ($mode, $source_file, $module_name) = @_;
    
    my $config = $MODES{$mode};
    my $lib = $config->{lib};
    
    my $header = "-- Auto-generated from $source_file\n";
    $header .= "-- Mode: $mode\n";
    $header .= "-- sv2ghdl version $VERSION\n\n";
    
    if ($lib eq 'ieee') {
        $header .= "library ieee;\n";
        $header .= "use ieee.std_logic_1164.all;\n";
        $header .= "use ieee.numeric_std.all;\n\n";
    } elsif ($lib eq 'cameron_eda') {
        $header .= "library cameron_eda;\n";
        $header .= "use cameron_eda.enhanced_logic_1164.all;\n";
        $header .= "use cameron_eda.numeric_std_enhanced.all;\n\n";
    }
    
    return $header;
}

sub generate_architecture_header {
    my ($module_name, $mode) = @_;
    
    my $arch = "architecture rtl of $module_name is\n";
    $arch .= "  -- Internal signals\n";
    return $arch;
}

sub generate_footer {
    my ($mode) = @_;
    return "end architecture;\n";
}

sub print_help {
    print <<'HELP';
sv2ghdl - SystemVerilog to VHDL Translator

Usage: sv2ghdl [options] input.v

Options:
  --mode=MODE           Translation mode (default: standard)
  -o, --output=FILE     Output file (default: input.vhd)
  --no-line-directives  Disable #line directives
  -v, --verbose         Verbose output
  -h, --help            Show this help
  --version             Show version

Modes:
  standard              IEEE std_logic (baseline)
  enhanced              cameron_eda enhanced types
  mixed                 IEEE testbench + enhanced DUT
  temporal_standard     Temporal optimization + IEEE
  temporal_enhanced     Temporal + enhanced
  temporal_accel_enh    Full optimization (requires modified GHDL)

Examples:
  sv2ghdl design.v
  sv2ghdl --mode=enhanced design.v -o design_enh.vhd
  sv2ghdl --mode=temporal_accel_enh picorv32.v

See README.md for more information.
HELP
}

__END__

=head1 NAME

sv2ghdl - SystemVerilog to VHDL Translator

=head1 SYNOPSIS

  sv2ghdl [options] input.v

=head1 DESCRIPTION

Translates SystemVerilog RTL to VHDL with enhanced mixed-signal semantics.

=head1 AUTHOR

Kevin Cameron

=head1 LICENSE

GPL v2+

=cut
