"""Real database permissions for uploads, model credentials and design evidence."""
from __future__ import annotations

import uuid
import psycopg
import pytest

from test_project_database import database, setup, identity, query, rpc  # noqa: F401


def test_team_model_credentials_are_never_returned_and_cannot_be_overwritten_by_another_team(setup):
    s=setup; uri=s['uri']; provider=uuid.uuid4()
    other,other_team=identity(uri)
    args=[s['user'],provider,'Team API','https://models.example.test/v1','encrypted-do-not-return-key','{model-a}',10000,False]
    with pytest.raises(psycopg.Error,match='ephemeral_credentials_required'):
        rpc(uri,'observer_save_provider',*args)
    assert query(uri,'select id from private.observer_providers where id=%s',(provider,))==[]
    # A record retained from the old platform is still private and can be disabled.
    query(uri,"insert into private.observer_providers(id,team_id,name,base_url,encrypted_key,models,daily_token_limit) values(%s,%s,'Old API','https://models.example.test/v1','encrypted-do-not-return-key',array['model-a'],10000)",(provider,s['team']))
    listed=rpc(uri,'observer_list_providers',role='authenticated',user=s['user'])
    own=next(p for p in listed if p['id']==str(provider))
    assert own['models']==['model-a'] and own['shared'] is False
    assert 'encrypted' not in str(listed) and 'key' not in own
    assert all(p['id']!=str(provider) for p in rpc(uri,'observer_list_providers',role='authenticated',user=other))
    args[0]=other
    with pytest.raises(psycopg.Error,match='ephemeral_credentials_required'):rpc(uri,'observer_save_provider',*args)
    with pytest.raises(psycopg.Error,match='permission denied'):
        rpc(uri,'observer_save_provider',*args,role='authenticated',user=other)
    with pytest.raises(psycopg.Error,match='provider_not_found'):
        rpc(uri,'observer_disable_provider',provider,role='authenticated',user=other)
    with pytest.raises(psycopg.Error,match='provider_not_found'):
        rpc(uri,'observer_disable_provider',s['provider'],role='authenticated',user=s['user'])
    rpc(uri,'observer_disable_provider',provider,role='authenticated',user=s['user'])
    assert query(uri,'select enabled from private.observer_providers where id=%s',(provider,))==[(False,)]


def test_source_upload_retry_creates_one_revision_and_other_teams_cannot_consume_it(setup):
    s=setup;uri=s['uri'];upload=uuid.uuid4();other,_=identity(uri)
    path=rpc(uri,'observer_reserve_upload',s['user'],upload,'source')
    assert path==f"{s['team']}/{upload}/source.zip"
    assert rpc(uri,'observer_upload_access',other,upload,'source') is None
    assert rpc(uri,'observer_upload_access',s['user'],upload,'csv') is None
    with pytest.raises(psycopg.Error,match='upload_not_found'):
        rpc(uri,'observer_submit_zip',upload,'Copied',role='authenticated',user=other)
    first=rpc(uri,'observer_submit_zip',upload,'Project',role='authenticated',user=s['user'])
    second=rpc(uri,'observer_submit_zip',upload,'Network retry',role='authenticated',user=s['user'])
    assert first==second
    assert query(uri,'select count(*) from public.observer_revisions where source_location=%s',(path,))==[(1,)]
    with pytest.raises(psycopg.Error,match='permission denied'):
        query(uri,'select * from private.observer_uploads',role='authenticated',user=s['user'])


def test_pending_uploads_have_a_team_limit_and_expire(setup):
    s=setup;uri=s['uri'];ids=[uuid.uuid4() for _ in range(5)]
    for id in ids:rpc(uri,'observer_reserve_upload',s['user'],id,'source')
    with pytest.raises(psycopg.Error,match='upload_limit'):
        rpc(uri,'observer_reserve_upload',s['user'],uuid.uuid4(),'source')
    query(uri,"update private.observer_uploads set expires_at=now()-interval '1 second' where id=%s",(ids[0],))
    assert rpc(uri,'observer_upload_access',s['user'],ids[0],'source') is None
    rpc(uri,'observer_reserve_upload',s['user'],uuid.uuid4(),'source')


def test_failed_preparations_still_count_toward_daily_compute_limit(setup):
    s=setup;uri=s['uri']
    for _ in range(10):
        revision=rpc(uri,'observer_create_project','Attempt','repository','https://github.com/owner/repo',role='authenticated',user=s['user'])
        query(uri,"update public.observer_revisions set status='failed' where id=%s",(revision,))
    with pytest.raises(psycopg.Error,match='preparation_daily_limit'):
        rpc(uri,'observer_create_project','Again','repository','https://github.com/owner/repo',role='authenticated',user=s['user'])


