"""Preparation receipts cannot bypass public testing or participant approval."""
import concurrent.futures
import secrets
import uuid

import psycopg
import psycopg.types.json
import pytest

from test_project_database import database, identity, query, rpc, setup  # noqa: F401
from test_project_orchestration import job


@pytest.fixture
def preparation(setup):
    s=setup;uri=s['uri']
    query(uri,"""insert into private.observer_installations
      (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
      values('AGENTIC-OBSERVER26-runner-1','101',202,'303',%s,true)
      on conflict(organization) do update set enabled=true""",('a'*40,))
    query(uri,'update public.scenarios set events_public=true where id=%s',(s['scenario'],))
    query(uri,'insert into private.observer_scenario_bundles values(%s,%s,%s)',(s['scenario'],f"{s['scenario']}/bundle.zip",'b'*64))
    query(uri,"""insert into private.observer_preparation_config(id,phase_id,scenario_id,model,enabled)
      values(true,%s,%s,'test-model',true) on conflict(id) do update
      set phase_id=excluded.phase_id,scenario_id=excluded.scenario_id,enabled=true""",(s['phase'],s['scenario']))
    rev=rpc(uri,'observer_create_project','My complete project','repository','https://github.com/example/project',
            role='authenticated',user=s['user'])
    return {**s,'revision':rev}


def reserve(s):
    return next(r for r in rpc(s['uri'],'observer_pending_preparations',10) if r['id']==str(s['revision']))


def start(s,reserved=None):
    reserved=reserved or reserve(s);j=job('prepare');participant=secrets.token_urlsafe(32)
    rpc(s['uri'],'observer_schedule_preparation',s['revision'],reserved['lease'],'AGENTIC-OBSERVER26-runner-1',
        participant,secrets.token_urlsafe(32),j)
    return {**reserved,'job':j,'participant':participant}


def finish(s,started,**changes):
    j=started['job'];repo='AGENTIC-OBSERVER26-runner-1/participant-'+s['user'].hex
    result={'status':'awaiting_public_test','revision_id':str(s['revision']), 'repository':repo,
        'source_digest':'c'*64,'source_commit':'d'*40,'materialized_digest':'e'*64,'approval_digest':'f'*64,
        'manifest':{'schema_version':'observer-project-v1','image':'python@sha256:'+'a'*64,'run':['python','agent.py']},
        'adapter_files':{},'explanation':'Existing interface','preview_path':'github:'+repo+'@'+'a'*40,**changes}
    rpc(s['uri'],'observer_claim_job',j['id'],j['nonce'],'404','1','303','101','a'*40)
    rpc(s['uri'],'observer_finish_job',j['id'],'404','1',result,'')
    rpc(s['uri'],'observer_reconcile_preparations')
    return result


def test_concurrent_preparation_reservations_cannot_create_two_model_sessions(preparation):
    s=preparation
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        values=list(pool.map(lambda _:rpc(s['uri'],'observer_pending_preparations',10),range(6)))
    ours=[v for group in values for v in group if v['id']==str(s['revision'])]
    assert len(ours)==1
    first=start(s,ours[0])
    start(s,ours[0])
    assert query(s['uri'],'select count(*) from private.observer_jobs where revision_id=%s',(s['revision'],))==[(1,)]
    assert query(s['uri'],'select token_limit,call_limit,concurrency_limit from private.observer_sessions where run_id=%s',
                 (first['model_run_id'],))==[(65536,1,1)]


