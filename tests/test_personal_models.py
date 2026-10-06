"""Team model keys for formal runs in real PostgreSQL.

Stored mode (default): ciphertext only, team-scoped, formal-only, bounded per run
and purged manually or automatically once no phase can use it. Relay mode (explicit
choice): the page relay stores no key. Neither mode ever falls back to organizer
credits, and choosing the relay deletes a saved key at once.
"""
import concurrent.futures
import uuid
from pathlib import Path
import sys

import psycopg
import pytest
from test_project_database import database,setup,session,identity,query,rpc  # noqa: F401

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'/'supabase'))
from pg import start  # noqa: E402

# Stands in for the Edge AES-GCM output; the database never receives a plaintext key.
CIPHER='v1.'+'A'*16+'.'+'B'*40+'=='
BASE='https://api.example.test/v1'
CALL_FIELDS={'id','run_id','provider_id','usage_day','request_digest','reserved_tokens','actual_tokens',
             'status','created_at','settled_at'}


def save(uri,user,*,base=BASE,model='team-model',cipher=CIPHER,hint='wxyz'):
    provider=uuid.uuid4()
    rpc(uri,'observer_save_team_model',user,provider,base,model,cipher,hint)
    return provider


def team_model(uri,user):
    return rpc(uri,'observer_team_model',role='authenticated',user=user)


def saved(uri,user):
    return team_model(uri,user)['saved']


def choose(uri,user,mode):
    return rpc(uri,'observer_set_team_model_mode',mode,role='authenticated',user=user)


def formal_run(s):
    """Opens a run, then marks its phase formal like the competition phase."""
    run,participant,_=session(s)
    query(s['uri'],'update public.phases set counts_for_final=true where id=%s',(s['phase'],))
    return run,participant


def reserve(uri,run,token,call=None,*,tokens=100,digest='d'*64):
    return rpc(uri,'observer_reserve_team_model',run,token,call or uuid.uuid4(),digest,tokens)


def organizer_reservation(s,run,participant):
    return rpc(s['uri'],'observer_reserve_model',run,participant,uuid.uuid4(),s['provider'],'test-model','a'*64,1)


def test_team_members_save_replace_and_delete_without_reading_the_key_back(setup):
    s=setup;uri=s['uri']
    teammate,_=identity(uri,team=s['team'])
    outsider,_=identity(uri)
    # Saving on the server is the default for a team that has not chosen.
    assert team_model(uri,s['user'])=={'mode':'stored','protocol':'openai','saved':None}
    first=save(uri,s['user'])
    view=team_model(uri,teammate)
    assert view['mode']=='stored' and set(view['saved'])=={'base_url','model','key_hint','saved_at'}
    assert (view['saved']['base_url'],view['saved']['model'],view['saved']['key_hint'])==(BASE,'team-model','wxyz')
    assert saved(uri,outsider) is None
    listed=rpc(uri,'observer_list_providers',role='authenticated',user=s['user'])
    assert CIPHER not in str(listed) and CIPHER not in str(view)
    # Participants can neither store raw values directly nor read the private tables.
    with pytest.raises(psycopg.Error,match='permission denied'):
        rpc(uri,'observer_save_team_model',s['user'],uuid.uuid4(),BASE,'m',CIPHER,'',role='authenticated',user=s['user'])
    for table in ('observer_team_models','observer_team_model_modes','observer_providers'):
        with pytest.raises(psycopg.Error,match='permission denied'):
            query(uri,f'select * from private.{table}',role='authenticated',user=s['user'])
    for bad in ({'base':'http://api.example.test/v1'},{'base':'https://user:pw@api.example.test/v1'},
                {'cipher':'sk-plaintext-key-must-never-be-stored'},{'hint':'abcde'},{'model':'a\nb'},{'model':''}):
        with pytest.raises(psycopg.Error,match='invalid_team_model'):
            save(uri,s['user'],**bad)
    # Any member replaces the team's single key; an unused previous key is removed.
    second=save(uri,teammate,model='replacement-model',hint='')
    assert query(uri,'select count(*) from private.observer_providers where id=%s',(first,))==[(0,)]
    assert query(uri,'select provider_id from private.observer_team_models where team_id=%s',(s['team'],))==[(second,)]
    assert (saved(uri,s['user'])['model'],saved(uri,s['user'])['key_hint'])==('replacement-model','')
    # Another team cannot delete it; the owning team can, once.
    assert rpc(uri,'observer_delete_team_model',role='authenticated',user=outsider) is False
    assert saved(uri,s['user']) is not None
    assert rpc(uri,'observer_delete_team_model',role='authenticated',user=teammate) is True
    assert rpc(uri,'observer_delete_team_model',role='authenticated',user=s['user']) is False
    assert team_model(uri,s['user'])=={'mode':'stored','protocol':'openai','saved':None}
    assert query(uri,'select count(*) from private.observer_providers where team_id=%s',(s['team'],))==[(0,)]
    audit=query(uri,"""select action,detail::text from public.audit_log
        where action like 'observer.team_model%%' and detail->>'team_id'=%s order by id""",(str(s['team']),))
    assert [a for a,_ in audit]==['observer.team_model_saved']*2+['observer.team_model_deleted']
    assert all(CIPHER not in d for _,d in audit)
    # Banned or teamless users manage nothing.
    query(uri,'update public.profiles set is_banned=true where id=%s',(teammate,))
    with pytest.raises(psycopg.Error,match='team_required'):save(uri,teammate)
    for name,args in (('observer_delete_team_model',()),('observer_set_team_model_mode',('relay',))):
        with pytest.raises(psycopg.Error,match='banned'):
            rpc(uri,name,*args,role='authenticated',user=teammate)
    loner=uuid.uuid4();query(uri,'insert into auth.users(id,email) values(%s,%s)',(loner,f'{loner}@example.test'))
    with pytest.raises(psycopg.Error,match='team_required'):save(uri,loner)
    assert team_model(uri,loner) is None


