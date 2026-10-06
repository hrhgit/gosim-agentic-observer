"""temporary_upload_lifecycle must never purge a zip source before its revision
is materialized, and scheduling must hard-fail (not silently retry) if a source
is ever cleaned while still in use. See 20261003003000_purge_requires_materialization_and_grace.sql."""
import secrets
import uuid

import psycopg
import pytest

from test_project_database import database, identity, query, rpc, setup  # noqa: F401
from test_project_orchestration import job
from test_project_preparation import finish, preparation, reserve, start  # noqa: F401


@pytest.fixture
def zip_preparation(preparation):
    """Same fixture as test_project_preparation.preparation, but the revision
    comes from the real upload portal flow (reserve -> submit_zip) instead of
    observer_create_project('repository', ...), so it has a backing
    private.observer_uploads row with consumed_at set."""
    s = preparation
    upload = uuid.uuid4()
    path = rpc(s['uri'], 'observer_reserve_upload', s['user'], upload, 'source')
    rev = rpc(s['uri'], 'observer_submit_zip', upload, 'My zip project', role='authenticated', user=s['user'])
    return {**s, 'revision': rev, 'upload': upload, 'path': path}


def expire_reservation(s):
    """Simulate the upload's expires_at (now 15 minutes from reservation) having
    already passed, as it will for any revision whose preparation takes longer
    than that to reach materialization."""
    query(s['uri'], "update private.observer_uploads set expires_at=now()-interval '1 second' where id=%s", (s['upload'],))


def test_expired_but_unmaterialized_zip_source_is_never_purge_eligible(zip_preparation):
    s = zip_preparation
    expire_reservation(s)
    assert query(s['uri'], 'select status from public.observer_revisions where id=%s', (s['revision'],)) == [('queued',)]
    assert rpc(s['uri'], 'observer_expired_uploads', 50) == []
    started = start(s)
    assert query(s['uri'], 'select status from public.observer_revisions where id=%s', (s['revision'],)) == [('preparing',)]
    assert rpc(s['uri'], 'observer_expired_uploads', 50) == []


def test_materialized_zip_source_has_a_seven_day_grace_before_purge(zip_preparation):
    s = zip_preparation
    uri = s['uri']
    expire_reservation(s)
    finish(s, start(s))
    assert query(uri, 'select revision_id from private.observer_materializations where revision_id=%s', (s['revision'],)) != []
    # Materialized moments ago, but still 'preparing' its public test: never purge-eligible.
    assert query(uri, 'select status from public.observer_revisions where id=%s', (s['revision'],)) == [('preparing',)]
    assert rpc(uri, 'observer_expired_uploads', 50) == []
    query(uri, "update private.observer_materializations set created_at=now()-interval '8 days' where revision_id=%s", (s['revision'],))
    assert rpc(uri, 'observer_expired_uploads', 50) == []
    # Finish the public test so the revision leaves 'preparing'.
    preview = query(uri, 'select preview_run_id from private.observer_preparations where revision_id=%s', (s['revision'],))[0][0]
    selected = next(r for r in rpc(uri, 'observer_pending_runs', 10) if r['id'] == str(preview))
    jobs = [job('engine'), job('execute')]
    rpc(uri, 'observer_schedule_run', preview, selected['lease'], 'AGENTIC-OBSERVER26-runner-1',
        secrets.token_urlsafe(32), secrets.token_urlsafe(32), None, jobs)
    query(uri, """update public.observer_runs set status='scored',score=123,finished_at=now(),
          score_summary='{"termination_reason":"survey_complete","committed_action_count":1}' where id=%s""", (preview,))
    for i, j in enumerate(jobs):
        rpc(uri, 'observer_claim_job', j['id'], j['nonce'], str(700 + i), '1', '303', '101', 'a' * 40)
        rpc(uri, 'observer_finish_job', j['id'], str(700 + i), '1', {'finished': True}, '')
    rpc(uri, 'observer_reconcile_preparations')
    assert query(uri, 'select status from public.observer_revisions where id=%s', (s['revision'],)) == [('reviewable',)]
    eligible = rpc(uri, 'observer_expired_uploads', 50)
    assert [u['id'] for u in eligible] == [str(s['upload'])]


