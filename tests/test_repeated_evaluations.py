"""Repeated evaluations (migration 20261004030000): the hidden final averages repeat_runs evaluations of
each team's final version and starts each card's repeats together (20261004120000), teams can run the same
3-run average as a self-check (run one after another), and the online board marks each team's final version."""
import csv
import sys
import uuid

import psycopg
import pytest

from test_project_database import database, identity, query, rpc, setup  # noqa: F401
from test_project_eval_ux import revision
from test_hidden_final import choose, hidden, materialize, online, scored_batch  # noqa: F401
from test_hidden_final_zero import final, participant_fail, run_id, score, script  # noqa: F401


def apply(s, team=None, **flags):
    return query(s['uri'], 'select private.observer_run_hidden_final(%s,%s,%s,true,false,%s,false)',
                 (s['phase'], s['hidden'], team, flags.get('platform', False)))[0][0]


def pending_batches(s, teams):
    """The batches of these teams whose runs the dispatcher hands out now (it leases what it returns)."""
    ids = []
    for _ in range(100):
        got = [p['id'] for p in rpc(s['uri'], 'observer_pending_runs', 10)]
        if not got: break
        ids += got
    if not ids: return set()
    return {r[0] for r in query(s['uri'], 'select distinct r.batch_id from public.observer_runs r join public.observer_batches b'
                                ' on b.id=r.batch_id where r.id=any(%s::uuid[]) and b.team_id=any(%s::uuid[])', (ids, list(teams)))}


def release(s, batch):
    """The dispatcher's lease on the batch's runs is dropped, as if the runs were started elsewhere."""
    query(s['uri'], 'delete from private.observer_run_leases where run_id in (select id from public.observer_runs where batch_id=%s)', (batch,))


@pytest.fixture
def two_teams(final):
    """The hidden final with repeat_runs=3 and a second team with a final version."""
    s = final; uri = s['uri']
    query(uri, 'update public.observer_phase_settings set repeat_runs=3 where phase_id=%s', (s['hidden'],))
    other, other_team = identity(uri)
    o = {**s, 'user': other, 'team': other_team}
    query(uri, "update public.phases set ends_at=now()+interval '1 day' where id=%s", (s['phase'],))
    rev = revision(o); materialize(o, rev, other); choose(o, rev)
    query(uri, "update public.phases set ends_at=now()-interval '1 second' where id=%s", (s['phase'],))
    query(uri, 'update public.teams set is_hidden=false')
    return s, o


def leased(s):
    """Run ids the dispatcher hands out in one pass (cap 2 per pass here)."""
    return [p['id'] for p in rpc(s['uri'], 'observer_pending_runs', 2)]


def test_the_hidden_final_starts_each_cards_three_repeats_together(two_teams):
    s, o = two_teams; uri = s['uri']
    result = apply(s)
    assert (result['runs'], result['created']) == (3, 6)
    teams = {t['team_id']: t for t in result['teams']}
    assert {len(t['batch_ids']) for t in teams.values()} == {3}
    # Team by team; each card's three repeats adjacent in creation order.
    rows = query(uri, """select b.team_id, r.scenario_id from public.observer_runs r join public.observer_batches b on b.id=r.batch_id
        where b.phase_id=%s order by r.created_at, r.id""", (s['hidden'],))
    groups = [rows[i:i + 3] for i in range(0, len(rows), 3)]
    assert len(groups) == 8 and all(len({tuple(x) for x in g}) == 1 for g in groups)
    # A pass never splits a card's repeats, even past its cap: the first pass leases one whole card (3 runs).
    first = leased(s)
    assert len(first) == 3
    cards = query(uri, 'select distinct b.team_id, r.scenario_id from public.observer_runs r join public.observer_batches b'
                  ' on b.id=r.batch_id where r.id=any(%s::uuid[])', (first,))
    assert len(cards) == 1
    # Not serialized: every repeat of both teams is handed out without waiting for an earlier one.
    rest = []
    for _ in range(20):
        got = leased(s)
        if not got: break
        rest += got
    assert len(first) + len(rest) == 24
    # Creating again adds nothing.
    assert apply(s)['created'] == 0


