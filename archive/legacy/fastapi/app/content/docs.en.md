## 1. Overview

The platform evaluates observing agents for a DESI-style survey. A scenario is three files: `weather.csv` (900-second slots with seeing, transparency, sky brightness and an open/closed flag), `tiles.csv` (the pointing catalogue with programs, priorities, exposure times and target counts) and `score_config.json` (frozen constants). An agent turns a scenario into `decisions.csv`, and `scorer.py` turns `decisions.csv` into a score report.

There are two ways to get a score:

1. Run your agent yourself on a public scenario and upload `decisions.csv`.
2. Upload your agent; the platform runs it on hidden scenarios through the observer-v1 step protocol and scores what it produced.

Both paths use the same `scorer.py`. The starter kit contains every file the platform uses.

## 2. Starter kit

Download `agent-observer-starter-kit.zip` from the Resources page. Contents:

| File | Purpose |
|---|---|
| `agent.py` | Baseline agent implementing the observer-v1 protocol. Replace `decide()`. |
| `local_runner.py` | Runs an agent through the protocol and scores it locally. |
| `protocol.py` | Builds the state the agent sees. Identical to the platform copy. |
| `scorer.py`, `score_config.json` | The frozen scorer and constants. |
| `generate_example_data.py` | Seeded generator for new weather/tile scenarios. |
| `example/` | The published development scenario (seed 11): 2 nights × 12 slots, 72 tiles. |
| `sac_submit.py` | Command-line submission using your API token. |
| `SKILL.md` | Instructions an AI coding agent can follow end to end. |

Run the baseline:

```
python3 local_runner.py --agent agent.py --weather example/weather.csv --tiles example/tiles.csv --config score_config.json --out run_output
```

The last line prints the score. `run_output/decisions.csv` is what you would upload as a results file; `run_output/score_report.json` is the same report the platform produces.

Generate a different scenario to avoid tuning to one weather sequence:

```
python3 generate_example_data.py --seed 7 --n-nights 5 --slots-per-night 20 --n-tiles 150 --output-dir scenario7
```

## 3. Data formats

Conventions: UTF-8, comma-separated, header required, unknown extra columns allowed. Timestamps are RFC 3339 in UTC (`2026-10-02T02:00:00Z`). Booleans accept `true/false` or `1/0`. IDs are strings and must be unique within their table. The spellings `transparency` and `program` are canonical.

### weather.csv

```
slot_id,night_id,timestamp_utc,duration_seconds,seeing_arcsec,transparency,sky_brightness,is_observable
```

Rows are chronological. Slots within one night are contiguous and last `slot_seconds` (900). A gap is allowed only between different `night_id` values. `is_observable=false` means the dome is closed: exposures taken then score zero and count as waste.

### tiles.csv

```
tile_id,ra_deg,dec_deg,program,region,priority,nominal_exptime_seconds,n_lrg,n_elg,n_qso,n_bgs
```

`program` is `DARK`, `BRIGHT` or `BACKUP`. `region = floor(ra_deg / 45)` (0–7) is a bookkeeping bin used for the completion and uniformity reports. `priority` is in [0, 10]. `nominal_exptime_seconds` is the uninterrupted time needed to complete the tile once.

### decisions.csv

```
decision_id,slot_id,action,tile_id,program,reason
```

1. Rows are evaluated in file order. `decision_id` must be unique and strictly increasing.
2. `slot_id` is the slot in which the action starts. Several rows may use the same slot; `decision_id` orders them. Slot order must be non-decreasing.
3. `observe` uses the tile's whole `nominal_exptime_seconds` and may run into following slots of the same night. When it finishes inside a slot, another decision with that same `slot_id` may use the remaining time. Time not used by any decision is idle.
4. `wait` consumes the remainder of the current slot. For `wait`, `tile_id` and `program` are empty. For `observe`, both are required and `program` must equal the tile's program.
5. An exposure that cannot finish before its night ends is invalid: zero score, the time until the night ends is waste. A completed tile cannot be observed again.
6. A malformed table is rejected as an invalid submission. Operationally invalid actions are listed in the report and consume time without scoring.

### score_report.json

Top-level fields: `score`, `science_score`, `waste_penalty`, `total_waste_seconds`, `waste_breakdown_seconds` (idle, invalid_actions, unproductive_exposure), `unavailable_unpenalized_seconds`, `completed_tiles`, `invalid_actions`, `region_completion` and one `actions` entry per decision with `valid`, `message`, `start_timestamp_utc`, `elapsed_seconds`, `segments`, `unproductive_seconds`, `science_score`.

## 4. Observer protocol (observer-v1)

The platform starts your entry script as `python3 -I -B agent.py` and communicates over standard input/output with one JSON object per line. Standard error is captured into a log you can download from the submission page. Print nothing else to standard output.

### Messages you receive

