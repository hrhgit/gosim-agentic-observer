"""Run admission commits sessions and all paired jobs in one transaction."""
import concurrent.futures
import secrets
import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from test_project_database import database, identity, query, rpc, setup  # noqa: F401


@pytest.fixture
def queued(setup):
    s = setup
    query(s['uri'], """insert into private.observer_installations
        (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
        values('AGENTIC-OBSERVER26-runner-1','101',202,'303',%s,true)
        on conflict(organization) do update set enabled=true""", ('a'*40,))
    query(s['uri'], 'insert into private.observer_scenario_bundles values(%s,%s,%s)',
          (s['scenario'], f"{s['scenario']}/bundle.zip", 'b'*64))
    batch = rpc(s['uri'], 'observer_create_batch', s['phase'], None, role='authenticated', user=s['user'])
    run = query(s['uri'], 'select id from public.observer_runs where batch_id=%s', (batch,))[0][0]
    return {**s, 'run': run, 'batch': batch}


def reserve(s):
    return next(r for r in rpc(s['uri'], 'observer_pending_runs', 10) if r['id'] == str(s['run']))


def job(kind):
    return dict(id=str(uuid.uuid4()), kind=kind, nonce=secrets.token_urlsafe(32),
                encrypted_input='encrypted payload', encrypted_nonce='encrypted nonce')


def schedule(s, lease, jobs=None, org='AGENTIC-OBSERVER26-runner-1', local='encrypted local credential'):
    return rpc(s['uri'], 'observer_schedule_run', s['run'], lease, org,
               secrets.token_urlsafe(32), secrets.token_urlsafe(32), local, jobs or [job('engine')])


def test_concurrent_schedulers_reserve_one_run_once(queued):
    s = queued
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        reservations = list(pool.map(lambda _: rpc(s['uri'], 'observer_pending_runs', 10), range(6)))
    ours = [r for rs in reservations for r in rs if r['id'] == str(s['run'])]
    assert len(ours) == 1
    assert ours[0]['mode'] == 'local' and ours[0]['scenario_digest'] == 'b'*64
    assert ours[0]['archive_ref'] is None


def test_local_activation_is_atomic_and_lost_response_does_not_rotate_credentials(queued):
    s = queued; lease = reserve(s)['lease']
    with pytest.raises(psycopg.Error, match='runner_not_configured'):
        schedule(s, lease, org='AGENTIC-OBSERVER26-runner-6')
    assert query(s['uri'], 'select status from public.observer_runs where id=%s', (s['run'],)) == [('queued',)]
    assert query(s['uri'], 'select run_id from private.observer_sessions where run_id=%s', (s['run'],)) == []
    assert query(s['uri'], 'select run_id from private.observer_local_credentials where run_id=%s', (s['run'],)) == []
    schedule(s, lease)
    before = query(s['uri'], 'select participant_hash,engine_hash from private.observer_sessions where run_id=%s', (s['run'],))
    schedule(s, lease)
    assert query(s['uri'], 'select participant_hash,engine_hash from private.observer_sessions where run_id=%s', (s['run'],)) == before
    assert query(s['uri'], 'select kind from private.observer_jobs where run_id=%s', (s['run'],)) == [('engine',)]
    assert rpc(s['uri'], 'observer_local_access', s['run'], s['user'])['encrypted_credential'] == 'encrypted local credential'


def test_cloud_admission_requires_both_jobs_and_has_no_local_credential(queued):
    s = queued; uri = s['uri']
    rev = rpc(uri, 'observer_create_project', 'Cloud', 'repository', 'https://github.com/example/project',
              role='authenticated', user=s['user'])
    query(uri, """update public.observer_revisions set status='reviewable',source_digest=%s,approval_digest=%s,
        manifest=%s,public_test='{"passed":true}' where id=%s""",
          ('c'*64, 'd'*64, Jsonb({'image':'python@sha256:'+'e'*64}), rev))
    rpc(uri, 'observer_approve_revision', rev, 'd'*64, role='authenticated', user=s['user'])
    query(uri, "update public.observer_batches set mode='project',revision_id=%s where id=%s", (rev, s['batch']))
    assert all(r['id'] != str(s['run']) for r in rpc(uri, 'observer_pending_runs', 10))
    ref = 'github:AGENTIC-OBSERVER26-runner-1/participant-' + s['user'].hex + '@' + 'f'*40
    query(uri, 'insert into private.observer_materializations values(%s,%s,%s)', (rev, ref, 'a'*64))
    reserved = reserve(s)
    assert reserved['archive_ref'] == ref and reserved['materialized_digest'] == 'a'*64
    with pytest.raises(psycopg.Error, match='invalid_run_jobs'):
        schedule(s, reserved['lease'], local=None)
    schedule(s, reserved['lease'], [job('engine'), job('execute')], local=None)
    assert len(query(uri, 'select id from private.observer_jobs where run_id=%s', (s['run'],))) == 2
    assert rpc(uri, 'observer_local_access', s['run'], s['user']) is None


