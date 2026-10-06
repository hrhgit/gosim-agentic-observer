# DB watchdog

`.github/workflows/db-watchdog.yml` + `scripts/db_watchdog.py` keep the Supabase
project (`vdiemcofukuxglqsmlyz`) up without any human in the loop. It sends no
notifications anywhere - the only trace of its activity is the Actions run log.

## What it does

Every ~5 minutes (`cron: */5 * * * *`), one workflow run:

1. Checks the project up to 4 times, ~60s apart:
   - Management API `GET /v1/projects/<ref>/health?services=db,rest,auth,storage`
   - A real end-user path: anon REST `GET` on the public `announcements` table
2. If 3 checks in a row fail, it restarts the project
   (`POST /v1/projects/<ref>/restart`), unless:
   - the project status is already `RESTARTING` or `COMING_UP`,
   - the last restart was less than 20 minutes ago, or
   - there have already been 4+ restarts in the last 6 hours.
3. Restart history (last restart time, timestamps in the last 6h) is persisted
   as `state.json` on a dedicated `db-watchdog-state` branch - never on `main`,
   and only written when a restart actually happens (dry-run or real).

A `concurrency` group (`db-watchdog`) guarantees runs never overlap: if a run
is still going when the next cron fires, the new one waits.

4. The last step re-dispatches the workflow (`gh workflow run db-watchdog.yml`)
   regardless of how the check step went, so the next run starts right away
   instead of waiting on GitHub's `schedule` trigger - see "Why the
   self-redispatch step" below.

Unless the check was simulated, every run also logs the anon-key-safe
`observer_incident_summary()` RPC result (open incidents, parked
revisions/runs) - the retry/requeue mechanism in
`supabase/migrations/20261003000100_platform_failure_resilience.sql` runs on
its own minute-by-minute via `observer_tick`; this is only a log line here so
a backlog is visible in the Actions run log between organizer checks of the
admin incidents page (`/admin/incidents`). No restart decision depends on it.

## Secrets

Repo Actions secrets (set via `gh secret set`): `SUPABASE_ACCESS_TOKEN`,
`SUPABASE_PROJECT_REF`, `SUPABASE_ANON_KEY`. The workflow also uses the
automatic `GITHUB_TOKEN` (needs `permissions: contents: write` and
`permissions: actions: write`, the latter for the self-redispatch step) to
read/write `state.json` on the state branch and to re-run the workflow.

## Manual testing

`workflow_dispatch` takes two inputs:
- `dry_run` - logs the restart decision but never calls the restart API
- `simulate_fail` - forces every check to report failure, so you don't have
  to wait for a real outage to exercise the restart branch

Run with both `true` to confirm the "would restart" log line without ever
touching the project. Run with just `dry_run: true` to see real health
checks end-to-end without risking an actual restart.

## Why the self-redispatch step

On 2026-10-02/03 the `schedule` trigger fired only twice in over 10 hours
for this workflow, instead of every ~5 min - not the "occasionally delayed"
behavior GitHub's docs describe, but a near-total failure to fire (YAML,
workflow state, and repo/org Actions settings all checked out fine). To not
depend on it, the job's last step always re-dispatches itself via
`workflow_dispatch`, the same self-chaining pattern `worker.yml` already
uses upstream. `schedule` is left in place as a free extra chance; the
`concurrency` group prevents the two mechanisms from ever running two
checks at once.

This makes the chain self-sustaining but also self-contained: nothing
supervises it from outside. If a run fails before reaching that last step
(it's `if: always()`, so only a hard crash of the whole job - e.g. the
runner image failing to provision - skips it), Actions gets disabled, or
someone edits out the step, the chain stops and needs a manual
`workflow_dispatch` to restart.

## Stopping it

Disable the workflow (`gh workflow disable db-watchdog.yml -R
gosimfoundation/hackathon-survey26`) or delete `.github/workflows/db-watchdog.yml`.
Either one also stops the self-redispatch chain, since a disabled/deleted
workflow can't be re-dispatched. There is nothing else to stop - it only
runs as a GitHub-hosted Action.

## Caveat

GitHub's `schedule` trigger is best-effort: under platform load, scheduled
runs can be delayed by several minutes or occasionally skipped entirely -
and, as above, has been observed to be far less reliable than that for this
repo. Treat it as a bonus, not the mechanism keeping this running; see
"Why the self-redispatch step".
