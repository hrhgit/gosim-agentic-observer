#!/usr/bin/env python3
"""Open Practice complete-project board next to CSV practice.

Creates (or verifies) the phase 'practice-projects': projects only, 5 evaluations
per team per day, the team's own model key only (migration 20260926000400), and
private engine bundles built from Practice's own scenarios. Formal
competition scenarios are never touched. Dry run by default; --apply writes.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('observer_competition', ROOT/'scripts/configure-observer-competition.py')
competition = importlib.util.module_from_spec(spec); spec.loader.exec_module(competition)
deploy = competition.deploy; q = deploy.quote
SLUG = 'practice-projects'


def plan(args):
    practice = deploy.query("select id,leaderboard_mode,sort_order,ends_at from public.phases where slug='practice' and is_active")
    if len(practice) != 1: raise RuntimeError('Expected one active practice phase')
    practice = practice[0]
    scenarios = deploy.query('select s.id,s.slug,s.global_wallclock_seconds from public.phase_scenarios ps join public.scenarios s'
                             ' on s.id=ps.scenario_id where ps.phase_id='+q(practice['id'])+' and s.is_active order by s.slug')
    if args.scenario: scenarios = [s for s in scenarios if s['slug'] in args.scenario]
    if not scenarios: raise RuntimeError('No Practice scenarios selected')
    formal = {r['scenario_id'] for r in deploy.query("select ps.scenario_id from public.phase_scenarios ps join public.phases p"
                                                     " on p.id=ps.phase_id where p.slug='online' or p.counts_for_final")}
    if any(s['id'] in formal for s in scenarios): raise RuntimeError('A selected scenario is formal material; refusing')
    runtimes = {s['global_wallclock_seconds'] for s in scenarios}
    if None in runtimes: raise RuntimeError('A selected scenario has no runtime')
    # Practice scenarios differ in length; the phase cap is the longest one
    # (each run still ends when its own scenario ends).
    existing = deploy.query('select id from public.phases where slug='+q(SLUG))
    phase_id = existing[0]['id'] if existing else str(uuid.uuid4())
    # Complete projects talk to the platform once per step over the network, so
    # they get more time than a local run: 5 hours (organizer decision 2026-09-26).
    runtime = max(args.runtime, max(int(r) for r in runtimes))
    statements = [
        'insert into public.phases(id,slug,name_en,name_zh,allow_results,allow_agents,leaderboard_mode,counts_for_final,is_active,sort_order,starts_at,ends_at)'
        ' values ('+','.join(map(q, (phase_id, SLUG, 'Practice board', '练习赛榜', False, False,
                                      practice['leaderboard_mode'], False, True, int(practice['sort_order'])+1)))
        +',now(),'+(q(practice['ends_at']) if practice['ends_at'] else 'null')+') on conflict(id) do update set is_active=true',
        'insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,runtime_seconds,daily_batches,'
        'model_token_limit,model_call_limit,model_concurrency) values ('+','.join(map(q, (phase_id, True, False, runtime, args.daily,
                                                                                           competition.MODEL_TOKEN_LIMIT,
                                                                                           competition.MODEL_CALL_LIMIT,
                                                                                           competition.MODEL_CONCURRENCY)))+')'
        ' on conflict(phase_id) do update set projects_enabled=true,local_sessions_enabled=false,daily_batches=excluded.daily_batches,'
        'model_token_limit=excluded.model_token_limit,model_call_limit=excluded.model_call_limit,model_concurrency=excluded.model_concurrency',
    ] + ['insert into public.phase_scenarios(phase_id,scenario_id) values ('+q(phase_id)+','+q(s['id'])+') on conflict do nothing'
         for s in scenarios]
    return {'phase_id': phase_id, 'is_new': not existing, 'runtime_seconds': runtime, 'daily_batches': args.daily,
            'scenarios': [s['slug'] for s in scenarios], 'statements': statements}, scenarios


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=int, default=18000, help='Phase time limit in seconds (max 18000)')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--daily', type=int, default=8)
    parser.add_argument('--scenario', action='append', help='Practice scenario slug (repeatable); default: all')
    args = parser.parse_args()
    result, scenarios = plan(args)
    if args.apply:
        # Practice scenarios ship without hidden anomaly tags; the engine treats that file as optional.
        practice_files = competition.FORMAL_FILES - {'outputs/reference/tile_anomalies.csv'}
        result['bundles'] = [competition.prepare_bundle(s, practice_files) for s in scenarios]
        for statement in result['statements']: deploy.query(statement)
        result['current_competition'] = deploy.query('select public.current_competition() as value')[0]['value']
    result.pop('statements') if args.apply else None
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__': main()
