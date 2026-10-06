---
id: "MP-049"
title: "Add hidden tile anomaly tags and report settlement"
status: "completed"
parent: "MP-045"
depends_on: ["MP-046", "MP-048"]
updated: "2026-09-18"
summary: "Generate hidden nova/reddening tile tags, apply their published multipliers in the scorer, and settle report rewards and fault misreport penalties at finalize."
artifacts: ["../agent-observer/challenge/scenario_builder.py", "../agent-observer/challenge/scoring_core.py"]
---

# MP-049: Add hidden tile anomaly tags and report settlement

## Objective

Generate per-tile `nova` (×1.5 placeholder) and `reddening` (×0.8
placeholder) anomaly tags, apply them as score multipliers invisible to the
agent, and settle all report rewards and penalties — tag reports at final
scoring, fault misreports during the run.

## Planned work

- Add a scenario artifact (e.g. `tile_anomalies.csv`) mapping tagged tiles to
  `nova`/`reddening`; factors are published in the scoring contract while the
  tagged tile set is withheld from agent snapshots only.
- Apply the multiplier at the per-segment scoring hook so realized scores
  deviate measurably from the public baseline computed by
  `scoring_preview.py`; the reddening factor intentionally overlaps the
  instrument-fault efficiency range to test discrimination.
- Settle tag reports in `finalize`: first report per (tile, tag) earns
  +100 if the tag is present, −150 if absent; duplicates ignored; both tags
  may be reported on one tile and are settled independently.
- Implement the fault misreport ledger: correct report (unacknowledged
  active fault) resets the counter; redundant reports on an acknowledged
  fault are neutral; reports with no active fault increment the counter and
  every misreport beyond the first since the last correct report costs the
  configured penalty (placeholder 100).
- Keep every value above in versioned config per MP-046.

## Decisions

- Tag truth lives in a scenario file that participants may inspect after the
  fact; only the agent's snapshot view excludes it. No encryption.
- Tag reports have no in-run effect — they are pure detection judgments
  settled at the end. Fault reports are the only in-run instrument (they buy
  the fault-status publication), hence only they need the anti-spam counter.
- Tag multipliers apply silently: the snapshot's published
  `tile_science_value` stays the untagged baseline so deviation is detectable
  only through realized-score feedback.

Amendments from implementation (2026-09-18):

- `tile_anomalies.csv` is generated inside the scenario-builder chain from a
  dedicated rng stream (seed + 4000), with counts configured via
  `tile_config.json` (`anomaly_tags.nova_count`/`reddening_count`, default
  0/0 so old configs are unchanged). The file is conditional everywhere:
  manifest, `describe_scenario`, and the scorer all treat a missing file as
  "no tags". It is not in `public_relpaths()` — hidden from the agent,
  auditable by participants after the fact.
- The tag multiplier applies to the per-segment base score and therefore
  also scales the program bonus derived from it; both tags on one tile
  multiply together. The weather-quality band is computed from the untagged
  combined quality (tags are intrinsic to the target, not observing
  conditions). The avoidable-wait truth simulation multiplies the factor
  too, keeping its judgement consistent with scoring.
- Fault repair truncates the fault's effective end at
  `report_time + repair_duration_days`, implemented as a per-scorer
  `end_overrides` layer on the weather simulator — truth files are never
  mutated and replay from decisions.csv + report.csv stays deterministic.
- A correct fault report requires an active, unacknowledged fault; a
  redundant report on an acknowledged fault is neutral; reports with no
  active fault increment the misreport counter and each misreport beyond the
  free allowance costs the configured penalty in-run. Tag reports dedupe per
  (tile, tag) and settle only in `finalize`.
- Reports merge into replay via `apply_decision_stream`: each report applies
  immediately after the decision carrying its `decision_sequence`, at the
  post-decision cursor time, never moving the cursor. MP-050's live path
  must match this rule exactly.
- `fault_response.response_latency_days` (x1) is not consumed yet; it only
  gates the fault-status publication timing in MP-050 and has no scoring
  effect.
- Score reports stay `score-report-v3` with additive keys (`report_reward`,
  `penalties.wrong_tag_report`, `penalties.fault_misreport`, and a `reports`
  detail section).

## Completion

Completed 2026-09-18. `tile_anomalies.csv` is generated, loaded, and
applied silently in the scorer; report loading, the fault report ledger with
repair truncation, and end-of-run tag settlement are implemented in
`ChallengeScorer`; `score_files`/`score_decisions.py` accept an optional
audited `report.csv` (starter-kit CLI included).

## Evidence

- New `challenge/tests/test_report_settlement.py` (9 tests): generation
  determinism, silent multiplier vs an untagged control scorer, double-tag
  compounding, unchanged published tile values, the full fault-ledger branch
  set, repair truncation returning segments to baseline, tag settlement
  dedupe/±/penalty-exceeds-reward/total reconciliation, and end-to-end
  `score_files` with and without reports.
- `pytest challenge/tests tests/test_starter_kit.py
  tests/test_challenge_runner.py -q` → 93 passed, 1 skipped (playwright
  render test) with the `survey-agent` conda Python 3.12.
- Reference and starter-kit scenarios regenerated with `tile_anomalies.csv`
  (reference tags: nova T00010/T00039, reddening T00022/T00024); tiles and
  targets verified byte-identical.
- Baseline anchor moved from `14066.536992` to `14229.273699` (tagged tiles
  lift realized scores); updated in the test constant, starter-kit README,
  SKILL.md, and the workspace AGENTS.md. The `live_week_validation` fixture
  was re-scored and re-rendered; its audit assertion now includes the
  anomalies input.
