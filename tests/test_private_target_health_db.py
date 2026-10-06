"""Health, cooldown and kill switch for the private runner organizations (20261004070000)."""
from __future__ import annotations

import secrets
import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from test_project_database import database, identity, query, rpc, setup  # noqa: F401
from test_runner_placement import ORG, install, queued_job

A, B, C = ORG + '1', ORG + '2', ORG + '3'


@pytest.fixture
def fleet(setup):
    s = setup; uri = s['uri']
    query(uri, "update private.observer_jobs set status='failed' where status in ('queued','dispatched','claimed')")
    query(uri, 'delete from private.observer_placements')
    query(uri, 'delete from private.observer_private_events')
    query(uri, 'delete from private.observer_dispatch_failures')
    for n in range(1, 14):
        install(uri, n, enabled=n in (1, 2, 3))
    query(uri, """update private.observer_installations set routing=true,cooldown_until=null,cooldown_reason='',
        github_minutes=null,github_minutes_estimate=null,github_minutes_at=null,monthly_minute_limit=1800,
        fallback_capacity=0""")
    health(s, 'on')
    query(uri, 'insert into private.observer_placements(user_id,organization) values(%s,%s)', (s['user'], A))
    yield s
    health(s, 'off')


def health(s, mode):
    return query(s['uri'], 'select public.observer_set_target_health(p_mode=>%s)', (mode,), role='service_role')[0][0]


def reconcile(s):
    return rpc(s['uri'], 'observer_target_health_reconcile')


def stall(s, job):
    query(s['uri'], """update private.observer_jobs set status='dispatched',dispatch_count=3,
        last_dispatch_at=now()-interval '10 minutes' where id=%s""", (job,))


def row(s, job):
    return query(s['uri'], 'select organization,status,dispatch_count from private.observer_jobs where id=%s', (job,))[0]


def org_health(s):
    return {o: (h, ok) for o, h, ok in query(s['uri'], 'select organization,health,healthy from public.observer_organizations_by_load()')}


def failed_claimed(s, org, code='job_http_503'):
    job = queued_job(s, org)
    query(s['uri'], """update private.observer_jobs set status='failed',claimed_at=now()-interval '2 minutes',
        finished_at=now(),error='engine_job_failed',result=%s where id=%s""",
          (Jsonb({'diagnostics': {'code': code}}), job))
    return job


def test_off_is_the_previous_behaviour(fleet):
    s = fleet
    health(s, 'off')
    job = queued_job(s, A)
    stall(s, job)
    assert reconcile(s) == {'mode': 'off'}
    assert row(s, job)[0] == A
    assert query(s['uri'], 'select count(*) from private.observer_private_events') == [(0,)]
    assert all(ok for _, ok in org_health(s).values())
    # observe records the stall but moves nothing
    health(s, 'observe')
    assert reconcile(s)['stalled'] == 1
    assert row(s, job) == (A, 'dispatched', 3)
    assert reconcile(s)['stalled'] == 0  # noted once


def test_a_stalled_job_moves_to_a_healthy_organization_with_its_owner(fleet):
    s = fleet
    job = queued_job(s, A)
    stall(s, job)
    out = reconcile(s)
    assert out['stalled'] == 1 and out['moved'] == 1
    org, status, count = row(s, job)
    assert org in (B, C) and status == 'queued' and count == 0
    assert query(s['uri'], 'select organization from private.observer_placements where user_id=%s', (s['user'],)) == [(org,)]
    # The job is dispatchable again right away in its new organization.
    assert [j['organization'] for j in rpc(s['uri'], 'observer_pending_jobs', 20) if j['id'] == str(job)] == [org]


def test_a_fresh_dispatch_is_not_a_stall_and_preparation_jobs_never_move(fleet):
    s = fleet
    fresh = queued_job(s, A)
    query(s['uri'], "update private.observer_jobs set status='dispatched',dispatch_count=3,last_dispatch_at=now() where id=%s", (fresh,))
    rev = rpc(s['uri'], 'observer_create_project', 'Agent', 'repository', 'https://github.com/example/agent',
              role='authenticated', user=s['user'])
    prep = uuid.uuid4()
    rpc(s['uri'], 'observer_enqueue_job', prep, 'prepare', None, rev, A, secrets.token_urlsafe(32),
        'encrypted job payload', 'encrypted nonce')
    stall(s, prep)
    out = reconcile(s)
    assert out['stalled'] == 1 and out['moved'] == 0
    assert row(s, fresh)[0] == A and row(s, prep)[0] == A


