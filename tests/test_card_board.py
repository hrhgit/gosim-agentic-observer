"""Per-card boards (public.observer_card_board): one best complete batch per team,
the same visibility as observer_board, and no change for phases left on 'overall'."""
import uuid

from psycopg.types.json import Jsonb

from test_project_database import database, identity, query, rpc, setup  # noqa: F401


def card(uri, phase, slug, name):
    sid = uuid.uuid4()
    query(uri, "insert into public.scenarios(id,slug,name) values(%s,%s,%s)", (sid, slug, name))
    query(uri, 'insert into public.phase_scenarios values(%s,%s)', (phase, sid))
    return sid


def v4_summary(total, required_missing=1, targets=100):
    """The run summary the colocated v4 engine writes (observer-run-summary-v1, plan 'Protocol changes' 10)."""
    return {'schema_version': 'observer-run-summary-v1', 'gameplay': 'v4',
            'score': {'total': total, 'sum_best_scores': total + 10, 'required_penalty': -6,
                      'uniformity_penalty': -4, 'report_settlement': 0},
            'required_missing': required_missing, 'targets_observed': targets, 'observe_actions': 50,
            'invalidated_observations': 0, 'termination_reason': 'survey_complete', 'committed_action_count': 80,
            'accounted_wallclock_seconds': 812.5, 'seed_secret': 'never-returned'}


def v4_nested_summary(total):
    """The runner's score_report layout (components nested), also accepted."""
    return {'score': {'total': total, 'components': {'sum_best_scores': total + 1, 'required_penalty': -1}}}


def batch(uri, phase, user, scores, summary=v4_summary):
    """A scored batch; scores maps scenario id -> run score."""
    b = rpc(uri, 'observer_create_batch', phase, None, role='authenticated', user=user)
    for run, scenario in query(uri, 'select id,scenario_id from public.observer_runs where batch_id=%s', (b,)):
        value = scores[scenario]
        query(uri, "update public.observer_runs set status='scored',score=%s,score_summary=%s,result_path='github:private-evidence',"
                   "finished_at=now() where id=%s", (value, Jsonb(summary(value)), run))
    query(uri, 'select private.observer_finalize_batch(%s)', (b,))
    return b


def board(uri, phase, slug=None, role='anon', user=None):
    return rpc(uri, 'observer_card_board', phase, slug, 100, role=role, user=user)


def v4_phase(s, layout='cards_overall'):
    """The test phase with its default scenario replaced by cards A-D."""
    uri = s['uri']
    query(uri, 'delete from public.phase_scenarios where phase_id=%s', (s['phase'],))
    cards = {c: card(uri, s['phase'], f'v4-card-{c}-{str(s["phase"])[:8]}', f'Card {c.upper()}') for c in 'abcd'}
    query(uri, 'update public.observer_phase_settings set board_layout=%s where phase_id=%s',
          (layout, s['phase']))
    return cards


def test_overall_layout_returns_the_unchanged_observer_board(setup):
    s = setup; uri = s['uri']
    batch(uri, s['phase'], s['user'], {s['scenario']: 42},
          lambda v: {'score': {'total': v, 'base_science': v, 'penalties': {'x': 1}}, 'completed_tiles': 3})
    result = board(uri, s['phase'])
    assert result['layout'] == 'overall' and result['cards'] == [] and result['scenario'] is None
    assert result['rows'] == rpc(uri, 'observer_board', s['phase'], 100, role='anon')
    assert result['rows'][0]['total_score'] == 42


def test_cards_come_from_the_best_complete_batch(setup):
    s = setup; uri = s['uri']
    cards = v4_phase(s)
    a, b, c, d = (cards[k] for k in 'abcd')
    other, _ = identity(uri)
    first = batch(uri, s['phase'], s['user'], {a: 100, b: 0, c: 0, d: 0})           # mean 25, best A
    best = batch(uri, s['phase'], s['user'], {a: 40, b: 40, c: 40, d: 40})           # mean 40
    rival = batch(uri, s['phase'], other, {a: 90, b: 30, c: 10, d: 10})              # mean 35
    result = board(uri, s['phase'])
    assert result['layout'] == 'cards_overall'
    assert [x['name'] for x in result['cards']] == ['Card A', 'Card B', 'Card C', 'Card D']
    rows = result['rows']
    assert [(r['observer_batch_id'], r['rank'], r['total_score']) for r in rows] == [(str(best), 1, 40), (str(rival), 2, 35)]
    slug = {k: v['slug'] for k, v in zip('abcd', result['cards'])}
    assert rows[1]['card_scores'] == {slug['a']: 90, slug['b']: 30, slug['c']: 10, slug['d']: 10}
    assert rows[0]['submission_count'] == 2 and rows[0]['targets_observed'] == 100
    # One evaluation per team (repeat_runs=1): no range of averaged evaluations.
    assert rows[0]['score_range'] is None and rows[0]['card_ranges'] is None and rows[0]['averaged_runs'] is None
    assert rows[0]['components'] == {'sum_best_scores': 50, 'required_penalty': -6, 'uniformity_penalty': -4, 'report_settlement': 0}
    # Card A: the same batches, not the team's best A run (100 in `first`).
    per_card = board(uri, s['phase'], slug['a'])
    assert per_card['scenario'] == slug['a']
    assert [(r['observer_batch_id'], r['rank'], r['total_score'], r['overall_score'], r['overall_rank'])
            for r in per_card['rows']] == [(str(rival), 1, 90, 35, 2), (str(best), 2, 40, 40, 1)]
    assert per_card['rows'][0]['termination_reason'] == 'survey_complete' and per_card['rows'][0]['required_missing'] == 1
    text = str(result) + str(per_card)
    assert 'private-evidence' not in text and 'never-returned' not in text and str(first) not in text
    assert board(uri, s['phase'], 'no-such-card')['rows'] == []