def test_colocated_cloud_run_is_admitted_with_one_engine_job(queued):
    s = queued; uri = s['uri']
    rev = rpc(uri, 'observer_create_project', 'Cloud', 'repository', 'https://github.com/example/project',
              role='authenticated', user=s['user'])
    query(uri, """update public.observer_revisions set status='reviewable',source_digest=%s,approval_digest=%s,
        manifest=%s,public_test='{"passed":true}' where id=%s""",
          ('c'*64, 'd'*64, Jsonb({'image':'python@sha256:'+'e'*64}), rev))
    rpc(uri, 'observer_approve_revision', rev, 'd'*64, role='authenticated', user=s['user'])
    query(uri, "update public.observer_batches set mode='project',revision_id=%s where id=%s", (rev, s['batch']))
    query(uri, 'insert into private.observer_materializations values(%s,%s,%s)',
          (rev, 'github:AGENTIC-OBSERVER26-runner-1/participant-' + s['user'].hex + '@' + 'f'*40, 'a'*64))
    query(uri, 'update public.observer_phase_settings set colocated=true where phase_id=%s', (s['phase'],))
    reserved = reserve(s)
    with pytest.raises(psycopg.Error, match='invalid_run_jobs'):
        schedule(s, reserved['lease'], [job('engine'), job('execute')], local=None)
    schedule(s, reserved['lease'], [job('engine')], local=None)
    assert query(uri, 'select kind from private.observer_jobs where run_id=%s', (s['run'],)) == [('engine',)]


def test_expired_lease_cannot_open_session_and_budget_exhaustion_parks_not_fails(queued):
    s = queued; first = reserve(s)
    query(s['uri'], "update private.observer_run_leases set expires_at=now()-interval '1 second' where run_id=%s", (s['run'],))
    second = reserve(s)
    assert first['lease'] != second['lease']
    with pytest.raises(psycopg.Error, match='run_lease_invalid'):
        schedule(s, first['lease'])
    # A scheduling lease that has been retrying for more than the ~2h budget
    # is parked, never failed: the contestant keeps seeing "queued" and an
    # incident pages the organizers instead.
    query(s['uri'], "update private.observer_run_leases set first_leased_at=now()-interval '3 hours',"
                    "expires_at=now()-interval '1 second' where run_id=%s", (s['run'],))
    rpc(s['uri'], 'observer_pending_runs', 10)
    assert query(s['uri'], 'select status,score from public.observer_runs where id=%s', (s['run'],)) == [('queued', None)]
    assert query(s['uri'], 'select status from public.observer_batches where id=%s', (s['batch'],)) == [('queued',)]
    assert query(s['uri'], 'select paused_at is not null from private.observer_run_leases where run_id=%s', (s['run'],)) == [(True,)]
    assert query(s['uri'], "select count(*) from private.observer_incidents where subject_type='run' and subject_id=%s and resolved_at is null",
                 (s['run'],)) == [(1,)]
    # A parked run no longer gets a new lease on its own...
    before = query(s['uri'], 'select expires_at from private.observer_run_leases where run_id=%s', (s['run'],))
    rpc(s['uri'], 'observer_pending_runs', 10)
    assert query(s['uri'], 'select expires_at from private.observer_run_leases where run_id=%s', (s['run'],)) == before
    # ...until an organizer requeues it, which also resolves the incident.
    admin, _ = identity(s['uri'])
    query(s['uri'], 'update public.profiles set is_admin=true where id=%s', (admin,))
    rpc(s['uri'], 'observer_admin_requeue', 'run', s['run'], role='authenticated', user=admin)
    assert query(s['uri'], "select count(*) from private.observer_incidents where subject_type='run' and subject_id=%s and resolved_at is null",
                 (s['run'],)) == [(0,)]
    assert query(s['uri'], 'select paused_at is null,attempts from private.observer_run_leases where run_id=%s', (s['run'],)) == [(True, 1)]


def test_disabled_phase_and_participants_cannot_open_background_jobs(queued):
    s = queued; lease = reserve(s)['lease']
    for stmt,args in [
        ('select * from private.observer_scenario_bundles', ()),
        ('select * from private.observer_materializations', ()),
        ('select public.observer_pending_runs(10)', ()),
        ('select public.observer_run_schedule_error(%s,%s,\'x\')', (s['run'], lease)),
    ]:
        with pytest.raises(psycopg.Error, match='permission denied'):
            query(s['uri'], stmt, args, role='authenticated', user=s['user'])
    query(s['uri'], 'update public.observer_phase_settings set local_sessions_enabled=false where phase_id=%s', (s['phase'],))
    with pytest.raises(psycopg.Error, match='run_not_eligible'):
        schedule(s, lease)


def test_local_scenarios_start_sequentially_without_waiting_for_csv_upload(queued):
    s=queued; uri=s['uri']; other=uuid.uuid4(); second=uuid.uuid4()
    query(uri,"insert into public.scenarios(id,slug,name) values(%s,%s,'Second')",(other,str(other)))
    query(uri,'insert into private.observer_scenario_bundles values(%s,%s,%s)',(other,f'{other}/bundle.zip','c'*64))
    query(uri,'insert into public.observer_runs(id,batch_id,scenario_id) values(%s,%s,%s)',(second,s['batch'],other))
    selected=rpc(uri,'observer_pending_runs',10)
    ours=[r for r in selected if r['id'] in (str(s['run']),str(second))]
    assert len(ours)==1 and ours[0]['id']==str(s['run'])
    schedule(s,ours[0]['lease'])
    assert all(r['id']!=str(second) for r in rpc(uri,'observer_pending_runs',10))
    query(uri,"update public.observer_runs set status='awaiting_csv' where id=%s",(s['run'],))
    assert any(r['id']==str(second) for r in rpc(uri,'observer_pending_runs',10))