def test_stored_mode_formal_runs_use_only_their_own_teams_saved_key(setup):
    s=setup;uri=s['uri'];run,participant=formal_run(s)
    # A team that has never chosen is already in stored mode by default.
    assert rpc(uri,'observer_model_route',run,participant)=={'personal':True,'mode':'stored','protocol':'openai'}
    assert choose(uri,s['user'],'stored')=='stored'
    assert rpc(uri,'observer_model_route',run,participant)=={'personal':True,'mode':'stored','protocol':'openai'}
    # No saved key: no organizer provider, legacy team row or other team is substituted.
    with pytest.raises(psycopg.Error,match='team_model_not_configured'):reserve(uri,run,participant)
    with pytest.raises(psycopg.Error,match='personal_api_required'):organizer_reservation(s,run,participant)
    other,_=identity(uri);other_provider=save(uri,other,hint='othr')
    query(uri,"""insert into private.observer_providers(id,team_id,name,base_url,encrypted_key,models,enabled,daily_token_limit)
      values(%s,%s,'Old API',%s,%s,array['old-model'],true,100000)""",(uuid.uuid4(),s['team'],BASE,CIPHER))
    with pytest.raises(psycopg.Error,match='team_model_not_configured'):reserve(uri,run,participant)
    provider=save(uri,s['user'])
    call=uuid.uuid4()
    assert reserve(uri,run,participant,call)=={'reserved':True,'provider_id':str(provider),'base_url':BASE,
                                              'model':'team-model','encrypted_key':CIPHER,'protocol':'openai'}
    # Retries never reach the provider twice; a changed request under the same ID is refused.
    assert reserve(uri,run,participant,call)=={'reserved':False,'status':'reserved'}
    with pytest.raises(psycopg.Error,match='request_id_conflict'):reserve(uri,run,participant,call,digest='e'*64)
    # A receipt cannot point at another team's key, even written directly.
    with pytest.raises(psycopg.Error,match='personal_api_required'):
        query(uri,"""insert into private.observer_model_calls(id,run_id,provider_id,usage_day,request_digest,reserved_tokens)
          values(%s,%s,%s,current_date,%s,1)""",(uuid.uuid4(),run,other_provider,'f'*64))
    with pytest.raises(psycopg.Error,match='permission denied'):
        rpc(uri,'observer_reserve_team_model',run,participant,uuid.uuid4(),'a'*64,1,role='authenticated',user=s['user'])
    # Stored mode never relays calls to a page.
    with pytest.raises(psycopg.Error,match='personal_model_not_enabled'):
        rpc(uri,'observer_request_personal_model',run,participant,uuid.uuid4(),'a'*64)
    assert rpc(uri,'observer_personal_model_routes',role='authenticated',user=s['user'])==[]
    rpc(uri,'observer_settle_model',call,40)
    receipts=query(uri,'select row_to_json(c) from private.observer_model_calls c where run_id=%s',(run,))
    assert [r[0]['provider_id'] for r in receipts]==[str(provider)]
    assert set(receipts[0][0])==CALL_FIELDS and CIPHER not in str(receipts)
    # Replacing a used key wipes the old ciphertext but keeps the receipt's audit row.
    replacement=save(uri,s['user'],model='second-model')
    assert query(uri,'select encrypted_key,enabled from private.observer_providers where id=%s',(provider,))==[('',False)]
    assert reserve(uri,run,participant)['provider_id']==str(replacement)


