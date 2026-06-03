package Regress::Block;
#
# The (suite x engine) block registry — the test matrix — plus engine
# environment profiles and dispatch to the right adapter.
#
# An "engine" is an environment profile: which simulator binaries the suite
# should use and which dir to prepend to PATH (shims, build-area bins). Tool
# paths are resolved build-area-first by Regress::Tools.
#
use strict;
use warnings;
use Regress::Tools qw(
    src_root nvc_bin nvc_libdir iverilog_bin vvp_bin verilator_bin
    run_regr_bin unit_test_bin shim_bin iverilog_steve_bin vvp_steve_bin);

use Regress::Adapter::Ivtest;
use Regress::Adapter::NvcNative;
use Regress::Adapter::SvTests;
use Regress::Adapter::Rtlmeter;

my %ADAPTER = (
    ivtest    => 'Regress::Adapter::Ivtest',
    nvc       => 'Regress::Adapter::NvcNative',
    'sv-tests'=> 'Regress::Adapter::SvTests',
    rtlmeter  => 'Regress::Adapter::Rtlmeter',
);

# ---- engines -------------------------------------------------------------
# Each returns { env => {...}, path_prepend => '...' }.

# Dir prepended to PATH for every engine: repo shims + nvc build-area bins.
sub _base_path {
    join(':', src_root() . '/sv2ghdl/bin', src_root() . '/nvc-build/bin');
}

sub _nvc_env {
    my %e;
    my $n = nvc_bin();      $e{NVC} = $n if $n;
    my $l = nvc_libdir();   $e{NVC_LIBDIR} = $l if $l;
    my $iv = iverilog_bin();$e{IVERILOG} = $iv if $iv;
    my $vp = vvp_bin();     $e{VVP} = $vp if $vp;
    return \%e;
}

my %ENGINES = (
    # native Icarus: rely on iverilog/vvp resolved on PATH (installed) — set
    # IVERILOG/VVP too so adapters that honor them pick the build-area copy.
    iverilog => sub {
        my %e;
        my $iv = iverilog_bin(); $e{IVERILOG} = $iv if $iv;
        my $vp = vvp_bin();      $e{VVP} = $vp if $vp;
        return { env => \%e, path_prepend => _base_path() };
    },
    # native nvc (VHDL) — needs nvc + iverilog (the vhdl_nvc runner compiles
    # the Verilog stage with iverilog).
    nvc => sub { { env => _nvc_env(), path_prepend => _base_path() } },
    # nvc via the iverilog shim (vvp_reg.pl --suffix=-sv2ghdl).
    'nvc-iv-shim' => sub { { env => _nvc_env(), path_prepend => _base_path() } },
    # upstream "Steve" iverilog from its private prefix — put its bin first on
    # PATH so the ivtest runner's bare `iverilog`/`vvp` resolve to it.
    'iverilog-steve' => sub {
        my %e;
        my $iv = iverilog_steve_bin(); $e{IVERILOG} = $iv if $iv;
        my $vp = vvp_steve_bin();      $e{VVP} = $vp if $vp;
        (my $bindir = $iv // '') =~ s{/iverilog$}{};
        return { env => \%e,
                 path_prepend => join(':', grep { length } $bindir, _base_path()) };
    },
    # native verilator (system tool).
    verilator => sub {
        my %e; my $v = verilator_bin(); $e{VERILATOR} = $v if $v;
        return { env => \%e, path_prepend => _base_path() };
    },
    # nvc via the verilator shim: put verilator-sv2ghdl first as `verilator`.
    'nvc-vl-shim' => sub {
        my $e = _nvc_env();
        my $shim = shim_bin('verilator-sv2ghdl');
        $e->{VERILATOR} = $shim if $shim;
        return { env => $e, path_prepend => _base_path() };
    },
);

# ---- block registry ------------------------------------------------------

my @BLOCKS = (
    { name => 'ivtest/iverilog',     suite => 'ivtest', engine => 'iverilog',
      params => { runner => 'vvp_reg.pl', suffix => '' },
      ready  => sub { iverilog_bin() ? 1 : 0 } },

    { name => 'ivtest/iverilog-nvc', suite => 'ivtest', engine => 'nvc-iv-shim',
      params => { runner => 'vvp_reg.pl', suffix => '-sv2ghdl', timeout => 60 },
      ready  => sub { nvc_bin() && iverilog_bin() && shim_bin('iverilog-sv2ghdl') ? 1 : 0 } },

    { name => 'ivtest/nvc-vhdl',     suite => 'ivtest', engine => 'nvc',
      params => { runner => 'vhdl_nvc_reg.pl', suffix => '', timeout => 60 },
      ready  => sub { nvc_bin() && iverilog_bin() ? 1 : 0 } },

    # upstream Icarus run against OUR ivtest suite (A/B vs ivtest/iverilog)
    { name => 'ivtest/iverilog-steve', suite => 'ivtest', engine => 'iverilog-steve',
      params => { runner => 'vvp_reg.pl', suffix => '' },
      ready  => sub { iverilog_steve_bin() ? 1 : 0 } },

    { name => 'nvc/regr',            suite => 'nvc', engine => 'nvc',
      params => { mode => 'regr' },
      ready  => sub { run_regr_bin() ? 1 : 0 } },

    { name => 'nvc/unit',            suite => 'nvc', engine => 'nvc',
      params => { mode => 'unit' },
      ready  => sub { unit_test_bin() ? 1 : 0 } },

    { name => 'sv-tests/verilator',  suite => 'sv-tests', engine => 'verilator',
      params => { runner => 'Verilator' },
      ready  => sub { verilator_bin() ? 1 : 0 } },

    { name => 'sv-tests/iverilog',   suite => 'sv-tests', engine => 'iverilog',
      params => { runner => 'Icarus' },
      ready  => sub { iverilog_bin() ? 1 : 0 } },

    { name => 'rtlmeter/verilator',  suite => 'rtlmeter', engine => 'verilator',
      params => { sim => 'verilator' },
      ready  => sub { Regress::Adapter::Rtlmeter::ready() && verilator_bin() ? 1 : 0 } },

    { name => 'rtlmeter/verilator-nvc', suite => 'rtlmeter', engine => 'nvc-vl-shim',
      params => { sim => 'verilator', shim => 1 },
      ready  => sub { Regress::Adapter::Rtlmeter::ready() && nvc_bin()
                       && shim_bin('verilator-sv2ghdl') ? 1 : 0 } },
);

sub all_blocks { @BLOCKS }

sub names { map { $_->{name} } @BLOCKS }

sub get {
    my $name = shift;
    for (@BLOCKS) { return $_ if $_->{name} eq $name }
    return undef;
}

sub is_ready { my $b = shift; $b->{ready} ? $b->{ready}->() : 1 }

# Run one block: merge engine profile, dispatch to its adapter, return
# { exit_code => N, results => [...] }.
sub dispatch {
    my ($block, %opt) = @_;
    my $eng = $ENGINES{ $block->{engine} }
        or die "unknown engine '$block->{engine}' for block $block->{name}\n";
    my $prof = $eng->();
    local $block->{env}          = $prof->{env};
    local $block->{path_prepend} = $prof->{path_prepend};

    my $adapter = $ADAPTER{ $block->{suite} }
        or die "no adapter for suite '$block->{suite}'\n";
    return $adapter->run($block, %opt);
}

1;
