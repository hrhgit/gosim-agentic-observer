#!/usr/bin/env python3
"""Rebalance recorded runner placements across the runner organizations.

A participant's placement (private.observer_placements, exposed as
public.observer_placement) decides which AGENTIC-OBSERVER26-runner-N
organization hosts their private repository and every future job. Count-based
placement left usage uneven (one week: runner-1 1543 minutes, runner-8 11).
This tool moves idle participants so per-organization recent usage converges.
Organizations already over their monthly minute cap (monthly_minute_limit on
their installation row) are never chosen as targets.

Dry-run by default: it only prints the planned moves as JSON. With --apply the
moves run in a single transaction that re-verifies each participant is still
idle before updating their row; any concurrent activity rolls everything back.

Who runs it: the organizer (BH3GEI) on a machine holding the production
management credentials SUPABASE_PROJECT_REF and SUPABASE_ACCESS_TOKEN (the
same pair used by deploy-observer-backend.py). Run it while evaluation traffic
is low. No GitHub operation is needed: the App is already installed on every
runner organization and the next scheduled job auto-creates a fresh
participant repository in the new organization. Previous revisions and
evidence stay readable in the old organization; nothing is transferred or
deleted. Participants with in-flight jobs, runs or preparations are never
moved.
"""
import argparse
import json
import os
import urllib.error
import urllib.request

# In-flight work pins a participant to their current organization: claimed job
# payloads and artifact uploads resolve the repository through the placement.
NOT_BUSY = """
  not exists(select 1 from private.observer_jobs j
      left join public.observer_runs r on r.id=j.run_id
      left join public.observer_batches b on b.id=r.batch_id
      left join public.observer_revisions v on v.id=j.revision_id
      left join public.observer_projects pr on pr.id=v.project_id
    where coalesce(b.user_id,pr.owner_id)=p.user_id
      and j.status in ('queued','dispatched','claimed') and j.expires_at>now())
  and not exists(select 1 from public.observer_batches b
      join public.observer_runs r on r.batch_id=b.id
    where b.user_id=p.user_id
      and r.status in ('queued','starting','ready','running','awaiting_csv'))
  and not exists(select 1 from public.observer_projects pr
      join public.observer_revisions v on v.project_id=pr.id
    where pr.owner_id=p.user_id and v.status in ('queued','preparing'))
"""

ENABLED_SQL = """
select i.organization from private.observer_installations i where i.enabled
  and coalesce((select sum(extract(epoch from j.finished_at-j.claimed_at)) from private.observer_jobs j
    where j.organization=i.organization and j.claimed_at is not null and j.finished_at is not null
      and j.finished_at>=date_trunc('month',now())),0) < i.monthly_minute_limit*60
  order by 1
"""

USAGE_SQL = """
select p.user_id, p.organization,
  coalesce((select sum(extract(epoch from j.finished_at-j.claimed_at)) from private.observer_jobs j
      left join public.observer_runs r on r.id=j.run_id
      left join public.observer_batches b on b.id=r.batch_id
      left join public.observer_revisions v on v.id=j.revision_id
      left join public.observer_projects pr on pr.id=v.project_id
    where coalesce(b.user_id,pr.owner_id)=p.user_id
      and j.claimed_at is not null and j.finished_at is not null
      and j.finished_at>now()-(%d||' days')::interval),0)::float8 as recent_seconds,
  not (""" + NOT_BUSY + """) as busy
from private.observer_placements p
"""


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def plan_moves(enabled, users, *, min_tolerance_seconds=600.0, tolerance_ratio=0.2):
    """Greedy reassignment: even out recent usage, then even out headcounts.

    users: [{'user_id', 'organization', 'recent_seconds', 'busy'}] covering every
    recorded placement. Only idle (not busy) participants move; placements on
    organizations that are no longer enabled are valid sources, never targets.
    Deterministic: ties resolve on the organization name, then the user id.
    """
    targets = sorted(enabled)
    if not targets:
        raise ValueError('no enabled runner organizations')
    names = sorted(set(targets) | {u['organization'] for u in users})
    loads = {o: 0.0 for o in names}
    counts = {o: 0 for o in names}
    movable = {o: [] for o in names}
    for u in users:
        loads[u['organization']] += float(u['recent_seconds'])
        counts[u['organization']] += 1
        if not u['busy']:
            movable[u['organization']].append(u)
    for entries in movable.values():
        entries.sort(key=lambda u: (-float(u['recent_seconds']), str(u['user_id'])))
    ideal = sum(loads[o] for o in targets) / len(targets)
    tolerance = max(min_tolerance_seconds, tolerance_ratio * ideal)
    before = {o: {'recent_seconds': loads[o], 'participants': counts[o]} for o in names}
    moves = []
    exhausted = set()
    while True:
        sources = [o for o in names if o not in exhausted and movable[o]]
        if not sources:
            break
        src = max(sources, key=lambda o: (loads[o], o))
        dst = min(targets, key=lambda o: (loads[o], o))
        gap = loads[src] - loads[dst]
        if gap <= tolerance:
            break
        candidates = [u for u in movable[src] if 0 < float(u['recent_seconds']) <= gap]
        if not candidates:
            exhausted.add(src)
            continue
        pick = min(candidates, key=lambda u: (abs(float(u['recent_seconds']) - gap / 2), str(u['user_id'])))
        seconds = float(pick['recent_seconds'])
        movable[src].remove(pick)
        loads[src] -= seconds
        loads[dst] += seconds
        counts[src] -= 1
        counts[dst] += 1
        moves.append({'user_id': str(pick['user_id']), 'from': src, 'to': dst, 'recent_seconds': seconds})
    # Second pass: zero-usage participants are free to move, so equalize counts
    # without touching the usage balance.
    starved = set()
    while True:
        sources = [o for o in names if o not in starved and any(float(u['recent_seconds']) == 0 for u in movable[o])]
        if not sources:
            break
        src = max(sources, key=lambda o: (counts[o], o))
        dst = min(targets, key=lambda o: (counts[o], o))
        if counts[src] - counts[dst] <= 1:
            break
        pick = next(u for u in movable[src] if float(u['recent_seconds']) == 0)
        movable[src].remove(pick)
        counts[src] -= 1
        counts[dst] += 1
        moves.append({'user_id': str(pick['user_id']), 'from': src, 'to': dst, 'recent_seconds': 0.0})
        if not any(float(u['recent_seconds']) == 0 for u in movable[src]):
            starved.add(src)
    after = {o: {'recent_seconds': loads[o], 'participants': counts[o]} for o in names}
    return {'moves': moves, 'load_before': before, 'load_after': after,
            'tolerance_seconds': tolerance, 'targets': targets}


