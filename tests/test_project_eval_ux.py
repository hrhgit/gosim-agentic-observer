"""Daily evaluation refunds, repeat confirmation and version withdrawal."""
import secrets
import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from test_project_database import database, identity, query, rpc, setup  # noqa: F401
from test_project_preparation import preparation, reserve, start  # noqa: F401

ORG = 'AGENTIC-OBSERVER26-runner-1'


@pytest.fixture
def team(setup):
    s = setup; uri = s['uri']
    query(uri, """insert into private.observer_installations
        (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
        values(%s,'101',202,'303',%s,true) on conflict(organization) do update set enabled=true""", (ORG, 'a'*40))
    query(uri, 'update public.observer_phase_settings set daily_batches=3 where phase_id=%s', (s['phase'],))
    return s


def revision(s, approve=True):
    uri = s['uri']
    rev = rpc(uri, 'observer_create_project', 'Agent', 'repository', 'https://github.com/example/agent',
              role='authenticated', user=s['user'])
    query(uri, """update public.observer_revisions set status='reviewable',source_digest=%s,approval_digest=%s,
          manifest=%s,public_test='{"passed":true}' where id=%s""",
          ('a'*64, 'b'*64, Jsonb({'image': 'python@sha256:'+'c'*64}), rev))
    if approve:
        rpc(uri, 'observer_approve_revision', rev, 'b'*64, role='authenticated', user=s['user'])
    return rev


def evaluate(s, rev, confirm=False):
    return query(s['uri'], 'select public.observer_create_batch(%s,%s,%s)', (s['phase'], rev, confirm),
                 role='authenticated', user=s['user'])[0][0]


def fail(s, batch, kind, code, stage=None):
    """A trusted executor or engine job reports failure for the batch's first run."""
    uri = s['uri']
    run = query(uri, 'select id from public.observer_runs where batch_id=%s', (batch,))[0][0]
    job, nonce = uuid.uuid4(), secrets.token_urlsafe(32)
    rpc(uri, 'observer_enqueue_job', job, kind, run, None, ORG, nonce, 'encrypted job payload', 'encrypted nonce')
    rpc(uri, 'observer_claim_job', job, nonce, '404', '1', '303', '101', 'a'*40)
    rpc(uri, 'observer_finish_job', job, '404', '1', {'diagnostics': {'stage': stage or kind, 'code': code, 'log': ''}},
        kind + '_job_failed')


def batch_state(s, batch):
    return query(s['uri'], 'select status,quota_refunded from public.observer_batches where id=%s', (batch,))[0]


def quota(s, user=None):
    rows = rpc(s['uri'], 'observer_evaluation_quota', role='authenticated', user=user or s['user'])
    return next(q for q in rows if q['phase_id'] == str(s['phase']))


