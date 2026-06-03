package Regress::Gate;
#
# CI gate: validate a candidate ref of a simulator repo and fast-forward
# origin/main if it introduces no regressions.
#
# Pipeline (each step recorded in the gate_job row so the dashboard can show
# progress): fetch origin -> resolve+checkout the candidate ref in the
# build-area repo -> build (build-area, per-repo recipe) -> run that repo's
# regressions through ./regress -> detect pass->fail regressions vs the
# previous run -> if clean and push requested, `git push origin <sha>:main`
# (no force, so a non-fast-forward is refused).
#
use strict;
use warnings;
use Regress::Tools qw(src_root);
use Regress::Util  qw(run_capture);
use Regress::DB;

# which blocks to run when gating each repo (everything that exercises it)
our %REPO_BLOCKS = (
    iverilog => [qw(ivtest/iverilog sv-tests/iverilog ivtest/nvc-vhdl ivtest/iverilog-nvc)],
    nvc      => [qw(nvc/regr nvc/unit ivtest/nvc-vhdl ivtest/iverilog-nvc)],
);

sub repo_dir { src_root() . '/' . $_[0] }

# run the gate. opts: db, gate_id, repo, ref, target, push, jobs, regress_bin,
# logdir, no_build, no_fetch, filter, push_remote, push_branch.
sub run {
    my (%o) = @_;
    my $db   = Regress::DB->new(path => $o{db});     # writable, own connection
    my $gid  = $o{gate_id};
    my $repo = $o{repo} or return _fail($db, $gid, "no repo");
    my $dir  = repo_dir($repo);
    return _fail($db, $gid, "no repo dir $dir") unless -d "$dir/.git";
    my $ref  = $o{ref} // 'HEAD';
    my $jobs = $o{jobs} || 16;
    my $logdir = $o{logdir} || "/tmp";
    my $blocks = $REPO_BLOCKS{ $o{target} // $repo } || $REPO_BLOCKS{$repo}
        or return _fail($db, $gid, "no blocks for target/repo $repo");

    # 1. fetch + resolve candidate sha
    $db->update_gate($gid, state => 'fetching');
    unless ($o{no_fetch}) {
        my ($rc) = run_capture(['git', '-C', $dir, 'fetch', 'origin', '--quiet'],
                               log => "$logdir/gate-fetch.log");
        return _fail($db, $gid, "git fetch failed") if $rc != 0;
    }
    my ($rc, $sha) = run_capture(['git', '-C', $dir, 'rev-parse', $ref],
                                 log => "$logdir/gate-rev.log");
    chomp $sha;
    return _fail($db, $gid, "cannot resolve ref '$ref'") if $rc != 0 || $sha !~ /^[0-9a-f]{7,}/;
    $db->update_gate($gid, sha => $sha);

    # 2. checkout candidate (detached) in the build-area repo
    ($rc) = run_capture(['git', '-C', $dir, 'checkout', '--detach', '--force', $sha],
                        log => "$logdir/gate-checkout.log");
    return _fail($db, $gid, "checkout $sha failed") if $rc != 0;

    # 3. build the build-area
    unless ($o{no_build}) {
        $db->update_gate($gid, state => 'building');
        my ($ok, $msg) = build_repo($repo, $jobs, $logdir);
        return _fail($db, $gid, "build failed: $msg") unless $ok;
    }

    # 4. run the repo's regressions through ./regress
    $db->update_gate($gid, state => 'testing');
    my $notes = "gate #$gid $repo\@" . substr($sha, 0, 12);
    my @cmd = ($o{regress_bin}, 'run', @$blocks, '--seq', '--db', $o{db}, '--notes', $notes);
    push @cmd, ('--filter', $o{filter}) if defined $o{filter} && length $o{filter};
    my ($trc, $tout) = run_capture(\@cmd, log => "$logdir/gate-regress.log");
    my ($run_id) = $tout =~ /Run #(\d+)/;
    $run_id //= $db->latest_run_id;
    $db->update_gate($gid, run_id => $run_id);

    # 5. detect regressions vs the repo's main baseline (not whatever ran last)
    my $base = $db->get_baseline($repo);
    my $base_run = $base ? $base->{run_id} : undef;
    $db->update_gate($gid, baseline_run_id => $base_run) if $base_run;
    my $regr = $base_run ? $db->regressions_vs_baseline($run_id, $base_run) : [];
    my $clean = (@$regr == 0) ? 1 : 0;
    $db->update_gate($gid, clean => $clean);
    my $base_note = $base_run ? "vs baseline run #$base_run" : "no main baseline set for $repo";

    # 6. fast-forward origin/main if clean and requested
    my $pushed = 0;
    my $summary;
    if (!$clean) {
        $summary = scalar(@$regr) . " regression(s) $base_note: "
                 . join(', ', map { "$_->{block}:$_->{test} ($_->{was}->$_->{now})" }
                                  @$regr[0 .. ($#$regr < 5 ? $#$regr : 5)]);
    } elsif ($o{push}) {
        $db->update_gate($gid, state => 'pushing');
        my $remote = $o{push_remote} || 'origin';
        my $branch = $o{push_branch} || 'main';
        my ($prc, $pout) = run_capture(
            ['git', '-C', $dir, 'push', $remote, "$sha:refs/heads/$branch"],
            log => "$logdir/gate-push.log");
        if ($prc == 0) {
            $pushed = 1;
            $summary = "clean ($base_note); fast-forwarded $remote/$branch to " . substr($sha, 0, 12);
            run_capture(['git', '-C', $dir, 'checkout', '--force', $branch], log => "$logdir/gate-co-main.log");
            # main now == this candidate -> it becomes the new baseline for $repo
            $db->set_baseline($repo, $run_id, $sha, "gate #$gid FF $remote/$branch");
        } else {
            $summary = "clean ($base_note) but push to $remote/$branch refused (non-fast-forward?) — not pushed";
        }
    } else {
        $summary = "clean ($base_note; push not requested)";
    }
    $db->update_gate($gid, pushed => $pushed, state => 'done',
                     finished_at => _now(), message => $summary);
    return { gate_id => $gid, run_id => $run_id, clean => $clean,
             pushed => $pushed, sha => $sha, message => $summary };
}

# ---- per-repo build recipes (build-area; mirror the manual recipes) -------
sub build_repo {
    my ($repo, $jobs, $logdir) = @_;
    return build_iverilog($jobs, $logdir) if $repo eq 'iverilog';
    return build_nvc($jobs, $logdir)      if $repo eq 'nvc';
    return (0, "no build recipe for $repo");
}

sub build_iverilog {
    my ($jobs, $logdir) = @_;
    my $dir = repo_dir('iverilog');
    my $pfx = "$dir/_install";
    for my $step (
        [['sh', '-c', "cd '$dir' && ./configure --prefix='$pfx'"], 'configure'],
        [['sh', '-c', "cd '$dir' && rm -f version_tag.h"],         'verstamp'],
        [['sh', '-c', "cd '$dir' && smak -j$jobs"],                'smak'],
        [['sh', '-c', "cd '$dir' && make -j$jobs"],                'make'],
        [['sh', '-c', "cd '$dir' && make install"],                'install'],
    ) {
        my ($rc) = run_capture($step->[0], log => "$logdir/build-iverilog-$step->[1].log");
        return (0, "iverilog $step->[1] rc=$rc") if $rc != 0;
    }
    my ($vrc) = run_capture(["$pfx/bin/iverilog", '-V'], log => "$logdir/build-iverilog-V.log");
    return ($vrc == 0, $vrc == 0 ? 'ok' : 'iverilog -V failed');
}

sub build_nvc {
    my ($jobs, $logdir) = @_;
    my $bdir = src_root() . '/nvc-build';
    return (0, "no nvc-build dir") unless -d $bdir;
    # smak first (per directive), then make -k to complete the VHDL libs
    run_capture(['sh', '-c', "cd '$bdir' && rm -rf lib"], log => "$logdir/build-nvc-clean.log");
    run_capture(['sh', '-c', "cd '$bdir' && smak -j$jobs"], log => "$logdir/build-nvc-smak.log");
    my ($mrc) = run_capture(['sh', '-c', "cd '$bdir' && make -k -j$jobs"],
                            log => "$logdir/build-nvc-make.log");
    my ($rrc) = run_capture(
        ['sh', '-c', "cd '$bdir' && BUILD_DIR=\$PWD NVC_LIBPATH=\$PWD/lib bin/run_regr wait1"],
        log => "$logdir/build-nvc-smoke.log");
    return ($rrc == 0, $rrc == 0 ? 'ok' : "nvc build smoke rc=$rrc make=$mrc");
}

sub _fail {
    my ($db, $gid, $msg) = @_;
    $db->update_gate($gid, state => 'failed', finished_at => _now(), message => $msg);
    return { gate_id => $gid, clean => 0, pushed => 0, error => $msg };
}

sub _now { my @t = gmtime; sprintf("%04d-%02d-%02d %02d:%02d:%02d",
                                   $t[5]+1900,$t[4]+1,$t[3],$t[2],$t[1],$t[0]) }

1;
