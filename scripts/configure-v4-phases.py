#!/usr/bin/env python3
"""Switch the complete-project phases between the v3 scenarios and the v4 cards.

Organizer decisions 2026-09-28 (go/no-go for v4: 2026-10-02 12:00 UTC):
  practice-projects  v4 practice cards (alpha, beta), one board per card;
                     --practice-mode replace (default) unlinks the v3 Practice
                     scenarios from this phase, add keeps them next to the cards,
                     skip leaves the phase alone;
  online             v4 formal cards (A-D), one board per card plus the overall
                     board, 900 s per card run, 4 evaluations per team per day;
  final-hidden       v4 hidden cards (E-H), sealed until results are published,
                     900 s per card run, boards per card plus overall.
Card files in the public 'scenarios' bucket are released through
private.observer_scenario_public_files: practice cards at once (config/, public/,
truth/ when all their weather flags are public), formal cards' config/ and
public/ only while 'online' is open and the site is in competition mode, hidden
cards never. --reverse restores the previous list. Files that reveal the season's
weather and event timeline (bulletins, forecasts, truth tables: TIMELINE_FILES) are
never released for a formal card, nor for a practice card whose weather is not
fully public, whatever directory they are in; a formal card that has one in the
public bucket is refused.
Every phase that runs v4 cards is set to colocated=true (the v4 engine refuses
anything else: v4_requires_colocated); --reverse restores the previous value.

Forward (default): python scripts/configure-v4-phases.py \\
    --practice v4-practice-alpha,v4-practice-beta,v4-practice-gamma,v4-practice-delta --formal v4-a,v4-b,v4-c,v4-d --final v4-e,v4-f,v4-g,v4-h \\
    --preview v4-public-test [--apply]
Reverse:           python scripts/configure-v4-phases.py --reverse [--apply]
Status:            python scripts/configure-v4-phases.py --status

Dry run by default: prints the checks and the planned changes, writes nothing.
--apply performs everything in one transaction that first re-checks, under a
lock, that no evaluation job is queued, dispatched or claimed and no batch of the
touched phases is active; otherwise it refuses and changes nothing.

Before switching, the exact current configuration of the touched phases (their
scenario links, the links of every scenario that moves, phase and evaluation
settings, the preparation preview) is stored in
private.observer_phase_config_snapshots; --reverse restores it and marks the
snapshot restored. Scenarios taken out of 'online' or 'final-hidden' (the v3
formal scenarios and the v3 hidden final, or the v4 cards on reverse) are moved
to the sealed, inactive phase 'scenario-parking' so their names and files stay
private. Hidden final cards must be unused elsewhere, private and without files
in the public scenario bucket; the transaction verifies after the change that
every hidden or parked scenario is still unlisted and private, and aborts
otherwise. No scenario content is printed.

Uses the management credential from the environment (SUPABASE_PROJECT_REF,
SUPABASE_ACCESS_TOKEN), like the other organizer scripts.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('observer_deploy', ROOT/'scripts/deploy-observer-backend.py')
deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)
q = deploy.quote
query = deploy.query          # replaced by the local database tests

PRACTICE, FORMAL, FINAL, PARKING = 'practice-projects', 'online', 'final-hidden', 'scenario-parking'
SNAPSHOT_LABEL = 'before-v4'
ACTIVE_JOBS = "('queued','dispatched','claimed')"


def uuids(values):
    return 'array[' + ','.join(q(v) for v in values) + ']::uuid[]'


def slugs_arg(value):
    items = [s.strip() for s in (value or '').split(',') if s.strip()]
    if len(set(items)) != len(items): raise argparse.ArgumentTypeError('duplicate card slug')
    return items


def phase(slug):
    rows = query('select p.id,p.slug,p.is_active,p.counts_for_final,p.leaderboard_mode,p.daily_limit,'
                 'to_jsonb(s)-\'phase_id\' as settings from public.phases p left join public.observer_phase_settings s'
                 ' on s.phase_id=p.id where p.slug='+q(slug))
    if len(rows) != 1: raise RuntimeError(f'Expected one phase {slug}, found {len(rows)}')
    row = rows[0]
    if isinstance(row['settings'], str): row['settings'] = json.loads(row['settings'])
    row['scenarios'] = [r['slug'] for r in query('select s.slug from public.phase_scenarios ps join public.scenarios s'
                        ' on s.id=ps.scenario_id where ps.phase_id='+q(row['id'])+' order by s.slug')]
    return row


def scenarios(slugs):
    if not slugs: return {}
    rows = query('select s.id,s.slug,s.is_active,s.weather_public or s.forecasts_public or s.events_public as public_flags,'
                 's.weather_public and s.forecasts_public and s.events_public as all_public,s.contract,'
                 's.global_wallclock_seconds,exists(select 1 from private.observer_scenario_bundles b where b.scenario_id=s.id) as bundle,'
                 'public.observer_scenario_listed(s.id) as listed,'
                 "coalesce((select jsonb_agg(substr(o.name,length(s.slug)+2) order by o.name) from storage.objects o"
                 " where o.bucket_id='scenarios' and split_part(o.name,'/',1)=s.slug),'[]'::jsonb) as files,"
                 "coalesce((select jsonb_agg(jsonb_build_object('phase',p.slug,'sealed',coalesce(c.sealed,false)) order by p.slug)"
                 ' from public.phase_scenarios ps join public.phases p on p.id=ps.phase_id left join public.observer_phase_settings c'
                 " on c.phase_id=p.id where ps.scenario_id=s.id),'[]'::jsonb) as links"
                 ' from public.scenarios s where s.slug=any(array['+','.join(q(x) for x in slugs)+']::text[])')
    found = {r['slug']: r for r in rows}
    for r in rows:
        if isinstance(r['links'], str): r['links'] = json.loads(r['links'])
        if isinstance(r['files'], str): r['files'] = json.loads(r['files'])
    missing = [s for s in slugs if s not in found]
    if missing: raise RuntimeError('Unknown card scenarios: ' + ', '.join(missing))
    return found


REQUIRED_SCHEMA = {'private.observer_phase_config_snapshots': '20260928004100_card_boards',
                   'private.observer_scenario_public_files': '20260928004400_v4_public_card_files'}


def missing_migrations():
    rows = query('select ' + ','.join(f"to_regclass({q(t)}) is not null as \"{t}\"" for t in REQUIRED_SCHEMA))[0]
    return sorted({m for t, m in REQUIRED_SCHEMA.items() if not rows[t]})


def activity():
    missing = missing_migrations()
    state = query('select (select count(*) from private.observer_jobs where status in '+ACTIVE_JOBS+') as jobs,'
                  "(select count(*) from public.observer_batches b join public.phases p on p.id=b.phase_id where b.status in"
                  " ('queued','running') and p.slug in ("+','.join(map(q, (PRACTICE, FORMAL, FINAL)))+")) as batches")[0]
    state['open_snapshots'] = 0 if missing else query(
        'select count(*) as n from private.observer_phase_config_snapshots where restored_at is null')[0]['n']
    state['missing_migrations'] = missing
    return state


def guard_sql(phase_ids, forward):
    return f"""
