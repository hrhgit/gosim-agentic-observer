import uuid
import psycopg
import pytest
from test_project_database import database,identity,query,rpc,setup  # noqa: F401


def test_private_rollout_is_visible_only_to_its_team_and_cannot_change_old_phase_access(setup):
    s=setup;uri=s['uri'];other,_=identity(uri)
    query(uri,'update public.observer_phase_settings set access_team_id=%s where phase_id=%s',(s['team'],s['phase']))
    old=uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,'Legacy','原有')",(old,str(old)))
    assert query(uri,'select id from public.phases where id=%s',(old,),role='anon')==[(old,)]
    for role,user in [('anon',None),('authenticated',other)]:
        assert query(uri,'select id from public.phases where id=%s',(s['phase'],),role=role,user=user)==[]
        assert query(uri,'select phase_id from public.observer_phase_settings where phase_id=%s',(s['phase'],),role=role,user=user)==[]
        assert query(uri,'select phase_id from public.phase_scenarios where phase_id=%s',(s['phase'],),role=role,user=user)==[]
    assert query(uri,'select id from public.phases where id=%s',(s['phase'],),role='authenticated',user=s['user'])==[(s['phase'],)]
    with pytest.raises(psycopg.Error,match='projects_not_enabled'):
        rpc(uri,'observer_create_project','Not admitted','repository','https://github.com/example/agent',role='authenticated',user=other)
    with pytest.raises(psycopg.Error,match='phase_closed'):
        rpc(uri,'observer_create_batch',s['phase'],None,role='authenticated',user=other)
    assert rpc(uri,'observer_create_project','Admitted','repository','https://github.com/example/agent',role='authenticated',user=s['user'])
    assert rpc(uri,'observer_create_batch',s['phase'],None,role='authenticated',user=s['user'])


def test_weather_timer_preserves_hidden_online_scenarios_and_legacy_publication(setup):
    s=setup;uri=s['uri'];old_phase=uuid.uuid4();old_scenario=uuid.uuid4()
    query(uri,"update public.phases set starts_at=now()-interval '1 minute',is_active=true where id=%s",(s['phase'],))
    query(uri,"update public.scenarios set weather_public=false,forecasts_public=false,events_public=false where id=%s",(s['scenario'],))
    query(uri,"insert into public.phases(id,slug,name_en,name_zh,starts_at,is_active) values(%s,%s,'Legacy','旧赛程',now()-interval '1 minute',true)",(old_phase,str(old_phase)))
    query(uri,"insert into public.scenarios(id,slug,name,weather_public,forecasts_public,events_public) values(%s,%s,'Legacy',false,false,false)",(old_scenario,str(old_scenario)))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(old_phase,old_scenario))
    query(uri,'select public.publish_open_phase_weather()')
    assert query(uri,'select weather_public,forecasts_public,events_public from public.scenarios where id=%s',(old_scenario,))==[(True,True,True)]
    assert query(uri,'select weather_public,forecasts_public,events_public from public.scenarios where id=%s',(s['scenario'],))==[(False,False,False)]
    # Even accidental reuse in a legacy phase must not publish the online answer.
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(old_phase,s['scenario']))
    query(uri,'select public.publish_open_phase_weather()')
    assert query(uri,'select weather_public from public.scenarios where id=%s',(s['scenario'],))==[(False,)]


def test_dispatch_configuration_is_private_and_defaults_off(setup):
    uri=setup['uri']
    assert query(uri,'select private.observer_tick()')==[(None,)]
    query(uri,"insert into private.observer_dispatch_config(endpoint,secret_id) values('https://test.supabase.co/functions/v1/observer-dispatch',%s)",(uuid.uuid4(),))
    assert query(uri,'select private.observer_tick()')==[(None,)]
    for role in ('anon','authenticated'):
        for statement in ('select * from private.observer_dispatch_config','select private.observer_tick()'):
            with pytest.raises(psycopg.Error,match='permission denied'):
                query(uri,statement,role=role,user=setup['user'])


def test_online_phase_cannot_bypass_session_with_legacy_csv_but_old_submissions_still_work(setup):
    s=setup;uri=s['uri'];legacy=uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,'Practice','练习赛')",(legacy,str(legacy)))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(legacy,s['scenario']))
    args=('results',str(s['scenario']),str(s['team'])+'/decisions.csv')
    historic=rpc(uri,'create_submission',str(legacy),*args,role='authenticated',user=s['user'])
    for role in ('authenticated','service_role'):
        with pytest.raises(psycopg.Error,match='online_session_required'):
            rpc(uri,'create_submission',str(s['phase']),*args,role=role,user=s['user'])
    with pytest.raises(psycopg.Error,match='online_session_required'):
        query(uri,'update public.submissions set phase_id=%s where id=%s',(s['phase'],historic))
    # Enabling the new flow must not prevent a worker from completing an old
    # submission, or rewrite its stored score/history.
    query(uri,'insert into public.observer_phase_settings(phase_id,local_sessions_enabled) values(%s,true)',(legacy,))
    query(uri,"update public.submissions set status='scored',score=21085.3 where id=%s",(historic,))
    assert query(uri,'select score,phase_id from public.submissions where id=%s',(historic,))==[(21085.3,legacy)]
    # Disabling both new submission modes restores the legacy route for that phase.
    query(uri,'update public.observer_phase_settings set local_sessions_enabled=false where phase_id=%s',(legacy,))
    assert rpc(uri,'create_submission',str(legacy),*args,role='authenticated',user=s['user'])
