# Per-attempt online evaluation

> Status 2026-09-27: not used by the `online` phase, which evaluates three fixed formal scenarios
> (see `competition-format.md`). Calibration now applies only to internal acceptance phases.

Practice and existing scores keep their current behavior. Randomization is opt-in
per formal phase, and every scenario in an enabled phase needs a frozen profile.
Configuration cannot change after the first formal batch. No production phase is
enabled by installing the migration alone.

## What varies

Each formal run receives a private 256-bit random key when its batch is created.
Keys are unique across teams and attempts. A retry of the same run keeps its key.
The calendar, target catalog, observation requests, scoring rules and runtime stay
fixed. The key independently randomizes weather/events/forecasts and hidden tile
tags. It is never included in executor jobs, model credentials, public catalogs,
rankings or participant result downloads.

Candidate generation and reference policies are versioned. The generator selects
the first candidate inside the frozen difficulty bounds. It rejects outliers and
fails explicitly if it cannot find a comparable candidate; it never substitutes a
fixed public scenario. A private immutable record is acknowledged by the platform
before the first observation is published. Existing sequential commits still
prevent participants submitting an unreleased future round or replacing an
accepted decision. Local CSV uploads must match the platform's committed trace.

Trusted preparation has a 1200-second process deadline, separate from the Agent's
scoring clock and below its 1500-second startup window. Exhaustion or timeout
fails explicitly, with no first observation or score published. It never accepts
an outlier just to meet a time limit. Candidate choice is independent of timing;
only whether preparation completes within the resource limit can differ by host.

## Difficulty calibration

Three fixed online reference policies prioritize gain per second, required tiles,
and observation requests respectively. None sees future weather. The all-wait
policy supplies a common zero-performance reference. Difficulty bounds constrain
the reference panel's score span, individual relative scores and observable-slot
fraction. The same profiles apply to every team in a phase.

The calibrated score is:

```
10000 * (raw score - all-wait score) / (reference-panel mean - all-wait score)
```

The panel mean is 10,000, all-wait is zero, and stronger agents may exceed 10,000.
Raw negative totals are supported. Batch ranking remains the mean of all scenarios
in the same completed batch. Raw scores and components remain available alongside
calibrated totals. Calibration reduces observed difficulty differences; it does
not prove that every strategy experiences identical difficulty.

`scripts/calibrate-observer-scenarios.py` fits a candidate profile using at least
20 training seeds and checks at least 10 separate validation seeds. A fourth
science-prioritizing policy is excluded from fitting and used for the holdout
comparison. The script's seeds are explicitly public experiment seeds, never the
keys issued to competitors. Its output is a study for organizer review, not an
automatic production activation. Review both official templates, acceptance rate,
remaining variance, completion outcomes and runtime before registering profiles.

Registration uses `scripts/configure-observer-calibration.py manifest.json`
(add `--apply` after review). The private manifest names the phase, each template
directory and study file, immutable bundle hashes and all six tested runner
versions. It reconstructs the profile and validation results, requires at least
half of independent validation seeds to be accepted, rejects increased holdout
variance, and requires accepted holdout standard deviation to be at most 5% of
its mean. These criteria are fixed before reviewing the official studies. The
activation transaction checks that no existing attempt would be displaced, all
phase scenarios are covered, and the tested runtimes are deployed. It refuses
to replace a different profile and records an organizer audit event.

## Audit and replay

Private records retain the seed, accepted candidate index, template and generated
file digests, profile digest, reference scores and rejected-candidate reasons.
Immutable profile rows retain the template archive digest. The engine job retains
the exact control Git revision (`workflow_sha`). Keep that revision/tag and the
corresponding immutable scenario bundle for audits.

From that revision, use the private record and the participant's committed CSV:

```
python scripts/replay-observer-instance.py --template /private/template \
  --record /private/record.json --decisions /private/decisions.csv
```

Replay regenerates the chosen scenario, checks its digest, recomputes calibration,
and runs the official scorer. It prints only the commitment and verified scores.
The private key and hidden inputs must stay in organizer storage, including when
the original run ended early. Model responses need not be reproduced to verify
the exact committed decisions and their scores.
