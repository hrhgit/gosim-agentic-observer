"""Hidden final scoring: a card the team itself fails scores 0 and the final score stays the mean over all
cards E-H; platform failures still fail the evaluation and are rerun (migration 20261001000900)."""
import csv
import importlib.util
import secrets
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from test_project_database import database, identity, query, rpc, setup  # noqa: F401
from test_project_eval_ux import ORG, revision
from test_hidden_final import choose, hidden, materialize, online  # noqa: F401


@pytest.fixture
def final(hidden):
    """The hidden phase on four cards, the online phase over, one team with a materialized final version."""
    s = hidden; uri = s['uri']
    query(uri, """insert into private.observer_installations
        (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
        values(%s,'101',202,'303',%s,true) on conflict(organization) do update set enabled=true""", (ORG, 'a'*40))
    query(uri, 'update public.scenarios set slug=%s,name=%s where id=%s',
          ('card-e-' + str(s['hidden'])[:8], 'Card E', s['hidden_scenario']))
    cards = {'e': s['hidden_scenario']}
    for c in 'fgh':
        cards[c] = uuid.uuid4()
        query(uri, 'insert into public.scenarios(id,slug,name) values(%s,%s,%s)',
              (cards[c], f'card-{c}-' + str(s['hidden'])[:8], f'Card {c.upper()}'))
        query(uri, 'insert into public.phase_scenarios values(%s,%s)', (s['hidden'], cards[c]))
        query(uri, "insert into private.observer_scenario_bundles values(%s,%s,%s)", (cards[c], f'hidden/{c}.zip', 'd'*64))
    query(uri, "update public.observer_phase_settings set board_layout='cards_overall' where phase_id=%s", (s['hidden'],))
    query(uri, 'update public.teams set is_hidden=false')
    rev = revision(s); materialize(s, rev); choose(s, rev)
    query(uri, "update public.phases set ends_at=now()-interval '1 second' where id=%s", (s['phase'],))
    return {**s, 'cards': cards}


def start(s, team=None, **flags):
    return query(s['uri'], 'select private.observer_run_hidden_final(%s,%s,%s,true,false,%s,%s)',
                 (s['phase'], s['hidden'], team or s['team'], flags.get('platform', False),
                  flags.get('participant', False)))[0][0]['teams'][0]


def run_id(s, batch, card):
    return query(s['uri'], 'select id from public.observer_runs where batch_id=%s and scenario_id=%s',
                 (batch, s['cards'][card]))[0][0]


def job(s, run, kind):
    uri = s['uri']
    j, nonce = uuid.uuid4(), secrets.token_urlsafe(32)
    rpc(uri, 'observer_enqueue_job', j, kind, run, None, ORG, nonce, 'encrypted job payload', 'encrypted nonce')
    rpc(uri, 'observer_claim_job', j, nonce, '404', '1', '303', '101', 'a'*40)
    return j


def participant_fail(s, batch, card):
    """The colocated engine reports that the team's project failed while executing the card."""
    j = job(s, run_id(s, batch, card), 'engine')
    rpc(s['uri'], 'observer_finish_job', j, '404', '1',
        {'diagnostics': {'stage': 'execute', 'code': 'project_operation_failed', 'log': ''}}, 'engine_job_failed')


def score(s, batch, card, value):
    uri = s['uri']
    query(uri, "update public.observer_runs set status='scored',score=%s,finished_at=now() where id=%s",
          (value, run_id(s, batch, card)))
    query(uri, 'select private.observer_finalize_batch(%s)', (batch,))


