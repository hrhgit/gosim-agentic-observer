"""Atomic dispatch/claim tests on the real PostgreSQL schema."""
from __future__ import annotations

import concurrent.futures
import secrets
import uuid

import psycopg
import pytest

from test_project_database import database, query, rpc, session, setup  # noqa: F401


@pytest.fixture
def job(setup):
    s=setup; uri=s["uri"]
    query(uri,"""insert into private.observer_installations
        (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
        values('AGENTIC-OBSERVER26-runner-1','101',202,'303',%s,true)
        on conflict(organization) do update set enabled=true""",("a"*40,))
    run,_,_=session(s)
    job_id=uuid.uuid4(); nonce=secrets.token_urlsafe(32)
    rpc(uri,"observer_enqueue_job",job_id,"engine",run,None,"AGENTIC-OBSERVER26-runner-1",nonce,"encrypted job payload","encrypted nonce")
    return {**s,"job":job_id,"nonce":nonce,"run":run}


def claim(s,github_run="404",attempt="1"):
    return rpc(s["uri"],"observer_claim_job",s["job"],s["nonce"],github_run,attempt,"303","101","a"*40)


def test_participants_cannot_enqueue_claim_read_or_complete_jobs(job):
    s=job
    for statement,args in [
        ("select * from private.observer_jobs",()),
        ("select public.observer_job_identity(%s)",(s["job"],)),
        ("select public.observer_claim_job(%s,%s,'404','1','303','101',%s)",(s["job"],s["nonce"],"a"*40)),
        ("select public.observer_finish_job(%s,'404','1','{}','')",(s["job"],)),
        ("select public.observer_job_artifact_target(%s,'404','1')",(s["job"],)),
    ]:
        with pytest.raises(psycopg.Error,match="permission denied"):
            query(s["uri"],statement,args,role="authenticated",user=s["user"])


def test_private_diagnostics_are_team_scoped_and_exclude_job_credentials(job):
    from test_project_database import identity
    s=job;uri=s['uri'];other,_=identity(uri);teammate,_=identity(uri,team=s['team'])
    claim(s)
    rpc(uri,'observer_finish_job',s['job'],'404','1',{'diagnostics':{'code':'completed','log':'MODEL_PROXY_OK'},
        'extra_secret':'must not be returned'},'')
    for user in (s['user'],teammate):
        result=rpc(uri,'observer_diagnostics',None,s['run'],role='authenticated',user=user)
        assert len(result)==1 and result[0]['log']=='MODEL_PROXY_OK'
        assert 'must not be returned' not in str(result) and 'encrypted' not in str(result)
    with pytest.raises(psycopg.Error,match='diagnostics_not_found'):
        rpc(uri,'observer_diagnostics',None,s['run'],role='authenticated',user=other)
    with pytest.raises(psycopg.Error,match='permission denied'):
        rpc(uri,'observer_diagnostics',None,s['run'],role='anon')


def test_artifact_credential_scope_comes_from_claimed_job_and_excludes_executors(job):
    s=job;uri=s['uri']
    with pytest.raises(psycopg.Error,match='artifact_access_denied'):
        rpc(uri,'observer_job_artifact_target',s['job'],'404','1')
    claim(s)
    value=rpc(uri,'observer_job_artifact_target',s['job'],'404','1')
    assert value=={'user_id':str(s['user']),'artifact_id':str(s['run']),'kind':'engine'}
    with pytest.raises(psycopg.Error,match='artifact_access_denied'):
        rpc(uri,'observer_job_artifact_target',s['job'],'other-job','1')
    query(uri,"update private.observer_jobs set kind='execute' where id=%s",(s['job'],))
    with pytest.raises(psycopg.Error,match='artifact_access_denied'):
        rpc(uri,'observer_job_artifact_target',s['job'],'404','1')


def test_claim_requires_dispatch_nonce_and_matching_immutable_repository_identity(job):
    s=job; uri=s["uri"]
    identity=rpc(uri,"observer_job_identity",s["job"])
    assert identity["repositoryId"]=="303" and identity["workflow"]=="observer-engine.yml"
    assert "encrypted" not in str(identity) and s["nonce"] not in str(identity)
    good=[s["job"],s["nonce"],"404","1","303","101","a"*40]
    for index,value in [(1,"wrong"),(4,"999"),(5,"999"),(6,"b"*40)]:
        args=good.copy(); args[index]=value
        with pytest.raises(psycopg.Error,match="job_identity_mismatch"):
            rpc(uri,"observer_claim_job",*args)
    assert claim(s)=="encrypted job payload"
    assert claim(s)=="encrypted job payload"
    with pytest.raises(psycopg.Error,match="job_already_claimed"):
        claim(s,attempt="2")
    with pytest.raises(psycopg.Error,match="job_already_claimed"):
        claim(s,github_run="405")


