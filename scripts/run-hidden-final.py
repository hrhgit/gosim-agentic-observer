#!/usr/bin/env python3
"""Evaluate every team's final version repeat_runs times (default 3) on the hidden final phase.

Run after the formal phase ('online') has ended. For each team with a
final version in that phase (its choice, else the version of its best scored
evaluation) this creates observer_phase_settings.repeat_runs formal evaluations of
it in the sealed hidden phase ('final-hidden'), outside the daily limit: each with
one run per card linked to that phase (the v4 cards E-H). The normal dispatcher
starts a team's repeats of each card together (migration 20261004120000): repeats
run one after another could carry what a program saw of a hidden card into the
next repeat. Any rerun (--retry-failed) replaces the team's whole set, so a card's
repeats always overlap in time; --results checks it and never ranks a set whose
repeats did not overlap (status not_concurrent).
Results stay invisible to participants until the hidden phase's
leaderboard_mode is set to 'published'. Operating procedure: docs/hidden-final-runbook.md.

Dry run by default: prints what would be created, the run settings of the target
phase, an estimate of runner minutes and duration, the minutes left in every runner
organization and whether the public-repository runner pool takes the runs (a sealed
phase prefers it, without Actions minutes, once the sealed transfer is verified). --apply creates the
evaluations in one transaction (private.observer_run_hidden_final, migrations
20260927000800, 20261001000200, 20261001000900 and 20261004030000). A team that
already has repeat_runs evaluations there (scored or still running) on the current
card set is skipped. A card the team itself fails (build, crash or protocol failure
of its project, or a rejected trace) scores 0 in that evaluation and the other
cards still run. A failure caused by the platform fails that evaluation, which
--retry-failed replaces. Recreating an evaluation for any other reason is an organizer
decision (--retry-participant-failures, only for an evaluation that failed without
a platform cause). --team limits the run to one team (slug, name or id); --before-freeze allows that single-team test before
the public phase has ended. --limit N creates the evaluations of at most N teams per call (a
canary first, then the rest with a second --apply); the dispatcher schedules
queued runs in creation order anyway, so no further batching is needed. v4 task
cards run only colocated, so the target phase needs colocated=true.

--status prints the progress of the hidden evaluations (batches, runs, runner
minutes so far). --results prints the organizer ranking: per team the mean score on
every card over its first repeat_runs scored evaluations and the mean over the cards,
with the range of the evaluations' scores, ranked over teams that are not hidden
and have all evaluations scored; a card the team itself failed counts as 0, marked *.
--csv PATH also writes it to a local file. Neither prints card contents,
and neither changes anything. After the runs, scripts/verify-v4-run.py replays any
v4 result against its bundle.

Uses the management credential from the environment (SUPABASE_PROJECT_REF,
SUPABASE_ACCESS_TOKEN), like the other organizer scripts.
"""
import argparse
import csv
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('observer_deploy', ROOT/'scripts/deploy-observer-backend.py')
deploy = importlib.util.module_from_spec(spec); spec.loader.exec_module(deploy)
q = deploy.quote

# The dispatcher is called once a minute (private.observer_tick) and schedules at
# most five runs per call (supabase/functions/_shared/observer-orchestrate.ts).
SCHEDULED_RUNS_PER_MINUTE = 5
# Runner time on top of the card's runtime: job start, project image, scoring, upload.
OVERHEAD_MINUTES = 3
# Slowest GitHub-hosted runner measured for the fair clock (docs/fair-clock.md): factor about 1.2.
SLOWEST_RUNNER_FACTOR = 1.25


def one(rows, what):
    if len(rows) != 1: raise RuntimeError(f'Expected exactly one {what}, found {len(rows)}')
    return rows[0]


def jsonish(value):
    return json.loads(value) if isinstance(value, str) else value


def plan_sql(source, target, team, apply, before_freeze, retry_failed, retry_participant=False, limit=None):
    return ('select private.observer_run_hidden_final(' + ','.join((
        q(source)+'::uuid', q(target)+'::uuid', (q(team)+'::uuid') if team else 'null',
        'true' if apply else 'false', 'true' if before_freeze else 'false', 'true' if retry_failed else 'false',
        'true' if retry_participant else 'false', str(int(limit)) if limit is not None else 'null'))
        + ') as result')


