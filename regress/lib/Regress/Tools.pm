package Regress::Tools;
#
# Tool discovery for the regression harness.
#
# Principle (per project direction): prefer the *build-area* version of every
# tool when it is present, falling back to whatever is installed on PATH.
# Each resolver honors an explicit environment override first.
#
use strict;
use warnings;
use Exporter 'import';

our @EXPORT_OK = qw(
    src_root nvc_bin nvc_libdir iverilog_bin vvp_bin verilator_bin
    run_regr_bin unit_test_bin shim_bin tool_versions
    iverilog_steve_bin vvp_steve_bin
    xyce_bin xyce_regr_dir xyce_regr_runner
    gnucap_bin ihp_pdk_dir gnucap2xyce_bin xyce_libdir pyms_dir
    ltz_bin ltz_tests_dir ltz_community_dir
);

# Root that holds the sibling source/build trees (nvc, nvc-build, iverilog, ...)
sub src_root { $ENV{SV2GHDL_SRC_ROOT} || '/usr/local/src' }

sub _first_exe { for (@_) { return $_ if defined $_ && length $_ && -x $_ } return undef }

sub _which {
    my $name = shift;
    for my $dir (split /:/, ($ENV{PATH} // '')) {
        my $p = "$dir/$name";
        return $p if -x $p;
    }
    return undef;
}

# nvc: $NVC override -> build tree (nvc-build/bin/nvc) -> PATH
sub nvc_bin {
    my $r = src_root();
    return _first_exe($ENV{NVC}, "$r/nvc-build/bin/nvc", _which('nvc'));
}

# nvc library dir holding std/ and (ideally) sv2vhdl/.
#   build tree:  nvc-build/lib            (has std/, sv2vhdl/ as siblings)
#   installed:   /usr/local/lib/nvc       (has nvc/sv2vhdl under it)
sub nvc_libdir {
    return $ENV{NVC_LIBDIR} if $ENV{NVC_LIBDIR};
    my $bin = nvc_bin() or return undef;
    require Cwd;
    $bin = Cwd::abs_path($bin);
    (my $prefix = $bin) =~ s{/bin/nvc$}{};
    return "$prefix/lib"      if -d "$prefix/lib/sv2vhdl" || -d "$prefix/lib/std";
    return "$prefix/lib/nvc"  if -d "$prefix/lib/nvc";
    return "$prefix/lib";
}

# iverilog: $IVERILOG -> build-area private install -> system PATH -> raw tree.
# The iverilog driver hard-codes its module dir at configure time and can't run
# from the raw build tree, so the "build area" is a private-prefix install
# (iverilog/_install, like iverilog-steve/_install) — preferred over the system
# copy so we test freshly-built code without installing to /usr/local.
sub iverilog_bin {
    my $r = src_root();
    return _first_exe($ENV{IVERILOG},
                      "$r/iverilog/_install/bin/iverilog",
                      _which('iverilog'),
                      "$r/iverilog/driver/iverilog");
}

sub vvp_bin {
    my $r = src_root();
    return _first_exe($ENV{VVP},
                      "$r/iverilog/_install/bin/vvp",
                      _which('vvp'),
                      "$r/iverilog/vvp/vvp");
}

# Upstream "Steve" iverilog, built to a private prefix so it doesn't clobber
# our installed fork. Used for A/B comparison against the same ivtest suite.
sub iverilog_steve_bin {
    my $r = src_root();
    return _first_exe($ENV{IVERILOG_STEVE}, "$r/iverilog-steve/_install/bin/iverilog");
}
sub vvp_steve_bin {
    my $r = src_root();
    return _first_exe($ENV{VVP_STEVE}, "$r/iverilog-steve/_install/bin/vvp");
}

# verilator is a system package here (no local source tree unless fetched).
sub verilator_bin {
    my $r = src_root();
    return _first_exe($ENV{VERILATOR},
                      "$r/verilator/bin/verilator",
                      _which('verilator'));
}

sub run_regr_bin {
    my $r = src_root();
    return _first_exe($ENV{RUN_REGR}, "$r/nvc-build/bin/run_regr");
}

sub unit_test_bin {
    my $r = src_root();
    return _first_exe($ENV{UNIT_TEST}, "$r/nvc-build/bin/unit_test");
}

# sv2ghdl shims live in the repo; prefer the repo copy over an installed one.
sub shim_bin {
    my $name = shift;            # e.g. 'iverilog-sv2ghdl'
    my $r = src_root();
    return _first_exe("$r/sv2ghdl/bin/$name", _which($name));
}

# Best-effort version strings for the run record.
sub tool_versions {
    my %v;
    my $nvc = nvc_bin();
    $v{nvc} = $nvc ? _run_ver("$nvc --version") : undef;
    my $iv  = iverilog_bin();
    $v{iverilog} = $iv ? _run_ver("$iv -V 2>&1") : undef;
    my $vl  = verilator_bin();
    $v{verilator} = $vl ? _run_ver("$vl --version 2>&1") : undef;
    return \%v;
}

sub _run_ver {
    my $cmd = shift;
    my $out = `$cmd 2>&1`;
    return undef unless defined $out;
    ($out) = split /\n/, $out;       # first line
    $out =~ s/^\s+|\s+$//g;
    return $out;
}

# Xyce: $XYCE override -> build tree (xyce-build/src/Xyce) -> PATH
sub xyce_bin {
    my $r = src_root();
    return _first_exe($ENV{XYCE}, "$r/xyce-build/src/Xyce", _which('Xyce'));
}

# Xyce_Regression checkout (TestScripts/run_xyce_regression + Netlists + OutputData)
sub xyce_regr_dir {
    my $r = src_root();
    for my $d ($ENV{XYCE_REGRESSION}, "$r/Xyce_Regression") {
        return $d if defined $d && length $d && -d "$d/TestScripts";
    }
    return undef;
}

sub xyce_regr_runner {
    my $d = xyce_regr_dir() or return undef;
    my $p = "$d/TestScripts/run_xyce_regression";
    return -f $p ? $p : undef;
}

# gnucap (Verilog-A capable build): $GNUCAP override -> PATH
sub gnucap_bin { return _first_exe($ENV{GNUCAP}, _which('gnucap')); }

# Dir holding Xyce's shared library, for LD_LIBRARY_PATH when running the
# build-area Xyce (libxyce.so lives next to the build-area binary).
sub xyce_libdir {
    my $r = src_root();
    for my $d ($ENV{XYCE_LIBDIR}, "$r/xyce-build/src") {
        return $d if defined $d && length $d && -d $d;
    }
    return undef;
}

# gnucap2xyce.pl converter ($GNUCAP2XYCE override -> xyce/utils).
sub gnucap2xyce_bin {
    my $r = src_root();
    for my $p ($ENV{GNUCAP2XYCE}, "$r/xyce/utils/gnucap2xyce.pl") {
        return $p if defined $p && length $p && -f $p;
    }
    return undef;
}

# Build-area PyMS tree (xyce/utils/PyMS). We pin Xyce's PYMS_DIR to this so the
# regression validates the BUILD-AREA PyMS, not whatever is installed under
# share/xyce/PyMS. $PYMS_DIR override wins.
sub pyms_dir {
    my $r = src_root();
    for my $d ($ENV{PYMS_DIR}, "$r/xyce/utils/PyMS") {
        return $d if defined $d && length $d && -d "$d/vae";
    }
    return undef;
}

# IHP-Open-PDK checkout (Verilog-A device models + their gnucap test decks)
sub ihp_pdk_dir {
    my $r = src_root();
    for my $d ($ENV{IHP_PDK}, "$r/IHP-Open-PDK") {
        return $d if defined $d && length $d && -d $d;
    }
    return undef;
}

# ltz: LTspice-compatible Xyce wrapper (the build-area tool under test).
sub ltz_bin {
    my $r = src_root();
    return _first_exe($ENV{LTZ}, "$r/ltz/bin/ltz", _which('ltz'));
}

# ltz's bundled LTspice circuit test corpus.
sub ltz_tests_dir {
    my $r = src_root();
    for my $d ($ENV{LTZ_TESTS}, "$r/ltz/tests/ltspice_circuits") {
        return $d if defined $d && length $d && -d $d;
    }
    return undef;
}

# Larger community LTspice corpus (fetch_tests.sh -> ../ltz-tests): several
# cloned repos of .asc schematics + .cir netlists.
sub ltz_community_dir {
    my $r = src_root();
    for my $d ($ENV{LTZ_TESTS_DIR}, "$r/ltz-tests") {
        return $d if defined $d && length $d && -d $d;
    }
    return undef;
}

1;
