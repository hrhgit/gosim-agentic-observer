"""Only organizers switch the current experience; legacy data stays intact."""
import uuid
import psycopg
import pytest
from test_project_database import database, identity, query, rpc  # noqa: F401


def test_mode_switch_is_admin_only_and_requires_a_ready_competition(database):
    uri=database; admin,team=identity(uri); participant,_=identity(uri)
    practice,competition,scenario=uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
    for phase,slug in ((practice,'practice'),(competition,'online')):
        query(uri,"insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,'Test','测试')",(phase,slug))
    query(uri,'update public.profiles set is_admin=true where id=%s',(admin,))
    before=query(uri,'select row_to_json(p)::text from public.phases p order by id')
    assert rpc(uri,'current_competition',role='anon')=={'mode':'practice','phase_id':str(practice)}
    with pytest.raises(psycopg.Error,match='permission denied'):
        rpc(uri,'set_competition_mode','competition',role='anon')
    with pytest.raises(psycopg.Error,match='admin_required'):
        rpc(uri,'set_competition_mode','competition',role='authenticated',user=participant)
    with pytest.raises(psycopg.Error,match='competition_not_ready'):
        rpc(uri,'set_competition_mode','competition',role='authenticated',user=admin)
    assert rpc(uri,'current_competition',role='anon')['mode']=='practice'
    query(uri,"insert into public.scenarios(id,slug,name) values(%s,%s,'Test')",(scenario,str(scenario)))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(competition,scenario))
    query(uri,'insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled) values(%s,true,true)',(competition,))
    # Fixed formal scenarios need an evaluation bundle, not per-team calibration.
    with pytest.raises(psycopg.Error,match='competition_not_ready'):
        rpc(uri,'set_competition_mode','competition',role='authenticated',user=admin)
    query(uri,"insert into private.observer_scenario_bundles values(%s,'test/template.zip',%s)",(scenario,'a'*64))
    assert query(uri,'select count(*) from private.observer_scenario_calibration')==[(0,)]
    result=rpc(uri,'set_competition_mode','competition',role='authenticated',user=admin)
    assert result=={'mode':'competition','phase_id':str(competition)}
    assert rpc(uri,'current_competition',role='anon')==result
    rpc(uri,'set_competition_mode','practice',role='authenticated',user=admin)
    assert rpc(uri,'current_competition',role='anon')['mode']=='practice'
    assert query(uri,'select row_to_json(p)::text from public.phases p order by id')==before


def test_formal_competition_rejects_csv_and_local_batches_even_if_config_is_stale(database):
    uri=database;user,team=identity(uri)
    phase,scenario=uuid.uuid4(),uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh,counts_for_final) values(%s,%s,'Formal','正式比赛',true)",(phase,str(phase)))
    query(uri,"insert into public.scenarios(id,slug,name) values(%s,%s,'Test')",(scenario,str(scenario)))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(phase,scenario))
    query(uri,'insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled) values(%s,true,true)',(phase,))
    with pytest.raises(psycopg.Error,match='competition_project_required'):
        rpc(uri,'observer_create_batch',phase,None,role='authenticated',user=user)
    with pytest.raises(psycopg.Error,match='competition_project_required'):
        rpc(uri,'create_submission',str(phase),'results',str(scenario),str(team)+'/result.csv',role='authenticated',user=user)
    assert query(uri,'select count(*) from public.observer_batches where phase_id=%s',(phase,))==[(0,)]
    assert query(uri,'select count(*) from public.submissions where phase_id=%s',(phase,))==[(0,)]