def test_batches_from_an_earlier_scenario_set_are_ignored(setup):
    s = setup; uri = s['uri']
    batch(uri, s['phase'], s['user'], {s['scenario']: 999})       # v3-era batch on the old scenario
    cards = v4_phase(s, 'cards')
    assert board(uri, s['phase'])['rows'] == []
    batch(uri, s['phase'], s['user'], {sid: 10 for sid in cards.values()})
    rows = board(uri, s['phase'])['rows']
    assert len(rows) == 1 and rows[0]['total_score'] == 10 and rows[0]['submission_count'] == 2
    # The v3 RPC is untouched and still ranks the batch mean of every batch.
    assert rpc(uri, 'observer_board', s['phase'], 100, role='anon')[0]['total_score'] == 999


def test_hidden_teams_restricted_and_sealed_phases_follow_observer_board(setup):
    s = setup; uri = s['uri']
    cards = v4_phase(s)
    batch(uri, s['phase'], s['user'], {sid: 5 for sid in cards.values()})
    admin, _ = identity(uri)
    query(uri, 'update public.profiles set is_admin=true where id=%s', (admin,))
    query(uri, 'update public.teams set is_hidden=true where id=%s', (s['team'],))
    assert board(uri, s['phase'])['rows'] == []
    assert len(board(uri, s['phase'], role='authenticated', user=admin)['rows']) == 1
    query(uri, 'update public.teams set is_hidden=false where id=%s', (s['team'],))
    query(uri, "update public.phases set leaderboard_mode='frozen' where id=%s", (s['phase'],))
    assert board(uri, s['phase'])['rows'] == []
    query(uri, "update public.phases set leaderboard_mode='hidden' where id=%s", (s['phase'],))
    query(uri, 'update public.observer_phase_settings set sealed=true where phase_id=%s', (s['phase'],))
    sealed = board(uri, s['phase'])
    assert sealed == {'layout': 'cards_overall', 'cards': [], 'scenario': None, 'rows': []}
    assert board(uri, s['phase'], role='authenticated', user=s['user'])['rows'] == []
    slug = query(uri, 'select slug from public.scenarios where id=%s', (cards['a'],))[0][0]
    assert board(uri, s['phase'], slug)['rows'] == []
    assert len(board(uri, s['phase'], role='authenticated', user=admin)['cards']) == 4
    # Published: the cards, their names and both boards appear.
    query(uri, "update public.phases set leaderboard_mode='published' where id=%s", (s['phase'],))
    published = board(uri, s['phase'])
    assert [c['name'] for c in published['cards']] == ['Card A', 'Card B', 'Card C', 'Card D']
    assert published['rows'][0]['total_score'] == 5
    assert board(uri, s['phase'], slug)['rows'][0]['total_score'] == 5
    query(uri, 'update public.observer_phase_settings set access_team_id=%s where phase_id=%s', (s['team'], s['phase']))
    assert board(uri, s['phase'])['rows'] == []


def test_formal_card_names_stay_hidden_before_the_competition_opens(setup):
    s = setup; uri = s['uri']
    cards = v4_phase(s)
    query(uri, "update public.phases set counts_for_final=true,starts_at=now()+interval '1 day' where id=%s", (s['phase'],))
    result = board(uri, s['phase'])
    assert result['cards'] == [] and result['rows'] == []
    slug = query(uri, 'select slug from public.scenarios where id=%s', (cards['a'],))[0][0]
    assert board(uri, s['phase'], slug)['rows'] == []


def test_v3_score_summaries_still_fill_the_board_columns(setup):
    s = setup; uri = s['uri']
    second = card(uri, s['phase'], 'second-' + str(s['phase'])[:8], 'Second')
    query(uri, "update public.observer_phase_settings set board_layout='cards' where phase_id=%s", (s['phase'],))
    summary = lambda v: {'score': {'total': v, 'base_science': v + 5, 'program_bonus': 2, 'penalties': {'bad': 3}},
                         'completed_tiles': 4, 'required_missing': 2}
    batch(uri, s['phase'], s['user'], {s['scenario']: 60, second: 20}, summary)
    row = board(uri, s['phase'], 'second-' + str(s['phase'])[:8])['rows'][0]
    assert (row['total_score'], row['base_science'], row['penalty_total'], row['completed_tiles'], row['overall_score']) == (20, 25, 3, 4, 40)
    assert row['components'] == {'base_science': 25, 'program_bonus': 2}


def test_nested_component_layout_is_read_too(setup):
    s = setup; uri = s['uri']
    cards = v4_phase(s, 'cards')
    batch(uri, s['phase'], s['user'], {sid: 7 for sid in cards.values()}, v4_nested_summary)
    slug = query(uri, 'select slug from public.scenarios where id=%s', (cards['b'],))[0][0]
    assert board(uri, s['phase'], slug)['rows'][0]['components'] == {'sum_best_scores': 8, 'required_penalty': -1}