def test_model_concurrency_and_run_limits_bound_stored_formal_use(setup):
    s=setup;uri=s['uri'];run,participant=formal_run(s)
    save(uri,s['user'])
    # The fixture phase allows concurrency 4: exactly four calls may be outstanding at once.
    def attempt(_):
        try:return reserve(uri,run,participant)['reserved']
        except psycopg.Error as exc:
            assert 'run_model_quota' in str(exc)
            return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        assert sorted(pool.map(attempt,range(6)))==[False]*2+[True]*4
    assert query(uri,'select calls_active from private.observer_sessions where run_id=%s',(run,))==[(4,)]
    with pytest.raises(psycopg.Error,match='run_model_quota'):reserve(uri,run,participant)
    for (active,) in query(uri,"select id from private.observer_model_calls where run_id=%s and status='reserved'",(run,)):
        rpc(uri,'observer_settle_model',active,10)
    # The fixture run allows 1,000 tokens and 10 calls.
    with pytest.raises(psycopg.Error,match='run_model_quota'):reserve(uri,run,participant,tokens=1000)
    for _ in range(6):
        call=uuid.uuid4();reserve(uri,run,participant,call);rpc(uri,'observer_settle_model',call,10)
    with pytest.raises(psycopg.Error,match='run_model_quota'):reserve(uri,run,participant)
    assert query(uri,'select calls_used,calls_active,tokens_used from private.observer_sessions where run_id=%s',(run,))==[(10,0,100)]
    query(uri,"update private.observer_sessions set deadline_at=clock_timestamp()-interval '1 second' where run_id=%s",(run,))
    with pytest.raises(psycopg.Error,match='session_deadline'):reserve(uri,run,participant)


def test_choosing_the_relay_deletes_the_saved_key_and_neither_mode_falls_back(setup):
    s=setup;uri=s['uri'];run,participant=formal_run(s)
    teammate,_=identity(uri,team=s['team'])
    provider=save(uri,s['user'])
    call=uuid.uuid4();reserve(uri,run,participant,call);rpc(uri,'observer_settle_model',call,5)
    other,_=identity(uri);other_provider=save(uri,other)
    with pytest.raises(psycopg.Error,match='invalid_team_model_mode'):choose(uri,teammate,'organizer')
    assert choose(uri,teammate,'relay')=='relay'
    # Deleted at once: the used key keeps only a keyless receipt anchor.
    assert team_model(uri,s['user'])=={'mode':'relay','protocol':'openai','saved':None}
    assert query(uri,'select count(*) from private.observer_team_models where team_id=%s',(s['team'],))==[(0,)]
    assert query(uri,'select encrypted_key,enabled from private.observer_providers where id=%s',(provider,))==[('',False)]
    assert saved(uri,other) is not None
    assert query(uri,'select encrypted_key from private.observer_providers where id=%s',(other_provider,))==[(CIPHER,)]
    route=rpc(uri,'observer_model_route',run,participant)
    assert route['personal'] and route['mode']=='relay' and len(route['topic'])>64
    with pytest.raises(psycopg.Error,match='team_model_not_configured'):reserve(uri,run,participant)
    with pytest.raises(psycopg.Error,match='personal_api_required'):organizer_reservation(s,run,participant)
    relay_call=uuid.uuid4()
    assert rpc(uri,'observer_request_personal_model',run,participant,relay_call,'a'*64)
    rpc(uri,'observer_finish_personal_model',relay_call,'timeout')
    mode_audit=query(uri,"""select detail->>'saved_key_deleted' from public.audit_log
        where action='observer.team_model_mode' and detail->>'team_id'=%s""",(str(s['team']),))
    assert mode_audit==[('true',)]
    # Choosing stored mode again restores nothing; saving a key selects it as well.
    assert choose(uri,s['user'],'stored')=='stored'
    assert team_model(uri,s['user'])=={'mode':'stored','protocol':'openai','saved':None}
    with pytest.raises(psycopg.Error,match='team_model_not_configured'):reserve(uri,run,participant)
    choose(uri,s['user'],'relay');renewed=save(uri,teammate)
    assert team_model(uri,s['user'])['mode']=='stored'
    assert rpc(uri,'observer_model_route',run,participant)=={'personal':True,'mode':'stored','protocol':'openai'}
    assert rpc(uri,'observer_personal_model_routes',role='authenticated',user=s['user'])==[]
    with pytest.raises(psycopg.Error,match='personal_model_not_enabled'):
        rpc(uri,'observer_request_personal_model',run,participant,uuid.uuid4(),'b'*64)
    assert reserve(uri,run,participant)['provider_id']==str(renewed)


