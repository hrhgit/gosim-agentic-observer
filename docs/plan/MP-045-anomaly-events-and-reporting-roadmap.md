---
id: "MP-045"
title: "Anomaly events and agent reporting roadmap"
status: "completed"
parent: "MP-010"
depends_on: ["MP-043"]
updated: "2026-09-18"
summary: "Extend the agent-observer v3 challenge with instrument faults, hidden tile anomaly tags, max-score repeat observation, and a report channel that tests agent anomaly detection."
artifacts: ["../idea-09181620.md", "../agent-observer/challenge/"]
---

# MP-045: Anomaly events and agent reporting roadmap

## Objective

Extend the delivered agent-observer v3 challenge so that agents must detect
and report anomalous conditions — region-scoped instrument faults, per-tile
nova boosts, and per-tile reddening — by comparing realized-score feedback
against the public scoring baseline, while repeat observations are rescored
under a highest-score-wins model.

Source requirement: `idea-09181620.md` plus the design decisions iterated in
the 2026-09-16 review discussion recorded below.

## Planned work

1. Freeze the anomaly/reporting contract: snapshot feedback field, report
   envelope and `report.csv` schema, config parameters, schema version bumps
   (MP-046).
2. Add baseline instrument-efficiency jitter and region-scoped instrument
   fault events, hidden from forecasts (MP-047).
3. Rework the scorer for repeat observations under per-tile max scoring and
   request visit/score decoupling (MP-048).
4. Add hidden nova/reddening tile tags with published multiplier factors and
   end-of-run report settlement (MP-049).
5. Add the report channel and realized-score feedback to the participant
   protocol and workflow (MP-050).
6. Teach the reference/minimal agents anomaly detection and reporting so
   participants have a working example (MP-051).
7. Verify determinism, replay equivalence, starter-kit sync, and document
   the release (MP-052).

## Decisions

Design decisions settled with the decision owner on 2026-09-16:

- **Detection channel**: every decision snapshot carries
  `tile_last_finished: {tile_id, score}` — the realized official score of the
  most recently completed observation. Agents compare it against the public
  formula's baseline (the scorer and formula are fully public) and report
  deviations. `tile_on_queue` is cancelled: exposures are atomic between
  decision points, so an in-progress exposure is never observable at a
  decision point. Mid-exposure decision points are a non-goal.
- **Information hiding boundary**: fault events, nova/reddening tags, and
  their parameters are hidden from the *agent* only (excluded from
  snapshots), never from participants — scenario truth files remain
  publishable and auditable. No encryption is introduced; integrity uses the
  existing sha256 manifest/replay pattern, extended to `report.csv`.
- **Report channel**: reports ride as an optional field on
  `decision_response` (one action per round plus zero or more reports);
  reporting costs no slot. Kinds: `Instrument_Failure`, `NOVA {tile}`,
  `Reddening {tile}`.
- **Fault lifecycle**: at most one fault active at a time. A fault report is
  *correct* when an unacknowledged fault is active; re-reporting an
  acknowledged, still-repaired fault is neutral (no reward, no penalty, does
  not touch the misreport counter); reporting with no active fault is a
  *misreport*. Between two correct reports at most one misreport is free;
  further misreports cost a configurable penalty (placeholder 100). The
  counter resets on every correct report. x1 (response latency) and x2
  (repair duration) are simulated days, configurable, placeholders 1 and 2.
  The fault response (region, efficiency impact, repair ETA) is published as
  a time-gated snapshot field, re-published nightly during repair, and
  removed once repair completes.
- **Nova/reddening settlement**: correctness is settled only in final
  scoring — the misreport counter never applies. First report per
  (tile, tag) counts; duplicates are ignored. Both tags may be reported on
  the same tile; the right one earns +100, the wrong one −150 (placeholders,
  shared configurable pair; penalty deliberately exceeds reward to deter
  guessing — blind guessing breaks even only above 60% confidence).
- **Repeat observation**: each tile's science score is the maximum over its
  observations; completion, REQUIRED-miss relief, and flexible quotas bank on
  the first legal observation. A request issued for a previously observed
  tile requires a new post-issue observation; a request-tagged observation of
  an unobserved tile counts as ordinary completion. Request-tagged
  observations score normally (the current revisit-zeroing rule is removed);
  request visit counts are fully decoupled from tile scores. Partial request
  progress still banks per-tile science scores.
- **Configurability**: baseline efficiency jitter range, fault multiplier
  floor, x1, x2, misreport penalty, report reward/penalty, and tag factors
  are all config parameters with placeholder values pending organizer
  calibration.
- **Timing**: fault response latency and repair duration are measured in
  simulated days, not wall-clock.

Non-goals: mid-exposure decision points; hiding scenario truth from
participants; encryption of any artifact; per-decision wall-clock limits.

## Completion

Completed 2026-09-18 — all child nodes MP-046 through MP-052 are complete.
The anomaly events and agent reporting feature line is implemented end to
end in `agent-observer`: frozen contract, efficiency jitter and instrument
faults, max-score repeat observation, hidden tile anomaly tags with
settlement, the report channel with realized-score feedback, a reference
agent that detects and reports anomalies, and the documentation release.

## Evidence

Child nodes MP-046 through MP-052 carry the per-step evidence. Headline:
full local suite green (115 passed, 1 environment-limited skip), starter-kit
byte lock intact, deterministic replay with report.csv matches live scores,
web production build passes, and the no-key deterministic reference agent
completes the dev-reference scenario with all anomaly reports correct and
zero misreports (baseline anchor `23451.354778`). Remaining organizer
follow-ups (hosted live validation, supabase/e2e suites, practice-upload
report carrier) are recorded in MP-052.
