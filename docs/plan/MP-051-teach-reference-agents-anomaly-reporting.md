---
id: "MP-051"
title: "Teach the reference agents anomaly detection and reporting"
status: "completed"
parent: "MP-045"
depends_on: ["MP-050"]
updated: "2026-09-18"
summary: "Extend the reference and minimal participant agents to compare realized feedback against the public baseline and emit fault/nova/reddening reports."
artifacts: ["../agent-observer/challenge/participant_agent/", "../agent-observer/archive/starter_kit_v3/agent/"]
---

# MP-051: Teach the reference agents anomaly detection and reporting

## Objective

Give participants a working example of the intended detection loop: compute
the public-formula baseline for each finished observation, compare it with
the realized `tile_last_finished` score, and emit calibrated reports without
spamming penalties.

## Planned work

- Extend the minimal agent's deterministic layer with a deviation check:
  realized score vs preview baseline beyond a configurable tolerance.
- Map deviation signatures to report kinds: efficiency-collapse pattern →
  `Instrument_Failure`; single-tile upward/downward deviation →
  `NOVA`/`Reddening` candidate reported only above the confidence level
  implied by the +100/−150 odds.
- Respect the fault report ledger semantics: never report while an
  acknowledged fault is unrepaired unless new evidence appears; never report
  without an active deviation signal.
- Keep the no-key deterministic mode fully functional so the hand-off
  property "valid decisions and replayable score with no API key" survives.
- Mirror changes into the byte-locked starter-kit agent package.

## Decisions

- The reference implementation demonstrates conservative reporting: the
  scoring odds make blind guessing negative-expected-value, and the example
  should embody that lesson rather than maximize report volume.
- Detection logic lives in the deterministic layer; model providers may only
  choose among legal candidates, consistent with MP-041's boundary.

Amendments from implementation (2026-09-18):

- Detection lives in a new `anomaly_detection.py` module
  (`AnomalyDetector`), not in `my_strategy.py`: the participant-edited file
  stays minimal while the pipeline handles baselines, deviation ratios,
  report gating, and fault-scope candidate filtering.
- Tag confirmation uses a read-fraction rule (>= 3 readings, >= 2/3 inside
  the calibrated ratio band) after two simpler rules produced false
  positives on weather-edge readings; the calibration story and measured
  numbers are documented in `reference_strategy.py`.
- Deviation bands are environment-tunable (`SAC_ANOMALY_*`): nova
  [1.32, 1.68], reddening [0.72, 0.88], fault collapse <= 0.60 — the gap
  between the fault multiplier floor range and the reddening factor keeps
  the two signatures separable.
- Fault reports require two collapse readings inside a rolling window and
  are then suspended until the `fault_status` publication resolves them;
  during repair the agent avoids the fault scope for REGION_SET and
  SKY_CAP_ICRS (HORIZON_SECTOR filtering is not implemented — the agent has
  no mount geometry).
- Idle endgame time is spent on confirmation re-observations of suspect
  tiles, which also fixed the MP-048 leftover zero-gain tie-break; the
  built-in ReferenceAgent now re-observes instead of waiting in the endgame.
- The tracked per-tile best table feeds `preview_actions(tile_best_scores)`
  so repeat-observation estimates reflect realized maxima.
- Amendment (2026-09-18): with shipped faults restricted to REGION_SET
  (MP-047 amendment), the HORIZON_SECTOR avoidance gap no longer applies to
  faults — agent-side fault avoidance is now complete for the shipped
  scenarios. The SKY_CAP_ICRS branch remains in the detector for future
  configurations.
- Amendment (2026-09-18, hidden efficiency recalibration): after
  `instrument_efficiency` was removed from the agent-visible weather
  (MP-050 amendment), the detector bands were recalibrated for the wider
  normal-noise band (nova [1.30, 1.65], reddening [0.70, 0.87], fault
  <= 0.60) and two systematic biases are compensated using public
  information only: program-bonus band flips (credit the bonus only when
  even the weakest jitter would not drop the band) and cold_wave efficiency
  dips (readings during forecast-covered cold waves are excluded from
  anomaly evidence). Clean-scenario run: 390 readings, zero false reports.
  Baseline anchor moved from `23451.354778` to `23337.183119` (slightly
  fewer program-bonus matches under the no-efficiency preview baseline).
- Amendment (2026-09-18, band unification follow-up): after program bands
  became efficiency-free (MP-048 amendment), the bonus band-flip
  compensation was removed again — preview and realized bands match by
  construction. Tag confirmation tightened to >= 5 readings, >= 80% in-band,
  spanning >= 2 nights after a residual false reddening was traced to
  mid-exposure weather drift crossing a band boundary (preview evaluates at
  commit time, the scorer at segment midpoints). Clean-scenario re-check:
  zero false reports.

## Completion

Completed 2026-09-18. The minimal agent detects and reports anomalies in
fully deterministic no-key mode: on the dev-reference scenario it settled
all 4 tag reports correctly (+400) and both faults correctly with zero
misreports; on an anomaly-free scenario it emits no reports at all.

## Evidence

- New `challenge/tests/test_anomaly_detection.py` (12 tests): unit coverage
  of the fraction rule, weather-edge immunity, interrupted-zero immunity,
  the fault evidence window, the fault_status state machine, repair-period
  silence and re-arming, scope avoidance; end-to-end coverage of a
  synthetic 14-night anomaly scenario (detect → report → settlement > 0 →
  replay-consistent) and an anomaly-free scenario (zero reports).
- `pytest challenge/tests tests/test_starter_kit.py
  tests/test_challenge_runner.py -q` → 115 passed, 1 skipped (playwright
  render test) with the `survey-agent` conda Python 3.12.
- Baseline anchor moved from `14229.273699` to `23451.354778`: report
  rewards (+400) plus the max-score endgame engine driven by realized-best
  tracking; zero observation penalties, subprocess and in-process runs agree
  to the point. Updated in the test constant, starter-kit README, SKILL.md,
  and the workspace AGENTS.md.
- Reference `score_report.json`/`workflow_result.json` re-scored after the
  ReferenceAgent endgame fix (total −2289.61 → −1083.28).
