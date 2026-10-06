# Health of the private runner organizations

Since migration `20261004070000` the 13 private organizations
(`AGENTIC-OBSERVER26-runner-N/observer-control`) have the same per-target
health, cooldown and kill switch as their 13 public repositories
(`ops/public-runner-pool.md`). All 26 targets are shown together:

```sql
select public.observer_targets_status();   -- {health, private: [13], public: [13]}
```

## What counts against an organization

Within `window_minutes` (15):

| event | meaning |
|---|---|
| `stalled` | GitHub accepted the job three times, no run claimed it `stall_minutes` (4) after the last dispatch. Actions locked or out of minutes, hosted runners unavailable, a runtime that cannot claim — a claim that keeps failing ends up here too. |
| `platform_failed` | a claimed job failed for a platform reason (diagnostic code other than `project_error` / `project_operation_failed`). |
| dispatch failure | an organization-level dispatch error (`ORGANIZATION_FAILURES` in `observer-dispatch.ts`), already recorded in `observer_dispatch_failures`. |

`threshold` (3) of them put the organization in a `cooldown_minutes` (15)
cooldown. Public repositories now also count `platform_failed` next to their
returns.

## What happens to an unhealthy organization

An organization is unhealthy while it cools down, while its routing is switched
off, or while its month's minutes are at `monthly_minute_limit` (1800 of
GitHub Free's 2000). Then:

* a stalled job moves to the best healthy organization at once (fresh claim
  window; the team's session gets back the time it waited; a late claim by the
  old run is refused because the job's repository changed);
* its queued jobs move to healthy organizations, and with each job the owner's
  placement, so the team's next jobs go there too;
* new participants, failovers and rescore jobs are not placed there;
* its colocated jobs may overflow to the public repositories.

Jobs already running are never touched. Preparation jobs stay in their
organization (their repository lives there). If no organization is healthy,
nothing moves. The work runs every minute in pg_cron
(`observer-target-health` → `observer_target_health_reconcile()`), independent
of the dispatcher Edge function.

## GitHub minutes

The platform's estimate (claimed → finished) undercounts what GitHub bills:
on 2026-10-04 runner-11 had 412 estimated vs 1182 billed minutes.
`scripts/sync-runner-minutes.py` reads the billed minutes of every organization
(`gh` login of an organization owner; the GitHub App has no billing permission)
and stores them; while health is `on`, the limit is checked against
`greatest(estimate, billed at last sync + estimate growth since)`. A failed or
stale sync only falls back to the estimate. It runs on the organizer's Mac
every 20 minutes (LaunchAgent `org.agentic-observer.minutes-sync`,
`/Users/Shared/observer-minutes-sync/`).

## Switches (one line each)

```sql
select public.observer_set_target_health(p_mode=>'on');        -- act (default after rollout)
select public.observer_set_target_health(p_mode=>'observe');   -- record and show only
select public.observer_set_target_health(p_mode=>'off');       -- rollback: exactly the old behaviour
select public.observer_set_private_target('AGENTIC-OBSERVER26-runner-7', p_routing=>false);   -- kill switch
select public.observer_set_private_target('AGENTIC-OBSERVER26-runner-7', p_routing=>true);
select public.observer_set_private_target('AGENTIC-OBSERVER26-runner-7', p_clear_cooldown=>true);
select public.observer_set_target_health(p_stall_minutes=>4, p_threshold=>3, p_window_minutes=>15, p_cooldown_minutes=>15);
```

`p_routing=>false` differs from `enabled=false`: it sends no new work, but runs
already claimed there keep working and finish (with `enabled=false` their claims
and results would be refused).