def test_public_preview_is_required_and_excluded_from_formal_quotas_and_board(preparation):
    s=preparation;uri=s['uri'];started=start(s)
    # Internal adaptation neither blocks a formal batch nor consumes its daily slot.
    query(uri,'update public.observer_phase_settings set daily_batches=1 where phase_id=%s',(s['phase'],))
    formal=rpc(uri,'observer_create_batch',s['phase'],None,role='authenticated',user=s['user'])
    assert formal
    finish(s,started)
    assert rpc(uri,'observer_materialized_project',s['revision'],s['user']) is None
    assert query(uri,'select status from public.observer_revisions where id=%s',(s['revision'],))==[('preparing',)]
    with pytest.raises(psycopg.Error,match='revision_not_ready'):
        rpc(uri,'observer_approve_revision',s['revision'],'f'*64,role='authenticated',user=s['user'])
    with pytest.raises(psycopg.Error,match='invalid_or_expired_capability'):
        rpc(uri,'observer_poll',started['model_run_id'],started['participant'])
    preview=query(uri,'select preview_run_id from private.observer_preparations where revision_id=%s',(s['revision'],))[0][0]
    selected=next(r for r in rpc(uri,'observer_pending_runs',10) if r['id']==str(preview))
    assert selected['runtime_seconds']==300 and selected['materialized_digest']=='e'*64
    jobs=[job('engine'),job('execute')]
    rpc(uri,'observer_schedule_run',preview,selected['lease'],'AGENTIC-OBSERVER26-runner-1',
        secrets.token_urlsafe(32),secrets.token_urlsafe(32),None,jobs)
    # Even an official engine score is insufficient until both trusted jobs finish.
    query(uri,"""update public.observer_runs set status='scored',score=123,finished_at=now(),score_summary='{"termination_reason":"survey_complete","committed_action_count":1}' where id=%s""",(preview,))
    rpc(uri,'observer_reconcile_preparations')
    assert query(uri,'select status from public.observer_revisions where id=%s',(s['revision'],))==[('preparing',)]
    for i,j in enumerate(jobs):
        rpc(uri,'observer_claim_job',j['id'],j['nonce'],str(500+i),'1','303','101','a'*40)
        rpc(uri,'observer_finish_job',j['id'],str(500+i),'1',{'finished':True},'')
    rpc(uri,'observer_reconcile_preparations')
    rpc(uri,'observer_reconcile_preparations')
    assert query(uri,'select status,public_test->>\'passed\' from public.observer_revisions where id=%s',(s['revision'],))==[('reviewable','true')]
    assert rpc(uri,'observer_materialized_project',s['revision'],s['user']).startswith('github:AGENTIC-OBSERVER26-runner-1/')
    other,_=identity(uri)
    assert rpc(uri,'observer_materialized_project',s['revision'],other) is None
    query(uri,"update public.observer_batches set status='scored',score=123,finished_at=now() where purpose='preview' and revision_id=%s",(s['revision'],))
    assert query(uri,'select * from public.observer_leaderboard(%s)',(s['phase'],),role='anon')==[]
    rpc(uri,'observer_approve_revision',s['revision'],'f'*64,role='authenticated',user=s['user'])
    assert query(uri,'select status from public.observer_revisions where id=%s',(s['revision'],))==[('approved',)]


@pytest.mark.parametrize('change',[{'repository':'foreign/private'}, {'preview_path':'github:foreign/private@'+'a'*40},
    {'approval_digest':None},{'adapter_files':[]},{'status':'approved'}])
def test_malformed_preparation_receipt_requeues_instead_of_materializing_or_approving(preparation,change):
    # A successful job that returned a malformed result is a runtime bug, not
    # the contestant's fault: requeue instead of failing the revision.
    s=preparation;finish(s,start(s),**change)
    assert query(s['uri'],'select status,error from public.observer_revisions where id=%s',(s['revision'],))==[('queued','')]
    assert query(s['uri'],'select revision_id from private.observer_materializations where revision_id=%s',(s['revision'],))==[]
    assert query(s['uri'],'select paused_at,attempts from private.observer_preparations where revision_id=%s',(s['revision'],))==[(None,1)]
    # Not 'preparing'/'failed' any more, so there is nothing left to reconcile.
    assert rpc(s['uri'],'observer_reconcile_preparations')==0


def test_hidden_scenario_is_never_used_for_public_test(preparation):
    s=preparation;uri=s['uri']
    query(uri,'update public.scenarios set events_public=false where id=%s',(s['scenario'],))
    assert rpc(uri,'observer_pending_preparations',10)==[]
    query(uri,'update public.scenarios set events_public=true where id=%s',(s['scenario'],))
    started=start(s)
    query(uri,'update public.scenarios set weather_public=false where id=%s',(s['scenario'],))
    finish(s,started)
    # The organizer disabled the public-test scenario mid-flight: requeue, do
    # not show the contestant a failure for an organizer-side change.
    assert query(uri,'select status from public.observer_revisions where id=%s',(s['revision'],))==[('queued',)]


