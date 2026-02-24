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
    'standard'           => { lib => 'ieee',        temporal => 0, accel => 0, logic3d => 0 },
    'enhanced'           => { lib => 'cameron_eda', temporal => 0, accel => 0, logic3d => 0 },
    'logic3d'            => { lib => 'work',        temporal => 0, accel => 0, logic3d => 1 },
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

    # Sanitize identifiers: VHDL doesn't allow leading/trailing underscores
    # or double underscores in basic identifiers
    foreach my $line (@lines) {
        # Replace leading underscore on word-boundary identifiers: _foo → v_foo
        # \b_ matches only at non-word→underscore boundary, so abc_def is untouched
        $line =~ s/\b_(\w)/v_$1/g;
    }

    # Extract module info
    my $module_name = extract_module_name(\@lines);
    my @ports = extract_ports(\@lines);
    my @signals = extract_signals(\@lines);
    my @instantiations = extract_instantiations(\@lines);

    # Build type map for signals/ports (used for integer literal conversion)
    our %signal_types = ();
    foreach my $line (@lines) {
        # Input/output ports: input [7:0] foo; or input bar;
        if ($line =~ /^\s*(?:input|output|inout)\s+(?:wire\s+|reg\s+)?(?:signed\s+)?(?:\[(\d+):(\d+)\]\s+)?(\w+)/) {
            my ($msb, $lsb, $name) = ($1, $2, $3);
            if (defined $msb) {
                $signal_types{$name} = { type => 'vector', width => $msb - $lsb + 1 };
            } else {
                $signal_types{$name} = { type => 'scalar' };
            }
        }
        # Wire/reg declarations
        if ($line =~ /^\s*(?:wire|reg)\s+(?:signed\s+)?(?:\[(\d+):(\d+)\]\s+)?([\w\s,]+);/) {
            my ($msb, $lsb, $names) = ($1, $2, $3);
            foreach my $name (split /\s*,\s*/, $names) {
                $name =~ s/^\s+|\s+$//g;
                next if $name eq '';
                if (defined $msb) {
                    $signal_types{$name} = { type => 'vector', width => $msb - $lsb + 1 };
                } else {
                    $signal_types{$name} = { type => 'scalar' };
                }
            }
        }
        # Integer declarations
        if ($line =~ /^\s*integer\s+([\w\s,]+);/) {
            foreach my $name (split /\s*,\s*/, $1) {
                $name =~ s/^\s+|\s+$//g;
                next if $name eq '';
                $signal_types{$name} = { type => 'integer' };
            }
        }
    }

    # Build output array instead of printing directly
    my @output;

    # Track intermediate signals needed
    our %intermediates = ();  # signal_name => type_declaration
    our @signal_decls = ();    # Array of signal declaration lines

    push @output, generate_header($mode, $input, $module_name);

    # Translate line by line with state tracking
    my $line_num = 0;
    my $in_entity = 0;
    my $in_architecture = 0;
    my $port_section_done = 0;
    my $arch_header_idx = -1;  # Index where architecture header ends

    my $in_block_comment = 0;
    my $in_process = 0;       # inside a process-generating block (initial or always)
    my $process_depth = 0;    # begin/end nesting depth within process block
    my $process_pending = 0;  # saw 'initial'/'always' alone, waiting for begin/stmt
    my @block_stack;          # tracks what each begin/end nesting level is: 'initial', 'if', 'else', 'block'
    my $last_keyword = '';    # last control keyword seen (for begin context)
    my $in_task = 0;          # inside task/function block (skip until endtask/endfunction)
    my $process_has_if = 0;   # process was opened with rising_edge/falling_edge if wrapper
    my $process_has_sens = 0; # process has sensitivity list (no wait allowed)

    foreach my $line (@lines) {
        $line_num++;

        # Strip Verilog attributes (* ... *)
        $line =~ s/\(\*.*?\*\)\s*//g;

        # Handle block comments /* ... */
        if ($in_block_comment) {
            if ($line =~ s/^.*?\*\///) {
                $in_block_comment = 0;
                # Fall through to process rest of line
            } else {
                push @output, "--" . $line;
                next;
            }
        }
        # Strip inline block comments and detect unterminated ones
        while ($line =~ s|/\*.*?\*/||g) {}  # Remove complete inline comments
        if ($line =~ s|/\*.*$||) {
            $in_block_comment = 1;
            # Process what's left of the line before the comment
        }

        # Skip empty lines left after comment stripping
        if ($line =~ /^\s*$/) {
            push @output, "\n";
            next;
        }

        # State machine for entity/architecture boundary
        if ($line =~ /^\s*module\s+\w+/) {
            $in_entity = 1;
        }

        # End of ports section (first non-port declaration)
        # Skip module, input, output, inout, parameter lines
        if ($in_entity && !$port_section_done &&
            $line !~ /^\s*(?:module|input|output|inout|parameter)\b/ &&
            ($line =~ /^\s*(?:wire|reg|always|assign|initial|integer|real|time|task|function|genvar|generate|defparam|specify|localparam)\b/ || $line =~ /^\s*\w+\s+\w+\s*\(/)) {
            # Close entity, start architecture
            push @output, "end entity;\n\n";
            push @output, generate_architecture_header($module_name, $mode, \@signals, \@instantiations);
            $arch_header_idx = $#output;  # Remember where architecture header ends
            $in_architecture = 1;
            $port_section_done = 1;
        }

        if ($line =~ /^\s*endmodule/) {
            # Auto-close any open process before ending module
            if ($in_process) {
                my $close = "";
                $close .= "    end if;\n" if $process_has_if;
                $close .= "    wait;\n" unless $process_has_sens;
                push @output, "${close}  end process;\n";
                $in_process = 0;
                $process_depth = 0;
                $process_has_if = 0;
                $process_has_sens = 0;
                @block_stack = ();
            }
            next;  # Footer will be added after loop
        }

        my $vhdl = translate_line($line, $line_num, $input, $mode, $in_entity, $in_architecture,
                                  \$in_process, \$process_depth, \$process_pending,
                                  \@block_stack, \$last_keyword,
                                  \$in_task, \$process_has_if, \$process_has_sens);

	if ($vhdl) {
	    push @output,$vhdl;
	}
    }

    # Insert signal declarations and begin before architecture body
    if ($arch_header_idx >= 0) {
        my @decls;

        # Insert signal declarations from wire/reg statements
        if (@signal_decls) {
            push @decls, "  -- Internal signals\n";
            push @decls, @signal_decls;
        }

        # Insert intermediate signal declarations
        if (%intermediates) {
            push @decls, "  -- Intermediate signals\n" unless @signal_decls;
            foreach my $sig_name (sort keys %intermediates) {
                push @decls, "  signal $sig_name : $intermediates{$sig_name};\n";
            }
        }

        # Always add begin
        push @decls, "begin\n";

        # Insert after architecture header
        splice @output, $arch_header_idx + 1, 0, @decls;
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
    my ($line, $line_num, $source_file, $mode, $in_entity, $in_architecture,
        $in_process_ref, $process_depth_ref, $process_pending_ref,
        $block_stack_ref, $last_keyword_ref,
        $in_task_ref, $process_has_if_ref, $process_has_sens_ref) = @_;

    my $config = $MODES{$mode};

    # Preserve blank lines
    return $line if $line =~ /^\s*$/;

    # Strip inline // comments (but not inside strings)
    if ($line =~ m{^([^"]*?)\s*//} && $line !~ m{^\s*//}) {
        $line = $1 . "\n";
    }

    # Preserve full-line comments
    if ($line =~ m{^\s*//(.*)}) {
        return "--$1\n";
    }

    # Preprocessor directives
    if ($line =~ /^\s*`timescale\b/) {
        return "-- " . $line;  # No VHDL equivalent
    }
    if ($line =~ /^\s*`include\b/) {
        return "-- " . $line;  # Include files not supported
    }
    if ($line =~ /^\s*`(?:define|ifdef|ifndef|else|endif|undef)\b/) {
        return "-- " . $line;
    }

    # --- Task/function block skipping ---
    if ($$in_task_ref) {
        if ($line =~ /^\s*(?:endtask|endfunction)\b/) {
            $$in_task_ref = 0;
        }
        return "-- " . $line;  # Comment out everything inside task/function
    }
    if ($line =~ /^\s*(?:task|function)\b/) {
        $$in_task_ref = 1;
        return "-- " . $line;
    }

    # --- Integer/real/time variable declarations (become signals) ---
    if ($line =~ /^\s*(integer|real|time)\s+([\w\s,]+);/) {
        my ($type, $names) = ($1, $2);
        my @var_names = split /\s*,\s*/, $names;
        foreach my $name (@var_names) {
            $name =~ s/^\s+|\s+$//g;
            next if $name eq '';
            our @signal_decls;
            push @signal_decls, "  signal $name : $type;\n";
        }
        return "";  # Collected, will be inserted before begin
    }

    # --- Handle pending process (saw 'initial'/'always' alone, waiting for begin/stmt) ---
    if ($$process_pending_ref) {
        $$process_pending_ref = 0;
        if ($line =~ /^\s*begin\s*$/) {
            # initial/always begin → full tracked block at depth 1
            $$in_process_ref = 1;
            $$process_depth_ref = 1;
            push @$block_stack_ref, 'initial';
            return "  process\n  begin\n";
        }
        # Multi-line construct (if, for, case, etc.) → open process at depth 0
        # Inner begin/end will track the real nesting; process closes when depth returns to 0
        if ($line =~ /^\s*(?:if|for|while|case|repeat)\b/) {
            $$in_process_ref = 1;
            $$process_depth_ref = 0;
            $$last_keyword_ref = '';
            $$process_has_if_ref = 0;
            $$process_has_sens_ref = 0;
            # Recursively process the line with process state already set
            my $line_result = translate_line($line, $line_num, $source_file, $mode,
                                             $in_entity, $in_architecture,
                                             $in_process_ref, $process_depth_ref,
                                             $process_pending_ref, $block_stack_ref,
                                             $last_keyword_ref, $in_task_ref,
                                             $process_has_if_ref, $process_has_sens_ref);
            return "  process\n  begin\n" . ($line_result // "");
        }
        # Simple single-line statement
        my $stmt = $line;
        $stmt =~ s/^\s+//;
        $stmt =~ s/\s+$//;
        my $translated = translate_statement($stmt, $line_num, $source_file, $config);
        return "  process\n  begin\n" .
               "    $translated\n" .
               "    wait;\n  end process;\n";
    }

    # --- Process block state tracking (initial and always blocks) ---
    if ($$in_process_ref) {
        # Handle begin/end nesting within process blocks
        if ($line =~ /^\s*begin\s*$/) {
            $$process_depth_ref++;
            # Determine context from last keyword
            my $ctx = $$last_keyword_ref || 'block';
            push @$block_stack_ref, $ctx;
            $$last_keyword_ref = '';
            return "";  # begin consumed (VHDL uses then/loop/etc. instead)
        }
        if ($line =~ /^\s*end\s*$/) {
            $$process_depth_ref--;
            my $ctx = pop @$block_stack_ref // 'block';
            if ($$process_depth_ref <= 0) {
                $$in_process_ref = 0;
                $$process_depth_ref = 0;
                my $had_if = $$process_has_if_ref;
                my $had_sens = $$process_has_sens_ref;
                $$process_has_if_ref = 0;
                $$process_has_sens_ref = 0;
                @$block_stack_ref = ();
                # Close any open if, then close process
                my $close = "";
                $close .= "    end if;\n" if $ctx eq 'if' || $ctx eq 'else';
                # If process was opened with edge-triggered if, close that too
                $close .= "    end if;\n" if $had_if;
                # No wait in processes with sensitivity lists
                $close .= "    wait;\n" unless $had_sens;
                return "${close}  end process;\n";
            }
            # Generate proper closing for the block context
            if ($ctx eq 'if' || $ctx eq 'else') {
                return "    end if;\n";
            }
            return "";  # plain block, no VHDL close needed
        }
    }

    # Initial block with begin (on same line)
    if ($line =~ /^\s*initial\s+begin\s*$/) {
        $$in_process_ref = 1;
        $$process_depth_ref = 1;
        push @$block_stack_ref, 'initial';
        $$last_keyword_ref = '';
        $$process_has_if_ref = 0;
        $$process_has_sens_ref = 0;
        return line_directive($line_num, $source_file) .
               "  process\n  begin\n";
    }

    # Bare 'initial' alone on a line — begin/stmt comes next
    if ($line =~ /^\s*initial\s*$/) {
        $$process_pending_ref = 1;
        return line_directive($line_num, $source_file);
    }

    # Initial block - single statement (no begin)
    if ($line =~ /^\s*initial\s+(.+)/) {
        my $stmt = $1;
        my $translated = translate_statement($stmt, $line_num, $source_file, $config);
        return line_directive($line_num, $source_file) .
               "  process\n  begin\n" .
               "    $translated\n" .
               "    wait;\n  end process;\n";
    }

    # Bare always with begin (no sensitivity list): always begin ... end
    if ($line =~ /^\s*always\s+begin\s*$/) {
        $$in_process_ref = 1;
        $$process_depth_ref = 1;
        push @$block_stack_ref, 'initial';
        $$last_keyword_ref = '';
        $$process_has_if_ref = 0;
        $$process_has_sens_ref = 0;
        return line_directive($line_num, $source_file) .
               "  process\n  begin\n";
    }

    # Bare 'always' alone on a line — begin/stmt comes next
    if ($line =~ /^\s*always\s*$/) {
        $$process_pending_ref = 1;
        return line_directive($line_num, $source_file);
    }

    # --- Delay statements ---
    if ($line =~ /^\s*#\s*(\d+)\s*;?\s*$/) {
        return line_directive($line_num, $source_file) .
               "    wait for $1 ns;\n";
    }

    # --- Event declarations and triggers ---
    if ($line =~ /^\s*event\s+\w+/) {
        return "-- " . $line;  # Events have no direct VHDL equivalent
    }
    if ($line =~ /^\s*->\s*\w+/) {
        return "-- " . $line;  # Event trigger: no direct VHDL equivalent
    }

    # --- Wait statements ---
    if ($line =~ /^\s*wait\s*\(.+\)\s*;/) {
        return "-- " . $line;  # wait(expr) not yet translated
    }

    # --- Fork/join, disable, forever ---
    if ($line =~ /^\s*(?:fork|join)\b/) {
        return "-- " . $line;
    }
    if ($line =~ /^\s*disable\s+\w+/) {
        return "-- " . $line;
    }
    if ($line =~ /^\s*forever\b/) {
        return "-- " . $line;
    }

    # --- System tasks (inside processes) ---
    # $display with no args or empty parens = just a newline
    if ($line =~ /^\s*\$display\s*(?:\(\s*\))?\s*;/) {
        return line_directive($line_num, $source_file) .
               "    report \"\" severity note;\n";
    }
    if ($line =~ /^\s*\$display\s*\((.+)\)\s*;/) {
        my $args = $1;
        return line_directive($line_num, $source_file) .
               "    " . translate_display($args) . "\n";
    }
    if ($line =~ /^\s*\$write\s*\((.+)\)\s*;/) {
        my $args = $1;
        return line_directive($line_num, $source_file) .
               "    " . translate_display($args) . "\n";
    }
    if ($line =~ /^\s*\$finish\s*(?:\(\s*\d*\s*\))?\s*;/) {
        return line_directive($line_num, $source_file) .
               "    std.env.finish;\n";
    }
    if ($line =~ /^\s*\$stop\s*(?:\(\s*\d*\s*\))?\s*;/) {
        return line_directive($line_num, $source_file) .
               "    std.env.stop;\n";
    }
    # Other system tasks - comment out rather than FIXME (cleaner output)
    if ($line =~ /^\s*\$\w+/) {
        return "-- " . $line;
    }

    # Module declaration — order matters: empty port list before generic port open
    if ($line =~ /^\s*module\s+(\w+)\s*\(\s*\)\s*;/) {
        # Module with empty port list: module foo();
        return line_directive($line_num, $source_file) .
               "entity $1 is\n";
    }
    if ($line =~ /^\s*module\s+(\w+)\s*;/) {
        return line_directive($line_num, $source_file) .
               "entity $1 is\n";
    }
    if ($line =~ /^\s*module\s+(\w+)\s*\(/) {
        return line_directive($line_num, $source_file) .
               "entity $1 is\n  port (\n";
    }
    
    # Parameters -> Generics
    if ($line =~ /^\s*parameter\s+(\w+)\s*=\s*(.+);/) {
        return line_directive($line_num, $source_file) .
               "  generic (\n    $1 : integer := $2\n  );\n";
    }
    
    # Ports
    if ($line =~ /^\s*input\s+(?:wire\s+)?(?:signed\s+)?(?:\[(\d+):(\d+)\]\s+)?(\w+)\s*([,;]?)/) {
        my ($msb, $lsb, $name, $term) = ($1, $2, $3, $4);
        my $vhdl_type = port_type($msb, $lsb, $config);
        my $separator = ($term eq ',') ? ';' : '';
        return line_directive($line_num, $source_file) .
               "    $name : in $vhdl_type$separator\n";
    }

    if ($line =~ /^\s*output\s+(?:reg\s+)?(?:signed\s+)?(?:\[(\d+):(\d+)\]\s+)?(\w+)\s*([,;]?)/) {
        my ($msb, $lsb, $name, $term) = ($1, $2, $3, $4);
        my $vhdl_type = port_type($msb, $lsb, $config);
        my $separator = ($term eq ',') ? ';' : '';

        # Create unsigned intermediate for vector outputs with reg (implies arithmetic)
        if (defined($msb) && defined($lsb) && $line =~ /\breg\b/) {
            add_intermediate("${name}_next", "unsigned($msb downto $lsb)");
        }

        return line_directive($line_num, $source_file) .
               "    $name : inout $vhdl_type$separator\n";
    }
    
    # Wire/reg declarations (internal signals) - multiple signals on one line
    if ($line =~ /^\s*(?:wire|reg)\s+(?:signed\s+)?(?:\[(\d+):(\d+)\]\s+)?([\w\s,]+);/) {
        my ($msb, $lsb, $names) = ($1, $2, $3);
        my $vhdl_type = port_type($msb, $lsb, $config);

        # Split multiple signal names
        my @signal_names = split /\s*,\s*/, $names;

        foreach my $name (@signal_names) {
            $name =~ s/^\s+|\s+$//g;  # Trim whitespace
            next if $name eq '';

            # Also create unsigned intermediate for vector signals (for arithmetic)
            if (defined($msb) && defined($lsb)) {
                add_intermediate("${name}_u", "unsigned($msb downto $lsb)");
            }

            # Store signal declaration for later insertion
            our @signal_decls;
            push @signal_decls, "  signal $name : $vhdl_type;\n";
        }

        return "";  # Don't output immediately, will be inserted before begin
    }
    
    # Always blocks with asynchronous reset
    if ($line =~ /^\s*always\s+@\s*\(\s*posedge\s+(\w+)\s+or\s+posedge\s+(\w+)\)/) {
        my ($clk, $rst) = ($1, $2);
        # Store clock name for elsif handling
        our $async_reset_clk = $clk;
        $$in_process_ref = 1;
        $$process_depth_ref = 1;
        push @$block_stack_ref, 'initial';
        $$last_keyword_ref = '';
        $$process_has_if_ref = 0;
        $$process_has_sens_ref = 1;
        return line_directive($line_num, $source_file) .
               "  process($clk, $rst)\n  begin\n";
    }

    # Always blocks with synchronous logic only (posedge)
    if ($line =~ /^\s*always\s+@\s*\(\s*posedge\s+(\w+)\s*\)/) {
        my $clk = $1;
        my $null_body = ($line =~ /;\s*$/);
        if ($null_body) {
            return line_directive($line_num, $source_file) .
                   "  -- null always @(posedge $clk)\n";
        }
        $$in_process_ref = 1;
        $$process_depth_ref = 1;
        push @$block_stack_ref, 'initial';
        $$last_keyword_ref = '';
        $$process_has_if_ref = 1;
        $$process_has_sens_ref = 1;
        if ($config->{temporal}) {
            return line_directive($line_num, $source_file) .
                   "  process\n  begin\n    wait until ${clk}'event;\n    wait until rising_edge($clk);\n";
        } else {
            return line_directive($line_num, $source_file) .
                   "  process($clk)\n  begin\n    if rising_edge($clk) then\n";
        }
    }

    # Always blocks with negedge
    if ($line =~ /^\s*always\s+@\s*\(\s*negedge\s+(\w+)\s*\)/) {
        my $clk = $1;
        my $null_body = ($line =~ /;\s*$/);
        if ($null_body) {
            return line_directive($line_num, $source_file) .
                   "  -- null always @(negedge $clk)\n";
        }
        $$in_process_ref = 1;
        $$process_depth_ref = 1;
        push @$block_stack_ref, 'initial';
        $$last_keyword_ref = '';
        $$process_has_if_ref = 1;
        $$process_has_sens_ref = 1;
        return line_directive($line_num, $source_file) .
               "  process($clk)\n  begin\n    if falling_edge($clk) then\n";
    }

    # Always @(*) — combinational
    if ($line =~ /^\s*always\s+@\s*\(\s*\*\s*\)\s*;?\s*$/) {
        my $null_body = ($line =~ /;\s*$/);
        if ($null_body) {
            return line_directive($line_num, $source_file) .
                   "  -- null always @(*)\n";
        }
        $$in_process_ref = 1;
        $$process_depth_ref = 1;
        push @$block_stack_ref, 'initial';
        $$last_keyword_ref = '';
        $$process_has_if_ref = 0;
        $$process_has_sens_ref = 1;
        return line_directive($line_num, $source_file) .
               "  process(all)\n  begin\n";  # VHDL-2008 process(all)
    }

    # Always @(signal list) — level-sensitive
    if ($line =~ /^\s*always\s+@\s*\(([^)]+)\)\s*;?\s*$/) {
        my $sens = $1;
        my $null_body = ($line =~ /;\s*$/);
        # Convert "or" to "," for VHDL sensitivity list
        $sens =~ s/\bor\b/,/g;
        $sens =~ s/\s+/ /g;
        $sens =~ s/^\s+|\s+$//g;
        if ($null_body) {
            return line_directive($line_num, $source_file) .
                   "  -- null always @($sens)\n";
        }
        $$in_process_ref = 1;
        $$process_depth_ref = 1;
        push @$block_stack_ref, 'initial';
        $$last_keyword_ref = '';
        $$process_has_if_ref = 0;
        $$process_has_sens_ref = 1;
        return line_directive($line_num, $source_file) .
               "  process($sens)\n  begin\n";
    }

    # Bare always with single statement (no @ or begin): always stmt;
    if ($line =~ /^\s*always\s+(?!@|begin)(.+;)\s*$/) {
        my $stmt = $1;
        my $translated = translate_statement($stmt, $line_num, $source_file, $config);
        return line_directive($line_num, $source_file) .
               "  process\n  begin\n" .
               "    $translated\n" .
               "    wait;\n  end process;\n";
    }
    
    # Assignments (concurrent)
    if ($line =~ /^\s*assign\s+(\w+)\s*=\s*(.+);/) {
        my ($lhs, $rhs) = ($1, $2);
        my $translated_rhs = translate_expression($rhs);
        $translated_rhs = convert_integer_for_type($translated_rhs, $lhs);
        return line_directive($line_num, $source_file) .
               "  $lhs <= $translated_rhs;\n";
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

        my $translated_rhs = translate_expression($rhs);
        # Convert bare integers based on target type
        $translated_rhs = convert_integer_for_type($translated_rhs, $lhs);

        return line_directive($line_num, $source_file) .
               "        $lhs <= $translated_rhs;\n";
    }

    # Blocking assignment (= -> :=)
    if ($line =~ /^\s*(\w+)\s*=\s*(.+);/) {
        my ($lhs, $rhs) = ($1, $2);
        my $translated_rhs = translate_expression($rhs);
        # Convert bare integers based on target type
        $translated_rhs = convert_integer_for_type($translated_rhs, $lhs);
        return line_directive($line_num, $source_file) .
               "        $lhs := $translated_rhs;\n";
    }

    # If statements: if(cond) or if(cond) begin
    if ($line =~ /^\s*if\s*\((.+)\)\s*(?:begin\s*)?$/) {
        my $cond_text = $1;
        my $has_begin = ($line =~ /begin\s*$/);
        my $condition = translate_condition($cond_text);
        if ($has_begin && $$in_process_ref) {
            # if(cond) begin — consume begin, track depth
            $$process_depth_ref++;
            push @$block_stack_ref, 'if';
        } else {
            $$last_keyword_ref = 'if' if $last_keyword_ref;
        }
        return line_directive($line_num, $source_file) .
               "      if $condition then\n";
    }

    # Else if statements: else if(cond) or else if(cond) begin
    if ($line =~ /^\s*else\s+if\s*\((.+)\)\s*(?:begin\s*)?$/) {
        my $cond_text = $1;
        my $has_begin = ($line =~ /begin\s*$/);
        my $condition = translate_condition($cond_text);
        if ($has_begin && $$in_process_ref) {
            $$process_depth_ref++;
            push @$block_stack_ref, 'if';
        } else {
            $$last_keyword_ref = 'if' if $last_keyword_ref;
        }
        return line_directive($line_num, $source_file) .
               "      elsif $condition then\n";
    }

    # end else begin — close if, start else block
    if ($line =~ /^\s*end\s+else\s+begin\s*$/ && $$in_process_ref) {
        # The end closes the previous if block
        $$process_depth_ref--;
        my $ctx = pop @$block_stack_ref // 'block';
        # Start the else block
        $$process_depth_ref++;
        push @$block_stack_ref, 'else';
        return line_directive($line_num, $source_file) .
               "      else\n";
    }

    # end else if(cond) begin — close if, start elsif block
    if ($line =~ /^\s*end\s+else\s+if\s*\((.+)\)\s*(?:begin\s*)?$/ && $$in_process_ref) {
        my $cond_text = $1;
        my $has_begin = ($line =~ /begin\s*$/);
        my $condition = translate_condition($cond_text);
        $$process_depth_ref--;
        my $ctx = pop @$block_stack_ref // 'block';
        if ($has_begin) {
            $$process_depth_ref++;
            push @$block_stack_ref, 'if';
        } else {
            $$last_keyword_ref = 'if' if $last_keyword_ref;
        }
        return line_directive($line_num, $source_file) .
               "      elsif $condition then\n";
    }

    # end else — close if, start else
    if ($line =~ /^\s*end\s+else\s*$/ && $$in_process_ref) {
        $$process_depth_ref--;
        my $ctx = pop @$block_stack_ref // 'block';
        $$last_keyword_ref = 'else' if $last_keyword_ref;
        return line_directive($line_num, $source_file) .
               "      else\n";
    }

    # Else begin
    if ($line =~ /^\s*else\s+begin\s*$/) {
        if ($$in_process_ref) {
            $$process_depth_ref++;
            push @$block_stack_ref, 'else';
        } else {
            $$last_keyword_ref = 'else' if $last_keyword_ref;
        }
        return line_directive($line_num, $source_file) .
               "      else\n";
    }

    # Else statements (bare)
    if ($line =~ /^\s*else\s*$/) {
        our $async_reset_clk;
        if (defined($async_reset_clk)) {
            # In async reset process, else becomes elsif rising_edge
            my $result = line_directive($line_num, $source_file) .
                         "    elsif rising_edge($async_reset_clk) then\n";
            $async_reset_clk = undef;  # Clear for next process
            return $result;
        } else {
            $$last_keyword_ref = 'else' if $last_keyword_ref;
            return line_directive($line_num, $source_file) .
                   "      else\n";
        }
    }

    # Begin/end blocks (fallback — only fires when NOT inside tracked process)
    if ($line =~ /^\s*begin\s*$/) {
        return "";  # VHDL doesn't need begin after if/process
    }

    if ($line =~ /^\s*end\s*$/) {
        return "";  # Untracked end — comment out rather than generating broken VHDL
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
        if ($config->{logic3d}) {
            return line_directive($line_num, $source_file) .
                   "  $output <= l3d_$gate($in1, $in2);\n";
        } else {
            return line_directive($line_num, $source_file) .
                   "  $output <= $in1 $gate $in2;\n";
        }
    }

    # Verilog gate primitives (1-input gates: not, buf)
    if ($line =~ /^\s*(not|buf)\s+\w+\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)\s*;/) {
        my ($gate, $output, $input) = ($1, $2, $3);
        if ($config->{logic3d}) {
            return line_directive($line_num, $source_file) .
                   "  $output <= l3d_$gate($input);\n";
        } elsif ($gate eq 'not') {
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
        my $l = $line;
        $l =~ s/\(\*.*?\*\)\s*//g;  # Strip attributes
        if ($l =~ /^\s*module\s+(\w+)/) {
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
            $1 ne 'module' && $1 ne 'if' && $1 ne 'else' && $1 ne 'elsif' && $1 ne 'case' &&
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
    my ($msb, $lsb, $config) = @_;

    if ($config->{logic3d}) {
        # 3D logic mode - single bit only for ATPG gates
        return "logic3d";
    } elsif (defined $msb && defined $lsb) {
        return "std_logic_vector($msb downto $lsb)";
    } else {
        return "std_logic";
    }
}

sub convert_integer_for_type {
    my ($value, $target_name) = @_;
    our %signal_types;

    # If the value is not a bare integer, return as-is
    return $value unless $value =~ /^\d+$/;

    if (exists $signal_types{$target_name}) {
        my $info = $signal_types{$target_name};
        if ($info->{type} eq 'scalar') {
            # std_logic: 0 → '0', 1 → '1'
            return "'0'" if $value == 0;
            return "'1'" if $value == 1;
        } elsif ($info->{type} eq 'vector') {
            # std_logic_vector: N → std_logic_vector(to_unsigned(N, width))
            return "std_logic_vector(to_unsigned($value, $info->{width}))";
        } elsif ($info->{type} eq 'integer') {
            return $value;  # Integer targets accept integer literals
        }
    }
    return $value;  # Unknown type, return as-is
}

sub translate_expression {
    my ($expr) = @_;

    # Numeric literals: 8'h00 -> x"00", 8'hzz -> x"zz", 8'd10 -> std_logic_vector(to_unsigned(10, 8))
    $expr =~ s/(\d+)'h([0-9a-fA-FxXzZ]+)/x"$2"/g;
    $expr =~ s/(\d+)'d(\d+)/std_logic_vector(to_unsigned($2, $1))/g;
    # Single bit: 1'b0 -> '0', 1'bx -> 'X', 1'bz -> 'Z'
    $expr =~ s/1'b([01xXzZ])/'\U$1\E'/g;
    # Multi-bit binary: 4'b0101 -> "0101", 4'bxxxx -> "XXXX"
    $expr =~ s/(\d+)'b([01xXzZ]+)/"\U$2\E"/g;

    # Bit slicing: signal[msb:lsb] -> signal(msb downto lsb)
    $expr =~ s/(\w+)\[(\d+):(\d+)\]/$1($2 downto $3)/g;

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

    # Translate expressions within the condition
    $cond = translate_expression($cond);

    # If it's just a bare identifier (no operators), add = '1'
    if ($cond =~ /^(\w+)$/) {
        return "$1 = '1'";
    }

    # If it has negation: !signal -> signal = '0'
    if ($cond =~ /^!(\w+)$/) {
        return "$1 = '0'";
    }

    # Translate comparison operators
    $cond =~ s/!==/\/=  /g;  # Verilog case inequality (extra space to not re-match)
    $cond =~ s/===/=   /g;  # Verilog case equality
    $cond =~ s/!=/\/=/g;    # Verilog inequality -> VHDL /=
    $cond =~ s/==/=/g;      # Verilog equality -> VHDL =

    # Clean up extra spaces from case operator translation
    $cond =~ s/\s+/ /g;

    # Type-aware integer conversion in comparisons: signal = 0 → signal = '0'
    if ($cond =~ /^(\w+)\s*(=|\/=|<|>|<=|>=)\s*(\d+)$/) {
        my ($sig, $op, $val) = ($1, $2, $3);
        my $converted = convert_integer_for_type($val, $sig);
        return "$sig $op $converted";
    }

    return $cond;
}

sub concat {
    my ($items) = @_;
    my @parts = split /\s*,\s*/, $items;
    return join(' & ', @parts);
}

sub translate_statement {
    my ($stmt, $line_num, $source_file, $config) = @_;

    # Delay: # N ;
    if ($stmt =~ /^#\s*(\d+)\s*;?$/) {
        return "wait for $1 ns;";
    }

    # $display("string")
    if ($stmt =~ /^\$display\s*\((.+)\)\s*;?$/) {
        return translate_display($1);
    }
    # $write("string")
    if ($stmt =~ /^\$write\s*\((.+)\)\s*;?$/) {
        return translate_display($1);
    }
    # $finish
    if ($stmt =~ /^\$finish\s*(?:\(\s*\d*\s*\))?\s*;?$/) {
        return "std.env.finish;";
    }
    # $stop
    if ($stmt =~ /^\$stop\s*(?:\(\s*\d*\s*\))?\s*;?$/) {
        return "std.env.stop;";
    }
    # Blocking assignment
    if ($stmt =~ /^(\w+)\s*=\s*(.+);$/) {
        my ($lhs, $rhs) = ($1, $2);
        my $tr = translate_expression($rhs);
        $tr = convert_integer_for_type($tr, $lhs);
        return "$lhs := $tr;";
    }
    # Non-blocking assignment
    if ($stmt =~ /^(\w+)\s*<=\s*(.+);$/) {
        my ($lhs, $rhs) = ($1, $2);
        my $tr = translate_expression($rhs);
        $tr = convert_integer_for_type($tr, $lhs);
        return "$lhs <= $tr;";
    }
    # Default: comment out
    $stmt =~ s/\s+$//;
    return "-- FIXME: $stmt";
}

sub translate_display {
    my ($args) = @_;

    # Simple case: just a string literal
    if ($args =~ /^\s*"([^"]*)"\s*$/) {
        my $str = $1;
        # Strip Verilog escape sequences that VHDL report doesn't need
        $str =~ s/\\n$//;      # Trailing \n (report adds newline)
        $str =~ s/\\n/\n/g;    # Embedded \n
        $str =~ s/\\t/\t/g;    # Tab
        $str =~ s/\\"/"/g;     # Escaped quote
        $str =~ s/\\\\//g;     # Escaped backslash
        # Escape VHDL double quotes
        $str =~ s/"/""/g;
        return "report \"$str\" severity note;";
    }

    # String with format arguments - extract just the string for now
    if ($args =~ /^\s*"([^"]*)"/) {
        my $str = $1;
        $str =~ s/\\n$//;
        $str =~ s/\\n/\n/g;
        $str =~ s/\\t/\t/g;
        $str =~ s/\\"/"/g;
        $str =~ s/\\\\//g;
        $str =~ s/"/""/g;
        return "report \"$str\" severity note; -- FIXME: format args";
    }

    # No string literal - just a variable/expression
    return "-- FIXME: \$display($args)";
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
    
    if ($config->{logic3d}) {
        $header .= "library work;\n";
        $header .= "use work.logic3d_pkg.all;\n\n";
    } elsif ($lib eq 'ieee') {
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

    # Signal declarations will be inserted later

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