def summarize(result):
    teams = result['teams']
    lines = [f"{'APPLIED' if result['apply'] else 'DRY RUN'}: {result['source_phase']} -> {result['target_phase']}"
             f" (source ends {result['source_ends_at']}, frozen={result['frozen']},"
             f" {result.get('runs', 1)} evaluation(s) per team)"]
    for t in teams:
        note = t['reason'] or ''
        if t['action'] == 'deferred':
            note = 'over --limit: created by a later --apply'
        elif t['action'] != 'skip' and t['model_mode'] == 'relay':
            note = ('relay model mode: model calls will fail unless a team page stays open during the run'
                    ' (rule: switch to stored mode before the online phase ends)')
        elif t['action'] != 'skip' and t['model_mode'] == 'stored' and not t['model_key_saved']:
            note = 'stored model mode without a saved key: model calls will fail'
        if t.get('failure') and t['action'] in ('created', 'would_create'):
            note = f"retry after {t['failure']} failure" + ('; ' + note if note else '')
        if t['action'] in ('created', 'would_create', 'deferred') and t.get('new_evaluations') not in (None, result.get('runs', 1)):
            note += f"{'; ' if note else ''}{t['new_evaluations']} more evaluation(s), {t['evaluations']} already scored or running"
        if t.get('stale_batches'):
            note += f"{'; ' if note else ''}{t['stale_batches']} batch(es) on an earlier card set ignored"
        lines.append(f"  {t['action']:<12} {t['team_name'][:40]:<40} version={t['revision_id']} ({t['source']})"
                     f" best={t['best_score']}{' hidden-team' if t['team_hidden'] else ''}{'  ! '+note if note else ''}")
    counts = {}
    for t in teams:
        key = t['action'] if t['action'] != 'skip' else 'skip:' + (t['reason'] or '')
        counts[key] = counts.get(key, 0) + 1
    lines.append('  totals: ' + ', '.join(f'{k}={v}' for k, v in sorted(counts.items())) + f"; batches created now: {result['created']}")
    return '\n'.join(lines)


def target_settings(target):
    """Batch shape and runtime of the sealed phase; v4 cards need a colocated phase."""
    row = one(deploy.query('select coalesce(c.colocated,false) as colocated, c.runtime_seconds,'
                           ' (select count(*) from public.phase_scenarios where phase_id='+q(target)+'::uuid) as scenarios'
                           ' from (select 1) x left join public.observer_phase_settings c on c.phase_id='+q(target)+'::uuid'),
              'target settings')
    line = (f"  target phase: {row['scenarios']} scenario(s) per batch, runtime_seconds={row['runtime_seconds']},"
            f" colocated={str(row['colocated']).lower()}")
    if not row['colocated']:
        line += ('\n  ! v4 task cards run only colocated: set observer_phase_settings.colocated=true first,'
                 ' or every v4 run fails with v4_requires_colocated')
    return line, row


def runner_capacity(user_ids):
    """Minutes left this month per enabled runner organization and the organization of each batch user.

    Jobs go to the organization recorded for the batch's user (observer_placement); an
    organization over its limit keeps receiving its placed users' jobs, and GitHub does
    not start jobs once its minutes are used up. Returns None if the fleet functions are missing.
    """
    rows = deploy.query("select to_regprocedure('public.observer_organizations_by_load()') is not null as present")
    if not rows or not rows[0]['present']: return None
    fleet = deploy.query('select organization, monthly_minute_limit, month_minutes from public.observer_organizations_by_load()')
    left = {r['organization']: max(0.0, float(r['monthly_minute_limit']) - float(r['month_minutes'])) for r in fleet}
    limit = {r['organization']: float(r['monthly_minute_limit']) for r in fleet}
    placed = {}
    if user_ids:
        placed = {str(r['user_id']): r['organization'] for r in deploy.query(
            'select user_id, organization from private.observer_placements where user_id=any(array['
            + ','.join(q(u) for u in user_ids) + ']::uuid[])')}
    return {'left': left, 'limit': limit, 'placed': placed}


