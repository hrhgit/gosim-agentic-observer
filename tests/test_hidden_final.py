"""Final version choice, the sealed hidden final phase and the organizer-run final evaluation."""
import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from test_project_database import database, identity, query, rpc, setup  # noqa: F401
from test_project_eval_ux import revision


def as_user(s, statement, args=(), user=None):
    return query(s['uri'], statement, args, role='authenticated', user=user or s['user'])


def state(s, user=None):
    rows = rpc(s['uri'], 'observer_final_versions', role='authenticated', user=user or s['user'])
    return next((r for r in rows if r['phase_id'] == str(s['phase'])), None)


def choose(s, rev, user=None):
    return rpc(s['uri'], 'observer_set_final_version', s['phase'], rev, role='authenticated', user=user or s['user'])


def materialize(s, rev, user=None):
    ref = 'github:AGENTIC-OBSERVER26-runner-1/participant-' + (user or s['user']).hex + '@' + 'f' * 40
    query(s['uri'], 'insert into private.observer_materializations values(%s,%s,%s)', (rev, ref, 'a' * 64))


def scored_batch(s, rev, score):
    uri = s['uri']
    batch = rpc(uri, 'observer_create_batch', s['phase'], rev, True, role='authenticated', user=s['user'])
    query(uri, "update public.observer_runs set status='scored',score=%s,finished_at=now() where batch_id=%s", (score, batch))
    query(uri, 'select private.observer_finalize_batch(%s)', (batch,))
    return batch


@pytest.fixture
def online(setup):
    """An open formal phase (like 'online'): counts for final, no calibration, ends tomorrow."""
    s = setup
    query(s['uri'], "update public.phases set counts_for_final=true,starts_at=now()-interval '1 day',"
                    "ends_at=now()+interval '1 day' where id=%s", (s['phase'],))
    return s


@pytest.fixture
def hidden(online):
    """The sealed hidden final phase with one registered scenario bundle."""
    s = online; uri = s['uri']
    phase, scenario = uuid.uuid4(), uuid.uuid4()
    query(uri, "insert into public.phases(id,slug,name_en,name_zh,counts_for_final,leaderboard_mode,starts_at)"
               " values(%s,%s,'Final','决赛',true,'hidden',now()-interval '1 hour')", (phase, 'final-' + str(phase)[:8]))
    query(uri, """insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,daily_batches,sealed)
                  values(%s,true,false,1,true)""", (phase,))
    query(uri, "insert into public.scenarios(id,slug,name) values(%s,%s,'Hidden final')", (scenario, 'eval-final-' + str(scenario)[:8]))
    query(uri, 'insert into public.phase_scenarios values(%s,%s)', (phase, scenario))
    query(uri, "insert into private.observer_scenario_bundles values(%s,'hidden/final.zip',%s)", (scenario, 'e' * 64))
    return {**s, 'hidden': phase, 'hidden_scenario': scenario}


def test_final_version_defaults_to_best_batch_and_can_be_chosen_and_cleared(online):
    s = online; uri = s['uri']
    first, second = revision(s), revision(s)
    assert state(s)['revision_id'] is None and state(s)['source'] is None
    scored_batch(s, first, 10)
    best = scored_batch(s, second, 30)
    scored_batch(s, first, 20)
    st = state(s)
    assert (st['revision_id'], st['source'], st['best_batch_id'], st['best_score']) == (str(second), 'best', str(best), 30)
    teammate, _ = identity(uri, team=s['team'])
    chosen = choose(s, first, teammate)
    assert (chosen['revision_id'], chosen['source'], chosen['chosen_by']) == (str(first), 'chosen', str(teammate))
    assert state(s) == chosen and not chosen['locked']
    # An approved version that was never evaluated may be chosen too.
    fresh = revision(s)
    assert choose(s, fresh)['revision_id'] == str(fresh)
    with pytest.raises(psycopg.Error, match='revision_not_withdrawable'):
        rpc(uri, 'observer_withdraw_revision', fresh, role='authenticated', user=s['user'])
    cleared = choose(s, None)
    assert (cleared['revision_id'], cleared['source']) == (str(second), 'best')
    rpc(uri, 'observer_withdraw_revision', fresh, role='authenticated', user=s['user'])
    assert query(uri, "select count(*) from public.audit_log where action='observer.final_version'")[0][0] >= 3