def test_cleaned_source_hard_fails_scheduling_instead_of_retrying_forever(zip_preparation):
    s = zip_preparation
    reserved = reserve(s)
    # A source cleaned (object physically removed) while still consumed and
    # unmaterialized must never be treated as available again.
    query(s['uri'], 'update private.observer_uploads set cleaned_at=now() where id=%s', (s['upload'],))
    with pytest.raises(psycopg.Error, match='source_upload_unavailable'):
        rpc(s['uri'], 'observer_schedule_preparation', s['revision'], reserved['lease'], 'AGENTIC-OBSERVER26-runner-1',
            'participant-token', 'engine-token', {'id': str(uuid.uuid4()), 'kind': 'prepare', 'nonce': 'n',
            'encrypted_input': 'i', 'encrypted_nonce': 'n2'})
    assert query(s['uri'], 'select status from public.observer_revisions where id=%s', (s['revision'],)) == [('queued',)]


def test_admin_marks_source_missing_refunds_and_resolves_incident(zip_preparation):
    s = zip_preparation
    admin, _ = identity(s['uri'])
    query(s['uri'], 'update public.profiles set is_admin=true where id=%s', (admin,))
    # Still has the source: nothing to mark missing yet.
    with pytest.raises(psycopg.Error, match='source_still_available'):
        rpc(s['uri'], 'observer_admin_mark_source_missing', s['revision'], role='authenticated', user=admin)
    query(s['uri'], 'update private.observer_uploads set cleaned_at=now() where id=%s', (s['upload'],))
    query(s['uri'], """insert into private.observer_incidents(subject_type,subject_id,reason)
          values('revision',%s,'preparation_retry_budget_exhausted')""", (s['revision'],))
    # A defensive refund path even though a queued revision cannot normally
    # have reached a formal (quota-consuming) batch.
    batch = query(s['uri'], """insert into public.observer_batches(team_id,user_id,phase_id,revision_id,mode,purpose)
          values(%s,%s,%s,%s,'project','formal') returning id""", (s['team'], s['user'], s['phase'], s['revision']))[0][0]
    with pytest.raises(psycopg.Error, match='admin_only'):
        rpc(s['uri'], 'observer_admin_mark_source_missing', s['revision'], role='authenticated', user=s['user'])
    rpc(s['uri'], 'observer_admin_mark_source_missing', s['revision'], role='authenticated', user=admin)
    assert query(s['uri'], 'select status,error from public.observer_revisions where id=%s', (s['revision'],)) == [
        ('failed', 'The uploaded file is no longer available. Please re-upload this version; '
                    'it will not count against your submission quota.')]
    assert query(s['uri'], 'select quota_refunded,status from public.observer_batches where id=%s', (batch,)) == [(True, 'failed')]
    assert query(s['uri'], "select count(*) from private.observer_incidents where subject_type='revision' and subject_id=%s and resolved_at is null",
                 (s['revision'],)) == [(0,)]
    with pytest.raises(psycopg.Error, match='revision_not_stuck'):
        rpc(s['uri'], 'observer_admin_mark_source_missing', s['revision'], role='authenticated', user=admin)


def test_admin_mark_source_missing_rejects_non_zip_and_non_admin(preparation):
    s = preparation  # the base fixture uses source_kind='repository'
    admin, _ = identity(s['uri'])
    query(s['uri'], 'update public.profiles set is_admin=true where id=%s', (admin,))
    with pytest.raises(psycopg.Error, match='not_a_zip_revision'):
        rpc(s['uri'], 'observer_admin_mark_source_missing', s['revision'], role='authenticated', user=admin)
    with pytest.raises(psycopg.Error, match='admin_only'):
        rpc(s['uri'], 'observer_admin_mark_source_missing', s['revision'], role='authenticated', user=s['user'])