select pg_advisory_xact_lock(hashtext('observer-v4-phase-switch'));
do $guard$ begin
  if exists(select 1 from private.observer_jobs where status in {ACTIVE_JOBS})
    then raise exception 'Evaluation jobs are queued, dispatched or claimed; refusing to switch phases';end if;
  if exists(select 1 from public.observer_batches where phase_id=any({uuids(phase_ids)}) and status in ('queued','running'))
    then raise exception 'An evaluation of a touched phase is still active; refusing to switch phases';end if;
  if {'' if forward else 'not '}exists(select 1 from private.observer_phase_config_snapshots where restored_at is null)
    then raise exception '{'Phases are already switched; run --reverse first' if forward else 'No switch to reverse'}';end if;
end $guard$;
"""


def privacy_sql(phase_slugs):
    """Every scenario of a sealed touched phase stays unlisted and private (checked inside the transaction)."""
    return f"""
do $privacy$ begin
  if exists(select 1 from public.phase_scenarios ps join public.phases p on p.id=ps.phase_id
      join public.observer_phase_settings c on c.phase_id=p.id join public.scenarios s on s.id=ps.scenario_id
      where p.slug=any(array[{','.join(map(q, phase_slugs))}]::text[]) and c.sealed and p.leaderboard_mode<>'published'
        and (public.observer_scenario_listed(s.id) or not public.observer_formal_source(s.slug)
             or exists(select 1 from public.phase_scenarios o where o.scenario_id=s.id and o.phase_id<>p.id)))
    then raise exception 'A hidden scenario would become visible; nothing was changed';end if;
  if exists(select 1 from private.observer_scenario_public_files f join public.phase_scenarios ps on ps.scenario_id=f.scenario_id
      join public.observer_phase_settings c on c.phase_id=ps.phase_id where c.sealed)
    then raise exception 'A file of a hidden scenario would be released; nothing was changed';end if;
  if exists(select 1 from public.phases p join public.observer_phase_settings c on c.phase_id=p.id
      where p.slug={q(FINAL)} and (not c.sealed or p.leaderboard_mode='published'))
    then raise exception 'The hidden final phase is not sealed; nothing was changed';end if;