1. `{"type": "init", "protocol": "observer-v1", "config": {...}, "site": {...}, "slot_seconds": 900, "n_tiles": N, "tiles": [...], "scenario": {...}, "limits": {...}}` once. `tiles` is the full catalogue (`tile_id, ra_deg, dec_deg, program, region, priority, nominal_exptime_seconds, n_lrg, n_elg, n_qso, n_bgs`).
2. `{"type": "step", ...}` once per decision point, with:
   - `now`: `slot_id`, `night_id`, `slot_index`, `timestamp_utc`, `slot_elapsed_seconds`, `slot_remaining_seconds`, `night_remaining_seconds`, `slots_remaining_in_night`, `slots_remaining_total`.
   - `weather`: `seeing_arcsec`, `transparency`, `sky_brightness`, `is_observable`, `zenith_quality` (A at airmass 1), `zenith_program`.
   - `forecast`: the next four slots (`slot_id`, `night_id`, `timestamp_utc`, `seeing_arcsec`, `transparency`, `sky_brightness`, `is_observable`).
   - `progress`: `completed_tiles`, `total_tiles`, `science_score`, `waste_seconds`, `idle_seconds`, `invalid_seconds`, `unproductive_exposure_seconds`, `invalid_actions`, `region_completion`, `completed_tile_ids`.
   - `available_tiles`: tiles that are not completed, fit in the remaining night, and are above 30° altitude at the midpoint of the first exposure segment. Each carries the catalogue fields plus `altitude_deg`, `airmass`, `quality` (A for this tile now), `condition_program`, `program_match`, `target_value`, `expected_gain` (score if the whole exposure were taken under the current slot's weather at the current airmass) and `expected_gain_per_second`. Sorted by `expected_gain`, descending.
   - `last_action`: the outcome of your previous answer (`valid`, `message`, `science_score`, `elapsed_seconds`), or `null`.
3. `{"type": "end", "summary": {...}}` once after the last slot. You may exit.

### Messages you send

Exactly one line per `step`:

```
{"action": "observe", "tile_id": "200069", "program": "DARK", "reason": "highest expected gain"}
{"action": "wait", "reason": "dome closed"}
```

`plan` is accepted as an alias of `program`. If `program` is omitted for `observe`, the tile's own program is used. `reason` is free text up to 500 characters and is stored in `decisions.csv`.

You may choose a tile that is not in `available_tiles`; the scorer then applies its normal rules (below-altitude segments are unproductive, night overrun is invalid). `available_tiles` is a convenience, not a restriction.

### Time and step accounting

Every answer consumes time: an `observe` consumes the tile's exposure time (or the rest of the night if it cannot finish); a `wait` consumes the rest of the current slot. The run ends when the weather horizon is reached. A scenario of 168 slots therefore needs at most a few hundred steps.

## 5. Platform run limits

| Limit | Value |
|---|---|
| Interpreter | Python 3.12, `-I -B`, standard library only |
| Network | none |
| Per decision | 20 s |
| Per scenario | 600 s wall time |
| Memory | 1 GB |
| Files written | 64 MB total, inside the run directory (`$OBSERVER_SCRATCH`) |
| Processes | 64 |
| Package | `.py` or `.zip` ≤ 20 MB, ≤ 2,000 files, ≤ 50 MB uncompressed |

Environment variables available to the agent: `OBSERVER_PROTOCOL=observer-v1`, `OBSERVER_SCRATCH` (writable directory), `HOME` and `TMPDIR` (same directory).

## 6. Submitting

### From the website

Dashboard → Submit. Choose the phase, the submission type, the scenario (results files only), and the file. The page shows how many submissions your team has left today. Each submission gets a page with the score, the per-scenario report, the action timeline, region completion, the generated `decisions.csv` and the agent log.

### From the command line

Your API token is on the Profile page.

```
python3 sac_submit.py --base https://<host> --token <token> --phase practice --kind results --scenario dev-example --file run_output/decisions.csv
python3 sac_submit.py --base https://<host> --token <token> --phase online --kind agent --file agent.py --wait
```

`--wait` polls until the evaluation finishes and prints the score.

### JSON API

All endpoints return JSON. Authenticate with `Authorization: Bearer <token>`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness |
| GET | `/api/phases` | phases, windows, scenarios |
| GET | `/api/leaderboard?phase=<slug>&limit=<n>` | standings (`entries`: rank, team_name, total_score, science_score, completion_rate, uniformity_score, submission_count) |
| GET | `/api/announcements` | published announcements |
| GET | `/api/me` | your account and team |
| GET | `/api/submissions` | your team's submissions |
| POST | `/api/submissions` | multipart form: `phase`, `kind`, `scenario` (results only), `file`, optional `title`, `notes` |
| GET | `/api/submissions/<id>` | status, score, per-scenario evaluations |
| GET | `/api/submissions/<id>/evaluations/<eid>/{report,decisions,log}` | artifacts |

Interactive documentation: `/api/docs`.

## 7. Strategy notes

1. `expected_gain` assumes the current slot's weather for the whole exposure. Long exposures late in a night may cross into worse sky brightness; check `forecast`.
2. `program_match` matters: the bonus is 25% for DARK, 15% for BRIGHT, 5% for BACKUP, and a mismatch pays nothing extra. A DARK tile observed under BRIGHT conditions still scores by A, only without the bonus.
3. Waiting costs 0.02 per idle second (18 points per empty slot). Observing a low-value tile is often better than waiting, unless a much better tile becomes visible within the forecast.
4. Exposures that would overrun the night are invalid and waste the remaining night. `available_tiles` already excludes them.
5. Altitude changes during long exposures. A tile rising at 31° is safer than one setting at 31°.

## 8. Local verification checklist

1. `python3 local_runner.py ...` finishes without warnings and prints a score.
2. `run_output/decisions.csv` re-scores to the same number with `python3 scorer.py --weather ... --tiles ... --decisions run_output/decisions.csv --config score_config.json`.
3. The agent uses no third-party imports and does not read files outside its directory.
4. A decision never takes longer than a few seconds.
