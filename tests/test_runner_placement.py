"""Recorded runner placement across up to ninety-nine organizations."""
from __future__ import annotations

import concurrent.futures
import secrets
import uuid

import psycopg
import pytest

from test_project_database import database, identity, query, rpc, session, setup  # noqa: F401

ORG = 'AGENTIC-OBSERVER26-runner-'


def finished_job(s, org, seconds, days_ago=1):
    """A succeeded engine job on org with a controlled duration inside the usage window."""
    uri = s['uri']
    batch, run = uuid.uuid4(), uuid.uuid4()
    query(uri, "insert into public.observer_batches(id,team_id,user_id,phase_id,mode) values(%s,%s,%s,%s,'local')",
          (batch, s['team'], s['user'], s['phase']))
    query(uri, "insert into public.observer_runs(id,batch_id,scenario_id,status) values(%s,%s,%s,'queued')",
          (run, batch, s['scenario']))
    job = uuid.uuid4()
    rpc(uri, 'observer_enqueue_job', job, 'engine', run, None, org,
        secrets.token_urlsafe(32), 'encrypted job payload', 'encrypted nonce')
    query(uri, """update private.observer_jobs set status='succeeded',
        claimed_at=now()-(%s||' days')::interval-(%s||' seconds')::interval,
        finished_at=now()-(%s||' days')::interval where id=%s""", (days_ago, seconds, days_ago, job))
    # The run is over; only the recorded job duration remains as usage.
    query(uri, "update public.observer_runs set status='scored',score=1,finished_at=now() where id=%s", (run,))
    return job


def claimed_job(s, org):
    """An unexpired claimed job on org: one unit of active load."""
    uri = s['uri']
    batch, run = uuid.uuid4(), uuid.uuid4()
    query(uri, "insert into public.observer_batches(id,team_id,user_id,phase_id,mode) values(%s,%s,%s,%s,'local')",
          (batch, s['team'], s['user'], s['phase']))
    query(uri, "insert into public.observer_runs(id,batch_id,scenario_id,status) values(%s,%s,%s,'queued')",
          (run, batch, s['scenario']))
    job = uuid.uuid4()
    rpc(uri, 'observer_enqueue_job', job, 'engine', run, None, org,
        secrets.token_urlsafe(32), 'encrypted job payload', 'encrypted nonce')
    query(uri, "update private.observer_jobs set status='claimed',claimed_at=now() where id=%s", (job,))
    return job


def queued_job(s, org):
    """A freshly enqueued, not yet dispatched job on org."""
    uri = s['uri']
    batch, run = uuid.uuid4(), uuid.uuid4()
    query(uri, "insert into public.observer_batches(id,team_id,user_id,phase_id,mode) values(%s,%s,%s,%s,'local')",
          (batch, s['team'], s['user'], s['phase']))
    query(uri, "insert into public.observer_runs(id,batch_id,scenario_id,status) values(%s,%s,%s,'queued')",
          (run, batch, s['scenario']))
    job = uuid.uuid4()
    rpc(uri, 'observer_enqueue_job', job, 'engine', run, None, org,
        secrets.token_urlsafe(32), 'encrypted job payload', 'encrypted nonce')
    return job


def month_job(s, org, seconds):
    """A succeeded job whose duration lands inside the current calendar month."""
    uri = s['uri']
    batch, run = uuid.uuid4(), uuid.uuid4()
    query(uri, "insert into public.observer_batches(id,team_id,user_id,phase_id,mode) values(%s,%s,%s,%s,'local')",
          (batch, s['team'], s['user'], s['phase']))
    query(uri, "insert into public.observer_runs(id,batch_id,scenario_id,status) values(%s,%s,%s,'queued')",
          (run, batch, s['scenario']))
    job = uuid.uuid4()
    rpc(uri, 'observer_enqueue_job', job, 'engine', run, None, org,
        secrets.token_urlsafe(32), 'encrypted job payload', 'encrypted nonce')
    query(uri, """update private.observer_jobs set status='succeeded',
        claimed_at=date_trunc('month',now())+interval '1 hour',
        finished_at=date_trunc('month',now())+interval '1 hour'+(%s||' seconds')::interval
        where id=%s""", (seconds, job))
    query(uri, "update public.observer_runs set status='scored',score=1,finished_at=now() where id=%s", (run,))
    return job
def install(uri, n, enabled=True):
    query(uri, """insert into private.observer_installations
        (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
        values(%s,%s,%s,%s,%s,%s) on conflict(organization) do update set enabled=excluded.enabled""",
          (ORG + str(n), str(1000 + n), 2000 + n, str(3000 + n), 'a' * 40, enabled))