end $privacy$;
"""


def parking_sql():
    pid = str(uuid.uuid4())
    return f"""
insert into public.phases(id,slug,name_en,name_zh,description_en,allow_results,allow_agents,leaderboard_mode,counts_for_final,is_active,sort_order)
  select {q(pid)},{q(PARKING)},'Parked scenarios','已停用场景','Scenarios kept private while not in use.',false,false,'hidden',false,false,20000
  where not exists(select 1 from public.phases where slug={q(PARKING)});
insert into public.observer_phase_settings(phase_id,projects_enabled,local_sessions_enabled,daily_batches,sealed)
  select id,false,false,1,true from public.phases where slug={q(PARKING)} on conflict(phase_id) do nothing;
do $parking$ begin
  if not exists(select 1 from public.phases p join public.observer_phase_settings c on c.phase_id=p.id
      where p.slug={q(PARKING)} and c.sealed and not p.is_active and not c.projects_enabled and not p.counts_for_final)
    then raise exception 'The parking phase exists but is not sealed and inactive';end if;
end $parking$;
"""


def park(scenario_ids):
    """Move scenarios to the sealed parking phase: remove every other link first (sealed exclusivity)."""
    if not scenario_ids: return ''
    return (f'delete from public.phase_scenarios where scenario_id=any({uuids(scenario_ids)});\n'
            f'insert into public.phase_scenarios(phase_id,scenario_id) select p.id,s from public.phases p,'
            f'unnest({uuids(scenario_ids)}) s where p.slug={q(PARKING)};\n')


def link(phase_id, scenario_ids, exclusive):
    """Link cards to a phase. Links to other sealed phases (staging) are removed; for a sealed target every other link."""
    if not scenario_ids: return ''
    other = '' if exclusive else ' and exists(select 1 from public.observer_phase_settings c where c.phase_id=ps.phase_id and c.sealed)'
    return (f'delete from public.phase_scenarios ps where ps.scenario_id=any({uuids(scenario_ids)}) and ps.phase_id<>{q(phase_id)}{other};\n'
            f'insert into public.phase_scenarios(phase_id,scenario_id) select {q(phase_id)},s from unnest({uuids(scenario_ids)}) s'
            ' on conflict do nothing;\n')


def expect_links(phase_id, scenario_ids):
    return (f"do $x$ begin if (select coalesce(array_agg(scenario_id order by scenario_id),'{{}}') from public.phase_scenarios"
            f" where phase_id={q(phase_id)}) is distinct from (select coalesce(array_agg(x order by x),'{{}}') from"
            f" unnest({uuids(scenario_ids)}) x) then raise exception 'Phase scenarios changed since the plan; run again';"
            " end if; end $x$;\n")


# Files from which the season's weather and event timeline can be read (the forecasts and
# bulletins issued ahead of each night, and every truth table). Denied by file name in any
# directory: a formal card never releases them, a practice card only when its weather,
# forecasts and events are all public.
TIMELINE_FILES = frozenset({
    'v4_bulletins.jsonl', 'v4_forecasts.jsonl', 'v4_weather_truth.csv', 'v4_events.csv', 'v4_slots.csv',
    'v4_earthquake_effects.csv', 'v4_stress_events.csv', 'bulletins.jsonl', 'forecasts.jsonl', 'weather.csv',
    'weather_truth.csv', 'events.csv'})
TIMELINE_MARKERS = ('bulletin', 'forecast', 'truth', 'event', 'weather', 'slots')


def timeline_file(path):
    name = path.rsplit('/', 1)[-1].lower()
    return name in TIMELINE_FILES or any(m in name for m in TIMELINE_MARKERS)


def released_files(cards, args, problems):
    """Files of the public 'scenarios' bucket that become downloadable (migration 20260928004400).
    Practice cards: config/ and public/, plus truth/ when weather, forecasts and events are all public.
    Formal cards: config/ and public/ only, released when the competition starts. Hidden cards: none.
    Timeline files (timeline_file) are denied unless the card's weather is fully public, i.e. never
    for a formal card."""
    out = {}
    for slug in args.practice if args.practice_mode != 'skip' else []:
        c = cards[slug]
        out[slug] = ('practice', [f for f in c['files'] if (f.split('/')[0] in ('config', 'public')
                                  or (f.split('/')[0] == 'truth' and c['all_public']))
                                  and (c['all_public'] or not timeline_file(f))])
    for slug in args.formal:
        files = cards[slug]['files']
        if any(f.split('/')[0] not in ('config', 'public') for f in files):
            problems.append(f'{slug} has files outside config/ and public/ in the public scenario bucket')
        if any(timeline_file(f) for f in files):
            problems.append(f'{slug} has weather or event timeline files in the public scenario bucket (never released)')
        out[slug] = ('competition', [f for f in files if f.split('/')[0] in ('config', 'public') and not timeline_file(f)])
    for slug, (_, files) in out.items():
        bad = [f for f in files if not re.fullmatch(r'(config|public|truth)/[A-Za-z0-9_.-]+', f) or '..' in f]
        if bad: problems.append(f'{slug} has unexpected file names in the public scenario bucket')
    return out


def plan_forward(args):
    problems, warnings = [], []
    phases = {slug: phase(slug) for slug in (PRACTICE, FORMAL, FINAL)}
    groups = {'practice': args.practice, 'formal': args.formal, 'final': args.final}
    all_cards = [s for g in groups.values() for s in g]
    if len(set(all_cards)) != len(all_cards): problems.append('a card is listed in more than one group')
    if not args.formal or not args.final: problems.append('--formal and --final cards are required')
    if args.practice_mode != 'skip' and not args.practice: problems.append('--practice cards are required unless --practice-mode skip')
    current = {slug: p['scenarios'] for slug, p in phases.items()}
    cards = scenarios(sorted(set(all_cards) | {s for v in current.values() for s in v} | ({args.preview} if args.preview else set())))
    for group, slugs in groups.items():
        for slug in slugs:
            c = cards[slug]
            if not c['is_active']: problems.append(f'{slug} is not active')
            if not c['bundle']: problems.append(f'{slug} has no evaluation bundle')
            if c['global_wallclock_seconds'] is None: warnings.append(f'{slug} has no wall-clock metadata')
            elif int(c['global_wallclock_seconds']) > args.runtime and group != 'practice':
                warnings.append(f"{slug} declares {c['global_wallclock_seconds']} s, more than the {args.runtime} s run cap")
            if group in ('formal', 'final') and c['listed']:
                warnings.append(f'{slug} is readable by participants now (not in a private phase)')
            if group in ('formal', 'final') and c['public_flags']: problems.append(f'{slug} has public weather, forecasts or events')
            if group == 'final':
                if c['files']: problems.append(f'{slug} has files in the public scenario bucket')
                if any(l['phase'] not in (FINAL,) and not l['sealed'] for l in c['links']):
                    problems.append(f'{slug} is linked to an unsealed phase')
            if group == 'formal' and any(l['phase'] == FINAL for l in c['links']): problems.append(f'{slug} belongs to the hidden final')
            if group == 'practice' and any(l['phase'] in (FORMAL, FINAL) for l in c['links']):
                problems.append(f'{slug} is formal material; refusing to use it for practice')
    released = released_files(cards, args, problems)
    final = phases[FINAL]
    if not (final['settings'] or {}).get('sealed'): problems.append('final-hidden is not sealed')
    if final['leaderboard_mode'] == 'published': problems.append('final-hidden results are already published')
    if not final['counts_for_final']: problems.append('final-hidden does not count for the final')
    if args.preview:
        # The public test of every new agent version runs a small dedicated public v4 card
        # (not alpha/beta and never formal or hidden material), within the 300 s preview cap.
        c = cards[args.preview]
        if args.preview in all_cards: problems.append('--preview must be a dedicated public test card, not a practice, formal or final card')
        if not c['is_active'] or not c['bundle']: problems.append(f'{args.preview} is not active or has no evaluation bundle')
        if not c['all_public']: problems.append(f'{args.preview} must have public weather, forecasts and events (a public test card)')
        if c['contract'] != 'v4-score-v1': problems.append(f"{args.preview} is not a v4 card (contract {c['contract']})")
        if any(l['phase'] in (FORMAL, FINAL) for l in c['links']): problems.append(f'{args.preview} is formal material')
        if c['global_wallclock_seconds'] is not None and int(c['global_wallclock_seconds']) > 300:
            warnings.append(f"{args.preview} declares {c['global_wallclock_seconds']} s; public tests are capped at 300 s")
    prep = query('select (select slug from public.scenarios where id=c.scenario_id) as scenario,'
                 '(select slug from public.phases where id=c.phase_id) as phase,c.enabled,'
                 'coalesce((select colocated from public.observer_phase_settings where phase_id=c.phase_id),false) as phase_colocated'
                 ' from private.observer_preparation_config c')
    if args.preview and prep:
        # The public test runs in the preparation config's phase; a v4 card runs only colocated.
        switched = {FORMAL, FINAL} | ({PRACTICE} if args.practice_mode != 'skip' else set())
        if prep[0]['phase'] not in switched and not prep[0]['phase_colocated']:
            problems.append(f"the public test runs in phase {prep[0]['phase']}, which is not colocated; a v4 preview card"
                            ' needs colocated=true there (v4_requires_colocated)')
    if not args.preview and prep and prep[0]['scenario'] not in all_cards:
        warnings.append(f"the preparation preview stays on {prep[0]['scenario']}; pass --preview <public test card> so the public"
                        ' test of new agent versions runs a v4 card')
    state = activity()
    if int(state['jobs']): problems.append(f"{state['jobs']} evaluation job(s) queued, dispatched or claimed")
    if int(state['batches']): problems.append(f"{state['batches']} evaluation(s) active in the touched phases")
    if int(state['open_snapshots']): problems.append('phases are already switched (an unrestored snapshot exists); run --reverse first')
    for m in state['missing_migrations']: problems.append(f'migration {m} is not applied')

    ids = lambda slugs: [cards[s]['id'] for s in slugs]
    target = {PRACTICE: (args.practice if args.practice_mode == 'replace' else
                         sorted(set(current[PRACTICE]) | set(args.practice)) if args.practice_mode == 'add' else current[PRACTICE]),
              FORMAL: args.formal, FINAL: args.final}
    parked = sorted({s for slug in (FORMAL, FINAL) for s in current[slug]} - set(all_cards))
    touched_phases = [phases[s]['id'] for s in (PRACTICE, FORMAL, FINAL)]
    moved = sorted({cards[s]['id'] for s in set(all_cards) | {x for v in current.values() for x in v}})
    changes = {
        PRACTICE: {'scenarios': target[PRACTICE], 'board_layout': 'cards' if args.practice_mode != 'skip' else
                   phases[PRACTICE]['settings'].get('board_layout', 'overall'),
                   'runtime_seconds': args.practice_runtime if args.practice_mode == 'replace' else phases[PRACTICE]['settings']['runtime_seconds'],
                   'colocated': True if args.practice_mode != 'skip' else phases[PRACTICE]['settings'].get('colocated')},
        FORMAL: {'scenarios': args.formal, 'board_layout': 'cards_overall', 'runtime_seconds': args.runtime,
                 'daily_batches': args.daily, 'daily_limit': args.daily, 'colocated': True},
        FINAL: {'scenarios': args.final, 'board_layout': 'cards_overall', 'runtime_seconds': args.runtime, 'sealed': True, 'colocated': True},
    }
    snapshot = f"""
