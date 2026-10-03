package Regress::Adapter::Hazard3;
#
# hazard3 suite: the Verijit test case, verilator-hazard3-mandelbrot-testbench
# (https://github.com/verijit/verilator-hazard3-mandelbrot-testbench): a
# Hazard3 RISC-V SoC running sw/mandelbrot.c, a fixed-point Mandelbrot.
# Fixtures, scripts and documentation: tests/hazard3_mandelbrot (README.md).
#
# Portable blocks (params{sim} = verilator | iverilog | vamos). For each variant
# in tests/hazard3_mandelbrot/variants.json: gen_soc.py turns upstream's
# soc.tmpl.v into the variant's SoC (no hierarchical references, small RAMs, a
# TOHOST snooper on the data port); the engine compiles it with
# tb/tb_hazard3.v and upstream's SRAM and Hazard3 sources (the HAZARD3_FILES
# list of upstream's Makefile), with SIM and CYCLE_CAP defined, and runs it.
# PASS = the testbench's TOHOST lines equal the variant's golden.tohost (from a
# native build of the same mandelbrot.c) exactly and, when the manifest records
# ref_cycles, the cycle count equals it. The message records the cycles, the
# compile and run wall times and the simulated cycles per second. --filter
# takes comma-separated substrings of test (variant) names; one that selects
# no test of the block is an error (filter_check).
#
# Bench block (params{mode} = bench; opt-in): upstream's ORIGINAL 1024x1024
# benchmark under Verilator, built as upstream's Makefile builds main_verilator
# (the same verilator options and OPT_FAST; the C++ compiler from
# Regress::Tools::hazard3_bench_cxx), from upstream's committed firmware
# sw/bin/mandelbrot_rv32imac. PASS = it finishes, output.ppm's md5 equals the
# native golden's (variants.json "bench") and, when bench.ref_cycles is
# recorded, the cycle count equals it. The message records cycles, upstream's
# MCycles/s figure and the run's wall time.
#
# Upstream checkout: Regress::Tools::hazard3_mandelbrot_dir. When none is
# found the adapter runs tests/hazard3_mandelbrot/setup.sh, which clones it at
# the commits pinned in tests/hazard3_mandelbrot/UPSTREAM; a checkout at other
# commits is an error. Work dirs: <run workdir>/hazard3/<block>/<variant>, or
# $HAZARD3_WORKDIR/<run>/<block>/<variant> when HAZARD3_WORKDIR is set (a fast
# local disk helps the Verilator builds).
#
use strict;
use warnings;
use File::Path qw(make_path remove_tree);
use File::Basename qw(basename);
use JSON::PP ();
use Digest::MD5 ();
use Regress::Tools qw(verilator_bin iverilog_bin vvp_bin vamos_vcs_bin python3_bin
                      hazard3_suite_dir hazard3_mandelbrot_dir hazard3_bench_cxx);
use Regress::Util  qw(run_capture slurp now_ms);

