> **Website repository:** `gosimfoundation/hackathon-survey26` · [Maintenance and publishing](MAINTAINING.md) · [Live site](https://create.gosim.org/survey26/)

# Agent Observer · 巡天智能体 — competition platform (challenge v3)

Event website and evaluation backend for the GOSIM "Agent Observer" hackathon. The site is a static Vue 3
app on GitHub Pages; everything stateful lives in one Supabase project; participant agents are executed by a
Python worker (GitHub Actions by default) against the **challenge v3** environment delivered by the science
team (`example3`: solar observing calendar, tile geometry with lunar quality, directional weather with hidden
events and uncertain forecasts, temporary observation requests, `challenge-score-v3`, JSON-Lines agent protocol
with one global wall clock).

```
web/                 Vue 3 + Vite + Tailwind 4 + supabase-js site (GitHub Pages)
supabase/            Postgres schema/RLS/RPC/storage migrations (v3 columns in 20260910001000_challenge_v3.sql) + the public leaderboard edge function; scoring runs in the worker
challenge/           the competition environment: vendored v3 modules (unchanged, relative imports) + the v4 engine (v4_*.py), scenario_builder, replay generator, reference scenario, tests
worker/              v3 evaluation worker: sandboxed agent runs (challenge_runner.py), scoring, replay upload, scenario seeding/admin CLI
project_platform/    complete-project platform: repo-URL/ZIP submissions snapshotted via a GitHub App and executed by a trusted job runner (docker workspace, session API, model adapters)
scoring/             standalone stage-one survey-decision scorer (standard library only, frozen CSV contract), imported by worker/
tests/               pytest: runner sandbox, starter kits, platform integration on an embedded Postgres + real PostgREST harness, browser e2e; hosted_smoke.py for the live project
docs/                organizer and participant documentation: competition-format.md, project-platform-rollout.md, example3-analysis-brief.md, ANOMALY_RELEASE_CHANGELOG_ZH.md
scripts/             one-off organizer scripts: phase/secrets/runner configuration, backend deploy, live tests, the release-site publisher
ops/                 the "Agentic Observer 2026 Evaluator" GitHub App manifest/installations and the control-workflow YAMLs dispatched for project jobs
legacy-event/        the former event website; still built by the site publisher to serve the /survey26/ event root page (which redirects to the platform)
archive/             old versions kept for reference: starter_kit_v3/ (v3 starter kit, still packaged into the practice download), starter_kit_v4/ (v4 starter kit, no longer published), legacy/ (first self-hosted FastAPI platform)
.github/workflows/   CI: tests.yml, the self-dispatching worker.yml evaluator, publish-site.yml (GitHub Pages deploy)
```

## Competition mechanics (as implemented)

| Topic | Behaviour |
|---|---|
| Scenario | directory `config/*.json` + `outputs/reference/*.csv`; stored in bucket `scenarios/<slug>/...`; per-file visibility flags (`weather_public`, `forecasts_public`, `events_public`); `global_wallclock_seconds` per scenario |
| Practice | scenarios fully public (incl. `weather_events.csv`) so local scoring reproduces the platform; results files (`decisions.csv`) only |
| Online competition | weather/forecasts/events hidden until the phase opens, then published automatically (pg_cron job `publish-open-phase-weather`); results files (`decisions.csv`) only; `tile_anomalies.csv` is never downloadable; score = mean over the phase's scenarios |
| Agent package (no longer accepted since 2026-09-24; kept for the evaluator's history) | zip with `minimal_agent.py`/`agent.py`/`main.py` at the root, optional `requirements.txt` (installed into a per-run venv), optional `.env` (model keys; loaded into the agent environment only, never logged); Python 3.12; network allowed (LLM APIs) |
| Isolation | agent runs in its own directory with a scrubbed environment, rlimits, process-group kill at the cutoff; the scenario directory is never mounted; docker mode (`SAC_SANDBOX_MODE=docker`) adds a read-only container |
| Scoring | `challenge/scoring_core.py` (unchanged from the science team) re-scores the committed `decisions.csv`; report v3 with breakdown, per-segment audit and input checksums |
| Anomaly reports | snapshots carry `tile_last_finished` (the realized score of the last finished exposure) and, after a correct fault report, a time-gated `fault_status`; `decision_response` may carry a `reports` array (`Instrument_Failure` / `NOVA` / `Reddening`). Accepted reports are flattened into `decisions.csv` as `report_*` action rows, so one file is the whole audit trail. Hidden per-tile tags settle at +100/−150, fault misreports beyond one free allowance cost 100 each. Details: [docs/ANOMALY_RELEASE_CHANGELOG_ZH.md](docs/ANOMALY_RELEASE_CHANGELOG_ZH.md) (Chinese) |
| Outcomes | `survey_complete`, `global_wallclock_expired`, `agent_error`, `agent_initialization_error` are all scored on what was committed plus terminal penalties (package semantics); only unreadable packages/files are `invalid` |
| Artifacts | per evaluation: `report.json`, `decisions.csv`, `agent.log`, `workflow_result.json`, `decision_replay.html` (organizer-style replay, generated by `challenge/replay.py`) in bucket `results/<team>/sub-<id>/<scenario>/` |
| Leaderboard | best scored submission per team; columns total, base science, program bonus, request reward, penalties, tiles, REQUIRED missing |

## Deploy (organizers)

1. Supabase: apply `supabase/migrations/*.sql` in order (SQL editor or `supabase db push`); Auth site URL and
   redirect URLs; auto-confirm sign-ups or configure SMTP.
2. Seed and promote the first admin (needs the service role key):
   `SUPABASE_URL=… SUPABASE_SERVICE_ROLE_KEY=… python -m worker.main seed`, `python -m worker.main promote-admin you@org`.
   Default seed: `demo-week` (7 nights, public, the copy shipped in the starter kit), `dev-reference` (180 nights,
   public), `dev-fortnight` (14 nights, public), `eval-a`/`eval-b` (30 nights, hidden, 3600 s wall clock) and
   phases `practice`/`online`. The practice complete-project board (phase `practice-projects`, 8 evaluations per
   team per day, team's own model key only) is created with `scripts/configure-observer-practice-projects.py`. Which phase accepts which submission route is recorded in
   [docs/competition-format.md](docs/competition-format.md).
3. More scenarios: `python -m worker.main gen-scenario --slug eval-c --seed 777 --days 30 --start-date 2026-10-05 --wallclock 3600 --hidden-weather --hidden-forecasts`
   or `add-scenario --root <dir>` for a directory produced by the science team. Scenarios are validated by the
   authoritative scorer and checksummed before upload.
4. Edge function: `supabase functions deploy leaderboard --no-verify-jwt --use-api`.
5. Worker: `.github/workflows/worker.yml` keeps one evaluator alive on a GitHub-hosted runner
   (`python -m worker.main run --max-seconds 19800`, polling every 5 s when busy and backing off to 30 s while
   idle, secrets `SUPABASE_URL` and
   `SUPABASE_SERVICE_ROLE_KEY`) and re-dispatches itself with the workflow token before it ends; the cron is only
   a backstop. It publishes a heartbeat (`site_settings.worker_heartbeat`) that the submission page shows with the
   queue position. Wall clocks of 1–2 h per scenario mean extra runners are advisable for the online phase:
   `python -m worker.main run` anywhere with Python 3.12, or the Docker image (`worker/Dockerfile`).
6. Website: GitHub Pages via `.github/workflows/deploy-pages.yml` (repo variables `VITE_SUPABASE_URL`,
   `VITE_SUPABASE_ANON_KEY`, `VITE_SITE_URL`, `VITE_BASE_PATH`).

## Tests

```bash
.venv/bin/pytest challenge/tests tests/test_challenge_runner.py tests/test_starter_kit.py   # environment, sandbox, kit
SAC_POSTGREST_BIN=/path/to/postgrest .venv/bin/pytest tests/supabase                        # RLS/RPC/worker on embedded Postgres + PostgREST
cd web && npm ci && npm run build && cd .. && .venv/bin/pytest tests/e2e_web                # browser e2e against the harness

SUPABASE_URL=… SUPABASE_ANON_KEY=… SUPABASE_SERVICE_ROLE_KEY=… python tests/hosted_smoke.py # live project, self-cleaning
python tests/hosted_agent_smoke.py --dispatch                                                # queue a minimal-agent zip on the hosted project and run it through the GitHub Actions worker
python tests/live_e2e.py                                                                     # browser walk-through of the production site (public pages, storage policy, submit, replay, admin)
python tests/live_cli_participant.py                                                         # command-line participant journey with the downloaded kit (fetch, run, score, pack, submit, mistakes)
```

`tests/e2e_web/` is four files: `test_site.py` walks the happy path (register → team → results file → agent
package → leaderboard → admin), `test_site_v2.py` / `test_site_v3.py` cover the console, replay and v3 report,
and `test_site_v4.py` runs five rounds over the same loop from other angles — sign-up validation and duplicate
e-mail, team membership gating a submission, what each phase accepts (kind, scenario visibility, extension,
daily limit), a package with no entry script plus the hidden-scenario file list, and both languages, mobile
navigation and `prefers-reduced-motion`.

The harness needs `pgserver`, `psycopg`, `playwright` (plus `playwright install chromium`) and a `postgrest`
binary. On macOS the release binary links against Homebrew's `libpq`; the copy inside `pgserver` works instead:

```bash
export SAC_POSTGREST_BIN=/path/to/postgrest SAC_NODE_BIN=$(dirname "$(command -v node)")
export DYLD_LIBRARY_PATH=$PWD/.venv/lib/python3.12/site-packages/pgserver/pginstall/lib
```

## Known deviations from the science team's package

- `challenge/run_challenge.py`: the agent transport writes non-blocking; the original blocking `os.write` of a
  message larger than the pipe buffer (the first decision snapshot is ~200 KB) could hang until the agent read it,
  defeating the global wall clock. Everything else in `challenge/` is the delivered code with relative imports and a
  root-parameterised `project_paths.py`.
- `challenge/scenario_builder.py` clamps `time_limited_window_days` and the forecast horizon for short scenarios.
- `challenge/replay.py` regenerates the delivered `decision_replay.html` (its generator was not in the package).