def test_organization_names_up_to_ninety_nine_only(setup):
    uri = setup['uri']
    for n in (1, 7, 12, 13, 36, 99):
        install(uri, n, enabled=False)
    for name in ('0', '01', '1x', '100'):
        with pytest.raises(psycopg.Error, match='organization_check'):
            query(uri, """insert into private.observer_installations
                (organization,organization_id,installation_id,repository_id,approved_sha)
                values(%s,'9','9','9',%s)""", (ORG + name, 'a' * 40))
    check = query(uri, """select pg_get_constraintdef(oid) from pg_constraint
        where conname='observer_materializations_archive_ref_check'""")[0][0]
    assert '[1-9]|[1-9][0-9]' in check


def test_existing_participant_keeps_the_organization_of_their_jobs(setup):
    s = setup; uri = s['uri']
    for n in range(1, 13):
        install(uri, n)
    # A job placed before this change stays authoritative for its owner.
    run, _, _ = session(s)
    rpc(uri, 'observer_enqueue_job', uuid.uuid4(), 'engine', run, None, ORG + '5',
        secrets.token_urlsafe(32), 'encrypted job payload', 'encrypted nonce')
    query(uri, 'delete from private.observer_placements where user_id=%s', (s['user'],))
    assert rpc(uri, 'observer_placement', s['user']) == ORG + '5'
    assert rpc(uri, 'observer_placement', s['user']) == ORG + '5'
    assert query(uri, 'select organization from private.observer_placements where user_id=%s',
                 (s['user'],)) == [(ORG + '5',)]


def test_new_participants_fill_the_least_loaded_enabled_organizations(setup):
    s = setup; uri = s['uri']
    query(uri, 'delete from private.observer_placements')
    for n in range(1, 7):
        install(uri, n)
    for n in range(7, 13):
        install(uri, n, enabled=False)
    for n in range(1, 7):
        user, _ = identity(uri)
        query(uri, 'insert into private.observer_placements(user_id,organization) values(%s,%s)', (user, ORG + str(n)))
    # Disabled organizations are never chosen.
    user, _ = identity(uri)
    assert rpc(uri, 'observer_placement', user) == ORG + '1'
    for n in range(7, 13):
        install(uri, n)
    placed = []
    for _ in range(6):
        user, _ = identity(uri)
        placed.append(rpc(uri, 'observer_placement', user))
    assert placed == [ORG + str(n) for n in range(7, 13)]
    # A recorded placement survives its organization being disabled later.
    install(uri, 7, enabled=False)
    first = query(uri, 'select user_id from private.observer_placements where organization=%s', (ORG + '7',))[0][0]
    assert rpc(uri, 'observer_placement', first) == ORG + '7'


def test_new_placement_avoids_organizations_with_active_jobs(setup):
    s = setup; uri = s['uri']
    query(uri, 'delete from private.observer_placements')
    for n in range(1, 4):
        install(uri, n)
    for n in range(4, 13):
        install(uri, n, enabled=False)
    # org-3 has zero placements but one active job: count-based placement would
    # have chosen it, load-based placement must not.
    claimed_job(s, ORG + '3')
    for n in (1, 2):
        placed, _ = identity(uri)
        query(uri, 'insert into private.observer_placements(user_id,organization) values(%s,%s)', (placed, ORG + str(n)))
    user, _ = identity(uri)
    assert rpc(uri, 'observer_placement', user) == ORG + '1'


def test_new_placement_prefers_the_least_recently_used_organization(setup):
    s = setup; uri = s['uri']
    query(uri, 'delete from private.observer_placements')
    for n in range(1, 4):
        install(uri, n)
    for n in range(4, 13):
        install(uri, n, enabled=False)
    # org-1 has zero placements but consumed an hour inside the seven-day window;
    # org-2 has one placement and no usage.
    finished_job(s, ORG + '1', 3600)
    placed, _ = identity(uri)
    query(uri, 'insert into private.observer_placements(user_id,organization) values(%s,%s)', (placed, ORG + '2'))
    user, _ = identity(uri)
    assert rpc(uri, 'observer_placement', user) == ORG + '2'
    # Usage outside the seven-day window no longer counts.
    query(uri, "update private.observer_jobs set claimed_at=now()-interval '10 days',finished_at=now()-interval '9 days'")
    user, _ = identity(uri)
    assert rpc(uri, 'observer_placement', user) == ORG + '1'


