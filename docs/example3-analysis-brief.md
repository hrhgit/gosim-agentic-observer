Analysis complete. Below is the engineering brief.

---

# Engineering brief: `example3` challenge package vs. the current Agent Observer platform

Paths (abbreviated below):
- **PKG** = `/Users/mac/Library/Application Support/CindyGlobal/owners/ae98ca1d7b2b6ae48f15/dialogues/2026-09-09/f286d255-69dd-408c-a206-ec1ca3e39544/inputs/example3/example3`
- **PLAT** = `/Users/mac/Library/Application Support/CindyGlobal/owners/ae98ca1d7b2b6ae48f15/dialogues/2026-09-09/f286d255-69dd-408c-a206-ec1ca3e39544/survey-agent-challenge`

## 0. TL;DR

- `example3` is a complete rewrite of the environment, not an increment: new time axis (solar calendar, 180 nights, 37–47 slots/night), new tile catalog (64 tiles, `REQUIRED`/`FLEXIBLE`, time-limited availability), directional weather with hidden `weather_events.csv`, uncertain forecasts, temporary observation requests, a new 7-column `decisions.csv`, a new score (`challenge-score-v3`: base + program bonus + request reward − 6 penalty classes incl. terminal penalties), and a new JSON-Lines protocol (`participant-agent-protocol-v1`) with **no per-decision timeout** and one **global wall clock (7200 s)**.
- Nothing in `PLAT/scoring/scorer.py`, `scoring/protocol.py`, `supabase/functions/_shared/scorer.ts`, `worker/runner.py`, `worker/scenarios.py`, the starter kit or the docs/UI content survives unchanged. What survives: auth/teams/phases/submissions/evaluations/leaderboard plumbing, storage buckets, worker claim loop, sandbox/zip handling, submission page skeleton.
- Package test suite: **52 passed, 6 subtests passed in 6.6 s** (`.venv/bin/python -m pytest tests -q`, Python 3.12.13). Stdlib only; `langgraph`/`langchain` are **not** installed in the venv and are not needed (tests and the deterministic agent fall back cleanly). No extra deps had to be installed (pytest 9.1.1 already present).
- Determinism verified: generators are byte-deterministic (tests), `decisions.csv` from two full deterministic minimal-agent runs are SHA-identical (`cc27367396cb76b6…`), and offline re-scoring of the shipped `outputs/live_week_validation/decisions.csv` reproduces `offline_score.json` exactly (score, actions, requests).
- Runtime: the deterministic minimal agent completes the **entire 180-night survey in ~12–13 s wall** (7,943 decisions, 90 exposures, 7,852 waits) and finishes all 64 tiles with zero terminal penalties (total 12,287.48). The LLM-backed validation (deepseek-v4-flash) did 7 nights / 265 decisions in 151.8 s (mean latency 0.56 s, max 14.7 s).
- Biggest platform impacts: (1) hidden-weather feeding must go through the workflow (no lookahead in the snapshot, but the scenario directory must never be mounted into the agent sandbox); (2) LLM agents need **outbound network + secrets in `.env`**, which the current `--network none` sandbox forbids; (3) the replay HTML is self-contained (252 KB, embedded JSON) but **its generator is not in the package**.

## 1. What the new package is

### 1.1 Layout and pipeline

```
config/*.json  ──►  src/observing_calendar.py generate      → outputs/reference/night_calendar.csv, slots.csv, calendar_metadata.json
                    src/tile_geometry_simulator.py generate → tiles.csv, targets.csv, catalog_metadata.json
                    src/tile_geometry_simulator.py windows  → tile_windows.csv (dev fixture, 3 days)
                    src/weather_simulator.py generate       → weather.csv, weather_forecasts.csv, weather_events.csv (HIDDEN), weather_metadata.json
                    src/observation_request_simulator.py generate → observation_requests.csv, observation_request_tiles.csv, observation_request_metadata.json
                    src/build_scenario_manifest.py          → scenario_manifest.json (sha256 + row counts)
src/run_challenge.py (ChallengeWorkflow + agent)            → <out>/decisions.csv, workflow_result.json
src/score_decisions.py <decisions.csv>                      → score_report.json  (authoritative replay, ChallengeScorer)
[missing script]                                            → decision_replay.html
```

Dependency direction (README): contracts → calendar → geometry → weather → requests → workflow → scorer. `src/contracts.py` holds every frozen column list (`NIGHT_COLUMNS`, `SLOT_COLUMNS`, `TILE_COLUMNS`, `TARGET_COLUMNS`, `TILE_WINDOW_COLUMNS`, `WEATHER_COLUMNS`, `FORECAST_COLUMNS`, `EVENT_COLUMNS`, `REQUEST_COLUMNS`, `REQUEST_TILE_COLUMNS`, `DECISION_COLUMNS`) plus `read_exact_csv` (exact ordered header required, `utf-8-sig`), `write_exact_csv`, `parse_utc`/`format_utc` (`YYYY-MM-DDTHH:MM:SSZ`), `parse_bool` (`true`/`false` only), `sha256_file`.

All scripts hard-code the package root through `src/project_paths.py` (`EXAMPLE3_ROOT`, `CONFIG_DIR`, `REFERENCE_OUTPUT_DIR = outputs/reference`). Only stdlib is used by `src/` and `tests/`.

### 1.2 Entry points / CLI

