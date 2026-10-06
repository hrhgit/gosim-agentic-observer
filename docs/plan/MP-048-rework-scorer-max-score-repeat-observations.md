---
id: "MP-048"
title: "Rework the scorer for max-score repeat observations"
status: "completed"
parent: "MP-045"
depends_on: ["MP-046"]
updated: "2026-09-18"
summary: "Replace one-credit-per-tile with per-tile highest-score accounting, keep completion banking on first legal observation, and decouple request visits from tile scores."
artifacts: ["../agent-observer/challenge/scoring_core.py", "../agent-observer/challenge/scoring_preview.py"]
---

# MP-048: Rework the scorer for max-score repeat observations

## Objective

Allow repeat observation of any tile throughout the survey with the tile's
science score taken as the maximum over its observations, while completion
semantics (REQUIRED-miss relief, flexible quotas, progress reporting) bank on
the first legal observation.

## Planned work

- Remove the `duplicate_tile` invalidation: re-observation without a request
  tag becomes a legal, normally scored action.
- Maintain a per-tile best-score map; a tile's science contribution is the
  maximum of its observation scores.
- Keep `completed_tiles` banking on the first legal observation so
  required-miss, quota, and coverage semantics are unchanged.
- Remove the revisit-zeroing rule for request-tagged observations: a
  request-tagged observation scores normally and participates in max-score
  accounting.
- Request semantics: a request naming a previously observed tile requires a
  new post-issue observation to count a visit; a request-tagged observation
  of an unobserved tile counts as ordinary completion. Partial request
  progress banks per-tile science scores immediately; visit counts are fully
  decoupled from tile scores (multiple visits each score, max wins).
- Rework `scoring_preview.py` so `already_completed` candidates show their
  repeat-observation estimate instead of a hard-coded zero.
- Re-audit avoidable-wait actionability: waiting while only completed tiles
  are observable is no longer automatically legitimate.
- Update the hard-coded baseline regression anchor and its documentation
  references (starter-kit README, SKILL.md).

## Decisions

- Max-score, not accumulation: repeat observation can only improve a tile's
  contribution, never add a second copy.
- Completion is a first-observation event; score is a max over all
  observations. The two ledgers never interact.
- The dormant `one_ordinary_credit_per_tile` config intent is superseded by
  this model and the key is removed or repurposed per MP-046.

Amendments from implementation (2026-09-18):

- The per-tile best ledger stores `(base, bonus)` per tile and banks only
  the positive delta when a new observation beats the banked best, so the
  run total is monotone. Each action record still carries that observation's
  own score — MP-050's `tile_last_finished.score` feedback needs per-attempt
  values, and the difference between the total and the per-action sum lives
  in the max ledger by design.
- Avoidable-wait actionability is now exact: a tile is actionable when an
  unfinished tile can legally complete from the cursor, or a finished tile
  can complete AND its forward-simulated repeat potential (truth weather,
  best of the three programs) exceeds its banked best. Waiting is penalized
  precisely when a repeat observation could improve the score.
- `scoring_preview.preview_actions` accepts an optional `tile_best_scores`
  map; without it, repeat estimates default to a conservative zero lower
  bound. An optimistic default was tried first and broke the baseline
  (427 repeat observations of one tile, missed request). Tracking agents get
  real marginal values by feeding the map from `tile_last_finished` feedback
  — no new snapshot field was needed, preserving the detection game.
- The old one-credit semantics are no longer selectable: configurations
  lacking `repeat_observation` fall back to `"max"`, and any other value is
  rejected by `load_score_config`.
- The starter-kit baseline end-to-end tests moved from a 120 s to a 240 s
  wall-clock budget; the 120 s timeouts on the aarch64 dev host were proven
  to be a pre-existing environment flake (identical on unmodified HEAD, no
  per-decision performance regression).

## Completion

Completed 2026-09-18. Repeat observation is legal and normally scored,
tile science contributions use per-tile max accounting, completion semantics
bank on the first legal observation, request-tagged observations score
normally with visit counts fully decoupled, and both the scorer and the
preview implement the new actionability and marginal-value rules.

## Evidence

- Seven new scorer tests plus two preview/agent tests cover max banking
  (degraded repeat never lowers, better repeat raises), first-observation
  completion, untagged repeats, request visit timing windows, request on an
  unobserved tile counting as ordinary completion, and both avoidable-wait
  branches.
- `pytest challenge/tests tests/test_starter_kit.py
  tests/test_challenge_runner.py -q` → 81 passed, 1 skipped (playwright
  render test) with the `survey-agent` conda Python 3.12, including the
  three baseline tests under the new 240 s budget.
- Baseline anchor moved from `11986.914955` to `14066.536992`
  (survey_complete, 64/64 tiles, 17/18 requests, zero penalties); updated in
  the test constant, starter-kit README, SKILL.md, and the workspace
  AGENTS.md.
- Reference `score_report.json`/`workflow_result.json` and the
  `live_week_validation` fixture were re-scored under the new semantics
  (the fixture's vendored `duplicate_tile` decision is now legal and
  scores).
- Amendment (2026-09-18, efficiency-free program bands): program band
  classification no longer includes `instrument_efficiency`
  (`weather_quality(..., include_efficiency=False)` for banding; the score
  magnitude still carries the full quality including efficiency). Preview
  and realized bands are now always identical, so the bonus band-flip
  mismatch is eliminated by construction; the cap is applied before banding
  in both paths, which cannot disagree because efficiency <= 1. Baseline
  anchor moved from `23337.183119` to `23430.568406` (bonus matches
  recovered).
