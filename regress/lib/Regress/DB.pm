package Regress::DB;
#
# SQLite-backed results store for the regression harness.
#
# Schema:
#   run        one harness invocation (timestamp, host, notes)
#   repo_sha   git SHA (or version string) of each tool/repo for a run
#   block_run  one (suite x engine) block within a run, with roll-up counts
#   result     one test result inside a block_run
#
# WAL journal mode + a busy timeout let the smak-dispatched per-block runners
# write concurrently into the same DB file.
#
use strict;
use warnings;
use DBI;

# Canonical status vocabulary every adapter normalizes to.
our @STATUSES = qw(pass fail skip notimpl xfail error);

sub new {
    my ($class, %opt) = @_;
    my $path = $opt{path} or die "Regress::DB: 'path' required\n";
    my %attr = (RaiseError => 1, AutoCommit => 1, PrintError => 0, sqlite_unicode => 1);
    if ($opt{readonly}) {
        # read-only open for the web dashboard: never writes, safe alongside a
        # live run writing in WAL mode.
        require DBD::SQLite;
        $attr{sqlite_open_flags} = DBD::SQLite::OPEN_READONLY();
    }
    my $dbh = DBI->connect("dbi:SQLite:dbname=$path", '', '', \%attr);
    $dbh->do("PRAGMA busy_timeout=30000");
    my $self = bless { dbh => $dbh, path => $path, readonly => $opt{readonly} }, $class;
    unless ($opt{readonly}) {
        $dbh->do("PRAGMA journal_mode=WAL");
        $dbh->do("PRAGMA foreign_keys=ON");
        $dbh->do("PRAGMA synchronous=NORMAL");
        $self->init_schema;
    }
    return $self;
}

sub dbh { $_[0]{dbh} }

sub init_schema {
    my $self = shift;
    my $dbh = $self->{dbh};
    $dbh->do($_) for (
        q{CREATE TABLE IF NOT EXISTS run (
            run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            host        TEXT,
            notes       TEXT
        )},
        q{CREATE TABLE IF NOT EXISTS repo_sha (
            run_id  INTEGER NOT NULL REFERENCES run(run_id),
            repo    TEXT NOT NULL,
            sha     TEXT,
            version TEXT,
            PRIMARY KEY (run_id, repo)
        )},
        q{CREATE TABLE IF NOT EXISTS block_run (
            block_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       INTEGER NOT NULL REFERENCES run(run_id),
            block        TEXT NOT NULL,
            suite        TEXT,
            engine       TEXT,
            options      TEXT,
            started_at   TEXT,
            finished_at  TEXT,
            exit_code    INTEGER,
            total        INTEGER DEFAULT 0,
            passed       INTEGER DEFAULT 0,
            failed       INTEGER DEFAULT 0,
            skipped      INTEGER DEFAULT 0,
            notimpl      INTEGER DEFAULT 0,
            xfail        INTEGER DEFAULT 0,
            errored      INTEGER DEFAULT 0
        )},
        q{CREATE TABLE IF NOT EXISTS result (
            block_run_id INTEGER NOT NULL REFERENCES block_run(block_run_id),
            test_name    TEXT NOT NULL,
            status       TEXT NOT NULL,
            duration_ms  INTEGER,
            message      TEXT,
            log_path     TEXT
        )},
        q{CREATE TABLE IF NOT EXISTS opinion (
            block       TEXT NOT NULL,
            test_name   TEXT NOT NULL,
            disposition TEXT,
            note        TEXT,
            author      TEXT,
            run_id      INTEGER,
            updated_at  TEXT,
            PRIMARY KEY (block, test_name)
        )},
        # one row per (block,test): last-known-good details + when the current
        # failing streak began. Updated every run (see record_results).
        q{CREATE TABLE IF NOT EXISTS test_status (
            block             TEXT NOT NULL,
            test_name         TEXT NOT NULL,
            last_run_id       INTEGER,
            last_status       TEXT,
            last_at           TEXT,
            last_pass_run_id  INTEGER,
            last_pass_at      TEXT,
            last_pass_sims    TEXT,
            last_pass_options TEXT,
            last_pass_ms      INTEGER,
            fail_since_run_id INTEGER,
            fail_since_at     TEXT,
            PRIMARY KEY (block, test_name)
        )},
        # CI gate jobs (laptop-delegated candidate runs)
        q{CREATE TABLE IF NOT EXISTS gate_job (
            gate_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT,
            finished_at TEXT,
            repo        TEXT,
            ref         TEXT,
            sha         TEXT,
            target      TEXT,
            do_push     INTEGER,
            state       TEXT,
            run_id      INTEGER,
            clean       INTEGER,
            pushed      INTEGER,
            message     TEXT
        )},
        # the run representing origin/main per repo — the gate's comparison baseline
        q{CREATE TABLE IF NOT EXISTS baseline (
            repo    TEXT PRIMARY KEY,
            run_id  INTEGER,
            sha     TEXT,
            set_at  TEXT,
            note    TEXT
        )},
        q{CREATE INDEX IF NOT EXISTS idx_result_block ON result(block_run_id)},
        q{CREATE INDEX IF NOT EXISTS idx_result_name  ON result(test_name)},
        q{CREATE INDEX IF NOT EXISTS idx_blockrun_run ON block_run(run_id, block)},
    );
    eval { $dbh->do("ALTER TABLE gate_job ADD COLUMN baseline_run_id INTEGER"); 1 };
    # migration for DBs created before the options column existed
    eval { $dbh->do("ALTER TABLE block_run ADD COLUMN options TEXT"); 1 };
}

