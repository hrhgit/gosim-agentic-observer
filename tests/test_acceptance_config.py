"""Guards of the acceptance-phase configuration script, against real SQL semantics."""
import importlib.util
import uuid
import psycopg

from test_project_database import database, query  # noqa: F401

spec=importlib.util.spec_from_file_location('configure_observer_acceptance','scripts/configure-observer-acceptance.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Args:
    daily_batches=10
    runtime_seconds=300
    sort_order=10100


def install(uri,storage_present=True):
    def local_query(sql):
        with psycopg.connect(uri) as conn:
            cur=conn.execute(sql)
            return cur.fetchall() if cur.description else []
    mod.query=local_query
    def one(sql):
        if 'storage.objects' in sql:
            return 1 if storage_present else 0
        rows=local_query(sql)
        assert len(rows)==1 and len(rows[0])==1,sql
        return rows[0][0]
    mod.one=one


def make_hidden_team(uri,name):
    from test_project_database import identity
    leader,team=identity(uri)
    query(uri,'update public.teams set name=%s,slug=%s,is_hidden=true where id=%s',(name,'slug-'+name,team))
    return team


def test_acceptance_plan_is_idempotent_and_projects_only(database):
    uri=database;install(uri)
    team=make_hidden_team(uri,'Acceptance Alpha')
    synthetic=[]
    for index in range(2):
        scenario=uuid.uuid4()
        query(uri,"insert into public.scenarios(id,slug,name,is_active) values(%s,%s,'Synthetic',true)",(scenario,f'synthetic-{index}'))
        synthetic.append({'scenario_id':str(scenario),'slug':f'synthetic-{index}','bundle_path':'bundle.zip','bundle_digest':'a'*64})
    scenarios=synthetic
    plan=mod.plan_phase(mod.check_team(str(team)),scenarios,Args)
    assert plan['is_new_phase']
    assert plan['projects_only']['projects_enabled'] is True
    assert plan['runtime_seconds']==300 and plan['daily_batches']==10
    assert plan['statements'][1].lower().count("'false'")==1 and plan['statements'][1].lower().count("'true'")
    # Applying the plan twice must be idempotent: the second pass reuses the phase.
    for statement in plan['statements']:query(uri,statement)
    replan=mod.plan_phase(mod.check_team(str(team)),scenarios,Args)
    assert not replan['is_new_phase'] and replan['phase_id']==plan['phase_id']
    row=query(uri,'select projects_enabled,local_sessions_enabled,access_team_id from public.observer_phase_settings where phase_id=%s',(plan['phase_id'],))[0]
    assert row==(True,False,team)
    # A second team plans its own independent phase, never shares the first.
    other=make_hidden_team(uri,'Acceptance Beta')
    plan_b=mod.plan_phase(mod.check_team(str(other)),scenarios,Args)
    assert plan_b['phase_id']!=plan['phase_id'] and plan_b['slug'].endswith(str(other)[:8])
    # An unmarked (visible) team is refused outright.
    query(uri,'update public.teams set is_hidden=false where id=%s',(team,))
    try:
        mod.check_team(str(team));raise SystemError('unmarked team accepted')
    except mod.CheckError:pass
    # A phase already restricted to another team is never re-pointed.
    query(uri,'update public.observer_phase_settings set access_team_id=%s where phase_id=%s',(other,plan['phase_id']))
    try:
        mod.plan_phase(mod.check_team(str(team)),scenarios,Args);raise SystemError('foreign phase accepted')
    except mod.CheckError:pass


def test_acceptance_scenario_guard_refuses_formal_and_lab_material(database):
    uri=database;install(uri,storage_present=True)
    formal_phase,formal_scenario=uuid.uuid4(),uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh,counts_for_final) values(%s,'online','Online','正式',true)",(formal_phase,))
    query(uri,"insert into public.scenarios(id,slug,name,is_active) values(%s,'formal-secret','Formal',true)",(formal_scenario,))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(formal_phase,formal_scenario))
    try:
        mod.check_scenario(str(formal_scenario),'x');raise SystemError('formal scenario accepted')
    except mod.CheckError:pass
    # Slug-protected phases (the hidden lab) refuse their scenarios too.
    lab_phase,lab_scenario=uuid.uuid4(),uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh) values(%s,'observer-platform-e2e','Lab','实验室')",(lab_phase,))
    query(uri,"insert into public.scenarios(id,slug,name,is_active) values(%s,'synthetic-b','Synthetic',true)",(lab_scenario,))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(lab_phase,lab_scenario))
    try:
        mod.check_scenario(str(lab_scenario),'x');raise SystemError('lab scenario accepted')
    except mod.CheckError:pass
    # A clean private synthetic scenario plans only with a bundle present.
    private=uuid.uuid4()
    query(uri,"insert into public.scenarios(id,slug,name,is_active) values(%s,'synthetic-a','Synthetic',true)",(private,))
    try:
        mod.check_scenario(str(private),'x');raise SystemError('bundle-less scenario accepted')
    except mod.CheckError:pass
    query(uri,'insert into private.observer_scenario_bundles(scenario_id,storage_path,digest) values(%s,%s,%s)',
          (private,'observer-scenarios/bundle.zip','b'*64))
    checked=mod.check_scenario(str(private),'x')
    assert checked['slug']=='synthetic-a' and checked['bundle_digest']=='b'*64