insert into private.observer_phase_config_snapshots(label,snapshot) select {q(SNAPSHOT_LABEL)},jsonb_build_object(
  'phases',(select jsonb_agg(to_jsonb(p) order by p.slug) from public.phases p where p.id=any({uuids(touched_phases)})),
  'settings',(select jsonb_agg(to_jsonb(s) order by s.phase_id) from public.observer_phase_settings s where s.phase_id=any({uuids(touched_phases)})),
  'links',(select coalesce(jsonb_agg(to_jsonb(ps) order by ps.phase_id,ps.scenario_id),'[]') from public.phase_scenarios ps
           where ps.phase_id=any({uuids(touched_phases)}) or ps.scenario_id=any({uuids(moved)})),
  'scenarios',to_jsonb({uuids(moved)}),
  'preparation',(select to_jsonb(c) from private.observer_preparation_config c where c.id),
  'public_files',(select coalesce(jsonb_agg(to_jsonb(f) order by f.scenario_id,f.path),'[]') from private.observer_scenario_public_files f
                  where f.scenario_id=any({uuids(moved)})));
"""
    body = [guard_sql(touched_phases, True)] + [expect_links(phases[s]['id'], ids(current[s])) for s in (PRACTICE, FORMAL, FINAL)]
    body += [snapshot, parking_sql(), park(ids(parked))]
    p_id, f_id, h_id = (phases[s]['id'] for s in (PRACTICE, FORMAL, FINAL))
    if args.practice_mode == 'replace':
        body.append(f"delete from public.phase_scenarios where phase_id={q(p_id)} and not (scenario_id=any({uuids(ids(args.practice))}));\n")
    if args.practice_mode != 'skip':
        body.append(link(p_id, ids(args.practice), False))
        body.append(f"update public.observer_phase_settings set board_layout='cards',colocated=true,runtime_seconds={int(changes[PRACTICE]['runtime_seconds'])}"
                    f" where phase_id={q(p_id)};\n")
    body.append(f"delete from public.phase_scenarios where phase_id={q(f_id)} and not (scenario_id=any({uuids(ids(args.formal))}));\n")
    body.append(link(f_id, ids(args.formal), False))
    body.append(f"update public.observer_phase_settings set board_layout='cards_overall',colocated=true,runtime_seconds={int(args.runtime)},"
                f"daily_batches={int(args.daily)} where phase_id={q(f_id)};\n")
    body.append(f"update public.phases set daily_limit={int(args.daily)} where id={q(f_id)};\n")
    body.append(f"delete from public.phase_scenarios where phase_id={q(h_id)} and not (scenario_id=any({uuids(ids(args.final))}));\n")
    body.append(link(h_id, ids(args.final), True))
    body.append(f"update public.observer_phase_settings set board_layout='cards_overall',colocated=true,runtime_seconds={int(args.runtime)}"
                f" where phase_id={q(h_id)} and sealed;\n")
    body.append(f"delete from private.observer_scenario_public_files where scenario_id=any({uuids(moved)});\n")
    for slug, (release, files) in released.items():
        if files:
            body.append('insert into private.observer_scenario_public_files(scenario_id,path,release) values'
                        + ','.join(f"({q(cards[slug]['id'])},{q(f)},{q(release)})" for f in files) + ';\n')
    # In-transaction backstop: no formal card file from which the weather timeline can be read is ever released.
    body.append("do $timeline$ begin if exists(select 1 from private.observer_scenario_public_files"
                f" where release='competition' and lower(path) ~ {q('(' + '|'.join(TIMELINE_MARKERS) + ')')})"
                " then raise exception 'A formal card would release a weather or event timeline file'; end if;"
                " end $timeline$;\n")
    if args.preview:
        body.append(f"update private.observer_preparation_config set scenario_id={q(cards[args.preview]['id'])} where id;\n")
    for slug in (PRACTICE, FORMAL, FINAL):
        body.append(expect_links(phases[slug]['id'], ids(target[slug])))
    body.append(f"""do $bundles$ begin
  if exists(select 1 from public.phase_scenarios ps where ps.phase_id=any({uuids([f_id, h_id])})
      and not exists(select 1 from private.observer_scenario_bundles b where b.scenario_id=ps.scenario_id))
    then raise exception 'A formal or hidden card has no evaluation bundle';end if;