def test_duplicate_github_dispatches_can_only_start_one_machine(job):
    s=job
    def attempt(number):
        try:
            return claim(s,github_run=str(number+1000))
        except psycopg.Error as exc:
            assert "job_already_claimed" in str(exc)
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(attempt,range(8)))
    assert results.count("encrypted job payload")==1
    assert results.count(None)==7


def test_job_completion_is_bound_to_claim_and_retries_do_not_rewrite_results(job):
    s=job; uri=s["uri"]
    claim(s)
    with pytest.raises(psycopg.Error,match="job_identity_mismatch"):
        rpc(uri,"observer_finish_job",s["job"],"405","1",{"done":True},"")
    rpc(uri,"observer_finish_job",s["job"],"404","1",{"done":True},"")
    rpc(uri,"observer_finish_job",s["job"],"404","1",{"done":True},"")
    # Metadata remains available for verifying a retry after a lost HTTP response.
    assert rpc(uri,"observer_job_identity",s["job"])["runId"]=="404"
    with pytest.raises(psycopg.Error,match="job_result_conflict"):
        rpc(uri,"observer_finish_job",s["job"],"404","1",{"done":False},"")
    with pytest.raises(psycopg.Error,match="job_unavailable"):
        claim(s)
    # Job completion cannot write a survey score.
    assert query(uri,"select status,score from public.observer_runs where id=%s",(s["run"],))==[("starting",None)]


def test_expiration_and_disabling_installation_revoke_job_access(job):
    s=job; uri=s["uri"]
    query(uri,"update private.observer_jobs set expires_at=now()-interval '1 second' where id=%s",(s["job"],))
    assert rpc(uri,"observer_job_identity",s["job"]) is None
    with pytest.raises(psycopg.Error,match="job_unavailable"): claim(s)
    query(uri,"update private.observer_jobs set expires_at=now()+interval '1 hour' where id=%s",(s["job"],))
    query(uri,"update private.observer_installations set enabled=false where organization='AGENTIC-OBSERVER26-runner-1'")
    assert rpc(uri,"observer_job_identity",s["job"]) is None
    with pytest.raises(psycopg.Error,match="job_unavailable"): claim(s)


