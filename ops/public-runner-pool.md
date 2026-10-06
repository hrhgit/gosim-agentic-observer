# Public-repository runner pool

GitHub-hosted runners are free and unmetered for **public** repositories. The
public pool is one public repository,
`AGENTIC-OBSERVER26-runner-12/observer-public` (repository id `1401336826`),
that takes evaluation jobs as an **overflow** of the private runner
organizations (`AGENTIC-OBSERVER26-runner-N/observer-control`). It is **off by
default**; nothing changes until an organizer switches it on.

Everything about a public run is visible to anyone: the workflow code, the run
page, its log, its inputs and any artifact. The pool is built so that none of
it carries anything private.

## What a public run can see, and what the public can see

| | Private pool | Public pool |
|---|---|---|
| Dispatch inputs (public) | `job_id`, `job_nonce` | `job_id` only (an opaque UUID) |
| Claim binding | nonce + GitHub OIDC (private repository) | the run id GitHub returned at dispatch + GitHub OIDC (`repository_visibility=public`, approved runtime tag only) |
| Claim payload (signed URLs, run credentials, manifest) | plain JSON over TLS | sealed to the job's runner key |
| Scenario bundle | signed URL to the bundle | signed URL to a **sealed copy** (`observer-staging/sealed/<job>/scenario.zip`) |
| Participant project | GitHub archive URL | signed URL to a **sealed copy** (`…/project.zip`) |
| Result | pushed to the team's private repository with a one-repository token | **sealed to the backend's public key**, uploaded to `…/result.zip` through an upload URL issued only when the result is ready; the backend opens it and commits it to the team's private repository. The runner never gets a repository token. |
| Run log | two status lines | one status line; all runtime output goes to `/dev/null` |
| Artifacts, caches, job summary | none | none (log retention 1 day) |

Sealing ("observer-seal-v1", `project_platform/sealing.py` and
`supabase/functions/_shared/observer-seal.ts`): ephemeral X25519 + HKDF-SHA256 +
AES-256-GCM. The runner generates a fresh X25519 key pair in memory for every
job and sends only the public half with its claim; the backend seals the claim
payload, the scenario and the project to it. The result is sealed to the public
half of `OBSERVER_RESULT_SEAL_KEY`, a Supabase secret that never leaves the
backend; the runner receives only the public key. Each sealed object is bound
to its purpose and job (`claim:<job>`, `scenario:<job>`, `project:<job>`,
`result:<job>`), so it cannot be replayed as another object or for another job.
A leaked signed URL yields only ciphertext. The dispatcher deletes the sealed
staging objects once the job has finished.

The participant's container keeps every sandbox and hardening layer of the
private pool (restricted egress, read-only root, the independent rescore): the
public pool runs the same trusted engine code, exported from the public
competition repository by `scripts/build-observer-control.py --public`.

Repository settings (set once, keep them): Actions limited to GitHub-owned
actions, default token read-only, fork pull request workflows need approval
for all outside contributors, log and artifact retention 1 day, issues / wiki /
projects off, no secrets, variable `OBSERVER_JOB_URL` only. There is no push or
pull request workflow in the repository.

## Which jobs use it

Only colocated engine jobs (one job per run, public scenarios of a colocated
phase; never split execute/engine runs, randomized private instances,
preparation or rescore jobs), and only for phases switched on in
`private.observer_public_pool_phases`. A **sealed (hidden) phase** such as
`final-hidden` additionally needs `sealed_transfer_verified = true`, which an
organizer sets after an end-to-end drill has shown the sealed path working.
In `overflow` mode such a sealed phase **prefers** the pool (migration
`20261004050000`): its runs go there whenever a slot is free (`max_active`,
`monthly_minute_cap`), without waiting for their organization to be busy or near
its minutes, and the rest run in their own organizations as usual.

Modes (`observer_set_public_pool`):

| mode | effect |
|---|---|
| `off` (default) | never used; queued public jobs go back to their organization |
| `drill` | only runs of `drill_users` (in enabled phases) |
| `overflow` | runs of `drill_users`, plus any enabled-phase run whose own organization is at `overflow_ratio` (default 0.8) of its monthly minutes, has `busy_jobs` (default 12) active jobs, or when every organization is over its limit; also a job whose organizations all failed to dispatch, before the self-hosted fallback |

Caps keep use modest: at most `max_active` (default 3) public jobs at once and
`monthly_minute_cap` (default 6000) minutes per month.