end $bundles$;
""")
    colocated_phases = [f_id, h_id] + ([p_id] if args.practice_mode != 'skip' else [])
    body.append(f"""do $colocated$ begin
  if exists(select 1 from public.observer_phase_settings where phase_id=any({uuids(colocated_phases)}) and not colocated)
    then raise exception 'A phase with v4 cards is not colocated';end if;
end $colocated$;
""")
    body.append(privacy_sql([FINAL, PARKING]))
    body.append(f"select private.audit('observer.v4_phases_switched',{q(json.dumps({'practice': target[PRACTICE], 'formal': args.formal, 'final_count': len(args.final), 'runtime_seconds': args.runtime, 'daily_batches': args.daily}))}::jsonb);\n")
    sql = 'begin;\n' + ''.join(body) + 'commit;\n'
    return {'mode': 'forward', 'problems': problems, 'warnings': warnings,
            'current': {s: {'scenarios': current[s], 'board_layout': (phases[s]['settings'] or {}).get('board_layout'),
                            'runtime_seconds': (phases[s]['settings'] or {}).get('runtime_seconds'),
                            'daily_batches': (phases[s]['settings'] or {}).get('daily_batches'),
                            'colocated': (phases[s]['settings'] or {}).get('colocated')} for s in phases},
            'planned': changes, 'parked': parked,
            'released_files': {slug: {'release': r, 'files': f} for slug, (r, f) in released.items()},
            'preview': args.preview or (prep[0]['scenario'] if prep else None)}, sql


def plan_reverse():
    problems = [f'migration {m} is not applied' for m in missing_migrations()]
    if problems: return {'mode': 'reverse', 'problems': problems}, None
    snap = query('select id,label,taken_at,snapshot from private.observer_phase_config_snapshots where restored_at is null order by id desc')
    if len(snap) != 1: problems.append('no unrestored snapshot to restore' if not snap else 'more than one unrestored snapshot')
    state = activity()
    if int(state['jobs']): problems.append(f"{state['jobs']} evaluation job(s) queued, dispatched or claimed")
    if int(state['batches']): problems.append(f"{state['batches']} evaluation(s) active in the touched phases")
    if problems: return {'mode': 'reverse', 'problems': problems}, None
    row = snap[0]; data = row['snapshot'] if isinstance(row['snapshot'], dict) else json.loads(row['snapshot'])
    sid = int(row['id'])
    phase_ids = [p['id'] for p in data['phases']]
    names = {r['id']: r['slug'] for r in query('select id,slug from public.scenarios where id=any('+uuids(
        {l['scenario_id'] for l in data['links']} | set(data['scenarios']))+')')}
    restore = {p['slug']: sorted(names.get(l['scenario_id'], l['scenario_id']) for l in data['links'] if l['phase_id'] == p['id'])
               for p in data['phases']}
    snapshot = f'(select snapshot from private.observer_phase_config_snapshots where id={sid})'
    sql = 'begin;\n' + guard_sql(phase_ids, False) + parking_sql() + f"""