def test_saved_keys_never_fund_other_runs_and_practice_is_unchanged(setup):
    s=setup;uri=s['uri'];run,participant,_=session(s)
    provider=save(uri,s['user'])
    assert rpc(uri,'observer_model_route',run,participant)=={'personal':False}
    with pytest.raises(psycopg.Error,match='team_model_not_enabled'):reserve(uri,run,participant)
    # The existing organizer path for practice runs keeps working as before.
    assert organizer_reservation(s,run,participant)['reserved']
    with pytest.raises(psycopg.Error,match='provider_model_quota|model_not_available'):
        rpc(uri,'observer_reserve_model',run,participant,uuid.uuid4(),provider,'team-model','b'*64,1)
    with pytest.raises(psycopg.Error,match='model_not_available'):
        query(uri,"""insert into private.observer_model_calls(id,run_id,provider_id,usage_day,request_digest,reserved_tokens)
          values(%s,%s,%s,current_date,%s,1)""",(uuid.uuid4(),run,provider,'c'*64))
    # In competition mode the shared preparation phase is formal too and uses the team key.
    query(uri,"update private.observer_site_mode set mode='competition' where id")
    try:
        assert rpc(uri,'observer_model_route',run,participant)=={'personal':True,'mode':'stored','protocol':'openai'}
        # The open organizer call counts toward the run's concurrency.
        query(uri,'update private.observer_sessions set concurrency_limit=1 where run_id=%s',(run,))
        with pytest.raises(psycopg.Error,match='run_model_quota'):reserve(uri,run,participant)
        open_call=query(uri,"select id from private.observer_model_calls where run_id=%s and status='reserved'",(run,))[0][0]
        rpc(uri,'observer_settle_model',open_call,1)
        assert reserve(uri,run,participant)['provider_id']==str(provider)
    finally:
        query(uri,"update private.observer_site_mode set mode='practice' where id")


def test_competition_mode_also_requires_personal_api_for_shared_preparation_phase(setup):
    s=setup;uri=s['uri'];run,participant,_=session(s)
    assert rpc(uri,'observer_model_route',run,participant)=={'personal':False}
    # Project adaptation and preview are assigned a separate shared phase.
    query(uri,"update private.observer_site_mode set mode='competition' where id")
    try:
        choose(uri,s['user'],'relay')
        assert rpc(uri,'observer_model_route',run,participant)['personal']
        assert rpc(uri,'observer_personal_model_routes',role='authenticated',user=s['user'])[0]['run_id']==str(run)
        with pytest.raises(psycopg.Error,match='personal_api_required'):organizer_reservation(s,run,participant)
    finally:
        query(uri,"update private.observer_site_mode set mode='practice' where id")


