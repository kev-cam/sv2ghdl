package Regress::Web;
#
# Minimal dependency-free dashboard for the regression harness.
#
# A tiny HTTP/1.0 server (core IO::Socket::INET only — no apt/cpan deps) that
# reads results.db read-only and renders:
#   /                      runs list; in-progress run highlighted with live
#                          block status + a tail of each running block's log
#   /run?id=N              per-block detail for a run (+ repo SHAs)
#   /block?run=N&block=..  per-test results (status-filterable)
#   /log?run=N&block=..    raw tail of a block's log file
#
# Pages auto-refresh (fast while a run is in progress) so progress shows live.
# "In progress" = run / block_run rows whose finished_at IS NULL.
#
use strict;
use warnings;
use IO::Socket::INET;
use POSIX ();
use Regress::DB;
use Regress::Tools qw(src_root);

my ($DBPATH, $REGRESS_DIR);

sub serve {
    my (%opt) = @_;
    $DBPATH      = $opt{db}          or die "Regress::Web: db required\n";
    $REGRESS_DIR = $opt{regress_dir} or die "Regress::Web: regress_dir required\n";
    my $port = $opt{port} || 8088;
    my $host = $opt{host} || '0.0.0.0';

    # one-time writable open to ensure the schema (incl. opinion table) exists,
    # so the read-only request handlers can SELECT it safely.
    eval { Regress::DB->new(path => $DBPATH); 1 };

    my $srv = IO::Socket::INET->new(
        LocalAddr => $host, LocalPort => $port, Listen => 32,
        ReuseAddr => 1, Proto => 'tcp',
    ) or die "Regress::Web: cannot listen on $host:$port: $!\n";

    $SIG{CHLD} = 'IGNORE';   # auto-reap forked request handlers
    my $hn = `hostname 2>/dev/null`; chomp $hn;
    print "regress dashboard on http://${\($host eq '0.0.0.0' ? ($hn||'localhost') : $host)}:$port/  (db=$DBPATH)\n";
    print "Ctrl-C to stop.\n";

    while (my $conn = $srv->accept) {
        my $pid = fork;
        if (!defined $pid) { $conn->close; next }
        if ($pid == 0) { $srv->close; _handle($conn); exit 0 }
        $conn->close;
    }
}

sub _handle {
    my $conn = shift;
    my $line = <$conn>;
    return unless defined $line;
    my %hdr;
    while (defined(my $h = <$conn>)) {
        last if $h =~ /^\r?$/;
        $hdr{lc $1} = $2 if $h =~ /^([^:]+):\s*(.*?)\r?$/;
    }
    my ($method, $uri) = $line =~ /^(\S+)\s+(\S+)/;
    $method //= 'GET'; $uri //= '/';
    my ($path, $qs) = split /\?/, $uri, 2;

    # POST: read the body and dispatch write actions (triage opinions)
    if ($method eq 'POST') {
        my $len = $hdr{'content-length'} || 0;
        my $body = '';
        read($conn, $body, $len) if $len > 0;
        my %f = _parse_qs($body);
        return handle_post($conn, $path, \%f);
    }

    my %q = _parse_qs($qs);
    my $db = eval { Regress::DB->new(path => $DBPATH, readonly => 1) };
    if (!$db) { _send($conn, '500 Internal Server Error', 'text/plain',
                      "DB open failed: $@"); return }

    if    ($path eq '/')      { _send($conn, '200 OK', 'text/html', page_index($db)) }
    elsif ($path eq '/run')   { _send($conn, '200 OK', 'text/html', page_run($db, $q{id})) }
    elsif ($path eq '/block') { _send($conn, '200 OK', 'text/html',
                                       page_block($db, $q{run}, $q{block}, $q{status})) }
    elsif ($path eq '/test')  { _send($conn, '200 OK', 'text/html',
                                       page_test($db, $q{run}, $q{block}, $q{test})) }
    elsif ($path eq '/gates') { _send($conn, '200 OK', 'text/html', page_gates($db)) }
    elsif ($path eq '/gate')  { _send($conn, '200 OK', 'text/html', page_gate($db, $q{id})) }
    elsif ($path eq '/help')  { _send($conn, '200 OK', 'text/html', page_help()) }
    elsif ($path eq '/log')   { _send($conn, '200 OK', 'text/plain',
                                       tail_log($q{run}, $q{block})) }
    elsif ($path eq '/file')  { _send($conn, '200 OK', 'text/plain',
                                       serve_file($q{path})) }
    else                      { _send($conn, '404 Not Found', 'text/plain', "not found\n") }
}