def public_pool(target):
    """Whether the public-repository runner pool (ops/public-runner-pool.md) takes the target phase's runs.

    In overflow mode a sealed phase switched on for the pool prefers it once the sealed transfer is
    verified (migration 20261004050000): up to max_active runs at a time, no Actions minutes, within
    the pool's monthly cap. Returns None if the pool functions are missing.
    """
    rows = deploy.query("select to_regprocedure('public.observer_public_pool()') is not null as present")
    if not rows or not rows[0]['present']: return None
    pool = jsonish(one(deploy.query('select public.observer_public_pool() as pool'), 'pool')['pool'])
    phase = one(deploy.query('select ph.slug, coalesce(c.sealed,false) as sealed from public.phases ph'
                             ' left join public.observer_phase_settings c on c.phase_id=ph.id where ph.id='
                             + q(target) + '::uuid'), 'target phase')
    reason = None
    if pool.get('mode', 'off') == 'off': reason = 'switched off'
    elif pool['mode'] not in ('overflow', 'primary'): reason = f"mode {pool['mode']} (drill users only)"
    elif phase['slug'] not in (pool.get('phases') or []): reason = f"{phase['slug']} not switched on for the pool"
    elif phase['sealed'] and not pool.get('sealed_transfer_verified'): reason = 'sealed transfer not verified'
    elif not phase['sealed']: reason = 'unsealed phase: only while an organization is busy or near its minutes'
    cap_left = max(0.0, float(pool.get('monthly_minute_cap') or 0) - float(pool.get('month_minutes') or 0))
    # One public repository per runner organization (migration 20261004061000): the healthy ones' slots, within the global cap.
    targets = pool.get('targets')
    slots = int(pool.get('max_active') or 0)
    if isinstance(targets, list):
        slots = min(slots, sum(int(t.get('max_active') or 0) for t in targets if t.get('healthy')))
        if reason is None and not slots: reason = 'no healthy public repository'
    return {'used': reason is None, 'reason': reason, 'max_active': slots,
            'cap_left': cap_left, 'cap': float(pool.get('monthly_minute_cap') or 0),
            'where': (f"{sum(1 for t in targets if t.get('healthy'))} public repositories" if isinstance(targets, list)
                      else f"{pool.get('organization', '?')}/{pool.get('repository', 'observer-public')}")}


def estimate(users, scenarios, runtime_seconds, capacity, pool=None):
    """Runner minutes and wall-clock time for new batches run by the given users (one entry per batch)."""
    runs = len(users) * int(scenarios or 0)
    if not runs: return '  estimate: nothing to run'
    # Fair clock (challenge/fair_clock.py): the budget is normalized agent time, so a full run
    # takes up to SLOWEST_RUNNER_FACTOR x the budget in real time (plus a little engine time).
    per_run = (runtime_seconds or 0) * SLOWEST_RUNNER_FACTOR / 60 + OVERHEAD_MINUTES
    minutes = math.ceil(runs * per_run)
    hours = (runs / SCHEDULED_RUNS_PER_MINUTE + per_run) / 60
    lines = [f'  estimate: {runs} runs, up to ~{minutes} runner minutes ({per_run:.0f} min per run at most),'
             f' about {hours:.1f} h until the last run finishes ({SCHEDULED_RUNS_PER_MINUTE} runs scheduled per minute)']
    # The share the public pool can take: its free slots over the whole run, within its monthly cap.
    share = 0.0
    if pool is not None:
        if pool['used']:
            share = min(float(minutes), pool['cap_left'], pool['max_active'] * hours * 60)
            lines.append(f"  public pool: preferred for this phase (sealed transfer verified), {pool['where']}:"
                         f" up to {pool['max_active']} runs at a time, no Actions minutes,"
                         f" {pool['cap_left']:.0f} of {pool['cap']:.0f} pool minutes left this month;"
                         f" takes up to ~{share:.0f} of the ~{minutes} minutes, the rest runs on the organizations below")
            if share < minutes and pool['max_active'] < 15 and pool['where'].endswith('observer-public'):
                lines.append("  public pool: for a larger share raise max_active before --apply, e.g."
                             " select public.observer_set_public_pool(p_max_active=>15); (at most 20, shared with"
                             " the pool organization's own jobs; ops/public-runner-pool.md)")
        else:
            lines.append(f"  public pool: not used for this phase ({pool['reason']}); every run uses Actions minutes")
    if capacity is None: return '\n'.join(lines)
    left = capacity['left']
    private = max(0.0, minutes - share)
    lines.append(f'  runner capacity: {sum(left.values()):.0f} minutes left this month on {len(left)} enabled organization(s)')
    need, unplaced = {}, 0
    for user in users:
        organization = capacity['placed'].get(str(user))
        if organization is None: unplaced += 1
        else: need[organization] = need.get(organization, 0) + int(scenarios) * per_run * (private / minutes)
    for organization in sorted(left, key=lambda o: (len(o), o)):
        used = capacity['limit'].get(organization, 0.0) - left[organization]
        lines.append(f'    {organization}: {left[organization]:.0f} left ({used:.0f} of {capacity["limit"].get(organization, 0.0):.0f} used)'
                     + (f', its placed teams need up to ~{need[organization]:.0f}' if organization in need else ''))
    short = sorted(o for o, n in need.items() if n > left.get(o, 0.0))
    for organization in short:
        lines.append(f'  ! {organization}: needs up to ~{need[organization]:.0f} min for its placed teams,'
                     f' {left.get(organization, 0.0):.0f} left (disabled or over its limit: jobs there may not start)')
    if unplaced:
        lines.append(f'  {unplaced} batch user(s) without a placement: assigned to the least loaded organization when scheduled')
    if short or sum(left.values()) < private:
        lines.append('  ! not enough runner minutes: enable more runner organizations, raise monthly_minute_limit'
                     ' or move placements (scripts/rebalance-observer-placements.py) first'
                     + ('' if pool is None or pool['used'] else '; or use the public pool (ops/public-runner-pool.md)'))
    return '\n'.join(lines)


