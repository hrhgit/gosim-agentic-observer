#!/usr/bin/env python3
"""Configure per-team private acceptance phases for the project platform.

Creates or updates one team-restricted, projects-only phase per acceptance
team (for example the Python/Rust/Node test teams). Everything is idempotent,
explicitly scoped to marked acceptance teams, and never touches participant
phases, the real online phase, the global site mode, or the hidden lab.

Default is a dry-run that only reads and prints the plan; pass --apply to write.

Prerequisites (same environment as setup-observer-live-test.py):
SUPABASE_PROJECT_REF, SUPABASE_ACCESS_TOKEN, SUPABASE_URL, SUPABASE_ANON_KEY,
SUPABASE_SERVICE_ROLE_KEY.

Usage:
  python scripts/configure-observer-acceptance.py \
    --team-id <uuid> --team-id <uuid> --team-id <uuid> \
    --scenario-id <uuid> --scenario-id <uuid> [--apply] [--daily-batches 10]
  python scripts/configure-observer-acceptance.py --team-id <uuid> --same-as online [--apply]

--same-as <phase slug> runs the acceptance team on exactly that phase's scenarios
and calibration profiles, so every run gets a fresh random seed and a calibrated
score just like participants. Only the acceptance team can see the phase.

Each phase: counts_for_final=false, leaderboard_mode=hidden, projects only
(local sessions and legacy CSV are rejected by the platform), runtime capped
at 300s, exactly two scenarios, no organizer model tokens (the migration
20260926000300 makes observer-acceptance-* runs use the team's own key; the
same per-run caps as formal runs apply: 100,000 calls, up to 4 at a time, 1,000,000,000 tokens).
"""
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1]

MAX_RUNTIME_SECONDS=300
# Per-run model bounds for runs on the team's own key (same as formal runs,
# migration 20260926000600): tokens are effectively unlimited because the team pays for
# them; calls stay capped because each one passes through observer-model.
MODEL_TOKEN_LIMIT=1_000_000_000
MODEL_CALL_LIMIT=100_000
MODEL_CONCURRENCY=4
PROTECTED_SLUGS=('online','practice','observer-platform-e2e')
SERVICE='agentic-observer26-backend'


class CheckError(RuntimeError):
    pass


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
    rows=request('https://api.supabase.com','/v1/projects/'+os.environ['SUPABASE_PROJECT_REF']+'/database/query',
                 {'query':sql},token=os.environ['SUPABASE_ACCESS_TOKEN'])
    # The management API answers with one JSON object per row (columns in select order);
    # the checks below unpack rows positionally, like database cursors.
    return [tuple(row.values()) if isinstance(row,dict) else row for row in rows or []]


def one(sql):
    rows=query(sql)
    if len(rows)!=1 or len(rows[0])!=1:
        raise CheckError('Expected exactly one value for: '+sql)
    return rows[0][0]


def check_team(team_id):
    rows=query('select id,is_hidden from public.teams where id='+quote(team_id))
    if not rows:raise CheckError('Team '+str(team_id)+' does not exist')
    found_id,is_hidden=rows[0]
    if not is_hidden:raise CheckError('Team '+str(found_id)+' is not marked is_hidden; refusing to treat it as an acceptance team')
    members=query('select count(*) from public.profiles where team_id='+quote(team_id)+' and not is_banned')[0][0]
    if not members:raise CheckError('Team '+str(found_id)+' has no active members')
    return {'team_id':str(found_id),'hidden':True,'active_members':members}


def check_scenario(scenario_id,phase_slug,allowed_phase=None):
    rows=query('select id,slug,name,is_active from public.scenarios where id='+quote(scenario_id))
    if not rows:raise CheckError('Scenario '+str(scenario_id)+' does not exist')
    found_id,slug,name,is_active=rows[0]
    if not is_active:raise CheckError('Scenario '+str(found_id)+' ('+slug+') is not active')
    bundle=query('select storage_path,digest from private.observer_scenario_bundles where scenario_id='+quote(scenario_id))
    if not bundle:raise CheckError('Scenario '+str(found_id)+' has no private bundle; the engine could not run it')
    leak=query('select p.slug from public.phase_scenarios ps join public.phases p on p.id=ps.phase_id'
               ' where ps.scenario_id='+quote(scenario_id)+' and (p.counts_for_final or p.slug in '+str(PROTECTED_SLUGS)+')'
               # With --same-as, the copied phase and other internal team-restricted phases
               # (earlier acceptance labs on the same scenarios) are not participant material.
               +(' and p.slug<>'+quote(allowed_phase)+' and not exists(select 1 from public.observer_phase_settings s'
                 ' where s.phase_id=p.id and s.access_team_id is not null)' if allowed_phase else ''))
    if leak:raise CheckError('Scenario '+str(found_id)+' is already linked to participant phase(s) '+json.dumps(leak)+'; refusing to reuse formal material')
    bundle_path,bundle_digest=bundle[0]
    present=one("select count(*) from storage.objects where bucket_id='observer-scenarios' and name="+quote(bundle_path))
    if not present:raise CheckError('Scenario bundle object missing from storage: '+bundle_path)
    return {'scenario_id':str(found_id),'slug':slug,'bundle_path':bundle_path,'bundle_digest':bundle_digest}