create temporary table v4_restore_scenarios on commit drop as
  select distinct x as scenario_id from (
    select (jsonb_array_elements_text({snapshot}->'scenarios'))::uuid x
    union select (l->>'scenario_id')::uuid from jsonb_array_elements({snapshot}->'links') l
    union select scenario_id from public.phase_scenarios where phase_id=any({uuids(phase_ids)})
    union select ps.scenario_id from public.phase_scenarios ps join public.phases p on p.id=ps.phase_id where p.slug={q(PARKING)}) s;
delete from public.phase_scenarios where phase_id=any({uuids(phase_ids)})
  or phase_id=(select id from public.phases where slug={q(PARKING)})
  or scenario_id in (select scenario_id from v4_restore_scenarios);
insert into public.phase_scenarios(phase_id,scenario_id)
  select r.phase_id,r.scenario_id from jsonb_populate_recordset(null::public.phase_scenarios,{snapshot}->'links') r;
insert into public.phase_scenarios(phase_id,scenario_id)
  select p.id,v.scenario_id from v4_restore_scenarios v,public.phases p where p.slug={q(PARKING)}
    and not exists(select 1 from public.phase_scenarios ps where ps.scenario_id=v.scenario_id);
update public.observer_phase_settings s set runtime_seconds=r.runtime_seconds,daily_batches=r.daily_batches,
    board_layout=coalesce(r.board_layout,'overall'),sealed=r.sealed,colocated=r.colocated
  from jsonb_populate_recordset(null::public.observer_phase_settings,{snapshot}->'settings') r where s.phase_id=r.phase_id;