def test_repeats_that_did_not_overlap_are_flagged_and_replaced_as_a_whole(two_teams, monkeypatch, capsys):
    s, o = two_teams; uri = s['uri']
    mine = {t['team_id']: t for t in apply(s, s['team'])['teams']}[str(s['team'])]['batch_ids']
    # Card E: repeat 3 starts only after repeat 1 finished (as if it had been rerun later).
    for i, batch in enumerate(mine):
        for card in 'efgh': score(s, batch, card, 50)
    t0 = '2026-10-08 00:00:00+00'
    query(uri, "update public.observer_runs set started_at=%s::timestamptz, finished_at=%s::timestamptz+interval '10 minutes'"
               " where batch_id=any(%s::uuid[])", (t0, t0, mine))
    assert query(uri, 'select private.observer_final_set_concurrent(%s,%s)', (s['team'], s['hidden'])) == [(True,)]
    query(uri, "update public.observer_runs set started_at=%s::timestamptz+interval '11 minutes' where id=%s", (t0, run_id(s, mine[2], 'e')))
    assert query(uri, 'select private.observer_final_set_concurrent(%s,%s)', (s['team'], s['hidden'])) == [(False,)]
    source, target = (query(uri, 'select slug from public.phases where id=%s', (p,))[0][0] for p in (s['phase'], s['hidden']))
    monkeypatch.setattr(sys, 'argv', ['run-hidden-final.py', '--source', source, '--target', target, '--results'])
    script(s, monkeypatch).main()
    out = capsys.readouterr().out
    assert 'not_concurrent' in out and 'ranked=0' in out
    skipped = apply(s, s['team'])['teams'][0]
    assert (skipped['reason'], skipped['failure']) == ('failed_not_concurrent', 'not_concurrent')
    replaced = apply(s, s['team'], platform=True)['teams'][0]
    assert (replaced['action'], len(replaced['batch_ids'])) == ('created', 3)
    assert query(uri, 'select count(*) from public.observer_batches where id=any(%s::uuid[]) and superseded_at is not null', (mine,)) == [(3,)]