| Script | Command | Notes |
|---|---|---|
| `src/observing_calendar.py` | `generate` / `validate` (`--config`, `--output-dir`) | |
| `src/tile_geometry_simulator.py` | `generate` / `windows --date D --days N [--output]` / `geometry --tile-id --timestamp-utc` | |
| `src/weather_simulator.py` | `generate` / `current --slot-id [--tile-id]` / `forecast --as-of-utc [--days]` | |
| `src/observation_request_simulator.py` | `generate` / `current --as-of-utc [--include-expired]` | |
| `src/run_challenge.py` | `[--wallclock-seconds F] [--seed 11] [--output-dir outputs/workflow_reference] [--agent-command CMD...]` | `--agent-command` is `argparse.REMAINDER`; without it an in-process seeded random `ReferenceAgent` is used |
| `src/score_decisions.py` | `DECISIONS [--output outputs/reference/score_report.json] [--termination-reason trace_complete]` | root fixed to `EXAMPLE3_ROOT`; the library function `scoring_core.score_files(root, decisions, output, termination)` accepts a root |
| `src/build_scenario_manifest.py`, `src/build_implementation_canvas.py [--check]` | | manifest / Obsidian canvas |
| `participant_agent/minimal_agent.py` | run as the agent subprocess | reads `participant_agent/.env`; adds `../src` to `sys.path` to import `scoring_preview` |

### 1.3 Configs (`config/`)

- `scenario_config.json` (`example3-scenario-v2`): `scenario_id`, `seed` 20260909, `competition.global_wallclock_seconds` 7200, `competition.per_decision_timeout` null, lists other config files.
- `calendar_config.json` (`observing-calendar-v1`): `survey.start_date` 2026-09-07, `days` 180, `slot_seconds` 900; `site` lat 31.9634 / lon −111.599 / `utc_offset_hours` −7 / `sun_altitude_limit_deg` −12.
- `tile_config.json` (`tile-geometry-v2`): `seed`, `geometry.minimum_altitude_deg` 30, `catalog` (8 regions × 8 tiles, 2 REQUIRED per region of which 1 time-limited for 14 days, exptime choices 450/600/900/1200/1350), `lunar_model` (decay 35°, `maximum_penalty` 0.75), `target_models` per class (`science_weight` LRG 1.0, ELG 1.0, QSO 1.7, BGS 0.45).
- `weather_config.json` (`directional-weather-v1`): quality processes, `background_closure`, `forecast` (7-day horizon, 12% miss, 6 false positives, daily revisions), `events` (rainy/cloudy/smoggy/rocket_launch/cold_wave/tornado with scope weights, `force_close`, multipliers), `score_interface` (`airmass_exponent` 1.0, `maximum_weather_quality` 3.0, formula string).
- `request_config.json` (`observation-requests-v1`): issue every 7 nights, p=0.55, 1–3 tiles, deadline classes ONE_WEEK/TWO_WEEKS/ONE_MONTH, `ALL`/`AT_LEAST_N`, reward 140 and miss penalty 190 per required tile.
- `score_config.json` (`challenge-score-v3`): thresholds dark 0.65 / bright 0.40; `program_bonus` DARK 0.25 / BRIGHT 0.15 / BACKUP 0.08; `penalties` unsafe_observation 2000, invalid_action 100, avoidable_wait_per_second 0.001, required_miss 1000, flexible_shortfall_per_tile 100; `flexible_quota_per_region` 4; `coefficient_status: "provisional organizer calibration values"`.
- `workflow_config.json` (`challenge-workflow-v1`): `global_wallclock_seconds` 7200, `weekly_horizon_days` 7, `tile_window_horizon_days` 7, `per_decision_timeout_seconds` null, `synthetic_timeout_action` null (`load_workflow_config` **raises** if either is non-null).

### 1.4 `challenge_workflow` run modes

`ChallengeWorkflow(root=EXAMPLE3_ROOT, clock=time.monotonic)` loads `ChallengeScorer.from_files(root)`, `ObservationRequestSimulator`, `targets.csv`. `run(provider, wallclock_seconds=None)`:

1. If `provider.publish_initial` exists it is called with `initial_publication()` **before the clock starts** (transport gives it a separate 30 s `initialization_timeout_seconds`; failure → `termination_reason="agent_initialization_error"`, score finalized with zero actions).
2. Loop while `scorer.current_slot()` is not None: build `decision_snapshot(sequence)`, call `provider(snapshot, deadline_monotonic)`, parse with `_decision_from_response`, `scorer.apply_decision(decision)`, append to `commit_log`.
3. Termination reasons: `survey_complete`, `global_wallclock_expired` (an in-flight response finishing at/after the cutoff is discarded, `ignored_in_flight_response=true`), `agent_error` (any exception incl. agent exit or malformed response; loop stops, score finalized with what was committed).
4. Two providers exist: in-process `ReferenceAgent(seed)` (random feasible policy) and `JsonLineAgentProcess(command)` (persistent subprocess, `select()`-driven raw-fd I/O, kills the child at the deadline; stderr inherited; **environment inherited, no cwd change, no resource limits**).

There is no per-decision timeout, no max-steps, no synthetic fallback action; the only cutoff is the global wall clock (`accounted_wallclock_seconds` reported). The clock runs during snapshot serialization + agent think time + parsing.

### 1.5 Participant protocol (exact schemas)

Envelopes (`participant_agent/protocol.py`, `src/contracts.py`): `PARTICIPANT_PROTOCOL_VERSION="participant-agent-protocol-v1"`, `INITIAL_PUBLICATION_VERSION="initial-publication-v2"`, `DECISION_SNAPSHOT_VERSION="decision-snapshot-v2"`, `WORKFLOW_RESULT_VERSION="workflow-result-v2"`. One JSON object per line on stdin/stdout, compact separators, `ensure_ascii=False`.

