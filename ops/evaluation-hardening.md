# Evaluation hardening switches

Two organizer switches, both **on** by default (migration
`20261001000600_evaluation_hardening.sql`):

| Switch | Effect when on | Effect when off |
|---|---|---|
| `restricted_egress` | New colocated engine jobs carry `restricted_egress: true`. The participant container runs on a per-run internal Docker network; its only reachable address is a forwarder sidecar (`project_platform/egress.py`) that relays POSTs under a random per-run prefix to the observer-model proxy. `OPENAI_BASE_URL` points at the forwarder; `ANTHROPIC_BASE_URL` points at the same sidecar and run prefix, minus the `/v1` segment the Anthropic SDK appends itself. The reviewed build step keeps the default bridge (package registries). | Exactly the previous behaviour: `--network bridge`, `OPENAI_BASE_URL`/`ANTHROPIC_BASE_URL` are the observer-model URL. |
| `rescore` | A finished formal project run is scored from the engine summary as before (`score_check='pending'`), then a `score` job (`observer-score.yml`, a fresh runner, no participant code) downloads the card and the run's private result, checks `decisions.csv` against the digest committed at finish and recomputes the score (v4: replays `actions.jsonl`, which must regenerate the same `decisions.csv`; v3: `challenge.scoring_core`). Same total → `verified`. Different total → `corrected`: the recomputed score replaces the reported one on the run and its batch, the original summary is kept in `private.observer_score_checks`, audit `observer.score_corrected`. Stored trace not the committed one → `rejected`: run `failed` (`score_verification_failed`, a participant failure: no refund, no automatic retry), batch voided, audit `observer.score_rejected`. A score job that cannot run → `unverified`, reported score stands. | No new `pending` runs; checks no score job has picked up are cleared; a score job already running only records its result (`unverified`), never changes a score. |

## One-click rollback

```sql
select public.observer_set_hardening(false, false);   -- both off
select public.observer_set_hardening(false, null);    -- egress only
select public.observer_set_hardening(null, false);    -- rescore only
select public.observer_hardening();                   -- current state
```

Each change is audited (`observer.hardening`). Running engine jobs keep the
egress mode they started with; the next scheduled run follows the switch.

## Deployment order

1. Runtime (`scripts/build-observer-control.py` export, incl. `observer-score.yml`)
   to every runner organization, approved_sha updated with no job in flight.
   New runtime + old backend behaves exactly as before.
2. Migration (`scripts/deploy-observer-backend.py`).
3. Edge functions `observer-job`, then `observer-dispatch`.

## Checking

```sql
select score_check, count(*) from public.observer_runs where finished_at > now() - interval '1 day' group by 1;
select * from private.observer_score_checks order by checked_at desc limit 20;
```