def test_same_as_reuses_formal_scenarios_with_their_calibration(database):
    uri=database;install(uri,storage_present=True)
    team=make_hidden_team(uri,'Acceptance Seeds')
    online=uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh,counts_for_final) values(%s,'formal-copy','Formal','正式',true)",(online,))
    scenarios,profiles=[],{}
    for index in range(2):
        scenario=uuid.uuid4()
        query(uri,"insert into public.scenarios(id,slug,name,is_active) values(%s,%s,'Formal',true)",(scenario,f'eval-{index}'))
        query(uri,'insert into public.phase_scenarios values(%s,%s)',(online,scenario))
        query(uri,'insert into private.observer_scenario_bundles(scenario_id,storage_path,digest) values(%s,%s,%s)',
              (scenario,f'observer-scenarios/eval-{index}.zip',str(index)*64))
        profile=query(uri,"""insert into private.observer_calibration_profiles(scenario_id,bundle_digest,profile) values(%s,%s,
            jsonb_build_object('schema_version','observer-calibration-profile-v1','panel_version','observer-reference-panel-v1',
            'template_digest',repeat('c',64),'bounds','{}'::jsonb)) returning id""",(scenario,str(index)*64))[0][0]
        query(uri,'insert into private.observer_scenario_calibration values(%s,%s,%s)',(online,scenario,profile))
        profiles[str(scenario)]=str(profile)
    # Without --same-as, formal material is still refused.
    try:
        mod.check_scenario(next(iter(profiles)),'x');raise SystemError('formal scenario accepted')
    except mod.CheckError:pass
    # An internal team-restricted lab on the same scenarios does not block --same-as ...
    lab_team=make_hidden_team(uri,'Seeds Lab')
    lab=uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh,counts_for_final) values(%s,'seeds-lab','Lab','实验室',true)",(lab,))
    query(uri,'insert into public.observer_phase_settings(phase_id,projects_enabled,access_team_id) values(%s,true,%s)',(lab,lab_team))
    for s in profiles:query(uri,'insert into public.phase_scenarios values(%s,%s)',(lab,s))
    checked=[mod.check_scenario(s,'x','formal-copy') for s in sorted(profiles)]
    # ... but a second participant-visible formal phase still does.
    public=uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh,counts_for_final) values(%s,'other-formal','Other','其他',true)",(public,))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(public,next(iter(profiles))))
    try:
        mod.check_scenario(next(iter(profiles)),'x','formal-copy');raise SystemError('public formal phase ignored')
    except mod.CheckError:pass
    query(uri,'delete from public.phase_scenarios where phase_id=%s',(public,))
    copied=mod.source_calibration('formal-copy',checked)
    assert copied==profiles
    plan=mod.plan_phase(mod.check_team(str(team)),checked,Args,copied)
    for statement in plan['statements']:query(uri,statement)
    rows=query(uri,'select scenario_id::text,profile_id::text from private.observer_scenario_calibration where phase_id=%s',(plan['phase_id'],))
    assert dict(rows)==profiles
    assert all(c['calibrated'] and c['profile_matches_bundle'] for c in plan['calibration'])
    limits=query(uri,'select model_call_limit,model_token_limit,model_concurrency from public.observer_phase_settings where phase_id=%s',(plan['phase_id'],))[0]
    assert limits==(100000,1000000000,4)


def test_management_api_rows_are_positional(monkeypatch):
    monkeypatch.setenv('SUPABASE_PROJECT_REF','ref');monkeypatch.setenv('SUPABASE_ACCESS_TOKEN','token')
    real=importlib.util.module_from_spec(spec);spec.loader.exec_module(real)
    real.request=lambda *args,**kwargs:[{'id':'a','is_hidden':True}]
    assert real.query('select 1')==[('a',True)]