def test_private_beta_entry_reachable_only_for_its_access_team(database):
    uri=database;member,team=identity(uri);other,_=identity(uri)
    beta,scenario=uuid.uuid4(),uuid.uuid4()
    practice=query(uri,"select id from public.phases where slug='practice'")[0][0]
    query(uri,"insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,'Formal','正式比赛')",(beta,'formal-beta'))
    query(uri,"insert into public.scenarios(id,slug,name) values(%s,%s,'Beta')",(scenario,'beta'))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(beta,scenario))
    query(uri,'insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled) values(%s,true,true)',(beta,))
    assert rpc(uri,'my_observer_phase',role='authenticated',user=member) is None
    query(uri,'update public.observer_phase_settings set access_team_id=%s where phase_id=%s',(team,beta))
    assert rpc(uri,'my_observer_phase',role='authenticated',user=member)==beta
    assert rpc(uri,'my_observer_phase',role='authenticated',user=other) is None
    with pytest.raises(psycopg.Error,match='permission denied'):
        rpc(uri,'my_observer_phase',role='anon')
    # Organizers outside the access team keep the public entry (they test as that team).
    admin,_=identity(uri)
    query(uri,'update public.profiles set is_admin=true where id=%s',(admin,))
    assert rpc(uri,'my_observer_phase',role='authenticated',user=admin) is None
    query(uri,'update public.profiles set is_banned=true where team_id=%s',(team,))
    assert rpc(uri,'my_observer_phase',role='authenticated',user=member) is None
    query(uri,'update public.profiles set is_banned=false where team_id=%s',(team,))
    # A phase without a live workflow or past its end stays hidden without
    # touching the site-wide switch.
    query(uri,'update public.observer_phase_settings set projects_enabled=false,local_sessions_enabled=false where phase_id=%s',(beta,))
    assert rpc(uri,'my_observer_phase',role='authenticated',user=member) is None
    query(uri,'update public.observer_phase_settings set local_sessions_enabled=true where phase_id=%s',(beta,))
    assert rpc(uri,'my_observer_phase',role='authenticated',user=member)==beta
    query(uri,"update public.phases set starts_at=now()+interval '1 hour' where id=%s",(beta,))
    assert rpc(uri,'my_observer_phase',role='authenticated',user=member) is None
    query(uri,'update public.phases set starts_at=now()-interval \'1 minute\' where id=%s',(beta,))
    assert rpc(uri,'my_observer_phase',role='authenticated',user=member)==beta
    query(uri,"update public.phases set ends_at=now()-interval '1 minute' where id=%s",(beta,))
    assert rpc(uri,'my_observer_phase',role='authenticated',user=member) is None
    assert rpc(uri,'current_competition',role='anon')=={'mode':'practice','phase_id':str(practice)}


def test_beta_entry_isolates_parallel_teams_and_follows_team_changes(database):
    uri=database;member_a,team_a=identity(uri);member_b,team_b=identity(uri)
    phase_a,phase_b=uuid.uuid4(),uuid.uuid4()
    # Same sort_order on purpose: the tiebreak must be deterministic (phase id).
    for phase,slug,team in ((phase_a,'acceptance-a',team_a),(phase_b,'acceptance-b',team_b)):
        query(uri,"insert into public.phases(id,slug,name_en,name_zh,sort_order) values(%s,%s,'Acceptance','验收',10100)",(phase,slug))
        query(uri,'insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,access_team_id) values(%s,true,false,%s)',(phase,team))
    assert rpc(uri,'my_observer_phase',role='authenticated',user=member_a)==phase_a
    assert rpc(uri,'my_observer_phase',role='authenticated',user=member_b)==phase_b
    admin,_=identity(uri)
    query(uri,'update public.profiles set is_admin=true where id=%s',(admin,))
    assert rpc(uri,'my_observer_phase',role='authenticated',user=admin) is None
    # Moving a member between acceptance teams switches their entry; leaving
    # every acceptance team removes it.
    query(uri,'update public.profiles set team_id=%s where id=%s',(team_b,member_a))
    assert rpc(uri,'my_observer_phase',role='authenticated',user=member_a)==phase_b
    query(uri,'update public.profiles set team_id=null where id=%s',(member_a,))
    assert rpc(uri,'my_observer_phase',role='authenticated',user=member_a) is None


def test_playground_project_board_is_offered_only_in_practice_mode(database):
    uri=database
    assert rpc(uri,'current_competition',role='anon').get('project_phase_id') is None
    board,scenario=uuid.uuid4(),uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh) values(%s,'practice-projects','Practice projects','练习赛·完整项目')",(board,))
    # Without settings the phase has no workflow and is not offered.
    assert rpc(uri,'current_competition',role='anon').get('project_phase_id') is None
    query(uri,'insert into public.observer_phase_settings(phase_id,projects_enabled,daily_batches) values(%s,true,5)',(board,))
    assert rpc(uri,'current_competition',role='anon')['project_phase_id']==str(board)
    # A restricted (acceptance-style) board is never offered to everyone.
    team=identity(uri)[1]
    query(uri,'update public.observer_phase_settings set access_team_id=%s where phase_id=%s',(team,board))
    assert rpc(uri,'current_competition',role='anon').get('project_phase_id') is None
    query(uri,'update public.observer_phase_settings set access_team_id=null where phase_id=%s',(board,))
    query(uri,"update public.phases set is_active=false where id=%s",(board,))
    assert rpc(uri,'current_competition',role='anon').get('project_phase_id') is None
    query(uri,"update public.phases set is_active=true where id=%s",(board,))
    # In competition mode only the formal phase counts.
    query(uri,"update private.observer_site_mode set mode='competition',phase_id=null")
    try:
        answer=rpc(uri,'current_competition',role='anon')
        assert answer.get('project_phase_id') is None
        # Practice stays usable next to the competition, under its own key.
        assert answer['practice_phase_id']==str(board)
        # Once the online phase has ended, practice becomes the default entry for every client.
        online=query(uri,"select id from public.phases where slug='online'")
        created=not online
        if created:
            query(uri,"insert into public.phases(slug,name_en,name_zh,counts_for_final) values('online','Online','线上赛',true)")
            online=query(uri,"select id from public.phases where slug='online'")
        online=online[0][0]
        ends=query(uri,'select ends_at from public.phases where id=%s',(online,))[0][0]
        query(uri,"update public.phases set ends_at=now()+interval '1 hour' where id=%s",(online,))
        assert 'project_phase_id' not in rpc(uri,'current_competition',role='anon')
        query(uri,"update public.phases set ends_at=now()-interval '1 minute' where id=%s",(online,))
        ended=rpc(uri,'current_competition',role='anon')
        assert ended['phase_id']==str(online) and ended['practice_phase_id']==str(board) and ended['project_phase_id']==str(board)
        query(uri,'update public.phases set ends_at=%s where id=%s',(ends,online))
        if created: query(uri,'delete from public.phases where id=%s',(online,))
        query(uri,"update public.phases set ends_at=now()-interval '1 minute' where id=%s",(board,))
        assert 'practice_phase_id' not in rpc(uri,'current_competition',role='anon')
        query(uri,"update public.phases set ends_at=null where id=%s",(board,))
    finally:
        query(uri,"update private.observer_site_mode set mode='practice',phase_id=null")
    query(uri,'delete from public.observer_phase_settings where phase_id=%s',(board,))
    query(uri,'delete from public.phases where id=%s',(board,))


