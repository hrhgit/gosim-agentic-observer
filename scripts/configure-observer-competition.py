#!/usr/bin/env python3
"""Prepare the fixed formal scenarios, then enable the agreed competition flow.

Organizer decision 2026-09-26: the 'online' phase evaluates on three fixed
scenarios (eval-a, eval-b, eval-c), the same template for every team (no
calibration, no per-team instance). Their files and weather stay private:
observations arrive step by step. The final ranking comes from one hidden
scenario in a separate sealed phase ('final-hidden'), run by
scripts/run-hidden-final.py after 'online' ends; this script only inspects it.

No phase dates, legacy submissions, practice configuration or provider budget is
changed. Activation requires the specified frontend revision to be live first.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
# Per-run model bounds for runs on the team's own key (see migration
# 20260926000600): tokens are effectively unlimited because the team pays for
# them; calls stay capped because each one passes through observer-model.
MODEL_TOKEN_LIMIT=1_000_000_000
MODEL_CALL_LIMIT=100_000
MODEL_CONCURRENCY=4
sys.path.insert(0,str(ROOT))
from project_platform.artifacts import pack_files
from project_platform.package import ProjectFile

spec=importlib.util.spec_from_file_location('observer_deploy',ROOT/'scripts/deploy-observer-backend.py')
deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)
q=deploy.quote


def storage(path,data=None):
    request=urllib.request.Request(os.environ['SUPABASE_URL']+'/storage/v1/'+path,data=data,
      headers={'Authorization':'Bearer '+os.environ['SUPABASE_SERVICE_ROLE_KEY'],
               'apikey':os.environ['SUPABASE_ANON_KEY'],'Content-Type':'application/zip','x-upsert':'false'})
    try:
        with urllib.request.urlopen(request,timeout=90) as response:return response.read(104857601)
    except urllib.error.HTTPError as exc:
        raise RuntimeError('Scenario storage request failed: HTTP '+str(exc.code)) from None


def configured_runners():
    # The runner list is ops/github-installations.json; a row marked enabled=false
    # (for example a newly added organization still being verified) is not required.
    config=json.loads((ROOT/'ops/github-installations.json').read_text())
    orgs=[r['organization'] for r in config['installations'] if r.get('enabled',True)]
    if not orgs or any(not re.fullmatch(r'AGENTIC-OBSERVER26-runner-(?:[1-9]|[1-9][0-9])',o) for o in orgs):
        raise ValueError('Invalid runner configuration')
    return orgs


def activation_sql(phase_id,preview_id,runtime,daily,model,scenario_count=3,runners=None):
    # Values come from existing DB metadata, never from a submitted project.
    if not 10<=runtime<=18000 or not 1<=daily<=100 or not 1<=int(scenario_count)<=10:raise ValueError('Invalid existing phase limits')
    runners=configured_runners() if runners is None else list(runners)
    if not runners:raise ValueError('Invalid runner configuration')
    required='array['+','.join(q(o) for o in runners)+']::text[]'
    return f"""
begin;
select pg_advisory_xact_lock(hashtext('observer-competition-activation'));
do $verify$ begin
  if not exists(select 1 from public.phases where id={q(phase_id)} and slug='online' and is_active and counts_for_final)
    then raise exception 'Competition phase is not available';end if;
  if exists(select 1 from public.submissions where phase_id={q(phase_id)})
    then raise exception 'Existing competition submissions require an explicit migration plan';end if;
  if exists(select unnest({required}) except select organization from private.observer_installations where enabled)
    or not exists(select 1 from private.observer_dispatch_config where enabled)
    then raise exception 'Runners or dispatcher are not ready';end if;
  if (select count(*) from public.phase_scenarios where phase_id={q(phase_id)})<>{int(scenario_count)}
    or exists(select 1 from public.phase_scenarios ps join public.scenarios s on s.id=ps.scenario_id
      left join private.observer_scenario_bundles b on b.scenario_id=s.id
      where ps.phase_id={q(phase_id)} and (b.scenario_id is null or not s.is_active
        or s.weather_public or s.forecasts_public or s.events_public))
    then raise exception 'Hidden competition bundles are not ready';end if;
  -- Fixed formal scenarios: one template for every team, never a private instance.
  if exists(select 1 from private.observer_scenario_calibration where phase_id={q(phase_id)})
    then raise exception 'Competition phase still has calibration rows';end if;
  -- A hidden final scenario is never evaluated in the public phase.
  if exists(select 1 from public.phase_scenarios ps join public.phase_scenarios other on other.scenario_id=ps.scenario_id
      join public.observer_phase_settings c on c.phase_id=other.phase_id
      where ps.phase_id={q(phase_id)} and c.sealed)
    then raise exception 'A hidden final scenario is linked to the competition phase';end if;
  if not exists(select 1 from public.scenarios s join private.observer_scenario_bundles b on b.scenario_id=s.id
    where s.id={q(preview_id)} and s.is_active and s.weather_public and s.forecasts_public and s.events_public)
    then raise exception 'Public preview is not ready';end if;
