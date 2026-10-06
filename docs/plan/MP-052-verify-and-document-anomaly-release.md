---
id: "MP-052"
title: "Verify and document the anomaly reporting release"
status: "completed"
parent: "MP-045"
depends_on: ["MP-047", "MP-048", "MP-049", "MP-050", "MP-051"]
updated: "2026-09-18"
summary: "Prove determinism, replay equivalence, and starter-kit parity for the anomaly release; update regression anchors, platform docs, and the audit chain."
artifacts: ["../agent-observer/tests/", "../agent-observer/docs/competition-format.md", "../agent-observer/docs/organizer-guide.md"]
---

# MP-052: Verify and document the anomaly reporting release

## Objective

Prove the anomaly release keeps the platform's determinism, replay, and
audit guarantees; synchronize the byte-locked starter kit; and update every
document and regression anchor the scoring/protocol changes touch.

## Planned work

- Extend the challenge test suite: fault generation and forecast exclusion,
  efficiency jitter bounds and frozenness, max-score repeat accounting,
  request visit/score decoupling, tag multiplier application, report
  settlement (dedupe, ±settlement, misreport counter/reset/neutral cases),
  feedback field correctness, and fault-publication timing.
- Prove decisions.csv + report.csv replay to byte-identical score reports in
  the worker and the local runner.
- Verify the starter-kit byte lock passes with mirrored changes and the
  no-key deterministic agent still completes a full run.
- Update the baseline regression anchor and its three coupled references
  (test constant, starter-kit README, SKILL.md).
- Update `docs/competition-format.md` (report channel, feedback field,
  repeat-observation rules, settlement) and `docs/organizer-guide.md`
  (calibration knobs, scenario regeneration, seed hygiene).
- Run one full online-mode validation with a hosted agent and confirm the
  audit chain: platform-generated decisions.csv and report.csv with sha256
  recorded, authoritative replay matching the live score.
- Regenerate public scenarios per the organizer-guide rules.

## Decisions

- Acceptance is provider-independent contract tests plus deterministic
  replay, consistent with MP-043; live-API results are reported separately.
- Scenario regeneration and seed rotation stay mandatory before any official
  round, now covering the anomaly tag artifact as well.

Amendments from implementation (2026-09-18):

- The hosted online-mode validation cannot run from a local checkout (it
  needs the live platform); per the acceptance decision above it is recorded
  as a follow-up organizer step, not part of this node's acceptance.
- The practice-phase results upload still has no report.csv carrier; the
  open options (e.g. accepting a zip) are documented in
  `docs/competition-format.md`.
- HORIZON_SECTOR fault avoidance is unimplemented (the agent has no mount
  geometry); documented in `docs/organizer-guide.md`.
- `docs/example3-analysis-brief.md` is a historical hand-off document and
  deliberately keeps its old version strings and anchor numbers.

## Completion

Completed 2026-09-18. All participant, organizer, and web documentation now
describes the anomaly release (protocol v2 / snapshot v3, reporting channel
and settlement, max-score repeat observation, jitter and instrument faults,
hidden tags, calibration knobs); the three version-pinned test assertions
were unlocked; the web production build passes; a demo-week smoke run
through the participant path completes with correct reports and a valid
replay.

## Evidence

- `pytest challenge/tests tests/ -q --ignore=tests/supabase
  --ignore=tests/e2e_web` → 115 passed, 1 skipped (playwright render test)
  with the `survey-agent` conda Python 3.12; byte-lock, determinism, replay
  equivalence, and anchor tests all green.
- Web `npm ci && npm run build` (vue-tsc + vite + kit bundling) passes.
- demo-week smoke via `archive/starter_kit_v3/local_runner.py`: survey_complete, total
  8547.99, all 4 tag reports correct (+400), 1 correct fault report, 0
  misreports, report.csv (5 rows) and replay HTML produced.
- Anchor `23451.354778` consistent in all four coupled places; stale
  version/anchor strings remain only in historical documents by design.
- Not run locally (environment): tests/supabase (needs Postgres+PostgREST),
  tests/e2e_web (needs Playwright browsers), and the hosted live validation
  — all listed as organizer follow-ups.