def test_contestant_failures_count_and_platform_failures_retry_without_ever_failing(team):
    s = team; rev = revision(s)
    assert (quota(s)['used'], quota(s)['remaining'], quota(s)['daily_batches']) == (0, 3, 3)

    # Contestant-caused: immediate, specific, not refunded -- counts.
    crashed = evaluate(s, rev)
    fail(s, crashed, 'execute', 'project_operation_failed')
    assert batch_state(s, crashed) == ('failed', False)
    assert (quota(s)['used'], quota(s)['remaining']) == (1, 2)
    teammate, _ = identity(s['uri'], team=s['team'])
    assert quota(s, teammate)['used'] == 1

    # Platform-caused (session_network_error, not project_operation_failed):
    # never shown as failed. The batch stays active and the run is requeued;
    # it already counts toward today's quota, exactly like any other
    # in-progress evaluation.
    retried = evaluate(s, rev, True)
    run = query(s['uri'], 'select id from public.observer_runs where batch_id=%s', (retried,))[0][0]
    fail(s, retried, 'engine', 'session_network_error')
    assert batch_state(s, retried) == ('queued', False)
    assert query(s['uri'], 'select status,error from public.observer_runs where id=%s', (run,)) == [('queued', '')]
    assert (quota(s)['used'], quota(s)['remaining']) == (2, 1)
    query(s['uri'], 'update public.observer_phase_settings set max_active_evaluations=1 where phase_id=%s', (s['phase'],))
    with pytest.raises(psycopg.Error, match='batch_already_active'):
        evaluate(s, rev, True)
    query(s['uri'], 'update public.observer_phase_settings set max_active_evaluations=4 where phase_id=%s', (s['phase'],))
    # No organizer action needed: the retry just runs again like a fresh
    # attempt, and a genuine success finishes the batch normally.
    query(s['uri'], "update public.observer_runs set status='scored',score=10,finished_at=now() where id=%s", (run,))
    query(s['uri'], 'select private.observer_finalize_batch(%s)', (retried,))
    assert batch_state(s, retried) == ('scored', False)
    assert (quota(s)['used'], quota(s)['remaining']) == (2, 1)

    # If the retry budget is ever exhausted, the run is parked (still not
    # "failed") and an incident pages the organizers instead of the
    # contestant ever seeing a failure.
    parked = evaluate(s, rev, True)
    prun = query(s['uri'], 'select id from public.observer_runs where batch_id=%s', (parked,))[0][0]
    fail(s, parked, 'engine', 'session_network_error')
    # This helper dispatches a job directly, without going through the normal
    # observer_pending_runs scheduling lease first; simulate one that has
    # already been retrying for more than the ~2h budget.
    query(s['uri'], """insert into private.observer_run_leases(run_id,lease,expires_at,attempts,first_leased_at)
          values(%s,gen_random_uuid(),now()-interval '1 second',3,now()-interval '3 hours')
          on conflict(run_id) do update set expires_at=excluded.expires_at,attempts=excluded.attempts,
            first_leased_at=excluded.first_leased_at""", (prun,))
    rpc(s['uri'], 'observer_pending_runs', 10)
    assert batch_state(s, parked) == ('queued', False)
    assert query(s['uri'], "select count(*) from private.observer_incidents where subject_type='run' and subject_id=%s and resolved_at is null",
                 (prun,)) == [(1,)]
    assert (quota(s)['used'], quota(s)['remaining']) == (3, 0)
    with pytest.raises(psycopg.Error, match='daily_limit'):
        evaluate(s, rev, True)

    # The organizer clears the incident; the run resumes and can still finish normally.
    admin, _ = identity(s['uri'])
    query(s['uri'], 'update public.profiles set is_admin=true where id=%s', (admin,))
    rpc(s['uri'], 'observer_admin_requeue', 'run', prun, role='authenticated', user=admin)
    assert query(s['uri'], "select count(*) from private.observer_incidents where subject_type='run' and subject_id=%s and resolved_at is null",
                 (prun,)) == [(0,)]
    query(s['uri'], "update public.observer_runs set status='scored',score=9,finished_at=now() where id=%s", (prun,))
    query(s['uri'], 'select private.observer_finalize_batch(%s)', (parked,))
    assert batch_state(s, parked) == ('scored', False)

    # A session that never opens before its window elapses is still a
    # platform-side expiry outside 'local' mode, refunded like before --
    # this path (observer_reconcile_sessions) is untouched by the retry
    # mechanism above. (Raise today's cap: it is already spent by the three
    # resolved batches above, none of which were refunded.)
    query(s['uri'], 'update public.observer_phase_settings set daily_batches=10 where phase_id=%s', (s['phase'],))
    expired = evaluate(s, rev, True)
    query(s['uri'], "update public.observer_runs set created_at=now()-interval '1 hour' where batch_id=%s", (expired,))
    rpc(s['uri'], 'observer_reconcile_sessions')
    assert batch_state(s, expired) == ('failed', True)

    # Participants cannot mark their own batches refunded.
    with pytest.raises(psycopg.Error, match='permission denied'):
        query(s['uri'], 'update public.observer_batches set quota_refunded=true where id=%s', (crashed,),
              role='authenticated', user=s['user'])


def test_repeat_evaluation_of_a_version_needs_explicit_confirmation(team):
    s = team; rev = revision(s); other = revision(s)
    first = evaluate(s, rev)
    query(s['uri'], "update public.observer_runs set status='scored',score=10,finished_at=now() where batch_id=%s", (first,))
    query(s['uri'], 'select private.observer_finalize_batch(%s)', (first,))
    assert batch_state(s, first) == ('scored', False)
    with pytest.raises(psycopg.Error, match='revision_already_evaluated'):
        evaluate(s, rev)
    # The two-argument call used by existing clients still works for a new version.
    second = rpc(s['uri'], 'observer_create_batch', s['phase'], other, role='authenticated', user=s['user'])
    # A platform-caused run failure (engine_job_failed, not project_operation_failed)
    # requeues rather than failing the batch outright -- so it never blocks a repeat,
    # whether it is still retrying or (as simulated here) already resolved and refunded.
    fail(s, second, 'engine', 'engine_job_failed')
    assert batch_state(s, second) == ('queued', False)
    query(s['uri'], "update public.observer_batches set status='failed',quota_refunded=true where id=%s", (second,))
    evaluate(s, other)