# How a failed hidden evaluation is classified (as in private.observer_run_hidden_final): 'participant' only
# when every failed or cancelled card is the team's own failure, else 'platform'.
FAILURE = """case when exists(select 1 from public.observer_runs r where r.batch_id={batch}
    and private.observer_participant_failure(r.id))
  and not exists(select 1 from public.observer_runs r where r.batch_id={batch}
    and r.status in ('failed','cancelled') and not private.observer_participant_failure(r.id))
  then 'participant' else 'platform' end"""

# Every evaluation on the phase's current card set that was not replaced by a new set, oldest first.
CURRENT_BATCHES = """
with cur as (
  select b.* from public.observer_batches b
  where b.phase_id={target}::uuid and b.purpose='formal' and b.superseded_at is null
    and private.observer_batch_covers_phase(b.id,b.phase_id))
"""


def repeat_runs(target):
    """How many evaluations per team the hidden final averages (observer_phase_settings.repeat_runs)."""
    rows = deploy.query('select greatest(1,coalesce(repeat_runs,1)) as n from public.observer_phase_settings'
                        ' where phase_id=' + q(target) + '::uuid')
    return int(rows[0]['n']) if rows else 1


def status(target):
    """Progress of the hidden evaluations; no scores."""
    cte = CURRENT_BATCHES.format(target=q(target))
    batches = deploy.query(cte + 'select status, count(*) as n from cur group by status order by status')
    teams = deploy.query(cte + "select count(*) filter (where scored>=" + str(repeat_runs(target)) + ") as complete, count(*) as n"
                         " from (select team_id, count(*) filter (where status='scored') as scored from cur group by team_id) x")
    runs = deploy.query(cte + 'select r.status, count(*) as n from public.observer_runs r join cur on cur.id=r.batch_id'
                        ' group by r.status order by r.status')
    jobs = one(deploy.query(cte + """select count(*) filter (where j.status in ('queued','dispatched','claimed')) as active,
        coalesce(sum(extract(epoch from j.finished_at-j.claimed_at))/60,0)::float as minutes,
        coalesce(avg(extract(epoch from j.finished_at-j.claimed_at))/60,0)::float as avg_minutes,
        count(j.finished_at) as finished
      from public.observer_runs r join cur on cur.id=r.batch_id
      join private.observer_jobs j on j.run_id=r.id and j.kind='engine'"""), 'job totals')
    failures = deploy.query(cte + 'select ' + FAILURE.format(batch='cur.id') + """ as failure, count(*) as n
      from cur where cur.status in ('failed','cancelled') group by 1 order by 1""")
    fmt = lambda rows: ', '.join(f"{r['status' if 'status' in r else 'failure']}={r['n']}" for r in rows) or 'none'
    # Runs still queued in a failed batch are never scheduled; count only live batches.
    waiting = one(deploy.query(cte + "select count(*) as n from public.observer_runs r join cur on cur.id=r.batch_id"
                               " where r.status='queued' and cur.status in ('queued','running')"), 'queued runs')['n']
    t = teams[0] if teams else {'complete': 0, 'n': 0}
    return '\n'.join([
        f'STATUS: batches {fmt(batches)}',
        f"  teams with all {repeat_runs(target)} evaluations scored: {t['complete']} of {t['n']}",
        f'  runs: {fmt(runs)}',
        f'  failed batches: {fmt(failures)} (replace platform failures with --apply --retry-failed)',
        f"  engine jobs: {jobs['active']} active, {jobs['finished']} finished, {jobs['minutes']:.0f} runner minutes so far"
        f" (avg {jobs['avg_minutes']:.1f} min per run)",
        f'  queued runs: {waiting}, about {waiting / SCHEDULED_RUNS_PER_MINUTE:.0f} min until all are scheduled',
    ])