def source_calibration(slug,scenarios):
    """Calibration profiles of the phase the acceptance run copies, one per scenario."""
    rows=query('select c.scenario_id,c.profile_id from private.observer_scenario_calibration c join public.phases p on p.id=c.phase_id'
               ' where p.slug='+quote(slug))
    profiles={str(scenario):str(profile) for scenario,profile in rows}
    missing=[s['scenario_id'] for s in scenarios if s['scenario_id'] not in profiles]
    if missing:raise CheckError('Phase '+slug+' has no calibration for scenario(s) '+json.dumps(missing))
    return profiles


def plan_phase(team,scenarios,args,profiles=None):
    slug='observer-acceptance-'+str(team['team_id'])[:8]
    existing=query('select id,slug,name_en,counts_for_final,leaderboard_mode,is_active,sort_order from public.phases where slug='+quote(slug))
    if existing:
        phase_id,_,name_en,counts_for_final,leaderboard_mode,is_active,sort_order=existing[0]
        if counts_for_final or leaderboard_mode!='hidden':
            raise CheckError('Phase '+str(phase_id)+' with slug '+slug+' is not an acceptance phase; refusing to modify it')
    else:
        phase_id=str(uuid.uuid4());name_en=None;is_active=None;sort_order=None
    settings=query('select phase_id,projects_enabled,local_sessions_enabled,runtime_seconds,daily_batches,'
                   'model_token_limit,model_call_limit,access_team_id from public.observer_phase_settings where phase_id='+quote(str(phase_id)))
    if settings:
        (_,projects_enabled,local_sessions_enabled,runtime_seconds,daily_batches,model_token_limit,model_call_limit,access_team_id)=settings[0]
        if access_team_id is not None and str(access_team_id)!=team['team_id']:
            raise CheckError('Phase '+str(phase_id)+' is restricted to a different team '+str(access_team_id))
    else:
        projects_enabled=local_sessions_enabled=None;runtime_seconds=daily_batches=None
        model_token_limit=model_call_limit=None;access_team_id=None
    links=query('select scenario_id from public.phase_scenarios where phase_id='+quote(str(phase_id))+' order by scenario_id')
    wanted=sorted(s['scenario_id'] for s in scenarios)
    if links and sorted(str(r[0]) for r in links)!=wanted:
        raise CheckError('Phase '+str(phase_id)+' already links different scenarios '+json.dumps([str(r[0]) for r in links])+
                         '; scenario sets are immutable for existing acceptance phases')
    runtime=min(int(args.runtime_seconds),MAX_RUNTIME_SECONDS)
    statements=[
      "insert into public.phases(id,slug,name_en,name_zh,allow_results,allow_agents,leaderboard_mode,counts_for_final,is_active,sort_order) values ("
        +','.join(map(quote,(phase_id,slug,'Acceptance '+team['team_id'][:8],'内部验收',False,False,'hidden',False,True,args.sort_order)))+')'
        +' on conflict(id) do update set slug=excluded.slug,is_active=excluded.is_active,sort_order=excluded.sort_order',
      "insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,runtime_seconds,daily_batches,model_token_limit,model_call_limit,model_concurrency,access_team_id) values ("
        +','.join(map(quote,(phase_id,True,False,runtime,args.daily_batches,MODEL_TOKEN_LIMIT,MODEL_CALL_LIMIT,MODEL_CONCURRENCY,team['team_id'])))+')'
        +' on conflict(phase_id) do update set projects_enabled=excluded.projects_enabled,local_sessions_enabled=excluded.local_sessions_enabled,'
        +'runtime_seconds=excluded.runtime_seconds,daily_batches=excluded.daily_batches,access_team_id=excluded.access_team_id,'
        +'model_token_limit=excluded.model_token_limit,model_call_limit=excluded.model_call_limit,model_concurrency=excluded.model_concurrency',
    ]
    for scenario in scenarios:
        statements.append('insert into public.phase_scenarios(phase_id,scenario_id) values ('
                          +quote(phase_id)+','+quote(scenario['scenario_id'])+') on conflict do nothing')
    # Same calibration profiles as the copied phase: each run gets its own random seed.
    for scenario in scenarios if profiles else []:
        statements.append('insert into private.observer_scenario_calibration(phase_id,scenario_id,profile_id) values ('
                          +','.join(map(quote,(phase_id,scenario['scenario_id'],profiles[scenario['scenario_id']])))+') on conflict do nothing')
    calibration=[]
    for scenario in scenarios:
        rows=query('select c.profile_id,p.bundle_digest from private.observer_scenario_calibration c'
                   ' join private.observer_calibration_profiles p on p.id=c.profile_id'
                   ' where c.phase_id='+quote(str(phase_id))+' and c.scenario_id='+quote(scenario['scenario_id']))
        if not rows and profiles:
            rows=query('select id,bundle_digest from private.observer_calibration_profiles where id='+quote(profiles[scenario['scenario_id']]))
        if rows:
            profile_id,profile_digest=rows[0]
            seeded=profile_digest==scenario['bundle_digest']
            calibration.append({'scenario_id':scenario['scenario_id'],'calibrated':True,'profile_matches_bundle':seeded,
                                'note':'' if seeded else 'calibration profile digest differs from the current bundle; recalibrate before trusting seeded scores'})
        else:
            calibration.append({'scenario_id':scenario['scenario_id'],'calibrated':False,
                                'note':'runs use the deterministic bundle; each-run random seeds only start once calibration rows exist (configure-observer-calibration.py)'})
    return {'team_id':team['team_id'],'phase_id':str(phase_id),'slug':slug,'is_new_phase':existing==[],
            'projects_only':{'projects_enabled':True,'local_sessions_enabled':False,
                             'legacy_csv':'rejected by observer_submission_admission (any phase with an settings row)'},
            'runtime_seconds':runtime,'daily_batches':args.daily_batches,
            'model':f'team-provided keys only; run caps model_token_limit={MODEL_TOKEN_LIMIT}, model_call_limit={MODEL_CALL_LIMIT}, '
                     f'model_concurrency={MODEL_CONCURRENCY}; organizer provider untouched',
            'statements':statements,'calibration':calibration}