def test_design_evidence_stays_team_private_and_does_not_affect_scores(setup):
    s=setup;uri=s['uri'];other,_=identity(uri)
    revision=rpc(uri,'observer_create_project','Evidence','repository','https://github.com/owner/repo',role='authenticated',user=s['user'])
    rpc(uri,'observer_save_evidence',revision,'Reproduce with the submitted manifest.','https://github.com/owner/repo',role='authenticated',user=s['user'])
    assert query(uri,'select notes from public.observer_evidence where revision_id=%s',(revision,),role='authenticated',user=s['user'])
    assert query(uri,'select notes from public.observer_evidence where revision_id=%s',(revision,),role='authenticated',user=other)==[]
    with pytest.raises(psycopg.Error,match='revision_not_found'):
        rpc(uri,'observer_save_evidence',revision,'Overwrite','',role='authenticated',user=other)
    assert query(uri,'select count(*) from public.observer_batches where team_id=%s',(s['team'],))==[(0,)]
    query(uri,'update public.profiles set is_banned=true where id=%s',(s['user'],))
    with pytest.raises(psycopg.Error):
        rpc(uri,'observer_save_evidence',revision,'Banned write','',role='authenticated',user=s['user'])


def test_cleanup_preserves_unarchived_submissions_and_only_marks_expired_staging(setup):
    s=setup;uri=s['uri'];pending,source=uuid.uuid4(),uuid.uuid4()
    rpc(uri,'observer_reserve_upload',s['user'],pending,'source')
    rpc(uri,'observer_reserve_upload',s['user'],source,'source')
    revision=rpc(uri,'observer_submit_zip',source,'Keep original',role='authenticated',user=s['user'])
    assert not any(x['id'] in (str(pending),str(source)) for x in rpc(uri,'observer_expired_uploads'))
    query(uri,"update private.observer_uploads set expires_at=now()-interval '1 second' where id in (%s,%s)",(pending,source))
    def ids():return {x['id'] for x in rpc(uri,'observer_expired_uploads')}
    assert str(pending) in ids() and str(source) not in ids()
    rpc(uri,'observer_upload_cleaned',pending)
    assert str(pending) not in ids()
    assert query(uri,'select count(*) from private.observer_uploads where id=%s',(pending,))==[(1,)]
    # Materialized is necessary but not sufficient: still 'queued'/'preparing', and inside
    # the 7-day grace period, so the source stays put either way.
    query(uri,'insert into private.observer_materializations(revision_id,archive_ref,digest) values(%s,%s,%s)',
      (revision,'github:AGENTIC-OBSERVER26-runner-1/participant-'+s['user'].hex+'@'+'a'*40,'b'*64))
    assert str(source) not in ids()
    query(uri,"update private.observer_materializations set created_at=now()-interval '8 days' where revision_id=%s",(revision,))
    assert str(source) not in ids()
    query(uri,"update public.observer_revisions set status='failed' where id=%s",(revision,))
    assert str(source) in ids()
    with pytest.raises(psycopg.Error,match='permission denied'):
        rpc(uri,'observer_expired_uploads',role='authenticated',user=s['user'])


def test_accepted_csv_releases_pending_upload_slot_atomically(setup):
    from test_project_database import session
    s=setup;uri=s['uri'];run,_,_=session(s);upload=uuid.uuid4()
    rpc(uri,'observer_reserve_upload',s['user'],upload,'csv')
    query(uri,"update public.observer_runs set status='awaiting_csv',score=1,decisions_digest=%s,result_path=%s,finished_at=now() where id=%s",
      ('a'*64,'github:private-result',run))
    with pytest.raises(psycopg.Error,match='csv_does_not_match_session'):
        rpc(uri,'observer_accept_uploaded_csv',run,s['user'],upload,'b'*64)
    assert query(uri,'select consumed_at from private.observer_uploads where id=%s',(upload,))==[(None,)]
    rpc(uri,'observer_accept_uploaded_csv',run,s['user'],upload,'a'*64)
    rpc(uri,'observer_accept_uploaded_csv',run,s['user'],upload,'a'*64)
    assert query(uri,'select consumed_at is not null,run_id from private.observer_uploads where id=%s',(upload,))==[(True,run)]
    for _ in range(5):rpc(uri,'observer_reserve_upload',s['user'],uuid.uuid4(),'csv')