@pytest.mark.parametrize('summary',[{}, {'termination_reason':'agent_error','committed_action_count':1},
    {'termination_reason':'global_wallclock_expired','committed_action_count':0}])
def test_preview_cannot_pass_an_agent_error_or_zero_committed_actions(preparation,summary):
    from psycopg.types.json import Jsonb
    s=preparation;uri=s['uri'];finish(s,start(s))
    preview=query(uri,'select preview_run_id from private.observer_preparations where revision_id=%s',(s['revision'],))[0][0]
    query(uri,"update public.observer_runs set status='scored',score=10,score_summary=%s,finished_at=now() where id=%s",(Jsonb(summary),preview))
    rpc(uri,'observer_reconcile_preparations')
    assert query(uri,'select status,public_test->>\'passed\' from public.observer_revisions where id=%s',(s['revision'],))==[('failed','false')]
    with pytest.raises(psycopg.Error,match='revision_not_ready'):
        rpc(uri,'observer_approve_revision',s['revision'],'f'*64,role='authenticated',user=s['user'])


def test_platform_preparation_failure_revokes_model_access_and_requeues(preparation):
    # An empty/unrecognized diagnostics code on a failed prepare job is a
    # platform failure (runner crash, not the contestant's code): it still
    # revokes the adaptation model access immediately, but requeues the
    # revision instead of ever showing it "failed".
    s=preparation;started=start(s);j=started['job'];uri=s['uri']
    for stmt in ['select * from private.observer_preparation_config','select * from private.observer_preparations',
                 'select public.observer_pending_preparations(3)','select public.observer_reconcile_preparations()']:
        with pytest.raises(psycopg.Error,match='permission denied'):
            query(uri,stmt,role='authenticated',user=s['user'])
    rpc(uri,'observer_claim_job',j['id'],j['nonce'],'404','1','303','101','a'*40)
    rpc(uri,'observer_finish_job',j['id'],'404','1',{},'prepare_job_failed')
    assert query(uri,'select status from public.observer_runs where id=%s',(started['model_run_id'],))==[('cancelled',)]
    assert query(uri,'select status,error from public.observer_revisions where id=%s',(s['revision'],))==[('queued','')]
    new_model_run_id=query(uri,'select model_run_id from private.observer_preparations where revision_id=%s',(s['revision'],))[0][0]
    assert new_model_run_id!=started['model_run_id']
    # Not 'preparing'/'failed' any more, so there is nothing left to reconcile.
    assert rpc(uri,'observer_reconcile_preparations')==0


def test_contestant_build_failure_in_preparation_immediately_fails_with_the_log(preparation):
    # project_error with a log is the contestant's own adaptation/build/entry
    # point failing inside the sandbox: shown plainly, no retry.
    s=preparation;started=start(s);j=started['job'];uri=s['uri']
    rpc(uri,'observer_claim_job',j['id'],j['nonce'],'404','1','303','101','a'*40)
    rpc(uri,'observer_finish_job',j['id'],'404','1',
        {'diagnostics':{'stage':'prepare','code':'project_error','log':'entry point not found'}},'prepare_job_failed')
    assert query(uri,'select status,error from public.observer_revisions where id=%s',(s['revision'],))==\
        [('failed','Project preparation failed: entry point not found')]
    rpc(uri,'observer_reconcile_preparations')
    assert query(uri,'select status from public.observer_runs where id=%s',(started['model_run_id'],))==[('cancelled',)]
    assert rpc(uri,'observer_reconcile_preparations')==0