# ---- run lifecycle -------------------------------------------------------

sub start_run {
    my ($self, %a) = @_;
    $self->{dbh}->do(
        "INSERT INTO run (started_at, host, notes) VALUES (?,?,?)",
        undef, _now(), ($a{host} // _host()), $a{notes});
    return $self->{dbh}->last_insert_id('', '', 'run', '');
}

sub finish_run {
    my ($self, $run_id) = @_;
    $self->{dbh}->do("UPDATE run SET finished_at=? WHERE run_id=?",
                     undef, _now(), $run_id);
}

sub record_repo {
    my ($self, $run_id, $repo, $sha, $version) = @_;
    $self->{dbh}->do(
        "INSERT OR REPLACE INTO repo_sha (run_id, repo, sha, version) VALUES (?,?,?,?)",
        undef, $run_id, $repo, $sha, $version);
}

# ---- block lifecycle -----------------------------------------------------

sub start_block_run {
    my ($self, %a) = @_;
    $self->{dbh}->do(
        "INSERT INTO block_run (run_id, block, suite, engine, options, started_at)
         VALUES (?,?,?,?,?,?)",
        undef, $a{run_id}, $a{block}, $a{suite}, $a{engine}, $a{options}, _now());
    return $self->{dbh}->last_insert_id('', '', 'block_run', '');
}

# short "iverilog=03059935(0305993-dirty); nvc=8249654; verilator=..." summary
sub sims_text {
    my ($self, $run_id) = @_;
    my $rows = $self->repo_shas_for($run_id);
    my @p;
    for my $r (@$rows) {
        next unless grep { $r->{repo} eq $_ } qw(iverilog nvc verilator sv2ghdl);
        my $s = substr($r->{sha} // '', 0, 9);
        my ($vtag) = ($r->{version} // '') =~ /\(([^)]+)\)\s*$/;
        push @p, $r->{repo} . '=' . ($s || $vtag || '?') . ($vtag && $vtag ne $s ? "($vtag)" : '');
    }
    return join('; ', @p);
}

# Insert all results for a block in one transaction, roll up the counts, and
# maintain per-test history (last pass + fail-since). Needs run_id/block/options.
sub record_results {
    my ($self, $block_run_id, $results, %a) = @_;
    my $dbh = $self->{dbh};
    my %n = map { $_ => 0 } @STATUSES;

    my ($run_id, $block, $options) = @a{qw(run_id block options)};
    my $sims  = ($run_id) ? $self->sims_text($run_id) : undef;
    my ($run_at) = $run_id
        ? $dbh->selectrow_array("SELECT started_at FROM run WHERE run_id=?", undef, $run_id)
        : undef;
    $run_at ||= _now();

    $dbh->begin_work;
    my $ins = $dbh->prepare(
        "INSERT INTO result (block_run_id, test_name, status, duration_ms, message, log_path)
         VALUES (?,?,?,?,?,?)");

    for my $r (@$results) {
        my $st = $r->{status} // 'error';
        $n{$st}++ if exists $n{$st};
        $ins->execute($block_run_id, $r->{test_name}, $st,
                      $r->{duration_ms}, $r->{message}, $r->{log_path});
        $self->_status_update($block, $r->{test_name}, $st, $r->{duration_ms},
                              $run_id, $run_at, $sims, $options)
            if $run_id && defined $block;
    }
    my $total = scalar @$results;
    $dbh->do(
        "UPDATE block_run SET finished_at=?, exit_code=?, total=?,
            passed=?, failed=?, skipped=?, notimpl=?, xfail=?, errored=?
         WHERE block_run_id=?",
        undef, _now(), $a{exit_code}, $total,
        $n{pass}, $n{fail}, $n{skip}, $n{notimpl}, $n{xfail}, $n{error},
        $block_run_id);
    $dbh->commit;
    return { total => $total, %n };
}

# Update one test's history row: refresh last-seen status, last-pass details
# (build sims/options/duration) on a pass, and the failing-streak start. Shared
# by live recording (record_results) and backfill().
sub _status_update {
    my ($self, $block, $test, $status, $dur, $run_id, $run_at, $sims, $options) = @_;
    my $dbh = $self->{dbh};
    my $e = $dbh->selectrow_hashref(
        "SELECT last_pass_run_id, last_pass_at, last_pass_sims, last_pass_options,
                last_pass_ms, fail_since_run_id, fail_since_at
           FROM test_status WHERE block=? AND test_name=?", undef, $block, $test);
    my $good = ($status eq 'pass' || $status eq 'xfail');
    my ($lp_run, $lp_at, $lp_sims, $lp_opt, $lp_ms, $fs_run, $fs_at);
    if ($good) {
        ($lp_run, $lp_at, $lp_sims, $lp_opt, $lp_ms) =
            ($run_id, $run_at, $sims, $options, $dur);
        ($fs_run, $fs_at) = (undef, undef);                 # passing → no failing streak
    } else {
        ($lp_run, $lp_at, $lp_sims, $lp_opt, $lp_ms) = $e
            ? @{$e}{qw(last_pass_run_id last_pass_at last_pass_sims last_pass_options last_pass_ms)}
            : (undef) x 5;
        ($fs_run, $fs_at) = ($e && $e->{fail_since_run_id})
            ? ($e->{fail_since_run_id}, $e->{fail_since_at})  # streak continues
            : ($run_id, $run_at);                             # streak starts here
    }
    $dbh->do(
        "INSERT OR REPLACE INTO test_status
           (block, test_name, last_run_id, last_status, last_at,
            last_pass_run_id, last_pass_at, last_pass_sims, last_pass_options, last_pass_ms,
            fail_since_run_id, fail_since_at)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        undef, $block, $test, $run_id, $status, $run_at,
        $lp_run, $lp_at, $lp_sims, $lp_opt, $lp_ms, $fs_run, $fs_at);
}

# Rebuild test_status from scratch by replaying every recorded run in
# chronological (run_id) order. Returns the number of result rows replayed.
sub backfill {
    my $self = shift;
    my $dbh = $self->{dbh};
    $dbh->begin_work;
    $dbh->do("DELETE FROM test_status");
    my $runs = $dbh->selectall_arrayref(
        "SELECT run_id, started_at FROM run ORDER BY run_id", { Slice => {} });
    my $n = 0;
    for my $run (@$runs) {
        my $rid    = $run->{run_id};
        my $run_at = $run->{started_at} || _now();
        my $sims   = $self->sims_text($rid);
        my $brs = $dbh->selectall_arrayref(
            "SELECT block_run_id, block, options FROM block_run
              WHERE run_id=? ORDER BY block_run_id", { Slice => {} }, $rid);
        for my $br (@$brs) {
            my $res = $dbh->selectall_arrayref(
                "SELECT test_name, status, duration_ms FROM result WHERE block_run_id=?",
                { Slice => {} }, $br->{block_run_id});
            for my $r (@$res) {
                $self->_status_update($br->{block}, $r->{test_name}, $r->{status},
                                      $r->{duration_ms}, $rid, $run_at, $sims, $br->{options});
                $n++;
            }
        }
    }
    $dbh->commit;
    return $n;
}

# ---- queries -------------------------------------------------------------

sub latest_run_id {
    my $self = shift;
    my ($id) = $self->{dbh}->selectrow_array(
        "SELECT run_id FROM run ORDER BY run_id DESC LIMIT 1");
    return $id;
}

# previous run that contains a given block (for regression diffing)
sub prev_run_with_block {
    my ($self, $run_id, $block) = @_;
    my ($id) = $self->{dbh}->selectrow_array(
        "SELECT run_id FROM block_run WHERE block=? AND run_id<? ORDER BY run_id DESC LIMIT 1",
        undef, $block, $run_id);
    return $id;
}

sub block_summaries {
    my ($self, $run_id) = @_;
    return $self->{dbh}->selectall_arrayref(
        "SELECT block, suite, engine, total, passed, failed, skipped, notimpl, xfail, errored, exit_code
         FROM block_run WHERE run_id=? ORDER BY block",
        { Slice => {} }, $run_id);
}

# test_name => status map for one block in one run
sub block_results_map {
    my ($self, $run_id, $block) = @_;
    my $rows = $self->{dbh}->selectall_arrayref(
        "SELECT r.test_name, r.status
           FROM result r JOIN block_run b ON r.block_run_id=b.block_run_id
          WHERE b.run_id=? AND b.block=?",
        undef, $run_id, $block);
    my %m; $m{$_->[0]} = $_->[1] for @$rows;
    return \%m;
}

# ---- dashboard queries ---------------------------------------------------

# all runs, newest first, with a rolled-up status and block counts
sub all_runs {
    my $self = shift;
    return $self->{dbh}->selectall_arrayref(
        "SELECT run_id, started_at, finished_at, host, notes FROM run ORDER BY run_id DESC",
        { Slice => {} });
}

sub run_meta {
    my ($self, $run_id) = @_;
    return $self->{dbh}->selectrow_hashref(
        "SELECT run_id, started_at, finished_at, host, notes FROM run WHERE run_id=?",
        undef, $run_id);
}

# full block_run rows for a run, including timestamps so the dashboard can
# show which blocks are still running (finished_at IS NULL).
sub block_rows {
    my ($self, $run_id) = @_;
    return $self->{dbh}->selectall_arrayref(
        "SELECT block_run_id, block, suite, engine, options, started_at, finished_at,
                exit_code, total, passed, failed, skipped, notimpl, xfail, errored
         FROM block_run WHERE run_id=? ORDER BY block",
        { Slice => {} }, $run_id);
}

sub repo_shas_for {
    my ($self, $run_id) = @_;
    return $self->{dbh}->selectall_arrayref(
        "SELECT repo, sha, version FROM repo_sha WHERE run_id=? ORDER BY repo",
        { Slice => {} }, $run_id);
}

# per-test rows for one block in a run, optionally filtered by status
sub results_for {
    my ($self, $run_id, $block, $status) = @_;
    my $sql = "SELECT r.test_name, r.status, r.duration_ms, r.message, r.log_path
                 FROM result r JOIN block_run b ON r.block_run_id=b.block_run_id
                WHERE b.run_id=? AND b.block=?";
    my @args = ($run_id, $block);
    if (defined $status && length $status) { $sql .= " AND r.status=?"; push @args, $status }
    $sql .= " ORDER BY r.status, r.test_name";
    return $self->{dbh}->selectall_arrayref($sql, { Slice => {} }, @args);
}

# full row for one test in a block of a run
sub result_row {
    my ($self, $run_id, $block, $test) = @_;
    return $self->{dbh}->selectrow_hashref(
        "SELECT r.test_name, r.status, r.duration_ms, r.message, r.log_path
           FROM result r JOIN block_run b ON r.block_run_id=b.block_run_id
          WHERE b.run_id=? AND b.block=? AND r.test_name=? LIMIT 1",
        undef, $run_id, $block, $test);
}

# ---- triage opinions (waive / investigate / ...) -------------------------
# Keyed by (block, test_name) so a disposition persists across runs.

sub set_opinion {
    my ($self, %a) = @_;
    $self->{dbh}->do(
        "INSERT INTO opinion (block, test_name, disposition, note, author, run_id, updated_at)
         VALUES (?,?,?,?,?,?,?)
         ON CONFLICT(block, test_name) DO UPDATE SET
            disposition=excluded.disposition, note=excluded.note,
            author=excluded.author, run_id=excluded.run_id, updated_at=excluded.updated_at",
        undef, $a{block}, $a{test}, $a{disposition}, $a{note},
        $a{author}, $a{run_id}, _now());
}

sub opinions_for_block {
    my ($self, $block) = @_;
    my $rows = $self->{dbh}->selectall_arrayref(
        "SELECT test_name, disposition, note, author, updated_at FROM opinion WHERE block=?",
        { Slice => {} }, $block);
    my %m; $m{$_->{test_name}} = $_ for @$rows;
    return \%m;
}

sub get_opinion {
    my ($self, $block, $test) = @_;
    return $self->{dbh}->selectrow_hashref(
        "SELECT disposition, note, author, updated_at FROM opinion WHERE block=? AND test_name=?",
        undef, $block, $test);
}

sub get_test_status {
    my ($self, $block, $test) = @_;
    return $self->{dbh}->selectrow_hashref(
        "SELECT * FROM test_status WHERE block=? AND test_name=?",
        undef, $block, $test);
}

# pass->fail regressions in $run_id vs the previous run of each block.
# Returns [ {block, test, was, now}, ... ].
sub regressions_in_run {
    my ($self, $run_id) = @_;
    my @out;
    for my $b (@{ $self->block_summaries($run_id) }) {
        my $prev = $self->prev_run_with_block($run_id, $b->{block}) or next;
        my $now  = $self->block_results_map($run_id, $b->{block});
        my $old  = $self->block_results_map($prev,  $b->{block});
        for my $t (sort keys %$now) {
            next unless exists $old->{$t};
            my ($o, $n) = ($old->{$t}, $now->{$t});
            push @out, { block => $b->{block}, test => $t, was => $o, now => $n }
                if _good_status($o) && !_good_status($n);
        }
    }
    return \@out;
}
sub _good_status { my $s = shift // ''; $s eq 'pass' || $s eq 'xfail' }

# pass->fail regressions in $run_id vs an explicit baseline run (block-by-block,
# common tests only). Used by the gate so it compares against last main, not
# whatever ran most recently.
sub regressions_vs_baseline {
    my ($self, $run_id, $base_run) = @_;
    my @out;
    return \@out unless $base_run;
    for my $b (@{ $self->block_summaries($run_id) }) {
        my $now = $self->block_results_map($run_id,  $b->{block});
        my $old = $self->block_results_map($base_run, $b->{block});
        next unless %$old;     # baseline didn't run this block -> can't judge
        for my $t (sort keys %$now) {
            next unless exists $old->{$t};
            push @out, { block => $b->{block}, test => $t, was => $old->{$t}, now => $now->{$t} }
                if _good_status($old->{$t}) && !_good_status($now->{$t});
        }
    }
    return \@out;
}

# ---- main baseline (per repo) --------------------------------------------

sub get_baseline {
    my ($self, $repo) = @_;
    return $self->{dbh}->selectrow_hashref(
        "SELECT repo, run_id, sha, set_at, note FROM baseline WHERE repo=?", undef, $repo);
}

sub set_baseline {
    my ($self, $repo, $run_id, $sha, $note) = @_;
    $self->{dbh}->do(
        "INSERT OR REPLACE INTO baseline (repo, run_id, sha, set_at, note) VALUES (?,?,?,?,?)",
        undef, $repo, $run_id, $sha, _now(), $note);
}

sub all_baselines {
    my $self = shift;
    return $self->{dbh}->selectall_arrayref(
        "SELECT repo, run_id, sha, set_at, note FROM baseline ORDER BY repo", { Slice => {} });
}

# ---- gate jobs -----------------------------------------------------------

sub create_gate {
    my ($self, %a) = @_;
    $self->{dbh}->do(
        "INSERT INTO gate_job (created_at, repo, ref, target, do_push, state)
         VALUES (?,?,?,?,?,?)",
        undef, _now(), $a{repo}, $a{ref}, $a{target}, ($a{push} ? 1 : 0), 'queued');
    return $self->{dbh}->last_insert_id('', '', 'gate_job', '');
}

sub update_gate {
    my ($self, $gate_id, %a) = @_;
    my @cols = grep { exists $a{$_} } qw(finished_at sha state run_id clean pushed message baseline_run_id);
    return unless @cols;
    my $set = join(', ', map { "$_=?" } @cols);
    $self->{dbh}->do("UPDATE gate_job SET $set WHERE gate_id=?",
                     undef, @a{@cols}, $gate_id);
}

sub get_gate {
    my ($self, $gate_id) = @_;
    return $self->{dbh}->selectrow_hashref(
        "SELECT * FROM gate_job WHERE gate_id=?", undef, $gate_id);
}

sub all_gates {
    my $self = shift;
    return $self->{dbh}->selectall_arrayref(
        "SELECT * FROM gate_job ORDER BY gate_id DESC LIMIT 100", { Slice => {} });
}

sub _now  { my @t = gmtime; sprintf("%04d-%02d-%02d %02d:%02d:%02d",
                                    $t[5]+1900,$t[4]+1,$t[3],$t[2],$t[1],$t[0]) }
sub _host { my $h = `hostname 2>/dev/null`; chomp $h if defined $h; $h || 'unknown' }

1;