def test_withdrawn_versions_cannot_be_approved_or_evaluated(team):
    s = team; uri = s['uri']
    outsider, _ = identity(uri)
    reviewable = revision(s, approve=False)
    with pytest.raises(psycopg.Error, match='revision_not_found'):
        rpc(uri, 'observer_withdraw_revision', reviewable, role='authenticated', user=outsider)
    rpc(uri, 'observer_withdraw_revision', reviewable, role='authenticated', user=s['user'])
    rpc(uri, 'observer_withdraw_revision', reviewable, role='authenticated', user=s['user'])
    with pytest.raises(psycopg.Error, match='revision_withdrawn'):
        rpc(uri, 'observer_approve_revision', reviewable, 'b'*64, role='authenticated', user=s['user'])
    approved = revision(s)
    teammate, _ = identity(uri, team=s['team'])
    rpc(uri, 'observer_withdraw_revision', approved, role='authenticated', user=teammate)
    assert query(uri, 'select status,archived_at is not null from public.observer_revisions where id=%s', (approved,)) == [('approved', True)]
    with pytest.raises(psycopg.Error, match='revision_withdrawn'):
        evaluate(s, approved, True)
    # Withdrawal stamps the version once; everything else about it stays frozen.
    with pytest.raises(psycopg.Error, match='approved_revision_immutable'):
        query(uri, 'update public.observer_revisions set archived_at=null where id=%s', (approved,))
    evaluated = revision(s)
    evaluate(s, evaluated)
    with pytest.raises(psycopg.Error, match='revision_not_withdrawable'):
        rpc(uri, 'observer_withdraw_revision', evaluated, role='authenticated', user=s['user'])
    with pytest.raises(psycopg.Error, match='permission denied'):
        query(uri, 'update public.observer_revisions set archived_at=now() where id=%s', (evaluated,),
              role='authenticated', user=s['user'])


def test_withdrawing_a_queued_version_stops_its_preparation(preparation):
    s = preparation; uri = s['uri']
    reserved = reserve(s)
    rpc(uri, 'observer_withdraw_revision', s['revision'], role='authenticated', user=s['user'])
    assert query(uri, 'select status,archived_at is not null from public.observer_revisions where id=%s',
                 (s['revision'],)) == [('failed', True)]
    with pytest.raises(psycopg.Error, match='preparation_lease_invalid'):
        start(s, reserved)
    assert query(uri, 'select count(*) from private.observer_jobs where revision_id=%s', (s['revision'],)) == [(0,)]
    assert all(r['id'] != str(s['revision']) for r in rpc(uri, 'observer_pending_preparations', 10))


def test_a_version_being_prepared_cannot_be_withdrawn(preparation):
    s = preparation; uri = s['uri']
    start(s)
    with pytest.raises(psycopg.Error, match='revision_not_withdrawable'):
        rpc(uri, 'observer_withdraw_revision', s['revision'], role='authenticated', user=s['user'])


def test_migration_refunds_earlier_platform_failures_only():
    from pathlib import Path
    from pg import start as start_database
    root = Path(__file__).resolve().parents[1]
    migration = root / 'supabase/migrations/20260927000200_project_eval_ux.sql'
    server, uri = start_database(apply_migrations=False)
    try:
        query(uri, (root / 'tests/supabase/auth_stub.sql').read_text())
        for path in sorted((root / 'supabase/migrations').glob('*.sql')):
            if path.name < migration.name:
                query(uri, path.read_text())
        user, team_id = identity(uri)
        phase, scenario = uuid.uuid4(), uuid.uuid4()
        query(uri, "insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,'Test','Test')", (phase, str(phase)))
        query(uri, 'insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled) values(%s,true,true)', (phase,))
        query(uri, "insert into public.scenarios(id,slug,name) values(%s,%s,'Test')", (scenario, str(scenario)))
        query(uri, 'insert into public.phase_scenarios values(%s,%s)', (phase, scenario))
        s = {'uri': uri, 'user': user, 'phase': phase}
        query(uri, """insert into private.observer_installations
            (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
            values(%s,'101',202,'303',%s,true)""", (ORG, 'a'*40))
        batches = []
        for kind, code in (('engine', 'invalid_or_expired_capability'), ('execute', 'project_operation_failed')):
            batch = rpc(uri, 'observer_create_batch', phase, None, role='authenticated', user=user)
            fail(s, batch, kind, code); batches.append(batch)
        query(uri, migration.read_text())
        assert [query(uri, 'select quota_refunded from public.observer_batches where id=%s', (b,))[0][0] for b in batches] == [True, False]
    finally:
        server.cleanup()


def test_colocated_public_runs_and_their_participant_failures(team):
    s = team; uri = s['uri']
    batch = evaluate(s, revision(s))
    run = query(uri, 'select id from public.observer_runs where batch_id=%s', (batch,))[0][0]
    assert rpc(uri, 'observer_run_colocated', run) is False
    query(uri, 'update public.observer_phase_settings set colocated=true where phase_id=%s', (s['phase'],))
    assert rpc(uri, 'observer_run_colocated', run) is True
    for role in ('authenticated', 'anon'):
        with pytest.raises(psycopg.Error, match='permission denied'):
            rpc(uri, 'observer_run_colocated', run, role=role, user=s['user'])
    # The participant container crashed inside the engine job: it still counts.
    fail(s, batch, 'engine', 'project_operation_failed', stage='execute')
    assert batch_state(s, batch) == ('failed', False)