def test_personal_model_routes_and_claims_are_private_and_credentials_are_not_stored(setup):
    s=setup;uri=s['uri'];run,participant,_=session(s)
    query(uri,'update public.phases set counts_for_final=true where id=%s',(s['phase'],))
    choose(uri,s['user'],'relay')
    route=rpc(uri,'observer_model_route',run,participant)
    assert route['personal'] and len(route['topic'])>64
    assert rpc(uri,'observer_personal_model_routes',role='authenticated',user=s['user'])==[{'run_id':str(run),'topic':route['topic']}]
    outsider,_=identity(uri)
    assert rpc(uri,'observer_personal_model_routes',role='authenticated',user=outsider)==[]
    with pytest.raises(psycopg.Error,match='permission denied'):
        rpc(uri,'observer_model_route',run,participant,role='authenticated',user=s['user'])
    with pytest.raises(psycopg.Error,match='personal_api_required'):organizer_reservation(s,run,participant)
    call=uuid.uuid4()
    assert rpc(uri,'observer_request_personal_model',run,participant,call,'a'*64)
    assert not rpc(uri,'observer_request_personal_model',run,participant,call,'a'*64)
    with pytest.raises(psycopg.Error,match='request_id_conflict'):
        rpc(uri,'observer_request_personal_model',run,participant,call,'b'*64)
    with pytest.raises(psycopg.Error,match='run_not_found'):
        rpc(uri,'observer_claim_personal_model',outsider,run,call,'a'*64)
    with pytest.raises(psycopg.Error,match='request_id_conflict'):
        rpc(uri,'observer_claim_personal_model',s['user'],run,call,'b'*64)
    def claim(_):
        try:return rpc(uri,'observer_claim_personal_model',s['user'],run,call,'a'*64)
        except psycopg.Error as e:
            assert 'model_request_already_received' in str(e)
            return 'duplicate'
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim,range(2)))==sorted([route['topic'],'duplicate'])
    rpc(uri,'observer_finish_personal_model',call,'done')
    rpc(uri,'observer_finish_personal_model',call,'timeout')
    assert query(uri,'select status from private.observer_personal_model_calls where id=%s',(call,))==[('done',)]
    stored=query(uri,'select row_to_json(c) from private.observer_personal_model_calls c where id=%s',(call,))[0][0]
    assert set(stored)=={'id','run_id','request_digest','status','created_at','updated_at'}
    # The relay honours the run's bounds: at most model_concurrency (4) outstanding calls...
    second=uuid.uuid4();assert rpc(uri,'observer_request_personal_model',run,participant,second,'a'*64)
    for _ in range(3):assert rpc(uri,'observer_request_personal_model',run,participant,uuid.uuid4(),'a'*64)
    with pytest.raises(psycopg.Error,match='run_model_quota'):
        rpc(uri,'observer_request_personal_model',run,participant,uuid.uuid4(),'a'*64)
    # ...and at most model_call_limit (10) calls per run.
    query(uri,"update private.observer_personal_model_calls set status='timeout' where run_id=%s and id<>%s",(run,second))
    for _ in range(5):
        extra=uuid.uuid4();assert rpc(uri,'observer_request_personal_model',run,participant,extra,'a'*64)
        rpc(uri,'observer_finish_personal_model',extra,'done')
    with pytest.raises(psycopg.Error,match='run_model_quota'):
        rpc(uri,'observer_request_personal_model',run,participant,uuid.uuid4(),'a'*64)
    assert query(uri,'select count(*) from private.observer_providers where team_id=%s',(s['team'],))==[(0,)]
    query(uri,"update private.observer_sessions set deadline_at=clock_timestamp()-interval '1 second' where run_id=%s",(run,))
    with pytest.raises(psycopg.Error,match='run_not_found'):
        rpc(uri,'observer_claim_personal_model',s['user'],run,second,'a'*64)


def test_purge_deletes_every_participant_key_and_keeps_organizer_providers(setup):
    s=setup;uri=s['uri'];run,participant=formal_run(s)
    rpc(uri,'observer_purge_provider_keys')  # keys saved by earlier tests in this shared database
    used=save(uri,s['user'])
    call=uuid.uuid4();reserve(uri,run,participant,call);rpc(uri,'observer_settle_model',call,5)
    other,other_team=identity(uri);unused=save(uri,other)
    legacy=uuid.uuid4()
    query(uri,"""insert into private.observer_providers(id,team_id,name,base_url,encrypted_key,models,enabled,daily_token_limit)
      values(%s,%s,'Old API',%s,%s,array['old-model'],false,100000)""",(legacy,other_team,BASE,CIPHER))
    organizer=query(uri,'select row_to_json(p)::text from private.observer_providers p where team_id is null order by id')
    with pytest.raises(psycopg.Error,match='permission denied'):
        rpc(uri,'observer_purge_provider_keys',role='authenticated',user=s['user'])
    assert rpc(uri,'observer_purge_provider_keys')==3
    assert query(uri,'select count(*) from private.observer_team_models')==[(0,)]
    assert query(uri,"select count(*) from private.observer_providers where team_id is not null and encrypted_key<>''")==[(0,)]
    # The used key's row stays as a keyless receipt anchor; unused rows are gone.
    assert query(uri,'select encrypted_key,enabled from private.observer_providers where id=%s',(used,))==[('',False)]
    assert query(uri,'select count(*) from private.observer_providers where id in (%s,%s)',(unused,legacy))==[(0,)]
    assert query(uri,'select row_to_json(p)::text from private.observer_providers p where team_id is null order by id')==organizer
    assert saved(uri,s['user']) is None and saved(uri,other) is None
    with pytest.raises(psycopg.Error,match='team_model_not_configured'):reserve(uri,run,participant)
    assert rpc(uri,'observer_purge_provider_keys')==0
    counts=query(uri,"select detail->>'count' from public.audit_log where action='observer.provider_keys_purged' order by id")
    assert [r[0] for r in counts][-2:]==['3','0']