def test_only_the_teams_confirmed_versions_can_be_chosen(online):
    s = online; uri = s['uri']
    outsider, _ = identity(uri)
    mine = revision(s)
    with pytest.raises(psycopg.Error, match='revision_not_found'):
        choose(s, mine, outsider)
    unconfirmed = revision(s, approve=False)
    with pytest.raises(psycopg.Error, match='revision_not_approved'):
        choose(s, unconfirmed)
    withdrawn = revision(s)
    rpc(uri, 'observer_withdraw_revision', withdrawn, role='authenticated', user=s['user'])
    with pytest.raises(psycopg.Error, match='revision_withdrawn'):
        choose(s, withdrawn)
    # Nothing to choose in a non-final phase, nor as an anonymous or banned user.
    query(uri, 'update public.phases set counts_for_final=false where id=%s', (s['phase'],))
    assert state(s) is None
    with pytest.raises(psycopg.Error, match='final_phase_invalid'):
        choose(s, mine)
    query(uri, 'update public.phases set counts_for_final=true where id=%s', (s['phase'],))
    with pytest.raises(psycopg.Error, match='permission denied'):
        rpc(uri, 'observer_set_final_version', s['phase'], mine, role='anon')
    with pytest.raises(psycopg.Error, match='permission denied'):
        as_user(s, 'select * from private.observer_final_versions')
    query(uri, 'update public.profiles set is_banned=true where id=%s', (s['user'],))
    with pytest.raises(psycopg.Error, match='banned'):
        choose(s, mine)


def test_the_choice_locks_when_the_phase_ends(online):
    s = online; uri = s['uri']
    first, second = revision(s), revision(s)
    choose(s, first)
    query(uri, "update public.phases set ends_at=now()-interval '1 second' where id=%s", (s['phase'],))
    st = state(s)
    assert st['locked'] and st['revision_id'] == str(first)
    for rev in (second, None):
        with pytest.raises(psycopg.Error, match='final_version_locked'):
            choose(s, rev)
    with pytest.raises(psycopg.Error, match='revision_not_withdrawable'):
        rpc(uri, 'observer_withdraw_revision', first, role='authenticated', user=s['user'])
    assert state(s)['revision_id'] == str(first)


def test_admin_sees_every_teams_final_version(online):
    s = online; uri = s['uri']
    rev = revision(s); choose(s, rev)
    with pytest.raises(psycopg.Error, match='forbidden'):
        rpc(uri, 'observer_admin_final_versions', s['phase'], role='authenticated', user=s['user'])
    admin, _ = identity(uri)
    query(uri, 'update public.profiles set is_admin=true where id=%s', (admin,))
    rows = rpc(uri, 'observer_admin_final_versions', s['phase'], role='authenticated', user=admin)
    row = next(r for r in rows if r['team_id'] == str(s['team']))
    assert (row['revision_id'], row['source'], row['project_title'], row['model_mode']) == (str(rev), 'chosen', 'Agent', 'stored')