end $verify$;
-- Participant-funded model use (team keys): no practical token cap, bounded calls, up to 4 at a time.
insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,runtime_seconds,
  daily_batches,model_token_limit,model_call_limit,model_concurrency)
  values({q(phase_id)},true,false,{runtime},{daily},{MODEL_TOKEN_LIMIT},{MODEL_CALL_LIMIT},{MODEL_CONCURRENCY})
on conflict(phase_id) do nothing;
do $settings$ begin
  if not exists(select 1 from public.observer_phase_settings where phase_id={q(phase_id)}
    and projects_enabled and not local_sessions_enabled and runtime_seconds={runtime}
    and daily_batches={daily} and access_team_id is null)
    then raise exception 'Existing phase settings differ; refusing to overwrite them';end if;
end $settings$;
insert into private.observer_preparation_config(id,phase_id,scenario_id,model,enabled)
  values(true,{q(phase_id)},{q(preview_id)},{q(model)},true)
on conflict(id) do update set phase_id=excluded.phase_id,scenario_id=excluded.scenario_id,model=excluded.model,enabled=true;
select private.audit('observer.competition_enabled',jsonb_build_object('phase_id',{q(phase_id)},'runtime_seconds',{runtime},'daily_batches',{daily}));
commit;
"""


FORMAL_FILES={'config/workflow_config.json','config/score_config.json','outputs/reference/weather.csv',
              'outputs/reference/tiles.csv','outputs/reference/tile_anomalies.csv'}


def prepare_bundle(scenario,required=FORMAL_FILES):
    sid,slug=scenario['id'],scenario['slug']
    objects=deploy.query("select name from storage.objects where bucket_id='scenarios' and (name like "+
      q(slug+'/config/%')+" or name like "+q(slug+'/outputs/reference/%')+") order by name")
    if not objects:raise RuntimeError('Existing scenario has no files')
    def fetch(item):
        name=item['name'];raw=storage('object/authenticated/scenarios/'+urllib.parse.quote(name,safe='/'))
        if len(raw)>104857600:raise RuntimeError('Scenario file exceeds the transfer limit')
        return ProjectFile(name.removeprefix(slug+'/'),raw)
    with ThreadPoolExecutor(max_workers=4) as pool:files=tuple(pool.map(fetch,objects))
    missing=sorted(set(required)-{f.path for f in files})
    if missing:raise RuntimeError('Incomplete scenario: missing '+', '.join(missing))
    data=pack_files(files);digest=hashlib.sha256(data).hexdigest();path=sid+'/'+digest+'.zip'
    recorded=deploy.query('select digest,storage_path from private.observer_scenario_bundles where scenario_id='+q(sid))
    if recorded and recorded!=[{'digest':digest,'storage_path':path}]:
        raise RuntimeError('Scenario already registered with different content; refusing replacement')
    existing=deploy.query("select 1 from storage.objects where bucket_id='observer-scenarios' and name="+q(path))
    if not existing:storage('object/observer-scenarios/'+path,data)
    actual=storage('object/authenticated/observer-scenarios/'+path)
    if hashlib.sha256(actual).hexdigest()!=digest:raise RuntimeError('Stored scenario checksum differs')
    deploy.query('insert into private.observer_scenario_bundles(scenario_id,storage_path,digest) values('+
      ','.join(map(q,(sid,path,digest)))+') on conflict(scenario_id) do nothing')
    return {'scenario':slug,'files':len(files),'verified':True}


def inspect_final(slug):
    """Read-only readiness of the sealed hidden final phase; never prints scenario contents."""
    rows=deploy.query("select p.id,p.is_active,p.counts_for_final,p.leaderboard_mode,c.sealed,c.projects_enabled "+
      "from public.phases p left join public.observer_phase_settings c on c.phase_id=p.id where p.slug="+q(slug))
    if not rows:return {'phase':slug,'ready':False,'problems':['phase missing']}
    p=rows[0];problems=[]
    if not p['sealed']:problems.append('phase is not sealed')
    if not p['counts_for_final']:problems.append('phase does not count for the final')
    if not p['projects_enabled']:problems.append('projects are not enabled')
    if p['leaderboard_mode']=='published':problems.append('results are already published')
    scenarios=deploy.query("select s.slug,s.weather_public or s.forecasts_public or s.events_public as public,"+
      "exists(select 1 from private.observer_scenario_bundles b where b.scenario_id=s.id) as bundle,"+
      "(select count(*) from public.phase_scenarios o where o.scenario_id=s.id) as links "+
      "from public.phase_scenarios ps join public.scenarios s on s.id=ps.scenario_id where ps.phase_id="+q(p['id'])+" order by s.slug")
    if len(scenarios)!=1:problems.append(f'expected one hidden scenario, found {len(scenarios)}')
    for s in scenarios:
        if s['public']:problems.append(s['slug']+' has public weather, forecasts or events')
        if not s['bundle']:problems.append(s['slug']+' has no evaluation bundle')
        if int(s['links'])!=1:problems.append(s['slug']+' is linked to another phase')
    return {'phase':slug,'scenarios':[s['slug'] for s in scenarios],'ready':not problems,'problems':problems}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--prepare',action='store_true');mode.add_argument('--activate',action='store_true')
    parser.add_argument('--revision',help='Exact published website commit; required for activation')
    parser.add_argument('--scenarios',type=int,default=3,help='Expected number of formal scenarios (default 3)')
    parser.add_argument('--final-phase',default='final-hidden',help='Sealed hidden final phase to inspect')
    args=parser.parse_args()
    phases=deploy.query("select id,daily_limit from public.phases where slug='online' and is_active and counts_for_final")
    if len(phases)!=1:raise RuntimeError('Expected one existing competition phase')
    phase=phases[0]
    scenarios=deploy.query("select s.id,s.slug,s.global_wallclock_seconds,s.weather_public,s.forecasts_public,s.events_public "+
      "from public.phase_scenarios ps join public.scenarios s on s.id=ps.scenario_id where ps.phase_id="+q(phase['id'])+" order by s.slug")
    if len(scenarios)!=args.scenarios or any(s[k] for s in scenarios for k in ('weather_public','forecasts_public','events_public')):
        raise RuntimeError(f'Expected {args.scenarios} private competition scenarios')
    runtimes={s['global_wallclock_seconds'] for s in scenarios}
    if len(runtimes)!=1 or None in runtimes:raise RuntimeError('Scenario runtime metadata differs')
    preview=deploy.query('select scenario_id,model from private.observer_preparation_config where enabled')
    if len(preview)!=1:raise RuntimeError('No verified public preview configuration')
    result={'phase_id':phase['id'],'scenario_count':len(scenarios),'runtime_seconds':next(iter(runtimes)),
            'daily_batches':phase['daily_limit'],'mode':'prepare' if args.prepare else 'activate' if args.activate else 'inspect',
            'hidden_final':inspect_final(args.final_phase)}
    if args.prepare:result['bundles']=[prepare_bundle(s) for s in scenarios]
    if args.activate:
        if not args.revision or not re.fullmatch('[0-9a-f]{40}',args.revision):raise RuntimeError('A published frontend revision is required')
        request=urllib.request.Request('https://create.gosim.org/survey26/platform/deployment.json?revision='+args.revision,
                                      headers={'Cache-Control':'no-cache'})
        with urllib.request.urlopen(request,timeout=30) as response:release=json.load(response)
        if release.get('revision')!=args.revision:raise RuntimeError('The requested frontend revision is not live')
        deploy.query(activation_sql(phase['id'],preview[0]['scenario_id'],result['runtime_seconds'],phase['daily_limit'],preview[0]['model'],args.scenarios))
        result['activated']=True
    print(json.dumps(result))


if __name__=='__main__':main()