# POST actions (write). Opens the DB writable just for the action.
sub handle_post {
    my ($conn, $path, $f) = @_;
    if ($path eq '/opinion') {
        my $db = eval { Regress::DB->new(path => $DBPATH) };
        if (!$db) { return _send($conn, '500 Internal Server Error', 'text/plain', "DB: $@") }
        $db->set_opinion(
            block => $f->{block}, test => $f->{test},
            disposition => $f->{disposition}, note => $f->{note},
            author => ($f->{author} && length $f->{author} ? $f->{author} : 'web'),
            run_id => $f->{run});
        # back to where they came from (test page if single, else the block list)
        my $loc = $f->{back} && length $f->{back} ? $f->{back}
                : "/test?run=" . _urlenc($f->{run}) . "&block=" . _urlenc($f->{block})
                  . "&test=" . _urlenc($f->{test});
        return _redirect($conn, $loc);
    }
    if ($path eq '/gate') {
        my $repo = $f->{repo} // '';
        unless ($repo =~ /^(?:iverilog|nvc)$/) {
            return _send($conn, '400 Bad Request', 'text/plain', "repo must be iverilog|nvc\n");
        }
        my $db = eval { Regress::DB->new(path => $DBPATH) };
        if (!$db) { return _send($conn, '500 Internal Server Error', 'text/plain', "DB: $@") }
        my $gid = $db->create_gate(repo => $repo, ref => ($f->{ref} || 'HEAD'),
                                   target => $f->{target}, push => $f->{push});
        _spawn_gate($gid, $f);
        return _redirect($conn, "/gate?id=$gid");
    }
    _send($conn, '404 Not Found', 'text/plain', "no such action\n");
}

# launch the gate pipeline as a detached worker so the HTTP request returns at
# once; the worker updates the gate_job row that /gate?id= polls.
sub _spawn_gate {
    my ($gid, $f) = @_;
    my $pid = fork;
    return unless defined $pid;
    if ($pid == 0) {
        POSIX::setsid();
        my $logdir = "$REGRESS_DIR/out/gate-$gid";
        mkdir $logdir;
        open STDIN,  '<', '/dev/null';
        open STDOUT, '>', "$logdir/worker.log";
        open STDERR, '>&', \*STDOUT;
        my @cmd = ("$REGRESS_DIR/regress", 'gate', '--gate-id', $gid,
                   '--repo', $f->{repo}, '--ref', ($f->{ref} || 'HEAD'), '--db', $DBPATH);
        push @cmd, ('--target', $f->{target}) if $f->{target} && length $f->{target};
        push @cmd, '--push'     if $f->{push};
        push @cmd, '--no-build' if $f->{no_build};
        push @cmd, '--no-fetch' if $f->{no_fetch};
        push @cmd, ('--filter', $f->{filter}) if defined $f->{filter} && length $f->{filter};
        exec @cmd;
        exit 127;
    }
}

sub _redirect {
    my ($conn, $loc) = @_;
    print $conn "HTTP/1.0 302 Found\r\nLocation: $loc\r\n"
              . "Content-Length: 0\r\nConnection: close\r\n\r\n";
}

# ---- HTTP plumbing -------------------------------------------------------

sub _send {
    my ($conn, $status, $ctype, $body) = @_;
    $body //= '';
    my $len = length $body;
    print $conn "HTTP/1.0 $status\r\nContent-Type: $ctype; charset=utf-8\r\n"
              . "Content-Length: $len\r\nConnection: close\r\n\r\n$body";
}

sub _parse_qs {
    my $qs = shift // '';
    my %q;
    for my $pair (split /&/, $qs) {
        my ($k, $v) = split /=/, $pair, 2;
        next unless defined $k;
        $v //= '';
        $_ =~ s/\+/ /g, $_ =~ s/%([0-9A-Fa-f]{2})/chr hex $1/ge for ($k, $v);
        $q{$k} = $v;
    }
    return %q;
}