**platform → agent `initialize`** (once, no reply expected; measured **2.29 MB**):
```
{"protocol_version":"participant-agent-protocol-v1","message_type":"initialize","payload":{
  "schema_version":"initial-publication-v2",
  "calendar":{"first_night","last_night","night_count":180,"slot_count":7928,"slot_duration_seconds":900},
  "site":{"latitude_deg","longitude_deg","utc_offset_hours","sun_altitude_limit_deg"},
  "tile_catalog":{"tile_count":64,"required_tile_ids":[...],"region_ids":["R00".."R07"],
                  "tiles":[{<TILE_COLUMNS as strings>, "tile_science_value":211.8}, ...]},
  "target_catalog":[{<TARGET_COLUMNS as strings>} x 15833],
  "scoring_contract":{"score_config":<score_config.json>,"weather_score_interface":<weather_config.score_interface>,
                      "lunar_model":<tile_config.lunar_model>,"preview_semantics":"..."},
  "global_wallclock_seconds":7200.0}}
```

**platform → agent `decision_request`** (per decision; ~18 KB mid-night, ~200 KB at a night/week boundary):
```
{"protocol_version":...,"message_type":"decision_request","decision_sequence":N,"payload":{
  "schema_version":"decision-snapshot-v2","decision_sequence":N,
  "cursor":{"slot_id":"N20260907-S001","night_id":"N20260907","timestamp_utc":"...Z","slot_offset_seconds":0},
  "current_site_weather":{"slot_id","night_id","timestamp_utc","duration_seconds","is_observable",
                          "seeing_arcsec","transparency","sky_quality","instrument_efficiency","tile_id":null,"active_event_ids":[]},
  "candidate_tiles":[{"tile_id","region_id","scheduling_class","nominal_exptime_seconds","tile_science_value",
                      "window_start_utc","window_end_utc",
                      "geometry":{"tile_id","timestamp_utc","altitude_deg","azimuth_deg","hour_angle_deg","airmass","moon_separation_deg","lunar_quality_factor"},
                      "effective_weather":{<same keys as current_site_weather, tile-effective, "tile_id":"T000xx">},
                      "already_completed":false}, ...],
  "active_requests":[{<REQUEST_COLUMNS>, "tile_requirements":[{"tile_id","required_visits","completed_visits","remaining_visits"}],
                      "satisfied_tile_count","is_complete"}],
  "night_start": null | {"night":{<NIGHT_COLUMNS>},"tile_windows":[{<TILE_WINDOW_COLUMNS>} for tonight]},   // first slot of each night
  "weekly": null | {"issued_at_utc","weather_forecast":[{<FORECAST_COLUMNS>, latest revision as-of now}],
                    "tile_windows":[... 7 days ...],"observation_requests":[...]},                    // every 7th night, first slot
  "progress":{"completed_tile_ids":[...],"flexible_completed_by_region":{"R00":n,...}}}}
```
Candidates = tiles whose tonight window (`get_tile_windows`, windows ≥ exptime, altitude ≥ 30°, inside `available_from/until`) contains the cursor and whose instantaneous altitude ≥ 30°. Candidates may still be unable to finish before `window_end_utc`; `scoring_preview.preview_actions` filters those.

**agent → platform `decision_response`**:
```
{"protocol_version":"participant-agent-protocol-v1","message_type":"decision_response","decision_sequence":N,
 "action":"observe"|"wait","tile_id":"T00057","program":"DARK"|"BRIGHT"|"BACKUP","request_id":""|"RQ0001",
 "reason":"...","decision_source":"deterministic"|"model"}
```
`ChallengeWorkflow._decision_from_response` only checks `protocol_version`/`message_type`/`decision_sequence` **if present**; `action` must be observe|wait (else `agent_error`); `program`/`tile_id`/`request_id` are not validated there (the scorer turns bad values into penalized invalid actions). `wait` blanks the other fields. Decision ids are generated as `D{sequence:06d}`; `slot_id` is the current cursor slot. There is no `end` message; the process is closed (`terminate`, then `kill`) after the run.

Reading a snapshot never advances time; only a committed action does. A short exposure can be followed by another action in the same `slot_id` (cursor has `slot_offset_seconds`).

### 1.6 `decisions.csv`

`DECISION_COLUMNS = decision_id,slot_id,action,tile_id,program,request_id,reason` (exact header order). `scoring_core.load_decisions`: `decision_id` non-empty and unique (any string; not required to be numeric or increasing); `action` ∈ {observe, wait}; `wait` must have empty tile/program/request; `observe` needs non-empty `tile_id` and `program` ∈ {DARK, BRIGHT, BACKUP}. Everything else is scored, not rejected: unknown `slot_id` → `unknown_slot` (invalid_action penalty), slot earlier than cursor → `stale_decision` (100, no time consumed), slot later than cursor → implicit waits are inserted (`_consume_until`). Malformed files raise a bare `ValueError` (no structured `invalid_submission` report as in the old scorer).

### 1.7 Score formula (`src/scoring_core.py`, `ChallengeScorer`)

