"""Organizer reset of today's evaluation quota (migration 20261004170000): a recorded event, nothing deleted;
the daily count starts at the later of the UTC day start and the phase's latest reset."""
import uuid

import psycopg
import pytest

from test_project_database import database, identity, query, rpc, setup  # noqa: F401


@pytest.fixture
def quota(setup):
    s = setup
    query(s['uri'], 'update public.observer_phase_settings set daily_batches=2, max_active_evaluations=10 where phase_id=%s', (s['phase'],))
    return s


def evaluate(s, user=None):
    return rpc(s['uri'], 'observer_create_batch', s['phase'], None, role='authenticated', user=user or s['user'])


def remaining(s, phase=None, user=None):
    rows = rpc(s['uri'], 'observer_evaluation_quota', role='authenticated', user=user or s['user'])
    return {r['phase_id']: r for r in rows}[str(phase or s['phase'])]['remaining']


def reset_as(uri, phases, note, role='service_role', user=None):
    return query(uri, 'select public.observer_reset_daily_quota(%s::text[], %s)', (phases, note), role=role, user=user)[0][0]


def reset(s, phases=False, note='test'):
    """phases=False: this test's phase; None or []: the default phases."""
    return reset_as(s['uri'], [str(s['phase'])] if phases is False else phases, note)


def other_phase(s):
    phase, scenario = uuid.uuid4(), uuid.uuid4()
    uri = s['uri']
    query(uri, "insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,'Other','Other')", (phase, str(phase)))
    query(uri, """insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,
          model_token_limit,model_call_limit,model_concurrency,daily_batches,max_active_evaluations)
          values(%s,true,true,1000,10,4,2,10)""", (phase,))
    query(uri, "insert into public.scenarios(id,slug,name) values(%s,%s,'Other')", (scenario, str(scenario)))
    query(uri, 'insert into public.phase_scenarios values(%s,%s)', (phase, scenario))
    return phase


def test_without_a_reset_the_utc_day_counts(quota):
    s = quota
    evaluate(s); evaluate(s)
    assert remaining(s) == 0
    with pytest.raises(psycopg.Error, match='daily_limit'):
        evaluate(s)
    # Yesterday's evaluations never counted.
    query(s['uri'], "update public.observer_batches set created_at=created_at-interval '1 day' where team_id=%s", (s['team'],))
    assert remaining(s) == 2


def test_a_reset_restores_the_full_quota_and_deletes_nothing(quota):
    s = quota; uri = s['uri']
    first, second = evaluate(s), evaluate(s)
    before = query(uri, 'select id,status,created_at from public.observer_batches where team_id=%s order by id', (s['team'],))
    result = reset(s, note='2026-10-04 organizer reset')
    assert result['phases'] == [str(s['phase'])] and result['note'] == '2026-10-04 organizer reset'
    assert remaining(s) == 2
    assert query(uri, 'select id,status,created_at from public.observer_batches where team_id=%s order by id', (s['team'],)) == before
    assert {first, second} <= {r[0] for r in before}
    evaluate(s); evaluate(s)
    assert remaining(s) == 0
    with pytest.raises(psycopg.Error, match='daily_limit'):
        evaluate(s)
    stored = query(uri, 'select phase_ids,note,actor from private.observer_quota_resets where id=%s', (result['reset_id'],))
    assert stored == [([s['phase']], '2026-10-04 organizer reset', 'service_role')]


def test_the_latest_of_several_resets_counts(quota):
    s = quota; uri = s['uri']
    evaluate(s)
    reset(s)
    evaluate(s)
    assert remaining(s) == 1
    reset(s)
    assert remaining(s) == 2
    # An older reset (earlier today) is superseded; a reset from before today changes nothing.
    query(uri, "update private.observer_quota_resets set reset_at=now()-interval '3 days'")
    assert remaining(s) == 0
    # A reset dated in the future does not apply yet.
    query(uri, "update private.observer_quota_resets set reset_at=now()+interval '1 hour'")
    assert remaining(s) == 0


def test_a_reset_only_affects_its_phases(quota):
    s = quota
    other = other_phase(s)
    rpc(s['uri'], 'observer_create_batch', other, None, role='authenticated', user=s['user'])
    evaluate(s)
    reset(s)
    assert remaining(s) == 2
    assert remaining(s, other) == 1
    reset(s, [str(s['phase']), str(other)])
    assert remaining(s, other) == 2


def test_default_phases_are_the_open_contestant_phases(quota):
    s = quota; uri = s['uri']
    restricted, sealed, ended = other_phase(s), other_phase(s), other_phase(s)
    query(uri, 'update public.observer_phase_settings set access_team_id=%s where phase_id=%s', (s['team'], restricted))
    query(uri, 'update public.observer_phase_settings set sealed=true where phase_id=%s', (sealed,))
    query(uri, "update public.phases set ends_at=now()-interval '1 hour' where id=%s", (ended,))
    phases = set(reset(s, None)['phases'])
    assert str(s['phase']) in phases
    assert not {str(restricted), str(sealed), str(ended)} & phases
    assert reset(s, [])['phases']  # an empty list means the default too
    with pytest.raises(psycopg.Error, match='unknown_phase: nope'):
        reset(s, [str(s['phase']), 'nope'])