sub h { my $s = shift; $s //= ''; $s =~ s/&/&amp;/g; $s =~ s/</&lt;/g; $s =~ s/>/&gt;/g; $s =~ s/"/&quot;/g; $s }
sub _urlenc { my $s = shift; $s //= ''; $s =~ s/([^A-Za-z0-9._\/-])/sprintf('%%%02X', ord $1)/ge; $s }

# ---- shared layout -------------------------------------------------------

my $CSS = <<'CSS';
body{font:13px/1.45 system-ui,sans-serif;margin:0;background:#0f1117;color:#d6dae0;min-height:100vh;display:flex;flex-direction:column}
a{color:#6cb6ff;text-decoration:none}a:hover{text-decoration:underline}
header{padding:10px 18px;background:#161a22;border-bottom:1px solid #262c38}
header h1{font-size:15px;margin:0;display:inline}
.wrap{padding:14px 18px;flex:1 1 auto;display:flex;flex-direction:column;min-height:0}
pre.srcfill{flex:1 1 auto;min-height:160px;overflow:auto;background:#0a0c10;border:1px solid #232936;padding:8px;font-size:12px;margin-bottom:0}
table{border-collapse:collapse;width:100%;margin:8px 0}
th,td{padding:4px 8px;border-bottom:1px solid #232936;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th.l,td.l{text-align:left}
th{color:#9aa4b2;font-weight:600}
tr:hover td{background:#171c26}
.run{font-weight:600}
.b{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600}
.running{background:#3a2f00;color:#ffd24a}.done{background:#10301a;color:#54d97f}
.pass{color:#54d97f}.fail{color:#ff6b6b}.err{color:#ff9f43}.skip{color:#8a93a3}
.zero{color:#5b6472}
pre.log{background:#0a0c10;border:1px solid #232936;padding:8px;max-height:260px;overflow:auto;font-size:12px}
.muted{color:#8a93a3}
.bar{height:6px;border-radius:3px;background:#232936;overflow:hidden;display:inline-block;width:120px;vertical-align:middle}
.bar>i{display:block;height:100%;float:left}
.bar .p{background:#54d97f}.bar .f{background:#ff6b6b}.bar .o{background:#ff9f43}
.d{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:600}
.d-waive{background:#2a2f3a;color:#9aa4b2}.d-investigate{background:#3a2f00;color:#ffd24a}
.d-bug{background:#3a1518;color:#ff8a8a}.d-wontfix{background:#241a2e;color:#c39bff}
.d-flaky{background:#123026;color:#54d9c0}
select,input,button{font:12px system-ui;background:#0a0c10;color:#d6dae0;border:1px solid #2b3343;border-radius:4px;padding:2px 5px}
button{cursor:pointer}form.op{display:inline}
CSS

sub _layout {
    my ($title, $refresh, $body) = @_;
    my $meta = $refresh ? qq(<meta http-equiv="refresh" content="$refresh">) : '';
    return "<!doctype html><html><head><meta charset=utf-8>$meta"
         . "<title>" . h($title) . "</title><style>$CSS</style></head><body>"
         . "<header><h1>🧪 regress dashboard</h1> "
         . "<span class=muted>&nbsp; <a href='/'>runs</a> · <a href='/gates'>gates</a> · <a href='/help'>help</a></span></header>"
         . "<div class=wrap>$body</div></body></html>";
}

sub page_help {
    my $b = <<'HTML';
<h2>regress — help &amp; documentation</h2>
<p class=muted>A multi-simulator regression harness with a SQLite results
tracker, a CI gate, and a cherry-picker. Built to vet other workers' changes to
the simulators; everything runs against <b>build-area</b> tool binaries
(<code>nvc-build/</code>, <code>iverilog/_install</code>), not system installs.</p>

<h3>Dashboard pages</h3>
<table>
<tr><th>page</th><th class=l>shows</th></tr>
<tr><td><a href='/'>/ (runs)</a></td><td class=l>All runs newest-first; an in-progress run is pinned on top with live per-block status and a tail of each running block's log. Each row links to the run; the pass/fail bar summarizes it. Auto-refreshes while a run is active.</td></tr>
<tr><td>/run?id=N</td><td class=l>One run: the simulator/tool SHAs + versions captured for it, and a per-block table (status, exit code, pass/fail/skip/ni/xf/err, bar). Every count links to the matching per-test list.</td></tr>
<tr><td>/block?run=N&amp;block=B</td><td class=l>Per-test results for a block, filterable by status. Each test links to its detail page; the <i>triage</i> column shows any disposition.</td></tr>
<tr><td>/test?run=N&amp;block=B&amp;test=T</td><td class=l>One test: verdict + message, <b>history</b> (last pass with sim SHAs/options/runtime, and failing-since), a <b>triage</b> form, the relevant <b>log</b> slice, and the <b>test source</b>.</td></tr>
<tr><td><a href='/gates'>/gates</a></td><td class=l>CI gate jobs (laptop-delegated candidate runs) with state, repo/ref/sha, result, and a link to the run.</td></tr>
</table>

<h3>The test matrix (blocks = suite &times; engine)</h3>
<table>
<tr><th>block</th><th class=l>what it runs</th></tr>
<tr><td>ivtest/iverilog</td><td class=l>Icarus ivtest (vvp) under native iverilog</td></tr>
<tr><td>ivtest/iverilog-nvc</td><td class=l>ivtest via the iverilog-sv2ghdl shim &rarr; nvc (per-test timeout)</td></tr>
<tr><td>ivtest/nvc-vhdl</td><td class=l>ivtest VHDL path via vhdl_nvc_reg.pl &rarr; nvc</td></tr>
<tr><td>ivtest/iverilog-steve</td><td class=l>ivtest under upstream Icarus (A/B reference)</td></tr>
<tr><td>nvc/regr, nvc/unit</td><td class=l>nvc's own functional regression + C unit tests</td></tr>
<tr><td>sv-tests/verilator, sv-tests/iverilog</td><td class=l>the CHIPS-Alliance sv-tests corpus under verilator / Icarus</td></tr>
<tr><td>rtlmeter/verilator, rtlmeter/verilator-nvc</td><td class=l>RTLMeter designs under verilator / the verilator-sv2ghdl shim (heavy; run scoped)</td></tr>
</table>

<h3>Statuses &amp; indicators</h3>
<p><span class=pass>pass</span> · <span class=fail>fail</span> · skip · ni (not implemented) ·
xf (expected-fail = good) · <span class=err>err</span> (harness/dispatch error).
On the test detail page, triage shows
<span class='d d-waive'>waive</span> <span class='d d-investigate'>investigate</span>
<span class='d d-bug'>bug</span> <span class='d d-wontfix'>wontfix</span>
<span class='d d-flaky'>flaky</span>.</p>

<h3>History, baseline &amp; triage</h3>
<p>Every run updates a per-test history row: the <b>last time it passed</b> (with the
simulator build SHAs, run options, and its run time) and, if currently failing,
<b>when the failing streak began</b>. Regressions are judged as
<b>pass&rarr;fail vs the repo's main baseline</b> (set with
<code>regress baseline --repo R --run N</code>; auto-advanced when a clean gate
fast-forwards main) &mdash; pre-existing failures don't count. <code>regress backfill</code>
rebuilds history from all recorded runs. <b>Triage</b> opinions (waive/investigate/…
+ note) persist per (block,test) across runs.</p>

<h3>Running tests</h3>
<pre class=log>./regress run [block...]            # default: all ready blocks; smak-parallel dispatch
./regress run ivtest/iverilog --seq # in-process, no dispatcher
./run_my_regressions.py iverilog    # friendly groups: everything|iverilog|nvc|verilator|vhdl|ivtest|svtests|steve</pre>
<p class=muted>The dispatcher runs blocks via smak (or GNU make with <code>--make</code>);
blocks that share a working dir (ivtest, nvc, sv-tests, rtlmeter) are serialized,
others run in parallel.</p>

<h3>Reports &amp; comparison</h3>
<pre class=log>./regress report [--run N]                 # per-block summary + regressions vs baseline
./regress compare ivtest/iverilog ivtest/iverilog-steve   # A/B two engines in one run
./regress diff RUNA RUNB                    # test-by-test between two runs</pre>

<h3>CI gate &mdash; delegate from a laptop</h3>
<p>Push a branch to the GitHub fork, then trigger the gate via this web server
(no SSH). It fetches the branch, builds it in the build area, runs that repo's
regressions, and <b>fast-forwards <code>origin/main</code> if there are no
regressions vs the baseline</b> (refuses a non-fast-forward).</p>
<pre class=log># on the laptop, from your iverilog/ or nvc/ clone:
delegate-regressions --push
# or locally on this box:
./regress gate --repo iverilog [--ref REF] [--push]</pre>

<h3>Cherry-picker</h3>
<p>Bring in commits from upstream/contributor repos, gated by the harness.
<code>cherry_picker.py --repo nvc --gui</code> lists candidates (upstream, a
remote branch, <code>--from URL#ref</code>, or a PR), opens diffs in meld,
accepts/rejects per file, and merges accepted commits. <code>--gate</code>
routes through the gate.</p>

<h3>Notes</h3>
<ul>
<li>Tools resolve build-area-first; <code>results.db</code> and <code>out/</code> are per-machine (gitignored).</li>
<li>Start this server with <code>./regress serve [--port N] [--host H]</code>.</li>
<li><code>./regress --help</code> (and <code>--help</code> on the Python tools) lists every command.</li>
</ul>
HTML
    return _layout('help', 0, $b);
}

sub _is_running { !defined $_[0] || $_[0] eq '' }

# count cells; when $id/$block are given, each non-zero count links through to
# the status-filtered per-test list (total -> the full list).
my %STATUS_OF = (passed=>'pass', failed=>'fail', skipped=>'skip',
                 notimpl=>'notimpl', xfail=>'xfail', errored=>'error');
sub _counts_cells {
    my ($r, $id, $block) = @_;
    my @c;
    for my $k (qw(total passed failed skipped notimpl xfail errored)) {
        my $v = $r->{$k} // 0;
        my $cls = $v == 0 ? 'zero'
                : $k eq 'passed' ? 'pass'
                : $k eq 'failed' ? 'fail'
                : $k eq 'errored' ? 'err'
                : $k eq 'skipped' ? 'skip' : '';
        my $cell = $v;
        if ($v > 0 && defined $id && defined $block) {
            my $st = $STATUS_OF{$k};   # undef for 'total' -> link to full list
            my $url = "/block?run=$id&block=" . _urlenc($block)
                    . (defined $st ? "&status=$st" : '');
            $cell = "<a href='$url'>$v</a>";
        }
        push @c, "<td class='$cls'>$cell</td>";
    }
    return join '', @c;
}

sub _bar {
    my $r = shift;
    my $t = $r->{total} || 0; return '' unless $t;
    my ($p,$f,$o) = map { int(100*($r->{$_}//0)/$t) } qw(passed failed);
    $o = 100 - $p - $f; $o = 0 if $o < 0;
    return "<span class=bar><i class=p style='width:${p}%'></i>"
         . "<i class=f style='width:${f}%'></i><i class=o style='width:${o}%'></i></span>";
}

my @DISPOSITIONS = ('', 'waive', 'investigate', 'bug', 'wontfix', 'flaky');

sub _disp_badge {
    my $op = shift;
    return '' unless $op && $op->{disposition} && length $op->{disposition};
    my $d = $op->{disposition};
    my $note = (defined $op->{note} && length $op->{note}) ? ' ' . h($op->{note}) : '';
    return "<span class='d d-" . h($d) . "'>" . h($d) . "</span>" . $note;
}

sub _opinion_form {
    my ($id, $block, $test, $op, $back) = @_;
    my $cur  = $op ? ($op->{disposition} // '') : '';
    my $note = $op ? ($op->{note} // '') : '';
    my $sel = join '', map {
        my $s = ($_ eq $cur) ? ' selected' : '';
        "<option value='" . h($_) . "'$s>" . ($_ eq '' ? '—' : h($_)) . "</option>";
    } @DISPOSITIONS;
    return "<form class=op method=post action=/opinion>"
         . "<input type=hidden name=run value='" . h($id) . "'>"
         . "<input type=hidden name=block value=\"" . h($block) . "\">"
         . "<input type=hidden name=test value=\"" . h($test) . "\">"
         . "<input type=hidden name=back value=\"" . h($back) . "\">"
         . "<select name=disposition>$sel</select> "
         . "<input name=note size=28 value=\"" . h($note) . "\" placeholder='note'> "
         . "<button>save</button></form>";
}

# ---- pages ---------------------------------------------------------------

sub page_index {
    my $db = shift;
    my $runs = $db->all_runs;
    my $any_running = grep { _is_running($_->{finished_at}) } @$runs;

    my $body = '';

    # In-progress section (live blocks + log tails)
    for my $run (grep { _is_running($_->{finished_at}) } @$runs) {
        my $id = $run->{run_id};
        $body .= "<h2>▶ Run #$id <span class='b running'>in progress</span></h2>";
        $body .= "<div class=muted>started " . h($run->{started_at}) . " · "
               . h($run->{notes} // '') . "</div>";
        my $blocks = $db->block_rows($id);
        $body .= "<table><tr><th>block</th><th class=l>engine</th><th class=l>status</th>"
               . "<th>total</th><th>pass</th><th>fail</th><th>skip</th><th>ni</th><th>xf</th><th>err</th></tr>";
        for my $b (@$blocks) {
            my $running = _is_running($b->{finished_at});
            my $badge = $running ? "<span class='b running'>running</span>"
                                 : "<span class='b done'>done</span>";
            $body .= "<tr><td><a href='/block?run=$id&block=" . h($b->{block}) . "'>"
                   . h($b->{block}) . "</a></td><td class=l>" . h($b->{engine}) . "</td><td class=l>$badge</td>"
                   . _counts_cells($b, $id, $b->{block}) . "</tr>";
        }
        $body .= "</table>";
        # tail logs of currently-running blocks
        for my $b (grep { _is_running($_->{finished_at}) } @$blocks) {
            my $t = tail_log($id, $b->{block}, 14);
            next unless length $t;
            $body .= "<div class=muted>" . h($b->{block}) . " — live log:</div>"
                   . "<pre class=log>" . h($t) . "</pre>";
        }
    }

    # All runs table
    $body .= "<h2>Runs</h2><table><tr><th>run</th><th class=l>status</th><th class=l>started</th>"
           . "<th class=l>finished</th><th></th><th>blocks</th><th>pass</th><th>fail</th><th>err</th></tr>";
    for my $run (@$runs) {
        my $id = $run->{run_id};
        my $blocks = $db->block_rows($id);
        my %s; for my $b (@$blocks) { $s{$_} += $b->{$_}//0 for qw(total passed failed errored) }
        my $running = _is_running($run->{finished_at});
        my $badge = $running ? "<span class='b running'>running</span>"
                             : "<span class='b done'>done</span>";
        $body .= "<tr><td class=run><a href='/run?id=$id'>#$id</a></td><td class=l>$badge</td>"
               . "<td class=l>" . h($run->{started_at}) . "</td>"
               . "<td class=l>" . h($run->{finished_at} // '—') . "</td>"
               . "<td>" . _bar(\%s) . "</td>"
               . "<td>" . scalar(@$blocks) . "</td>"
               . "<td class=pass>" . ($s{passed}//0) . "</td>"
               . "<td class=fail>" . ($s{failed}//0) . "</td>"
               . "<td class=err>"  . ($s{errored}//0) . "</td></tr>";
    }
    $body .= "</table>";
    $body .= "<p class=muted>" . scalar(@$runs) . " runs · db $DBPATH</p>";

    return _layout('regress runs', ($any_running ? 5 : 30), $body);
}

sub page_run {
    my ($db, $id) = @_;
    $id =~ s/\D//g if defined $id;
    return _layout('run', 0, "<p>bad run id</p>") unless $id;
    my $run = $db->run_meta($id) or return _layout('run', 0, "<p>no run #$id</p>");
    my $running = _is_running($run->{finished_at});

    my $body = "<h2>Run #$id " . ($running ? "<span class='b running'>in progress</span>"
                                            : "<span class='b done'>done</span>") . "</h2>";
    $body .= "<div class=muted>started " . h($run->{started_at})
           . " · finished " . h($run->{finished_at} // '—')
           . " · " . h($run->{notes} // '') . "</div>";

    # repo SHAs
    my $shas = $db->repo_shas_for($id);
    if (@$shas) {
        $body .= "<table><tr><th>repo/tool</th><th>sha</th><th>version</th></tr>";
        for my $s (@$shas) {
            $body .= "<tr><td>" . h($s->{repo}) . "</td><td class=muted>"
                   . h(substr($s->{sha} // '', 0, 12)) . "</td><td class=muted>"
                   . h($s->{version} // '') . "</td></tr>";
        }
        $body .= "</table>";
    }

    my $blocks = $db->block_rows($id);
    $body .= "<table><tr><th>block</th><th class=l>engine</th><th class=l>status</th><th>exit</th>"
           . "<th>total</th><th>pass</th><th>fail</th><th>skip</th><th>ni</th><th>xf</th><th>err</th><th></th></tr>";
    for my $b (@$blocks) {
        my $br = _is_running($b->{finished_at});
        my $badge = $br ? "<span class='b running'>running</span>" : "<span class='b done'>done</span>";
        $body .= "<tr><td><a href='/block?run=$id&block=" . h($b->{block}) . "'>" . h($b->{block})
               . "</a></td><td class=l>" . h($b->{engine}) . "</td><td class=l>$badge</td>"
               . "<td>" . (defined $b->{exit_code} ? $b->{exit_code} : '—') . "</td>"
               . _counts_cells($b, $id, $b->{block}) . "<td>" . _bar($b) . "</td></tr>";
    }
    $body .= "</table>";
    return _layout("run #$id", ($running ? 5 : 0), $body);
}

sub page_block {
    my ($db, $id, $block, $status) = @_;
    $id =~ s/\D//g if defined $id;
    return _layout('block', 0, "<p>bad args</p>") unless $id && defined $block;
    my $rows = $db->results_for($id, $block, $status);

    my %by; $by{$_->{status}}++ for @{ $db->results_for($id, $block) };
    my $filt = "<div class=muted>filter: <a href='/block?run=$id&block=" . h($block) . "'>all</a>";
    for my $st (sort keys %by) {
        $filt .= " · <a href='/block?run=$id&block=" . h($block) . "&status=$st'>"
               . "$st ($by{$st})</a>";
    }
    $filt .= "</div>";

    my $ops = $db->opinions_for_block($block);

    my $body = "<h2><a href='/run?id=$id'>Run #$id</a> / " . h($block) . "</h2>$filt";
    $body .= "<table><tr><th>test</th><th class=l>status</th><th>ms</th>"
           . "<th class=l>triage</th><th class=l>message</th></tr>";
    for my $r (@$rows) {
        my $cls = $r->{status} eq 'pass' ? 'pass' : $r->{status} eq 'fail' ? 'fail'
                : $r->{status} eq 'error' ? 'err' : 'skip';
        # test name -> detail page (log + triage). Pass status so the page can
        # send the user back to this filtered view.
        my $url = "/test?run=$id&block=" . _urlenc($block) . "&test=" . _urlenc($r->{test_name});
        my $name = "<a href='$url'>" . h($r->{test_name}) . "</a>";
        $body .= "<tr><td>$name</td>"
               . "<td class='$cls l'>" . h($r->{status}) . "</td>"
               . "<td>" . (defined $r->{duration_ms} ? $r->{duration_ms} : '') . "</td>"
               . "<td class=l>" . (_disp_badge($ops->{$r->{test_name}}) || '') . "</td>"
               . "<td class=muted style='text-align:left'>" . h($r->{message} // '') . "</td></tr>";
    }
    $body .= "</table><p class=muted>" . scalar(@$rows) . " rows</p>";
    return _layout("run #$id / $block", 0, $body);
}

# roots that /file may serve: harness logs + the suites' test-source trees.
sub _serve_roots {
    my $r = src_root();
    return ("$REGRESS_DIR/out", "$r/iverilog/ivtest", "$r/nvc/test",
            "$r/sv-tests/tests");
}

# serve a captured log/result/source file, but only if it resolves under one
# of the allowed roots — prevents path traversal to arbitrary files.
sub serve_file {
    my $p = shift;
    return "no path\n" unless defined $p && length $p;
    require Cwd;
    my $real = Cwd::abs_path($p);
    return "not found\n" unless defined $real;
    for my $root (_serve_roots()) {
        my $r = Cwd::abs_path($root) or next;
        if ($real eq $r || index($real, "$r/") == 0) {
            open my $fh, '<', $real or return "cannot open\n";
            local $/; my $c = <$fh>; close $fh;
            return $c // '';
        }
    }
    return "denied (outside allowed roots)\n";
}

# resolve the test's source file(s) for the given block.
sub source_paths {
    my ($block, $test) = @_;
    my $r = src_root();
    my @cand;
    if ($block =~ m{^ivtest/}) {
        my $s = _ivtest_src("$r/iverilog/ivtest", $test);
        push @cand, $s if $s;
    } elsif ($block =~ m{^nvc/}) {
        push @cand, "$r/nvc/test/regress/$test.$_" for qw(vhd v);
    } elsif ($block =~ m{^sv-tests/}) {
        my $p = "$r/sv-tests/tests/$test";
        $p .= '.sv' unless $p =~ /\.sv$/;
        push @cand, $p;
    }
    return [ grep { -f $_ } @cand ];
}

# look up an ivtest test's source via the regress lists (old-style: dir/<name>;
# json-style: the .json's "source" relative to ivltests/).
sub _ivtest_src {
    my ($iv, $test) = @_;
    for my $lf (glob("$iv/regress-*.list $iv/*_regress.list")) {
        open my $fh, '<', $lf or next;
        while (my $l = <$fh>) {
            next if $l =~ /^\s*#/;
            my @f = split ' ', $l;
            next unless @f >= 2 && $f[0] eq $test;
            if ($f[1] =~ /\.json$/) {
                my $src = _json_source("$iv/$f[1]");
                return "$iv/ivltests/$src" if $src && -f "$iv/ivltests/$src";
            } else {
                my $dir = $f[2] // 'ivltests';
                for my $e (qw(v sv vhd)) { return "$iv/$dir/$test.$e" if -f "$iv/$dir/$test.$e" }
            }
        }
    }
    for my $e (qw(v sv vhd)) { return "$iv/ivltests/$test.$e" if -f "$iv/ivltests/$test.$e" }
    return undef;
}

sub _json_source {
    my $j = shift;
    open my $fh, '<', $j or return undef;
    local $/; my $c = <$fh>; close $fh;
    return $c =~ /"source"\s*:\s*"([^"]+)"/ ? $1 : undef;
}

# per-test detail: verdict + triage opinion form + the relevant log slice
sub page_test {
    my ($db, $id, $block, $test) = @_;
    $id =~ s/\D//g if defined $id;
    return _layout('test', 0, "<p>bad args</p>")
        unless $id && defined $block && defined $test;
    my $r  = $db->result_row($id, $block, $test);
    my $op = $db->get_opinion($block, $test);
    my $back = "/block?run=$id&block=" . _urlenc($block);

    my $st  = $r ? $r->{status} : '(no record)';
    my $cls = !$r ? 'muted' : $st eq 'pass' ? 'pass' : $st eq 'fail' ? 'fail'
            : $st eq 'error' ? 'err' : 'skip';

    my $body = "<h2><a href='/run?id=$id'>Run #$id</a> / "
             . "<a href='" . h($back) . "'>" . h($block) . "</a> / " . h($test) . "</h2>";
    $body .= "<p>status: <span class='$cls'>" . h($st) . "</span>";
    $body .= " &nbsp; <span class=muted>" . h($r->{message}) . "</span>" if $r && $r->{message};
    $body .= "</p>";

    # history: last known pass (build/options/runtime) + failing-streak start
    my $ts = $db->get_test_status($block, $test);
    $body .= "<h3>History</h3>";
    if ($ts && $ts->{last_pass_run_id}) {
        $body .= "<p>last passed: <b>" . h($ts->{last_pass_at}) . "</b>"
               . " (<a href='/run?id=$ts->{last_pass_run_id}'>run #$ts->{last_pass_run_id}</a>)";
        $body .= " · " . h($ts->{last_pass_ms}) . " ms" if defined $ts->{last_pass_ms};
        $body .= "<br><span class=muted>sims: " . h($ts->{last_pass_sims} // '?')
               . "<br>opts: " . h($ts->{last_pass_options} // '?') . "</span></p>";
    } else {
        $body .= "<p class=muted>no recorded pass</p>";
    }
    if ($ts && $ts->{fail_since_run_id}) {
        $body .= "<p class=fail>failing since " . h($ts->{fail_since_at})
               . " (<a href='/run?id=$ts->{fail_since_run_id}'>run #$ts->{fail_since_run_id}</a>)</p>";
    }

    $body .= "<h3>Triage</h3>";
    $body .= "<p>current: " . (_disp_badge($op) || "<span class=muted>none</span>");
    $body .= " <span class=muted>(by " . h($op->{author}) . " " . h($op->{updated_at}) . ")</span>"
        if $op && $op->{disposition};
    $body .= "</p>";
    $body .= _opinion_form($id, $block, $test, $op, $back);

    $body .= "<h3>Log</h3>";
    if ($r && $r->{log_path}) {
        $body .= "<div class=muted>" . h($r->{log_path})
               . " · <a href='/file?path=" . _urlenc($r->{log_path}) . "' target=_blank>raw</a></div>";
        $body .= "<pre class=log>" . h(log_slice($r->{log_path}, $test)) . "</pre>";
    } else {
        $body .= "<p class=muted>no log recorded for this test</p>";
    }

    # test source at the bottom
    my $srcs = source_paths($block, $test);
    if (@$srcs) {
        $body .= "<h3>Test source</h3>";
        for my $sp (@$srcs) {
            $body .= "<div class=muted>" . h($sp)
                   . " · <a href='/file?path=" . _urlenc($sp) . "' target=_blank>raw</a></div>";
            $body .= "<pre class=srcfill>" . h(serve_file($sp)) . "</pre>";
        }
    }
    return _layout("$block / $test", 0, $body);
}

# return the portion of a log relevant to one test: the whole file if small,
# else the lines mentioning the test (with context) — handles big block logs.
sub log_slice {
    my ($path, $test) = @_;
    my $c = serve_file($path);
    return $c if $c =~ /\A(?:no path|not found|denied|cannot open)/;
    my @lines = split /\n/, $c, -1;
    return $c if @lines <= 300;
    my @hit = grep { index($lines[$_], $test) >= 0 } 0 .. $#lines;
    unless (@hit) {
        my $from = @lines > 200 ? $#lines - 199 : 0;
        return "(" . scalar(@lines) . "-line log; no lines mention '$test' — showing tail)\n"
             . join("\n", @lines[$from .. $#lines]);
    }
    my %want; for my $i (@hit) { $want{$_} = 1 for ($i - 3 .. $i + 3) }
    my @out = map { $lines[$_] } grep { $_ >= 0 && $_ <= $#lines } sort { $a <=> $b } keys %want;
    return "(showing lines mentioning '$test' in a " . scalar(@lines) . "-line block log)\n"
         . join("\n", @out);
}

sub _gate_active { my $s = shift // ''; $s !~ /^(?:done|failed)$/ }

sub page_gates {
    my $db = shift;
    my $gates = $db->all_gates;
    my $any = grep { _gate_active($_->{state}) } @$gates;
    my $body = "<h2>CI gates</h2>"
       . "<p class=muted>laptop flow: push a branch to origin, then POST /gate "
       . "(repo, ref, push) — or use the delegate client. This box fetches, builds, "
       . "runs the repo's regressions, and fast-forwards origin/main if clean.</p>";
    $body .= "<table><tr><th>gate</th><th class=l>state</th><th class=l>repo</th>"
           . "<th class=l>ref</th><th class=l>sha</th><th class=l>result</th>"
           . "<th>run</th><th class=l>created</th></tr>";
    for my $g (@$gates) {
        my $st = $g->{state} // '';
        my $badge = _gate_active($st) ? "<span class='b running'>" . h($st) . "</span>"
                  : $st eq 'failed'   ? "<span class='b' style='background:#3a1518;color:#ff8a8a'>failed</span>"
                  :                     "<span class='b done'>done</span>";
        my $result = !defined $g->{clean} ? ''
                   : $g->{clean} ? ($g->{pushed} ? "<span class=pass>clean · pushed</span>"
                                                 : "<span class=pass>clean</span>")
                   :              "<span class=fail>regressions</span>";
        $body .= "<tr><td><a href='/gate?id=$g->{gate_id}'>#$g->{gate_id}</a></td>"
               . "<td class=l>$badge</td><td class=l>" . h($g->{repo}) . "</td>"
               . "<td class=l>" . h($g->{ref}) . "</td>"
               . "<td class=l muted>" . h(substr($g->{sha} // '', 0, 9)) . "</td>"
               . "<td class=l>$result</td>"
               . "<td>" . ($g->{run_id} ? "<a href='/run?id=$g->{run_id}'>#$g->{run_id}</a>" : '—') . "</td>"
               . "<td class=l muted>" . h($g->{created_at}) . "</td></tr>";
    }
    $body .= "</table>";
    return _layout('gates', ($any ? 5 : 0), $body);
}

sub page_gate {
    my ($db, $id) = @_;
    $id =~ s/\D//g if defined $id;
    return _layout('gate', 0, "<p>bad id</p>") unless $id;
    my $g = $db->get_gate($id) or return _layout('gate', 0, "<p>no gate #$id</p>");
    my $active = _gate_active($g->{state});
    my $body = "<h2>Gate #$id — " . h($g->{repo}) . " @ " . h($g->{ref})
             . " <span class='b " . ($active ? 'running' : 'done') . "'>" . h($g->{state}) . "</span></h2>";
    $body .= "<table>";
    $body .= "<tr><th>sha</th><td class=l>" . h($g->{sha} // '—') . "</td></tr>";
    $body .= "<tr><th>push?</th><td class=l>" . ($g->{do_push} ? 'yes' : 'no') . "</td></tr>";
    $body .= "<tr><th>result</th><td class=l>" . (defined $g->{clean}
              ? ($g->{clean} ? 'CLEAN' : 'REGRESSIONS') . ($g->{pushed} ? ' · pushed to origin/main' : '')
              : '(running)') . "</td></tr>";
    $body .= "<tr><th>run</th><td class=l>" . ($g->{run_id}
              ? "<a href='/run?id=$g->{run_id}'>#$g->{run_id}</a>" : '—') . "</td></tr>";
    $body .= "<tr><th>created</th><td class=l>" . h($g->{created_at}) . "</td></tr>";
    $body .= "<tr><th>finished</th><td class=l>" . h($g->{finished_at} // '—') . "</td></tr>";
    $body .= "<tr><th>message</th><td class=l>" . h($g->{message} // '') . "</td></tr>";
    $body .= "</table>";
    # tail the worker log
    my $wl = "$REGRESS_DIR/out/gate-$id/worker.log";
    if (-f $wl) {
        my $t = tail_log_file($wl, 40);
        $body .= "<h3>worker log</h3><pre class=log>" . h($t) . "</pre>" if length $t;
    }
    return _layout("gate #$id", ($active ? 5 : 0), $body);
}

# tail an absolute log file (path-guarded to the out/ dir)
sub tail_log_file {
    my ($f, $n) = @_;
    my $c = serve_file($f);
    return '' if $c =~ /\A(?:no path|not found|denied|cannot open)/;
    my @lines = split /\n/, $c;
    @lines = @lines[-$n .. -1] if @lines > $n;
    return join("\n", @lines);
}

# tail the on-disk log for a block (works for in-progress blocks, whose
# per-test rows aren't written until the block finishes)
sub tail_log {
    my ($id, $block, $n) = @_;
    $n ||= 200;
    return '' unless defined $id && defined $block;
    $id =~ s/\D//g;
    (my $safe = $block) =~ s{[/ ]}{_}g;
    my $f = "$REGRESS_DIR/out/run-$id/logs/$safe.log";
    open my $fh, '<', $f or return '';
    my @lines = <$fh>; close $fh;
    @lines = @lines[-$n .. -1] if @lines > $n;
    return join '', @lines;
}

1;