def test_sealed_phase_is_invisible_to_participants_until_published(hidden):
    s = hidden; uri = s['uri']
    admin, _ = identity(uri, team=s['team'])  # an organizer who is also a member
    query(uri, 'update public.profiles set is_admin=true where id=%s', (admin,))
    rev = revision(s)
    materialize(s, rev)
    choose(s, rev)
    query(uri, "update public.phases set ends_at=now()-interval '1 second' where id=%s", (s['phase'],))
    choose_locked = rpc(uri, 'observer_final_versions', role='authenticated', user=s['user'])
    assert all(r['phase_id'] != str(s['hidden']) for r in choose_locked)
    # Participants cannot evaluate in a sealed phase, even knowing its ID.
    with pytest.raises(psycopg.Error, match='phase_closed'):
        rpc(uri, 'observer_create_batch', s['hidden'], rev, True, role='authenticated', user=s['user'])
    with pytest.raises(psycopg.Error, match='phase_closed'):
        rpc(uri, 'observer_create_batch', s['hidden'], rev, True, role='authenticated', user=admin)
    result = query(uri, 'select private.observer_run_hidden_final(%s,%s,%s,true)', (s['phase'], s['hidden'], s['team']))[0][0]
    batch = result['teams'][0]['batch_id']
    run = query(uri, 'select id from public.observer_runs where batch_id=%s', (batch,))[0][0]
    query(uri, "update public.observer_runs set status='scored',score=42,result_path='github:org/result@x',finished_at=now() where id=%s", (run,))
    query(uri, 'select private.observer_finalize_batch(%s)', (batch,))

    def participant_view(user):
        return {
            'phase': as_user(s, 'select id from public.phases where id=%s', (s['hidden'],), user),
            'settings': as_user(s, 'select phase_id from public.observer_phase_settings where phase_id=%s', (s['hidden'],), user),
            'links': as_user(s, 'select scenario_id from public.phase_scenarios where phase_id=%s', (s['hidden'],), user),
            'scenario': as_user(s, 'select slug from public.scenarios where id=%s', (s['hidden_scenario'],), user),
            'batch': as_user(s, 'select score from public.observer_batches where id=%s', (batch,), user),
            'run': as_user(s, 'select result_path from public.observer_runs where id=%s', (run,), user),
            'quota': [q for q in rpc(uri, 'observer_evaluation_quota', role='authenticated', user=user) if q['phase_id'] == str(s['hidden'])],
            'board': rpc(uri, 'observer_board', s['hidden'], 10, role='authenticated', user=user),
        }
    sealed = participant_view(s['user'])
    assert all(not v for v in sealed.values()), sealed
    assert rpc(uri, 'observer_board', s['hidden'], 10, role='anon') == []
    with pytest.raises(psycopg.Error, match='diagnostics_not_found'):
        rpc(uri, 'observer_diagnostics', None, run, role='authenticated', user=s['user'])
    # Organizers see everything, including the board, while it is sealed.
    staff = participant_view(admin)
    assert staff['phase'] and staff['scenario'] and staff['batch'] == [(42.0,)] and staff['board'][0]['total_score'] == 42
    # Frozen or live is not enough; only publication opens it.
    for mode in ('live', 'frozen'):
        query(uri, 'update public.phases set leaderboard_mode=%s where id=%s', (mode, s['hidden']))
        assert not participant_view(s['user'])['batch']
    query(uri, "update public.phases set leaderboard_mode='published' where id=%s", (s['hidden'],))
    published = participant_view(s['user'])
    assert published['phase'] and published['scenario'] and published['batch'] == [(42.0,)]
    assert published['run'] == [('github:org/result@x',)] and published['board'][0]['total_score'] == 42
    assert rpc(uri, 'observer_diagnostics', None, run, role='authenticated', user=s['user']) == []
    # Source files of the hidden scenario stay private even after publication.
    slug = query(uri, 'select slug from public.scenarios where id=%s', (s['hidden_scenario'],))[0][0]
    assert query(uri, 'select public.observer_formal_source(%s)', (slug,)) == [(True,)]
    query(uri, 'update public.observer_phase_settings set daily_batches=10 where phase_id=%s', (s['hidden'],))
    with pytest.raises(psycopg.Error, match='phase_closed'):
        rpc(uri, 'observer_create_batch', s['hidden'], rev, True, role='authenticated', user=s['user'])


def test_sealed_phase_does_not_keep_project_uploads_open(hidden):
    s = hidden; uri = s['uri']
    query(uri, "update public.phases set ends_at=now()-interval '1 second' where id=%s", (s['phase'],))
    query(uri, 'update public.observer_phase_settings set projects_enabled=false where phase_id<>%s', (s['hidden'],))
    try:
        with pytest.raises(psycopg.Error, match='projects_not_enabled'):
            rpc(uri, 'observer_create_project', 'Late', 'repository', 'https://github.com/example/late',
                role='authenticated', user=s['user'])
    finally:
        query(uri, 'update public.observer_phase_settings set projects_enabled=true where phase_id<>%s', (s['hidden'],))