def test_the_board_and_the_organizer_results_average_three_evaluations(two_teams, monkeypatch, capsys, tmp_path):
    s, o = two_teams; uri = s['uri']
    teams = {t['team_id']: t['batch_ids'] for t in apply(s)['teams']}
    mine, theirs = teams[str(s['team'])], teams[str(o['team'])]
    # Mine: E scores 90, 60, 60 (mean 70); F 50 each; G fails in the first evaluation (0), 30, 60 (mean 30); H 40.
    for i, batch in enumerate(mine):
        score(s, batch, 'e', [90, 60, 60][i]); score(s, batch, 'f', 50); score(s, batch, 'h', 40)
        if i == 0: participant_fail(s, batch, 'g')
        else: score(s, batch, 'g', [None, 30, 60][i])
    # Theirs: 50 on every card in two evaluations; the third is still running.
    for batch in theirs[:2]:
        for card in 'efgh': score(o, batch, card, 50)
    query(uri, "update public.phases set leaderboard_mode='published' where id=%s", (s['hidden'],))
    overall = rpc(uri, 'observer_card_board', s['hidden'], None, 100, role='anon')
    slug = {c['name'][-1].lower(): c['slug'] for c in overall['cards']}
    assert [r['team_id'] for r in overall['rows']] == [str(s['team'])]  # incomplete teams are not listed yet
    row = overall['rows'][0]
    assert row['averaged_runs'] == 3 and row['total_score'] == pytest.approx(47.5)  # (70 + 50 + 30 + 40) / 4
    assert {c: pytest.approx(v) for c, v in row['card_scores'].items()} == {slug['e']: 70, slug['f']: 50, slug['g']: 30, slug['h']: 40}
    assert row['unfinished_cards'] == [slug['g']] and row['observer_batch_id'] == mine[0]
    card_g = rpc(uri, 'observer_card_board', s['hidden'], slug['g'], 100, role='anon')['rows'][0]
    assert (card_g['total_score'], card_g['unfinished'], card_g['termination_reason']) == (pytest.approx(30), True, None)
    # The range of the averaged evaluations: overall (evaluation means 45, 45, 52.5), per card, and on a card tab.
    assert row['score_range'] == [pytest.approx(45), pytest.approx(52.5)]
    assert {c: [pytest.approx(v) for v in r] for c, r in row['card_ranges'].items()} == {
        slug['e']: [60, 90], slug['f']: [50, 50], slug['g']: [0, 60], slug['h']: [40, 40]}
    assert card_g['score_range'] == [pytest.approx(0), pytest.approx(60)]
    # An inactive phase (a rehearsal): organizers still see its board, nobody else does.
    query(uri, 'update public.phases set is_active=false where id=%s', (s['hidden'],))
    admin, _ = identity(uri)
    query(uri, 'update public.profiles set is_admin=true where id=%s', (admin,))
    assert rpc(uri, 'observer_card_board', s['hidden'], None, 100, role='anon')['rows'] == []
    assert [r['team_id'] for r in rpc(uri, 'observer_card_board', s['hidden'], None, 100, role='authenticated', user=admin)['rows']] == [str(s['team'])]
    query(uri, 'update public.phases set is_active=true where id=%s', (s['hidden'],))
    # Every evaluation stays stored.
    assert query(uri, "select count(*) from public.observer_batches where phase_id=%s and status='scored'", (s['hidden'],)) == [(5,)]

    for card in 'efgh': score(o, theirs[2], card, 80)  # mean 60 > 47.5
    rows = rpc(uri, 'observer_card_board', s['hidden'], None, 100, role='anon')['rows']
    assert [(r['team_id'], r['rank']) for r in rows] == [(str(o['team']), 1), (str(s['team']), 2)]

    source, target = (query(uri, 'select slug from public.phases where id=%s', (p,))[0][0] for p in (s['phase'], s['hidden']))
    path = tmp_path / 'ranking.csv'
    monkeypatch.setattr(sys, 'argv', ['run-hidden-final.py', '--source', source, '--target', target, '--results', '--csv', str(path)])
    script(s, monkeypatch).main()
    out = capsys.readouterr().out
    assert 'ranked=2' in out and '3/3' in out and '50.00-80.00' in out
    row = next(r for r in csv.DictReader(path.open(encoding='utf-8')) if r['team_id'] == str(s['team']))
    assert (row['rank'], float(row['mean']), float(row[slug['g']]), row['unfinished_cards'], row['evaluations']) == (
        '2', pytest.approx(47.5), pytest.approx(30), slug['g'], '3')