A public job that GitHub never starts (no claim ten minutes after dispatch), a
public dispatch that fails (it is then dispatched to its own organization in
the same round), a queued public job left for five minutes, and a queued public
job when the pool is switched off all go back to their own organization
(`observer_public_return_job`) **for good**: a returned job is never offered to
the pool again, so a broken pool cannot bounce jobs. The run's session window is
extended by the time spent in the pool. A late claim by the abandoned public
run is refused. When every private organization fails to dispatch, the public
pool is tried before the self-hosted fallback, and the fallback still follows if
the public dispatch fails.

`observer-public` shares runner-12's concurrent-job limit (GitHub Free: 20)
with runner-12's private jobs; a public job that waits more than ten minutes
for a runner goes home. Rescore jobs of public runs run in the team's own
organization.

## Switches

```sql
select public.observer_public_pool();                          -- state, minutes used, enabled phases
select public.observer_set_public_pool(p_mode=>'off');         -- turn it off (the kill switch)
select public.observer_set_public_pool(p_mode=>'overflow');    -- overflow for enabled phases
select public.observer_set_public_pool(p_mode=>'drill', p_drill_users=>array['<user uuid>']::uuid[]);
select public.observer_set_public_pool(p_max_active=>3, p_monthly_minute_cap=>6000,
  p_overflow_ratio=>0.8, p_busy_jobs=>12);
select public.observer_set_public_pool_phase('practice-projects', true);
select public.observer_set_public_pool_phase('online', true);
-- hidden final: only after a passing sealed-transfer drill (done 2026-10-04, see below)
select public.observer_set_public_pool(p_sealed_transfer_verified=>true);
select public.observer_set_public_pool_phase('final-hidden', true);
select public.observer_set_public_pool(p_max_active=>15);       -- during the final: a larger free share
```

Every change is audited (`observer.public_pool`, `observer.public_pool_phase`).
Turning the pool off never interrupts a run already claimed.

## Sealed-transfer drill (hidden phases)

Run it with test data only: a hidden test team and an inactive rehearsal phase
whose cards are copies, never the real hidden cards.

1. `observer_set_public_pool(p_drill_users=>array['<test user>']::uuid[], p_sealed_transfer_verified=>true)`
   and `observer_set_public_pool_phase('<rehearsal phase>', true)` (no real hidden
   phase is switched on yet, so the flag only reaches the rehearsal).
2. Create one evaluation of the test team's version in the rehearsal phase; up to
   `max_active` of its runs go to the pool, the rest to the team's organization.
3. While a run is in flight, read `observer-staging/sealed/<job>/*.zip` with the
   service key: every object must be ciphertext (no ZIP header, ~8 bits/byte).
4. After the runs: every public job `succeeded`, `sealed_cleaned_at` set (staging
   objects gone), runs `scored` with `score_check='verified'` (rescored in the home
   organization), results committed to the team's private repository, and the
   scores equal a private-pool evaluation of the same version.
5. Public side, for every public run (`gh api repos/AGENTIC-OBSERVER26-runner-12/observer-public/actions/runs/<id>/logs`):
   the log has one status line and the step's environment (`OBSERVER_JOB_URL`, the
   job id, `OBSERVER_POOL`) only; no card, scenario, team, revision, score, digest,
   signed URL or token appears; the run has no artifacts or annotations and the
   repository no caches.
6. Reset `p_drill_users=>'{}'`, switch the rehearsal phase off; keep
   `sealed_transfer_verified` only if every check passed, then switch the hidden
   phase on.