def test_hidden_final_run_creates_one_batch_per_team_for_its_final_version(hidden):
    s = hidden; uri = s['uri']
    run = lambda *a: query(uri, 'select private.observer_run_hidden_final(%s,%s,%s,%s,%s,%s)', a)[0][0]
    chosen, best = revision(s), revision(s)
    scored_batch(s, best, 50)
    choose(s, chosen)
    other, other_team = identity(uri)
    other_s = {**s, 'user': other, 'team': other_team}
    other_best = revision(other_s)
    scored_batch(other_s, other_best, 60)
    idle, _ = identity(uri)  # no version: not listed
    materialize(s, chosen)
    materialize(s, other_best, other)
    # Refused before the online phase ends, except for one explicitly named test team.
    with pytest.raises(psycopg.Error, match='source_phase_not_finished'):
        run(s['phase'], s['hidden'], None, False, False, False)
    with pytest.raises(psycopg.Error, match='source_phase_not_finished'):
        run(s['phase'], s['hidden'], None, True, True, False)
    early = run(s['phase'], s['hidden'], s['team'], False, True, False)
    assert [t['action'] for t in early['teams']] == ['would_create']
    query(uri, "update public.phases set ends_at=now()-interval '1 second' where id=%s", (s['phase'],))
    dry = run(s['phase'], s['hidden'], None, False, False, False)
    plan = {t['team_id']: t for t in dry['teams']}
    assert dry['frozen'] and dry['created'] == 0 and str(idle) not in str(dry)
    assert (plan[str(s['team'])]['revision_id'], plan[str(s['team'])]['source']) == (str(chosen), 'chosen')
    assert (plan[str(other_team)]['revision_id'], plan[str(other_team)]['source']) == (str(other_best), 'best')
    assert query(uri, 'select count(*) from public.observer_batches where phase_id=%s', (s['hidden'],)) == [(0,)]
    # Daily limit 1 in the hidden phase and a used-up day do not matter.
    applied = run(s['phase'], s['hidden'], None, True, False, False)
    assert applied['created'] == 2
    rows = query(uri, """select b.team_id,b.revision_id,b.purpose,b.mode,b.status,count(r.id) from public.observer_batches b
        join public.observer_runs r on r.batch_id=b.id where b.phase_id=%s group by b.id order by b.team_id""", (s['hidden'],))
    assert sorted((r[0], r[1]) for r in rows) == sorted([(s['team'], chosen), (other_team, other_best)])
    assert all(r[2:] == ('formal', 'project', 'queued', 1) for r in rows)
    # The normal dispatcher picks them up; no private instance is involved.
    pending = rpc(uri, 'observer_pending_runs', 10)
    hidden_runs = {r[0] for r in query(uri, 'select r.id from public.observer_runs r join public.observer_batches b on b.id=r.batch_id where b.phase_id=%s', (s['hidden'],))}
    assert hidden_runs <= {uuid.UUID(p['id']) for p in pending}
    assert all(rpc(uri, 'observer_instance_input', r) is None for r in hidden_runs)
    # Running it again creates nothing; a failed evaluation is retried only on request.
    again = run(s['phase'], s['hidden'], None, True, False, False)
    assert again['created'] == 0 and {t['reason'] for t in again['teams']} == {'already_evaluated'}
    query(uri, "update public.observer_batches set status='failed' where phase_id=%s and team_id=%s", (s['hidden'], other_team))
    assert run(s['phase'], s['hidden'], other_team, True, False, False)['created'] == 0
    assert run(s['phase'], s['hidden'], other_team, True, False, True)['created'] == 1
    with pytest.raises(psycopg.Error, match='permission denied'):
        query(uri, 'select private.observer_run_hidden_final(%s,%s)', (s['phase'], s['hidden']), role='service_role')


def test_hidden_final_run_refuses_an_unsealed_or_unready_target(hidden):
    s = hidden; uri = s['uri']
    query(uri, "update public.phases set ends_at=now()-interval '1 second' where id=%s", (s['phase'],))
    run = lambda target: query(uri, 'select private.observer_run_hidden_final(%s,%s)', (s['phase'], target))[0][0]
    with pytest.raises(psycopg.Error, match='target_not_sealed_final_phase'):
        run(s['phase'])
    query(uri, 'delete from private.observer_scenario_bundles where scenario_id=%s', (s['hidden_scenario'],))
    with pytest.raises(psycopg.Error, match='target_scenarios_not_ready'):
        run(s['hidden'])