def test_a_platform_failure_replaces_all_three_repeats_never_averaged_as_zero(two_teams):
    s, o = two_teams; uri = s['uri']
    mine = {t['team_id']: t for t in apply(s)['teams']}[str(s['team'])]['batch_ids']
    for card in 'efgh': score(s, mine[0], card, 50)
    query(uri, "update public.observer_runs set status='failed',error='evaluation_schedule_failed',finished_at=now() where id=%s",
          (run_id(s, mine[1], 'e'),))
    query(uri, 'select private.observer_finalize_batch(%s)', (mine[1],))
    skipped = apply(s, s['team'])['teams'][0]
    assert (skipped['reason'], skipped['failure'], skipped['evaluations']) == ('failed_platform', 'platform', 2)
    # The whole set is replaced: no repeat runs after another repeat of its card finished.
    retried = apply(s, s['team'], platform=True)['teams'][0]
    assert (retried['action'], retried['new_evaluations'], len(retried['batch_ids'])) == ('created', 3, 3)
    assert query(uri, 'select status from public.observer_runs where id=%s', (run_id(s, mine[1], 'f'),)) == [('cancelled',)]
    assert query(uri, 'select count(*) from public.observer_batches where id=any(%s::uuid[]) and superseded_at is not null',
                 (mine,)) == [(3,)]
    # Replaced evaluations (even the scored one) never count; they stay stored.
    for card in 'efgh': score(s, mine[2], card, 99)
    for batch in retried['batch_ids']:
        for card in 'efgh': score(s, batch, card, 80)
    query(uri, "update public.phases set leaderboard_mode='published' where id=%s", (s['hidden'],))
    row = next(r for r in rpc(uri, 'observer_card_board', s['hidden'], None, 100, role='anon')['rows'] if r['team_id'] == str(s['team']))
    assert (row['averaged_runs'], row['total_score']) == (3, pytest.approx(80))
    assert apply(s, s['team'], platform=True)['teams'][0]['reason'] == 'already_evaluated'


def self_check(s, rev, confirm=False, user=None):
    return query(s['uri'], 'select public.observer_create_repeat_batches(%s,%s,%s)', (s['phase'], rev, confirm),
                 role='authenticated', user=user or s['user'])[0][0]


def test_self_check_uses_three_evaluations_runs_them_in_turn_and_the_board_keeps_the_best(online):
    s = online; uri = s['uri']
    query(uri, 'update public.observer_phase_settings set daily_batches=7 where phase_id=%s', (s['phase'],))
    rev = revision(s); materialize(s, rev)
    query(uri, "insert into private.observer_scenario_bundles values(%s,'public/test.zip',%s)", (s['scenario'], 'f'*64))
    started = self_check(s, rev)
    batches = [uuid.UUID(b) for b in started['batch_ids']]
    assert len(batches) == 3
    rows = query(uri, 'select repeat_group,repeat_runs,purpose,status from public.observer_batches where id=any(%s) order by created_at', (batches,))
    assert {r[0] for r in rows} == {uuid.UUID(started['repeat_group'])} and {r[1:] for r in rows} == {(3, 'formal', 'queued')}
    assert query(uri, 'select id from public.observer_batches where id=any(%s) order by created_at,id', (batches,)) == [(b,) for b in batches]
    assert next(q for q in rpc(uri, 'observer_evaluation_quota', role='authenticated', user=s['user'])
                if q['phase_id'] == str(s['phase']))['remaining'] == 4
    # One self-check set at a time; its evaluations run one after another.
    with pytest.raises(psycopg.Error, match='repeat_already_active'):
        self_check(s, rev, True)
    assert pending_batches(s, (s['team'],)) == {batches[0]}
    for value, batch in zip((30, 50, 40), batches):
        release(s, batch)
        query(uri, "update public.observer_runs set status='scored',score=%s,finished_at=now() where batch_id=%s", (value, batch))
        query(uri, 'select private.observer_finalize_batch(%s)', (batch,))
        nxt = batches[batches.index(batch) + 1:batches.index(batch) + 2]
        assert pending_batches(s, (s['team'],)) == set(nxt)
    # Fewer than three left today: refused, nothing created.
    query(uri, 'update public.observer_phase_settings set daily_batches=5 where phase_id=%s', (s['phase'],))
    with pytest.raises(psycopg.Error, match='repeat_daily_limit'):
        self_check(s, rev, True)
    assert query(uri, 'select count(*) from public.observer_batches where team_id=%s', (s['team'],)) == [(3,)]
    # Each evaluation is an ordinary one: the board keeps the best single one.
    query(uri, "update public.phases set leaderboard_mode='live' where id=%s", (s['phase'],))
    query(uri, "update public.observer_phase_settings set board_layout='cards_overall' where phase_id=%s", (s['phase'],))
    query(uri, 'update public.teams set is_hidden=false where id=%s', (s['team'],))
    row = rpc(uri, 'observer_card_board', s['phase'], None, 100, role='anon')['rows'][0]
    assert (row['total_score'], row['averaged_runs'], row['submission_count']) == (50, None, 3)
    with pytest.raises(psycopg.Error, match='permission denied'):
        query(uri, 'select public.observer_create_repeat_batches(%s,%s,false)', (s['phase'], rev), role='anon')


