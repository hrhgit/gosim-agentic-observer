---
id: "MP-047"
title: "Add efficiency jitter and instrument-fault events"
status: "completed"
parent: "MP-045"
depends_on: ["MP-046"]
updated: "2026-09-18"
summary: "Give baseline instrument_efficiency frozen per-slot jitter in [0.90, 1.00] and add region-scoped instrument-fault events hidden from forecasts."
artifacts: ["../agent-observer/challenge/weather_simulator.py", "../agent-observer/challenge/reference/config/weather_config.json"]
---

# MP-047: Add efficiency jitter and instrument-fault events

## Objective

Make `instrument_efficiency` a realistic per-slot quantity with small frozen
jitter, and add a new `instrument_fault` event type that sharply reduces
efficiency over a sky region for a configurable number of simulated days.

## Planned work

- Extend `generate_weather` so baseline `instrument_efficiency` draws a small
  per-slot jitter inside the configured range (placeholder [0.90, 1.00]);
  once generated, a slot's efficiency never changes (truth stays frozen).
- Add `instrument_fault` to `CONDITIONS` with a configurable
  `instrument_efficiency_multiplier` reaching down to the configured floor
  (placeholder 0.10) so faults are clearly distinguishable from normal
  jitter; use the existing directional scope machinery (REGION_SET /
  SKY_CAP_ICRS / HORIZON_SECTOR) to target a sky region.
- Constrain generation so at most one fault is active at any time.
- Exclude `instrument_fault` events from `generate_forecasts` — faults are
  never forecastable.
- Ensure fault multipliers apply through `get_effective_conditions` for the
  scorer but are filtered out of agent-visible snapshot weather (the agent
  detects faults only via realized-score deviation).
- Bump the weather schema version per MP-046 and mirror every change into
  the byte-locked starter-kit copy.

## Decisions

- Faults ride the existing event/scope/multiplier machinery; no new weather
  subsystem.
- The legal efficiency range [0.10, 1.00] stays unchanged; faults bottom out
  at 0.10 rather than forcing slots closed.
- Fault events are generated with scenario entropy but withheld from agent
  snapshots; they remain in `weather_events.csv` for participant audit
  (hidden from the agent, not from participants — MP-045).

Amendments from implementation (2026-09-18):

- Weather schema bumped to `directional-weather-v2`; every in-repo scenario
  weather config now defines `instrument_fault` and the
  `instrument_efficiency` jitter range (placeholders [0.90, 1.00]).
- Fault placement uses deterministic bounded resampling on the same rng
  stream, placed after all other conditions, so pre-existing events,
  forecasts, and non-weather scenario files stay byte-identical.
- Agent visibility split is implemented as
  `get_effective_conditions(..., include_instrument_faults=False)` at the
  two snapshot construction sites in `challenge_workflow.py`; the scorer,
  replay, and CLI keep the truth view. `weather.csv` continues to hold only
  baseline + global-event values.
- Scenario regeneration (reference, dev-reference, demo-week) changed only
  the weather trio, weather metadata, and manifests; calendar, tiles,
  targets, windows, requests verified byte-identical.
- The starter-kit baseline anchor moved from `12287.478365` to
  `11986.914955` (jitter slightly lowers mean efficiency); updated in the
  test constant, starter-kit README, SKILL.md, and the workspace AGENTS.md.
- The `live_week_validation` fixture was re-scored and re-rendered against
  the new weather; its documented nights-4-7 sample defect disappeared and
  the corresponding test assertion was simplified to full equality.
- Amendment (2026-09-18, post-release simplification): shipped
  `instrument_fault` events are restricted to `REGION_SET` scope
  (`scope_weights: {"REGION_SET": 1.0}` in all scenario configs) to lower
  difficulty and make agent-side avoidance complete; the generator still
  supports every scope type, only the configuration changed. `weather.csv`
  is byte-identical to the MP-047 state (faults are directional and never
  enter it); events/metadata/manifests were regenerated and the baseline
  anchor moved from `23451.354778` to `23467.243423`. A new test pins the
  shipped configs and fault events to REGION_SET.
- Amendment (2026-09-18, persistence semantics): faults no longer end on
  their own. `instrument_fault` drops `duration_slots` in favour of
  `"persists_until_survey_end": true` (actual_end = end of the survey), so
  the repair clock from a correct report (report + x2 days) is the only way
  a fault ends early. Because persistence is incompatible with "at most one
  active fault", `count` is now 1 and the config loader rejects
  `count > 1` for persistent events. Anchor moved back to `23451.354778`:
  in the reference seed the fault (R05, starting 2026-12-10) lands after the
  affected tiles banked their best scores, so its net effect on the baseline
  is zero — collapse readings simply never win the max ledger. Detection
  latency (fault starts 12-10, reported 01-20) is a known calibration item
  for the organizers.
- Amendment (2026-09-18, severity configurability): the previously
  hard-coded severity draw (`rng.uniform(0.55, 1.0)`, shared by all events)
  is now an optional per-event config key `severity_range: [lo, hi]`
  (default [0.55, 1.0], validated by `load_config`). Shipped configs set it
  explicitly only on `instrument_fault`. The fault band is therefore fully
  config-driven: weak end via `severity_range`, strong end via
  `instrument_efficiency_multiplier`, absolute floor via
  `quality.instrument_efficiency.minimum`. Generation stays byte-identical
  (verified by sha256 on all three scenarios); the anchor is unchanged.
- Amendment (2026-09-18, direct multiplier band): the severity indirection
  was dropped for faults after review feedback — `instrument_fault` now uses
  `instrument_efficiency_multiplier_range: [0.10, 0.55]`, drawn directly
  without `_scaled_multiplier`; `severity_range` and the scalar multiplier
  were removed from that event. Severity is still drawn and recorded in
  `weather_events.csv` for schema compatibility. Only the fault rows change
  in the regenerated events (weather.csv and forecasts byte-identical);
  the anchor stays `23451.354778`. Known follow-up recorded: fault detection
  depends on the fault region being observed after onset — if its tiles
  banked their maxima before onset, the reference agent may never re-observe
  it; calibration or patrol behaviour is an organizer-side decision.

## Completion

Completed 2026-09-18. Baseline `instrument_efficiency` now carries frozen
per-slot jitter, region-scoped `instrument_fault` events are generated (at
most one active at any time), faults are excluded from forecasts including
false positives, and the agent snapshot never sees fault multipliers while
the scorer does.

## Evidence

- Five new unit tests in `challenge/tests/test_weather_simulator.py` cover
  jitter bounds/frozenness, single-active-fault, forecast exclusion, scoped
  application vs a control tile, and snapshot filtering.
- `pytest challenge/tests` fully green (playwright render test skipped as
  before) with the `survey-agent` conda Python 3.12.
- `pytest tests/test_challenge_runner.py -q` → 5 passed.
- `tests/test_starter_kit.py` green except three baseline end-to-end tests
  that exceed the 120 s wall-clock budget on this slow aarch64 host (~63
  decisions/s vs ~66 needed). Proven pre-existing: the same tests time out
  on unmodified HEAD in a clean worktree, and an in-process A/B shows no
  per-decision performance regression (380.4 vs 384.3 decisions/s, identical
  decision counts). Resolution: raise the test wall-clock budget to 240 s
  under MP-048 so the suite is green on this host; CI/x86 finishes in
  15-25 s either way.