def test_concurrent_first_placement_is_recorded_once(setup):
    s = setup; uri = s['uri']
    for n in range(1, 13):
        install(uri, n)
    user, _ = identity(uri)
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = set(pool.map(lambda _: rpc(uri, 'observer_placement', user), range(8)))
    assert len(results) == 1
    assert query(uri, 'select count(*) from private.observer_placements where user_id=%s', (user,)) == [(1,)]


def test_no_enabled_runner_and_participant_access_are_refused(setup):
    s = setup; uri = s['uri']
    query(uri, 'update private.observer_installations set enabled=false')
    user, _ = identity(uri)
    with pytest.raises(psycopg.Error, match='runner_not_configured'):
        rpc(uri, 'observer_placement', user)
    for role in ('authenticated', 'anon'):
        with pytest.raises(psycopg.Error, match='permission denied'):
            rpc(uri, 'observer_placement', user, role=role, user=user)
        with pytest.raises(psycopg.Error, match='permission denied'):
            query(uri, 'select * from private.observer_placements', role=role, user=user)


def enable_only(uri, numbers):
    for n in range(1, 13):
        install(uri, n, enabled=n in numbers)


def test_organizations_over_the_monthly_minute_cap_are_skipped(setup):
    s = setup; uri = s['uri']
    query(uri, 'delete from private.observer_placements')
    enable_only(uri, {8, 9, 10})
    # runner-8 has no placements but already burned more than the default cap.
    month_job(s, ORG + '8', 2000 * 60)
    user, _ = identity(uri)
    assert rpc(uri, 'observer_placement', user) == ORG + '9'
    user, _ = identity(uri)
    assert rpc(uri, 'observer_placement', user) == ORG + '10'
    # The cap is configurable per installation row: zero minutes allowed on 9.
    query(uri, 'update private.observer_installations set monthly_minute_limit=0 where organization=%s', (ORG + '9',))
    user, _ = identity(uri)
    assert rpc(uri, 'observer_placement', user) == ORG + '10'
    # When every organization is over its cap the least-used one is the last resort.
    query(uri, 'update private.observer_installations set monthly_minute_limit=0 where organization=%s', (ORG + '10',))
    user, _ = identity(uri)
    assert rpc(uri, 'observer_placement', user) in (ORG + '9', ORG + '10')


def test_recent_dispatch_failures_demote_an_organization_temporarily(setup):
    s = setup; uri = s['uri']
    query(uri, 'delete from private.observer_placements')
    enable_only(uri, {11, 12})
    # Equal active load; runner-11 additionally logged a dispatch failure.
    failing = queued_job(s, ORG + '11')
    queued_job(s, ORG + '12')
    rpc(uri, 'observer_dispatch_error', failing, 'github_request_failed')
    user, _ = identity(uri)
    assert rpc(uri, 'observer_placement', user) == ORG + '12'
    # The demotion decays: after the failure window the organization is chosen again.
    query(uri, "update private.observer_dispatch_failures set failed_at=now()-interval '1 hour'")
    user, _ = identity(uri)
    assert rpc(uri, 'observer_placement', user) == ORG + '11'


def test_organizations_by_load_is_service_role_only(setup):
    uri = setup['uri']
    user, _ = identity(uri)
    for role in ('authenticated', 'anon'):
        with pytest.raises(psycopg.Error, match='permission denied'):
            query(uri, 'select * from public.observer_organizations_by_load()', role=role, user=user)
    rows = query(uri, 'select organization,over_limit from public.observer_organizations_by_load()',
                 role='service_role')
    assert all(r[0].startswith(ORG) for r in rows)