def mean(values):
    return sum(values) / len(values) if values else None


def results(target):
    """Organizer ranking: one row per team, the mean of its first repeat_runs scored evaluations.

    Per card the mean over those evaluations (a card the team itself failed is 0 in its evaluation),
    overall the mean over the cards (equal to the mean of the evaluations' scores), as on the board.
    """
    cte = CURRENT_BATCHES.format(target=q(target))
    batches = deploy.query(cte + """select t.id as team_id, t.name as team_name, t.is_hidden, cur.id as batch_id,
        cur.status, cur.score, cur.revision_id, cur.finished_at,
        case when cur.status in ('failed','cancelled') then """ + FAILURE.format(batch='cur.id') + """ end as failure,
        (select jsonb_object_agg(s.slug, jsonb_build_object('status',r.status,'score',r.score,'error',nullif(r.error,''),
            'participant_failure',private.observer_participant_failure(r.id)))
          from public.observer_runs r join public.scenarios s on s.id=r.scenario_id where r.batch_id=cur.id) as runs
      from cur join public.teams t on t.id=cur.team_id order by t.id, cur.created_at, cur.id""")
    cards = sorted(r['slug'] for r in deploy.query(
        'select s.slug from public.phase_scenarios ps join public.scenarios s on s.id=ps.scenario_id'
        ' where ps.phase_id='+q(target)+'::uuid'))
    mode = one(deploy.query('select leaderboard_mode from public.phases where id='+q(target)+'::uuid'), 'phase')['leaderboard_mode']
    need = repeat_runs(target)
    # Whether each card's repeats all overlapped in time (latest start before earliest finish).
    concurrent = {r['team_id']: r['ok'] for r in deploy.query(
        'select distinct b.team_id, private.observer_final_set_concurrent(b.team_id,b.phase_id) as ok'
        ' from public.observer_batches b where b.phase_id=' + q(target) + "::uuid and b.purpose='formal'")}
    teams = {}
    for b in batches:
        b['runs'] = jsonish(b['runs']) or {}
        b['score'] = None if b['score'] is None else float(b['score'])
        teams.setdefault(b['team_id'], []).append(b)
    rows = []
    for team_id, own in teams.items():
        scored = [b for b in own if b['status'] == 'scored'][:need]
        active = [b for b in own if b['status'] in ('queued', 'running')]
        last = own[-1]
        r = {'team_id': team_id, 'team_name': last['team_name'], 'is_hidden': last['is_hidden'],
             'revision_id': last['revision_id'], 'batch_id': scored[0]['batch_id'] if scored else last['batch_id'],
             'batch_ids': [b['batch_id'] for b in scored], 'evaluations': len(scored), 'repeat_runs': need,
             'finished_at': max((b['finished_at'] for b in scored), default=None), 'failure': None,
             'concurrent': concurrent.get(team_id, True) if need > 1 else True}
        if len(scored) >= need and not r['concurrent']:
            r['status'] = 'not_concurrent'   # replaced with --retry-failed; never ranked
        elif len(scored) >= need:
            r['status'] = 'scored'
        elif active:
            r['status'] = 'running'
        else:
            r['status'] = last['status'] if last['status'] != 'scored' else 'incomplete'
            r['failure'] = last['failure']
        # A card the team itself failed scores 0 in that evaluation and counts in the mean.
        r['unfinished'] = sorted({c for b in scored for c, run in b['runs'].items() if run.get('participant_failure')}) \
            if r['status'] == 'scored' else []
        r['runs'] = {c: {'score': mean([0.0 if b['runs'].get(c, {}).get('participant_failure')
                                        else float(b['runs'][c]['score']) for b in scored
                                        if b['runs'].get(c, {}).get('participant_failure') or b['runs'].get(c, {}).get('score') is not None])}
                     for c in cards} if r['status'] == 'scored' else {}
        totals = [b['score'] for b in scored if b['score'] is not None]
        r['score'] = mean(totals) if r['status'] == 'scored' else None
        r['min'], r['max'] = (min(totals), max(totals)) if r['status'] == 'scored' and totals else (None, None)
        rows.append(r)
    ranked = sorted((r for r in rows if r['status'] == 'scored' and not r['is_hidden']),
                    key=lambda r: (-r['score'], r['team_name'], str(r['team_id'])))
    rank, previous = 0, None
    for position, r in enumerate(ranked, 1):
        if r['score'] != previous: rank, previous = position, r['score']
        r['rank'] = rank  # equal scores share a rank; ties are broken by the organizers
    others = sorted((r for r in rows if 'rank' not in r), key=lambda r: (r['is_hidden'], r['status'], r['team_name']))
    return ranked + others, cards, mode