- `V_tile = Σ science_weight` over `targets.csv` rows of the tile (`load_tile_values`; published as `tile_science_value`).
- Exposure runs from the cursor for `nominal_exptime_seconds`, split at slot boundaries. Per segment (midpoint geometry):
  `A_atm = min(instrument_efficiency·transparency·sky_quality / (seeing_arcsec·airmass^airmass_exponent), maximum_weather_quality)` (`weather_simulator.weather_quality`, using `get_effective_conditions(slot, tile)` which applies directional events);
  `combined = A_atm · lunar_quality_factor`; band = DARK if ≥0.65, BRIGHT if ≥0.40 else BACKUP;
  `base = V_tile · seconds/exptime · combined`; `bonus = base · program_bonus[program]` iff `program == band`.
- Science is credited only when the exposure completes (`interrupted_exposure_science_score = 0`). Outcomes: `completed`; `weather_interrupted` (later segment closed: no science, no penalty); `geometry_or_night_interrupted` (tile sets / night ends: no science + `invalid_action` 100); `unsafe_observation` (closed at start: 2000 + rest of slot consumed); `invalid_observe` / `invalid_request_tag` / `duplicate_tile` / `outside_tile_window` / `unknown_slot` (100 + rest of slot consumed); `stale_decision` (100); `wait`.
- Ordinary science credit once per tile (`one_ordinary_credit_per_tile`); a request-tagged revisit of a completed tile scores 0 but counts a visit. `request_id` must exist, contain the tile, and the start must lie in `[available_from_utc, deadline_utc)`.
- Waits: `wait` consumes the rest of the slot; it is "avoidable" (0.001/s ≈ 0.9 per slot) iff some uncompleted tile could be completed from now (`_has_actionable_tile` – this peeks at truth, scorer-internal only).
- Terminal (`finalize(termination_reason)`): `required_miss` = 1000 × uncompleted REQUIRED tiles (16 in the reference catalog, 8 of them only available for 14 days); `flexible_shortfall` = 100 × Σ_region max(0, 4 − completed FLEXIBLE); requests issued before the final cursor: completed → `+completion_reward`; expired & incomplete → `miss_penalty` unless `excused_unobservable` (fewer feasible tiles than required, judged on truth via `_tile_has_request_opportunity`).
- `total = base_science + program_bonus + request_reward − Σ penalties`. Terminal penalties are applied even to truncated runs (`validation_summary.partial_score_note` acknowledges this).

### 1.8 Outputs

- `workflow_result.json` (`workflow-result-v2`): `initial_publication`, `termination_reason`, `global_wallclock_seconds`, `accounted_wallclock_seconds`, `ignored_in_flight_response`, `committed_action_count`, `commit_log[{sequence, committed, completed_wallclock_seconds, decision_id, outcome | error}]`, `score_report`. 3.7–4.0 MB because it embeds the 2.3 MB initial publication.
- `score_report.json` / `offline_score.json` (`score-report-v3`, from `score_decisions.py`): `score{total, base_science, program_bonus, request_reward, penalties{...}}`, `final_cursor`, `completion{completed_tiles, required_missing, flexible_by_region, flexible_shortfall}`, `requests[{request_id,status,satisfied_tile_count,required_tile_count,feasible_tile_count,reward,penalty}]`, `wait_seconds{explicit,implicit,invalid,avoidable,unavailable}`, `actions[{decision_id,slot_id,action,tile_id,program,request_id,start_utc,elapsed_seconds,outcome,base_science_score,program_bonus_score,penalty,segments[{slot_id,start_utc,duration_seconds,airmass,active_event_ids,atmospheric_quality,lunar_quality_factor,combined_quality,quality_band,program_matched,base_science_score,program_bonus_score}]}]`, `parameters`, `input_sha256` (14 governed inputs incl. `events`).
- `validation_summary.json` (`live-week-validation-v1`, only in `live_week_validation/`): provider/model, latency stats, `decision_sources`, outcomes, partial score.

### 1.9 Visualization (`outputs/live_week_validation/decision_replay.html`)

- 252,084 bytes, 134 lines, one file. Exactly one inline `<script>`; **no** external URLs, fonts, CDN, `fetch`, `import`, or `JSON.parse`. Fully self-contained and safe to serve from a private bucket / sandboxed iframe.
- Structure: `<style>` (cyberpunk "ASTRA" theme) + DOM (`#sky` canvas, weather bars, forecast list, action/score panels, play/scrub/speed) + `const DATA = {...}` (232,490 bytes of JSON) + ~9.7 KB vanilla JS (canvas 2D horizon projection using the same GMST formula as the scorer, animated clouds, playback).
- `DATA` keys: `meta{title,round_count,night_count,tile_count}`, `site`, `tiles[64]{tile_id,ra_deg,dec_deg,region_id,scheduling_class,nominal_exptime_seconds}`, `nights{night_id:{tile_status[{tile_id,is_available_tonight,windows[]}], forecast_snapshot{as_of_utc,horizon_days,forecast_count}, forecasts[]}}`, `events{event_id:{condition,regions,severity}}` (all 29 events, anonymized: no timestamps), `rounds[265]{round_id,decision_time_utc,slot_id,night_id,weather{…,active_event_ids,regional_overrides},decision{action,tile_id,program,reason},transition{start_time_utc,end_time_utc,elapsed_seconds,outcome,next_slot_id},score{outcome,base_science_score,program_bonus_score,penalty}}`, `score_summary`.
- It is a **template + embedded JSON**, derived from `workflow_result.json` (actions/segments), per-night `get_tile_windows`, forecasts and `weather_events.csv`. **The generator script is not in the package** (no `src/` file produces it; grep finds "replay" only in docstrings). Likewise the `validation_horizon_reached` termination and the extra `commit_log` fields (`latency_seconds`, `decision_source`, `night_id`) in `live_week_validation/workflow_result.json` come from an unreleased harness variant.

