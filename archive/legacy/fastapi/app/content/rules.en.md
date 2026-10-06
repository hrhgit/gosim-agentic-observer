## 1. Eligibility and teams

1. Participation is open worldwide to individuals and teams. One account per person.
2. A team has 1 to 8 members. A person belongs to at most one team. Submissions are made on behalf of a team.
3. Organizers, evaluation-platform maintainers, and their immediate collaborators may participate in the practice phase but are excluded from awards. Their teams are marked hidden on the boards.
4. Team names and content must follow the code of conduct (section 8).

## 2. Phases

| Phase | Dates (UTC) | Submissions | Board |
|---|---|---|---|
| Practice | from registration until Awards Day | decisions.csv or agent package, 50 per team per day | informational |
| Online Competition | 2026-10-04 16:00 to 2026-10-07 15:59 (Oct 5–7 in UTC+8) | agent package only, 10 per team per day | decides the awards |
| Awards Day | 2026-10-17 at GOSIM Shenzhen | none | final results announced |

The live phase configuration table above this document is authoritative if the two differ.

## 3. What you submit

1. **Results file.** A `decisions.csv` with the columns `decision_id, slot_id, action, tile_id, program, reason`, produced by running your agent locally against a public scenario. Scored immediately by the frozen scorer.
2. **Agent package.** A single `agent.py`, or a `.zip` whose root (or single top-level folder) contains `agent.py`. The platform runs it against every scenario of the phase through the observer-v1 protocol and scores the decisions it produced. The competition weather replay is never downloadable.
3. Files are limited to 20 MB. Archives are limited to 2,000 files and 50 MB uncompressed. Symbolic links and paths outside the archive root are rejected.

## 4. Platform runs

1. Runtime: Python 3.12, standard library only. No network access. No package installation.
2. Limits per scenario: 20 seconds per decision, 10 minutes total wall time, 1 GB memory, 64 MB of written files, 64 processes.
3. A run that exceeds a limit, exits early, or answers with something other than a JSON object containing `action` is marked **failed**. Failed and invalid submissions do not appear on the board and do not affect a team's best score.
4. The agent receives only the current slot, a four-slot forecast, its own progress, and the list of legal candidate tiles. It never receives future weather beyond the forecast.
5. Attempts to read other teams' data, to escape the sandbox, to tamper with the scorer or with score files, or to exhaust platform resources deliberately lead to disqualification.

## 5. Scoring

The score is computed by the published `scorer.py` (schema `stage1-score-v1`) with the constants in `score_config.json`. In summary:

1. Weather is given in 900-second slots. Each `observe` action uses the tile's full `nominal_exptime_seconds` and may continue across slots of the same night. An exposure that cannot finish before the end of its night is invalid: it scores zero and the time consumed is waste.
2. For each exposure segment inside one slot, evaluated at the segment midpoint:
   `A = transparency / (seeing_arcsec · sky_brightness · airmass)`,
   `segment_score = H(altitude − 30°) · A · (dt / nominal_exptime_seconds) · target_value(tile) · priority_factor · (1 + B)`,
   where `target_value = Σ_c n_c · w_c · q_c` over the classes LRG, ELG, QSO, BGS, `priority_factor = 0.5 + 0.5 · priority / 10`, and `B` is the program bonus (DARK 0.25, BRIGHT 0.15, BACKUP 0.05) applied only when the decision program equals the condition program implied by `A` (DARK if A ≥ 0.30, BRIGHT if 0.14 ≤ A < 0.30, otherwise BACKUP).
3. `score = Σ action_score − 0.02 · total_waste_seconds`. Waste counts idle observable time, invalid actions, and exposure time spent while the site is closed or the tile is below 30° altitude. Closed time that the agent did not try to use is not penalised.
4. A tile scores once. Re-observing a completed tile is invalid. The decision program must equal the tile program; otherwise the action is invalid and consumes the exposure time.
5. **Completion** (completed tiles ÷ tiles in the catalogue) and **uniformity** (1 − 2 × standard deviation of per-region completion, clipped to [0, 1]) are reported for information and do not enter the stage-one score.
6. When a phase has several scenarios, a submission's score is the arithmetic mean of its per-scenario scores. A submission counts only if every scenario scored.

## 6. Ranking, ties, and verification

1. A team's best scored, non-excluded submission in the phase counts. Ranking is by score, descending; on an exact tie the earlier submission ranks first.
2. The Online Competition board is live. Organizers may freeze the board during the final hours and publish the final standings after verification.
3. Before awards are confirmed, organizers rerun the top submissions and may request the agent package and a short description of the approach from the top teams. Results that cannot be reproduced on the platform are removed.
4. Organizers may re-score submissions if a scorer defect is found. Any change to the scorer or the constants is announced with a version number and applies to every submission of the phase.

## 7. Awards

| Award | Prize | Count |
|---|---|---|
| First Prize | $2,000 | 1 |
| Second Prize | $1,000 | 2 |
| Third Prize | $500 | 3 |

Amounts are gross. Winning teams are invited to Awards Day at GOSIM Shenzhen on October 17, 2026; attendance is not required to receive a prize.

## 8. Code of conduct

1. Be respectful. Harassment, discrimination, and abusive content in team names, notes, or agent output are not tolerated.
2. Do not share accounts, do not submit another team's work, and do not create multiple teams to multiply the daily limit.
3. Report platform defects to the organizers instead of exploiting them. Reports of scorer or sandbox issues are welcome and credited.
4. Decisions of the organizers on eligibility, disqualification, and awards are final.

## 9. Data and privacy

1. Registration data (name, email, affiliation, GitHub handle) is used only to run the event and to contact winners.
2. Uploaded files, run logs, and score reports are stored on the platform until 90 days after Awards Day and are visible to the submitting team and to organizers.
3. Team names, scores, and ranks are public.

Contact: hackathon@gosim.org