update public.phases p set daily_limit=r.daily_limit
  from jsonb_populate_recordset(null::public.phases,{snapshot}->'phases') r where p.id=r.id;
update private.observer_preparation_config c set phase_id=r.phase_id,scenario_id=r.scenario_id,model=r.model,enabled=r.enabled
  from jsonb_populate_record(null::private.observer_preparation_config,{snapshot}->'preparation') r where c.id=r.id;
delete from private.observer_scenario_public_files where scenario_id in (select scenario_id from v4_restore_scenarios);
insert into private.observer_scenario_public_files(scenario_id,path,release)
  select r.scenario_id,r.path,r.release
  from jsonb_populate_recordset(null::private.observer_scenario_public_files,coalesce({snapshot}->'public_files','[]')) r;
update private.observer_phase_config_snapshots set restored_at=now() where id={sid};
""" + privacy_sql([FINAL, PARKING]) + \
        f"select private.audit('observer.v4_phases_reversed',jsonb_build_object('snapshot',{sid}));\n" + 'commit;\n'
    return {'mode': 'reverse', 'problems': [], 'snapshot': sid, 'taken_at': str(row['taken_at']), 'restore': restore}, sql


def status():
    phases = {slug: phase(slug) for slug in (PRACTICE, FORMAL, FINAL)}
    out = {slug: {'scenarios': p['scenarios'] if slug != FINAL else f"{len(p['scenarios'])} (sealed, not printed)",
                  'board_layout': (p['settings'] or {}).get('board_layout'), 'runtime_seconds': (p['settings'] or {}).get('runtime_seconds'),
                  'daily_batches': (p['settings'] or {}).get('daily_batches'), 'daily_limit': p['daily_limit'],
                  'colocated': (p['settings'] or {}).get('colocated')}
           for slug, p in phases.items()}
    out['activity'] = activity()
    out['preview'] = query('select (select slug from public.scenarios where id=c.scenario_id) as scenario from private.observer_preparation_config c')
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--reverse', action='store_true', help='Restore the configuration saved before the switch')
    mode.add_argument('--status', action='store_true', help='Print the current configuration of the three phases')
    parser.add_argument('--practice', type=slugs_arg, default=[], help='Practice card slugs, comma separated (alpha,beta)')
    parser.add_argument('--formal', type=slugs_arg, default=[], help='Formal card slugs for online (A-D)')
    parser.add_argument('--final', type=slugs_arg, default=[], help='Hidden final card slugs (E-H)')
    parser.add_argument('--practice-mode', choices=('replace', 'add', 'skip'), default='replace',
                        help='replace: the practice board becomes the practice cards; add: keep the v3 scenarios too')
    parser.add_argument('--runtime', type=int, default=900, help='Wall clock per card run in online and final-hidden (s)')
    parser.add_argument('--practice-runtime', type=int, default=900, help='Wall clock per run in practice-projects (replace mode)')
    parser.add_argument('--daily', type=int, default=4, help='Evaluations per team per day in online')
    parser.add_argument('--preview', help='Dedicated public v4 test card (e.g. v4-public-test) used for the public test'
                        ' when a new agent version is prepared')
    parser.add_argument('--apply', action='store_true', help='Write the change (default: dry run)')
    parser.add_argument('--sql', action='store_true', help='Also print the transaction (dry run)')
    args = parser.parse_args(argv)
    if not 10 <= args.runtime <= 18000 or not 10 <= args.practice_runtime <= 18000 or not 1 <= args.daily <= 100:
        parser.error('runtime must be 10..18000 s and daily 1..100')
    if args.status:
        print(json.dumps(status(), indent=1, default=str)); return 0
    result, sql = plan_reverse() if args.reverse else plan_forward(args)
    if sql: result['transaction_sha256'] = hashlib.sha256(sql.encode()).hexdigest()
    if result['problems']:
        result['applied'] = False
        print(json.dumps(result, indent=1, default=str)); return 2
    if args.apply:
        query(sql)
        result['applied'] = True
        result['after'] = status()
    else:
        result['applied'] = False
        if args.sql: result['sql'] = sql
    print(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == '__main__': sys.exit(main())