def test_migration_restores_explicit_formal_limits_only():
    server,uri=start(apply_migrations=False)
    try:
        query(uri,(ROOT/'tests/supabase/auth_stub.sql').read_text())
        migrations=sorted((ROOT/'supabase/migrations').glob('*.sql'))
        stored=ROOT/'supabase/migrations/20260926000200_stored_model_keys.sql'
        assert stored in migrations
        for path in migrations:
            if path.name<stored.name:query(uri,path.read_text())
        formal,online,other=uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
        query(uri,"insert into public.phases(id,slug,name_en,name_zh,counts_for_final) values(%s,'final-a','F','F',true),(%s,'online','O','O',false),(%s,'lab','L','L',false)",
              (formal,online,other))
        query(uri,"""insert into public.observer_phase_settings(phase_id,model_token_limit,model_call_limit,model_concurrency)
          values(%s,0,0,1),(%s,0,0,2),(%s,5000,10,4)""",(formal,online,other))
        query(uri,stored.read_text())
        rows=query(uri,'select phase_id,model_token_limit,model_call_limit,model_concurrency from public.observer_phase_settings')
        assert sorted(rows)==sorted([(formal,10000000,10000,1),(online,10000000,10000,1),(other,5000,10,4)])
    finally:
        server.cleanup()


def test_participant_key_phases_get_loose_limits_and_organizer_phases_are_unchanged():
    server,uri=start(apply_migrations=False)
    try:
        query(uri,(ROOT/'tests/supabase/auth_stub.sql').read_text())
        migrations=sorted((ROOT/'supabase/migrations').glob('*.sql'))
        loose=ROOT/'supabase/migrations/20260926000700_participant_model_limits.sql'
        assert loose in migrations
        for path in migrations:
            if path.name<loose.name:query(uri,path.read_text())
        phases={slug:uuid.uuid4() for slug in ('final-a','online','practice-projects','observer-acceptance-abc','practice','lab')}
        for slug,phase in phases.items():
            query(uri,"insert into public.phases(id,slug,name_en,name_zh,counts_for_final) values(%s,%s,'P','P',%s)",
                  (phase,slug,slug=='final-a'))
        query(uri,"""insert into public.observer_phase_settings(phase_id,model_token_limit,model_call_limit,model_concurrency)
          select id,10000000,10000,1 from public.phases where slug<>'lab'""")
        query(uri,'insert into public.observer_phase_settings(phase_id,model_token_limit,model_call_limit,model_concurrency) values(%s,5000,10,2)',
              (phases['lab'],))
        # Re-running the migration is harmless.
        query(uri,loose.read_text());query(uri,loose.read_text())
        rows={r[0]:r[1:] for r in query(uri,'select phase_id,model_token_limit,model_call_limit,model_concurrency from public.observer_phase_settings')}
        for slug in ('final-a','online','practice-projects','observer-acceptance-abc'):
            assert rows[phases[slug]]==(1000000000,100000,4),slug
        # Phases that spend organizer keys keep their settings.
        assert rows[phases['practice']]==(10000000,10000,1) and rows[phases['lab']]==(5000,10,2)
        for column,value in (('model_token_limit',1000000001),('model_call_limit',100001),('model_concurrency',5),('model_concurrency',0)):
            with pytest.raises(psycopg.errors.CheckViolation):
                query(uri,f'update public.observer_phase_settings set {column}=%s where phase_id=%s',(value,phases['lab']))
    finally:
        server.cleanup()


OPT_IN=ROOT/'supabase/migrations/20260926000600_model_key_opt_in_auto_purge.sql'
STORED_DEFAULT=ROOT/'supabase/migrations/20261002170000_stored_model_mode_default.sql'