### 1.10 Runtime measurements (this Mac, venv Python 3.12.13)

| Run | Result |
|---|---|
| `run_challenge.py --wallclock-seconds 60 --agent-command <venv python> -B participant_agent/minimal_agent.py` (deterministic) | `survey_complete` in 12.7 s / 12.0 s (two runs), 7,943 decisions, 90 completed exposures, 7,852 waits, all 64 tiles done, 9 requests completed, total **12,287.48**, identical `decisions.csv` both runs |
| `ChallengeWorkflow()` load | 0.09 s; snapshot build 0.12 s at a week boundary, negligible otherwise |
| Shipped `outputs/workflow_reference` (ReferenceAgent, `--wallclock-seconds 2`) | 638 actions before `global_wallclock_expired`, total 589.484228 (7 REQUIRED missed); the single `decisions.csv` is the whole audit trail — `report_*` rows live in it, there is no separate `report.csv` |
| Shipped `live_week_validation` (deepseek-v4-flash, 7 nights) | 265 decisions (47 model / 218 deterministic waits), 151.8 s, mean 0.56 s, max 14.7 s, total −1,939.05 (9 REQUIRED still missing) |

Extrapolation for an LLM agent over 180 nights: ~7,900 decisions, but the LLM is invoked only when candidates exist (~100–1,200 calls) → roughly 15–60 min, inside the 7,200 s budget. Note the catalog is tiny relative to the calendar: a good agent finishes all 64 tiles in about 3 weeks and idles for 160 nights.

## 2. Exact differences vs. the contract the platform implements

| Area | Previous (`stage1-score-v1`, observer-v1) | New (`example3`) |
|---|---|---|
| `weather.csv` | `slot_id,night_id,timestamp_utc,duration_seconds,seeing_arcsec,transparency,sky_brightness,is_observable`; fixed `slot_seconds`; `sky_brightness` lower = better (divides) | `slot_id,night_id,timestamp_utc,duration_seconds,is_observable,seeing_arcsec,transparency,sky_quality,instrument_efficiency`; quality fields **empty** when `is_observable=false`; `sky_quality` higher = better (multiplies); site baseline only — tile-effective weather also needs hidden `weather_events.csv` |
| `tiles.csv` | `tile_id,ra_deg,dec_deg,program,region,priority,nominal_exptime_seconds,n_lrg,n_elg,n_qso,n_bgs`; numeric ids (`200000`), `region` int 0–7, fixed per-tile `program`, `priority` 0–10 | `tile_id,ra_deg,dec_deg,nominal_exptime_seconds,region_id,scheduling_class,available_from_utc,available_until_utc,n_lrg,n_elg,n_qso,n_bgs`; ids `T00001`, `region_id` `R00`, `scheduling_class` REQUIRED/FLEXIBLE, no fixed program (agent chooses program to match the quality band), time-limited availability |
| `decisions.csv` | 6 cols, `decision_id` int strictly increasing, chronological slots enforced (else invalid submission) | 7 cols with `request_id`; `decision_id` any unique string; non-chronological rows penalized, not rejected |
| Extra inputs | none | `night_calendar.csv`, `slots.csv` (time axis), `targets.csv` (15,833 rows, defines `V_tile`), `tile_windows.csv` (fixture), `weather_forecasts.csv` (public, as-of), `weather_events.csv` (**hidden truth**), `observation_requests.csv` + `observation_request_tiles.csv`, 6 config JSONs, `scenario_manifest.json` |
| Slot/night ids | `N01-S001` / `N01` | `N20260907-S001` / `N20260907`; 37–47 slots per night |
| Tile value | counts × weights × flux factor + priority factor | Σ per-target `science_weight` from `targets.csv` |
| Quality | `transparency/(seeing·sky_brightness·airmass)`, thresholds 0.30/0.14 | `eff·transp·sky/(seeing·airmass)` capped 3.0, × lunar factor, thresholds 0.65/0.40 |
| Score | `science − 0.02·(idle+invalid+unproductive seconds)`; partial exposure segments score | 6 penalty classes + request rewards + terminal penalties; only completed exposures score; unsafe observe −2000 |
| Report | `score, science_score, waste_penalty, completed_tiles, invalid_actions, region_completion, actions[]` | `score{total,...}`, `completion{...}`, `requests[]`, `wait_seconds{}`, `actions[].segments[]`, `input_sha256` |
| Protocol | `init` / `step` / `end`, `type` field; step includes `forecast` = next 4 slots of **true** weather (lookahead) and `available_tiles` with `expected_gain` | `initialize` / `decision_request` / `decision_response`, `protocol_version` + `message_type` + `decision_sequence`; no future weather; forecasts are uncertain, as-of, revised daily; agent does its own preview (`scoring_preview.py`) |
| Timing | 20 s/step, 600 s/scenario, 50,000 steps max, step timeout → `AgentRunError` | single global clock 7,200 s; init 30 s; no per-decision limit; cutoff kills the process and keeps committed actions |
| Agent runtime | stdlib only, no network, `python -I -B agent.py` | minimal agent optionally uses LangChain/LangGraph + provider SDKs, `.env` secrets, outbound HTTPS to OpenAI/Anthropic/DeepSeek/etc. Deterministic mode needs nothing |
| Determinism | seeded generator | seeded (20260909 + module offsets), byte-identical outputs; wallclock makes committed count machine-dependent |
| Scale | 7 nights × 24 slots, 240 tiles | 180 nights, 7,928 slots, 64 tiles, 18 requests, 29 events |