@pytest.mark.parametrize('log',['Model service is unavailable.','Model call failed (HTTP 503).'])
def test_model_proxy_outage_in_preparation_requeues_instead_of_failing(preparation,log):
    # Our own observer-model proxy being unreachable or erroring is reported
    # with the same diagnostics.code='project_error' as a genuine contestant
    # mistake, but these two exact messages are ours, not theirs: requeue.
    s=preparation;started=start(s);j=started['job'];uri=s['uri']
    rpc(uri,'observer_claim_job',j['id'],j['nonce'],'404','1','303','101','a'*40)
    rpc(uri,'observer_finish_job',j['id'],'404','1',
        {'diagnostics':{'stage':'prepare','code':'project_error','log':log}},'prepare_job_failed')
    assert query(uri,'select status,error from public.observer_revisions where id=%s',(s['revision'],))==[('queued','')]
    assert query(uri,'select status from public.observer_runs where id=%s',(started['model_run_id'],))==[('cancelled',)]
    new_model_run_id=query(uri,'select model_run_id from private.observer_preparations where revision_id=%s',(s['revision'],))[0][0]
    assert new_model_run_id!=started['model_run_id']
    assert rpc(uri,'observer_reconcile_preparations')==0


def test_contestants_own_model_setup_failure_in_preparation_still_fails_immediately(preparation):
    # "No model API is set up for your team..." is the contestant's own
    # account/config, not a proxy outage: unchanged, immediate, no retry.
    s=preparation;started=start(s);j=started['job'];uri=s['uri']
    rpc(uri,'observer_claim_job',j['id'],j['nonce'],'404','1','303','101','a'*40)
    rpc(uri,'observer_finish_job',j['id'],'404','1',
        {'diagnostics':{'stage':'prepare','code':'project_error','log':'No model API is set up for your team.'}},'prepare_job_failed')
    assert query(uri,'select status,error from public.observer_revisions where id=%s',(s['revision'],))==\
        [('failed','Project preparation failed: No model API is set up for your team.')]


def test_failed_public_test_remains_unapproved_but_owner_can_inspect_materialized_adapter(preparation):
    s=preparation;uri=s['uri'];finish(s,start(s))
    preview=query(uri,'select preview_run_id from private.observer_preparations where revision_id=%s',(s['revision'],))[0][0]
    query(uri,"update public.observer_runs set status='failed',error='build_failed',finished_at=now() where id=%s""",(preview,))
    rpc(uri,'observer_reconcile_preparations')
    assert query(uri,'select status,public_test->>\'passed\' from public.observer_revisions where id=%s',(s['revision'],))==[('failed','false')]
    assert rpc(uri,'observer_materialized_project',s['revision'],s['user']).startswith('github:')
    with pytest.raises(psycopg.Error,match='revision_not_ready'):
        rpc(uri,'observer_approve_revision',s['revision'],'f'*64,role='authenticated',user=s['user'])


def test_known_preparation_error_is_shown_on_the_revision(preparation):
    s=preparation;started=start(s);j=started['job'];uri=s['uri']
    rpc(uri,'observer_claim_job',j['id'],j['nonce'],'404','1','303','101','a'*40)
    rpc(uri,'observer_finish_job',j['id'],'404','1',
        {'diagnostics':{'stage':'prepare','code':'project_error','log':'Automatic adaptation could not identify the entry point.'}},
        'prepare_job_failed')
    assert query(uri,'select status,error from public.observer_revisions where id=%s',(s['revision'],))==[
        ('failed','Project preparation failed: Automatic adaptation could not identify the entry point.')]


def test_colocated_public_test_with_one_engine_job_makes_the_version_reviewable(preparation):
    s=preparation;uri=s['uri'];started=start(s)
    finish(s,started)
    query(uri,'update public.observer_phase_settings set colocated=true')
    preview=query(uri,'select preview_run_id from private.observer_preparations where revision_id=%s',(s['revision'],))[0][0]
    selected=next(r for r in rpc(uri,'observer_pending_runs',10) if r['id']==str(preview))
    engine=job('engine')
    rpc(uri,'observer_schedule_run',preview,selected['lease'],'AGENTIC-OBSERVER26-runner-1',
        secrets.token_urlsafe(32),secrets.token_urlsafe(32),None,[engine])
    query(uri,"""update public.observer_runs set status='scored',score=123,finished_at=now(),score_summary='{"termination_reason":"survey_complete","committed_action_count":1}' where id=%s""",(preview,))
    rpc(uri,'observer_reconcile_preparations')
    assert query(uri,'select status from public.observer_revisions where id=%s',(s['revision'],))==[('preparing',)]
    rpc(uri,'observer_claim_job',engine['id'],engine['nonce'],'600','1','303','101','a'*40)
    rpc(uri,'observer_finish_job',engine['id'],'600','1',{'finished':True},'')
    rpc(uri,'observer_reconcile_preparations')
    assert query(uri,'select status,public_test->>\'passed\' from public.observer_revisions where id=%s',(s['revision'],))==[('reviewable','true')]


