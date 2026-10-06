"""Competition activation is additive, repeatable and preserves legacy results."""
import importlib.util
import json
from pathlib import Path
import uuid

import psycopg
import pytest
from test_project_database import database,setup,query,rpc  # noqa: F401

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('competition',ROOT/'scripts/configure-observer-competition.py')
competition=importlib.util.module_from_spec(spec);spec.loader.exec_module(competition)


def test_personal_provider_configuration_keeps_https_boundaries():
    spec=importlib.util.spec_from_file_location('provider_config',ROOT/'scripts/configure-observer-secrets.py')
    config=importlib.util.module_from_spec(spec);spec.loader.exec_module(config)
    assert config.approved_bases(['https://personal.example/v1/','https://personal.example/v1']).count('https://personal.example/v1')==1
    for url in ('http://private.example/v1','https://name:key@example.test/v1','https://example.test?key=secret',
                'https://example.test/#key','https://one.test,https://two.test','https://bad host/v1'):
        with pytest.raises(ValueError):config.approved_bases([url])


def test_activation_preserves_old_rows_and_refuses_existing_competition_results(setup):
    s=setup;uri=s['uri'];phase=uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh,counts_for_final,daily_limit) values(%s,'online','Formal','正式赛',true,10)",(phase,))
    formal=[uuid.uuid4(),uuid.uuid4(),uuid.uuid4()]
    for scenario in formal:
        query(uri,"insert into public.scenarios(id,slug,name,weather_public,forecasts_public,events_public) values(%s,%s,'Fixed',false,false,false)",(scenario,str(scenario)))
        query(uri,'insert into public.phase_scenarios values(%s,%s)',(phase,scenario))
        query(uri,'insert into private.observer_scenario_bundles values(%s,%s,%s)',(scenario,str(scenario)+'/bundle.zip','a'*64))
    query(uri,'update public.scenarios set weather_public=true,forecasts_public=true,events_public=true where id=%s',(s['scenario'],))
    query(uri,'insert into private.observer_scenario_bundles values(%s,%s,%s)',(s['scenario'],str(s['scenario'])+'/bundle.zip','a'*64))
    # Activation requires every enabled runner in ops/github-installations.json.
    runners=json.loads((ROOT/'ops/github-installations.json').read_text())['installations']
    for i,row in enumerate(runners,1):
        query(uri,"insert into private.observer_installations(organization,organization_id,installation_id,repository_id,approved_sha,enabled) values(%s,%s,%s,%s,%s,true)",
          (row['organization'],str(i),i,str(i+100),'a'*40))
    query(uri,"insert into private.observer_dispatch_config(endpoint,secret_id,enabled) values('https://example.supabase.co/functions/v1/observer-dispatch',%s,true)",(uuid.uuid4(),))
    before=query(uri,"select row_to_json(p)::text from public.phases p order by id")
    query(uri,'update private.observer_providers set enabled=false')
    providers=query(uri,'select id,daily_token_limit from private.observer_providers order by id')
    statement=competition.activation_sql(str(phase),str(s['scenario']),3600,10,'test-model')
    query(uri,statement);query(uri,statement)
    assert query(uri,"select row_to_json(p)::text from public.phases p order by id")==before
    assert query(uri,'select id,daily_token_limit from private.observer_providers order by id')==providers
    assert query(uri,'select projects_enabled,local_sessions_enabled,runtime_seconds,daily_batches from public.observer_phase_settings where phase_id=%s',(phase,))==[(True,False,3600,10)]
    # Formal runs spend the team's own key: no practical token cap, 100,000 calls, up to 4 at a time.
    assert query(uri,'select model_token_limit,model_call_limit,model_concurrency from public.observer_phase_settings where phase_id=%s',(phase,))==[(1000000000,100000,4)]
    assert query(uri,'select phase_id,scenario_id from private.observer_preparation_config')==[(phase,s['scenario'])]
    # A legacy result cannot be silently displaced if an operator uses this
    # script on a phase that already has old submissions.
    query(uri,'update public.observer_phase_settings set projects_enabled=false,local_sessions_enabled=false where phase_id=%s',(phase,))
    # Seed a historical CSV from before project-only admission; existing rows must survive.
    query(uri,"update public.phases set counts_for_final=false,slug=%s where id=%s",(str(phase),phase))
    old=rpc(uri,'create_submission',str(phase),'results',str(formal[0]),str(s['team'])+'/old.csv',role='authenticated',user=s['user'])
    query(uri,"update public.submissions set status='scored',score=21085.3 where id=%s",(old,))
    query(uri,"update public.phases set counts_for_final=true,slug='online' where id=%s",(phase,))
    with pytest.raises(psycopg.Error,match='Existing competition submissions'):
        query(uri,statement)
    assert query(uri,'select score from public.submissions where id=%s',(old,))==[(21085.3,)]
    assert query(uri,'select projects_enabled,local_sessions_enabled from public.observer_phase_settings where phase_id=%s',(phase,))==[(False,False)]
    query(uri,'delete from public.submissions where id=%s',(old,))
    query(uri,'update public.observer_phase_settings set projects_enabled=true where phase_id=%s',(phase,))
    # Fixed private formal scenarios only: a published one, a missing one, calibration or a hidden final scenario are refused.
    query(uri,'update public.scenarios set events_public=true where id=%s',(formal[0],))
    with pytest.raises(psycopg.Error,match='Hidden competition bundles are not ready'):
        query(uri,statement)
    query(uri,'update public.scenarios set events_public=false where id=%s',(formal[0],))
    with pytest.raises(psycopg.Error,match='Hidden competition bundles are not ready'):
        query(uri,competition.activation_sql(str(phase),str(s['scenario']),3600,10,'test-model',2))
    hidden=uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh,counts_for_final) values(%s,%s,'Final','决赛',true)",(hidden,str(hidden)))
    query(uri,'insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,sealed) values(%s,true,false,true)',(hidden,))
    with pytest.raises(psycopg.Error,match='sealed_scenario_shared'):
        query(uri,'insert into public.phase_scenarios values(%s,%s)',(hidden,formal[0]))
    query(uri,statement)