def card_score(r, card):
    """The mean score of one card over the team's evaluations (a card the team itself failed is 0 there)."""
    return r['runs'].get(card, {}).get('score')


def format_results(rows, cards, mode):
    lines = [f'RESULTS (leaderboard_mode={mode}; participants see nothing until it is published)',
             '  ' + ' '.join(['rank', f"{'team':<40}", *(f'{c:>9}' for c in cards), f"{'mean':>9}", f"{'range':>17}", ' evals  status'])]
    for r in rows:
        cell = lambda v, mark='': (f'{v:8.2f}' if v is not None else f"{'-':>8}") + (mark or ' ')
        note = r['status'] + (f" ({r['failure']})" if r['failure'] else '') + (' hidden-team' if r['is_hidden'] else '')
        spread = f"{r['min']:.2f}-{r['max']:.2f}" if r['min'] is not None else '-'
        lines.append(f"  {str(r.get('rank', '-')):>4} {r['team_name'][:40]:<40} "
                     + ' '.join(cell(card_score(r, c), '*' if c in r['unfinished'] else '') for c in cards)
                     + f" {cell(r['score'])} {spread:>17}  {r['evaluations']}/{r['repeat_runs']}  {note}")
    ranked = sum(1 for r in rows if 'rank' in r)
    lines.append(f'  ranked={ranked}, not ranked={len(rows) - ranked} (incomplete, failed or hidden teams)')
    lines.append('  each card and the mean average the first evaluations of each team; range: lowest-highest evaluation mean')
    if any(not r['concurrent'] for r in rows):
        lines.append('  ! not_concurrent: a card\'s repeats did not all run at the same time; not ranked.'
                     ' Replace the whole set with --apply --retry-failed')
    if any(r['unfinished'] for r in rows):
        lines.append("  * failed because of the team's project (build, crash, protocol) or a rejected trace: 0, counted in the mean")
    return '\n'.join(lines)


