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
    run_regr_bin unit_test_bin shim_bin iverilog_steve_bin vvp_steve_bin
    xyce_bin xyce_regr_runner gnucap_bin ihp_pdk_dir gnucap2xyce_bin
    cadence2xyce_bin adms_examples_dir
    ltz_bin ltz_tests_dir ltz_community_dir
    qspice_qux_bin qspice2xyce_bin qspice_tests_dir
    vamos_vcs_bin hazard3_bench_cxx);

use Regress::Adapter::Ivtest;
use Regress::Adapter::NvcNative;
use Regress::Adapter::SvTests;
use Regress::Adapter::Rtlmeter;
use Regress::Adapter::Xyce;
use Regress::Adapter::XyceIHP;
use Regress::Adapter::XycePyMS;
use Regress::Adapter::Ltz;
use Regress::Adapter::Qspice;
use Regress::Adapter::Hazard3;

my %ADAPTER = (
    ivtest      => 'Regress::Adapter::Ivtest',
    nvc         => 'Regress::Adapter::NvcNative',
    'sv-tests'  => 'Regress::Adapter::SvTests',
    rtlmeter    => 'Regress::Adapter::Rtlmeter',
    xyce        => 'Regress::Adapter::Xyce',
    'xyce-ihp'  => 'Regress::Adapter::XyceIHP',
    'xyce-pyms' => 'Regress::Adapter::XycePyMS',
    ltz         => 'Regress::Adapter::Ltz',
    qspice      => 'Regress::Adapter::Qspice',
    hazard3     => 'Regress::Adapter::Hazard3',
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
    # native nvc with --accel: same env profile as nvc; the accel toggle is
    # applied per-run by NvcNative via NVC_ACCEL (not the engine env).
    'nvc-accel' => sub { { env => _nvc_env(), path_prepend => _base_path() } },
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
        # sv-tests' runner and verilated.mk invoke bare `verilator`/`ccache`
        # by PATH lookup: put the build-area bin dir first, and drop the
        # OBJCACHE default when no ccache is installed (else Error 127).
        (my $vdir = $v // '') =~ s{/verilator$}{};
        $e{OBJCACHE} = '' unless Regress::Tools::_which('ccache');
        return { env => \%e,
                 path_prepend => join(':', grep { length } $vdir, _base_path()) };
    },
    # nvc via the verilator shim: put verilator-sv2ghdl first as `verilator`.
    # rtlmeter invokes literal `verilator` by PATH lookup (it does NOT consult
    # $VERILATOR), so prepend the as-verilator/ wrapper dir where the shim is
    # presented under that name.
    'nvc-vl-shim' => sub {
        my $e = _nvc_env();
        my $shim = shim_bin('verilator-sv2ghdl');
        $e->{VERILATOR} = $shim if $shim;
        (my $asdir = $shim // '') =~ s{/verilator-sv2ghdl$}{/as-verilator};
        $asdir = '' unless -d $asdir;
        # NB: _base_path() is a colon-joined STRING — never -d filter it.
        return { env => $e,
                 path_prepend => join(':', grep { length } $asdir, _base_path()) };
    },
    # native Xyce (analog / SPICE) — point XYCE at the build-area binary.
    xyce => sub {
        my %e; my $x = xyce_bin(); $e{XYCE} = $x if $x;
        return { env => \%e, path_prepend => _base_path() };
    },
    # ltz (LTspice->Xyce wrapper) — XYCE at the build-area binary; the adapter
    # also pins LD_LIBRARY_PATH for libxyce.so.
    ltz => sub {
        my %e; my $x = xyce_bin(); $e{XYCE} = $x if $x;
        return { env => \%e, path_prepend => _base_path() };
    },
    # qspice (QSPICE->Xyce clone) — Xyce at the build-area binary; the adapter
    # pins LD_LIBRARY_PATH itself and reaches the Windows QSPICE via interop.
    qspice => sub {
        my %e; my $x = xyce_bin(); $e{XYCE} = $x if $x;
        return { env => \%e, path_prepend => _base_path() };
    },
    # vamos (VCS command lines on sv2ghdl + nvc): the nvc env profile, with
    # vamos pinned (VAMOS_NVC/VAMOS_IVERILOG) to the same build-area nvc and
    # iverilog the other engines resolve.
    vamos => sub {
        my $e = _nvc_env();
        $e->{VAMOS_NVC}      = $e->{NVC}      if $e->{NVC};
        $e->{VAMOS_IVERILOG} = $e->{IVERILOG} if $e->{IVERILOG};
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

    # Same VHDL suite, but every test that passes normal simulation is retried
    # with nvc --accel (via NVC_ACCEL in the env); accel results land here,
    # separate from nvc/regr, so accel-only regressions are visible.
    { name => 'nvc/regr-accel',      suite => 'nvc', engine => 'nvc-accel',
      params => { mode => 'regr', accel => 1 },
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
      params => { cases => 'VeeR-EH1:default:hello' },
      ready  => sub { Regress::Adapter::Rtlmeter::ready() && verilator_bin() ? 1 : 0 } },

    { name => 'rtlmeter/verilator-nvc', suite => 'rtlmeter', engine => 'nvc-vl-shim',
      params => { cases => 'VeeR-EH1:default:hello VeeR-EH2:default:hello', shim => 1 },
      ready  => sub { Regress::Adapter::Rtlmeter::ready() && nvc_bin()
                       && shim_bin('verilator-sv2ghdl') ? 1 : 0 } },

    # Xyce_Regression via its native run_xyce_regression (no cmake/ctest).
    { name => 'xyce/regr',           suite => 'xyce', engine => 'xyce',
      params => { tags => '+serial+nightly' },
      ready  => sub { xyce_bin() && xyce_regr_runner() ? 1 : 0 } },

    # IHP-Open-PDK device tests run THROUGH Xyce (testing Xyce/PyMS, not
    # gnucap): each gnucap-stats .gc deck is converted with gnucap2xyce.pl,
    # run in Xyce, and its .prn diffed against the gnucap golden ref/*.gc.out.
    # --filter selects device dirs (resistor,capacitor,moslv,moshv).
    { name => 'xyce/ihp-pdk',        suite => 'xyce-ihp', engine => 'xyce',
      params => {},  # set fail_rtol to gate on gross divergence
      ready  => sub { xyce_bin() && gnucap2xyce_bin() && ihp_pdk_dir() ? 1 : 0 } },

    # ADMS-example Verilog-A decks run through Xyce via PyMS (the ADMS
    # replacement): each .sp deck (carrying a .hdl directive) is preprocessed
    # with cadence2xyce.pl and run with PYMS_DIR set, so PyMS JIT-compiles the
    # Verilog-A at runtime. PASS = clean convert + run (device registers and
    # instantiates). --filter selects model-family dirs (e.g. psp103,BSIM6.1.1).
    { name => 'xyce/pyms',           suite => 'xyce-pyms', engine => 'xyce',
      params => {},
      ready  => sub { xyce_bin() && cadence2xyce_bin() && adms_examples_dir() ? 1 : 0 } },

    # ltz LTspice circuits run through ltz -> Xyce. Pass = clean sim (no LTspice
    # gold here; LTspice runs under Wine but isn't installed). Build-area Xyce.
    { name => 'ltz/circuits',        suite => 'ltz', engine => 'ltz',
      params => {},
      ready  => sub { ltz_bin() && ltz_tests_dir() && xyce_bin() ? 1 : 0 } },

    # Larger community LTspice corpus (../ltz-tests): .asc + .cir across several
    # cloned repos, walked recursively. --filter selects a repo name.
    { name => 'ltz/community',       suite => 'ltz', engine => 'ltz',
      params => { corpus => 'community' },
      ready  => sub { ltz_bin() && ltz_community_dir() && xyce_bin() ? 1 : 0 } },

    # QSPICE schematics: QUX -Netlist -> qspice2xyce.pl -> Xyce, compared
    # against QSPICE64's own rawfile. Needs the Windows QSPICE install
    # (reached via WSL interop) and the qspice-tests corpus.
    { name => 'qspice/circuits',     suite => 'qspice', engine => 'qspice',
      params => {},
      ready  => sub { qspice_qux_bin() && qspice2xyce_bin()
                      && qspice_tests_dir() && xyce_bin() ? 1 : 0 } },

    # hazard3: the Verijit test case (github.com/verijit/
    # verilator-hazard3-mandelbrot-testbench), a Hazard3 RISC-V SoC running a
    # fixed-point Mandelbrot. Each block runs every variant of
    # tests/hazard3_mandelbrot on the same Verilog testbench and generated SoC;
    # PASS = the TOHOST stream equals the native golden exactly (and the cycle
    # count equals the recorded reference). One test per variant; --filter
    # takes comma-separated substrings of variant names. The upstream checkout
    # is set up on first use (tests/hazard3_mandelbrot/setup.sh).
    { name => 'hazard3/verilator',   suite => 'hazard3', engine => 'verilator',
      params => { sim => 'verilator', timeout => 1800 },
      ready  => sub { verilator_bin() && Regress::Adapter::Hazard3::ready() ? 1 : 0 } },

    { name => 'hazard3/iverilog',    suite => 'hazard3', engine => 'iverilog',
      params => { sim => 'iverilog', timeout => 1800 },
      ready  => sub { iverilog_bin() && vvp_bin()
                      && Regress::Adapter::Hazard3::ready() ? 1 : 0 } },

    # vamos's vcs personality (vcs, then ./simv) through sv2ghdl/shims/vcs.
    { name => 'hazard3/vamos',       suite => 'hazard3', engine => 'vamos',
      params => { sim => 'vamos', timeout => 3600 },
      ready  => sub { vamos_vcs_bin() && nvc_bin() && iverilog_bin()
                      && Regress::Adapter::Hazard3::ready() ? 1 : 0 } },

    # OPT-IN (runs only when named): upstream's own 1024x1024 benchmark, built
    # with Verilator as upstream's Makefile builds it (-O3, -march=native),
    # from upstream's committed firmware. Records cycles, MCycles/s and the
    # output.ppm md5, which must equal the native golden's. 4.6 G cycles,
    # ~21 min at Verilator's ~3.6 MCycles/s.
    { name => 'hazard3/bench-verilator', suite => 'hazard3', engine => 'verilator',
      optin  => 1,
      params => { mode => 'bench', timeout => 14400 },
      ready  => sub { verilator_bin() && (hazard3_bench_cxx())[0]
                      && Regress::Adapter::Hazard3::ready() ? 1 : 0 } },
);

sub all_blocks { @BLOCKS }

sub names { map { $_->{name} } @BLOCKS }

# The blocks `regress run` runs when none are named: every block except the
# opt-in ones (optin => 1), which run only when named on the command line.
sub default_names { map { $_->{name} } grep { !$_->{optin} } @BLOCKS }

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