def test_three_strikes_cool_an_organization_down_and_its_queue_moves(fleet):
    s = fleet; uri = s['uri']
    failed_claimed(s, A)
    failed_claimed(s, A, code='project_operation_failed')  # the participant's fault: not counted
    query(uri, "insert into private.observer_dispatch_failures(organization,error) values(%s,'github_request_failed')", (A,))
    query(uri, "insert into private.observer_dispatch_failures(organization,error) values(%s,'github_unavailable')", (A,))
    waiting = queued_job(s, A)
    out = reconcile(s)
    assert out['platform_failed'] == 1
    assert org_health(s)[A] == ('ok', True)
    failed_claimed(s, A, code='job_http_500')
    reconcile(s)
    assert org_health(s)[A] == ('cooldown', False)
    # The queued job and the owner's placement leave; a new participant is never placed there.
    reconcile(s)
    assert row(s, waiting)[0] in (B, C)
    newcomer, _ = identity(uri)
    assert rpc(uri, 'observer_placement', newcomer) in (B, C)
    status = rpc(uri, 'observer_targets_status')
    a = next(p for p in status['private'] if p['organization'] == A)
    assert a['health'] == 'cooldown' and a['platform_failed_1h'] == 2 and a['moved_away_1h'] >= 1
    assert len(status['private']) == 3 and isinstance(status['public'], list)
    query(uri, 'select public.observer_set_private_target(%s,p_clear_cooldown=>true)', (A,), role='service_role')
    assert org_health(s)[A] == ('ok', True)


def test_kill_switch_keeps_running_claims_and_nothing_moves_when_no_one_is_healthy(fleet):
    s = fleet; uri = s['uri']
    running = queued_job(s, A)
    query(uri, "update private.observer_jobs set status='claimed',claimed_at=now() where id=%s", (running,))
    for org in (A, B, C):
        query(uri, 'select public.observer_set_private_target(%s,p_routing=>false)', (org,), role='service_role')
    waiting = queued_job(s, A)
    reconcile(s)
    assert row(s, waiting)[0] == A  # every organization is off: the job stays (never stranded)
    assert row(s, running)[1] == 'claimed'
    assert rpc(uri, 'observer_job_identity', running)['organization'] == A
    query(uri, 'select public.observer_set_private_target(%s,p_routing=>true)', (C,), role='service_role')
    reconcile(s)
    assert row(s, waiting)[0] == C


def test_billed_minutes_override_a_low_estimate_only_while_on(fleet):
    s = fleet; uri = s['uri']
    assert rpc(uri, 'observer_set_github_minutes', {A: 1850, 'unknown-org': 5}) == 1
    assert org_health(s)[A] == ('minute_limit', False)
    waiting = queued_job(s, A)
    reconcile(s)
    assert row(s, waiting)[0] in (B, C)
    minutes = {o: m for o, m in query(uri, 'select organization,month_minutes from public.observer_organizations_by_load()')}
    assert minutes[A] == 1850
    health(s, 'observe')
    assert org_health(s)[A] == ('ok', True)
    assert {o: m for o, m in query(uri, 'select organization,month_minutes from public.observer_organizations_by_load()')}[A] < 100
    with pytest.raises(psycopg.Error, match='invalid_minutes'):
        rpc(uri, 'observer_set_github_minutes', {A: -1})


def test_public_repositories_count_platform_failures_too(fleet):
    s = fleet; uri = s['uri']
    query(uri, """insert into private.observer_public_targets(organization,repository_id,organization_id,approved_sha,enabled)
        values(%s,'4242','112',%s,true) on conflict(organization) do update set cooldown_until=null""", (C, 'b' * 40))
    query(uri, 'delete from private.observer_public_events')
    for _ in range(3):
        job = failed_claimed(s, C)
        query(uri, "update private.observer_jobs set runner='public-hosted',home_organization=%s where id=%s", (A, job))
    reconcile(s)
    target = next(t for t in rpc(uri, 'observer_public_targets_status') if t['organization'] == C)
    assert target['healthy'] is False
    assert org_health(s)[C] == ('ok', True)  # the private organization itself is unaffected
    query(uri, 'delete from private.observer_public_targets where organization=%s', (C,))


def test_switches_are_service_role_only(fleet):
    s = fleet
    for role in ('authenticated', 'anon'):
        for statement in ("select public.observer_set_target_health(p_mode=>'off')",
                          'select public.observer_targets_status()',
                          'select public.observer_target_health_reconcile()',
                          "select public.observer_set_github_minutes('{}'::jsonb)",
                          "select public.observer_set_private_target('x',p_routing=>false)"):
            with pytest.raises(psycopg.Error, match='permission denied'):
                query(s['uri'], statement, role=role, user=s['user'])