def test_formal_scenarios_stay_unnamed_until_the_competition_opens(database):
    uri=database
    formal,shared,practice_s=uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
    online=query(uri,"select id from public.phases where slug='online'")
    online=online[0][0] if online else None
    if online is None:
        online=uuid.uuid4()
        query(uri,"insert into public.phases(id,slug,name_en,name_zh,counts_for_final) values(%s,'online','Online','正式',true)",(online,))
    query(uri,"update public.phases set starts_at=now()+interval '3 days' where id=%s",(online,))
    practice=query(uri,"select id from public.phases where slug='practice'")[0][0]
    for sid,slug in ((formal,'hidden-eval'),(shared,'shared-eval'),(practice_s,'plain-practice')):
        query(uri,"insert into public.scenarios(id,slug,name,is_active) values(%s,%s,'S',true)",(sid,slug))
    query(uri,'insert into public.phase_scenarios values(%s,%s),(%s,%s)',(online,formal,online,shared))
    query(uri,'insert into public.phase_scenarios values(%s,%s),(%s,%s)',(practice,shared,practice,practice_s))
    def visible(role='anon',user=None):
        with psycopg.connect(uri) as conn:
            conn.execute("select set_config('role',%s,true)",(role,))
            if user:conn.execute("select set_config('request.jwt.claims',%s,true)",('{"sub":"%s","role":"authenticated"}'%user,))
            return {r[0] for r in conn.execute('select slug from public.scenarios').fetchall()}
    names=visible()
    assert 'hidden-eval' not in names and 'shared-eval' in names and 'plain-practice' in names
    user,_=identity(uri)
    assert 'hidden-eval' not in visible('authenticated',user)
    query(uri,'update public.profiles set is_admin=true where id=%s',(user,))
    assert 'hidden-eval' in visible('authenticated',user)
    query(uri,"update public.phases set starts_at=now()-interval '1 minute' where id=%s",(online,))
    assert 'hidden-eval' in visible()


def test_practice_board_keeps_taking_evaluations_during_the_competition(database):
    uri=database;user,team=identity(uri)
    board,scenario=uuid.uuid4(),uuid.uuid4()
    query(uri,"insert into public.phases(id,slug,name_en,name_zh) values(%s,'practice-projects','Practice projects','练习赛·完整项目')",(board,))
    query(uri,'insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,daily_batches,max_active_evaluations) values(%s,true,true,5,10)',(board,))
    query(uri,"insert into public.scenarios(id,slug,name) values(%s,%s,'Practice')",(scenario,str(scenario)))
    query(uri,'insert into public.phase_scenarios values(%s,%s)',(board,scenario))
    query(uri,"update private.observer_site_mode set mode='competition',phase_id=null")
    try:
        assert rpc(uri,'current_competition',role='anon')['practice_phase_id']==str(board)
        batch=rpc(uri,'observer_create_batch',board,None,role='authenticated',user=user)
        assert query(uri,'select phase_id from public.observer_batches where id=%s',(batch,))==[(board,)]
        quota={r['phase_id']:r for r in rpc(uri,'observer_evaluation_quota',role='authenticated',user=user)}
        assert quota[str(board)]['remaining']==4
    finally:
        query(uri,"update private.observer_site_mode set mode='practice',phase_id=null")
        query(uri,'delete from public.observer_runs where batch_id in (select id from public.observer_batches where phase_id=%s)',(board,))
        query(uri,'delete from public.observer_batches where phase_id=%s',(board,))
        query(uri,'delete from public.phase_scenarios where phase_id=%s',(board,))
        query(uri,'delete from public.observer_phase_settings where phase_id=%s',(board,))
        query(uri,'delete from public.phases where id=%s',(board,))