def test_the_online_board_marks_each_teams_final_version(online):
    s = online; uri = s['uri']
    query(uri, "update public.observer_phase_settings set board_layout='cards_overall' where phase_id=%s", (s['phase'],))
    query(uri, "update public.phases set leaderboard_mode='live' where id=%s", (s['phase'],))
    query(uri, 'update public.teams set is_hidden=false')
    first, second = revision(s), revision(s)
    scored_batch(s, first, 30)
    scored_batch(s, second, 70)
    scored_batch(s, first, 40)
    row = lambda slug=None: rpc(uri, 'observer_card_board', s['phase'], slug, 100, role='anon')['rows'][0]
    assert (row()['total_score'], row()['final_version']) == (70, {'chosen': False, 'score': None})
    choose(s, first)
    assert row()['final_version'] == {'chosen': True, 'score': 40}
    slug = query(uri, 'select slug from public.scenarios where id=%s', (s['scenario'],))[0][0]
    assert row(slug)['final_version'] == {'chosen': True, 'score': 40}
    choose(s, revision(s))  # chosen, never evaluated
    assert row()['final_version'] == {'chosen': True, 'score': None}
    # Not in practice phases.
    query(uri, 'update public.phases set counts_for_final=false where id=%s', (s['phase'],))
    assert row()['final_version'] is None


def test_a_team_runs_up_to_four_evaluations_side_by_side(online):
    """A/B comparisons: different versions evaluated at the same time; a self-check set counts once."""
    s = online; uri = s['uri']
    query(uri, 'update public.observer_phase_settings set daily_batches=40 where phase_id=%s', (s['phase'],))
    query(uri, "insert into private.observer_scenario_bundles values(%s,'public/test.zip',%s)", (s['scenario'], 'f'*64))
    a, b = revision(s), revision(s)
    materialize(s, a); materialize(s, b)
    create = lambda rev: query(uri, 'select public.observer_create_batch(%s,%s,true)', (s['phase'], rev),
                               role='authenticated', user=s['user'])[0][0]
    first, second = create(a), create(b)
    # Both are handed to the dispatcher at once (no per-team serialization outside a self-check set).
    assert pending_batches(s, (s['team'],)) == {first, second}
    group = self_check(s, a, True)                  # the third unit: three evaluations, counted once
    fourth = create(b)
    with pytest.raises(psycopg.Error, match='batch_already_active'):
        create(a)
    # The self-check's evaluations still run one after another; the fourth runs right away.
    assert pending_batches(s, (s['team'],)) == {uuid.UUID(group['batch_ids'][0]), fourth}
    # Configurable per phase.
    query(uri, 'update public.observer_phase_settings set max_active_evaluations=6 where phase_id=%s', (s['phase'],))
    create(a)
    # The organizers' sealed final evaluations never count toward the limit.
    assert query(uri, 'select private.observer_active_evaluations(%s)', (s['team'],)) == [(5,)]