def test_only_organizers_can_reset(quota):
    s = quota; uri = s['uri']
    with pytest.raises(psycopg.Error, match='admin_only'):
        reset_as(uri, [str(s['phase'])], 'x', role='authenticated', user=s['user'])
    with pytest.raises(psycopg.Error, match='permission denied'):
        reset_as(uri, [str(s['phase'])], 'x', role='anon')
    admin, _ = identity(uri)
    query(uri, 'update public.profiles set is_admin=true where id=%s', (admin,))
    got = reset_as(uri, [str(s['phase'])], 'x', role='authenticated', user=admin)
    assert query(uri, 'select created_by,actor from private.observer_quota_resets where id=%s', (got['reset_id'],)) == [(admin, 'admin')]
    # A direct SQL session (the organizers' one-liner) works too.
    direct = reset_as(uri, [str(s['phase'])], 'direct', role=None)
    assert query(uri, 'select actor from private.observer_quota_resets where id=%s', (direct['reset_id'],))[0][0] != 'admin'


def test_contestants_see_the_latest_recent_reset_of_their_phases(quota):
    s = quota; uri = s['uri']
    notice = lambda user=s['user']: rpc(uri, 'observer_quota_reset_notice', role='authenticated', user=user)
    query(uri, 'delete from private.observer_quota_resets')  # earlier tests' resets of their (public) phases
    assert notice() is None
    first = reset(s)['reset_id']
    got = notice()
    assert got['id'] == first and [p['daily_batches'] for p in got['phases']] == [2]
    second = reset(s)['reset_id']
    assert notice()['id'] == second
    # No team, no notice.
    loner = uuid.uuid4()
    query(uri, 'insert into auth.users(id,email) values(%s,%s)', (loner, f'{loner}@example.test'))
    assert notice(loner) is None
    # Another team's restricted phase is not mentioned to this team.
    restricted = other_phase(s)
    other_user, other_team = identity(uri)
    query(uri, 'update public.observer_phase_settings set access_team_id=%s where phase_id=%s', (other_team, restricted))
    hidden = reset(s, [str(restricted)])['reset_id']
    assert notice()['id'] == second
    assert notice(other_user)['id'] == hidden
    # Older than 24 hours: nothing to tell.
    query(uri, "update private.observer_quota_resets set reset_at=now()-interval '25 hours'")
    assert notice() is None


def test_project_preparations_follow_the_evaluation_quota_and_its_resets(quota):
    """Migration 20261004220000: the daily preparation limit is the largest daily evaluation quota of the team's
    contestant phases (no separate number to keep in sync) and an organizer reset restarts its count too."""
    s = quota; uri = s['uri']
    assert query(uri, 'select coalesce(max(daily_batches),0) from public.observer_phase_settings')[0][0] < 98
    query(uri, 'update public.observer_phase_settings set daily_batches=98, access_team_id=%s where phase_id=%s', (s['team'], s['phase']))

    def prep():
        rows = rpc(uri, 'observer_evaluation_quota', role='authenticated', user=s['user'])
        row = {r['phase_id']: r for r in rows}[str(s['phase'])]
        return row['preparations_daily'], row['preparations_used'], row['preparations_remaining']

    def create():
        return rpc(uri, 'observer_create_project', 'p', 'repository', 'https://github.com/example/project', role='authenticated', user=s['user'])

    first = create()
    query(uri, """insert into public.observer_revisions(project_id,source_kind,source_location,status)
        select project_id,source_kind,source_location,'failed' from public.observer_revisions, generate_series(1,%s)
        where id=%s""", (98 - prep()[1], first))
    assert prep() == (98, 98, 0)
    with pytest.raises(psycopg.Error, match='preparation_daily_limit'):
        create()
    query(uri, 'update public.observer_phase_settings set daily_batches=99 where phase_id=%s', (s['phase'],))
    assert prep() == (99, 98, 1)
    reset(s)
    assert prep() == (99, 0, 99)
    create()
    assert prep()[1] == 1
    # Another team's private phase never raises this team's limit; a sealed phase does not count either.
    query(uri, 'update public.observer_phase_settings set sealed=true where phase_id=%s', (s['phase'],))
    rows = rpc(uri, 'observer_evaluation_quota', role='authenticated', user=s['user'])
    assert rows == [] or all(r['preparations_daily'] < 98 for r in rows)
    query(uri, 'update public.observer_phase_settings set sealed=false, access_team_id=null where phase_id=%s', (s['phase'],))