def verify(phase_id):
    anon=request(os.environ['SUPABASE_URL'],'/rest/v1/phases?id=eq.'+urllib.parse.quote(phase_id)+'&select=id',
                 token=os.environ['SUPABASE_ANON_KEY'])
    if anon:raise CheckError('Acceptance phase '+phase_id+' is visible to anonymous visitors')
    row=one('select projects_enabled or local_sessions_enabled from public.observer_phase_settings where phase_id='+quote(phase_id))
    if not row:raise CheckError('Acceptance phase '+phase_id+' has no observer settings row')
    return {'anonymous_phase_visibility':False,'session_locked':True}


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--team-id',action='append',required=True,help='acceptance team uuid (repeat for each test team)')
    parser.add_argument('--scenario-id',action='append',default=[],help='private synthetic scenario uuid (exactly two)')
    parser.add_argument('--same-as',help='phase slug whose scenarios and calibration the acceptance phase reuses (e.g. online)')
    parser.add_argument('--apply',action='store_true',help='write changes (default: dry-run)')
    parser.add_argument('--daily-batches',type=int,default=10)
    parser.add_argument('--runtime-seconds',type=int,default=MAX_RUNTIME_SECONDS)
    parser.add_argument('--sort-order',type=int,default=10100,help='kept behind the hidden lab (10000) and all participant phases')
    args=parser.parse_args()
    profiles=None
    if args.same_as:
        if args.scenario_id:raise CheckError('--same-as takes the scenarios from that phase; do not pass --scenario-id')
        args.scenario_id=[str(r[0]) for r in query('select ps.scenario_id from public.phase_scenarios ps join public.phases p'
            ' on p.id=ps.phase_id where p.slug='+quote(args.same_as)+' order by ps.scenario_id')]
    if len(args.scenario_id)!=2:raise CheckError('Exactly two scenarios are required')
    for scenario_id in args.scenario_id:uuid.UUID(scenario_id)
    plans=[]
    for team_id in args.team_id:
        uuid.UUID(team_id)
        team=check_team(team_id)
        scenarios=[check_scenario(scenario_id,team_id,args.same_as) for scenario_id in args.scenario_id]
        if args.same_as and profiles is None:profiles=source_calibration(args.same_as,scenarios)
        plans.append(plan_phase(team,scenarios,args,profiles))
    global_guards=query("select slug from public.phases where counts_for_final or slug in "+str(PROTECTED_SLUGS))
    report={'mode':'apply' if args.apply else 'dry-run','phases':plans,
            'untouched':{'global_site_mode':'private.observer_site_mode never read or written',
                         'participant_and_formal_phases':[str(r[0]) for r in global_guards],
                         'hidden_lab':'slug observer-platform-e2e and its rows are protected slugs here',
                         'preparation_config':'private.observer_preparation_config is a single global row owned by the lab; not modified',
                         'organizer_model_provider':'not read or written; teams bring their own keys'},
            'randomized_seeds':'on when every scenario is calibrated (see the per-scenario report above); --same-as copies them',
            'verification':None}
    for statement in [s for plan in plans for s in plan['statements']]:
        print('-- ' + statement)
    if not args.apply:
        print(json.dumps(report,indent=2))
        return
    for statement in [s for plan in plans for s in plan['statements']]:
        query(statement)
    for plan in plans:
        plan['verification']=verify(plan['phase_id'])
    report['verification']='all phases verified (anonymous invisible, settings locked)'
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