def write_csv(path, rows, cards):
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        out = csv.writer(handle)
        out.writerow(['rank', 'team_id', 'team_name', 'hidden_team', 'revision_id', 'batch_id', 'status', 'failure',
                      *cards, 'mean', 'unfinished_cards', 'evaluations', 'min', 'max', 'batch_ids', 'concurrent'])
        for r in rows:
            out.writerow([r.get('rank', ''), r['team_id'], r['team_name'], r['is_hidden'], r['revision_id'], r['batch_id'],
                          r['status'], r['failure'] or '', *('' if (v := card_score(r, c)) is None else v for c in cards),
                          '' if r['score'] is None else r['score'], ' '.join(r['unfinished']), r['evaluations'],
                          '' if r['min'] is None else r['min'], '' if r['max'] is None else r['max'],
                          ' '.join(str(b) for b in r['batch_ids']), r['concurrent']])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', default='online', help='Formal phase slug (default: online)')
    parser.add_argument('--target', default='final-hidden', help='Sealed hidden phase slug (default: final-hidden)')
    parser.add_argument('--team', help='Only this team (slug, name or id)')
    parser.add_argument('--apply', action='store_true', help='Create the evaluations (default: dry run)')
    parser.add_argument('--before-freeze', action='store_true', help='With --team: allow before the source phase ends')
    parser.add_argument('--retry-failed', action='store_true',
                        help='Replace a team\'s whole set of evaluations where one failed because of the platform,'
                             ' the set is incomplete, or a card\'s repeats did not run at the same time')
    parser.add_argument('--retry-participant-failures', action='store_true',
                        help="Also recreate evaluations that failed because of the team's project (organizer decision)")
    parser.add_argument('--limit', type=int, help='With --apply: create at most this many evaluations now')
    parser.add_argument('--status', action='store_true', help='Show the progress of the hidden evaluations')
    parser.add_argument('--results', action='store_true', help='Show the organizer ranking (changes nothing)')
    parser.add_argument('--csv', help='With --results: also write the ranking to this local CSV file')
    parser.add_argument('--json', action='store_true', help='Print the full JSON result')
    args = parser.parse_args()
    if args.before_freeze and not args.team: parser.error('--before-freeze requires --team')
    if args.csv and not args.results: parser.error('--csv requires --results')
    if args.limit is not None and (not args.apply or args.limit < 1): parser.error('--limit requires --apply and a positive number')
    if (args.status or args.results) and (args.apply or args.team or args.retry_failed or args.retry_participant_failures):
        parser.error('--status and --results only read; run them without --apply, --team or --retry-*')
    target = one(deploy.query('select id from public.phases where slug='+q(args.target)), 'target phase')['id']
    if args.status or args.results:
        if args.status: print(status(target))
        if args.results:
            rows, cards, mode = results(target)
            print(json.dumps(rows, ensure_ascii=False, indent=1, default=str) if args.json else format_results(rows, cards, mode))
            if args.csv: write_csv(args.csv, rows, cards); print(f'  written to {args.csv} (keep it private until publication)')
        return
    phase = one(deploy.query('select id, ends_at is not null and now()>=ends_at as finished, ends_at from public.phases'
                             ' where slug='+q(args.source)), 'source phase')
    source = phase['id']
    if not phase['finished'] and not args.before_freeze:
        # The database refuses this (source_phase_not_finished); say why instead of an opaque API error.
        parser.error(f"{args.source} has not ended yet (ends_at={phase['ends_at']}); test one team with --team ... --before-freeze")
    team = None
    if args.team:
        team = one(deploy.query('select id from public.teams where id::text='+q(args.team)+' or slug='+q(args.team)
                                +' or name='+q(args.team)), 'team')['id']
    result = jsonish(one(deploy.query(plan_sql(source, target, team, args.apply, args.before_freeze, args.retry_failed,
                                               args.retry_participant_failures, args.limit)), 'result')['result'])
    if args.json: print(json.dumps(result, ensure_ascii=False, indent=1)); return
    line, settings = target_settings(target)
    # One entry per evaluation to create: each is one batch of the team's member.
    users = [t['user_id'] for t in result['teams'] if t['action'] in ('would_create', 'created')
             for _ in range(t.get('new_evaluations') or 1)]
    print(summarize(result) + '\n' + line + '\n'
          + estimate(users, settings['scenarios'], settings['runtime_seconds'], runner_capacity(users), public_pool(target)))


if __name__ == '__main__': main()
