---
id: "MP-046"
title: "Freeze the anomaly and reporting contract"
status: "completed"
parent: "MP-045"
depends_on: []
updated: "2026-09-18"
summary: "Define the report envelope, report.csv schema, snapshot feedback field, fault-publication field, and all configurable parameters before implementation."
artifacts: ["../agent-observer/challenge/contracts.py", "../agent-observer/challenge/reference/config/"]
---

# MP-046: Freeze the anomaly and reporting contract

## Objective

Define every new interface surface — schemas, message fields, file formats,
version bumps, and config parameters — before any simulator, scorer, or
workflow code changes, so all child workstreams implement against a frozen
contract.

## Planned work

- Extend `decision_response` with an optional `reports` array; each entry is
  `{kind: "Instrument_Failure"}` or `{kind: "NOVA" | "Reddening", tile_id}`.
  Validation rules: unknown kinds rejected, malformed entries rejected,
  duplicates tolerated (settlement dedupes).
- Define `report.csv` columns (report_id, decision_sequence, slot_id,
  timestamp_utc, kind, tile_id) and its place in the run artifacts and audit
  chain (sha256 alongside decisions.csv).
- Extend the decision snapshot (bump to `decision-snapshot-v3`) with
  `tile_last_finished: {tile_id, score} | null` and the time-gated
  `fault_status` publication (region scope, effective efficiency multiplier,
  repair completion date), present only while an acknowledged fault is
  unrepaired.
- Bump `participant-agent-protocol-v1` to v2 and
  `directional-weather-v1` as required; record which fields participants may
  rely on.
- Add config parameters with placeholders: efficiency jitter range
  [0.90, 1.00], fault efficiency multiplier floor 0.10, fault response
  latency 1 day, repair duration 2 days, misreport penalty 100, report
  reward +100 / penalty −150, nova factor 1.5, reddening factor 0.8.
- Update the participant-facing protocol documentation to match.

## Decisions

- Contract-first: no implementation begins until this node is complete,
  matching the example3 contract-first precedent (MP-029).
- `tile_on_queue` is deliberately absent from the contract (cancelled with
  the atomic-exposure model; see MP-045).
- Reports are transport metadata on `decision_response`, not a new action:
  they never consume slot time and never advance the cursor.
- All placeholder values live in versioned config, not code, so organizer
  calibration never touches logic.

## Decisions

- Contract-first: no implementation begins until this node is complete,
  matching the example3 contract-first precedent (MP-029).
- `tile_on_queue` is deliberately absent from the contract (cancelled with
  the atomic-exposure model; see MP-045).
- Reports are transport metadata on `decision_response`, not a new action:
  they never consume slot time and never advance the cursor.
- All placeholder values live in versioned config, not code, so organizer
  calibration never touches logic.

Amendments from implementation (2026-09-18):

- Protocol bumps landed as `participant-agent-protocol-v2` and
  `decision-snapshot-v3` across code and unit tests; web content, docs, and
  starter-kit prose keep the old strings until the MP-052 release, so the
  e2e/live assertions that pin site prose intentionally still reference
  v1/v2.
- `challenge-score-v3` is kept as the score schema: the new sections are
  additive placeholders and the loader tolerates unknown keys. The dormant
  `one_ordinary_credit_per_tile` key is removed (superseded by MP-048);
  `interrupted_exposure_science_score` and `coefficient_status` stay.
- The weather schema bump (`directional-weather-v1` to v2) ships with
  MP-047 together with the generator changes and scenario regeneration, so
  configs and generated data never disagree.
- Participant-facing protocol documentation is deferred to MP-050/MP-052
  when the fields actually ship, instead of publishing an unimplemented
  contract.
- The `live_week_validation` fixture's `offline_score.json` had only its
  `input_sha256.score_config` entry refreshed; regenerating the whole
  fixture with current tooling is not byte-faithful and is left to MP-052.
- Amendment (2026-09-18, reports as decision rows): the report.csv contract
  was retired after release review. Reports now travel as decision rows with
  actions `report_instrument_failure` / `report_nova` / `report_reddening`
  (tag rows carry tile_id; program/request_id empty; never consume slot
  time or move the cursor). `REPORT_COLUMNS` is gone, replaced by
  `REPORT_ACTIONS`; the agent-facing protocol still carries the optional
  `reports` array on `decision_response` — the workflow flattens accepted
  reports into decisions.csv rows immediately after the carrying decision.
  Single-file audit chain; the practice-upload carrier problem is solved by
  construction.

## Completion

Completed 2026-09-18. The contract surface is frozen in code:
`challenge/contracts.py` carries the bumped protocol/snapshot versions and
the new `REPORT_COLUMNS`, `REPORT_KINDS`, `ANOMALY_TAG_VALUES`, and
`TILE_ANOMALY_COLUMNS` constants; the participant protocol module mirrors
the version bumps; all three scenario `score_config.json` files carry the
`repeat_observation`, `reporting`, `anomaly_tags`, and `fault_response`
placeholder sections with manifests refreshed; starter-kit byte-locked
mirrors are synchronized.

## Evidence

- `pytest challenge/tests tests/test_starter_kit.py -q` → 66 passed,
  1 skipped (playwright-only replay render test, pre-existing environment
  limitation), run with the `survey-agent` conda Python 3.12.
- `pytest tests/test_challenge_runner.py -q` → 5 passed.
- Baseline anchor `12287.478365` still passes: the placeholder config
  sections have no scoring-behavior effect.
- Manifest sha256 for the updated score_config refreshed in
  `challenge/reference` and both starter-kit scenarios.