def test_public_formal_run_scores_on_the_template(online):
    s = online; uri = s['uri']
    rev = revision(s)
    batch = rpc(uri, 'observer_create_batch', s['phase'], rev, role='authenticated', user=s['user'])
    run = query(uri, 'select id from public.observer_runs where batch_id=%s', (batch,))[0][0]
    assert rpc(uri, 'observer_instance_input', run) is None
    query(uri, "update public.observer_runs set status='scored',score=5,score_summary=%s,finished_at=now() where id=%s",
          (Jsonb({'score': {'total': 5}}), run))


def test_organizer_script_dry_run_and_apply(hidden, monkeypatch, capsys):
    import importlib.util
    import sys
    from pathlib import Path
    from psycopg.rows import dict_row
    s = hidden; uri = s['uri']
    spec = importlib.util.spec_from_file_location('run_hidden_final', Path(__file__).resolve().parents[1]/'scripts/run-hidden-final.py')
    script = importlib.util.module_from_spec(spec); spec.loader.exec_module(script)

    def management_query(statement):
        with psycopg.connect(uri, row_factory=dict_row) as conn:
            cur = conn.execute(statement)
            return cur.fetchall() if cur.description else []
    monkeypatch.setattr(script.deploy, 'query', management_query)
    source, target = (query(uri, 'select slug from public.phases where id=%s', (p,))[0][0] for p in (s['phase'], s['hidden']))
    team_slug = query(uri, 'select slug from public.teams where id=%s', (s['team'],))[0][0]
    rev = revision(s); materialize(s, rev); choose(s, rev)

    def main(*flags):
        monkeypatch.setattr(sys, 'argv', ['run-hidden-final.py', '--source', source, '--target', target, *flags])
        script.main()
        return capsys.readouterr().out
    with pytest.raises(SystemExit):
        main('--before-freeze')
    with pytest.raises(SystemExit):
        main()
    assert 'has not ended yet' in capsys.readouterr().err
    out = main('--team', team_slug, '--before-freeze')
    assert 'DRY RUN' in out and 'would_create' in out and 'stored model mode without a saved key' in out
    assert 'scenario(s) per batch' in out and 'colocated=false' in out and 'v4_requires_colocated' in out
    assert query(uri, 'select count(*) from public.observer_batches where phase_id=%s', (s['hidden'],)) == [(0,)]
    query(uri, "update public.phases set ends_at=now()-interval '1 second' where id=%s", (s['phase'],))
    out = main('--apply', '--team', team_slug)
    assert 'APPLIED' in out and 'created=1' in out and '1 evaluation(s) per team' in out
    assert query(uri, 'select revision_id from public.observer_batches where phase_id=%s', (s['hidden'],)) == [(rev,)]
    assert '"already_evaluated"' in main('--team', team_slug, '--json')