def test_a_waiting_self_check_evaluation_never_expires_and_a_platform_failure_is_replaced(online):
    """Queue expiry counts from when a run can start (20261005010000); an expired or platform-failed
    evaluation of a self-check set is replaced automatically, so the set completes."""
    s = online; uri = s['uri']
    query(uri, 'update public.observer_phase_settings set daily_batches=10 where phase_id=%s', (s['phase'],))
    query(uri, "insert into private.observer_scenario_bundles values(%s,'public/test.zip',%s)", (s['scenario'], 'f'*64))
    rev = revision(s); materialize(s, rev)
    group = self_check(s, rev)
    first, second, third = [uuid.UUID(b) for b in group['batch_ids']]
    used = lambda: next(q for q in rpc(uri, 'observer_evaluation_quota', role='authenticated', user=s['user'])
                        if q['phase_id'] == str(s['phase']))['used']
    reconcile = lambda: rpc(uri, 'observer_reconcile_sessions')
    status = lambda b: query(uri, 'select status from public.observer_batches where id=%s', (b,))[0][0]
    # Created 50 minutes ago; the first finished long ago, the second is still running.
    query(uri, "update public.observer_runs set created_at=now()-interval '50 minutes' where batch_id=any(%s)", ([first, second, third],))
    query(uri, "update public.observer_runs set status='scored',score=10,finished_at=now()-interval '45 minutes' where batch_id=%s", (first,))
    query(uri, 'select private.observer_finalize_batch(%s)', (first,))
    query(uri, "update public.observer_batches set finished_at=now()-interval '45 minutes' where id=%s", (first,))
    query(uri, "update public.observer_runs set status='running' where batch_id=%s", (second,))
    query(uri, "update public.observer_batches set status='running' where id=%s", (second,))
    reconcile()
    assert status(third) == 'queued'
    # The second finished 10 minutes ago: the third could start only since then.
    query(uri, "update public.observer_runs set status='scored',score=20,finished_at=now()-interval '10 minutes' where batch_id=%s", (second,))
    query(uri, 'select private.observer_finalize_batch(%s)', (second,))
    query(uri, "update public.observer_batches set finished_at=now()-interval '10 minutes' where id=%s", (second,))
    reconcile()
    assert status(third) == 'queued'
    # A run the dispatcher is retrying (it has a lease) follows the lease budget, not this timer.
    query(uri, "update public.observer_batches set finished_at=now()-interval '40 minutes' where id=%s", (second,))
    run = query(uri, 'select id from public.observer_runs where batch_id=%s', (third,))[0][0]
    query(uri, "insert into private.observer_run_leases(run_id,lease,expires_at) values(%s,gen_random_uuid(),now()-interval '1 minute')", (run,))
    reconcile()
    assert status(third) == 'queued'
    # Never taken by the dispatcher 30 minutes after it could start: expired (platform), refunded and replaced.
    query(uri, 'delete from private.observer_run_leases where run_id=%s', (run,))
    reconcile()
    assert status(third) == 'failed'
    rows = query(uri, 'select id,status,revision_id,repeat_runs from public.observer_batches where repeat_group=%s order by created_at,id',
                 (group['repeat_group'],))
    assert len(rows) == 4 and rows[3][1:] == ('queued', rev, 3)
    assert used() == 3                         # the expired one is refunded, its replacement counts
    replacement = rows[3][0]
    assert pending_batches(s, (s['team'],)) == {replacement}
    # At most two replacements per set.
    for _ in range(3):
        last = query(uri, 'select id from public.observer_batches where repeat_group=%s order by created_at desc,id desc limit 1',
                     (group['repeat_group'],))[0][0]
        query(uri, 'delete from private.observer_run_leases where run_id in (select id from public.observer_runs where batch_id=%s)', (last,))
        query(uri, "update public.observer_runs set status='failed',error='evaluation_expired',finished_at=now() where batch_id=%s", (last,))
        query(uri, 'select private.observer_finalize_batch(%s)', (last,))
    assert query(uri, 'select count(*) from public.observer_batches where repeat_group=%s', (group['repeat_group'],)) == [(5,)]
    # An ordinary queued run that could start 31 minutes ago and was never taken still expires.
    other = query(uri, 'select public.observer_create_batch(%s,%s,true)', (s['phase'], rev), role='authenticated', user=s['user'])[0][0]
    query(uri, "update public.observer_runs set created_at=now()-interval '31 minutes' where batch_id=%s", (other,))
    reconcile()
    assert status(other) == 'failed'