def auto_purge(uri):
    return query(uri,'select private.observer_auto_purge_provider_keys()')[0][0]


def keys_of(uri,team):
    return query(uri,"select count(*) from private.observer_providers where team_id=%s and encrypted_key<>''",(team,))[0][0]


def test_opt_in_migration_keeps_teams_that_saved_and_is_idempotent(setup):
    s=setup;uri=s['uri']
    saver,saver_team=identity(uri);save(uri,saver)
    chooser,_=identity(uri);choose(uri,chooser,'stored')
    newcomer,_=identity(uri)
    # A saved key without a mode row (older data) is kept in stored mode by the migration.
    query(uri,'delete from private.observer_team_model_modes where team_id=%s',(saver_team,))
    query(uri,OPT_IN.read_text());query(uri,OPT_IN.read_text())
    assert team_model(uri,saver)['mode']=='stored' and saved(uri,saver) is not None
    assert team_model(uri,chooser)=={'mode':'stored','protocol':'openai','saved':None}
    assert team_model(uri,newcomer)=={'mode':'relay','protocol':'openai','saved':None}
    assert query(uri,'select count(*),bool_and(enabled),max(retention)::text from private.observer_key_retention')==[(1,True,'7 days')]
    with pytest.raises(psycopg.Error,match='permission denied'):
        query(uri,'select private.observer_auto_purge_provider_keys()',role='authenticated',user=newcomer)


def test_stored_default_migration_only_changes_teams_that_never_chose(setup):
    s=setup;uri=s['uri']
    chose_relay,_=identity(uri);choose(uri,chose_relay,'relay')
    chose_stored,_=identity(uri);choose(uri,chose_stored,'stored')
    saver,_=identity(uri);save(uri,saver)
    never_chosen,_=identity(uri)
    # Already reached by the migration chain applied in setup; re-running it is a no-op.
    query(uri,STORED_DEFAULT.read_text());query(uri,STORED_DEFAULT.read_text())
    assert team_model(uri,chose_relay)=={'mode':'relay','protocol':'openai','saved':None}
    assert team_model(uri,chose_stored)=={'mode':'stored','protocol':'openai','saved':None}
    assert team_model(uri,saver)['mode']=='stored'
    assert team_model(uri,never_chosen)=={'mode':'stored','protocol':'openai','saved':None}
    assert query(uri,'select count(*) from private.observer_team_model_modes where team_id=%s',(never_chosen,))==[(0,)]