def test_hidden_final_counts_only_the_current_card_set_and_retries_only_platform_failures(hidden):
    from test_project_eval_ux import ORG, fail
    s = hidden; uri = s['uri']
    query(uri, """insert into private.observer_installations
        (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
        values(%s,'101',202,'303',%s,true) on conflict(organization) do update set enabled=true""", (ORG, 'a'*40))
    run = lambda **flags: query(uri, 'select private.observer_run_hidden_final(%s,%s,%s,true,false,%s,%s)',
                                (s['phase'], s['hidden'], s['team'], flags.get('platform', False),
                                 flags.get('participant', False)))[0][0]['teams'][0]
    rev = revision(s); materialize(s, rev); choose(s, rev)
    query(uri, "update public.phases set ends_at=now()-interval '1 second' where id=%s", (s['phase'],))
    old = run()['batch_id']
    # The phase moves to a new card set (v3 -> v4): the earlier batch neither blocks nor counts.
    card = uuid.uuid4()
    query(uri, "insert into public.scenarios(id,slug,name) values(%s,%s,'Card')", (card, 'card-' + str(card)[:8]))
    query(uri, 'insert into public.phase_scenarios values(%s,%s)', (s['hidden'], card))
    query(uri, "insert into private.observer_scenario_bundles values(%s,'hidden/card.zip',%s)", (card, 'd' * 64))
    current = run()
    assert (current['action'], current['stale_batches'], current['previous_batch_id']) == ('created', 1, None)
    assert {r[0] for r in query(uri, 'select scenario_id from public.observer_runs where batch_id=%s',
                                (current['batch_id'],))} == {s['hidden_scenario'], card}
    assert run()['reason'] == 'already_evaluated'
    # A platform failure is rerun only on request; the rerun uses all cards again.
    query(uri, "update public.observer_runs set status='failed',error='evaluation_schedule_failed',finished_at=now()"
               " where batch_id=%s and scenario_id=%s", (current['batch_id'], card))
    query(uri, 'select private.observer_finalize_batch(%s)', (current['batch_id'],))
    # Classified and retried only once no run of the failed batch is still running.
    query(uri, "update public.observer_runs set status='running' where batch_id=%s and scenario_id=%s",
          (current['batch_id'], s['hidden_scenario']))
    assert run(platform=True)['reason'] == 'failed_settling'
    query(uri, "update public.observer_runs set status='queued' where batch_id=%s and scenario_id=%s",
          (current['batch_id'], s['hidden_scenario']))
    skipped = run()
    assert (skipped['reason'], skipped['failure'], skipped['previous_status']) == ('failed_platform', 'platform', 'failed')
    retried = run(platform=True)
    assert retried['action'] == 'created' and retried['previous_batch_id'] == current['batch_id']
    # The failed batch's leftover queued run is cancelled; the new runs are ordered for the dispatcher.
    assert query(uri, 'select status from public.observer_runs where batch_id=%s and scenario_id=%s',
                 (current['batch_id'], s['hidden_scenario'])) == [('cancelled',)]
    assert len({r[0] for r in query(uri, 'select created_at from public.observer_runs where batch_id=%s',
                                    (retried['batch_id'],))}) == 2
    # A card failed by the team's own project scores 0; the evaluation goes on and is not rerun.
    fail(s, retried['batch_id'], 'engine', 'project_operation_failed', stage='execute')
    assert query(uri, 'select status from public.observer_batches where id=%s', (retried['batch_id'],)) == [('queued',)]
    query(uri, "update public.observer_runs set status='scored',score=40,finished_at=now() where batch_id=%s and status='queued'",
          (retried['batch_id'],))
    query(uri, 'select private.observer_finalize_batch(%s)', (retried['batch_id'],))
    assert query(uri, 'select status,score from public.observer_batches where id=%s', (retried['batch_id'],)) == [('scored', 20.0)]
    assert run(platform=True, participant=True)['reason'] == 'already_evaluated'
    assert query(uri, 'select count(*) from public.observer_batches where phase_id=%s', (s['hidden'],)) == [(3,)]
    assert query(uri, 'select private.observer_batch_covers_phase(%s,%s)', (old, s['hidden'])) == [(False,)]