def test_failover_moves_a_pending_job_and_the_owners_placement(setup):
    s = setup; uri = s['uri']
    enable_only(uri, set())
    for n, sha in ((8, 'a' * 40), (9, 'b' * 40)):
        query(uri, """insert into private.observer_installations
            (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
            values(%s,%s,%s,%s,%s,true) on conflict(organization) do update
            set approved_sha=excluded.approved_sha,enabled=true""",
              (ORG + str(n), str(1000 + n), 2000 + n, str(3000 + n), sha))
    job = queued_job(s, ORG + '8')
    query(uri, 'insert into private.observer_placements(user_id,organization) values(%s,%s)',
          (s['user'], ORG + '8'))
    query(uri, "update private.observer_jobs set error='github_request_failed',dispatch_count=2 where id=%s", (job,))
    rpc(uri, 'observer_failover_job', job, ORG + '9')
    row = query(uri, 'select organization,workflow_sha,error,dispatch_count from private.observer_jobs where id=%s',
                (job,))[0]
    assert row == (ORG + '9', 'b' * 40, '', 0)
    # The owner's placement follows the job; claim-time credentials resolve to runner-9.
    assert query(uri, 'select organization from private.observer_placements where user_id=%s',
                 (s['user'],)) == [(ORG + '9',)]
    with pytest.raises(psycopg.Error, match='job_conflict'):
        rpc(uri, 'observer_failover_job', job, ORG + '9')
    with pytest.raises(psycopg.Error, match='runner_not_configured'):
        rpc(uri, 'observer_failover_job', job, ORG + '10')
    # A claimed (in-flight) job never moves.
    query(uri, "update private.observer_jobs set status='claimed',claimed_at=now() where id=%s", (job,))
    with pytest.raises(psycopg.Error, match='job_unavailable'):
        rpc(uri, 'observer_failover_job', job, ORG + '8')
    for role in ('authenticated', 'anon'):
        with pytest.raises(psycopg.Error, match='permission denied'):
            rpc(uri, 'observer_failover_job', job, ORG + '8', role=role, user=s['user'])


def fallback_ready(uri, capacity):
    """runner-8 GitHub-hosted only; runner-13 additionally hosts the self-hosted fallback runner."""
    query(uri, "update private.observer_jobs set status='failed' where status in ('queued','dispatched','claimed')")
    query(uri, 'update private.observer_installations set fallback_capacity=0,monthly_minute_limit=1800')
    enable_only(uri, {8})
    query(uri, """insert into private.observer_installations
        (organization,organization_id,installation_id,repository_id,approved_sha,enabled,fallback_capacity)
        values(%s,'1013',2013,'3013',%s,true,%s) on conflict(organization) do update
        set approved_sha=excluded.approved_sha,enabled=true,fallback_capacity=excluded.fallback_capacity""",
          (ORG + '13', 'f' * 40, capacity))


def test_fallback_moves_a_pending_job_to_a_free_self_hosted_slot(setup):
    s = setup; uri = s['uri']
    fallback_ready(uri, 0)
    query(uri, 'delete from private.observer_placements where user_id=%s', (s['user'],))
    query(uri, 'insert into private.observer_placements(user_id,organization) values(%s,%s)', (s['user'], ORG + '8'))
    first, second = queued_job(s, ORG + '8'), queued_job(s, ORG + '8')
    with pytest.raises(psycopg.Error, match='fallback_unavailable'):
        rpc(uri, 'observer_fallback_job', first)
    query(uri, 'update private.observer_installations set fallback_capacity=1 where organization=%s', (ORG + '13',))
    query(uri, "update private.observer_jobs set status='dispatched',dispatch_count=3,error='x' where id=%s", (first,))
    assert rpc(uri, 'observer_fallback_job', first) == {'organization': ORG + '13', 'approved_sha': 'f' * 40,
                                                     'partner': None}
    row = query(uri, """select organization,repository_id,organization_id,workflow_sha,runner,status,error,
        dispatch_count from private.observer_jobs where id=%s""", (first,))[0]
    assert row == (ORG + '13', '3013', '1013', 'f' * 40, 'self-hosted', 'queued', '', 1)
    # The owner's placement (and so the job's participant repository) stays put.
    assert query(uri, 'select organization from private.observer_placements where user_id=%s',
                 (s['user'],)) == [(ORG + '8',)]
    with pytest.raises(psycopg.Error, match='job_conflict'):
        rpc(uri, 'observer_fallback_job', first)
    # The single slot stays taken until the self-hosted job finishes or expires.
    with pytest.raises(psycopg.Error, match='fallback_unavailable'):
        rpc(uri, 'observer_fallback_job', second)
    query(uri, "update private.observer_jobs set status='succeeded',finished_at=now() where id=%s", (first,))
    assert rpc(uri, 'observer_fallback_job', second)['organization'] == ORG + '13'
    query(uri, "update private.observer_jobs set status='claimed',runner='github-hosted' where id=%s", (second,))
    with pytest.raises(psycopg.Error, match='job_unavailable'):
        rpc(uri, 'observer_fallback_job', second)
    for role in ('authenticated', 'anon'):
        with pytest.raises(psycopg.Error, match='permission denied'):
            rpc(uri, 'observer_fallback_job', second, role=role, user=s['user'])


