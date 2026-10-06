---
id: "MP-050"
title: "Add report channel and realized-score feedback to the protocol"
status: "completed"
parent: "MP-045"
depends_on: ["MP-046", "MP-047", "MP-049"]
updated: "2026-09-18"
summary: "Carry reports on decision_response, publish tile_last_finished and time-gated fault status in snapshots, and emit an audited report.csv."
artifacts: ["../agent-observer/challenge/challenge_workflow.py", "../agent-observer/challenge/run_challenge.py", "../agent-observer/challenge/participant_agent/protocol.py"]
---

# MP-050: Add report channel and realized-score feedback to the protocol

## Objective

Give the agent the two information flows the anomaly design depends on —
realized-score feedback for completed observations and the delayed
fault-status publication — and carry agent reports back to the platform
without new message types or slot costs.

## Planned work

- Add `tile_last_finished: {tile_id, score} | null` to every decision
  snapshot: the realized official score of the most recently completed
  observation (including interrupted-at-dawn zero results). Null before the
  first completion. `tile_on_queue` is not implemented (cancelled, MP-045).
- Accept an optional `reports` array on `decision_response`; validate kinds
  and tile references per MP-046; reports never advance the cursor or consume
  slot time.
- Record accepted reports to `report.csv` as a first-class run artifact,
  hashed into the audit chain alongside `decisions.csv`.
- Implement the fault report lifecycle in the workflow: on a correct fault
  report, schedule the fault-status publication x1 simulated days later
  (placeholder 1); publish it as a time-gated snapshot field at each night
  start while the fault remains unrepaired; remove it when repair completes
  after x2 simulated days (placeholder 2). A report with no active fault gets
  an "instrument normal" response on the same schedule.
- Reuse the existing as-of publication pattern (forecast/request gating);
  no new platform→agent message type is introduced.
- Update the practice-phase submission path so local runs emit and upload
  `report.csv` the same way.

## Decisions

- Feedback is pull-based inside snapshots, consistent with the protocol's
  "platform never pushes" property.
- The fault-status field is absent (not empty) when no acknowledged fault is
  unrepaired.
- Realized feedback covers observations only; waits and invalid actions
  produce no feedback entry beyond existing report fields.

Amendments from implementation (2026-09-18):

- `tile_last_finished` updates only after observation outcomes (including
  interrupted-at-dawn zeros) and carries that attempt's own base+bonus;
  waits and invalid actions never touch it.
- `fault_status` is computed at night-start decision points only: the fault
  form carries scope, effective multiplier, report/publish/repair timestamps
  and re-publishes each night start during repair; a misreport yields a
  one-shot `{"status": "normal"}` notice on the same x1 schedule. Both forms
  are absent unless due.
- Malformed report entries are dropped individually with a `dropped_reports`
  count in the commit log — a bad report never kills a legal action.
  Duplicates are tolerated (settlement dedupes).
- `report.csv` is always written (header-only when empty) so the audit chain
  and `score_files` can reference it unconditionally; report ids are
  `R000001`-style, applied after their decision at the post-decision cursor
  time per MP-049.
- Amendment (2026-09-18): report.csv was retired; reports are flattened
  into decisions.csv rows (see MP-046 amendment). `write_outputs` no longer
  emits report.csv, `score_files` replays a single file, and the worker no
  longer uploads a separate report artifact. The practice-phase carrier
  leftover below is resolved by this change.
- The practice-phase results-kind upload still has no report carrier (it
  accepts a bare decisions.csv); extending the upload format is left to the
  platform side or MP-052.
- Amendment (2026-09-18, information boundary): the agent-visible weather
  no longer carries `instrument_efficiency` at all — the workflow strips
  the key from `current_site_weather` and every candidate's
  `effective_weather` (`_public_weather`), and `scoring_preview` computes
  the public baseline without the efficiency factor. Realized score vs
  baseline now reflects the full actual efficiency (jitter × fault
  multiplier × anomaly tag) instead of only the hidden fault part; the
  jitter band [0.90, 1.00] becomes the normal-noise band for detection.

## Completion

Completed 2026-09-18. Snapshots carry `tile_last_finished` and the
time-gated `fault_status`; `decision_response` accepts an optional `reports`
array end-to-end (workflow validation, participant protocol helper, local
runner, worker upload); runs emit an audited `report.csv`, and
`score_files` replay with reports matches the live score exactly.

## Evidence

- New `challenge/tests/test_report_channel.py` (10 tests): feedback null/
  score/zero-interrupt/wait cases, fault-status gating/nightly
  republication/removal, the one-shot instrument-normal notice, malformed
  entry dropping, cursor immobility, protocol envelope passthrough,
  end-to-end replay equivalence with reports, and the empty-report.csv case.
- `pytest challenge/tests tests/test_starter_kit.py
  tests/test_challenge_runner.py -q` → 103 passed, 1 skipped (playwright
  render test) with the `survey-agent` conda Python 3.12.
- Baseline anchor unchanged at `14229.273699`: the minimal agent does not
  report yet (MP-051) and the new snapshot fields do not alter its
  decisions.