def script(s, monkeypatch):
    """scripts/run-hidden-final.py with its management queries sent to the test database."""
    spec = importlib.util.spec_from_file_location('run_hidden_final', Path(__file__).resolve().parents[1]/'scripts/run-hidden-final.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

    def management_query(statement):
        with psycopg.connect(s['uri'], row_factory=dict_row) as conn:
            cur = conn.execute(statement)
            return cur.fetchall() if cur.description else []
    monkeypatch.setattr(module.deploy, 'query', management_query)
    return module


def batch_state(s, batch):
    return query(s['uri'], 'select status,score from public.observer_batches where id=%s', (batch,))[0]


def test_a_card_the_team_fails_scores_zero_and_the_other_cards_still_run(final):
    s = final; uri = s['uri']
    batch = start(s)['batch_id']
    participant_fail(s, batch, 'f')
    assert batch_state(s, batch) == ('queued', None)
    # The remaining cards are still handed to the dispatcher.
    pending = {p['id'] for p in rpc(uri, 'observer_pending_runs', 10)}
    assert {str(run_id(s, batch, c)) for c in 'egh'} <= pending
    for card, value in (('e', 80), ('g', 40)):
        score(s, batch, card, value)
    assert batch_state(s, batch)[0] == 'queued'
    score(s, batch, 'h', 60)
    assert batch_state(s, batch) == ('scored', 45.0)  # (80 + 0 + 40 + 60) / 4
    again = start(s, platform=True, participant=True)
    assert (again['action'], again['reason']) == ('skip', 'already_evaluated')


def test_every_card_failed_by_the_team_scores_zero(final):
    s = final
    batch = start(s)['batch_id']
    for card in 'efgh':
        participant_fail(s, batch, card)
    assert batch_state(s, batch) == ('scored', 0.0)


def test_a_platform_failure_still_fails_the_evaluation_and_is_rerun(final):
    s = final; uri = s['uri']
    batch = start(s)['batch_id']
    participant_fail(s, batch, 'e')
    score(s, batch, 'f', 50)
    query(uri, "update public.observer_runs set status='failed',error='evaluation_schedule_failed',finished_at=now() where id=%s",
          (run_id(s, batch, 'g'),))
    query(uri, 'select private.observer_finalize_batch(%s)', (batch,))
    assert batch_state(s, batch) == ('failed', None)
    skipped = start(s)
    assert (skipped['reason'], skipped['failure']) == ('failed_platform', 'platform')
    retried = start(s, platform=True)
    assert retried['action'] == 'created' and retried['previous_batch_id'] == batch
    assert query(uri, 'select status from public.observer_runs where id=%s', (run_id(s, batch, 'h'),)) == [('cancelled',)]


def test_an_evaluation_failed_only_by_the_team_needs_an_organizer_decision(final, monkeypatch):
    """Batches failed before this rule (the team's failure stopped them) are not rerun automatically."""
    s = final; uri = s['uri']
    batch = start(s)['batch_id']
    participant_fail(s, batch, 'e')
    query(uri, "update public.observer_batches set status='failed' where id=%s", (batch,))
    skipped = start(s, platform=True)
    assert (skipped['reason'], skipped['failure']) == ('failed_participant', 'participant')
    # Not scored: the organizer ranking shows no 0 for it.
    rows, cards, _ = script(s, monkeypatch).results(s['hidden'])
    assert rows[0]['unfinished'] == [] and 'rank' not in rows[0]
    assert start(s, platform=True, participant=True)['action'] == 'created'


def test_a_rejected_trace_scores_zero_without_failing_the_evaluation(final):
    s = final; uri = s['uri']
    batch = start(s)['batch_id']
    for card in 'efgh':
        query(uri, 'update public.observer_runs set score_summary=%s where id=%s',
              (Jsonb({'score': {'total': 40, 'components': {'sum_best_scores': 48}}, 'termination_reason': 'survey_complete'}),
               run_id(s, batch, card)))
        score(s, batch, card, 40)
    assert batch_state(s, batch) == ('scored', 40.0)
    run = run_id(s, batch, 'g')
    query(uri, "update public.observer_runs set score_check='pending' where id=%s", (run,))
    query(uri, "select private.observer_settle_score_check(%s,'rejected',null)", (job(s, run, 'score'),))
    assert query(uri, 'select status,error from public.observer_runs where id=%s', (run,)) == [('failed', 'score_verification_failed')]
    assert batch_state(s, batch) == ('scored', 30.0)
    # The board counts the voided card as 0 and never shows its old summary.
    query(uri, "update public.phases set leaderboard_mode='published' where id=%s", (s['hidden'],))
    overall = rpc(uri, 'observer_card_board', s['hidden'], None, 100, role='anon')
    row = overall['rows'][0]
    assert (row['total_score'], row['raw_total_score'], row['components']) == (30, 30, {'sum_best_scores': 36})
    slug_g = query(uri, 'select slug from public.scenarios where id=%s', (s['cards']['g'],))[0][0]
    assert row['unfinished_cards'] == [slug_g]
    card = rpc(uri, 'observer_card_board', s['hidden'], slug_g, 100, role='anon')['rows'][0]
    assert (card['total_score'], card['unfinished'], card['raw_total_score'], card['components'], card['termination_reason']) == (
        0, True, 0, {}, None)


def test_outside_the_hidden_final_the_first_failed_card_still_fails_the_evaluation(final):
    s = final; uri = s['uri']
    batch = start(s)['batch_id']
    query(uri, 'update public.observer_phase_settings set sealed=false where phase_id=%s', (s['hidden'],))
    participant_fail(s, batch, 'e')
    assert batch_state(s, batch) == ('failed', None)


def test_boards_and_organizer_results_show_the_zero(final, monkeypatch, capsys, tmp_path):
    s = final; uri = s['uri']
    other, other_team = identity(uri)
    o = {**s, 'user': other, 'team': other_team}
    query(uri, "update public.phases set ends_at=now()+interval '1 day' where id=%s", (s['phase'],))
    rev = revision(o); materialize(o, rev, other); choose(o, rev)
    query(uri, "update public.phases set ends_at=now()-interval '1 second' where id=%s", (s['phase'],))
    query(uri, 'update public.teams set is_hidden=false')
    mine, theirs = start(s)['batch_id'], start(o, team=other_team)['batch_id']
    participant_fail(s, mine, 'h')
    for card in 'efg':
        score(s, mine, card, 80)          # mean (80*3 + 0) / 4 = 60
    for card in 'efgh':
        score(o, theirs, card, 50)        # mean 50
    query(uri, "update public.phases set leaderboard_mode='published' where id=%s", (s['hidden'],))
    overall = rpc(uri, 'observer_card_board', s['hidden'], None, 100, role='anon')
    slug = {c['name'][-1].lower(): c['slug'] for c in overall['cards']}
    rows = {r['team_id']: r for r in overall['rows']}
    me = rows[str(s['team'])]
    assert (me['rank'], me['total_score'], me['card_scores'][slug['h']], me['unfinished_cards']) == (1, 60, 0, [slug['h']])
    assert (rows[str(other_team)]['rank'], rows[str(other_team)]['unfinished_cards']) == (2, [])
    card_h = rpc(uri, 'observer_card_board', s['hidden'], slug['h'], 100, role='anon')['rows']
    assert [(r['team_id'], r['total_score'], r['unfinished']) for r in card_h] == [
        (str(other_team), 50, False), (str(s['team']), 0, True)]

    source, target = (query(uri, 'select slug from public.phases where id=%s', (p,))[0][0] for p in (s['phase'], s['hidden']))
    path = tmp_path / 'ranking.csv'
    monkeypatch.setattr(sys, 'argv', ['run-hidden-final.py', '--source', source, '--target', target, '--results', '--csv', str(path)])
    script(s, monkeypatch).main()
    out = capsys.readouterr().out
    assert 'ranked=2' in out and '0.00*' in out and "failed because of the team's project" in out
    row = next(r for r in csv.DictReader(path.open(encoding='utf-8')) if r['team_id'] == str(s['team']))
    assert (row['rank'], row['mean'], row[slug['h']], row['unfinished_cards']) == ('1', '60.0', '0.0', slug['h'])