sub _which {
    my $n = shift;
    for my $d (split /:/, ($ENV{PATH} // '')) { return "$d/$n" if length $d && -x "$d/$n" }
    return undef;
}

# Ready when the fixtures and python3 are here and the upstream checkout is
# present or can be cloned (git).
sub ready {
    my $s = hazard3_suite_dir() or return 0;
    return 0 unless -f "$s/variants.json" && -f "$s/gen_soc.py" && python3_bin();
    return 1 if hazard3_mandelbrot_dir();
    return _which('git') ? 1 : 0;
}

use constant BENCH_TEST => 'upstream_1024x1024';

# The test names of a block: the variants of variants.json, or the benchmark.
sub _test_names {
    my ($block, $m) = @_;
    return (BENCH_TEST) if ($block->{params}{mode} // '') eq 'bench';
    return map { $_->{name} } @{ ($m || {})->{variants} || [] };
}

# --filter: comma-separated substrings of test names
sub _select {
    my ($filter, @names) = @_;
    return @names unless defined $filter && length $filter;
    my @f = grep { length } split /,/, $filter;
    return grep { my $n = $_; grep { index($n, $_) >= 0 } @f } @names;
}

# undef when --filter selects a test of $block, else why it selects none (the
# check a harness can make before running; run() makes it again).
sub filter_check {
    my ($block, $filter) = @_;
    return undef unless defined $filter && length $filter;
    my $s = hazard3_suite_dir() or return undef;
    my $m = eval { JSON::PP->new->decode(slurp("$s/variants.json")) } or return undef;
    my @names = _test_names($block, $m);
    return undef if _select($filter, @names);
    return "--filter '$filter' selects no test of $block->{name} (tests: "
         . join(', ', @names) . ')';
}

sub run {
    my ($class, $block, %opt) = @_;
    my $suite = hazard3_suite_dir()
        or return _err($block, 'tests/hazard3_mandelbrot not found');
    my $py = python3_bin() or return _err($block, 'python3 not found');
    # a --filter that selects none of this block's tests is an error
    if (my $why = filter_check($block, $opt{filter})) {
        return _err($block, $why);
    }

    (my $bsafe = $block->{name}) =~ s{[/ ]}{_}g;
    my $work = _work_root(\%opt) . "/hazard3/$bsafe";
    make_path($work);
    my $blog = $opt{log};
    _blog($blog, "hazard3 block $block->{name}: work dir $work");

    my ($up, $uerr) = _upstream($suite, $work);
    return _err($block, $uerr) unless $up;
    my @files = _hazard3_files($up)
        or return _err($block, "no HAZARD3_FILES list in $up/Makefile");
    my $m = eval { JSON::PP->new->decode(slurp("$suite/variants.json")) }
        or return _err($block, "cannot read $suite/variants.json: $@");

    my $timeout = $opt{timeout} // $block->{params}{timeout};
    my $jobs = $opt{jobs} || 4;
    $jobs = 4 if $jobs > 4;
    my %ctx = (suite => $suite, up => $up, py => $py, files => \@files,
               work => $work, timeout => $timeout, jobs => $jobs, blog => $blog,
               env => $block->{env}, path_prepend => $block->{path_prepend},
               manifest => $m);

    if (($block->{params}{mode} // '') eq 'bench') {
        return { exit_code => 0, results => [ _run_bench(%ctx) ] };
    }

    my $sim = $block->{params}{sim} // '';
    my %tool = (verilator => verilator_bin(), iverilog => iverilog_bin(),
                vvp => vvp_bin(), vcs => vamos_vcs_bin());
    my @need = $sim eq 'verilator' ? ('verilator')
             : $sim eq 'iverilog'  ? ('iverilog', 'vvp')
             : $sim eq 'vamos'     ? ('vcs')
             : ();
    return _err($block, "unknown params{sim} '$sim'") unless @need;
    for my $t (@need) { return _err($block, "$t not found") unless $tool{$t} }
    $ctx{sim} = $sim;
    $ctx{tool} = \%tool;

    my %want = map { $_ => 1 } _select($opt{filter}, _test_names($block, $m));
    my @variants = grep { $want{ $_->{name} } } @{ $m->{variants} || [] };
    my @r = map { _run_variant($_, %ctx) } @variants;
    return { exit_code => 0, results => \@r };
}

# ---------------------------------------------------------------------------
# portable variants

sub _run_variant {
    my ($v, %c) = @_;
    my $name = $v->{name};
    my $vd = "$c{work}/$name";
    remove_tree($vd) if -d $vd;
    make_path($vd);
    my %run = (env => $c{env}, path_prepend => $c{path_prepend}, timeout => $c{timeout});

    my $soc = "$vd/soc_portable.v";
    my ($grc, $gout) = _step([$c{py}, "$c{suite}/gen_soc.py", '--template', "$c{up}/soc.tmpl.v",
                              '--variant', $name, '-o', $soc],
                             dir => $vd, log => "$vd/gen_soc.log", %run);
    return _res($c{blog}, $name, 'error', "gen_soc.py failed (exit $grc): " . _last_line($gout),
                "$vd/gen_soc.log") if $grc;

    # cycle cap: twice the reference when there is one, else the manifest's
    # worst-case bound
    my $cap = $v->{cycle_cap};
    if ($v->{ref_cycles}) {
        my $c2 = 2 * $v->{ref_cycles} + 100_000;
        $cap = $c2 if !$cap || $c2 < $cap;
    }
    my @srcs = ("$c{suite}/tb/tb_hazard3.v", $soc, map { "$c{up}/$_" } @{ $c{files} });
    my @inc  = ("$c{up}/Hazard3", "$c{up}/Hazard3/hdl");
    my $t = $c{tool};
    my ($ccmd, $rcmd);
    if ($c{sim} eq 'verilator') {
        $ccmd = [$t->{verilator}, '--binary', '--timing', '-j', $c{jobs}, '-O3',
                 '-DSIM', "-DCYCLE_CAP=$cap", '--timescale', '1ns/1ps', '-Wno-fatal',
                 '--top-module', 'tb', (map { "-I$_" } @inc), '--Mdir', "$vd/obj", @srcs];
        $rcmd = ["$vd/obj/Vtb"];
    } elsif ($c{sim} eq 'iverilog') {
        $ccmd = [$t->{iverilog}, '-g2012', '-DSIM', "-DCYCLE_CAP=$cap",
                 (map { "-I$_" } @inc), '-s', 'tb', '-o', "$vd/sim.vvp", @srcs];
        $rcmd = [$t->{vvp}, '-n', "$vd/sim.vvp"];
    } else {    # vamos: the vcs two-step flow, vcs then ./simv
        $ccmd = [$t->{vcs}, '-full64', '-sverilog', '-timescale=1ns/1ps',
                 '+define+SIM', "+define+CYCLE_CAP=$cap", (map { "+incdir+$_" } @inc),
                 '-top', 'tb', '-o', 'simv', @srcs];
        $rcmd = ['./simv'];
    }

    my ($crc, $cout, $csec) = _step($ccmd, dir => $vd, log => "$vd/compile.log", %run);
    # Verilator's precompiled headers are ~160 MB a variant and only serve the build
    unlink glob("$vd/obj/*.gch") if $c{sim} eq 'verilator';
    my $built = $c{sim} eq 'verilator' ? -x "$vd/obj/Vtb"
              : $c{sim} eq 'iverilog'  ? -f "$vd/sim.vvp"
              :                          -x "$vd/simv";
    if ($crc || !$built) {
        return _res($c{blog}, $name, 'fail',
                    sprintf('%s compile failed (exit %d%s): %s', $c{sim}, $crc,
                            ($crc == 124 ? ', timed out' : ''), _first_error($cout)),
                    "$vd/compile.log", int($csec * 1000));
    }
    my @notes;
    if ($c{sim} eq 'vamos') {
        my $nerr = () = $cout =~ /^\*\* Error:/mg;
        push @notes, "compile log has $nerr nvc '** Error' line(s)" if $nerr;
    }

    my ($rrc, $rout, $rsec) = _step($rcmd, dir => $vd, log => "$vd/run.log", %run);
    my $ms = int(($csec + $rsec) * 1000);
    (my $out = $rout) =~ s/\r//g;
    my @got = grep { /^TOHOST / } split /\n/, $out;
    my ($cycles, $words) = $out =~ /^HAZARD3 DONE cycles=(\d+) words=(\d+)/m;
    my ($capmsg) = $out =~ /^HAZARD3 FAIL (.*)$/m;
    my @exp = split /\n/, slurp("$c{suite}/variants/$name/golden.tohost");
    my $timing = sprintf('compile %.1fs, run %.2fs', $csec, $rsec);

    my $first_bad;
    my $n = @got > @exp ? scalar @got : scalar @exp;
    for my $i (0 .. $n - 1) {
        if (!defined $got[$i] || !defined $exp[$i] || $got[$i] ne $exp[$i]) { $first_bad = $i; last }
    }
    my ($status, $msg);
    if (defined $first_bad) {
        $status = 'fail';
        $msg = sprintf('TOHOST stream differs from golden at word %d (%s): got %s, expected %s; '
                       . '%d of %d words',
                       $first_bad, _word_role($first_bad, $v),
                       defined $got[$first_bad] ? "'$got[$first_bad]'" : 'nothing',
                       defined $exp[$first_bad] ? "'$exp[$first_bad]'" : 'nothing',
                       scalar @got, scalar @exp);
        $msg .= "; $capmsg" if $capmsg;
        $msg .= "; simulator exit $rrc" . ($rrc == 124 ? ' (timed out)' : '') if !defined $cycles;
    } elsif (!defined $cycles) {
        $status = 'fail';
        $msg = 'golden words all seen but no HAZARD3 DONE line'
             . ($rrc ? "; simulator exit $rrc" : '');
    } else {
        my $ref = $v->{ref_cycles};
        if ($ref && $cycles != $ref) {
            $status = 'fail';
            $msg = sprintf('TOHOST stream matches golden (%d words) but cycles=%d, reference %d',
                           scalar @got, $cycles, $ref);
        } else {
            $status = 'pass';
            $msg = sprintf('golden match (%d words); cycles=%d%s', scalar @got, $cycles,
                           $ref ? ' (= reference)' : ' (no reference recorded)');
        }
    }
    if (defined $cycles) {
        $msg .= sprintf('; %s; %s', $timing, _rate($cycles, $rsec));
    } else {
        $msg .= "; $timing";
    }
    $msg .= '; ' . join('; ', @notes) if @notes;
    return _res($c{blog}, $name, $status, $msg, "$vd/run.log", $ms);
}

# what word $i of the TOHOST stream is: header, pixel, checksum or DONE
sub _word_role {
    my ($i, $v) = @_;
    my $size = $v->{size} || (1 << ($v->{size_log2} // 0));
    my $np = $size * $size;
    return 'header SIZE' if $i == 0;
    return 'header MAX_ITERS' if $i == 1;
    if ($i < 2 + $np) {
        my $p = $i - 2;
        return sprintf('pixel %d: row %d, column %d', $p, int($p / $size), $p % $size);
    }
    return 'checksum' if $i == 2 + $np;
    return 'DONE marker' if $i == 3 + $np;
    return 'past the end of the stream';
}

# ---------------------------------------------------------------------------
# upstream's 1024x1024 benchmark (opt-in)

sub _run_bench {
    my (%c) = @_;
    my $name = BENCH_TEST;
    my $bd = "$c{work}/$name";
    remove_tree($bd) if -d $bd;
    make_path($bd);
    my %run = (env => $c{env}, path_prepend => $c{path_prepend}, timeout => $c{timeout});
    my $vl = verilator_bin() or return _res($c{blog}, $name, 'error', 'verilator not found');
    my ($cxx, $compiler) = hazard3_bench_cxx();
    return _res($c{blog}, $name, 'error', 'no C++ compiler (clang++-19, clang++ or g++)')
        unless $cxx;
    my $bench = $c{manifest}{bench} || {};
    my $t0 = now_ms();

    # 1. mandelbrot_10.v and its preload images from the committed firmware, as
    #    the Makefile's "load_elf.py sw/bin/mandelbrot_rv32imac mandelbrot_10.v"
    #    (elf2hex.py is a standard-library load_elf.py; like load_elf.py it
    #    names the images by replacing ".v" in the output path, so pass it the
    #    bare file name and run it in the work dir)
    my ($rc, $out) = _step([$c{py}, "$c{suite}/elf2hex.py", '--template', "$c{up}/soc.tmpl.v",
                            "$c{up}/sw/bin/mandelbrot_rv32imac", 'mandelbrot_10.v'],
                           dir => $bd, log => "$bd/elf2hex.log", %run);
    return _res($c{blog}, $name, 'error', 'elf2hex.py failed: ' . _last_line($out),
                "$bd/elf2hex.log") if $rc;

    # 2. verilate, as the Makefile's main_verilator target (obj_dir in the work dir)
    ($rc, $out) = _step([$vl, '--cc', '--exe', '-j', '1', '-O3', '--x-assign', 'fast',
                         '--x-initial', 'fast', '--no-assert', '--compiler', $compiler,
                         "$c{up}/main_verilator.cpp", "-I$c{up}/Hazard3/", "-I$c{up}/Hazard3/hdl/",
                         "$bd/mandelbrot_10.v", map { "$c{up}/$_" } @{ $c{files} }],
                        dir => $bd, log => "$bd/verilate.log", %run);
    return _res($c{blog}, $name, 'fail', "verilator failed (exit $rc): " . _first_error($out),
                "$bd/verilate.log") if $rc;

    # 3. (cd obj_dir; make OPT_FAST="-O3 -march=native --std=c++20" -f Vmandelbrot_10.mk),
    #    with the C++ compiler named explicitly (verilated.mk's default may be g++)
    my $make = _gnu_make() || 'make';
    ($rc, $out) = _step([$make, "-j$c{jobs}", 'OPT_FAST=-O3 -march=native --std=c++20',
                         "CXX=$cxx", "LINK=$cxx", '-f', 'Vmandelbrot_10.mk'],
                        dir => "$bd/obj_dir", log => "$bd/build.log", %run);
    return _res($c{blog}, $name, 'fail', "C++ build failed (exit $rc): " . _first_error($out),
                "$bd/build.log") if $rc;
    my $build_s = (now_ms() - $t0) / 1000;

    # 4. run (writes output.ppm in the work dir)
    my ($rrc, $rout, $rsec) = _step(["$bd/obj_dir/Vmandelbrot_10"], dir => $bd,
                                    log => "$bd/run.log", %run);
    my ($cycles) = $rout =~ /^Finished at cycle (\d+)\./m;
    my ($mcps)   = $rout =~ /^Ran with ([0-9.eE+-]+) MCycles\/s\./m;
    my $ppm = "$bd/output.ppm";
    my $md5;
    if (-f $ppm && open my $fh, '<:raw', $ppm) {
        $md5 = Digest::MD5->new->addfile($fh)->hexdigest;
        close $fh;
    }
    my $cxxname = basename($cxx);
    my $how = "verilator --compiler $compiler, $cxxname, OPT_FAST -O3 -march=native";
    my ($status, $msg);
    if ($rrc || !defined $cycles) {
        $status = 'fail';
        $msg = "benchmark did not finish (exit $rrc" . ($rrc == 124 ? ', timed out' : '') . ')';
    } elsif (!defined $md5) {
        $status = 'fail';
        $msg = "finished at cycle $cycles but wrote no output.ppm";
    } elsif ($bench->{ppm_md5} && $md5 ne $bench->{ppm_md5}) {
        $status = 'fail';
        $msg = "cycles=$cycles but output.ppm md5 $md5 != native golden $bench->{ppm_md5}";
    } elsif ($bench->{ref_cycles} && $cycles != $bench->{ref_cycles}) {
        $status = 'fail';
        $msg = "output.ppm md5 $md5 (= native golden) but cycles=$cycles, "
             . "reference $bench->{ref_cycles}";
    } else {
        $status = 'pass';
        $msg = "cycles=$cycles"
             . ($bench->{ref_cycles} ? ' (= reference)' : ' (no reference recorded)')
             . "; output.ppm md5 $md5"
             . ($bench->{ppm_md5} ? ' (= native golden)' : ' (no golden recorded)');
    }
    if (defined $cycles) {
        $msg .= sprintf('; %s MCycles/s (upstream harness), run %.1fs wall = %s',
                        $mcps // '?', $rsec, _rate($cycles, $rsec));
    }
    $msg .= sprintf('; build %.0fs; %s', $build_s, $how);
    return _res($c{blog}, $name, $status, $msg, "$bd/run.log", int($rsec * 1000));
}

# ---------------------------------------------------------------------------
# helpers

sub _work_root {
    my $opt = shift;
    my $wd = $opt->{workdir} || '.';
    return $wd unless $ENV{HAZARD3_WORKDIR};
    return "$ENV{HAZARD3_WORKDIR}/" . basename($wd);
}

# the upstream checkout: find it, else set it up; then check the pinned commits
sub _upstream {
    my ($suite, $work) = @_;
    my $d = hazard3_mandelbrot_dir();
    unless ($d) {
        my ($rc, $out) = run_capture(['sh', "$suite/setup.sh"], log => "$work/setup.log");
        $d = hazard3_mandelbrot_dir();
        return (undef, "upstream checkout not found and setup.sh failed (exit $rc): "
                       . _last_line($out) . " (log $work/setup.log)") unless $d;
    }
    my $pins = _pins($suite);
    if (-e "$d/.git") {
        my $head = _git_head($d) // '';
        if ($pins->{UPSTREAM_COMMIT} && $head ne $pins->{UPSTREAM_COMMIT}) {
            return (undef, "$d is at '$head', not the pinned $pins->{UPSTREAM_COMMIT} "
                           . "(see tests/hazard3_mandelbrot/setup.sh)");
        }
        my $h3 = _git_head("$d/Hazard3") // '';
        if ($pins->{HAZARD3_COMMIT} && $h3 ne $pins->{HAZARD3_COMMIT}) {
            return (undef, "$d/Hazard3 is at '$h3', not the pinned $pins->{HAZARD3_COMMIT} "
                           . "(git -C $d submodule update --init --depth 1 Hazard3)");
        }
    }
    return ($d, undef);
}

sub _git_head {
    my $d = shift;
    open(my $ph, '-|', 'git', '-C', $d, 'rev-parse', 'HEAD') or return undef;
    my $h = <$ph>;
    close $ph;
    return undef unless defined $h;
    chomp $h;
    return $h;
}

sub _pins {
    my $suite = shift;
    my %p;
    open my $fh, '<', "$suite/UPSTREAM" or return \%p;
    while (my $l = <$fh>) {
        next if $l =~ /^\s*(?:#|$)/;
        $p{$1} = $2 if $l =~ /^\s*(\w+)\s*=\s*(\S+)/;
    }
    close $fh;
    return \%p;
}

# HAZARD3_FILES of upstream's Makefile: the SRAM models and the Hazard3 core
sub _hazard3_files {
    my $up = shift;
    my $mk = slurp("$up/Makefile");
    my ($blk) = $mk =~ /^HAZARD3_FILES\s*:=\s*\\\n((?:[ \t]+\S+[ \t]*\\?\n)+)/m;
    return () unless defined $blk;
    return grep { length && $_ ne '\\' } split /\s+/, $blk;
}

# GNU make, for the builds the steps start (verilator --binary runs $MAKE)
my $GNU_MAKE;
sub _gnu_make {
    return $GNU_MAKE if defined $GNU_MAKE;
    for my $c (_which('gmake'), '/usr/bin/make', _which('make')) {
        next unless defined $c && -x $c;
        open(my $ph, '-|', $c, '--version') or next;
        my $v = do { local $/; <$ph> } // '';
        close $ph;
        return $GNU_MAKE = $c if $v =~ /GNU Make/;
    }
    return $GNU_MAKE = '';
}

# run a command with an optional timeout (coreutils timeout; exit 124 when it
# fires); returns (exit, output, seconds).
# Every step gets a clean make environment: under `regress run` the blocks run
# inside the smak dispatcher, which exports MAKE=smak plus its job-server
# variables (and GNU make exports MAKEFLAGS). A build started from a step
# (verilator --binary runs $MAKE) must not join the dispatcher's job server: a
# recursive smak there waits forever. So drop those variables and point MAKE
# at GNU make.
sub _step {
    my ($cmd, %o) = @_;
    my @c = @$cmd;
    if ($o{timeout} && (my $to = _which('timeout'))) {
        @c = ($to, '-k', '10', $o{timeout}, @c);
    }
    my @unset = grep { /^(?:SMAK_\w+|MAKE|MAKEFLAGS|MFLAGS|MAKELEVEL|MAKEOVERRIDES|GNUMAKEFLAGS|MAKE_TERMOUT|MAKE_TERMERR)$/ }
                keys %ENV;
    my %env = %{ $o{env} || {} };
    $env{MAKE} = _gnu_make() if _gnu_make();
    my $t0 = now_ms();
    my ($rc, $out) = run_capture(\@c, dir => $o{dir}, env => \%env, unset => \@unset,
                                 path_prepend => $o{path_prepend}, log => $o{log});
    return ($rc, $out, (now_ms() - $t0) / 1000);
}

sub _rate {
    my ($cycles, $secs) = @_;
    return 'cycles/s n/a' unless $secs && $secs > 0;
    my $r = $cycles / $secs;
    return $r >= 1e6 ? sprintf('%.2f Mcycles/s', $r / 1e6)
         : $r >= 1e3 ? sprintf('%.2f kcycles/s', $r / 1e3)
         :             sprintf('%.0f cycles/s', $r);
}

sub _first_error {
    my $out = shift // '';
    for my $l (split /\n/, $out) {
        return substr($l, 0, 300) if $l =~ /error|fatal|cannot|not found/i;
    }
    return _last_line($out);
}

sub _last_line {
    my $out = shift // '';
    my @l = grep { /\S/ } split /\n/, $out;
    return @l ? substr($l[-1], 0, 300) : '(no output)';
}

sub _blog {
    my ($log, $text) = @_;
    return unless $log;
    open my $fh, '>>', $log or return;
    print $fh "$text\n";
    close $fh;
}

sub _res {
    my ($blog, $name, $status, $msg, $log, $ms) = @_;
    _blog($blog, sprintf('%-20s %-5s %s%s', $name, uc $status, $msg, $log ? "  [$log]" : ''));
    return { test_name => $name, status => $status, message => $msg,
             log_path => $log, (defined $ms ? (duration_ms => $ms) : ()) };
}

sub _err {
    my ($block, $msg) = @_;
    return { exit_code => 2, results => [
        { test_name => $block->{name}, status => 'error', message => $msg } ] };
}

1;