def test_organizer_script_status_results_and_estimate(hidden, monkeypatch, capsys, tmp_path):
    import csv
    import importlib.util
    import sys
    from pathlib import Path
    from psycopg.rows import dict_row
    s = hidden; uri = s['uri']
    spec = importlib.util.spec_from_file_location('run_hidden_final', Path(__file__).resolve().parents[1]/'scripts/run-hidden-final.py')
    script = importlib.util.module_from_spec(spec); spec.loader.exec_module(script)

    def management_query(statement):
        with psycopg.connect(uri, row_factory=dict_row) as conn:
            cur = conn.execute(statement)
            return cur.fetchall() if cur.description else []
    monkeypatch.setattr(script.deploy, 'query', management_query)
    source, target = (query(uri, 'select slug from public.phases where id=%s', (p,))[0][0] for p in (s['phase'], s['hidden']))
    query(uri, 'update public.observer_phase_settings set colocated=true,runtime_seconds=900 where phase_id=%s', (s['hidden'],))
    teams = []
    for score in (30, 50, 50, None):
        user, team = identity(uri)
        t = {**s, 'user': user, 'team': team}
        rev = revision(t); materialize(t, rev, user); choose(t, rev)
        teams.append((team, score))
    query(uri, 'update public.teams set is_hidden=false')
    query(uri, "update public.phases set ends_at=now()-interval '1 second' where id=%s", (s['phase'],))

    def main(*flags):
        monkeypatch.setattr(sys, 'argv', ['run-hidden-final.py', '--source', source, '--target', target, *flags])
        script.main()
        return capsys.readouterr().out
    from test_project_eval_ux import ORG
    query(uri, """insert into private.observer_installations
        (organization,organization_id,installation_id,repository_id,approved_sha,enabled,monthly_minute_limit)
        values(%s,'101',202,'303',%s,true,10) on conflict(organization) do update set enabled=true,monthly_minute_limit=10""",
          (ORG, 'a'*40))
    first_user = query(uri, 'select id from public.profiles where team_id=%s', (teams[0][0],))[0][0]
    query(uri, 'insert into private.observer_placements(user_id,organization) values(%s,%s)', (first_user, ORG))
    out = main()
    assert 'DRY RUN' in out and 'colocated=true' in out and 'estimate: 4 runs' in out and '22 min per run' in out
    assert 'runner capacity:' in out and f'! {ORG}: needs up to ~22 min' in out and '3 batch user(s) without a placement' in out
    assert 'not enough runner minutes' in out
    assert f'    {ORG}: 10 left (0 of 10 used), its placed teams need up to ~22' in out
    assert 'public pool: not used for this phase (switched off)' in out
    # A verified sealed transfer: the hidden phase prefers the public pool, which takes most of these minutes.
    query(uri, "delete from private.observer_public_targets")
    query(uri, "delete from private.observer_public_pool")
    query(uri, """insert into private.observer_public_pool(organization,repository_id,organization_id,approved_sha,mode,
        sealed_transfer_verified) values(%s,'4242','112',%s,'overflow',true)""", (ORG, 'b'*40))
    query(uri, """insert into private.observer_public_targets(organization,repository_id,organization_id,approved_sha,enabled,max_active)
        values(%s,'4242','112',%s,true,3) on conflict(organization) do update set enabled=true,max_active=3""", (ORG, 'b'*40))
    query(uri, "update private.observer_public_pool set max_active=3")
    query(uri, 'select public.observer_set_public_pool_phase(%s,true)', (target,))
    out = main()
    assert 'public pool: preferred for this phase (sealed transfer verified)' in out and 'up to 3 runs at a time' in out
    assert f'! {ORG}' not in out
    query(uri, 'update private.observer_public_pool set sealed_transfer_verified=false')
    assert 'public pool: not used for this phase (sealed transfer not verified)' in main()
    query(uri, "delete from private.observer_public_pool_phases")
    query(uri, "delete from private.observer_public_targets")
    query(uri, "delete from private.observer_public_pool")
    with pytest.raises(SystemExit):
        main('--limit', '2')
    out = main('--apply', '--limit', '2')
    assert 'created=2' in out and 'deferred=2' in out and 'over --limit' in out and 'estimate: 2 runs' in out
    assert 'created=2' in main('--apply')
    for team, score in teams:
        batch = query(uri, 'select id from public.observer_batches where phase_id=%s and team_id=%s', (s['hidden'], team))[0][0]
        if score is None:
            query(uri, "update public.observer_runs set status='failed',error='evaluation_schedule_failed',finished_at=now()"
                       " where batch_id=%s", (batch,))
        else:
            query(uri, "update public.observer_runs set status='scored',score=%s,finished_at=now() where batch_id=%s", (score, batch))
        query(uri, 'select private.observer_finalize_batch(%s)', (batch,))
    out = main('--status')
    assert 'STATUS: batches' in out and 'failed batches: platform=1' in out
    with pytest.raises(SystemExit):
        main('--status', '--apply')
    path = tmp_path / 'ranking.csv'
    out = main('--results', '--csv', str(path))
    assert 'leaderboard_mode=hidden' in out and 'ranked=3' in out
    rows = list(csv.DictReader(path.open(encoding='utf-8')))
    ranks = sorted((r['rank'], r['mean']) for r in rows)
    assert ranks == [('', ''), ('1', '50.0'), ('1', '50.0'), ('3', '30.0')]
    assert next(r for r in rows if r['rank'] == '')['failure'] == 'platform'
    assert 'retry after platform failure' in main('--apply', '--retry-failed')
