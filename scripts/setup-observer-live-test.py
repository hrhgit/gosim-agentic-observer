#!/usr/bin/env python3
"""Provision a hidden acceptance-test team and team-restricted Observer phase.

Uses a new synthetic account, preserves all existing participants/submissions,
and keeps its reusable password/IDs only in macOS Keychain.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from challenge.scenario_builder import generate_scenario
from project_platform.artifacts import pack_files
from project_platform.package import ProjectFile

SERVICE='agentic-observer26-backend'


def quote(value):return "'"+str(value).replace("'","''")+"'"


def request(base,path,data=None,*,token=None,raw=False,headers=None):
    secret=token or os.environ['SUPABASE_SERVICE_ROLE_KEY']
    payload=data if isinstance(data,bytes) else None if data is None else json.dumps(data).encode()
    req=urllib.request.Request(base+path,data=payload,headers={
      'Authorization':'Bearer '+secret,'apikey':os.environ['SUPABASE_ANON_KEY'],'Content-Type':'application/json',**(headers or {})})
    try:
        with urllib.request.urlopen(req,timeout=60) as response:
            value=response.read()
            return value if raw else json.loads(value) if value else None
    except urllib.error.HTTPError as e:
        raise RuntimeError('Remote operation failed: '+str(e.code)+' '+path.split('?')[0]) from None


def query(sql):
    return request('https://api.supabase.com','/v1/projects/'+os.environ['SUPABASE_PROJECT_REF']+'/database/query',
                   {'query':sql},token=os.environ['SUPABASE_ACCESS_TOKEN'])


def save(state):
    result=subprocess.run(['security','add-generic-password','-U','-s',SERVICE,'-a',os.environ['SUPABASE_PROJECT_REF'],
                           '-w',json.dumps(state)],capture_output=True)
    if result.returncode:raise RuntimeError('Cannot save test identity in Keychain')


def upload_bundle(scenario_id,files):
    bundle=pack_files(files);digest=hashlib.sha256(bundle).hexdigest()
    path=str(scenario_id)+'/'+digest+'.zip'
    present=query("select 1 from storage.objects where bucket_id='observer-scenarios' and name="+quote(path))
    if not present:
        request(os.environ['SUPABASE_URL'],'/storage/v1/object/observer-scenarios/'+path,bundle,
                headers={'Content-Type':'application/zip','x-upsert':'false'})
    query('insert into private.observer_scenario_bundles(scenario_id,storage_path,digest) values('
          +','.join(map(quote,(scenario_id,path,digest)))+') on conflict(scenario_id) do nothing')


def main():
    state=json.loads(subprocess.check_output(['security','find-generic-password','-s',SERVICE,
                      '-a',os.environ['SUPABASE_PROJECT_REF'],'-w'],text=True))
    if 'lab' not in state:
        state['lab']={'email':'observer-platform-e2e@create.gosim.org','password':secrets.token_urlsafe(32),
          'phase_id':str(uuid.uuid4()),'scenario_ids':[str(uuid.uuid4()),str(uuid.uuid4())]}
        save(state)
    lab=state['lab'];url=os.environ['SUPABASE_URL']
    users=query("select id,raw_user_meta_data->>'observer_platform_e2e' as marker from auth.users where email="+quote(lab['email']))
    if users:
        if users[0]['marker']!='true':raise RuntimeError('Refusing to modify an existing non-test account')
        user=users[0]['id']
    else:
        user=request(url,'/auth/v1/admin/users',{'email':lab['email'],'password':lab['password'],'email_confirm':True,
          'user_metadata':{'full_name':'Observer platform acceptance test','observer_platform_e2e':True}})['id']
    lab['user_id']=user;save(state)
    query("begin;update public.profiles set show_on_wall=false where id="+quote(user)+";"
      "insert into public.site_settings(key,value) values('excluded_accounts',jsonb_build_array("+quote(user)+"::text)) "
      "on conflict(key) do update set value=case when public.site_settings.value @> excluded.value then public.site_settings.value "
      "else public.site_settings.value || excluded.value end;commit;")
    auth=request(url,'/auth/v1/token?grant_type=password',{'email':lab['email'],'password':lab['password']},
                 token=os.environ['SUPABASE_ANON_KEY'])['access_token']
    profile=query('select team_id from public.profiles where id='+quote(user))[0]
    team=profile['team_id'] or request(url,'/rest/v1/rpc/create_team',{
      'p_name':'Observer platform acceptance test','p_max_size':3,'p_project_idea':'Private automated acceptance tests','p_github_repo':''},token=auth)
    lab['team_id']=team;save(state)
    query('update public.teams set is_hidden=true where id='+quote(team))
    # The phase becomes active and restricted in the same transaction.
    # The legacy homepage chooses its default phase by sort_order. Keep this
    # hidden acceptance phase behind participant phases even while it is open.
    query("begin;insert into public.phases(id,slug,name_en,name_zh,allow_results,allow_agents,leaderboard_mode,counts_for_final,is_active,sort_order) values("
      +quote(lab['phase_id'])+",'observer-platform-e2e','Private platform acceptance','平台内部验收',false,false,'hidden',false,true,10000) on conflict(id) do update set sort_order=10000;"
      "insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,runtime_seconds,daily_batches,model_token_limit,model_call_limit,access_team_id) values("
      +quote(lab['phase_id'])+",true,true,300,20,5000,10,"+quote(team)+") on conflict(phase_id) do nothing;commit;")
    for i,scenario_id in enumerate(lab['scenario_ids']):
        slug='observer-platform-e2e-'+str(i+1)
        query("insert into public.scenarios(id,slug,name,is_active,weather_public,forecasts_public,events_public,n_nights) values("
              +','.join(map(quote,(scenario_id,slug,'Private acceptance scenario '+str(i+1))))+",false,false,false,false,7) on conflict(id) do nothing")
        query('insert into public.phase_scenarios(phase_id,scenario_id) values('+quote(lab['phase_id'])+','+quote(scenario_id)+') on conflict do nothing')
        if not query('select 1 from private.observer_scenario_bundles where scenario_id='+quote(scenario_id)):
            with tempfile.TemporaryDirectory(prefix='observer-live-scenario-') as root:
                scenario=Path(root)/'scenario'
                generate_scenario(scenario,scenario_id=slug,seed=800+i,days=7,start_date='2026-10-05',global_wallclock_seconds=300)
                upload_bundle(scenario_id,[ProjectFile(p.relative_to(scenario).as_posix(),p.read_bytes()) for p in scenario.rglob('*') if p.is_file()])
    public=query("select id from public.scenarios where slug='dev-fortnight' and weather_public and forecasts_public and events_public")[0]['id']
    if not query('select 1 from private.observer_scenario_bundles where scenario_id='+quote(public)):
        objects=query("select name from storage.objects where bucket_id='scenarios' and (name like 'dev-fortnight/config/%' or name like 'dev-fortnight/outputs/reference/%') order by name")
        files=[]
        for item in objects:
            name=item['name']
            files.append(ProjectFile(name.removeprefix('dev-fortnight/'),request(url,'/storage/v1/object/authenticated/scenarios/'+
                urllib.parse.quote(name,safe='/'),raw=True)))
        upload_bundle(public,files)
    query("insert into private.observer_preparation_config(id,phase_id,scenario_id,model,enabled) values(true,"
      +quote(lab['phase_id'])+','+quote(public)+",'qwen3.6:35b-a3b',true) on conflict(id) do nothing")
    visible=request(url,'/rest/v1/phases?id=eq.'+lab['phase_id']+'&select=id',token=os.environ['SUPABASE_ANON_KEY'])
    if visible:raise RuntimeError('Private test phase is visible to visitors')
    print(json.dumps({'team_id':team,'user_id':user,'phase_id':lab['phase_id'],'private_scenarios':lab['scenario_ids'],
      'preview_scenario':public,'anonymous_phase_visibility':False,'credentials':'Keychain only'}))


if __name__=='__main__':main()