def test_saved_keys_are_purged_automatically_only_after_every_phase_that_uses_them_ended():
    server,uri=start()
    try:
        idle,idle_team=identity(uri);busy,busy_team=identity(uri)
        phase,scenario,lab=uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
        query(uri,"insert into public.phases(id,slug,name_en,name_zh,daily_limit,counts_for_final) values(%s,'final-a','F','F',10,false)",(phase,))
        query(uri,"""insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,
              model_token_limit,model_call_limit,model_concurrency) values(%s,true,true,1000,10,1)""",(phase,))
        query(uri,"insert into public.scenarios(id,slug,name) values(%s,'s','S')",(scenario,))
        query(uri,'insert into public.phase_scenarios values(%s,%s)',(phase,scenario))
        idle_key=save(uri,idle);save(uri,busy)
        run,participant,_=session({'uri':uri,'phase':phase,'user':busy})
        query(uri,'update public.phases set counts_for_final=true where id=%s',(phase,))
        # An open-ended, upcoming-end or recently ended formal phase keeps every key.
        assert auto_purge(uri)==0
        query(uri,"update public.phases set ends_at=now()+interval '1 day' where id=%s",(phase,))
        assert auto_purge(uri)==0
        query(uri,"update public.phases set ends_at=now()-interval '1 day' where id=%s",(phase,))
        assert auto_purge(uri)==0
        # Ended long enough ago, but a key saved recently still gets the full retention period.
        query(uri,"update public.phases set ends_at=now()-interval '8 days' where id=%s",(phase,))
        assert auto_purge(uri)==0
        query(uri,"update private.observer_team_models set saved_at=now()-interval '8 days'")
        # A phase restricted to another team does not hold this team's key; an open
        # non-formal phase holds keys only while the site is in competition mode.
        acceptance=uuid.uuid4()
        query(uri,"insert into public.phases(id,slug,name_en,name_zh) values(%s,'observer-acceptance-x','A','A'),(%s,'lab','L','L')",(acceptance,lab))
        query(uri,"insert into public.observer_phase_settings(phase_id,projects_enabled,access_team_id) values(%s,true,%s),(%s,true,null)",
              (acceptance,busy_team,lab))
        query(uri,"update private.observer_site_mode set mode='competition' where id")
        assert auto_purge(uri)==0 and keys_of(uri,idle_team)==1
        query(uri,"update private.observer_site_mode set mode='practice' where id")
        query(uri,"update private.observer_key_retention set enabled=false where id")
        assert auto_purge(uri)==0
        query(uri,"update private.observer_key_retention set enabled=true where id")
        # The idle team's key goes; the team whose evaluation is still queued keeps its key.
        assert auto_purge(uri)==1
        assert keys_of(uri,idle_team)==0 and keys_of(uri,busy_team)==1
        assert query(uri,'select count(*) from private.observer_providers where id=%s',(idle_key,))==[(0,)]
        assert team_model(uri,idle)=={'mode':'stored','protocol':'openai','saved':None}
        # An unsettled reservation also holds the key after the run has left the queue.
        query(uri,"update public.observer_runs set status='scored',finished_at=now(),score=1 where id=%s",(run,))
        query(uri,"update public.observer_batches set status='scored',finished_at=now(),score=1")
        provider=query(uri,'select provider_id from private.observer_team_models where team_id=%s',(busy_team,))[0][0]
        call=uuid.uuid4()
        query(uri,"""insert into private.observer_provider_usage(provider_id,usage_day) values(%s,current_date) on conflict do nothing""",(provider,))
        query(uri,"""insert into private.observer_model_calls(id,run_id,provider_id,usage_day,request_digest,reserved_tokens)
              values(%s,%s,%s,current_date,%s,5)""",(call,run,provider,'e'*64))
        assert auto_purge(uri)==0
        query(uri,"update private.observer_model_calls set status='settled',actual_tokens=5,settled_at=now() where id=%s",(call,))
        # The open acceptance phase restricted to this team still holds its key.
        assert auto_purge(uri)==0
        query(uri,"update public.phases set ends_at=now()-interval '10 days' where id=%s",(acceptance,))
        # The used key keeps only a keyless receipt anchor, as with the manual purge.
        assert auto_purge(uri)==1 and auto_purge(uri)==0
        assert query(uri,'select encrypted_key,enabled from private.observer_providers where id=%s',(provider,))==[('',False)]
        audit=query(uri,"select detail from public.audit_log where action='observer.provider_keys_auto_purged' order by id")
        assert [a[0] for a in audit]==[{'count':1,'teams':[str(idle_team)]},{'count':1,'teams':[str(busy_team)]}]
        assert CIPHER not in str(audit)
        # The manual purge keeps working alongside it.
        save(uri,idle);assert rpc(uri,'observer_purge_provider_keys')==1
    finally:
        server.cleanup()


def test_saved_keys_outlive_the_online_phase_until_sealed_final_results_are_published():
    server,uri=start()
    try:
        user,team=identity(uri);save(uri,user)
        query(uri,"update private.observer_team_models set saved_at=now()-interval '30 days'")
        online,final=uuid.uuid4(),uuid.uuid4()
        query(uri,"""insert into public.phases(id,slug,name_en,name_zh,counts_for_final,ends_at,leaderboard_mode)
              values(%s,'online','O','O',true,now()-interval '20 days','live'),
                    (%s,'final-hidden','H','H',true,now()+interval '10 days','hidden')""",(online,final))
        query(uri,"insert into public.observer_phase_settings(phase_id,projects_enabled,sealed) values(%s,true,false),(%s,false,true)",(online,final))
        # The online phase ended long ago: the hidden final still needs the key.
        assert auto_purge(uri)==0 and keys_of(uri,team)==1
        # Past the sealed phase's end plus retention, but results not yet published.
        query(uri,"update public.phases set ends_at=now()-interval '10 days' where id=%s",(final,))
        assert auto_purge(uri)==0 and keys_of(uri,team)==1
        # Results published (verified): the key goes.
        query(uri,"update public.phases set leaderboard_mode='published' where id=%s",(final,))
        assert auto_purge(uri)==1 and keys_of(uri,team)==0
    finally:
        server.cleanup()