@pytest.mark.parametrize('contract,reason,actions,passed', [
    ('v4-score-v1', 'agent_finished', 1, True),         # a v4 agent ended its own run with "finish"
    ('v4-score-v1', 'survey_complete', 5, True),
    ('v4-score-v1', 'global_wallclock_expired', 5, True),
    ('v4-score-v1', 'agent_finished', 0, False),        # still needs one committed decision
    ('v4-score-v1', 'agent_error', 5, False),
    ('challenge-score-v3', 'agent_finished', 5, False),  # v3 public tests keep the old rule
    ('challenge-score-v3', 'survey_complete', 1, True),
])
def test_v4_public_test_accepts_an_agent_finish(preparation, contract, reason, actions, passed):
    s=preparation;uri=s['uri']
    query(uri,'update public.scenarios set contract=%s where id=%s',(contract,s['scenario']))
    started=start(s)
    assert started['gameplay']==('v4' if contract=='v4-score-v1' else 'v3')
    finish(s,started)
    query(uri,'update public.observer_phase_settings set colocated=true')
    preview=query(uri,'select preview_run_id from private.observer_preparations where revision_id=%s',(s['revision'],))[0][0]
    selected=next(r for r in rpc(uri,'observer_pending_runs',10) if r['id']==str(preview))
    assert selected['runtime_seconds']==300  # the public-test cap is unchanged for v4
    engine=job('engine')
    rpc(uri,'observer_schedule_run',preview,selected['lease'],'AGENTIC-OBSERVER26-runner-1',
        secrets.token_urlsafe(32),secrets.token_urlsafe(32),None,[engine])
    summary={'schema_version':'observer-run-summary-v1','termination_reason':reason,'committed_action_count':actions,
             'score':{'total':-100.0}}
    query(uri,"update public.observer_runs set status='scored',score=-100,finished_at=now(),score_summary=%s where id=%s",
          (psycopg.types.json.Jsonb(summary),preview))
    rpc(uri,'observer_claim_job',engine['id'],engine['nonce'],'600','1','303','101','a'*40)
    rpc(uri,'observer_finish_job',engine['id'],'600','1',{'finished':True},'')
    rpc(uri,'observer_reconcile_preparations')
    rpc(uri,'observer_reconcile_preparations')
    assert query(uri,'select status,public_test->>\'passed\' from public.observer_revisions where id=%s',(s['revision'],))==[
        ('reviewable','true') if passed else ('failed','false')]


def test_a_second_prepare_job_can_be_enqueued_after_the_first_platform_failure_requeues(preparation):
    # private.observer_jobs has unique(revision_id,kind): before the fix, a revision whose
    # first prepare job failed for a platform reason (requeued, not failed) could never get a
    # second job -- observer_enqueue_job's plain insert hit that old row's unique constraint on
    # every later attempt, so the revision retried for the full budget and parked forever
    # without ever creating another job. This is the actual root cause of revisions reported
    # stuck "queued"/"preparing" indefinitely.
    s=preparation;uri=s['uri'];started=start(s);j=started['job']
    rpc(uri,'observer_claim_job',j['id'],j['nonce'],'404','1','303','101','a'*40)
    rpc(uri,'observer_finish_job',j['id'],'404','1',{'diagnostics':{'stage':'prepare','code':'project_operation_failed'}},'prepare_job_failed')
    assert query(uri,'select status from public.observer_revisions where id=%s',(s['revision'],))==[('queued',)]
    assert query(uri,'select count(*) from private.observer_jobs where revision_id=%s',(s['revision'],))==[(1,)]
    second=start(s)
    assert query(uri,'select count(*) from private.observer_jobs where revision_id=%s',(s['revision'],))==[(1,)]
    assert query(uri,'select id from private.observer_jobs where revision_id=%s',(s['revision'],))==[(uuid.UUID(second['job']['id']),)]