def apply_sql(moves):
    """One transaction: re-check idleness per move, verify, or roll everything back."""
    if not moves:
        raise ValueError('no moves to apply')
    lines = [
        'begin;',
        "select pg_advisory_xact_lock(hashtext('observer-placement'));",
        'create temp table observer_rebalance_plan(user_id uuid primary key, source text not null,'
        ' target text not null) on commit drop;',
        'insert into observer_rebalance_plan values '
        + ','.join('(' + quote(m['user_id']) + ',' + quote(m['from']) + ',' + quote(m['to']) + ')' for m in moves)
        + ';',
    ]
    for m in moves:
        lines.append(
            'update private.observer_placements p set organization=' + quote(m['to'])
            + ' where p.user_id=' + quote(m['user_id']) + ' and p.organization=' + quote(m['from'])
            + ' and ' + NOT_BUSY + ';')
    lines.append("""
do $$
declare r record;
begin
  for r in select t.user_id, t.target, p.organization from observer_rebalance_plan t
      join private.observer_placements p on p.user_id=t.user_id loop
    if r.organization is distinct from r.target then
      raise exception 'rebalance_refused: % stayed in % (concurrent activity); rolled back', r.user_id, r.organization;
    end if;
  end loop;
  if exists(select 1 from observer_rebalance_plan t
      left join private.observer_placements p on p.user_id=t.user_id where p.user_id is null) then
    raise exception 'rebalance_refused: a planned placement row vanished; rolled back';
  end if;
end $$;""")
    lines.append('commit;')
    return '\n'.join(lines)


def query(sql):
    ref = os.environ['SUPABASE_PROJECT_REF']
    request = urllib.request.Request('https://api.supabase.com/v1/projects/' + ref + '/database/query',
        data=json.dumps({'query': sql}).encode(), headers={
            'Authorization': 'Bearer ' + os.environ['SUPABASE_ACCESS_TOKEN'],
            'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'Management API returned HTTP {error.code}; no credentials were logged') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument('--apply', action='store_true', help='Execute the planned moves (default: dry-run only)')
    parser.add_argument('--window-days', type=int, default=7,
                        help='Recent-usage window in days; must match the placement function (default 7)')
    args = parser.parse_args()
    if not 1 <= args.window_days <= 90:
        raise RuntimeError('--window-days must be between 1 and 90')
    organizations = [row['organization'] for row in query(ENABLED_SQL)]
    users = query(USAGE_SQL % args.window_days)
    plan = plan_moves(organizations, users)
    print(json.dumps({'dry_run': not args.apply, 'window_days': args.window_days,
                      'organizations': plan['targets'], 'tolerance_seconds': plan['tolerance_seconds'],
                      'load_before': plan['load_before'], 'moves': plan['moves'],
                      'load_after': plan['load_after']}, indent=2), flush=True)
    if not args.apply or not plan['moves']:
        return
    query(apply_sql(plan['moves']))
    print(json.dumps({'applied': len(plan['moves'])}))


if __name__ == '__main__':
    main()