def test_dispatch_queue_reserves_attempts_atomically_and_preserves_ambiguous_claims(job):
    s=job; uri=s["uri"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        batches=list(pool.map(lambda _:rpc(uri,"observer_pending_jobs",20),range(4)))
    ids=[entry["id"] for batch in batches for entry in batch]
    assert len(ids)==len(set(ids)) and str(s["job"]) in ids
    picked=next(entry for batch in batches for entry in batch if entry["id"]==str(s["job"]))
    assert picked["encrypted_nonce"]=="encrypted nonce"
    assert s["nonce"] not in str(picked)
    rpc(uri,"observer_dispatch_error",s["job"],"github_unavailable")
    assert claim(s)=="encrypted job payload"
    assert all(j["id"]!=str(s["job"]) for j in rpc(uri,"observer_pending_jobs",20))


def test_expired_jobs_requeue_the_run_without_inventing_a_score_or_failing_the_contestant(job):
    # A dispatch/claim-lease timeout is always ours, never the contestant's --
    # no participant code even ran -- so it requeues instead of failing.
    s=job; uri=s["uri"]
    query(uri,"update private.observer_jobs set expires_at=now()-interval '1 second' where id=%s",(s["job"],))
    assert rpc(uri,"observer_reconcile_jobs")>=1
    assert query(uri,"select status,score,error from public.observer_runs where id=%s",(s["run"],))==[("queued",None,"")]
    assert query(uri,"select b.status,b.score from public.observer_batches b join public.observer_runs r on r.batch_id=b.id where r.id=%s",
                 (s["run"],))==[("running",None)]


def test_platform_job_failure_requeues_run_and_never_overwrites_completed_score(job):
    # An empty/unrecognized diagnostics code on a failed job counts as a
    # platform failure (never the contestant's), so it requeues the run
    # instead of failing it.
    s=job;uri=s["uri"]
    query(uri,"update private.observer_jobs set kind='execute' where id=%s",(s["job"],))
    claim(s)
    rpc(uri,"observer_finish_job",s["job"],"404","1",{},"execute_job_failed")
    rpc(uri,"observer_finish_job",s["job"],"404","1",{},"execute_job_failed")
    assert query(uri,"select status,score,error from public.observer_runs where id=%s",(s["run"],))==[("queued",None,"")]
    assert query(uri,"select b.status from public.observer_batches b join public.observer_runs r on r.batch_id=b.id where r.id=%s",
                 (s["run"],))==[("running",)]


def test_contestant_project_failure_immediately_fails_run_and_never_overwrites_completed_score(job):
    # project_operation_failed on an execute job is the contestant's own code
    # crashing/timing out/violating the protocol: shown plainly, no retry.
    s=job;uri=s["uri"]
    query(uri,"update private.observer_jobs set kind='execute' where id=%s",(s["job"],))
    claim(s)
    rpc(uri,"observer_finish_job",s["job"],"404","1",{"diagnostics":{"stage":"execute","code":"project_operation_failed"}},"execute_job_failed")
    assert query(uri,"select status,score,error from public.observer_runs where id=%s",(s["run"],))==[("failed",None,"execute_job_failed")]
    assert query(uri,"select b.status from public.observer_batches b join public.observer_runs r on r.batch_id=b.id where r.id=%s",
                 (s["run"],))==[("failed",)]


def test_late_receipt_error_after_trusted_score_publication_cannot_delete_the_score(job):
    s=job;uri=s["uri"]
    # Free the team's one-active-batch slot held by the `job` fixture's own batch.
    query(uri,"update public.observer_batches set status='cancelled',finished_at=now() where id="
               "(select batch_id from public.observer_runs where id=%s)",(s["run"],))
    run,_,_=session(s)
    second=uuid.uuid4();nonce=secrets.token_urlsafe(32)
    rpc(uri,"observer_enqueue_job",second,"execute",run,None,"AGENTIC-OBSERVER26-runner-1",nonce,"encrypted job payload","encrypted nonce")
    rpc(uri,"observer_claim_job",second,nonce,"405","1","303","101","a"*40)
    query(uri,"update public.observer_runs set status='scored',score=123,finished_at=now() where id=%s",(run,))
    rpc(uri,"observer_finish_job",second,"405","1",{},"lost_receipt")
    assert query(uri,"select status,score from public.observer_runs where id=%s",(run,))==[("scored",123)]


@pytest.mark.parametrize("terminal_status",["failed","succeeded"])
def test_a_retry_may_reenqueue_the_same_run_and_kind_once_the_old_job_is_terminal(job,terminal_status):
    # The retry/backoff system mints a brand-new job id on every attempt; a revision or run
    # that fails once and gets requeued must be able to get a second job row, not collide
    # with unique(run_id,kind) forever (the root cause of revisions/runs parked permanently
    # by the ~2h retry budget without ever creating a second job).
    s=job;uri=s["uri"]
    query(uri,"update private.observer_jobs set status=%s,finished_at=now() where id=%s",(terminal_status,s["job"]))
    second=uuid.uuid4();nonce=secrets.token_urlsafe(32)
    rpc(uri,"observer_enqueue_job",second,"engine",s["run"],None,"AGENTIC-OBSERVER26-runner-1",nonce,"encrypted job payload","encrypted nonce")
    assert query(uri,"select id,status from private.observer_jobs where run_id=%s",(s["run"],))==[(second,"queued")]


def test_a_still_active_job_still_blocks_a_second_enqueue_for_the_same_run_and_kind(job):
    s=job;uri=s["uri"]
    second=uuid.uuid4();nonce=secrets.token_urlsafe(32)
    with pytest.raises(psycopg.Error,match="duplicate key value violates unique constraint"):
        rpc(uri,"observer_enqueue_job",second,"engine",s["run"],None,"AGENTIC-OBSERVER26-runner-1",nonce,"encrypted job payload","encrypted nonce")
    assert query(uri,"select count(*) from private.observer_jobs where run_id=%s",(s["run"],))==[(1,)]