Python deps (`participant_agent/requirements.txt`): `langchain>=1.0,<2`, `langgraph>=1.0,<2`, `langchain-openai`, `langchain-anthropic`, `python-dotenv`, `socksio`. Providers (`model_factory.py`): `openai`/`chatgpt` (Responses or chat API), `anthropic`/`claude` (native), and OpenAI-compatible profiles `xai`/`grok`, `zai`/`glm`, `deepseek`, `moonshot`/`kimi`, `dashscope`/`qwen`, `minimax` (need `MODEL_BASE_URL`). Keys via `<PROVIDER>_API_KEY` or `MODEL_API_KEY_ENV`. `MODEL_PROVIDER=deterministic` (default) → no packages, no network, no keys. The LLM is asked to pick one of the top-K (`LLM_TOP_K_CANDIDATES`=12) preview candidates; any invalid/hallucinated answer falls back to the deterministic best.

## 3. Feasibility per competition mode

### (a) Participants download CSVs, run locally, upload `decisions.csv`

Feasible and cheap: `scoring_core.score_files(root, decisions, out, termination)` is pure stdlib and re-scores 800+ actions in well under a second; the report is deterministic. Requirements:
- The scenario "download" must include everything `ChallengeScorer.from_files(root)` needs **except** what must stay hidden: `config/{calendar,tile,weather,request,score}_config.json`, `outputs/reference/{night_calendar,slots,tiles,targets,weather,weather_forecasts,observation_requests,observation_request_tiles}.csv`. `weather_events.csv` is required by the scorer, so participants scoring locally **need** it (or a version of `weather.csv` that already has tile-effective conditions baked in, which the file format cannot express because events are per-tile).
- Lookahead: with `weather.csv` public, the participant knows all future site weather (same as the old "public weather" practice mode). With it hidden, blind `decisions.csv` submissions are hopeless (−2000 per observe into a closed slot; 393 of 7,928 slots are closed, plus directional closures).
- Conclusion: mode (a) works only for **practice on fully public scenarios** (weather + events). It cannot be the official mode with hidden weather. Recommendation: keep `results` submissions for practice phases; hide events only on evaluation scenarios.

### (b) Participants upload agent + environment; platform runs it

