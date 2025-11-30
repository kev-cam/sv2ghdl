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
    'standard'           => { lib => 'ieee',        temporal => 0, accel => 0 },
    'enhanced'           => { lib => 'cameron_eda', temporal => 0, accel => 0 },
);

# Command-line options
my $mode = 'standard';
my $output_file;
my $output_dir;
my $emit_line_directives = 0;
my $verbose = 0;
my $help = 0;
my $version = 0;
my $find_files = 0;
my $find_path = '.';
my $find_name = '*.v';

GetOptions(
    'mode=s'              => \$mode,
    'output|o=s'          => \$output_file,
    'outdir|d=s'          => \$output_dir,
    'find:s'              => sub { $find_files = $_[1] // '.' },  # Optional value (path)
    'name=s'              => \$find_name,    # Pattern for -find
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

# Handle -find option
my @input_files;

if ($find_files) {
    # -find was specified (with or without a path)
    $find_path = $find_files;

    print STDERR "Searching for $find_name files in $find_path\n" if $verbose;

    # Find all matching files
    @input_files = find_verilog_files($find_path, $find_name);

    if (@input_files == 0) {
        die "No files matching '$find_name' found in $find_path\n";
    }

    print STDERR "Found " . scalar(@input_files) . " files\n" if $verbose;

    # Set output directory if not specified
    $output_dir = "vhdl_output" unless $output_dir || $output_file;

} else {
    # Get input file(s) from command line
    @input_files = @ARGV;
    die "No input file specified (use -find to search, or provide file name)\n" unless @input_files;
}

# Check all input files exist (unless using -find)
unless ($find_files) {
    foreach my $file (@input_files) {
        die "Input file not found: $file\n" unless -f $file;
    }
}

# Main translation
if (@input_files == 1 && $output_file) {
    # Single file with explicit output
    print STDERR "Translating $input_files[0] -> $output_file (mode: $mode)\n" if $verbose;
    translate_file($input_files[0], $output_file, $mode);
    print STDERR "✓ Translation complete\n" if $verbose;
    
} elsif ($output_dir) {
    # Multiple files to output directory
    mkdir $output_dir unless -d $output_dir;
    print STDERR "Translating " . scalar(@input_files) . " files to $output_dir/ (mode: $mode)\n" if $verbose;
    
    foreach my $input_file (@input_files) {
        my $basename = basename($input_file, '.v', '.sv');
        my $output = "$output_dir/$basename.vhd";
        
        print STDERR "  $input_file -> $output\n" if $verbose;
        translate_file($input_file, $output, $mode);
    }
    
    print STDERR "✓ Translated " . scalar(@input_files) . " files\n" if $verbose;
    
} else {
    # Single file, default output name
    my $input_file = $input_files[0];
    my $basename = basename($input_file, '.v', '.sv');
    $output_file = "$basename.vhd";
    
    print STDERR "Translating $input_file -> $output_file (mode: $mode)\n" if $verbose;
    translate_file($input_file, $output_file, $mode);
    print STDERR "✓ Translation complete\n" if $verbose;
}

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

    # Extract module info
    my $module_name = extract_module_name(\@lines);
    my @ports = extract_ports(\@lines);
    my @signals = extract_signals(\@lines);
    my @instantiations = extract_instantiations(\@lines);

    # Build output array instead of printing directly
    my @output;

    # Track intermediate signals needed
    our %intermediates = ();  # signal_name => type_declaration

    push @output, generate_header($mode, $input, $module_name);

    # Translate line by line with state tracking
    my $line_num = 0;
    my $in_entity = 0;
    my $in_architecture = 0;
    my $port_section_done = 0;
    my $arch_header_idx = -1;  # Index where architecture header ends

    foreach my $line (@lines) {
        $line_num++;

        # State machine for entity/architecture boundary
        if ($line =~ /^\s*module\s+\w+/) {
            $in_entity = 1;
        }

        # End of ports section (first non-port declaration)
        # Skip module, input, output, inout, parameter lines
        if ($in_entity && !$port_section_done &&
            $line !~ /^\s*(?:module|input|output|inout|parameter)\b/ &&
            ($line =~ /^\s*(?:wire|reg|always|assign)\b/ || $line =~ /^\s*\w+\s+\w+\s*\(/)) {
            # Close entity, start architecture
            push @output, "end entity;\n\n";
            push @output, generate_architecture_header($module_name, $mode, \@signals, \@instantiations);
            $arch_header_idx = $#output;  # Remember where architecture header ends
            $in_architecture = 1;
            $port_section_done = 1;
        }

        if ($line =~ /^\s*endmodule/) {
            # Just close architecture
            next;  # Footer will be added after loop
        }

        my $vhdl = translate_line($line, $line_num, $input, $mode, $in_entity, $in_architecture);

	if ($vhdl) {
	    push @output,$vhdl;
	}
    }

    # Insert intermediate signal declarations before architecture begin
    if ($arch_header_idx >= 0 && %intermediates) {
        my @intermediate_decls;
        push @intermediate_decls, "  -- Intermediate signals\n";
        foreach my $sig_name (sort keys %intermediates) {
            push @intermediate_decls, "  signal $sig_name : $intermediates{$sig_name};\n";
        }
        push @intermediate_decls, "begin\n";

        # Insert after architecture header
        splice @output, $arch_header_idx + 1, 0, @intermediate_decls;
    } else {
        # No intermediates, just add begin
        if ($arch_header_idx >= 0) {
            splice @output, $arch_header_idx + 1, 0, "begin\n";
        }
    }

    push @output, generate_footer($mode);

    # Write output array to file
    open my $out_fh, '>', $output or die "Can't create $output: $!\n";
    print $out_fh join('', @output);
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
    if ($line =~ /^\s*input\s+(?:wire\s+)?(?:\[(\d+):(\d+)\]\s+)?(\w+)\s*([,;]?)/) {
        my ($msb, $lsb, $name, $term) = ($1, $2, $3, $4);
        my $vhdl_type = port_type($msb, $lsb, $config->{lib});
        my $separator = ($term eq ',') ? ';' : '';
        return line_directive($line_num, $source_file) .
               "    $name : in $vhdl_type$separator\n";
    }

    if ($line =~ /^\s*output\s+(?:reg\s+)?(?:\[(\d+):(\d+)\]\s+)?(\w+)\s*([,;]?)/) {
        my ($msb, $lsb, $name, $term) = ($1, $2, $3, $4);
        my $vhdl_type = port_type($msb, $lsb, $config->{lib});
        my $separator = ($term eq ',') ? ';' : '';

        # Create unsigned intermediate for vector outputs with reg (implies arithmetic)
        if (defined($msb) && defined($lsb) && $line =~ /\breg\b/) {
            add_intermediate("${name}_next", "unsigned($msb downto $lsb)");
        }

        return line_directive($line_num, $source_file) .
               "    $name : inout $vhdl_type$separator\n";
    }
    
    # Wire/reg declarations (internal signals)
    if ($line =~ /^\s*(?:wire|reg)\s+(?:\[(\d+):(\d+)\]\s+)?(\w+);/) {
        my ($msb, $lsb, $name) = ($1, $2, $3);
        my $vhdl_type = port_type($msb, $lsb, $config->{lib});

        # Also create unsigned intermediate for vector signals (for arithmetic)
        if (defined($msb) && defined($lsb)) {
            add_intermediate("${name}_u", "unsigned($msb downto $lsb)");
        }

        return line_directive($line_num, $source_file) .
               "  signal $name : $vhdl_type;\n";
    }
    
    # Always blocks
    if ($line =~ /^\s*always\s+@\(posedge\s+(\w+)\)/) {
        if ($config->{temporal}) {
            return line_directive($line_num, $source_file) .
                   "  process\n  begin\n    wait until $1'event;\n    wait until rising_edge($1);\n";
        } else {
            return line_directive($line_num, $source_file) .
                   "  process($1)\n  begin\n    if rising_edge($1) then\n";
        }
    }

    if ($line =~ /^\s*always\s+@\(\*\)/) {
        return line_directive($line_num, $source_file) .
               "  process(all)\n  begin\n";  # VHDL-2008 process(all)
    }
    
    # Assignments
    if ($line =~ /^\s*assign\s+(\w+)\s*=\s*(.+);/) {
        return line_directive($line_num, $source_file) .
               "  $1 <= $2;\n";
    }
    
    # Non-blocking assignment (already uses <=)
    if ($line =~ /^\s*(\w+)\s*<=\s*(.+);/) {
        my ($lhs, $rhs) = ($1, $2);
        my $intermediate = "${lhs}_next";

        # Check if we have an intermediate for this signal and it's arithmetic
        our %intermediates;
        if (exists $intermediates{$intermediate} && $rhs =~ /[\+\-\*\/]/) {
            # Use intermediate for cleaner code
            my $expr = $rhs;
            # Convert to use intermediate
            if ($expr =~ /(\w+)\s*([\+\-\*\/])\s*(.+)/) {
                my ($left, $op, $right) = ($1, $2, $3);
                return line_directive($line_num, $source_file) .
                       "        $intermediate <= unsigned($left) $op $right;\n" .
                       "        $lhs <= std_logic_vector($intermediate);\n";
            }
        }

        return line_directive($line_num, $source_file) .
               "        $lhs <= " . translate_expression($rhs) . ";\n";
    }

    # Blocking assignment (= -> :=)
    if ($line =~ /^\s*(\w+)\s*=\s*(.+);/) {
        return line_directive($line_num, $source_file) .
               "        $1 := " . translate_expression($2) . ";\n";
    }

    # If statements
    if ($line =~ /^\s*if\s*\((.+)\)\s*$/) {
        my $condition = translate_condition($1);
        return line_directive($line_num, $source_file) .
               "      if $condition then\n";
    }

    # Else statements
    if ($line =~ /^\s*else\s*$/) {
        return line_directive($line_num, $source_file) .
               "      else\n";
    }

    # Begin/end blocks
    if ($line =~ /^\s*begin\s*$/) {
        return "";  # VHDL doesn't need begin after if/process
    }

    if ($line =~ /^\s*end\s*$/) {
        return "      end if;\n    end if;\n  end process;\n";
    }
    
    # Endmodule
    if ($line =~ /^\s*endmodule/) {
        return "";  # Handled by state machine
    }
    
    # Port list terminator
    if ($line =~ /^\s*\);/) {
        return "  );\n";
    }

    # Verilog gate primitives (2-input gates)
    if ($line =~ /^\s*(and|or|nand|nor|xor|xnor)\s+\w+\s*\(\s*(\w+)\s*,\s*(\w+)\s*,\s*(\w+)\s*\)\s*;/) {
        my ($gate, $output, $in1, $in2) = ($1, $2, $3, $4);
        return line_directive($line_num, $source_file) .
               "  $output <= $in1 $gate $in2;\n";
    }

    # Verilog gate primitives (1-input gates: not, buf)
    if ($line =~ /^\s*(not|buf)\s+\w+\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)\s*;/) {
        my ($gate, $output, $input) = ($1, $2, $3);
        if ($gate eq 'not') {
            return line_directive($line_num, $source_file) .
                   "  $output <= not $input;\n";
        } elsif ($gate eq 'buf') {
            return line_directive($line_num, $source_file) .
                   "  $output <= $input;\n";
        }
    }

    # Module instantiation
    if ($line =~ /^\s*(\w+)\s+(\w+)\s*\(/ &&
        $1 ne 'module' && $1 ne 'if' && $1 ne 'case' &&
        $1 !~ /^(and|or|nand|nor|xor|xnor|not|buf)$/) {
        my $module_type = $1;
        my $inst_name = $2;
        return line_directive($line_num, $source_file) .
               "  $inst_name: entity work.$module_type port map (\n";
    }
    
    # Port mapping: .port(signal)
    if ($line =~ /^\s*\.(\w+)\s*\(\s*(\w+)\s*\)([,)])/) {
        my ($port, $signal, $term) = ($1, $2, $3);
        my $separator = ($term eq ',') ? ',' : '';
        return line_directive($line_num, $source_file) .
               "    $port => $signal$separator\n";
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

sub extract_ports {
    my ($lines) = @_;
    my @ports;
    foreach my $line (@$lines) {
        if ($line =~ /^\s*(?:input|output|inout)\s+/) {
            push @ports, $line;
        }
    }
    return @ports;
}

sub extract_signals {
    my ($lines) = @_;
    my @signals;
    foreach my $line (@$lines) {
        if ($line =~ /^\s*(?:wire|reg)\s+/) {
            push @signals, $line;
        }
    }
    return @signals;
}

sub extract_instantiations {
    my ($lines) = @_;
    my @insts;

    # Look for module instantiations: module_name inst_name (...)
    # Exclude gate primitives
    for (my $i = 0; $i < @$lines; $i++) {
        if ($lines->[$i] =~ /^\s*(\w+)\s+(\w+)\s*\(/ &&
            $1 ne 'module' && $1 ne 'if' && $1 ne 'case' &&
            $1 !~ /^(and|or|nand|nor|xor|xnor|not|buf)$/) {
            # This is likely a module instantiation
            my $module_type = $1;
            my $inst_name = $2;
            push @insts, { type => $module_type, name => $inst_name };
        }
    }
    return @insts;
}

sub find_verilog_files {
    my ($path, $pattern) = @_;
    
    my @files;
    
    # Convert glob pattern to regex
    my $regex = glob_to_regex($pattern);
    
    # Use File::Find if available, otherwise manual recursion
    eval {
        require File::Find;
        no warnings 'once';
        File::Find::find(
            sub {
                return unless -f $_;
                my $file = $File::Find::name;
                if (basename($file) =~ /$regex/) {
                    push @files, $file;
                }
            },
            $path
        );
    };
    
    if ($@) {
        # File::Find not available, do manual search
        @files = manual_find($path, $regex);
    }
    
    return sort @files;
}

sub manual_find {
    my ($dir, $regex) = @_;
    my @found;
    
    opendir(my $dh, $dir) or return @found;
    my @entries = readdir($dh);
    closedir($dh);
    
    foreach my $entry (@entries) {
        next if $entry eq '.' || $entry eq '..';
        my $path = "$dir/$entry";
        
        if (-d $path) {
            # Recurse into subdirectory
            push @found, manual_find($path, $regex);
        } elsif (-f $path && basename($path) =~ /$regex/) {
            push @found, $path;
        }
    }
    
    return @found;
}

sub glob_to_regex {
    my ($glob) = @_;
    
    # Convert shell glob to regex
    $glob = quotemeta($glob);
    $glob =~ s/\\\*/.*/g;   # * -> .*
    $glob =~ s/\\\?/./g;    # ? -> .
    
    return qr/^$glob$/;
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

    # Numeric literals: 8'h00 -> x"00", 8'd10 -> std_logic_vector(to_unsigned(10, 8))
    $expr =~ s/(\d+)'h([0-9a-fA-F]+)/x"$2"/g;
    $expr =~ s/(\d+)'d(\d+)/std_logic_vector(to_unsigned($2, $1))/g;
    $expr =~ s/(\d+)'b([01]+)/"$2"/g;

    # Bit concatenation: {a, b, c} -> a & b & c
    $expr =~ s/\{([^}]+)\}/concat($1)/ge;

    # Ternary: a ? b : c -> b when a else c
    if ($expr =~ /(.+?)\s*\?\s*(.+?)\s*:\s*(.+)/) {
        return "$2 when $1 else $3";
    }

    # Arithmetic operations on vectors: wrap with unsigned conversion
    # Handle: signal + literal, signal - literal, signal + signal, etc.
    if ($expr =~ /(\w+)\s*([\+\-\*\/])\s*(.+)/) {
        my ($left, $op, $right) = ($1, $2, $3);
        # Check if this looks like vector arithmetic (not bit operations)
        if ($op =~ /[\+\-\*\/]/) {
            return "std_logic_vector(unsigned($left) $op $right)";
        }
    }

    return $expr;
}

sub translate_condition {
    my ($cond) = @_;

    # Remove whitespace
    $cond =~ s/^\s+|\s+$//g;

    # If it's just a bare identifier (no operators), add = '1'
    if ($cond =~ /^(\w+)$/) {
        return "$1 = '1'";
    }

    # If it has negation: !signal -> signal = '0'
    if ($cond =~ /^!(\w+)$/) {
        return "$1 = '0'";
    }

    # Otherwise return as-is (already has comparison operators)
    return $cond;
}

sub concat {
    my ($items) = @_;
    my @parts = split /\s*,\s*/, $items;
    return join(' & ', @parts);
}

sub add_intermediate {
    my ($name, $vhdl_type) = @_;
    our %intermediates;
    $intermediates{$name} = $vhdl_type unless exists $intermediates{$name};
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
    my ($module_name, $mode, $signals_ref, $insts_ref) = @_;
    
    my @signals = @{$signals_ref || []};
    my @insts = @{$insts_ref || []};
    
    my $arch = "architecture rtl of $module_name is\n";
    
    # Component declarations for instantiated modules
    if (@insts) {
        $arch .= "  -- Component declarations\n";
        my %seen_components;
        foreach my $inst (@insts) {
            my $comp_type = $inst->{type};
            next if $seen_components{$comp_type};
            $seen_components{$comp_type} = 1;
            
            # GHDL will auto-bind by name, so we can use simple declaration
            # or rely on direct entity instantiation (VHDL-93+)
            $arch .= "  -- Component $comp_type (auto-bound by GHDL)\n";
        }
        $arch .= "\n";
    }
    
    # Internal signals
    if (@signals) {
        $arch .= "  -- Internal signals\n";
    }
    
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
       sv2ghdl [options] -find [path]

Options:
  --mode=MODE           Translation mode (default: standard)
  -o, --output=FILE     Output file (for single file translation)
  -d, --outdir=DIR      Output directory (for multiple files)
  -find[=PATH]          Find and translate all Verilog files (default: .)
  --name=PATTERN        File pattern for -find (default: *.v)
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
  # Translate single file
  sv2ghdl design.v
  sv2ghdl design.v -o output.vhd
  
  # Translate with mode
  sv2ghdl --mode=enhanced design.v
  
  # Find and translate all .v files in current directory
  sv2ghdl -find -d vhdl_output
  
  # Find all .v files starting from rtl/ directory
  sv2ghdl -find=rtl -d translated
  
  # Find .sv files instead of .v
  sv2ghdl -find --name='*.sv' -d output
  
  # Find and translate with enhanced mode
  sv2ghdl -find --mode=enhanced -d vhdl_output
  
  # Verbose find
  sv2ghdl -find -v -d output

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