def test_a_job_moved_to_the_fallback_keeps_time_for_the_vm_to_boot(setup):
    s = setup; uri = s['uri']
    fallback_ready(uri, 1)
    job = queued_job(s, ORG + '8')
    run = query(uri, 'select run_id from private.observer_jobs where id=%s', (job,))[0][0]
    # Stalled for fourteen minutes: three dispatches, then ten minutes without a claim.
    query(uri, """update private.observer_jobs set status='dispatched',dispatch_count=3,
        created_at=now()-interval '14 minutes',expires_at=now()+interval '16 minutes' where id=%s""", (job,))
    query(uri, """insert into private.observer_sessions(run_id,participant_hash,engine_hash,expires_at,
        token_limit,call_limit,concurrency_limit) values(%s,%s,%s,now()+interval '40 minutes',1,1,1)""",
          (run, secrets.token_bytes(32), secrets.token_bytes(32)))
    rpc(uri, 'observer_fallback_job', job)
    claim, window = query(uri, """select extract(epoch from j.expires_at-now()),extract(epoch from s.expires_at-now())
        from private.observer_jobs j join private.observer_sessions s on s.run_id=j.run_id where j.id=%s""", (job,))[0]
    assert 29 * 60 < claim <= 30 * 60
    assert 53 * 60 < window <= 54 * 60
    query(uri, "update private.observer_jobs set status='failed' where status in ('queued','dispatched','claimed')")
    query(uri, 'update private.observer_installations set fallback_capacity=0')


def test_preparation_jobs_never_change_organization(setup):
    """observer-job only accepts a preparation job's repository in the job's own organization."""
    s = setup; uri = s['uri']
    fallback_ready(uri, 1)
    query(uri, """insert into private.observer_installations
        (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
        values(%s,'1009',2009,'3009',%s,true) on conflict(organization) do update set enabled=true""",
          (ORG + '9', 'a' * 40))
    revision = rpc(uri, 'observer_create_project', 'Moved preparation', 'repository',
                   'https://github.com/example/project', role='authenticated', user=s['user'])
    job = uuid.uuid4()
    rpc(uri, 'observer_enqueue_job', job, 'prepare', None, revision, ORG + '8',
        secrets.token_urlsafe(32), 'encrypted job payload', 'encrypted nonce')
    with pytest.raises(psycopg.Error, match='fallback_unavailable'):
        rpc(uri, 'observer_fallback_job', job)
    with pytest.raises(psycopg.Error, match='job_conflict'):
        rpc(uri, 'observer_failover_job', job, ORG + '9')
    # Where the fallback runner is the job's own organization, it may take it.
    query(uri, "update private.observer_jobs set organization=%s where id=%s", (ORG + '13', job))
    assert rpc(uri, 'observer_fallback_job', job)['organization'] == ORG + '13'
    query(uri, "update private.observer_jobs set status='failed' where status in ('queued','dispatched','claimed')")
    query(uri, 'update private.observer_installations set fallback_capacity=0')
    query(uri, 'update private.observer_installations set enabled=false where organization=%s', (ORG + '9',))


def pending(uri):
    return {j['id']: j for j in rpc(uri, 'observer_pending_jobs', 20)}


def test_pending_jobs_name_the_fallback_reason_only_while_a_slot_is_free(setup):
    s = setup; uri = s['uri']
    fallback_ready(uri, 1)
    fresh = queued_job(s, ORG + '8')
    assert pending(uri)[str(fresh)]['fallback'] is None
    # GitHub accepted three dispatches but no run claimed the job.
    stalled = queued_job(s, ORG + '8')
    query(uri, """update private.observer_jobs set status='dispatched',dispatch_count=3,
        last_dispatch_at=now()-interval '5 minutes' where id=%s""", (stalled,))
    assert str(stalled) not in pending(uri)
    query(uri, "update private.observer_jobs set last_dispatch_at=now()-interval '11 minutes' where id=%s", (stalled,))
    query(uri, 'update private.observer_installations set fallback_capacity=0')
    assert str(stalled) not in pending(uri)
    query(uri, 'update private.observer_installations set fallback_capacity=1 where organization=%s', (ORG + '13',))
    assert pending(uri)[str(stalled)]['fallback'] == 'stalled'
    # Every enabled organization over its monthly minute cap.
    query(uri, 'update private.observer_installations set monthly_minute_limit=0')
    over = queued_job(s, ORG + '8')
    assert pending(uri)[str(over)]['fallback'] == 'over_limit'
    # A job GitHub already accepted keeps waiting for its hosted run.
    query(uri, """update private.observer_jobs set status='dispatched',dispatch_count=1,
        last_dispatch_at=now()-interval '3 minutes' where id=%s""", (over,))
    assert pending(uri)[str(over)]['fallback'] is None
    query(uri, 'update private.observer_installations set monthly_minute_limit=1800')
    # A self-hosted run accepted by GitHub waits for its runner instead of being re-dispatched.
    waiting = queued_job(s, ORG + '8')
    rpc(uri, 'observer_fallback_job', waiting)
    query(uri, """update private.observer_jobs set status='dispatched',
        last_dispatch_at=now()-interval '5 minutes' where id=%s""", (waiting,))
    assert str(waiting) not in pending(uri)
    query(uri, "update private.observer_jobs set status='queued' where id=%s", (waiting,))
    retried = pending(uri)[str(waiting)]
    assert (retried['runner'], retried['fallback'], retried['organization']) == ('self-hosted', None, ORG + '13')
    query(uri, "update private.observer_jobs set status='failed' where status in ('queued','dispatched','claimed')")
    query(uri, 'update private.observer_installations set fallback_capacity=0')