Feasible with the workflow as the runner, but several things must change:
- **Invocation**: use `ChallengeWorkflow(root=<scenario_dir>)` + `JsonLineAgentProcess(cmd)` programmatically (or add `--root` to `run_challenge.py`). The scenario dir must mirror `config/` + `outputs/reference/`.
- **Hidden weather**: fed by the workflow from the scenario dir; the agent only ever sees `decision_snapshot` (current slot conditions, as-of forecasts, geometry windows, requests) and the initial catalog. **No lookahead leakage** in the messages (verified: no `weather_events`, no future slots; `active_event_ids` only reveal currently active events). Leakage risk is purely filesystem: the agent process must **not** be able to read the scenario dir (today's runner runs the agent with `cwd=workdir` and, in docker mode, mounts only `workdir` read-only — keep that; `run_challenge.py`'s transport inherits cwd/env and must not be used as-is).
- **Network / LLM**: any non-deterministic agent needs outbound HTTPS to the provider and its API key. Current sandbox is `--network none` (docker) / unenforced (subprocess). Options: allow egress only to an allow-list of provider hosts via a proxy (`HTTPS_PROXY`, LangChain honours it; `socksio` is in requirements for that reason), or route every call through an organizer-owned LLM gateway with per-team credits (the platform already has a `redeem_codes` / credits feature in migrations `..000800/000900`, which suggests this was the plan). Deterministic agents need nothing.
- **Environment**: `requirements.txt` + `.env` in the zip is realistic; a literal "virtual environment" upload is not (venvs are OS/arch/interpreter-specific and tens of MB; the current cap is 50 MB and symlinks are rejected). Recommend: a fixed platform image with the pinned `participant_agent/requirements.txt` stack pre-installed (build once with network), optional `pip install -r requirements.txt --no-deps` into a per-submission venv with network only during install, `.env` loaded from the zip root (or `participant_agent/.env`) into the agent's env only — never into logs (stderr is uploaded as `agent.log`; the minimal agent prints only `provider=`).
- **Timeouts**: none per decision; enforce the global clock (`--wallclock-seconds`, worker default should be the phase's `global_wallclock_seconds`, 7,200 s) and the 30 s init timeout. A stuck agent burns the whole budget and is then scored on what it committed plus terminal penalties (`required_miss` etc.) — that is the package's intended semantics. Worker occupancy is up to 2 h per (submission × scenario); the GitHub-Actions worker (`.github/workflows/worker.yml`, 330 min job) can hold ~2 runs per job, so a dedicated runner is advisable.
- **Per-run cost**: initial message 2.3 MB; snapshots ~18 KB (200 KB at boundaries); ~7,900 round-trips; workflow overhead ~12 s total.
- **Agent packaging detail**: `minimal_agent.py` does `sys.path.insert(0, EXAMPLE3_ROOT/"src")` to import `scoring_preview` (which itself is stdlib-only and self-contained). Ship `scoring_preview.py` inside the starter kit agent folder instead of relying on `../src`, otherwise the platform would have to mount `src/` (harmless) next to the agent.

## 4. Integration plan for the platform

**Vendor into `PLAT/challenge/` (new package, keep `scoring/` for the legacy scorer until cut-over):** `src/contracts.py`, `project_paths.py` (replace with a `root`-parameterized version), `observing_calendar.py`, `tile_geometry_simulator.py`, `weather_simulator.py`, `observation_request_simulator.py`, `scoring_core.py`, `scoring_preview.py`, `challenge_workflow.py`, `run_challenge.py` (only `JsonLineAgentProcess`/`ReferenceAgent`), `score_decisions.py` (as a function with `root`), `build_scenario_manifest.py`. Vendor `tests/test_scoring_core.py`, `test_challenge_workflow.py`, `test_participant_protocol.py`, `test_contracts.py` and the fixture. Drop `build_implementation_canvas.py`/canvas tests. Keep the modules unmodified except for root parameterization so the package's own checksums/tests stay valid.

**Worker (`worker/main.py`, `worker/runner.py`, `worker/scoring.py`, `worker/scenarios.py`):**
- `fetch_scenario_files`: download the full scenario file set (`config/*.json` + `outputs/reference/*.csv`, ~2.4 MB) into `data/scenarios/<slug>/` preserving the `config/` and `outputs/reference/` layout.
- `results` kind: `challenge.scoring_core.score_files(root, decisions, report_path, termination_reason="trace_complete")`; wrap `ValueError` from `load_decisions`/`read_exact_csv` into `InvalidSubmission`.
- `agent` kind: replace `run_agent` with a `ChallengeWorkflow(root)` run over a hardened `JsonLineAgentProcess` (keep `Sandbox`'s env scrubbing, `setsid`, rlimits, docker mode, stdout cap; replace the `threading` reader with the package's `select` loop or keep the queue reader and implement the global deadline on top). Write `decisions.csv` + `workflow_result.json` (strip `initial_publication` before upload, or upload separately) and then re-score with `score_files` for the official `report.json`. Map `termination_reason` → evaluation status (see open decisions).
- `derive_metrics`: new metric set from `score_report.score` + `completion` + `requests` (below).
- `validate_scenario_files`: call `ChallengeScorer.from_files(root)` and `build_scenario_manifest.build`; `gen-scenario` = run the four generators with a seed (all accept `--config`/`--output-dir`; seeds live in the config JSONs, so write per-scenario configs).

**Edge function `score-results` (`supabase/functions/_shared/scorer.ts`)**: the TS port is now obsolete. Porting `scoring_core.py` + geometry (sun/moon models, lunar factor) + directional weather to Deno is a multi-thousand-line effort with parity risk. Recommend: **retire in-function scoring**; let the Python worker score `results` submissions too (`SAC_WORKER_KINDS=agent,results`), keep the webhook only as a queue nudge. Keep `leaderboard/index.ts`.

**DB / storage:**
- `scenarios`: add `global_wallclock_seconds int`, `manifest jsonb` (from `scenario_manifest.json`), `events_public bool` (alongside `weather_public`, `tiles_public`), `n_requests int`; `n_slots/n_nights/n_tiles` stay.
- `submissions`/`evaluations`: replace `science_score/completion/uniformity` semantics: add `base_science`, `program_bonus`, `request_reward`, `penalty_total`, `required_missing int`, `flexible_shortfall int`, `completed_tiles int`, `termination_reason text`, `accounted_wallclock_seconds`, `replay_path text`; keep `score`. Update `leaderboard()` return columns accordingly (currently returns `science_score, completion_rate, uniformity_score`).
- Storage: `scenarios/<slug>/config/*.json` + `scenarios/<slug>/outputs/reference/*.csv`; extend the `scenario files read` policy (`20260909000400_storage_policy_fix.sql`) to per-file flags (config + calendar/slots/tiles/targets/requests always readable; `weather.csv`+`weather_forecasts.csv` iff `weather_public`; `weather_events.csv` iff `events_public`). `results/<team>/sub-<id>/<slug>/{report.json,decisions.csv,agent.log,workflow_result.json,decision_replay.html}`.

**Replay HTML hosting:** generate per evaluation in the worker (the generator must be obtained from the colleague or rewritten from the `DATA` schema in §1.9: rounds from `report.actions[]`, tile status from `get_tile_windows`, forecasts from `get_weather_forecast`, events anonymized), upload to the results bucket, store `evaluations.replay_path`, and in `web/src/pages/SubmissionDetailPage.vue` add a "Replay" button that `readObjectText('results', ev.replay_path)` and renders it in an `<iframe sandbox="allow-scripts" srcdoc=...>` (self-contained, no network needed; 250 KB per evaluation). Do not embed organizer event timestamps; the current generator only embeds `{condition, regions, severity}`.

**Web:** `SubmissionDetailPage.vue` expects `actions[].valid/message/unproductive_seconds/science_score` and `region_completion` — rewrite against `score-report-v3` (`outcome`, `penalty`, `base_science_score`, `segments`, `completion.flexible_by_region`, `requests`). `ObservedSkyMap.vue`/`lib/skymap.ts` parse `tiles.csv` columns `program` → switch to `scheduling_class`. `SubmitPage.vue` accept `.zip` only for agents (a bare `.py` cannot carry `.env`/deps). Rewrite `content/docs.*.md`, `rules.*.md`, `components/docs/ProtocolExplorer.vue`, `content/demo/protocol-sample.json`, `demo/replay.json`.

**Starter kit:** replace with `participant_agent/*` (+ `scoring_preview.py`, `contracts.py`), a `local_runner.py` wrapper around `ChallengeWorkflow`/`JsonLineAgentProcess`, the public reference scenario (with events, for practice), `score_decisions.py`, `sac_submit.py` (unchanged), updated `SKILL.md`/`README.md`. Remove the "stdlib only, no network, 20 s per decision" text.

**Stays the same:** Supabase auth/profiles/teams/invites, `phases`/`phase_scenarios`, `create_submission` (add `.zip`-only check for agents if desired), `claim_submission`/`requeue_stale`, worker loop and zip extraction (`prepare_agent_dir`, zip-slip/symlink checks; entry candidates should add `minimal_agent.py`/`agent.py`), results bucket policies, announcements/admin, GitHub Pages deploy.

## 5. Unclear / inconsistent items in the package

1. **Missing tooling**: no generator for `decision_replay.html`; no script producing `validation_summary.json`, `termination_reason="validation_horizon_reached"`, or the extra `commit_log` fields (`latency_seconds`, `decision_source`, `night_id`) — `challenge_workflow.py` cannot produce them. `live_week_validation/workflow_result.json` has `global_wallclock_seconds: null`, which `ChallengeWorkflow.run` never emits.
2. `outputs/reference/score_report.json` and `outputs/workflow_reference/*` are a **2-second truncated** ReferenceAgent run (`global_wallclock_expired` at night 19, 638 actions, total 589.484228, 7 REQUIRED missed), not a reference solution. Their whole audit trail is the one `decisions.csv` (accepted reports, if any, are `report_*` rows in it); no separate `report.csv` is written.
3. `participant_agent/.env_example` is a duplicate of `.env.example` (differs only by a trailing blank line).
4. `tile_windows.csv` in `outputs/reference` is a 3-day sample (135 rows) but is listed in `scenario_manifest.json`; the scorer/workflow never read it (they call `get_tile_windows` live).
5. `scenario_config.json` uses `competition.per_decision_timeout`; `workflow_config.json` uses `per_decision_timeout_seconds` + `synthetic_timeout_action`; `global_wallclock_seconds` is duplicated in both files (7200 int vs 7200.0).
6. `run_challenge.py` / `score_decisions.py` hard-code `EXAMPLE3_ROOT`; no `--root`. `JsonLineAgentProcess` inherits the parent env/cwd/stderr (fine locally, unsafe on a platform).
7. Weather quality fields are strings in CSV but floats in JSON; tile catalog rows in `initial_publication` keep CSV **strings** (`"ra_deg":"5.549752"`) while `tile_science_value` is a float; `target_catalog` is 15,833 raw string rows — bulky and type-inconsistent.
8. README says "run from `example3/`" and `python3 -B -m unittest discover -s tests -v`; `pytest` also works. Conda env `survey-agent` mentioned but nothing depends on it.
9. `_has_actionable_tile` (avoidable-wait) and `excused_unobservable` use hidden truth; correct for scoring but means the same `decisions.csv` cannot be fully self-scored without `weather_events.csv`.
10. Scale mismatch: 64 tiles for 180 nights; the deterministic baseline already reaches the ceiling (all tiles, all requests) — the only remaining differentiator is exposure quality/timing and request handling. Weights are labelled provisional.

## 6. Open decisions for the organizers (with recommendation)

1. **Official mode**: (b) agent runs only; (a) `results` for practice on fully public scenarios (publish `weather_events.csv` there). *Recommend yes.*
2. **LLM access policy**: allow arbitrary provider keys in `.env` with egress allow-list, or mandate the organizer gateway (credits feature already exists). *Recommend gateway + allow-list fallback; forbid keys in logs; delete zips after evaluation.*
3. **Wallclock per scenario**: 7,200 s is the package default; with several hidden scenarios per phase that is many worker-hours. *Recommend 1,800–3,600 s for online phases (`scenarios.global_wallclock_seconds`), 7,200 s for the final.*
4. **`agent_error` / `agent_initialization_error` handling**: package semantics = score what was committed plus terminal penalties (≈ −16,000 floor). *Recommend: status `scored` with the package score, error text surfaced (consistent with local runs), except zip/entry-point errors → `invalid`.*
5. **Catalog scale and weights**: 64 tiles is too small for 180 nights; raise `tile_config.catalog.n_regions/tiles_per_region` (e.g. 16×16) and/or shorten `calendar_config.survey.days`, then re-calibrate `score_config.json` (`coefficient_status` says provisional). *Must be frozen before starter-kit release.*
6. **Replay generator ownership**: obtain the script from the colleague or reimplement against the `DATA` schema; decide whether replays are shown only to the owning team (recommended) or publicly for leaderboard entries.
7. **Edge-function scorer**: retire the TS port (Python-only scoring) vs. re-port. *Recommend retire.*
8. **Agent packaging contract**: zip root must contain an entry script (`minimal_agent.py`/`agent.py`), optional `requirements.txt` (pinned subset allowed), optional `.env`; interpreter and pre-installed stack version to be announced. *Recommend a published Docker image tag as the reference environment.*
9. **Partial-run scoring on the leaderboard**: terminal penalties make truncated runs look terrible; decide whether leaderboards show `total` only (package semantics) or also `base_science + program_bonus + request_reward`. *Recommend total, with the breakdown columns visible.*
10. **Multiple hidden scenarios**: regenerate with different `seed`s per config file (four seeds to change) and keep `scenario_manifest.json` checksums in `scenarios.manifest` for audit.