**2026-10-04 drill: passed.** Rehearsal phase `rehearsal-final-avg` (inactive,
sealed, colocated, 4 rehearsal cards), hidden test team
`acceptance-w02-platform-test`, one evaluation: 3 runs in the pool (runs
37176946016, 37176948557, 37176950489), 1 in runner-13. All four scored and
verified by the rescore; the evaluation's score equals the team's three earlier
private-pool evaluations exactly. Staging objects were ciphertext in flight and
deleted afterwards. The 27 log files of the three public runs were checked against
2,052 strings taken from the four card bundles and the runs (file names, keys,
values, ids, scores, digests, commit ids) plus generic markers (`sig=`, `token=`,
`eyJ`, `storage/v1`, `sk-`): no match apart from GitHub's own "Prepare all
required actions". No artifacts, annotations or caches. Afterwards
`sealed_transfer_verified=true` and `final-hidden` was switched on for the pool
(mode `overflow`, `max_active` 3). Raise `max_active` (at most 20, shared with
runner-12's own jobs) before the final for a larger share;
`scripts/run-hidden-final.py` shows the share in its preview.

## One public repository per runner organization (2026-10-04)

Since migration `20261004061000` every runner organization has its own public
repository `AGENTIC-OBSERVER26-runner-N/observer-public` (13 in total, identical
runner-only content: the engine workflow and the trusted runtime, the same
approved runtime tag, the same repository settings and variable). Each one is a
row of `private.observer_public_targets` with its own switch, `max_active`
(default 15; GitHub Free runs at most 20 jobs at once per organization, shared
with that organization's private repository) and health.

How a job is placed:

1. `observer_pending_jobs` asks `observer_public_candidate` whether the job may
   use a public repository (phase switched on, sealed rules, global cap
   `max_active` and `monthly_minute_cap`, mode rules below).
2. `observer_public_job` picks the least busy healthy public repository with a
   free slot (busy = active jobs / max_active, ties at random) and the
   dispatcher sends the job there with only its id.
3. A public repository that does not start or dispatch a job (not claimed within
   ten minutes, queued five minutes, GitHub error) gives the job back to its own
   organization for good, and three such returns within 15 minutes put the
   repository in a 15-minute cooldown: no new jobs, the load goes to the others.
   Private organizations have the same health, cooldown and kill switch since
   migration `20261004070000` (`ops/private-target-health.md`); all 26 targets:
   `select public.observer_targets_status();`.

Modes: `off`, `drill`, `overflow` as before, plus `primary`: public repositories
are the first choice for the teams within `rollout_percent` (a stable hash of the
team id; 100 = every team); other teams keep the overflow rules.

```sql
select public.observer_public_targets_status();                      -- load and health per repository
select public.observer_set_public_target('AGENTIC-OBSERVER26-runner-7', p_enabled=>false);  -- one repository off
select public.observer_set_public_target('AGENTIC-OBSERVER26-runner-7', p_clear_cooldown=>true);
select public.observer_set_public_pool(p_mode=>'primary', p_rollout_percent=>10);   -- staged rollout
select public.observer_set_public_pool(p_mode=>'overflow', p_rollout_percent=>0);   -- rollback to overflow only
select public.observer_set_public_pool(p_mode=>'off');                              -- everything back to private
select organization, month_minutes, monthly_minute_limit, active_jobs, over_limit
  from public.observer_organizations_by_load();                       -- private organizations
```

Runtime updates now go to all 13 repositories: push the same commit and tag to
each (`git push <repo> main refs/tags/observer-runtime-<sha>`; identical history
gives identical commit ids) and then
`update private.observer_public_targets set approved_sha='<sha>';`.

**Widened drill 2026-10-04 (team variables, every repository): passed.** A hidden
test team saved a secret and a plain team variable (random canaries) and an
adversarial test project that printed both, workflow commands (`::warning::`,
`::error::`, ...) and the first card message to its own output, tried to write the
runner's step summary / env / output files and runner directories, listed
processes and probed the cloud metadata service, the Docker host, the internet,
GitHub and Supabase. 8 runs on runner-12 (4 practice, 4 sealed rehearsal), then
one sealed rehearsal run on each of the 12 new repositories: all 20 scored and
committed to the team's private repository. 180 public log files checked against
about 12,700 strings (both canaries, all card-bundle strings, run ids, scores,
digests, result commits, team / user / revision ids, `sig=`, `token=`, `eyJ`,
`sk-`, `API_KEY`, `RUN_TOKEN`, ...): no match except GitHub's own wording. No
artifacts, annotations or caches in any repository. The team's private log shows
the plain canary and the secret one redacted; inside the container there were no
`GITHUB_*` variables, no runner files, only the project's own process, and none of
the probed addresses was reachable. Public runs started 13–25 s after dispatch.

**2026-10-04: open egress runtime** (hackathon-survey26 #262) approved on all 13
public repositories (`6adf43a`, previous `e49c8db`) and all 13 private
`observer-control` repositories (`ops/github-installations.json`). Queued public
jobs were moved to the new commit in the same transaction as the approval.

**2026-10-04: egress route runtime** (hackathon-survey26 #301) approved on all 13
public repositories (`266caa0`, previous `6adf43a`); see `ops/egress-routes.md`.

**2026-10-05 (16:08 UTC 2026-10-04): evaluations without a model** (hackathon-survey26 #319,
`OBSERVER_MODEL_DISABLED`) approved on all 13 public repositories (`aa0faa6`, previous `266caa0`) and all 13
private `observer-control` repositories (`ops/github-installations.json`), after a canary on runner-1
(private and public) whose engine and score jobs succeeded.

**2026-10-04 21:07 UTC: transient-error backoff** (hackathon-survey26 #330: job API calls,
the sealed result PUT and signed downloads ride out network/429/5xx for about a minute; run
b9285236 had failed on a Storage 520) approved on all 13 public repositories (`33315a6`, previous
`aa0faa6`) and all 13 private `observer-control` repositories (`ops/github-installations.json`),
after a canary on runner-1 (private and public): its public engine and score jobs succeeded (no private job ran in the canary window; same files).
Queued public jobs were moved to the new commit in the same transaction.

## Updating the runtime

1. `python scripts/build-observer-control.py --public <new dir>` from the
   reviewed main commit, commit it to `observer-public` `main`, tag
   `observer-runtime-<commit sha>` and push both (SSH; the HTTPS token lacks the
   workflow scope).
2. `update private.observer_installations` is not involved; approve the commit
   with `update private.observer_public_pool set approved_sha='<sha>';` while
   no public job is in flight. Only the tag is ever dispatched.

## Deployment order

1. Migration `20261002000500_public_runner_pool.sql`
   (`scripts/deploy-observer-backend.py --apply`). Pool row absent = off.
2. Supabase secret `OBSERVER_RESULT_SEAL_KEY`: 32 random bytes, unpadded
   base64url, e.g.
   `python3 -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode())"`.
   Without it (or if it is malformed) the job API refuses every public-pool
   request; the private pool is unaffected. Rotating it only affects results
   in flight.
   The GitHub App installation on runner-12 must cover `observer-public`
   (`repository_selection=all` does) with `actions: write`. No organization or
   repository variable may turn on debug logging (`ACTIONS_STEP_DEBUG`,
   `ACTIONS_RUNNER_DEBUG`): debug lines would appear on the public page.
3. Edge functions `observer-job`, then `observer-dispatch`.
4. Pool row: `insert into private.observer_public_pool(organization,repository_id,organization_id,approved_sha)
   values('AGENTIC-OBSERVER26-runner-12','1401336826','334569349','<sha>');`
5. Drill (`drill` mode, a test team, a practice card), check the public run page,
   then `overflow` with the phases to cover.

## Checking

```sql
select runner, status, count(*) from private.observer_jobs
  where created_at > now() - interval '1 day' group by 1, 2;
select id, home_organization, public_run_id, status, error from private.observer_jobs
  where runner = 'public-hosted' order by created_at desc limit 20;
```

## Rescore jobs in the public repositories (2026-10-04)

Migration `20261004110000`. Score jobs (the independent rescore; no participant
code) may run in the public repositories through the second public workflow
`observer-score.yml`, so rescoring costs no private Actions minutes. Same
guarantees as engine jobs: dispatch input is the job id only, the claim is bound
to the dispatched run and sealed, the scenario and the run's result are sealed to
the job's in-memory key and staged as ciphertext (`sealed/<job>/scenario.zip`,
`sealed/<job>/trace.zip`, deleted when the job ends), the log is one status line
and the recomputed score goes back to the job API only. Placement as for engine
jobs (least busy healthy repository with a free slot, global caps); with no
healthy public repository, or a public dispatch that fails or does not start,
the job runs in its private organization as before. Hidden phases need
`sealed_transfer_verified`.

```sql
select public.observer_set_public_pool(p_drill_users=>array['<test user>']::uuid[]); -- stage: test teams' rescores
select public.observer_set_public_score_jobs(true);    -- everyone's rescores
select public.observer_set_public_score_jobs(false);   -- rollback
```

Safety net while private minutes run low: rescore sampling
(`observer_set_rescore_sampling(p_mode=>'auto'|'on'|'off')`). In `auto` it turns on
when the private organizations' remaining included minutes fall below 3000
(checked every minute, audited as `observer.rescore_sampling`); then only each
team's best run per phase and card plus a stable 20% are rescored, the rest keep
`score_check='pending'` and are rescored once sampling ends.