def fallback_avoiding(uri, job, avoid):
    return query(uri, 'select public.observer_fallback_job(%s,%s::text[])', (job, avoid))[0][0]


def test_fallback_avoids_failed_organizations_and_moves_split_runs_together(setup):
    s = setup; uri = s['uri']
    fallback_ready(uri, 1)
    query(uri, """insert into private.observer_installations
        (organization,organization_id,installation_id,repository_id,approved_sha,enabled,fallback_capacity)
        values(%s,'1014',2014,'3014',%s,true,2) on conflict(organization) do update
        set enabled=true,fallback_capacity=2""", (ORG + '14', 'e' * 40))
    lone = queued_job(s, ORG + '8')
    with pytest.raises(psycopg.Error, match='fallback_unavailable'):
        fallback_avoiding(uri, lone, [ORG + '13', ORG + '14'])
    assert fallback_avoiding(uri, lone, [ORG + '14'])['organization'] == ORG + '13'
    # A split run's execute and engine jobs must run together: two slots or none.
    batch, run = uuid.uuid4(), uuid.uuid4()
    query(uri, "insert into public.observer_batches(id,team_id,user_id,phase_id,mode) values(%s,%s,%s,%s,'local')",
          (batch, s['team'], s['user'], s['phase']))
    query(uri, "insert into public.observer_runs(id,batch_id,scenario_id,status) values(%s,%s,%s,'queued')",
          (run, batch, s['scenario']))
    engine, execute = uuid.uuid4(), uuid.uuid4()
    for job, kind in ((engine, 'engine'), (execute, 'execute')):
        rpc(uri, 'observer_enqueue_job', job, kind, run, None, ORG + '8',
            secrets.token_urlsafe(32), 'encrypted job payload', 'encrypted nonce')
    query(uri, "update private.observer_jobs set status='dispatched',dispatch_count=3 where id=%s", (execute,))
    with pytest.raises(psycopg.Error, match='fallback_unavailable'):
        fallback_avoiding(uri, engine, [ORG + '14'])
    assert rpc(uri, 'observer_fallback_job', engine)['organization'] == ORG + '14'
    rows = query(uri, """select id,organization,runner,status,dispatch_count,last_dispatch_at is null
        from private.observer_jobs where run_id=%s order by kind""", (run,))
    assert rows == [(engine, ORG + '14', 'self-hosted', 'queued', 1, False),
                    (execute, ORG + '14', 'self-hosted', 'queued', 0, True)]
    # Fallback minutes and load never count against GitHub-hosted organizations.
    query(uri, """update private.observer_jobs set status='succeeded',claimed_at=now()-interval '40 hours',
        finished_at=now() where run_id=%s""", (run,))
    loads = {r[0]: r[1:] for r in query(uri, """select organization,month_minutes,active_jobs,over_limit
        from public.observer_organizations_by_load()""")}
    assert loads[ORG + '14'][1] == 0 and loads[ORG + '13'][1] == 0
    assert loads[ORG + '14'][0] < 1800 and not loads[ORG + '14'][2]
    query(uri, "update private.observer_jobs set status='failed' where status in ('queued','dispatched','claimed')")
    query(uri, 'update private.observer_installations set fallback_capacity=0')
    query(uri, 'update private.observer_installations set enabled=false where organization=%s', (ORG + '14',))
